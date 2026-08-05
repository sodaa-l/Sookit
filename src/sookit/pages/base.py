"""
pages/base.py
页面基类
"""

import os
import logging
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFileDialog, QApplication

logger = logging.getLogger("Sookit")
from PyQt6.QtCore import Qt, QTimer, QMimeData
from PyQt6.QtGui import QFont, QDragEnterEvent, QDropEvent
import qfluentwidgets as qfw
from sookit.core.workers import Worker
from sookit.core.config import load_task_complete_action
from sookit.core.task_queue import TaskQueueManager, TaskType


class PageBase(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self._threads = []
        self._drop_target = None  # 拖放目标输入框（用于单文件输入页面）
        self._owned_task_ids = set()  # 本页面发起的任务 ID 集合
        self.setAcceptDrops(True)  # 启用拖放

    def dragEnterEvent(self, event: QDragEnterEvent):
        """拖入事件：接受包含文件 URL 的拖放"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        """放置事件：将文件路径填入目标输入框"""
        urls = event.mimeData().urls()
        if not urls:
            return
        
        # 获取第一个文件路径
        file_path = urls[0].toLocalFile()
        if not file_path:
            return
        
        # 优先填入注册的拖放目标（单文件输入页面）
        if self._drop_target and isinstance(self._drop_target, qfw.LineEdit):
            self._drop_target.setText(file_path)
            return
        
        # 其次查找当前焦点的 LineEdit
        focused = QApplication.focusWidget()
        if isinstance(focused, qfw.LineEdit):
            focused.setText(file_path)
            return
        
        # 最后查找鼠标位置下的 LineEdit
        for widget in self.findChildren(qfw.LineEdit):
            if widget.underMouse():
                widget.setText(file_path)
                return

    def _setup_log_area(self, layout):
        layout.addSpacing(20)

    def log(self, msg):
        print(msg)
        logger.info(msg)

    def browse_file(self, entry, file_types):
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", file_types)
        if path:
            entry.setText(path)

    def browse_dir(self, entry):
        path = QFileDialog.getExistingDirectory(self, "选择目录")
        if path:
            entry.setText(path)

    def add_file_row(self, grid, label, row, btn_text, file_types):
        lbl = qfw.BodyLabel(label)
        entry = qfw.LineEdit()
        entry.setPlaceholderText("请选择或拖入文件路径")
        btn = qfw.PushButton(btn_text)
        btn.setFixedWidth(100)
        btn.clicked.connect(lambda: self.browse_file(entry, file_types))
        grid.addWidget(lbl, row, 0)
        grid.addWidget(entry, row, 1)
        grid.addWidget(btn, row, 2)
        return entry

    def add_dir_row(self, grid, label, row, btn_text):
        lbl = qfw.BodyLabel(label)
        entry = qfw.LineEdit()
        entry.setPlaceholderText("请选择输出目录")
        btn = qfw.PushButton(btn_text)
        btn.setFixedWidth(100)
        btn.clicked.connect(lambda: self.browse_dir(entry))
        grid.addWidget(lbl, row, 0)
        grid.addWidget(entry, row, 1)
        grid.addWidget(btn, row, 2)
        return entry

    def create_start_button(self, parent, row, text, func, args_getter):
        btn = qfw.PrimaryPushButton(text)
        btn.setFixedWidth(200)
        btn.clicked.connect(lambda: self.run_task(func, args_getter()))
        parent.addWidget(btn, row, 1, alignment=Qt.AlignmentFlag.AlignCenter)
        return btn

    def add_time_input(self, grid, label, row, placeholder):
        lbl = qfw.BodyLabel(label)
        entry = qfw.LineEdit()
        entry.setPlaceholderText(placeholder)
        grid.addWidget(lbl, row, 0)
        grid.addWidget(entry, row, 1)
        return entry

    @staticmethod
    def create_caption_label(text):
        """创建字号更大的 CaptionLabel"""
        lbl = qfw.CaptionLabel(text)
        lbl.setFont(QFont("Microsoft YaHei", 11))
        return lbl

    def run_task(self, func, args):
        if self.worker and self.worker.isRunning():
            self.log("任务正在运行中，请等待完成")
            qfw.InfoBar.warning(
                parent=self,
                title="提示",
                content="请等待当前任务完成",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                duration=3000
            )
            return
        self.worker = Worker(func, args)
        self.worker.log_signal.connect(self.log)
        self.worker.finished_signal.connect(lambda ok: self._on_task_done(ok))
        self.worker.start()
        self.log("▶ 任务开始...")

    def run_queued_task(self, func, args, task_type: TaskType, title: str, metadata: dict = None):
        """将任务添加到任务队列管理器"""
        mgr = TaskQueueManager.instance()
        task = mgr.add_task(
            task_type=task_type,
            title=title,
            func=func,
            args=args,
            metadata=metadata or {}
        )
        self._owned_task_ids.add(task.task_id)
        # 首次连接日志转发
        if not hasattr(self, '_task_log_connected'):
            mgr.task_log.connect(self._on_task_log)
            self._task_log_connected = True
        self.log(f"▶ 任务已加入队列: {title}")
        return task.task_id
    
    def _on_task_log(self, task_id, msg):
        """转发属于本页面的任务日志"""
        if task_id in self._owned_task_ids:
            self.log(msg)

    def _on_task_done(self, ok):
        if ok:
            self.log("✅ 任务完成")
            qfw.InfoBar.success(
                parent=self,
                title="完成",
                content="任务执行成功",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                duration=3000
            )
        else:
            self.log("❌ 任务失败")
            qfw.InfoBar.error(
                parent=self,
                title="错误",
                content="任务执行失败，请查看日志",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
            )
        self._check_auto_action()

    def _check_auto_action(self):
        action_idx = load_task_complete_action()
        if action_idx == 0:  # 不操作
            return
        actions = ["不操作", "关闭工具箱", "关闭计算机"]
        action = actions[action_idx]
        if action == "关闭工具箱":
            self.log("2 秒后关闭工具箱...")
            QTimer.singleShot(2000, QApplication.quit)
        elif action == "关闭计算机":
            self._confirm_shutdown()

    def _confirm_shutdown(self):
        dialog = qfw.MessageBox("关机确认", "所有任务已完成。\n20 秒后将关闭计算机，点击「取消」可中止关机。", self)
        dialog.yesButton.setText("确定关机")
        dialog.cancelButton.setText("取消")
        if dialog.exec():
            os.system("shutdown /s /t 20")
            self.log("已执行关机命令（20 秒倒计时），可在设置页改选「不操作」取消")
        else:
            self.log("已取消关机")
