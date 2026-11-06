"""直播监控平铺条目：自适应封面、元信息及由页面处理的操作信号。"""

from datetime import datetime
import urllib.request

from PyQt6.QtCore import QEvent, QSize, QThread, Qt, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QFont, QFontMetrics, QImage, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget
import qfluentwidgets as qfw

from sookit.core.utils import get_certifi_ssl_context
from sookit.core.youtube_utils import build_thumbnails, extract_youtube_id
from sookit.widgets.elided_label import ElidedLabel, count_lines
from sookit.widgets.task_card import RoundedCoverWidget


# 线程无卡片父对象；删除卡片也必须持有线程直到 finished（决策 18）。
_ACTIVE_LOADERS = set()


class _CoverLoader(QThread):
    loaded = pyqtSignal(bytes)
    failed = pyqtSignal()

    def __init__(self, thumbnails):
        super().__init__()
        self._thumbnails = thumbnails
        self.finished.connect(self._release)

    @pyqtSlot()
    def _release(self):
        _ACTIVE_LOADERS.discard(self)
        self.deleteLater()

    @pyqtSlot()
    def cancel(self):
        self.requestInterruption()

    def run(self):
        for thumbnail in self._thumbnails:
            if self.isInterruptionRequested():
                return
            try:
                request = urllib.request.Request(
                    thumbnail["url"], headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(
                    request, timeout=10, context=get_certifi_ssl_context()
                ) as response:
                    data = response.read()
                if self.isInterruptionRequested():
                    return
                # QImage 可在工作线程解码；QPixmap 只在 GUI 回调里创建。
                if data and not QImage.fromData(data).isNull():
                    self.loaded.emit(data)
                    return
            except Exception:
                if self.isInterruptionRequested():
                    return
        if not self.isInterruptionRequested():
            self.failed.emit()


class MonitorCard(QWidget):
    """监控任务展示；操作信号携带 task_id，由页面执行业务动作。

    scheduled 接受 Unix 秒时间戳、datetime 或已格式化字符串。调用
    dispose() 可立即断开封面回调；正在进行的 HTTP 请求在超时后退出，
    不阻塞 GUI，也不会继续尝试下一档。直接删除控件同样安全。
    """

    watch_requested = pyqtSignal(str)
    start_requested = pyqtSignal(str)
    remove_requested = pyqtSignal(str)

    _MARGIN = 6
    _INFO_MIN_HEIGHT = 180
    _SPACING = 10

    def __init__(self, task_id, url, title, parent=None):
        super().__init__(parent)
        self.task_id = task_id
        self.url = url
        self._disposed = False
        self._waiting = False
        self._downloading = False
        self._cover_loader = None
        self.setMinimumWidth(320)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding,
                             QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(self._SPACING)
        self.cover = RoundedCoverWidget(308, 173, self)
        # 复用圆角绘制，不继承 CoverAreaWidget 的文件/删除交互。
        self.cover.setMinimumWidth(0)
        self.cover.setMaximumWidth(16777215)
        self.cover.setSizePolicy(QSizePolicy.Policy.Expanding,
                                 QSizePolicy.Policy.Fixed)
        self.cover.setText("封面加载中…")
        layout.addWidget(self.cover)

        self._info = QWidget(self)
        self._info.setMinimumHeight(self._INFO_MIN_HEIGHT)
        info_layout = QVBoxLayout(self._info)
        info_layout.setContentsMargins(0, 4, 0, 6)
        info_layout.setSpacing(self._SPACING)
        self.title_label = ElidedLabel(
            title or url, max_lines=2, parent=self._info,
            font=QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        meta_font = QFont("Microsoft YaHei", 10)
        self.channel_label = ElidedLabel(
            "频道：待获取", max_lines=1, parent=self._info, font=meta_font)
        self.status_label = ElidedLabel(
            "正在获取直播信息…", max_lines=2, parent=self._info, font=meta_font)
        self.time_label = ElidedLabel(
            "开播时间待定", max_lines=1, parent=self._info, font=meta_font)
        for label in self._labels():
            label.setMinimumWidth(0)
            label.setTextFormat(Qt.TextFormat.PlainText)
            info_layout.addWidget(label)
        info_layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.watch_btn = qfw.PushButton("观看", self._info)
        self.start_btn = qfw.PrimaryPushButton("立即开始", self._info)
        self.remove_btn = qfw.PushButton("删除", self._info)
        for button in (self.watch_btn, self.start_btn, self.remove_btn):
            button.setMinimumHeight(32)
            buttons.addWidget(button)
        info_layout.addLayout(buttons)
        layout.addWidget(self._info)
        self.watch_btn.clicked.connect(self._request_watch)
        self.start_btn.clicked.connect(self._request_start)
        self.remove_btn.clicked.connect(self._request_remove)
        self._update_buttons()
        qfw.qconfig.themeChangedFinished.connect(self._update_theme_colors)
        self._update_theme_colors()
        self._sync_geometry()
        self._load_cover()

    def _labels(self):
        return (self.title_label, self.channel_label,
                self.status_label, self.time_label)

    def set_metadata(self, title, channel="", scheduled=None):
        self.title_label.setText(title or self.url)
        self.channel_label.setText(f"频道：{channel or '待获取'}")
        if isinstance(scheduled, (int, float)):
            try:
                scheduled = datetime.fromtimestamp(scheduled)
            except (OverflowError, OSError, ValueError):
                scheduled = None
        if isinstance(scheduled, datetime):
            scheduled = scheduled.astimezone().strftime("%Y-%m-%d %H:%M")
        self.time_label.setText(
            f"预计开播：{scheduled}" if scheduled else "开播时间待定")
        for label in self._labels():
            label.setToolTip(label.text())
        self._sync_geometry()

    def set_status(self, text):
        self.status_label.setText(text or "")
        self.status_label.setToolTip(text or "")
        self._sync_geometry()

    def set_waiting(self, waiting):
        self._waiting = bool(waiting)
        self._update_buttons()

    def set_downloading(self, downloading):
        self._downloading = bool(downloading)
        if downloading:
            self._waiting = False
        self._update_buttons()

    def _update_buttons(self):
        self.watch_btn.setEnabled(True)
        self.start_btn.setVisible(self._waiting and not self._downloading)
        self.remove_btn.setText("取消" if self._downloading else "删除")

    @pyqtSlot()
    def _request_watch(self):
        self.watch_requested.emit(self.task_id)

    @pyqtSlot()
    def _request_start(self):
        if self._waiting and not self._downloading:
            self.start_requested.emit(self.task_id)

    @pyqtSlot()
    def _request_remove(self):
        self.remove_requested.emit(self.task_id)

    def _load_cover(self):
        thumbnails = build_thumbnails(extract_youtube_id(self.url))
        if not thumbnails:
            self.cover.setText("暂无封面")
            return
        worker = _CoverLoader(thumbnails)
        self._cover_loader = worker
        _ACTIVE_LOADERS.add(worker)
        worker.loaded.connect(self._on_cover_loaded, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(self._on_cover_failed, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._on_cover_finished)
        # 绑定 QObject 方法而非捕获 self 的 lambda：删除卡片时 Qt 自动断连。
        self.destroyed.connect(worker.cancel)
        worker.start()

    @pyqtSlot(bytes)
    def _on_cover_loaded(self, data):
        if self._disposed or self.sender() is not self._cover_loader:
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(data):
            self.cover.setPixmap(pixmap)
        else:
            self.cover.setText("暂无封面")

    @pyqtSlot()
    def _on_cover_failed(self):
        if not self._disposed and self.sender() is self._cover_loader:
            self.cover.setText("暂无封面")

    @pyqtSlot()
    def _on_cover_finished(self):
        if self.sender() is self._cover_loader:
            self._cover_loader = None

    def dispose(self):
        """幂等取消加载并断开外部回调；随后可安全 deleteLater()。"""
        if self._disposed:
            return
        self._disposed = True
        worker = self._cover_loader
        self._cover_loader = None
        if worker is not None:
            worker.requestInterruption()
            for signal, slot in ((worker.loaded, self._on_cover_loaded),
                                 (worker.failed, self._on_cover_failed),
                                 (worker.finished, self._on_cover_finished)):
                signal.disconnect(slot)
        qfw.qconfig.themeChangedFinished.disconnect(self._update_theme_colors)

    @pyqtSlot()
    def _update_theme_colors(self):
        dark = qfw.isDarkTheme()
        self.title_label.setStyleSheet(f"color: {'white' if dark else 'black'};")
        for label in (self.channel_label, self.time_label):
            label.setStyleSheet(f"color: {'#aaa' if dark else '#888'};")
        self.status_label.setStyleSheet(f"color: {'#ddd' if dark else '#444'};")
        self.cover.update()

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        content_width = max(1, width - 2 * self._MARGIN)
        cover_height = int(content_width * 9 / 16)
        # 决策 21：直接用当前宽度/标签字体算行数，不读标签旧的高度缓存。
        text_height = sum(
            count_lines(label.text(), label.font(), content_width, max_lines=lines)
            * QFontMetrics(label.font()).height()
            for label, lines in zip(self._labels(), (2, 1, 2, 1)))
        button_height = max(32, self.watch_btn.sizeHint().height(),
                            self.start_btn.sizeHint().height(),
                            self.remove_btn.sizeHint().height())
        info_height = max(self._INFO_MIN_HEIGHT,
                          10 + text_height + 4 * self._SPACING + button_height)
        return 2 * self._MARGIN + cover_height + self._SPACING + info_height

    def sizeHint(self):
        width = max(self.minimumWidth(), self.width())
        return QSize(width, self.heightForWidth(width))

    def minimumSizeHint(self):
        return QSize(self.minimumWidth(), self.heightForWidth(self.minimumWidth()))

    def _sync_geometry(self):
        self.cover.setFixedHeight(int(max(1, self.width() - 12) * 9 / 16))
        height = self.heightForWidth(self.width())
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)
        self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "time_label"):
            self._sync_geometry()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange and hasattr(self, "time_label"):
            self._sync_geometry()
