"""取消期间线程退出先后顺序的确定性回归，不启动进程或读写用户配置。"""
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QObject, pyqtSignal

from sookit.core.task_queue import Task, TaskQueueManager, TaskStatus, TaskType


class EndingWorker(QObject):
    finished = pyqtSignal()

    def __init__(self, running=True, finish_in_cancel=True):
        super().__init__()
        self.running = running
        self.finish_in_cancel = finish_in_cancel

    def cancel(self, delete_part=True):
        if self.running and self.finish_in_cancel:
            self.running = False
            self.finished.emit()

    def isRunning(self):
        return self.running


class QueueCancellationTests(unittest.TestCase):
    def setUp(self):
        with patch.object(TaskQueueManager, '_load_completed_tasks'), \
             patch.object(TaskQueueManager, '_cleanup_stale_workspaces'):
            self.manager = TaskQueueManager()
        self.manager.max_concurrent = 0

    def cancel(self, worker):
        task = Task('cancel-race', TaskType.YTDLP, 'cancel', lambda: None, ())
        task.status = TaskStatus.RUNNING
        task.worker = worker
        self.manager.active_tasks[task.task_id] = task
        self.manager.task_order.append(task.task_id)
        self.manager.cancel_task(task.task_id)
        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertEqual(self.manager.active_tasks, {})

    def test_worker_finishing_inside_cancel_releases_reference(self):
        self.cancel(EndingWorker())
        self.assertEqual(self.manager._cancelling_workers, set())

    def test_worker_already_finished_before_cancel_releases_reference(self):
        self.cancel(EndingWorker(running=False))
        self.assertEqual(self.manager._cancelling_workers, set())

    def test_still_running_worker_is_retained_until_finished(self):
        worker = EndingWorker(finish_in_cancel=False)
        self.cancel(worker)
        self.assertEqual(self.manager._cancelling_workers, {worker})
        worker.running = False
        worker.finished.emit()
        self.assertEqual(self.manager._cancelling_workers, set())


if __name__ == '__main__':
    unittest.main()
