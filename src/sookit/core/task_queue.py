"""
core/task_queue.py
任务队列管理器、任务模型、进度解析器
"""

import os
import json
import re
import time
import uuid
import random
import threading
from enum import Enum
from PyQt6.QtCore import QObject, pyqtSignal

from sookit.paths import get_cover_dir, get_data_dir

# ULID 使用的 Crockford Base32 字符集
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _generate_ulid() -> str:
    """生成完整 ULID（26 字符，Crockford Base32）。

    结构：前 10 字符为 48 位毫秒时间戳，后 16 字符为 80 位随机数。
    与任务标题/URL 无关，只作为 task workspace 的唯一标识。
    """
    ts = int(time.time() * 1000)
    # 48 位时间戳 -> 10 个 base32 字符
    ts_str = ""
    for _ in range(10):
        ts_str = _CROCKFORD[ts & 0x1F] + ts_str
        ts >>= 5
    # 80 位随机数 -> 16 个 base32 字符
    rand_str = "".join(_CROCKFORD[random.getrandbits(5)] for _ in range(16))
    return ts_str + rand_str


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
        # ---- workspace（仅 yt-dlp/aria2c 下载任务使用）----
        self.output_dir = ""         # 用户目标目录
        self.workspace = ""          # output_dir/._sookit_tmp_<ULID>/，Task 生命周期管理
        self.output_files = []       # 最终交付文件路径列表（由 run_ytdlp 收集）
        
        # yt-dlp 专用元数据
        self.channel = metadata.get("channel", "") if metadata else ""
        self.duration = metadata.get("duration", "") if metadata else ""
        self.cover_url = metadata.get("cover_url", "") if metadata else ""


# ---------- 已完成任务持久化 ----------

COMPLETED_FILE = str(get_data_dir() / 'completed_tasks.json')

# 当前未完成 workspace registry（启动清理残留用）。
# 只记录 Sookit 自己创建的 workspace 完整路径，不扫描任意用户目录。
ACTIVE_WORKSPACES_FILE = str(get_data_dir() / 'active_workspaces.json')


def _load_active_workspaces() -> list:
    """读取 active_workspaces registry，返回 workspace 路径列表。"""
    try:
        if os.path.exists(ACTIVE_WORKSPACES_FILE):
            with open(ACTIVE_WORKSPACES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                return [str(p) for p in data]
    except Exception:
        pass
    return []


def _save_active_workspaces(paths: list) -> None:
    """原子保存 active_workspaces registry（先写临时文件再 os.replace）。"""
    try:
        os.makedirs(os.path.dirname(ACTIVE_WORKSPACES_FILE), exist_ok=True)
        tmp = ACTIVE_WORKSPACES_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(paths, f, indent=2)
        os.replace(tmp, ACTIVE_WORKSPACES_FILE)
    except Exception:
        pass


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
        # 当前未完成 workspace 的 registry（内存副本 + 持久化）
        self._active_workspaces = []
        # 启动时恢复已完成任务
        self._load_completed_tasks()
        # 清理上次异常退出遗留的 workspace（读 registry，只清自己登记的）
        self._cleanup_stale_workspaces()

    def _register_workspace(self, path: str) -> None:
        """登记一个 workspace（先写 registry 原子保存，确保即使后续创建失败/崩溃也能被清理）。"""
        if not path or path in self._active_workspaces:
            return
        self._active_workspaces.append(path)
        _save_active_workspaces(self._active_workspaces)

    def _unregister_workspace(self, path: str) -> None:
        """从 registry 移除一个 workspace（在删除 workspace 后调用）。"""
        if path in self._active_workspaces:
            self._active_workspaces.remove(path)
            _save_active_workspaces(self._active_workspaces)

    def _cleanup_stale_workspaces(self):
        """清理上次异常退出遗留的临时工作目录。

        只读取 registry 中登记的 workspace 路径，逐个删除；删除不存在或已清理的，
        从 registry 移除记录。不做全盘扫描，不依赖 completed_tasks，不删用户文件。
        """
        import shutil
        self._active_workspaces = _load_active_workspaces()
        remaining = []
        for ws in self._active_workspaces:
            try:
                if os.path.isdir(ws):
                    shutil.rmtree(ws, ignore_errors=True)
                # 无论是否删除成功，从 registry 移除（已不存在或已清理）
            except Exception:
                pass
        _save_active_workspaces([])

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
        
        # 仅 yt-dlp/aria2c 下载任务使用独立 workspace（由 Task 生命周期管理）
        if task.task_type == TaskType.YTDLP:
            self._ensure_workspace(task)
        
        task.status = TaskStatus.RUNNING
        self.task_updated.emit(task)
        
        # 创建并启动 worker
        from sookit.core.workers import TaskWorker
        worker = TaskWorker(task_id, task.task_type, task.func, task.args,
                            workspace=task.workspace)
        task.worker = worker
        
        # 连接信号
        worker.progress_signal.connect(lambda p: self._on_progress(task_id, p))
        worker.log_signal.connect(lambda msg: self._on_log(task_id, msg))
        worker.finished_signal.connect(lambda ok: self._on_finished(task_id, ok))
        
        worker.start()
    
    def _ensure_workspace(self, task):
        """为 yt-dlp 下载任务创建独立 workspace：output_dir/._sookit_tmp_<ULID>/。

        workspace 由 Task 生命周期管理（创建/删除都由 Task/TaskQueue 控制），
        Worker 只负责使用 task.workspace。临时目录在用户目标目录内部，同卷可原子移动。
        """
        out_dir = (task.metadata or {}).get('out_dir', '')
        if not out_dir:
            # 无 out_dir 时不启用 workspace（download_youtube 回退用 output_dir 参数）
            task.output_dir = ''
            task.workspace = ''
            return
        task.output_dir = out_dir
        workspace = os.path.join(out_dir, f"._sookit_tmp_{_generate_ulid()}")
        # 先登记 registry（原子保存），再创建目录。
        # 顺序保证：宁可 registry 记录一个尚不存在的 workspace，也不漏清实际已创建的。
        self._register_workspace(workspace)
        try:
            os.makedirs(workspace, exist_ok=True)
            # Windows 隐藏属性（仅隐藏，不作安全机制）
            if os.name == 'nt':
                try:
                    import ctypes
                    ctypes.windll.kernel32.SetFileAttributesW(workspace, 0x2)  # FILE_ATTRIBUTE_HIDDEN
                except Exception:
                    pass
            task.workspace = workspace
        except OSError:
            # 创建失败则不启用 workspace，回退直接写 out_dir；同时从 registry 移除
            task.workspace = ''
            self._unregister_workspace(workspace)

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
    
    def cancel_task(self, task_id: str, delete_part: bool = True):
        """取消指定任务 - 直接丢弃。

        delete_part=True（默认）：终止进程树后删除该任务自己的 .part/.part.aria2。
        delete_part=False：终止进程但保留临时文件。
        """
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
            worker.cancel(delete_part)
            # 取消（delete_part=True）删除了 workspace，从 registry 移除登记
            if delete_part and task.workspace:
                self._unregister_workspace(task.workspace)
            # 保留 worker 引用，防止 QThread 在后台线程尚未退出时被 GC 回收，
            # 避免 "QThread: Destroyed while thread is still running" 崩溃
            self._cancelling_workers.add(worker)
            # 使用 Qt 内建的 finished 信号（无论是否取消都会触发），
            # 在 worker 真正停止后自动释放引用
            worker.finished.connect(
                lambda w=worker: self._cancelling_workers.discard(w))

    def has_running_tasks(self) -> bool:
        """是否有正在运行的下载任务（供关闭 Sookit 时确认弹窗判断）。"""
        return any(t.status == TaskStatus.RUNNING for t in self.active_tasks.values())

    def cancel_all(self, delete_part: bool = True):
        """取消所有任务（供 Sookit 退出时统一清理下载进程树）。

        对每个任务只通过其自己的 launcher PID 清理自己的进程树，不按进程名全局杀，
        因此不影响独立运行的 updater.exe。用快照遍历，避免 cancel_task 内部
        修改 active_tasks 导致迭代冲突。

        delete_part=True（默认）：终止进程树并删除各任务自己的 .part/.part.aria2。
        delete_part=False：终止进程但保留临时文件。
        """
        for task_id in list(self.active_tasks.keys()):
            self.cancel_task(task_id, delete_part)
    
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
            # workspace 任务：把最终输出文件移动到用户目标目录（防覆盖 + fallback），
            # 全部移动成功后才删除 workspace
            if task.workspace:
                moved, final_paths = self._finalize_workspace(task)
                if final_paths:
                    task.output_path = final_paths[0]
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
                # 下载失败：删除整个 task workspace（放弃当前进度），并从 registry 移除
                task.worker.delete_workspace()
                if task.workspace:
                    self._unregister_workspace(task.workspace)
            self.task_updated.emit(task)
            self.task_failed.emit(task)
            # 失败任务保留在活跃列表中显示

    def _finalize_workspace(self, task):
        """把 workspace 中的最终输出文件移动到用户目标目录。

        返回 (moved_ok, final_paths)：
        - moved_ok: 是否所有文件都成功移动到目标目录
        - final_paths: 移动后的最终路径列表

        规则：
        - 逐个移动 task.worker.output_files 到 task.output_dir（同卷原子 rename）。
        - 防覆盖：目标已存在时追加后缀 _1/_2。
        - 全部成功 → 删除 workspace，返回 True。
        - 任一失败 → 不删 workspace（保留已下载文件），fallback 到系统 Downloads 目录；
          再失败则保留 workspace（文件仍暂存在 workspace，避免丢失）。
        """
        srcs = list(getattr(task.worker, 'output_files', []) or [])
        if not srcs:
            # 无明确 output_files，尝试移动 workspace 内所有普通文件（兜底）
            try:
                srcs = [os.path.join(task.workspace, f)
                        for f in os.listdir(task.workspace)
                        if os.path.isfile(os.path.join(task.workspace, f))]
            except OSError:
                srcs = []

        def _move_one(src, dest_dir):
            """移动到 dest_dir，防覆盖加后缀。返回移动后的路径或 None。"""
            name = os.path.basename(src)
            target = os.path.join(dest_dir, name)
            counter = 1
            while os.path.exists(target):
                base, ext = os.path.splitext(name)
                target = os.path.join(dest_dir, f"{base}_{counter}{ext}")
                counter += 1
            os.replace(src, target)
            return target

        moved = []
        # 第一优先：用户目标目录
        dest_candidates = [task.output_dir]
        if not task.output_dir:
            dest_candidates = []
        # 第二优先：系统 Downloads
        try:
            downloads = os.path.join(os.path.expanduser('~'), 'Downloads')
            dest_candidates.append(downloads)
        except Exception:
            pass

        for src in srcs:
            placed = None
            for dest_dir in dest_candidates:
                if not dest_dir or not os.path.isdir(dest_dir):
                    continue
                try:
                    placed = _move_one(src, dest_dir)
                    break
                except OSError:
                    continue
            if placed:
                moved.append(placed)

        all_moved = len(moved) == len(srcs) and len(srcs) > 0
        if all_moved:
            # 全部成功 → 删 workspace，并从 registry 移除
            if task.worker:
                task.worker.delete_workspace()
            if task.workspace:
                self._unregister_workspace(task.workspace)
        # 任一失败：不删 workspace（文件保留），moved 中已移动的已安全
        return (all_moved, moved)

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