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
import re
import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal, QTimer
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QHBoxLayout, QDialog

import qfluentwidgets as qfw

from sookit.core.ytdlp_utils import download_ytdlp, download_deno
from sookit.paths import get_tools_dir

_PCT_RE = re.compile(r"(\d+)")


class _DownloadWorker(QObject):
    """在后台线程顺序下载 yt-dlp 与 Deno（各自独立判断、互不干扰）。"""
    progress = pyqtSignal(str)
    done = pyqtSignal(object)  # {"yt": {...}, "deno": {...}}

    def run(self):
        def _comp(name, fn):
            ok, status, err = True, "up_to_date", ""
            try:
                status = fn(self.progress.emit)
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
        try:
            Path(self._result_path).write_text(
                json.dumps(self._result, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
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
    """updater.exe 独立入口：构建 qfw 窗口跑下载，写结果文件后退出。"""
    result_path = _parse_result_path()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    dialog = UpdaterDialog(result_path)
    dialog.start()
    dialog.show()
    return app.exec()
