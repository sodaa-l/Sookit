"""
sookit/updater.py
独立 GUI 下载器入口（打包为 updater.exe）。

由 Sookit 主程序用 runas 提权启动：
    updater.exe --ytdlp-updater-gui <result_path>

职责：显示 qfw 进度窗口，顺序下载/更新 yt-dlp + Deno 到软件目录
（写操作由提权保证），结束后把结果 JSON 写到 result_path 供 Sookit 轮询读取。
本进程独立运行，不进入 Sookit 主窗口/托盘，无硬超时，下载器自己跑完。
"""

import json
import os
import re
import sys
import traceback
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal, QTimer
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QHBoxLayout, QDialog

import qfluentwidgets as qfw

from sookit.core.ytdlp_utils import (
    download_ytdlp, download_deno, DownloadCancelled)
from sookit.paths import get_tools_dir

_PCT_RE = re.compile(r"(\d+)")

# ---------- 日志与结果兜底（保证进程无论成败都留下痕迹，Sookit 不会永久等待） ----------


def _log_path() -> Path:
    """日志写到 %LOCALAPPDATA%\\Sookit\\log\\updater.log（普通用户可写）。"""
    try:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home())
        log_dir = Path(base) / "Sookit" / "log"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / "updater.log"
    except OSError:
        return Path.home() / "updater.log"


def _log(msg: str) -> None:
    """追加一行日志（失败静默，不影响主流程）。"""
    try:
        with open(_log_path(), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass


def _write_result(result_path: str, data: dict) -> None:
    """写结果文件；失败时退而写日志，保证 Sookit 能感知失败而非永久等待。"""
    try:
        Path(result_path).write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        _log(f"[FATAL] 结果文件写入失败: {result_path}")


class _DownloadWorker(QObject):
    """在后台线程顺序下载 yt-dlp 与 Deno（各自独立判断、互不干扰）。

    支持取消：set_cancelled() 后，当前下载会在下载循环的检查点中断
    （download_ytdlp/download_deno 收到 cancel_cb=True 抛 DownloadCancelled），
    并清理已下载的 .part 临时文件。
    """
    progress = pyqtSignal(str)
    done = pyqtSignal(object)  # {"yt": {...}, "deno": {...}}

    def __init__(self):
        super().__init__()
        self._cancelled = False

    def set_cancelled(self):
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def run(self):
        def _comp(name, fn):
            ok, status, err = True, "up_to_date", ""
            try:
                status = fn(self.progress.emit, True, lambda: self._cancelled)
            except DownloadCancelled:
                ok, status, err = False, "cancelled", "用户取消"
            except Exception as e:  # noqa: BLE001
                ok, err = False, str(e)
            return {"name": name, "ok": ok, "status": status, "error": err}

        yt = _comp("yt-dlp", download_ytdlp)
        deno = _comp("Deno", download_deno)
        self.done.emit({"yt": yt, "deno": deno})


class UpdaterDialog(QDialog):
    """qfw Fluent 风格小窗口：标题 + 主题色进度条 + 状态文字 + 取消按钮。"""

    def __init__(self, result_path: str, parent=None):
        super().__init__(parent)
        self._result_path = result_path
        self._result = None
        self._thread = None
        self._worker = None
        self._finished = False

        self.setWindowTitle("yt-dlp/Deno 更新")
        self.setFixedSize(420, 230)
        self.setStyleSheet("SubtitleLabel { font-weight: normal; }")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 16)

        lay.addWidget(qfw.SubtitleLabel("正在更新组件"))
        lay.addSpacing(18)

        self.bar = qfw.ProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        lay.addWidget(self.bar)
        lay.addSpacing(12)

        self.status_label = qfw.BodyLabel("准备中…")
        self.status_label.setWordWrap(True)
        lay.addWidget(self.status_label)
        lay.addStretch()

        btn_lay = QHBoxLayout()
        btn_lay.addStretch()
        self.cancel_btn = qfw.PushButton("取消")
        self.cancel_btn.clicked.connect(self._on_cancel)
        btn_lay.addWidget(self.cancel_btn)
        lay.addLayout(btn_lay)

    def start(self):
        """启动后台下载线程并自动开始。"""
        self._thread = QThread(self)
        self._worker = _DownloadWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.done.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    def _on_progress(self, text: str):
        self.status_label.setText(text)
        m = _PCT_RE.search(text)
        if m:
            self.bar.setValue(int(m.group(1)))

    def _on_done(self, result):
        self._result = result
        ok = result["yt"]["ok"] and result["deno"]["ok"]
        self.status_label.setText("已完成" if ok else "下载完成，结果见 Sookit 提示")
        self.bar.setValue(100 if ok else 0)
        # 短暂展示结果后自动关闭
        QTimer.singleShot(600, self._finish)

    def _on_cancel(self):
        if self._finished:
            return
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("正在取消，请稍候…")
        # 请求后台线程中断当前下载（download_ytdlp/deno 收到 cancel_cb=True 会停止并清理临时文件）
        if self._worker:
            self._worker.set_cancelled()
        # 等待后台线程真正结束，确保下载已中断、临时文件已清理后再收尾
        if self._thread and self._thread.isRunning():
            self._thread.wait(15000)
        self.status_label.setText("已取消…")
        self._result = {
            "yt": {"name": "yt-dlp", "ok": False, "status": "cancelled", "error": "用户取消"},
            "deno": {"name": "Deno", "ok": False, "status": "cancelled", "error": "用户取消"},
        }
        self._finish()

    def _finish(self):
        """写结果文件并关闭窗口（结果由 Sookit 轮询读取）。"""
        if self._finished:
            return
        self._finished = True
        _log(f"[DONE] 下载器结束, result_path={self._result_path}, result={self._result}")
        _write_result(self._result_path, self._result)
        self.accept()

    def closeEvent(self, e):
        # 用户直接点窗口关闭按钮：按取消处理（写结果）
        if not self._finished:
            self._on_cancel()
        super().closeEvent(e)


def _parse_result_path() -> str:
    """解析 --ytdlp-updater-gui 后的结果文件路径参数；缺失时用兜底路径。"""
    try:
        i = sys.argv.index("--ytdlp-updater-gui")
        if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-"):
            return sys.argv[i + 1]
    except ValueError:
        pass
    return str(get_tools_dir() / ".ytdlp_updater_result.json")


def main() -> int:
    """updater.exe 独立入口：构建 qfw 窗口跑下载，写结果文件后退出。

    全程 try 兜底：任何阶段异常都记录日志并写结果文件（含 traceback），
    保证 Sookit 能拿到结果而非永久等待。
    """
    result_path = _parse_result_path()
    _log(f"[START] updater 启动, argv={sys.argv}, result_path={result_path}")
    try:
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        dialog = UpdaterDialog(result_path)
        dialog.start()
        dialog.show()
        _log("[START] GUI 已显示, 开始下载")
        return app.exec()
    except Exception as e:  # noqa: BLE001
        _log("[FATAL] updater 启动失败:\n" + traceback.format_exc())
        _write_result(result_path, {
            "yt": {"name": "yt-dlp", "ok": False, "status": "failed",
                   "error": f"updater 启动失败: {e}"},
            "deno": {"name": "Deno", "ok": False, "status": "failed",
                     "error": f"updater 启动失败: {e}"},
            "fatal": traceback.format_exc(),
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
