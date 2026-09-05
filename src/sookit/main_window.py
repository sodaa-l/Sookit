"""
sookit/main_window.py
主窗口 MainWindow 类（原单文件脚本的 MainWindow）
"""

import os
import sys

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFileDialog, \
    QApplication, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, \
    QSplitter, QLabel, QSizePolicy, QSystemTrayIcon
from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal, pyqtSlot, QTimer, QByteArray, QSize, QUrl
from PyQt6.QtGui import QTextCursor, QIcon, QDesktopServices

import qfluentwidgets as qfw
from qfluentwidgets import FluentIcon as FIF, NavigationItemPosition, \
    InfoBadge, InfoBadgePosition, DotInfoBadge

from sookit import APP_NAME, APP_VERSION
from sookit.paths import get_icon_path

# ---------- 从 widgets 导入自定义控件 ----------
from sookit.widgets.cover_image import CoverImageWidget
from sookit.widgets.infobar import show_infobar

# ---------- 从 core.functions 导入工具函数和功能类 ----------
from sookit.core.functions import (
    Functions, sanitize_path, format_duration, format_filesize,
    extract_youtube_id, build_thumbnails,
    fetch_youtube_metadata, get_video_duration, run_ffmpeg, run_ytdlp,
    load_theme_color, load_close_action, DEFAULT_OUTPUT_DIR, ensure_output_dir,
    check_latest_version, get_current_version, get_ignored_version,
    set_ignored_version, download_installer, RELEASES_URL,
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

        # 依赖缺失提示在各页面内展示（页面构造时检测），主窗口不再全局弹条

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
        # 展开态面板宽度：默认 322px 偏宽，收窄到 250px（setExpandWidth 会同步更新条目宽度）
        self.navigationInterface.panel.setExpandWidth(250)
        self.addSubInterface(self.youtube_page, FIF.PLAY, "视频嗅探")
        self.addSubInterface(self.monitor_page, FIF.SYNC, "直播监控")
        self.addSubInterface(self.subtitle_page, FIF.FONT, "字幕烧录")
        self.addSubInterface(self.replace_page, FIF.MUSIC, "音频覆盖")
        self.addSubInterface(self.extract_page, FIF.HEADPHONE, "音频提取")
        # 侧边栏底部
        self.addSubInterface(self.queue_page, FIF.UPDATE, "任务队列", position=NavigationItemPosition.BOTTOM)
        self.addSubInterface(self.settings_page, FIF.SETTING, "设置", position=NavigationItemPosition.BOTTOM)

        # 进入任务队列页时：进行中为空且已完成非空 → 直接落在"已完成"视图
        self.stackedWidget.currentChanged.connect(self._on_stack_changed)

        qfw.setThemeColor(load_theme_color())

        # 更新状态（驱动导航"设置" Badge）：Sookit 新版本号 / yt-dlp 有新版或未安装
        self._sookit_update = None
        self._ytdlp_update = False

        # 延时创建任务队列 Badge（确保导航界面已完全初始化）
        QTimer.singleShot(0, self._setup_queue_badge)
        # 延时创建"设置"导航项 Badge（更新来源红点：Sookit / yt-dlp）
        QTimer.singleShot(0, self._setup_settings_badge)

        # 延时自动检查更新（避免阻塞启动渲染；release 未上传/查询失败时静默跳过）
        QTimer.singleShot(2000, self._check_update_at_startup)

    def _on_stack_changed(self, index: int):
        """切换到任务队列页时：进行中为空且已完成非空 → 直接落在"已完成"。"""
        if self.stackedWidget.widget(index) is not self.queue_page:
            return
        from sookit.core.task_queue import TaskQueueManager
        mgr = TaskQueueManager.instance()
        if not mgr.get_active_tasks() and mgr.get_completed_tasks():
            self.queue_page.segment_widget.setCurrentItem("completed")

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

    # ========== "设置"导航项更新 Badge ==========

    def _setup_settings_badge(self):
        """创建"设置"导航项的空文案 error InfoBadge（尺寸与任务队列 badge 等高）"""
        nav_item = self.navigationInterface.widget(self.settings_page.objectName())
        if not nav_item:
            return

        self._settings_badge = InfoBadge.error(
            "", parent=self, target=nav_item, position=InfoBadgePosition.TOP_RIGHT)
        # 与任务队列文字 badge 等高（任务队列 badge 先创建，高度即其样式高度）
        h = self._queue_badge.height() if hasattr(self, "_queue_badge") else 16
        self._settings_badge.setFixedSize(h, h)
        # make() 仅在创建时按空文案的初始小宽度定位一次，放大后（左上角锚定）
        # 圆心会右/下偏移，须按最终尺寸重新对位
        self._settings_badge.move(self._settings_badge.manager.position())
        self._settings_badge.setVisible(False)
        self._update_settings_badge()

    def _update_settings_badge(self):
        """刷新设置圆点：任一来源（Sookit / yt-dlp）有更新即显示，无更新时隐藏"""
        if not hasattr(self, "_settings_badge"):
            return

        count = (1 if self._sookit_update else 0) + (1 if self._ytdlp_update else 0)
        self._settings_badge.setVisible(count > 0)

        # 同步设置页"检查更新"按钮上的圆点（Sookit 有新版时亮）
        self.settings_page.set_sookit_update_dot(bool(self._sookit_update))

    def _set_sookit_update(self, latest):
        """记录 Sookit 新版本状态并刷新 Badge（None = 无更新/已被忽略，跟随静默）"""
        self._sookit_update = latest
        self._update_settings_badge()

    def notify_ytdlp_update(self, available: bool):
        """SettingsPage 回调：yt-dlp 有新版本/未安装(True) 或 已更新(False)，刷新 Badge"""
        self._ytdlp_update = available
        self._update_settings_badge()

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
        """后台线程查询更新状态，结果回主线程交给 done_cb((status, version))。"""
        class _CheckWorker(QObject):
            done = pyqtSignal(object)
            def run(s):
                try:
                    s.done.emit(check_latest_version())
                except Exception:
                    s.done.emit(("failed", ""))

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
        """启动时后台查询：仅"未被忽略的新版本"弹常驻条引导更新（失败/无更新/已忽略均静默）"""
        self._run_check_update_async(lambda r: self._on_check_done(r, manual=False))

    def check_update_manual(self):
        """手动检查更新（设置页按钮调用）：结果一律回显——有新版弹常驻条（含已忽略版本），
        无新版提示已最新，失败提示检查失败并引导 GitHub。"""
        self._run_check_update_async(lambda r: self._on_check_done(r, manual=True))

    @pyqtSlot(object)
    def _on_check_done(self, result, manual: bool = False):
        """检查结果分派（四态 × 自动/手动）：

        - newer            → 常驻条引导更新（点亮 Badge）
        - ignored + 手动   → 视同有新版弹常驻条（文案注明此前已忽略）；自动检查静默
        - latest  + 手动   → 提示已是最新；自动检查静默
        - failed  + 手动   → 失败条引导 GitHub；自动检查静默
        """
        status, version = result
        if status == "newer" or (manual and status == "ignored"):
            self._set_sookit_update(version)
            self._show_update_bar(version, ignored=(status == "ignored"))
        elif status == "failed":
            if manual:
                self._show_update_failed_bar()
        elif manual:
            # 到这里只剩：latest（手动检查）→ 提示已最新；
            # 自动检查的 latest / ignored 不进入本分支（静默，不触碰 Badge）
            self._set_sookit_update(None)
            show_infobar(self, "info", title="已是最新版本",
                         content=f"当前已是最新版本（{get_current_version()}）", duration=5000)

    def _close_update_bar(self):
        """关闭旧的更新状态常驻条（防重复检查叠加多条）"""
        bar = getattr(self, "_update_bar", None)
        if bar is not None:
            try:
                bar.close()
            except Exception:  # noqa: BLE001
                pass
            self._update_bar = None

    def _show_update_bar(self, latest: str, ignored: bool = False):
        """「发现新版本」常驻 InfoBar（挂主窗口全局可见）：下载更新 / 忽略此版本。

        点 X 关闭仅关闭条（不写入忽略），下次检查仍会提示。
        """
        self._close_update_bar()
        note = "（此前已忽略）" if ignored else ""
        bar = show_infobar(
            self, "warning", title="发现新版本",
            content=f"Sookit {latest} 已发布{note}，当前版本 {get_current_version()}。"
                    "下载安装器后需手动运行完成覆盖安装。",
            duration=-1)

        dl_btn = qfw.PrimaryPushButton("下载更新")

        def _on_download():
            self._close_update_bar()
            self._do_update(latest)

        dl_btn.clicked.connect(_on_download)
        bar.addWidget(dl_btn)

        def _on_ignore():
            set_ignored_version(latest)
            self._close_update_bar()
            # 忽略后跟随静默：清除 Sookit 更新状态（Badge/圆点随之熄灭）
            self._set_sookit_update(None)
            show_infobar(self, "info", title="已忽略",
                         content=f"已忽略版本 {latest}，将在出现更新版本后重新提醒。",
                         duration=5000)

        ignore_btn = qfw.PushButton("忽略此版本")
        ignore_btn.clicked.connect(_on_ignore)
        bar.addWidget(ignore_btn)
        self._update_bar = bar

    def _show_update_failed_bar(self):
        """「检查更新失败」常驻 InfoBar：引导前往 GitHub 手动下载（不误报为已最新）"""
        self._close_update_bar()
        bar = show_infobar(
            self, "warning", title="检查更新失败",
            content="无法连接 GitHub，请检查网络（可能需要代理）后重试，或前往 GitHub 手动下载最新版本。",
            duration=-1)
        gh_btn = qfw.PushButton("前往 GitHub 下载")
        gh_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(RELEASES_URL)))
        bar.addWidget(gh_btn)
        self._update_bar = bar

    def _do_update(self, latest: str):
        """后台下载安装器，进度经 InfoBar 回显，完成后提示路径 + 打开按钮"""
        self._update_infobar = show_infobar(
            self, "info", title="正在下载更新",
            content=f"正在下载 Sookit {latest} 安装器…", closable=False)

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
            show_infobar(self, "error", title="更新失败",
                         content=f"安装器下载失败：{payload}")
            return
        path = payload
        bar = show_infobar(self, "success", title="下载完成",
                           content=f"安装器已保存到：\n{path}，\n\n请运行安装器完成更新。")
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
            show_infobar(self, "warning", title="无法打开",
                         content=f"{e}", duration=6000)

    def _open_folder(self, path: str):
        """在资源管理器中定位文件"""
        import subprocess
        try:
            subprocess.Popen(["explorer", "/select,", str(path)])
        except Exception as e:  # noqa: BLE001
            show_infobar(self, "warning", title="无法打开",
                         content=f"{e}", duration=6000)
