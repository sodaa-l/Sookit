"""
sookit/main_window.py
主窗口 MainWindow 类（原单文件脚本的 MainWindow）
"""

import os
import sys

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFileDialog, \
    QApplication, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, \
    QSplitter, QLabel, QSizePolicy, QSystemTrayIcon
from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal, pyqtSlot, QTimer, QByteArray, QSize
from PyQt6.QtGui import QTextCursor, QIcon

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
    check_latest_version, get_current_version, get_ignored_version,
    set_ignored_version, download_installer,
)

# ---------- 从 core.workers 导入工作线程 ----------
from sookit.core.workers import Worker, MonitorWorker


# ---------- 从 pages 导入所有页面类 ----------
from sookit.pages import (
    PageBase, YouTubePage,
    SubtitlePage, ReplaceAudioPage, ExtractAudioPage,
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
        self._ytdlp_warning_bar = None
        if not is_ytdlp_available():
            self._ytdlp_warning_bar = qfw.InfoBar.warning(
                parent=self,
                title="警告",
                content="未找到 yt-dlp！YouTube 相关功能将不可用，请前往设置页下载安装。",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                duration=-1
            )
            btn = qfw.PushButton("前往设置")
            btn.clicked.connect(lambda: self.switchTo(self.settings_page))
            self._ytdlp_warning_bar.addWidget(btn)

        # 创建各页面
        self.youtube_page = YouTubePage(self)
        self.youtube_page.setObjectName("youtubePage")
        self.subtitle_page = SubtitlePage(self)
        self.subtitle_page.setObjectName("subtitlePage")
        self.monitor_page = MonitorPage(self)
        self.monitor_page.setObjectName("monitorPage")
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
        self.addSubInterface(self.subtitle_page, FIF.FONT, "字幕烧录")
        self.addSubInterface(self.monitor_page, FIF.SYNC, "直播监控")
        self.addSubInterface(self.replace_page, FIF.MUSIC, "音频覆盖")
        self.addSubInterface(self.extract_page, FIF.HEADPHONE, "音频提取")
        # 侧边栏底部
        self.addSubInterface(self.queue_page, FIF.UPDATE, "任务队列", position=NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.settings_page, FIF.SETTING, "设置", position=NavigationItemPosition.BOTTOM)

        qfw.setThemeColor(load_theme_color())

        # 延时创建任务队列 Badge（确保导航界面已完全初始化）
        QTimer.singleShot(0, self._setup_queue_badge)

        # 延时自动检查更新（避免阻塞启动渲染；release 未上传/查询失败时静默跳过）
        QTimer.singleShot(2000, self._check_update_at_startup)

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
            return
        # 有正在运行的下载任务时，先弹确认框（避免误关导致下载中断且 .part 残留）
        try:
            from sookit.core.task_queue import TaskQueueManager
            if TaskQueueManager.instance().has_running_tasks():
                dialog = qfw.Dialog(
                    "关闭 Sookit",
                    "关闭 Sookit 会停止正在进行的任务，是否关闭？",
                    self)
                dialog.yesButton.setText("关闭")
                dialog.cancelButton.setText("取消")
                if not dialog.exec():
                    event.ignore()
                    return
        except Exception:  # noqa: BLE001
            pass
        # 直接退出（quit_app 会终止进程并删除各任务 .part）
        self.quit_app()

    def quit_app(self):
        """真正退出应用前清理所有子进程"""
        # 停止直播监控的 workers
        self.monitor_page.stop_all_workers()
        # 取消所有下载任务，终止各自的 yt-dlp/aria2c 进程树
        # （延迟 import 避免循环依赖；每个任务只通过自己的 launcher PID 清理，不影响 updater.exe）
        try:
            from sookit.core.task_queue import TaskQueueManager
            TaskQueueManager.instance().cancel_all()
        except Exception:  # noqa: BLE001
            pass
        QApplication.instance().quit()

    def refresh_ytdlp_status(self):
        """yt-dlp 装好后统一刷新各页「未找到 yt-dlp」提示（关闭已显示/已初始化的 warning infobar）"""
        if self._ytdlp_warning_bar is not None and is_ytdlp_available():
            try:
                self._ytdlp_warning_bar.close()
            except Exception:
                pass
            self._ytdlp_warning_bar = None
        for page in (self.youtube_page, self.monitor_page):
            if page is not None and hasattr(page, "refresh_ytdlp_status"):
                try:
                    page.refresh_ytdlp_status()
                except Exception:
                    pass

    # ========== 自动更新 ==========

    def _info_parent(self):
        """返回当前内容区页面作为 InfoBar 的 parent（定位在标题栏下方），
        使提示位置与其他页面（如 yt-dlp 更新）一致；内容区为空时回退主窗口。"""
        page = self.stackedWidget.currentWidget()
        return page if page is not None else self

    def _run_check_update_async(self, done_cb):
        """后台线程查询是否有新版本，结果回主线程交给 done_cb(latest|None)。"""
        class _CheckWorker(QObject):
            done = pyqtSignal(object)
            def run(s):
                try:
                    s.done.emit(check_latest_version())
                except Exception:
                    s.done.emit(None)

        thread = QThread(self)
        w = _CheckWorker()
        w.moveToThread(thread)
        thread.started.connect(w.run)
        w.done.connect(done_cb)
        w.done.connect(thread.quit)
        w.done.connect(w.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._check_worker = w
        self._check_thread = thread

    def _check_update_at_startup(self):
        """启动时后台查询是否有新版本，有则弹 Dialog 引导更新（查询失败静默跳过）"""
        self._run_check_update_async(self._on_startup_check_done)

    @pyqtSlot(object)
    def _on_startup_check_done(self, latest):
        """启动检查结果：有新版 → 弹更新 Dialog（无新版/失败静默）"""
        if latest:
            self.prompt_update(latest)

    def check_update_manual(self):
        """手动检查更新（设置页按钮调用）：后台查询，有新版弹 Dialog，无新版提示已最新。"""
        self._run_check_update_async(self._on_manual_check_done)

    @pyqtSlot(object)
    def _on_manual_check_done(self, latest):
        """手动检查结果：有新版弹 Dialog，无新版提示已是最新"""
        if latest:
            self.prompt_update(latest)
        else:
            qfw.InfoBar.info(
                parent=self._info_parent(), title="已是最新版本",
                content=f"当前已是最新版本（{get_current_version()}）",
                orient=Qt.Orientation.Horizontal, isClosable=True, duration=5000)

    def prompt_update(self, latest: str):
        """弹出「发现新版本」Dialog（更新/忽略此版本/取消）。latest 须为已查询到的版本号。"""
        if not latest:
            return

        dialog = qfw.MessageBox(
            "发现新版本",
            f"当前版本：{get_current_version()}\n最新版本：{latest}\n\n"
            "是否下载安装器进行更新？下载后需手动运行安装器完成覆盖安装。",
            self)
        dialog.yesButton.setText("更新")
        dialog.cancelButton.setText("取消")

        # 在按钮区追加「忽略此版本」按钮：点击后持久化忽略并关闭 Dialog
        self._ignore_handled = False
        ignore_btn = qfw.PushButton("忽略此版本")

        def _on_ignore():
            self._ignore_handled = True
            set_ignored_version(latest)
            try:
                dialog.reject()
            except Exception:
                dialog.close()
            qfw.InfoBar.info(
                parent=self._info_parent(), title="已忽略",
                content=f"已忽略版本 {latest}，将在出现更新版本后重新提醒。",
                orient=Qt.Orientation.Horizontal, isClosable=True, duration=5000)

        ignore_btn.clicked.connect(_on_ignore)
        dialog.addWidget(ignore_btn)

        if dialog.exec():
            # 点「更新」
            self._do_update(latest)
        elif not self._ignore_handled:
            # 点「取消」或关闭 → 不做任何事
            pass

    def _do_update(self, latest: str):
        """后台下载安装器，进度经 InfoBar 回显，完成后提示路径 + 打开按钮"""
        self._update_infobar = qfw.InfoBar.info(
            parent=self._info_parent(), title="正在下载更新",
            content=f"正在下载 Sookit {latest} 安装器…",
            orient=Qt.Orientation.Horizontal, isClosable=False, duration=-1)

        class _DownloadWorker(QObject):
            progress = pyqtSignal(str)
            done = pyqtSignal(object)
            def run(s):
                try:
                    path = download_installer(latest, lambda t: s.progress.emit(t))
                    s.done.emit(("ok", str(path)))
                except Exception as e:  # noqa: BLE001
                    s.done.emit(("error", str(e)))

        thread = QThread(self)
        w = _DownloadWorker()
        w.moveToThread(thread)
        thread.started.connect(w.run)
        w.progress.connect(self._on_update_progress)
        w.done.connect(self._on_update_download_done)
        w.done.connect(thread.quit)
        w.done.connect(w.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._update_dl_worker = w
        self._update_dl_thread = thread

    @pyqtSlot(str)
    def _on_update_progress(self, text: str):
        """下载进度 → 更新 InfoBar 内容"""
        if getattr(self, "_update_infobar", None) is not None:
            self._update_infobar.setContent(text)

    @pyqtSlot(object)
    def _on_update_download_done(self, result):
        """下载完成：成功提示路径 + 打开按钮；失败弹错误"""
        if getattr(self, "_update_infobar", None) is not None:
            try:
                self._update_infobar.close()
            except Exception:
                pass
            self._update_infobar = None
        status, payload = result
        if status != "ok":
            qfw.InfoBar.error(
                parent=self._info_parent(), title="更新失败",
                content=f"安装器下载失败：{payload}",
                orient=Qt.Orientation.Vertical, isClosable=True, duration=-1)
            return
        path = payload
        bar = qfw.InfoBar.success(
            parent=self._info_parent(), title="下载完成",
            content=f"安装器已保存到：\n{path}\n\n请运行安装器完成更新（覆盖安装，需管理员权限）。",
            orient=Qt.Orientation.Vertical, isClosable=True, duration=-1)
        open_btn = qfw.PushButton("打开文件")
        open_btn.clicked.connect(lambda: self._open_file(path))
        bar.addWidget(open_btn)
        folder_btn = qfw.PushButton("打开所在文件夹")
        folder_btn.clicked.connect(lambda: self._open_folder(path))
        bar.addWidget(folder_btn)

    def _open_file(self, path: str):
        """用系统默认程序打开文件（运行安装器）"""
        try:
            os.startfile(path)  # Windows 专用
        except Exception as e:  # noqa: BLE001
            qfw.InfoBar.warning(
                parent=self._info_parent(), title="无法打开",
                content=f"{e}", orient=Qt.Orientation.Horizontal,
                isClosable=True, duration=6000)

    def _open_folder(self, path: str):
        """在资源管理器中定位文件"""
        import subprocess
        try:
            subprocess.Popen(["explorer", "/select,", str(path)])
        except Exception as e:  # noqa: BLE001
            qfw.InfoBar.warning(
                parent=self._info_parent(), title="无法打开",
                content=f"{e}", orient=Qt.Orientation.Horizontal,
                isClosable=True, duration=6000)
