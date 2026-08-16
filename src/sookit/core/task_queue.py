"""
core/task_queue.py
任务队列管理器、任务模型、进度解析器
"""

import os
import json
import re
import uuid
import threading
from enum import Enum
from PyQt6.QtCore import QObject, pyqtSignal

from sookit.paths import get_cover_dir, get_data_dir


# ---------- 封面缓存目录 ----------
COVER_CACHE_DIR = str(get_cover_dir())



# ---------- 任务状态枚举 ----------

class TaskStatus(Enum):
    WAITING = "waiting"       # 等待中
    RUNNING = "running"       # 运行中
    PAUSED = "paused"         # 暂停中
    COMPLETED = "completed"   # 已完成
    FAILED = "failed"         # 失败
    CANCELLED = "cancelled"   # 已取消（直接丢弃，不显示）


# ---------- 任务类型枚举 ----------

class TaskType(Enum):
    YTDLP = "ytdlp"      # yt-dlp 下载任务
    M3U8 = "m3u8"        # m3u8 下载任务
    FFMPEG = "ffmpeg"    # ffmpeg 转码任务


# ---------- 任务数据模型 ----------

class Task:
    """任务数据模型"""
    def __init__(self, task_id: str, task_type: TaskType,
                 title: str, func, args, metadata: dict = None):
        self.task_id = task_id
        self.task_type = task_type
        self.title = title           # 显示名称（视频标题/文件名）
        self.func = func             # 执行函数
        self.args = args             # 函数参数
        self.metadata = metadata or {}  # 额外元数据
        self.status = TaskStatus.WAITING
        self.progress = 0.0          # 0-100
        self.speed = ""              # 下载/转码速度
        self.eta = ""                # 预计完成时间
        self.worker = None           # TaskWorker 实例
        self.output_path = ""        # 输出文件路径（用于打开文件夹）
        self.error = ""              # 失败时的错误信息（供 UI 弹窗提示）
        
        # yt-dlp 专用元数据
        self.channel = metadata.get("channel", "") if metadata else ""
        self.duration = metadata.get("duration", "") if metadata else ""
        self.cover_url = metadata.get("cover_url", "") if metadata else ""


# ---------- 已完成任务持久化 ----------

COMPLETED_FILE = str(get_data_dir() / 'completed_tasks.json')


def _task_to_dict(task: 'Task') -> dict:
    """将 Task 序列化为可 JSON 存储的 dict (排除可执行对象 + 二进制数据)"""
    meta = dict(task.metadata)
    # 封面数据缓存到本地文件，不塞进 JSON
    cover_data = meta.pop('cover_data', None)
    if cover_data:
        os.makedirs(COVER_CACHE_DIR, exist_ok=True)
        cover_path = os.path.join(COVER_CACHE_DIR, f"{task.task_id}.jpg")
        try:
            with open(cover_path, 'wb') as cf:
                cf.write(cover_data)
        except Exception:
            pass
    # 移除其他可能残留的二进制数据
    for key in list(meta.keys()):
        if isinstance(meta[key], (bytes, bytearray)):
            del meta[key]
    return {
        'task_id': task.task_id,
        'task_type': task.task_type.value,
        'title': task.title,
        'output_path': task.output_path,
        'metadata': meta,
    }


def _dict_to_task(d: dict) -> 'Task':
    """从 dict 还原 Task (已完成状态, func/args 用占位)"""
    meta = dict(d.get('metadata', {}))
    # 从本地缓存加载封面
    task_id = d['task_id']
    cover_path = os.path.join(COVER_CACHE_DIR, f"{task_id}.jpg")
    if os.path.exists(cover_path):
        try:
            with open(cover_path, 'rb') as cf:
                meta['cover_data'] = cf.read()
        except Exception:
            pass
    task = Task(
        task_id=task_id,
        task_type=TaskType(d['task_type']),
        title=d['title'],
        func=lambda: None,
        args=(),
        metadata=meta,
    )
    task.output_path = d.get('output_path', '')
    if not task.output_path:
        for key in ('out', 'output', 'out_dir'):
            val = meta.get(key)
            if val:
                task.output_path = val
                break
    task.status = TaskStatus.COMPLETED
    task.progress = 100.0
    return task


# ---------- 任务队列管理器（单例）----------

class TaskQueueManager(QObject):
    """任务队列管理器 - 单例模式"""
    _instance = None
    
    # 信号
    task_added = pyqtSignal(object)        # 新任务添加 (Task)
    task_updated = pyqtSignal(object)      # 任务状态/进度更新 (Task)
    task_completed = pyqtSignal(object)    # 任务完成 (Task)
    task_failed = pyqtSignal(object)       # 任务失败 (Task)
    task_removed = pyqtSignal(str)         # 任务移除 (task_id)
    task_log = pyqtSignal(str, str)        # 任务日志 (task_id, msg)
    
    _instance_lock = threading.Lock()

    @classmethod
    def instance(cls):
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    def __init__(self):
        super().__init__()
        self.active_tasks = {}       # task_id -> Task (进行中/等待中/暂停中)
        self.completed_tasks = {}    # task_id -> Task (已完成)
        self.task_order = []         # 活跃任务顺序列表
        self.max_concurrent = 3      # 最大并发任务数
        self._cancelling_workers = set()  # 保留取消中的 worker 引用，防止 QThread 被 GC
        self._cancelled_task_ids = set()  # 已取消的 task_id，防止 finished_signal 误将取消任务移入已完成
        # 启动时恢复已完成任务
        self._load_completed_tasks()
    
    def add_task(self, task_type: TaskType, title: str, func, args,
                 metadata: dict = None, output_path: str = "") -> Task:
        """添加新任务到队列"""
        task_id = str(uuid.uuid4())[:8]
        task = Task(task_id, task_type, title, func, args, metadata)
        task.output_path = output_path
        
        self.active_tasks[task_id] = task
        self.task_order.append(task_id)
        
        self.task_added.emit(task)
        self._process_queue()
        
        return task
    
    def start_task(self, task_id: str):
        """启动指定任务"""
        task = self.active_tasks.get(task_id)
        if not task or task.status != TaskStatus.WAITING:
            return
        
        # 检查并发数
        running_count = sum(1 for t in self.active_tasks.values()
                          if t.status == TaskStatus.RUNNING)
        if running_count >= self.max_concurrent:
            return  # 等待其他任务完成
        
        task.status = TaskStatus.RUNNING
        self.task_updated.emit(task)
        
        # 创建并启动 worker
        from sookit.core.workers import TaskWorker
        worker = TaskWorker(task_id, task.task_type, task.func, task.args)
        task.worker = worker
        
        # 连接信号
        worker.progress_signal.connect(lambda p: self._on_progress(task_id, p))
        worker.log_signal.connect(lambda msg: self._on_log(task_id, msg))
        worker.finished_signal.connect(lambda ok: self._on_finished(task_id, ok))
        
        worker.start()
    
    def pause_task(self, task_id: str):
        """暂停指定任务"""
        task = self.active_tasks.get(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return
        
        task.status = TaskStatus.PAUSED
        if task.worker:
            task.worker.pause()
        self.task_updated.emit(task)
    
    def resume_task(self, task_id: str):
        """继续指定任务"""
        task = self.active_tasks.get(task_id)
        if not task or task.status != TaskStatus.PAUSED:
            return
        
        task.status = TaskStatus.RUNNING
        if task.worker:
            task.worker.resume()
        self.task_updated.emit(task)
    
    def cancel_task(self, task_id: str):
        """取消指定任务 - 直接丢弃"""
        task = self.active_tasks.get(task_id)
        if not task:
            return
        
        task.status = TaskStatus.CANCELLED
        self._cancelled_task_ids.add(task_id)
        
        # 立即从活跃列表移除（不阻塞 UI 线程）
        self._remove_active_task(task_id)
        
        # 在后台处理 worker 终止
        if task.worker:
            worker = task.worker
            worker.cancel()
            # 保留 worker 引用，防止 QThread 在后台线程尚未退出时被 GC 回收，
            # 避免 "QThread: Destroyed while thread is still running" 崩溃
            self._cancelling_workers.add(worker)
            # 使用 Qt 内建的 finished 信号（无论是否取消都会触发），
            # 在 worker 真正停止后自动释放引用
            worker.finished.connect(
                lambda w=worker: self._cancelling_workers.discard(w))

    def cancel_all(self):
        """取消所有任务（供 Sookit 退出时统一清理下载进程树）。

        对每个任务只通过其自己的 launcher PID 清理自己的进程树，不按进程名全局杀，
        因此不影响独立运行的 updater.exe。用快照遍历，避免 cancel_task 内部
        修改 active_tasks 导致迭代冲突。
        """
        for task_id in list(self.active_tasks.keys()):
            self.cancel_task(task_id)
    
    def _remove_active_task(self, task_id: str):
        """从活跃任务列表移除"""
        if task_id in self.active_tasks:
            del self.active_tasks[task_id]
        if task_id in self.task_order:
            self.task_order.remove(task_id)
        self.task_removed.emit(task_id)
        self._process_queue()
    
    def _on_progress(self, task_id: str, progress_data: dict):
        """进度更新回调"""
        task = self.active_tasks.get(task_id)
        if not task:
            return
        
        task.progress = progress_data.get("progress", task.progress)
        task.speed = progress_data.get("speed", task.speed)
        task.eta = progress_data.get("eta", task.eta)
        self.task_updated.emit(task)
    
    def _on_log(self, task_id: str, msg: str):
        """日志回调 - 转发给关联的页面"""
        self.task_log.emit(task_id, msg)
    
    def _on_finished(self, task_id: str, success: bool):
        """任务完成回调"""
        # 取消的任务不处理状态迁移
        if task_id in self._cancelled_task_ids:
            self._cancelled_task_ids.discard(task_id)
            return
        
        task = self.active_tasks.get(task_id)
        if not task:
            print(f"[TaskQueue] ⚠ _on_finished: task {task_id} not in active_tasks (success={success})")
            return
        
        if success:
            task.status = TaskStatus.COMPLETED
            task.progress = 100.0
            task.speed = ""
            task.eta = ""
            # 优先使用 worker 返回的精确文件路径（yt-dlp --print-to-file）
            if (hasattr(task.worker, '_output_path')
                    and task.worker._output_path
                    and os.path.isfile(task.worker._output_path)):
                task.output_path = task.worker._output_path
            # 从 metadata 回填输出路径
            if not task.output_path:
                for key in ('out', 'output', 'out_dir'):
                    val = task.metadata.get(key)
                    if val:
                        task.output_path = val
                        break
            # 移动到已完成列表
            self.completed_tasks[task_id] = task
            self._remove_active_task(task_id)
            self.task_completed.emit(task)
            # 持久化已完成任务
            self._save_completed_tasks()
            # 如果有 cover_data，同时缓存到本地
            cover_data = task.metadata.get('cover_data')
            if cover_data:
                os.makedirs(COVER_CACHE_DIR, exist_ok=True)
                cover_path = os.path.join(COVER_CACHE_DIR, f"{task_id}.jpg")
                try:
                    with open(cover_path, 'wb') as cf:
                        cf.write(cover_data)
                except Exception:
                    pass
        else:
            task.status = TaskStatus.FAILED
            task.speed = ""
            task.eta = ""
            # 记录失败原因，供 UI 弹窗提示
            if task.worker is not None:
                task.error = getattr(task.worker, 'error', "") or task.error
            self.task_updated.emit(task)
            self.task_failed.emit(task)
            # 失败任务保留在活跃列表中显示
    
    def _process_queue(self):
        """处理队列，启动等待中的任务"""
        running_count = sum(1 for t in self.active_tasks.values()
                          if t.status == TaskStatus.RUNNING)
        
        if running_count >= self.max_concurrent:
            return
        
        # 启动等待中的任务
        for task_id in self.task_order:
            task = self.active_tasks.get(task_id)
            if task and task.status == TaskStatus.WAITING:
                self.start_task(task_id)
                running_count += 1
                if running_count >= self.max_concurrent:
                    break
    
    def get_active_tasks(self) -> list:
        """获取所有活跃任务（等待中/运行中/暂停中/失败）"""
        return [self.active_tasks[tid] for tid in self.task_order
                if tid in self.active_tasks]
    
    def get_completed_tasks(self) -> list:
        """获取所有已完成任务"""
        return list(self.completed_tasks.values())

    def remove_completed_task(self, task_id: str):
        """从已完成列表中移除指定任务"""
        if task_id in self.completed_tasks:
            del self.completed_tasks[task_id]
            self.task_removed.emit(task_id)
            self._save_completed_tasks()

    def remove_failed_task(self, task_id: str):
        """从活跃列表中移除指定的 FAILED 任务（供监控页等清理失败任务用）"""
        task = self.active_tasks.get(task_id)
        if not task or task.status != TaskStatus.FAILED:
            return
        self._cancelled_task_ids.discard(task_id)
        self._remove_active_task(task_id)
    
    def _save_completed_tasks(self):
        """持久化已完成任务列表到 JSON 文件"""
        try:
            data = [_task_to_dict(t) for t in self.completed_tasks.values()]
            os.makedirs(os.path.dirname(COMPLETED_FILE), exist_ok=True)
            with open(COMPLETED_FILE, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception:
            pass  # 静默失败，不影响主流程
    
    def _load_completed_tasks(self):
        """启动时从 JSON 文件恢复已完成任务"""
        if not os.path.exists(COMPLETED_FILE):
            return
        try:
            with open(COMPLETED_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception as e:
            # 文件损坏时备份，避免静默丢失数据
            backup = COMPLETED_FILE + '.bak'
            try:
                os.replace(COMPLETED_FILE, backup)
                print(f"[TaskQueue] JSON 文件损坏，已备份为 {backup}: {e}")
            except Exception:
                pass
            return
        for item in data:
            try:
                task = _dict_to_task(item)
                self.completed_tasks[task.task_id] = task
            except Exception:
                pass


# ---------- 进度解析器 ----------

class ProgressParser:
    """进度解析器"""
    
    @staticmethod
    def parse_ytdlp_output(line: str) -> dict:
        """
        解析 yt-dlp 输出
        原生下载器: [download]   5.2% of   50.21MiB at  2.50MiB/s ETA 00:19
        aria2c:     [aria2c] Downloaded 5.2% of 50.21MiB at 2.50MiB/s ETA 00:19
        aria2c 原生: [#a1b2c3 0.1MiB/50.2MiB(5%) CN:2 DL:1.2MiB/s ETA:00:19]
        返回: {"progress": 5.2, "speed": "1.2MiB/s", "eta": "00:19"}
        """
        # 匹配进度百分比（兼容 yt-dlp 原生 [download] / aria2c 包装 [aria2c] 等前缀格式）
        progress_match = re.search(
            r'\[(?:download|aria2c|NativeDownloader|wget|curl)\]\s+'
            r'(?:Downloaded\s+)?(\d+\.?\d*)%', line)
        if progress_match:
            result = {"progress": float(progress_match.group(1))}

            # 匹配速度（yt-dlp/包装格式: at 2.50MiB/s）
            speed_match = re.search(r'at\s+(\d+\.?\d*\w+/s)', line)
            if speed_match:
                result["speed"] = speed_match.group(1)

            # 匹配 ETA（yt-dlp/包装格式: ETA 00:19）
            eta_match = re.search(r'ETA\s+(\d+:\d+)', line)
            if eta_match:
                result["eta"] = eta_match.group(1)

            return result

        # 回退：aria2c 原生进度行格式（[#<gid> 已下载/总大小(XX%) CN:x DL:速度 ETA:时间]），gid 为十六进制字符串
        aria2c_match = re.search(
            r'\[\#[0-9a-fA-F]+[^\n]*?\((\d+(?:\.\d+)?)%\)', line)
        if aria2c_match:
            result = {"progress": float(aria2c_match.group(1))}
            # aria2c 原生速度格式: DL:1.2MiB/s
            speed_match = re.search(r'DL:\s*(\d+\.?\d*\w+/s)', line)
            if speed_match:
                result["speed"] = speed_match.group(1)
            # aria2c 原生 ETA 格式: ETA:00:19
            eta_match = re.search(r'ETA:\s*(\d+:\d+)', line)
            if eta_match:
                result["eta"] = eta_match.group(1)
            return result

        return None
    
    @staticmethod
    def parse_ffmpeg_output(line: str, total_duration: float = None) -> dict:
        """
        解析 ffmpeg 输出
        格式: time=00:00:04.00 speed=2.0x
        返回: {"progress": 40.0, "speed": "2.0x", "eta": "00:06"}
        """
        result = {}
        
        # 匹配速度
        speed_match = re.search(r'speed=\s*(\d+\.?\d*)x', line)
        if speed_match:
            result["speed"] = f"{speed_match.group(1)}x"
        
        # 匹配时间
        time_match = re.search(r'time=(\d{2}):(\d{2}):(\d{2})\.(\d+)', line)
        if time_match and total_duration and total_duration > 0:
            h, m, s = int(time_match.group(1)), int(time_match.group(2)), int(time_match.group(3))
            current_time = h * 3600 + m * 60 + s
            progress = min((current_time / total_duration) * 100, 100)
            result["progress"] = progress
            
            # 计算 ETA
            speed_val = float(speed_match.group(1)) if speed_match else 1.0
            if speed_val > 0:
                remaining = total_duration - current_time
                eta_seconds = remaining / speed_val
                eta_m = int(eta_seconds // 60)
                eta_s = int(eta_seconds % 60)
                result["eta"] = f"{eta_m:02d}:{eta_s:02d}"
        
        return result if result else None