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
            r = subprocess.run(self.cmd, capture_output=True, text=True, timeout=20)
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

    def __init__(self, task_id, url, output_dir, interval, remote_components,
                 concurrent_fragments=4, use_aria2c=True, aria2c_connections=16):
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
        self._process = None  # 存储子进程引用
        self._process_lock = threading.Lock()  # 保护 _process 的读写

    def stop(self):
        self._stop_flag = True
        # 如果有正在运行的子进程，终止它
        with self._process_lock:
            if self._process is not None:
                try:
                    self._process.terminate()
                    self._process = None
                except Exception:
                    pass

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
                    self.log_signal.emit(f"[{self.task_id}] 可下载，开始下载最高画质...")
                    self.status_signal.emit(self.task_id, '下载中')
                    try:
                        # 使用 on_process_created 回调立即保存子进程引用
                        def on_process_created(process):
                            with self._process_lock:
                                self._process = process
                        Functions.download_youtube(
                            self.url, 'bestvideo+bestaudio/best',
                            self.output_dir, self.remote,
                            concurrent_fragments=self.concurrent_fragments,
                            use_aria2c=self.use_aria2c,
                            aria2c_connections=self.aria2c_connections,
                            log=self.log_signal.emit,
                            on_process_created=on_process_created)
                        # 下载完成后清理引用
                        with self._process_lock:
                            self._process = None
                        self.log_signal.emit(f"[{self.task_id}] 下载完成!")
                        self.done_signal.emit(self.task_id, True)
                        return
                    except Exception as e:
                        # 如果被终止则不重试
                        if self._stop_flag:
                            self.log_signal.emit(f"[{self.task_id}] 下载已取消")
                            self.done_signal.emit(self.task_id, False)
                            return
                        # 下载失败（如 premiere 刚结束还在处理中），不递增 retries，
                        # 直接等待一轮后重新检测状态
                        self.log_signal.emit(
                            f"[{self.task_id}] 下载失败: {e}，{self.interval}秒后重试")
                        self.status_signal.emit(self.task_id, '等待重试')

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

    def __init__(self, task_id, task_type, func, args):
        super().__init__()
        self.task_id = task_id
        self.task_type = task_type
        self.func = func
        self.args = args
        self._paused = False
        self._cancelled = False
        self._process = None
        self._process_lock = threading.Lock()

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

    def cancel(self):
        """取消任务"""
        self._cancelled = True
        with self._process_lock:
            if self._process:
                try:
                    self._process.terminate()
                except Exception:
                    pass

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
                elif self.task_type == TaskType.M3U8:
                    progress_data = ProgressParser.parse_ytdlp_output(msg)
                
                if progress_data:
                    self.progress_signal.emit(progress_data)

            # 执行函数
            self._output_path = None
            if self.task_type == TaskType.YTDLP:
                # yt-dlp 任务需要 on_process_created 回调
                result = self.func(*self.args, log=log_with_progress,
                                   on_process_created=self._on_process_created)
            else:
                result = self.func(*self.args, log=log_with_progress)
            # 捕获返回的路径（如果是字符串）
            if isinstance(result, str):
                self._output_path = result
            
            if not self._cancelled:
                self.finished_signal.emit(True)
        except Exception as e:
            if not self._cancelled:
                self.log_signal.emit(f"错误: {e}")
                self.finished_signal.emit(False)
