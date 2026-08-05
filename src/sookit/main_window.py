"""
sookit/main_window.py
主窗口 MainWindow 类（原单文件脚本的 MainWindow）
"""

import os
import sys

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFileDialog, \
    QApplication, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, \
    QSplitter, QLabel, QSizePolicy, QSystemTrayIcon
from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal, pyqtSlot, QTimer, QRectF, QByteArray, QSize
from PyQt6.QtGui import QFont, QTextCursor, QIcon, QPixmap, QPainter, QColor, QPainterPath

import qfluentwidgets as qfw
from qfluentwidgets import FluentIcon as FIF, NavigationItemPosition, InfoBadge, InfoBadgePosition

from sookit import APP_NAME, APP_VERSION
from sookit.paths import get_icon_path

# ---------- 从 widgets 导入自定义控件 ----------
from sookit.widgets.cover_image import CoverImageWidget

# ---------- 从 core.functions 导入工具函数和功能类 ----------
from sookit.core.functions import (
    Functions, check_ffmpeg, sanitize_path, format_duration, format_filesize,
    extract_youtube_id, build_thumbnails, is_ytdlp_available,
    fetch_youtube_metadata, get_video_duration, run_ffmpeg, run_ytdlp,
    load_theme_color, load_close_action, DEFAULT_OUTPUT_DIR, ensure_output_dir,
)

# ---------- 从 core.workers 导入工作线程 ----------
from sookit.core.workers import Worker, MonitorWorker


# ---------- 从 pages 导入所有页面类 ----------
from sookit.pages import (
    PageBase, MergePage, Img2VidPage, M3U8Page, XSpacePage, YouTubePage,
    SubtitlePage, CutPage, ReplaceAudioPage, ExtractAudioPage, FramePage,
    MonitorPage, QueuePage, SettingsPage
)


# ========== 主窗口 ==========
class MainWindow(qfw.FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(1131, 731)
        self.setMinimumSize(966, 644)

        # 设置窗口图标
        self.setWindowIcon(QIcon(str(get_icon_path())))

        # 检查 ffmpeg 可用性（可选依赖，缺失时仅警告）
        FFMPEG_AVAILABLE = check_ffmpeg()
        if not FFMPEG_AVAILABLE:
            qfw.InfoBar.warning(
                parent=self,
                title="警告",
                content="未找到 FFmpeg！请将 FFmpeg 放入 tools/ffmpeg/ 目录，或安装并添加到 PATH。",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                duration=10000
            )

        # 检查 yt-dlp 可用性（可选依赖，PATH 全局或内置 tools/ 均可，缺失时 YouTube 功能不可用）
        if not is_ytdlp_available():
            bar = qfw.InfoBar.warning(
                parent=self,
                title="警告",
                content="未找到 yt-dlp！YouTube 相关功能将不可用，请前往设置页下载安装。",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                duration=-1
            )
            btn = qfw.PushButton("前往设置")
            btn.clicked.connect(lambda: self.switchTo(self.settings_page))
            bar.addWidget(btn)

        # 创建各页面
        self.youtube_page = YouTubePage(self)
        self.youtube_page.setObjectName("youtubePage")
        self.cut_page = CutPage(self)
        self.cut_page.setObjectName("cutPage")
        self.subtitle_page = SubtitlePage(self)
        self.subtitle_page.setObjectName("subtitlePage")
        self.monitor_page = MonitorPage(self)
        self.monitor_page.setObjectName("monitorPage")
        self.merge_page = MergePage(self)
        self.merge_page.setObjectName("mergePage")
        self.img2vid_page = Img2VidPage(self)
        self.img2vid_page.setObjectName("img2vidPage")
        self.m3u8_page = M3U8Page(self)
        self.m3u8_page.setObjectName("m3u8Page")
        self.xspace_page = XSpacePage(self)
        self.xspace_page.setObjectName("xspacePage")
        self.frame_page = FramePage(self)
        self.frame_page.setObjectName("framePage")
        self.replace_page = ReplaceAudioPage(self)
        self.replace_page.setObjectName("replacePage")
        self.extract_page = ExtractAudioPage(self)
        self.extract_page.setObjectName("extractPage")
        self.queue_page = QueuePage(self)
        self.queue_page.setObjectName("queuePage")
        self.settings_page = SettingsPage(self)
        self.settings_page.setObjectName("settingsPage")

        # 添加到导航栏
        self.addSubInterface(self.youtube_page, FIF.PLAY, "YouTube 嗅探")
        self.addSubInterface(self.cut_page, FIF.CUT, "视频裁切")
        self.addSubInterface(self.subtitle_page, FIF.FONT, "字幕烧录")
        self.addSubInterface(self.monitor_page, FIF.SYNC, "直播监控")
        self.addSubInterface(self.merge_page, FIF.PHOTO, "图片+音频合并")
        self.addSubInterface(self.img2vid_page, FIF.VIDEO, "图片转视频")
        self.addSubInterface(self.m3u8_page, FIF.SAVE, "M3U8 下载")
        # X 图标——大画布+大字号绘制 𝕏，导航栏缩放后依然清晰
        self._x_icon_pixmap = QPixmap(128, 128)
        self._x_icon_pixmap.fill(Qt.GlobalColor.transparent)
        p = QPainter(self._x_icon_pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        font = QFont("Segoe UI Symbol", 100)
        p.setFont(font)
        icon_color = Qt.GlobalColor.white if qfw.isDarkTheme() else Qt.GlobalColor.black
        p.setPen(icon_color)
        p.drawText(QRectF(0, 0, 128, 128), Qt.AlignmentFlag.AlignCenter, "𝕏")
        p.end()
        self.addSubInterface(self.xspace_page, QIcon(self._x_icon_pixmap), "X Space 下载")
        self.addSubInterface(self.frame_page, FIF.CAMERA, "帧提取")
        self.addSubInterface(self.replace_page, FIF.MUSIC, "音频覆盖")
        self.addSubInterface(self.extract_page, FIF.HEADPHONE, "音频提取")
        # 侧边栏底部
        self.addSubInterface(self.queue_page, FIF.UPDATE, "任务队列", position=NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.settings_page, FIF.SETTING, "设置", position=NavigationItemPosition.BOTTOM)

        qfw.setThemeColor(load_theme_color())

        # 延时创建任务队列 Badge（确保导航界面已完全初始化）
        QTimer.singleShot(0, self._setup_queue_badge)

    def _setup_queue_badge(self):
        """创建任务队列的 InfoBadge（显示"进行中"任务数）"""
        from sookit.core.task_queue import TaskQueueManager

        nav_item = self.navigationInterface.widget(self.queue_page.objectName())
        if not nav_item:
            return

        self._queue_badge = InfoBadge.attension(
            "0", parent=self, target=nav_item, position=InfoBadgePosition.TOP_RIGHT)
        self._queue_badge.setVisible(False)

        # 连接信号实时更新
        mgr = TaskQueueManager.instance()
        mgr.task_added.connect(self._update_queue_badge)
        mgr.task_completed.connect(self._update_queue_badge)
        mgr.task_removed.connect(self._update_queue_badge)

        # 初始同步已有活跃任务数
        self._update_queue_badge()

    def _update_queue_badge(self, *args):
        """更新 Badge 显示的活跃任务数"""
        from sookit.core.task_queue import TaskQueueManager

        mgr = TaskQueueManager.instance()
        count = len(mgr.get_active_tasks())
        if count > 0:
            self._queue_badge.setText(str(count))
            self._queue_badge.setVisible(True)
        else:
            self._queue_badge.setVisible(False)

    def _center_on_screen(self):
        """在 show() 之前居中，用 width/height 而非 frameGeometry"""
        screen = QApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()
        x = geo.x() + (geo.width() - self.width()) // 2
        y = geo.y() + (geo.height() - self.height()) // 2
        self.move(x, y)

    def closeEvent(self, event):
        """关闭窗口时根据配置决定行为"""
        if load_close_action() == 0:
            # 最小化至托盘
            event.ignore()
            self.hide()
        else:
            # 直接退出
            self.quit_app()

    def quit_app(self):
        """真正退出应用前清理所有子进程"""
        # 停止直播监控的 workers
        self.monitor_page.stop_all_workers()
        QApplication.instance().quit()
