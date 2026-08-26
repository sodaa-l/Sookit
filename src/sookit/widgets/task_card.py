"""
widgets/task_card.py
任务卡片组件 - 三种类型的任务卡片
"""

import os
import urllib.request
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QByteArray, QRectF, QUrl
from PyQt6.QtGui import QPixmap, QFont, QColor, QPainter, QPainterPath
import qfluentwidgets as qfw
from qfluentwidgets import FluentIcon, ToolTipFilter, qconfig
from PyQt6.QtGui import QDesktopServices

from sookit.core.task_queue import Task, TaskType, TaskStatus
from sookit.core.ffmpeg_utils import format_duration
from sookit.core.utils import get_certifi_ssl_context


# ---------- 任务卡片基类 ----------

class TaskCardBase(qfw.CardWidget):
    """任务卡片基类 - 固定高度 180px"""
    
    def __init__(self, task: Task, parent=None):
        super().__init__(parent)
        self.task = task
        self.setFixedHeight(180)
        self.setMinimumWidth(400)
        
        # 监听主题/强调色变化，动态更新进度条颜色
        qfw.qconfig.themeChangedFinished.connect(self._on_theme_changed)
        
        # 主布局
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(16, 14, 16, 14)
        main_layout.setSpacing(12)
        
        # 左侧信息区
        info_layout = QVBoxLayout()
        info_layout.setSpacing(6)
        
        # 标题行
        self.title_label = qfw.BodyLabel(task.title)
        self.title_label.setFont(QFont("Microsoft YaHei", 11, QFont.Weight.Bold))
        self.title_label.setWordWrap(True)
        info_layout.addWidget(self.title_label)
        
        # 元信息行（频道名/时长等）
        self.meta_label = qfw.CaptionLabel("")
        self.meta_label.setFont(QFont("Microsoft YaHei", 9))
        # 填充频道和时长
        meta_parts = []
        if task.channel:
            meta_parts.append(f"频道: {task.channel}")
        if task.duration:
            try:
                dur = int(task.duration)
                meta_parts.append(f"时长: {format_duration(dur) if dur else ''}")
            except (ValueError, TypeError):
                meta_parts.append(f"时长: {task.duration}")
        self.meta_label.setText("    ".join(meta_parts))
        info_layout.addWidget(self.meta_label)
        
        # 进度条行（进度条 + 百分比 + 按钮）
        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)

        # 进度条容器（垂直居中）
        bar_container = QHBoxLayout()
        bar_container.setContentsMargins(0, 0, 0, 0)
        self.progress_bar = qfw.ProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(8)
        bar_container.addWidget(self.progress_bar, stretch=1,
                                 alignment=Qt.AlignmentFlag.AlignVCenter)
        progress_row.addLayout(bar_container, stretch=1)

        # 百分比标签
        self.percent_label = qfw.CaptionLabel("0%")
        self.percent_label.setFixedWidth(45)
        self.percent_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        progress_row.addWidget(self.percent_label,
                               alignment=Qt.AlignmentFlag.AlignVCenter)

        # 暂停/继续按钮
        self.pause_btn = qfw.TransparentToolButton(FluentIcon.PAUSE)
        self.pause_btn.setFixedSize(32, 32)
        self.pause_btn.setToolTip("暂停")
        self.pause_btn.installEventFilter(ToolTipFilter(self.pause_btn, showDelay=300))
        self.pause_btn.clicked.connect(self._on_pause_resume)
        progress_row.addWidget(self.pause_btn)

        # 取消按钮
        self.cancel_btn = qfw.TransparentToolButton(FluentIcon.CANCEL_MEDIUM)
        self.cancel_btn.setFixedSize(32, 32)
        self.cancel_btn.setToolTip("取消")
        self.cancel_btn.installEventFilter(ToolTipFilter(self.cancel_btn, showDelay=300))
        self.cancel_btn.clicked.connect(self._on_cancel)
        progress_row.addWidget(self.cancel_btn)
        
        info_layout.addLayout(progress_row)
        
        # 状态行（速度/ETA 或 状态标签）
        status_row = QHBoxLayout()
        status_row.setSpacing(16)
        
        self.speed_label = qfw.CaptionLabel("")
        self.speed_label.setFont(QFont("Microsoft YaHei", 9))
        status_row.addWidget(self.speed_label)
        
        self.eta_label = qfw.CaptionLabel("")
        self.eta_label.setFont(QFont("Microsoft YaHei", 9))
        status_row.addWidget(self.eta_label)
        
        self.status_label = qfw.CaptionLabel("")
        self.status_label.setFont(QFont("Microsoft YaHei", 9))
        status_row.addWidget(self.status_label)
        
        status_row.addStretch()
        info_layout.addLayout(status_row)
        
        main_layout.addLayout(info_layout, stretch=1)
        
        # 右侧封面区（子类可覆盖）
        self.cover_widget = self._create_cover_widget()
        if self.cover_widget:
            main_layout.addWidget(self.cover_widget, alignment=Qt.AlignmentFlag.AlignVCenter)
        
        # 初始化样式
        self._update_style()
    
    def _create_cover_widget(self):
        """创建封面控件（子类可覆盖）"""
        return None
    
    def _on_pause_resume(self):
        """暂停/继续按钮点击"""
        from sookit.core.task_queue import TaskQueueManager
        mgr = TaskQueueManager.instance()
        
        if self.task.status == TaskStatus.RUNNING:
            mgr.pause_task(self.task.task_id)
        elif self.task.status == TaskStatus.PAUSED:
            mgr.resume_task(self.task.task_id)
    
    def _on_cancel(self):
        """取消按钮点击"""
        from sookit.core.task_queue import TaskQueueManager
        mgr = TaskQueueManager.instance()
        mgr.cancel_task(self.task.task_id)
    
    def update_progress(self, progress: float, speed: str, eta: str):
        """更新进度"""
        self.progress_bar.setValue(int(progress))
        # RUNNING 且进度为 0.0 时显示"准备中"，避免 0.0% 假象
        if self.task.status == TaskStatus.RUNNING and progress == 0.0:
            self.percent_label.setText("准备中")
        else:
            self.percent_label.setText(f"{progress:.1f}%")
        self.speed_label.setText(f"速度: {speed}" if speed else "")
        self.eta_label.setText(f"ETA: {eta}" if eta else "")
    
    def update_status(self, status: TaskStatus):
        """更新状态样式"""
        self.task.status = status
        self._update_style()
    
    def _on_theme_changed(self):
        """强调色变化时刷新运行中的进度条颜色"""
        if self.task.status == TaskStatus.RUNNING:
            theme_color = qconfig.themeColor.value.name()
            self.progress_bar.setCustomBarColor(theme_color, theme_color)
    
    def _update_style(self):
        """根据状态更新样式"""
        status = self.task.status
        
        if status == TaskStatus.RUNNING:
            # 运行中：使用 QFluentWidgets 当前主题色
            theme_color = qconfig.themeColor.value.name()
            self.progress_bar.setCustomBarColor(theme_color, theme_color)
            self.progress_bar.setVisible(True)
            self.percent_label.setVisible(True)
            # RUNNING 且进度为 0.0 时显示"准备中"，避免 0.0% 假象
            if self.task.progress == 0.0:
                self.percent_label.setText("准备中")
            self.pause_btn.setIcon(FluentIcon.PAUSE)
            self.pause_btn.setToolTip("暂停")
            self.pause_btn.setEnabled(True)
            self.cancel_btn.setEnabled(True)
            self.speed_label.setVisible(True)
            self.eta_label.setVisible(True)
            self.status_label.setVisible(False)
            
        elif status == TaskStatus.PAUSED:
            # 暂停中：灰色进度条 + "已暂停"标签
            self.progress_bar.setCustomBarColor("#999999", "#999999")
            self.pause_btn.setIcon(FluentIcon.PLAY)
            self.pause_btn.setToolTip("继续")
            self.pause_btn.setEnabled(True)
            self.cancel_btn.setEnabled(True)
            self.speed_label.setVisible(False)
            self.eta_label.setVisible(False)
            self.status_label.setText("已暂停")
            self.status_label.setVisible(True)
            
        elif status == TaskStatus.FAILED:
            # 失败：红色进度条 + "任务失败"标签
            self.progress_bar.setCustomBarColor("#D83B01", "#D83B01")
            self.pause_btn.setEnabled(False)
            self.cancel_btn.setEnabled(True)
            self.cancel_btn.setToolTip("删除")
            self.speed_label.setVisible(False)
            self.eta_label.setVisible(False)
            self.status_label.setText("任务失败")
            self.status_label.setStyleSheet("color: #D83B01;")
            self.status_label.setVisible(True)
            
        elif status == TaskStatus.COMPLETED:
            # 已完成：隐藏进度条，显示打开文件夹按钮
            self.progress_bar.setVisible(False)
            self.percent_label.setVisible(False)
            self.speed_label.setVisible(False)
            self.eta_label.setVisible(False)
            self.status_label.setText("已完成")
            self.status_label.setStyleSheet("color: #107C10;")
            self.status_label.setVisible(True)
            self.pause_btn.setIcon(FluentIcon.FOLDER)
            self.pause_btn.setToolTip("打开文件夹")
            self.pause_btn.setEnabled(True)
            self.cancel_btn.setVisible(False)
            
        elif status == TaskStatus.WAITING:
            # 等待中
            self.progress_bar.setValue(0)
            self.percent_label.setText("等待中")
            self.speed_label.setVisible(False)
            self.eta_label.setVisible(False)
            self.status_label.setText("等待中")
            self.status_label.setVisible(True)
            self.pause_btn.setEnabled(False)
            self.cancel_btn.setEnabled(True)


# ---------- 圆角封面控件 ----------

class RoundedCoverWidget(QWidget):
    """自绘封面控件，圆角绘制，无黑边；支持图标占位"""
    def __init__(self, w, h, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._icon = None
        self._text = "加载中..."
        self.setFixedSize(w, h)
    
    def setPixmap(self, pixmap):
        self._pixmap = pixmap
        self._icon = None
        self.update()
    
    def setText(self, text):
        self._text = text
        self._pixmap = QPixmap()
        self._icon = None
        self.update()
    
    def setIcon(self, icon):
        """设置图标封面（当无实际封面图时居中显示）"""
        self._pixmap = QPixmap()
        self._icon = icon
        self.update()
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = self.rect()
        radius = 8
        
        # 画圆角背景
        painter.setPen(Qt.PenStyle.NoPen)
        bg = QColor("#999") if qfw.isDarkTheme() else QColor("#ddd")
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, radius, radius)
        
        if self._pixmap.isNull():
            if self._icon:
                # 使用 QIcon.paint() 绘制图标，确保居中且 DPI 正确
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                icon_size = min(rect.width(), rect.height()) // 2
                icon_rect = QRectF(
                    (rect.width() - icon_size) / 2,
                    (rect.height() - icon_size) / 2,
                    icon_size, icon_size
                )
                self._icon.icon().paint(painter, icon_rect.toRect())
                painter.restore()
            else:
                painter.setPen(QColor("#666"))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)
            return
        
        # 缩放图片适应控件，保持比例
        scaled = self._pixmap.scaled(
            rect.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation
        )
        x = (rect.width() - scaled.width()) // 2
        y = (rect.height() - scaled.height()) // 2
        
        # 圆角裁剪
        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(x, y, scaled.width(), scaled.height()), radius, radius)
        painter.setClipPath(clip)
        painter.drawPixmap(x, y, scaled)
        painter.restore()


# ---------- yt-dlp 任务卡片 ----------

class YtDlpTaskCard(TaskCardBase):
    """yt-dlp 任务卡片 - 包含封面图"""
    
    COVER_W = 270  # 152 * 16/9 ≈ 270, 保持 16:9 比例
    COVER_H = 152
    
    def _create_cover_widget(self):
        """创建封面控件"""
        self.cover_w = RoundedCoverWidget(self.COVER_W, self.COVER_H)
        
        # 加载封面：优先使用预加载数据，避免重复下载
        if self.task.cover_url:
            cover_data = self.task.metadata.get('cover_data')
            if cover_data:
                self._on_cover_loaded(cover_data)
            else:
                self._load_cover(self.task.cover_url)
        else:
            # 无封面 URL 时显示图标占位（如 X Space）
            self.cover_w.setIcon(qfw.FluentIcon.VIDEO)
        
        return self.cover_w
    
    def _load_cover(self, url: str):
        """异步加载封面"""
        class CoverLoader(QThread):
            loaded = pyqtSignal(bytes)
            failed = pyqtSignal()
            
            def run(self):
                try:
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=10, context=get_certifi_ssl_context()) as resp:
                        data = resp.read()
                    if data:
                        self.loaded.emit(data)
                    else:
                        self.failed.emit()
                except Exception:
                    self.failed.emit()
        
        self._cover_loader = CoverLoader()
        self._cover_loader.loaded.connect(self._on_cover_loaded)
        self._cover_loader.failed.connect(lambda: self.cover_w.setText("封面加载失败"))
        self._cover_loader.start()
    
    def _on_cover_loaded(self, data: bytes):
        """封面加载完成"""
        pixmap = QPixmap()
        if pixmap.loadFromData(QByteArray(data)):
            scaled = pixmap.scaled(
                self.COVER_W, self.COVER_H,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            self.cover_w.setPixmap(scaled)
        else:
            self.cover_w.setText("封面加载失败")


# ---------- ffmpeg 任务卡片（三级封面策略）----------

class FfmpegTaskCard(TaskCardBase):
    """ffmpeg 任务卡片 - 三级封面策略：
       1. input_img → 直接加载图片
       2. input_video → 异步截取 I 帧
       3. 图标占位（fallback）
    """
    COVER_W = 270
    COVER_H = 152

    def _create_cover_widget(self):
        self.cover_w = RoundedCoverWidget(self.COVER_W, self.COVER_H)
        self._apply_cover()
        return self.cover_w

    def _apply_cover(self):
        """三级封面策略"""
        meta = self.task.metadata

        # 优先检查已缓存的封面数据（已完成任务）
        cover_data = meta.get('cover_data')
        if cover_data:
            pixmap = QPixmap()
            if pixmap.loadFromData(QByteArray(cover_data)):
                scaled = pixmap.scaled(self.COVER_W, self.COVER_H,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                self.cover_w.setPixmap(scaled)
                return

        # 检查 cover_cache 目录
        from sookit.core.task_queue import COVER_CACHE_DIR
        cache_path = os.path.join(COVER_CACHE_DIR, f"{self.task.task_id}.jpg")
        if os.path.exists(cache_path):
            pixmap = QPixmap(cache_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(self.COVER_W, self.COVER_H,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                self.cover_w.setPixmap(scaled)
                return

        # 策略一：输入图片缩略图
        img_path = meta.get('input_img')
        if img_path and os.path.exists(img_path):
            pixmap = QPixmap(img_path)
            if not pixmap.isNull():
                scaled = pixmap.scaled(self.COVER_W, self.COVER_H,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation)
                self.cover_w.setPixmap(scaled)
                return

        # 策略二：输入视频异步截取 I 帧
        video_path = meta.get('input_video')
        if video_path and os.path.exists(video_path):
            self._extract_frame(video_path)
            return

        # 策略三：图标占位
        self.cover_w.setIcon(qfw.FluentIcon.VIDEO)

    def _extract_frame(self, video_path):
        """异步截取视频中段 I 帧"""
        class _FrameExtractor(QThread):
            loaded = pyqtSignal(bytes)
            failed = pyqtSignal()
            def run(self2):
                try:
                    from sookit.core.ffmpeg_utils import extract_video_frame
                    data = extract_video_frame(video_path)
                    if data:
                        self2.loaded.emit(data)
                    else:
                        self2.failed.emit()
                except Exception:
                    self2.failed.emit()

        self._frame_worker = _FrameExtractor()
        self._frame_worker.loaded.connect(self._on_frame_loaded)
        self._frame_worker.failed.connect(
            lambda: self.cover_w.setIcon(qfw.FluentIcon.VIDEO))
        self._frame_worker.start()

    def _on_frame_loaded(self, data):
        """帧截取完成"""
        pixmap = QPixmap()
        if pixmap.loadFromData(QByteArray(data)):
            scaled = pixmap.scaled(self.COVER_W, self.COVER_H,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation)
            self.cover_w.setPixmap(scaled)
            # 持久化 cover_data，任务完成时自动写入 cover_cache
            self.task.metadata['cover_data'] = data
            _cache_cover_file(self.task.task_id, data)
        else:
            self.cover_w.setIcon(qfw.FluentIcon.VIDEO)






# ---------- 已完成缩略图卡片 - YouTube 风格 ----------

class CoverAreaWidget(QWidget):
    """封面区域 - 自绘缩略图，保持 16:9 比例，单击弹出 CommandBarView"""

    THUMB_W = 250
    THUMB_H = 140

    def __init__(self, task: Task, parent=None):
        super().__init__(parent)
        self.task = task
        self._hovered = False
        self._pixmap = QPixmap()          # 原图（不预缩放）
        self._icon = None
        self.setMinimumHeight(self.THUMB_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._load_cover()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        desired_h = max(self.THUMB_H, int(self.width() * 9 / 16))
        if self.height() != desired_h:
            self.setFixedHeight(desired_h)

    # ---- 封面加载（三级策略） ----

    def _load_cover(self):
        meta = self.task.metadata

        # 1. cover_data
        cover_data = meta.get('cover_data')
        if cover_data:
            pixmap = QPixmap()
            if pixmap.loadFromData(QByteArray(cover_data)):
                self._set_pixmap(pixmap)
                return

        # 2. cover_cache 文件
        cache_path = os.path.join(COVER_CACHE_DIR, f"{self.task.task_id}.jpg")
        if os.path.exists(cache_path):
            pixmap = QPixmap(cache_path)
            if not pixmap.isNull():
                self._set_pixmap(pixmap)
                return

        # 3. input_img
        img_path = meta.get('input_img')
        if img_path and os.path.exists(img_path):
            pixmap = QPixmap(img_path)
            if not pixmap.isNull():
                self._set_pixmap(pixmap)
                return

        # 4. input_video → 异步截取 I 帧
        video_path = meta.get('input_video')
        if video_path and os.path.exists(video_path):
            self._extract_frame(video_path)
            return

        # 5. 异步下载
        if self.task.cover_url:
            self._download_cover()
            return

        # 6. 图标占位（音频输出任务显示 MUSIC）
        if meta.get('input_video'):
            self._icon = qfw.FluentIcon.VIDEO
        else:
            self._icon = qfw.FluentIcon.MUSIC
        self.update()

    def _set_pixmap(self, pixmap):
        self._pixmap = pixmap
        self._icon = None
        self.update()

    def _download_cover(self):
        class _CovDL(QThread):
            loaded = pyqtSignal(bytes)
            failed = pyqtSignal()
            def run(self2):
                try:
                    req = urllib.request.Request(
                        self.task.cover_url,
                        headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=10, context=get_certifi_ssl_context()) as resp:
                        data = resp.read()
                    if data:
                        self2.loaded.emit(data)
                    else:
                        self2.failed.emit()
                except Exception:
                    self2.failed.emit()
        loader = _CovDL()
        loader.loaded.connect(self._on_cover_downloaded)
        loader.failed.connect(self._set_fallback)
        self._cover_loader = loader
        loader.start()

    def _on_cover_downloaded(self, data):
        pixmap = QPixmap()
        if pixmap.loadFromData(QByteArray(data)):
            self._set_pixmap(pixmap)
        else:
            self._set_fallback()

    def _set_fallback(self):
        meta = self.task.metadata
        if meta.get('input_video'):
            self._icon = qfw.FluentIcon.VIDEO
        else:
            self._icon = qfw.FluentIcon.MUSIC
        self.update()

    def _extract_frame(self, video_path):
        """异步截取视频中段 I 帧"""
        class _FrameExtractor(QThread):
            loaded = pyqtSignal(bytes)
            failed = pyqtSignal()
            def run(self2):
                try:
                    from sookit.core.ffmpeg_utils import extract_video_frame
                    data = extract_video_frame(video_path)
                    if data:
                        self2.loaded.emit(data)
                    else:
                        self2.failed.emit()
                except Exception:
                    self2.failed.emit()

        self._frame_worker = _FrameExtractor()
        self._frame_worker.loaded.connect(self._on_frame_loaded)
        self._frame_worker.failed.connect(self._set_fallback)
        self._frame_worker.start()

    def _on_frame_loaded(self, data):
        """帧截取完成"""
        pixmap = QPixmap()
        if pixmap.loadFromData(QByteArray(data)):
            self._set_pixmap(pixmap)
            # 持久化到 cover_cache，供任务移入已完成时直接加载
            _cache_cover_file(self.task.task_id, data)
        else:
            self._set_fallback()

    # ---- 鼠标事件 ----

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._show_command_bar()
        super().mouseReleaseEvent(event)

    # ---- CommandBarView ----

    def _show_command_bar(self):
        from qfluentwidgets import Flyout, FlyoutAnimationType
        from PyQt6.QtGui import QAction
        bar = qfw.CommandBarView()

        is_batch = self._is_batch_task()

        # 批量任务不显示"打开文件"（Play icon）
        if not is_batch:
            act_open = QAction(qfw.FluentIcon.PLAY.icon(), "打开文件")
            act_open.triggered.connect(self._on_open_file)
            bar.addAction(act_open)
            bar.addSeparator()

        act_folder = QAction(qfw.FluentIcon.FOLDER.icon(), "打开文件夹")
        act_folder.triggered.connect(self._on_open_folder)
        bar.addAction(act_folder)

        bar.addSeparator()

        act_delete = QAction(qfw.FluentIcon.DELETE.icon(), "删除文件")
        act_delete.triggered.connect(self._on_delete_clicked)
        bar.addAction(act_delete)

        # 保存 delete button widget 引用，用于 TeachingTip 定位
        self._delete_btn_widget = None
        for btn in bar.bar.commandButtons:
            if btn.action() and btn.action().text() == "删除文件":
                self._delete_btn_widget = btn
                break

        bar.resizeToSuitableWidth()
        Flyout.make(bar, target=self, aniType=FlyoutAnimationType.PULL_UP)

    def _is_batch_task(self):
        """判断是否为批量任务（output_path 是目录或无精确文件路径）"""
        path = self._get_output_path()
        return not path or os.path.isdir(path)

    def _on_delete_clicked(self):
        """点击删除：批量任务直接清理，普通任务弹确认窗"""
        if self._is_batch_task():
            # 批量任务：不弹窗、不删磁盘文件，直接清理
            self._cleanup_completed_task()
            return

        # 普通任务：弹出确认弹窗，勾选 CheckBox 后可删除磁盘文件
        from qfluentwidgets import (
            PopupTeachingTip, TeachingTipView, TeachingTipTailPosition,
            PrimaryPushButton, CheckBox, PushButton
        )
        from PyQt6.QtWidgets import QHBoxLayout, QWidget
        from PyQt6.QtCore import Qt, QPoint

        # 在 Flyout 关闭前，保存 delete 按钮的位置，创建一个临时 target widget
        btn = self._delete_btn_widget
        temp_target = QWidget(self)
        temp_target.setFixedSize(1, 1)
        if btn:
            # 获取按钮底部中心在全局的坐标
            global_bottom = btn.mapToGlobal(QPoint(btn.width() // 2, btn.height()))
            local_pos = self.mapFromGlobal(global_bottom)
            temp_target.move(local_pos.x() - 1, local_pos.y() - 1)
        temp_target.show()

        view = TeachingTipView(title="确定要删除吗？", content="", isClosable=False)
        view.setMinimumWidth(280)
        # 标题字号比 CheckBox 文字更大
        view.titleLabel.setStyleSheet("font-size: 18px; font-weight: 600;")
        # 标题与 CheckBox 之间增加间距
        view.widgetLayout.insertSpacing(1, 8)

        check_box = CheckBox("同时删除本地文件")
        check_box.setChecked(False)
        view.addWidget(check_box)

        # 增加垂直间距
        spacer = QWidget()
        spacer.setFixedHeight(8)
        view.addWidget(spacer)

        # 按钮容器，两个按钮等宽
        btn_widget = QWidget()
        btn_layout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(0, 0, 0, 0)

        btn_cancel = PushButton("取消")
        btn_confirm = PrimaryPushButton("确定删除")
        btn_cancel.setFixedWidth(110)
        btn_confirm.setFixedWidth(110)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_confirm)
        btn_layout.addStretch()

        view.addWidget(btn_widget, 0, Qt.AlignmentFlag.AlignCenter)

        tip = PopupTeachingTip.make(
            view=view,
            target=temp_target,
            duration=-1,
            tailPosition=TeachingTipTailPosition.TOP,
        )

        def _on_confirm():
            tip.close()
            self._on_delete(delete_disk=check_box.isChecked())

        btn_cancel.clicked.connect(tip.close)
        btn_confirm.clicked.connect(_on_confirm)

    def _on_open_file(self):
        path = self._get_output_path()
        if path and os.path.exists(path):
            if os.path.isfile(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _on_open_folder(self):
        path = self._get_output_path()
        if path and os.path.exists(path):
            if os.path.isfile(path):
                QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _delete_disk_files(self):
        """删除输出磁盘文件（仅当勾选时才调用）"""
        path = self._get_output_path()
        if not path or not os.path.isfile(path):
            return
        try:
            os.remove(path)
        except Exception:
            pass

    def _cleanup_completed_task(self):
        """清理已完成任务（始终执行）：从列表移除 + 删封面缓存"""
        from sookit.core.task_queue import TaskQueueManager
        mgr = TaskQueueManager.instance()
        mgr.remove_completed_task(self.task.task_id)

        cache_path = os.path.join(COVER_CACHE_DIR, f"{self.task.task_id}.jpg")
        try:
            if os.path.exists(cache_path):
                os.remove(cache_path)
        except Exception:
            pass

    def _on_delete(self, delete_disk=True):
        """删除入口：delete_disk=True 时同时删除磁盘文件"""
        if delete_disk:
            self._delete_disk_files()
        self._cleanup_completed_task()

    def _get_output_path(self):
        path = self.task.output_path
        if not (path and os.path.exists(path)):
            meta = self.task.metadata
            for key in ('out', 'output', 'out_dir'):
                val = meta.get(key)
                if val and os.path.exists(val):
                    path = val
                    break
        return path

    # ---- 绘制 ----

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        radius = 8

        clip = QPainterPath()
        clip.addRoundedRect(QRectF(rect), radius, radius)
        painter.setClipPath(clip)

        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                rect.width(), rect.height(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            x = (rect.width() - scaled.width()) // 2
            y = (rect.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        else:
            bg = QColor("#999") if qfw.isDarkTheme() else QColor("#ddd")
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg)
            painter.drawRoundedRect(rect, radius, radius)
            if self._icon:
                icon_size = min(rect.width(), rect.height()) // 2
                icon_rect = QRectF(
                    (rect.width() - icon_size) / 2,
                    (rect.height() - icon_size) / 2,
                    icon_size, icon_size)
                self._icon.icon().paint(painter, icon_rect.toRect())

        # 悬停时轻微变暗（视觉反馈）
        if self._hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 80))
            painter.drawRoundedRect(rect, radius, radius)


class CompletedThumbnailCard(QWidget):
    """YouTube 风格已完成卡片 - 封面 + 标题 + 元信息（频道/时长）"""

    THUMB_W = 250
    THUMB_H = 140
    _MARGIN = 6
    _TITLE_FONT = QFont("Microsoft YaHei", 12, QFont.Weight.Bold)
    _META_FONT = QFont("Microsoft YaHei", 10)

    def __init__(self, task: Task, parent=None):
        super().__init__(parent)
        self.task = task

        self.setMinimumWidth(self.THUMB_W)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(self._MARGIN, self._MARGIN,
                                  self._MARGIN, self._MARGIN)
        layout.setSpacing(6)

        # 封面区域
        self.cover = CoverAreaWidget(task, self)
        layout.addWidget(self.cover)

        # 标题
        self.title_label = QLabel(task.title)
        self.title_label.setFont(self._TITLE_FONT)
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        # 元信息
        self.meta_label = QLabel()
        self.meta_label.setFont(self._META_FONT)
        meta_parts = []
        if task.channel:
            meta_parts.append(f"频道: {task.channel}")
        if task.duration:
            try:
                dur = int(task.duration)
                dur_str = format_duration(dur)
                if dur_str:
                    meta_parts.append(f"时长: {dur_str}")
            except (ValueError, TypeError):
                meta_parts.append(f"时长: {task.duration}")
        self.meta_label.setText("    ".join(meta_parts))
        layout.addWidget(self.meta_label)

        # 主题变化时刷新文字颜色
        qconfig.themeChanged.connect(self._update_theme_colors)
        self._update_theme_colors()

    def _update_theme_colors(self, theme=None):
        """根据当前主题更新文字颜色"""
        dark = qfw.isDarkTheme()
        self.title_label.setStyleSheet(
            f"color: {'white' if dark else 'black'};")
        self.meta_label.setStyleSheet(
            f"color: {'#aaa' if dark else '#888'};")

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w: int) -> int:
        m = self._MARGIN
        content_w = w - 2 * m

        # 封面高度（16:9）
        cover_h = max(self.THUMB_H, int(content_w * 9 / 16))

        # 标题高度（根据 content_w 换行估算）
        fm = QFontMetrics(self._TITLE_FONT)
        title_rect = fm.boundingRect(
            0, 0, content_w, 2000,
            Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.WordBreak,
            self.task.title)
        title_h = title_rect.height()

        # 元信息单行高度
        meta_fm = QFontMetrics(self._META_FONT)
        meta_h = meta_fm.height()

        # 总高度：上边距 + 封面 + 间距 + 标题 + 间距 + 元信息 + 下边距 + 补偿
        return m + cover_h + 6 + max(title_h, 18) + 4 + meta_h + m + 4


# ---------- 创建卡片的工厂函数 ----------

from sookit.core.task_queue import COVER_CACHE_DIR

def _set_cover_from_data(cover_widget, data):
    """从二进制数据设置封面"""
    pixmap = QPixmap()
    if pixmap.loadFromData(QByteArray(data)):
        scaled = pixmap.scaled(
            cover_widget.width(), cover_widget.height(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation)
        cover_widget.setPixmap(scaled)

def _cache_cover_file(task_id, data):
    """将封面数据保存到本地缓存"""
    try:
        os.makedirs(COVER_CACHE_DIR, exist_ok=True)
        path = os.path.join(COVER_CACHE_DIR, f"{task_id}.jpg")
        with open(path, 'wb') as f:
            f.write(data)
    except Exception:
        pass

def create_task_card(task: Task) -> TaskCardBase:
    """根据任务类型创建对应的卡片"""
    if task.task_type == TaskType.YTDLP:
        return YtDlpTaskCard(task)
    elif task.task_type == TaskType.FFMPEG:
        return FfmpegTaskCard(task)
    else:
        return TaskCardBase(task)