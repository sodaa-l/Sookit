"""
core/workers.py
后台工作线程
"""

import os
import sys
import threading
import subprocess
from PyQt6.QtCore import QThread, QObject, pyqtSignal
from sookit.core.functions import Functions
from sookit.core.task_queue import TaskType, ProgressParser


def _kill_process_tree(pid: int) -> None:
    """用 taskkill /T /F 递归终止 pid 的整个进程树（Windows）。

    /T 递归终止 pid 的所有子进程（如 yt-dlp launcher → real yt-dlp → aria2c），
    /F 强制终止。仅针对该 PID 的进程树，不按进程名全局杀，不会误伤其他任务。
    失败 / 进程已退出时静默忽略，绝不抛出导致崩溃。
    """
    if not pid or sys.platform != "win32":
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception:  # noqa: BLE001
        pass


# ---------- 单命令执行器 ----------
class SingleCmdWorker(QObject):
    """并行执行一条命令，合并 stdout+stderr，返回版本字符串"""
    finished = pyqtSignal(object)

    def __init__(self, cmd, parser=None):
        super().__init__()
        self.cmd = cmd
        self.parser = parser

    def run(self):
        try:
            r = subprocess.run(self.cmd, capture_output=True, text=True, timeout=20,
                               creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            raw = (r.stdout + "\n" + r.stderr).strip()
            if self.parser:
                out = self.parser(raw)
            else:
                out = raw.splitlines()[0] if raw else ""
            self.finished.emit(out)
        except Exception:
            self.finished.emit("")


# ---------- 通用工作线程 ----------
class Worker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)

    def __init__(self, func, args):
        super().__init__()
        self.func = func
        self.args = args

    def run(self):
        try:
            self.func(*self.args, log=self.log_signal.emit)
            self.finished_signal.emit(True)
        except Exception as e:
            self.log_signal.emit(f"错误: {e}")
            self.finished_signal.emit(False)


class GenericWorker(QThread):
    """通用异步任务执行器: 调用函数并返回结果/错误"""
    done = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, func, args=None, kwargs=None):
        super().__init__()
        self.func = func
        self.args = args or ()
        self.kwargs = kwargs or {}
        self._stop_flag = False

    def run(self):
        try:
            result = self.func(*self.args, **self.kwargs)
            if not self._stop_flag:
                self.done.emit(result)
        except Exception as e:
            if not self._stop_flag:
                self.error.emit(str(e))

    def request_stop(self):
        self._stop_flag = True


# ---------- 直播监控工作线程 ----------
class MonitorWorker(QThread):
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str, str)
    done_signal = pyqtSignal(str, bool)
    # 检测到可下载时发出，由监控页提交到全局任务队列
    download_ready = pyqtSignal(str, str, str, str, dict, dict)
    #                      task_id, url, output_dir, format_spec, download_config, info

    def __init__(self, task_id, url, output_dir, interval, remote_components,
                 concurrent_fragments=10, use_aria2c=True, aria2c_connections=16):
        super().__init__()
        self.task_id = task_id
        self.url = url
        self.output_dir = output_dir
        self.interval = interval
        self.remote = remote_components
        self.concurrent_fragments = concurrent_fragments
        self.use_aria2c = use_aria2c
        self.aria2c_connections = aria2c_connections
        self._stop_flag = False

    def stop(self):
        self._stop_flag = True

    def update_interval(self, interval):
        """动态更新轮询间隔"""
        self.interval = interval
        self.log_signal.emit(f"[{self.task_id}] 轮询间隔已更新为 {interval} 秒")

    def update_remote(self, remote):
        """动态更新 remote-components 设置"""
        self.remote = remote
        self.log_signal.emit(f"[{self.task_id}] remote-components 已{'启用' if remote else '禁用'}")

    def update_output_dir(self, output_dir):
        """动态更新保存目录"""
        self.output_dir = output_dir
        self.log_signal.emit(f"[{self.task_id}] 保存目录已更新为: {output_dir}")

    def run(self):
        retries = 0
        max_retries = 3
        while not self._stop_flag:
            try:
                self.log_signal.emit(f"[{self.task_id}] 检测直播状态...")
                info = Functions.check_live_status(self.url)
                status = info.get('live_status', 'unknown')

                if status in ('was_live', 'post_live', 'not_live'):
                    self.log_signal.emit(f"[{self.task_id}] 可下载，提交下载任务...")
                    self.status_signal.emit(self.task_id, '下载中')
                    config = {
                        'format_spec': 'bestvideo+bestaudio/best',
                        'concurrent_fragments': self.concurrent_fragments,
                        'use_aria2c': self.use_aria2c,
                        'aria2c_connections': self.aria2c_connections,
                        'remote': self.remote,
                    }
                    self.download_ready.emit(
                        self.task_id, self.url, self.output_dir,
                        'bestvideo+bestaudio/best', config, info)
                    # 立即结束，防止重复提交
                    return

                elif status == 'is_live':
                    self.log_signal.emit(f"[{self.task_id}] 正在直播中，{self.interval}秒后重试")
                    self.status_signal.emit(self.task_id, '直播中')

                elif status == 'is_upcoming':
                    self.log_signal.emit(f"[{self.task_id}] Premiere 尚未开始，{self.interval}秒后重试")
                    self.status_signal.emit(self.task_id, '等待中')

                else:
                    self.log_signal.emit(f"[{self.task_id}] 状态={status}，{self.interval}秒后重试")
                    self.status_signal.emit(self.task_id, status)

                retries = 0
                for _ in range(self.interval):
                    if self._stop_flag:
                        return
                    self.msleep(1000)

            except Exception as e:
                retries += 1
                self.log_signal.emit(f"[{self.task_id}] 检测出错: {e}")
                if retries >= max_retries:
                    self.log_signal.emit(f"[{self.task_id}] 连续 {max_retries} 次失败，终止")
                    self.status_signal.emit(self.task_id, '失败')
                    self.done_signal.emit(self.task_id, False)
                    return
                for _ in range(10):
                    if self._stop_flag:
                        return
                    self.msleep(1000)


# ---------- 任务队列工作线程 ----------
class TaskWorker(QThread):
    """支持暂停/继续/取消的任务工作线程"""
    log_signal = pyqtSignal(str)
    progress_signal = pyqtSignal(dict)   # {"progress", "speed", "eta"}
    finished_signal = pyqtSignal(bool)

    def __init__(self, task_id, task_type, func, args, workspace=""):
        super().__init__()
        self.task_id = task_id
        self.task_type = task_type
        self.func = func
        self.args = args
        self._paused = False
        self._cancelled = False
        self._process = None
        self._process_lock = threading.Lock()
        self.error = ""              # 任务失败时的错误信息（供 task_queue / UI 弹窗）
        # 该任务的独立临时工作目录（由 Task 生命周期管理，Worker 只负责使用）。
        # 所有临时文件（.part/.aria2/.ytdl/.f<ID> 等）都在 workspace 内。
        self.workspace = workspace
        # 最终交付文件路径列表（由 run_ytdlp 的 --print-to-file 收集，多格式可多个）
        self.output_files = []

    def delete_workspace(self) -> None:
        """删除整个 task workspace（含所有临时文件）。失败静默。"""
        if self.workspace:
            try:
                import shutil
                shutil.rmtree(self.workspace, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass

    def pause(self):
        """暂停任务 (Windows: NtSuspendProcess)"""
        self._paused = True
        with self._process_lock:
            if self._process and sys.platform == 'win32':
                try:
                    import ctypes
                    from ctypes import wintypes
                    ntdll = ctypes.WinDLL('ntdll.dll')
                    # NtSuspendProcess(HANDLE ProcessHandle) -> NTSTATUS
                    ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
                    ntdll.NtSuspendProcess.restype = wintypes.LONG
                    handle = ctypes.windll.kernel32.OpenProcess(
                        0x0001 | 0x0010, False, self._process.pid)  # PROCESS_SUSPEND_RESUME | PROCESS_TERMINATE
                    if handle:
                        ntdll.NtSuspendProcess(handle)
                        ctypes.windll.kernel32.CloseHandle(handle)
                except Exception:
                    pass

    def resume(self):
        """继续任务 (Windows: NtResumeProcess)"""
        self._paused = False
        with self._process_lock:
            if self._process and sys.platform == 'win32':
                try:
                    import ctypes
                    from ctypes import wintypes
                    ntdll = ctypes.WinDLL('ntdll.dll')
                    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
                    ntdll.NtResumeProcess.restype = wintypes.LONG
                    handle = ctypes.windll.kernel32.OpenProcess(
                        0x0001 | 0x0010, False, self._process.pid)  # PROCESS_SUSPEND_RESUME | PROCESS_TERMINATE
                    if handle:
                        ntdll.NtResumeProcess(handle)
                        ctypes.windll.kernel32.CloseHandle(handle)
                except Exception:
                    pass

    def cancel(self, delete_part: bool = True):
        """取消任务：终止当前任务的整个下载进程树（yt-dlp launcher → real → aria2c）。

        用 taskkill /T /F 按本 worker 的 launcher PID 递归终止，只杀本任务进程树，
        不按进程名全局杀，不会影响其他任务/updater。taskkill 失败或进程已退出时
        静默忽略；再以 _process.terminate() 兜底。仅对 Windows 生效。

        delete_part=True（默认，用户主动取消/关闭）：删除整个 task workspace。
        delete_part=False（暂停保留续传）：保留 workspace。
        """
        self._cancelled = True
        with self._process_lock:
            proc = self._process
        if proc is not None:
            _kill_process_tree(getattr(proc, "pid", None))
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        if delete_part:
            self.delete_workspace()

    def _on_process_created(self, process):
        """保存子进程引用"""
        with self._process_lock:
            self._process = process

    def run(self):
        """执行任务"""
        try:
            # 包装 log 回调以支持进度解析
            def log_with_progress(msg):
                if self._cancelled:
                    return
                
                # 等待暂停结束
                while self._paused and not self._cancelled:
                    self.msleep(100)
                
                self.log_signal.emit(msg)
                
                # 解析进度
                progress_data = None
                if self.task_type == TaskType.YTDLP:
                    progress_data = ProgressParser.parse_ytdlp_output(msg)
                elif self.task_type == TaskType.FFMPEG:
                    # ffmpeg 需要 total_duration，从 metadata 获取
                    total_duration = self.args[-1] if len(self.args) > 0 and isinstance(self.args[-1], (int, float)) else None
                    progress_data = ProgressParser.parse_ffmpeg_output(msg, total_duration)
                
                if progress_data:
                    self.progress_signal.emit(progress_data)

            # 执行函数
            self._output_path = None
            if self.task_type == TaskType.YTDLP:
                # yt-dlp 任务：传 workspace（临时目录），run_ytdlp 返回最终输出路径列表
                result = self.func(*self.args, log=log_with_progress,
                                   on_process_created=self._on_process_created,
                                   workspace=self.workspace)
            elif self.task_type == TaskType.FFMPEG:
                # ffmpeg 任务也需要保存进程句柄，取消时才能 taskkill 终止
                result = self.func(*self.args, log=log_with_progress,
                                   on_process_created=self._on_process_created)
            else:
                result = self.func(*self.args, log=log_with_progress)
            # 捕获返回的最终输出路径列表（yt-dlp 多格式可能返回多个）
            if isinstance(result, list):
                self.output_files = [p for p in result if p]
                if self.output_files:
                    self._output_path = self.output_files[0]
            
            if not self._cancelled:
                self.finished_signal.emit(True)
        except Exception as e:
            if not self._cancelled:
                self.error = str(e)
                self.log_signal.emit(f"错误: {e}")
                self.finished_signal.emit(False)
