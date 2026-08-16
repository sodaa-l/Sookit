"""
pages/queue_page.py
任务队列页面 - 显示进行中和已完成的任务
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QWidget, QScrollArea, QFrame

import qfluentwidgets as qfw

from sookit.core.task_queue import TaskQueueManager, Task, TaskStatus
from sookit.widgets.task_card import create_task_card, TaskCardBase, CompletedThumbnailCard
from sookit.pages.base import PageBase
from sookit.core.utils import get_scrollbar_style


class QueuePage(PageBase):
    """任务队列页面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        
        # 卡片映射：task_id -> TaskCardBase
        self._active_cards = {}
        self._completed_cards = {}
        
        # 主布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 20, 36, 20)
        layout.setSpacing(12)
        
        # SegmentedWidget 切换"进行中"和"已完成"
        self.segment_widget = qfw.SegmentedWidget()
        self.segment_widget.addItem("active", "进行中")
        self.segment_widget.addItem("completed", "已完成")
        self.segment_widget.setCurrentItem("active")
        self.segment_widget.currentItemChanged.connect(self._on_segment_changed)
        layout.addWidget(self.segment_widget)
        
        # 进行中页面
        self.active_page = QWidget()
        active_layout = QVBoxLayout(self.active_page)
        active_layout.setContentsMargins(0, 0, 0, 0)
        active_layout.setSpacing(8)
        
        # 进行中滚动区域
        self.active_scroll = QScrollArea()
        self.active_scroll.setWidgetResizable(True)
        self.active_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.active_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        self.active_scroll_content = QWidget()
        self.active_scroll_content.setStyleSheet("QWidget { background: transparent; }")
        self.active_cards_layout = QVBoxLayout(self.active_scroll_content)
        self.active_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.active_cards_layout.setSpacing(8)
        self.active_cards_layout.addStretch()
        
        self.active_scroll.setWidget(self.active_scroll_content)
        active_layout.addWidget(self.active_scroll)
        
        layout.addWidget(self.active_page)
        
        # 已完成页面
        self.completed_page = QWidget()
        completed_layout = QVBoxLayout(self.completed_page)
        completed_layout.setContentsMargins(0, 0, 0, 0)
        completed_layout.setSpacing(0)

        # 已完成滚动区域
        self.completed_scroll = QScrollArea()
        self.completed_scroll.setWidgetResizable(True)
        self.completed_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.completed_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.completed_scroll_content = QWidget()
        self.completed_scroll_content.setStyleSheet("QWidget { background: transparent; }")
        # 使用 AdaptiveFlowLayout 自适应网格排列
        self.completed_flow_layout = qfw.AdaptiveFlowLayout(
            self.completed_scroll_content, needAni=False, isTight=True)
        self.completed_flow_layout.setWidgetMinimumWidth(CompletedThumbnailCard.THUMB_W)
        self.completed_flow_layout.setWidgetMaximumWidth(CompletedThumbnailCard.THUMB_W * 2)
        self.completed_flow_layout.setHorizontalSpacing(16)
        self.completed_flow_layout.setVerticalSpacing(24)
        self.completed_flow_layout.setContentsMargins(8, 8, 8, 8)

        self.completed_scroll.setWidget(self.completed_scroll_content)
        completed_layout.addWidget(self.completed_scroll)

        layout.addWidget(self.completed_page)
        
        # 默认显示进行中页面
        self.completed_page.setVisible(False)
        
        # 连接 TaskQueueManager 信号
        mgr = TaskQueueManager.instance()
        mgr.task_added.connect(self._on_task_added)
        mgr.task_updated.connect(self._on_task_updated)
        mgr.task_completed.connect(self._on_task_completed)
        mgr.task_failed.connect(self._on_task_failed)
        mgr.task_removed.connect(self._on_task_removed)
        
        # 恢复持久化的已完成任务（最新的在最前面）
        for task in reversed(mgr.get_completed_tasks()):
            card = CompletedThumbnailCard(task)
            self._completed_cards[task.task_id] = card
            self.completed_flow_layout.addWidget(card)

        # 更新滚动条样式
        self._update_scrollbar_style()
        # 主题变化时刷新滚动条颜色
        qfw.qconfig.themeChangedFinished.connect(self._update_scrollbar_style)
    
    def _on_segment_changed(self, key: str):
        """切换进行中/已完成页面"""
        if key == "active":
            self.active_page.setVisible(True)
            self.completed_page.setVisible(False)
        else:
            self.active_page.setVisible(False)
            self.completed_page.setVisible(True)
    
    def _on_task_added(self, task: Task):
        """新任务添加"""
        # 创建卡片
        card = create_task_card(task)
        self._active_cards[task.task_id] = card
        
        # 插入到最前面（最新的在最上面）
        self.active_cards_layout.insertWidget(0, card)
    
    def _on_task_updated(self, task: Task):
        """任务状态/进度更新"""
        card = self._active_cards.get(task.task_id)
        if not card:
            return
        
        # 更新进度
        card.update_progress(task.progress, task.speed, task.eta)
        card.update_status(task.status)
    
    def _on_task_completed(self, task: Task):
        """任务完成 - 移动到已完成列表"""
        # 从进行中移除
        card = self._active_cards.pop(task.task_id, None)
        if card:
            self.active_cards_layout.removeWidget(card)
            card.deleteLater()

        # 创建缩略图卡片
        thumb_card = CompletedThumbnailCard(task)
        self._completed_cards[task.task_id] = thumb_card

        # 插入到最前面（最新在上）
        self.completed_flow_layout.insertWidget(0, thumb_card)
    
    def _on_task_failed(self, task: Task):
        """任务失败 - 保留在进行中列表 + 弹常显错误提示（需手动关闭）"""
        card = self._active_cards.get(task.task_id)
        if card:
            card.update_status(TaskStatus.FAILED)
        # 弹常显错误 InfoBar，提醒用户失败原因（不自动消失）
        title = task.title or "下载任务失败"
        content = task.error or "任务执行失败，请查看日志"
        # content 可能较长，截断显示
        if len(content) > 200:
            content = content[:200] + "…"
        qfw.InfoBar.error(
            parent=self, title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True, duration=-1)
    
    def _on_task_removed(self, task_id: str):
        """任务移除（取消 / 删除已完成）"""
        card = self._active_cards.pop(task_id, None)
        if card:
            self.active_cards_layout.removeWidget(card)
            card.deleteLater()

        # 如果是已完成卡片被删除
        completed_card = self._completed_cards.pop(task_id, None)
        if completed_card:
            self.completed_flow_layout.removeWidget(completed_card)
            completed_card.deleteLater()
        # 触发布局重排，使后续卡片自动前移填补空缺
        self.completed_flow_layout.update()
    
    def _update_scrollbar_style(self):
        """根据当前主题更新滚动条样式（与设置页一致）"""
        style = get_scrollbar_style(qfw.isDarkTheme())
        self.active_scroll.setStyleSheet(style)
        self.completed_scroll.setStyleSheet(style)