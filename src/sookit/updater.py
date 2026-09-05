"""
sookit/updater.py
独立 GUI 下载器入口（打包为 updater.exe）。

两种任务（子命令分发）：
1. Sookit 主程序用 runas 提权启动（下载组件写入只读的程序目录）：
       updater.exe --ytdlp-updater-gui <result_path>
   职责：下载/更新 yt-dlp + Deno 到软件目录，结果 JSON 写 result_path。
2. Sookit 主程序普通权限启动（Sookit 自更新安装包下载，目标在用户目录无需提权）：
       updater.exe --app-setup <tag> <result_path>
   职责：下载 Sookit-Setup-<ver>.exe 到 %APPDATA%\\Sookit\\updates\\（含 sha256
   校验与旧版本清理），结果 JSON 写 result_path。

两种任务均在结束后把结果 JSON 写到 result_path 供 Sookit 轮询读取。
本进程独立运行，不进入 Sookit 主窗口/托盘，无硬超时，下载器自己跑完。
"""

import json
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal, QTimer
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QHBoxLayout, QDialog

import qfluentwidgets as qfw

from sookit.core.ytdlp_utils import (
    download_ytdlp, download_deno, DownloadCancelled)
from sookit.core.app_update import download_installer, _SETUP_PREFIX
from sookit.paths import get_tools_dir

# 进度文本抓百分比（如 "下载 yt-dlp.exe — 23% (…)"）。
# 必须 anchor 到 % 符号——label 本身可能含数字（安装器版本号 260905.2），
# 泛数字匹配会误抓版本号当进度。
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")

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


class _CancellableWorker(QObject):
    """可取消下载任务的公共骨架：取消标志 + 当前 aria2c 进程记录 + 强制终止兜底。

    支持取消：set_cancelled() 后，当前下载会在下载循环的检查点中断
    （下载函数收到 cancel_cb=True 抛 DownloadCancelled），并清理已下载的 .part
    临时文件。记录当前 aria2c 子进程（on_proc 回调），供取消超时后强制终止兜底。
    """
    progress = pyqtSignal(str)
    done = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        self._cancelled = False
        self._proc = None  # 当前 aria2c 子进程（Popen 对象，含 pid）

    def set_cancelled(self):
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def set_proc(self, proc):
        """on_proc 回调：记录当前 aria2c 子进程。"""
        self._proc = proc

    def current_pid(self):
        """当前 aria2c 进程 pid（无则 None）。"""
        return self._proc.pid if self._proc is not None else None

    def force_terminate(self):
        """强制终止当前 aria2c 进程树（兜底，不按进程名全局杀）。

        先尝试 terminate/kill；仍存活则用 taskkill /PID <pid> /T /F 精准杀。
        """
        proc = self._proc
        if proc is None:
            return
        pid = proc.pid
        if pid:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            # 等待短暂退出，否则 taskkill 兜底
            try:
                proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                pass
        if pid and proc.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception:  # noqa: BLE001
                pass


class _DownloadWorker(_CancellableWorker):
    """在后台线程顺序下载 yt-dlp 与 Deno（各自独立判断、互不干扰）。"""

    def run(self):
        def _comp(name, fn):
            # 已取消则不再进入该组件（含版本检查/下载），直接返回取消结果
            if self._cancelled:
                return {"name": name, "ok": False, "status": "cancelled",
                        "error": "用户取消"}
            ok, status, err = True, "up_to_date", ""
            try:
                status = fn(self.progress.emit, True, lambda: self._cancelled,
                            self.set_proc)
            except DownloadCancelled:
                ok, status, err = False, "cancelled", "用户取消"
            except Exception as e:  # noqa: BLE001
                ok, err = False, str(e)
            return {"name": name, "ok": ok, "status": status, "error": err}

        yt = _comp("yt-dlp", download_ytdlp)
        deno = _comp("Deno", download_deno)
        self._proc = None  # 下载结束，清空
        self.done.emit({"yt": yt, "deno": deno})


def _cleanup_old_setups(keep: Path):
    """app 安装包下载成功后，清理 updates/ 下其他版本的旧安装包（尽力而为）。

    磁盘上最多保留"最新下载的"一个安装包；删除失败静默（清理不影响主流程）。
    """
    try:
        for p in keep.parent.glob(f"{_SETUP_PREFIX}*.exe"):
            if p != keep:
                p.unlink(missing_ok=True)
                _log(f"[CLEANUP] 删除旧安装包 {p.name}")
    except OSError:
        pass


class _AppSetupWorker(_CancellableWorker):
    """后台线程下载 Sookit 自更新安装包（含 sha256 校验，成功后清理旧版本）。

    done 结果结构：{"task": "app_setup", "ok": bool, "status": "ok|cancelled|failed",
    "error": str, "path": str}
    """

    def __init__(self, tag: str):
        super().__init__()
        self._tag = tag

    def run(self):
        try:
            path = download_installer(
                self._tag, self.progress.emit, lambda: self._cancelled, self.set_proc)
        except DownloadCancelled:
            self._proc = None
            self.done.emit({"task": "app_setup", "ok": False, "status": "cancelled",
                            "error": "用户取消", "path": ""})
            return
        except Exception as e:  # noqa: BLE001
            self._proc = None
            self.done.emit({"task": "app_setup", "ok": False, "status": "failed",
                            "error": str(e), "path": ""})
            return
        self._proc = None
        _cleanup_old_setups(Path(path))
        self.done.emit({"task": "app_setup", "ok": True, "status": "ok",
                        "error": "", "path": str(path)})


class UpdaterDialog(QDialog):
    """qfw Fluent 风格小窗口：标题 + 主题色进度条 + 状态文字 + 取消按钮。

    tag 不为 None 时为 app 安装包下载任务（--app-setup），否则为 yt-dlp/Deno 任务。
    """

    def __init__(self, result_path: str, parent=None,
                 title: str = "yt-dlp/Deno 更新", tag: str | None = None):
        super().__init__(parent)
        self._result_path = result_path
        self._result = None
        self._thread = None
        self._worker = None
        self._finished = False
        self._tag = tag

        self.setWindowTitle(title)
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
        """启动后台下载线程并自动开始（按 _tag 选择任务 worker）。"""
        self._thread = QThread(self)
        if self._tag is not None:
            self._worker = _AppSetupWorker(self._tag)
        else:
            self._worker = _DownloadWorker()
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_done)
        self._worker.done.connect(self._thread.quit)
        # 注意：不连接 thread.finished → deleteLater。done 信号会同时触发 _on_done/
        # thread.quit，deleteLater 与 _poll_cancel(QTimer) 存在生命周期竞态——QThread
        # 的 C++ 对象被删后 _poll_cancel 访问 isRunning() 抛 RuntimeError 导致 updater
        # 静默崩溃（实测）。QThread 由对话框属性持有，随对话框销毁回收。
        self._thread.start()

    def _on_progress(self, text: str):
        self.status_label.setText(text)
        m = _PCT_RE.search(text)
        if m:
            self.bar.setValue(int(float(m.group(1))))

    @staticmethod
    def _result_ok(result) -> bool:
        """按任务类型解析结果 dict 的整体成功标志"""
        if result.get("task") == "app_setup":
            return bool(result.get("ok"))
        return bool(result["yt"]["ok"]) and bool(result["deno"]["ok"])

    def _on_done(self, result):
        self._result = result
        ok = self._result_ok(result)
        if ok:
            text = "已完成"
        elif result.get("task") == "app_setup":
            text = "已取消" if result.get("status") == "cancelled" else "下载失败，结果见 Sookit 提示"
        else:
            text = "下载完成，结果见 Sookit 提示"
        self.status_label.setText(text)
        self.bar.setValue(100 if ok else 0)
        # 短暂展示结果后自动关闭
        QTimer.singleShot(600, self._finish)

    def _on_cancel(self):
        if self._finished:
            return
        if self._result is not None:
            # 下载恰已完成（done 已到达），直接按真实结果收尾
            self._finish()
            return
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("正在取消，请稍候…")
        # 请求后台线程中断当前下载（download_ytdlp/deno 收到 cancel_cb=True 会停止并清理临时文件）
        if self._worker:
            self._worker.set_cancelled()
        # 非阻塞轮询线程状态：立即返回让事件循环渲染提示文字，避免 GUI 冻结
        self._cancel_deadline = time.monotonic() + 20
        self._poll_cancel()

    def _thread_alive(self) -> bool:
        """后台线程是否仍在运行。QThread 的 C++ 对象可能已被 deleteLater 删除
        （与 done/finished 信号的竞态），此时访问 isRunning() 抛 RuntimeError——
        按"线程已结束"处理。"""
        try:
            return self._thread is not None and self._thread.isRunning()
        except RuntimeError:
            return False

    def _poll_cancel(self):
        """每 100ms 轮询后台线程退出状态（非阻塞，保证 GUI 不冻结、取消提示可见）。

        - 线程已退出：若 done 信号已写入真实结果则以其为准（解决"取消瞬间下载恰好
          完成、文件已被替换"的竞态），否则补写取消结果；
        - 超时（20s）：先 force_terminate 杀干净 aria2c 进程树（防孤儿进程继续下载），
          再给 5s 最后机会，确实卡死才硬写取消结果并关窗。
        """
        if self._finished:
            return
        cancelled_result = {
            "yt": {"name": "yt-dlp", "ok": False, "status": "cancelled", "error": "用户取消"},
            "deno": {"name": "Deno", "ok": False, "status": "cancelled", "error": "用户取消"},
        }
        if not self._thread_alive():
            if self._result is None:
                self._result = cancelled_result
            self._finish()
            return
        if time.monotonic() >= self._cancel_deadline:
            if self._worker:
                self._worker.force_terminate()
            if self._thread_alive():
                self._thread.wait(5000)
            if self._result is None:
                self._result = cancelled_result
            self._finish()
            return
        QTimer.singleShot(100, self._poll_cancel)

    def _finish(self):
        """写结果文件并关闭窗口（结果由 Sookit 轮询读取）。"""
        if self._finished:
            return
        self._finished = True
        _log(f"[DONE] 下载器结束, result_path={self._result_path}, result={self._result}")
        _write_result(self._result_path, self._result)
        self.accept()

    def closeEvent(self, e):
        # 用户直接点窗口关闭按钮：按取消处理。
        # 未完成时忽略关闭并隐藏窗口，后台继续取消流程（写完结果文件后由 _finish
        # 关闭对话框退出），避免进程提前退出导致 Sookit 拿不到结果而空等超时。
        if not self._finished:
            e.ignore()
            self.hide()
            self._on_cancel()
        else:
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


def _parse_app_setup_args() -> tuple[str, str]:
    """解析 --app-setup <tag> <result_path>；缺失项返回空串"""
    try:
        i = sys.argv.index("--app-setup")
        tag = sys.argv[i + 1] if i + 1 < len(sys.argv) and not sys.argv[i + 1].startswith("-") else ""
        result = sys.argv[i + 2] if i + 2 < len(sys.argv) and not sys.argv[i + 2].startswith("-") else ""
        return tag, result
    except ValueError:
        return "", ""


def _main_app_setup() -> int:
    """--app-setup 任务入口：下载 Sookit 安装包（非提权，普通用户目录）"""
    tag, result_path = _parse_app_setup_args()
    if not tag or not result_path:
        _log(f"[FATAL] --app-setup 参数缺失, argv={sys.argv}")
        if result_path:
            _write_result(result_path, {"task": "app_setup", "ok": False,
                                        "status": "failed", "error": "updater 参数缺失", "path": ""})
        return 1
    _log(f"[START] updater --app-setup, tag={tag}, result_path={result_path}")
    try:
        app = QApplication(sys.argv)
        app.setStyle("Fusion")
        dialog = UpdaterDialog(result_path, title="Sookit 更新", tag=tag)
        dialog.start()
        dialog.show()
        _log("[START] GUI 已显示, 开始下载安装包")
        return app.exec()
    except Exception as e:  # noqa: BLE001
        _log("[FATAL] app-setup 启动失败:\n" + traceback.format_exc())
        _write_result(result_path, {"task": "app_setup", "ok": False, "status": "failed",
                                    "error": f"updater 启动失败: {e}", "path": ""})
        return 1


def main() -> int:
    """updater.exe 独立入口：按子命令分发任务，构建 qfw 窗口跑下载，写结果文件后退出。

    全程 try 兜底：任何阶段异常都记录日志并写结果文件（含 traceback），
    保证 Sookit 能拿到结果而非永久等待。
    """
    if "--app-setup" in sys.argv:
        return _main_app_setup()

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
