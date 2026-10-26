"""
设置 页面
"""
import os
import re
import weakref

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QFrame, QApplication, QSizePolicy,
    QLabel,
)
from PyQt6.QtCore import (
    Qt, QThread, QObject, pyqtSignal, pyqtSlot, QTimer, QUrl, QRectF, QSize,
)
from PyQt6.QtGui import (
    QColor, QDesktopServices, QFontMetrics, QPainter, QPainterPath, QPen,
    QPixmap,
)
from PyQt6.QtSvg import QSvgRenderer

import qfluentwidgets as qfw
from qfluentwidgets import DotInfoBadge, InfoBadgePosition
from qfluentwidgets.common.config import qconfig
from qfluentwidgets.common.style_sheet import isDarkTheme, setCustomStyleSheet

from sookit.core.functions import (
    is_ytdlp_available, get_ytdlp_source, build_ytdlp_cmd,
    launch_ytdlp_updater,
    get_deno_current_version,
    check_ytdlp_deno_update_needed, check_path_ytdlp_update,
    get_ytdlp_latest_version,
    check_ffmpeg, check_aria2c, load_download_config, save_download_config,
    load_theme_color, save_theme_color, THEME_COLORS, get_ffmpeg_path,
    set_autostart, is_autostart, load_close_action, save_close_action,
    load_task_complete_action, save_task_complete_action,
    load_toast_notification_enabled, save_toast_notification_enabled,
)
from sookit.core.config import (
    load_browser_extension_enabled, save_browser_extension_enabled,
)
from sookit.core.browser_diagnostics import (
    diagnose_browser_components, get_extension_dir,
)
from sookit.core.utils import set_smooth_scroll_params
from sookit import APP_NAME, APP_VERSION
from sookit.paths import get_chrome_logo_path
from sookit.widgets.infobar import show_infobar
from sookit.widgets.elided_label import ElidedLabel


# ===== Chrome 扩展安装引导区的小部件 =====

_GUIDE_DETAIL_QSS = "QLabel { color: rgb(96, 96, 96); font: 11px --FontFamilies; }"
_GUIDE_DETAIL_DARK_QSS = "QLabel { color: rgb(208, 208, 208); font: 11px --FontFamilies; }"


def _guide_text_label(text: str, word_wrap: bool = True) -> qfw.BodyLabel:
    """引导区次要文字：沿用卡片 contentLabel 的 11px 灰字样式（随主题切换）"""
    label = qfw.BodyLabel(text)
    label.setObjectName("contentLabel")
    label.setWordWrap(word_wrap)
    qfw.FluentStyleSheet.SETTING_CARD.apply(label)
    return label


def _guide_detail_label(text: str, object_name: str) -> qfw.BodyLabel:
    """步骤说明文字：需要独立 objectName（测试定位），因此单独给 11px 灰字样式"""
    label = qfw.BodyLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(True)
    setCustomStyleSheet(label, _GUIDE_DETAIL_QSS, _GUIDE_DETAIL_DARK_QSS)
    return label


def _guide_value_button(icon, text: str, tooltip: str) -> qfw.TransparentPushButton:
    """值条内的操作按钮：透明底 + 图标，跟随值条高度"""
    button = qfw.TransparentPushButton(icon, text)
    button.setToolTip(tooltip)
    button.setIconSize(QSize(14, 14))
    button.setFixedHeight(28)
    return button


def _guide_observe(signal, widget, callback=None) -> None:
    """订阅主题信号后重绘 widget；用弱引用，控件销毁后回调不报错"""
    ref = weakref.ref(widget)

    def _handler(*_args):
        target = ref()
        if target is None:
            return
        try:
            if callback is None:
                target.update()
            else:
                callback(target)
        except RuntimeError:
            pass

    signal.connect(_handler)


class _GuideHLine(QWidget):
    """头部与步骤之间的 1px 分隔线（手绘，避免依赖样式表注册）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(1)
        _guide_observe(qconfig.themeChangedFinished, self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(),
                         QColor(255, 255, 255, 38) if isDarkTheme()
                         else QColor(0, 0, 0, 26))


class _GuideValueChip(QWidget):
    """引导区值条：圆角浅底 + 1px 细边框，内部承载值文本与操作按钮

    宽度按内容自适应（Preferred），只有超过调用方设置的 maximumWidth 时
    才收缩并触发值文本省略。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Fixed)
        _guide_observe(qconfig.themeChangedFinished, self)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 6, 6)
        if isDarkTheme():
            painter.fillPath(path, QColor(255, 255, 255, 18))
            painter.setPen(QPen(QColor(255, 255, 255, 30), 1))
        else:
            painter.fillPath(path, QColor(0, 0, 0, 9))
            painter.setPen(QPen(QColor(0, 0, 0, 20), 1))
        painter.drawPath(path)


class _GuideStepNumber(qfw.CaptionLabel):
    """步骤序号：淡主题色圆底 + 主题色数字，主题色变化时重刷"""

    def __init__(self, number: int, parent=None):
        super().__init__(parent=parent)
        self.setText(str(number))
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFixedSize(28, 28)
        self.apply_theme_color()
        _guide_observe(qconfig.themeColorChanged, self,
                       callback=lambda label: label.apply_theme_color())

    def apply_theme_color(self):
        color = qfw.themeColor()
        rgb = f"{color.red()}, {color.green()}, {color.blue()}"
        template = ("QLabel { border-radius: 14px; font-weight: 600; "
                    "font-size: 12px; color: rgba(%s, 1.00); "
                    "background: rgba(%s, %s); }")
        setCustomStyleSheet(
            self,
            template % (rgb, rgb, "0.10"),
            template % (rgb, rgb, "0.20"))


class _GuideValueLabel(ElidedLabel):
    """值条内的长文本标签

    ElidedLabel 的 sizeHint 依赖当前宽度，无法撑开自适应值条；这里按完整
    文本宽度给出 sizeHint（超过 max_width 封顶），超宽时仍由 ElidedLabel
    在末行省略，tooltip 保留完整内容。
    """

    def __init__(self, text: str = "", max_width: int = 640, parent=None):
        super().__init__(text, max_lines=1, parent=parent)
        self._max_width = max_width
        self.setSizePolicy(QSizePolicy.Policy.Preferred,
                           QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        metrics = QFontMetrics(self.font())
        return QSize(
            min(metrics.horizontalAdvance(self.text()), self._max_width),
            super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(0, super().sizeHint().height())


class _ChromeLogo(QWidget):
    """Chrome 标志图标（16×16，与 SettingCard 的图标尺寸一致）

    使用随代码包提供的官方 Chrome 矢量标志（src/sookit/assets/chrome-logo.svg，
    取自 Google 官方 Chrome 站点），按控件矩形直接矢量渲染，任何 DPI 下都不会
    出现位图缩放的发虚效果。
    """

    _renderer = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)

    def renderer(self):
        """返回 SVG 渲染器；资源缺失或非法时返回 None"""
        if _ChromeLogo._renderer is None:
            renderer = QSvgRenderer(str(get_chrome_logo_path()))
            _ChromeLogo._renderer = renderer if renderer.isValid() else False
        return _ChromeLogo._renderer or None

    def pixmap(self):
        """返回按当前控件尺寸渲染的图标（供测试与需要位图的场景使用）"""
        renderer = self.renderer()
        if renderer is None:
            return None
        ratio = self.devicePixelRatioF()
        size = QSize(round(self.width() * ratio), round(self.height() * ratio))
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(0, 0, size.width(), size.height()))
        painter.end()
        pixmap.setDevicePixelRatio(ratio)
        return pixmap

    def paintEvent(self, event):
        renderer = self.renderer()
        if renderer is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer.render(painter, QRectF(self.rect()))


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._version_threads = []
        self._version_workers = []
        self._update_thread = None
        self._yt_current_ver = ""
        self._yt_update_checked = False
        self._yt_new_version_bar = None  # 「yt-dlp 有新版本」常驻 InfoBar（更新成功后关闭）
        self._update_progress = None
        # yt-dlp / Deno 版本检测结果（None=尚未返回；""=未安装/获取失败）
        self._yt_ver = None
        self._deno_ver = None
        self._browser_extension_load_error = None
        try:
            self._browser_extension_enabled = load_browser_extension_enabled()
        except Exception as exc:
            self._browser_extension_enabled = False
            self._browser_extension_load_error = str(exc)
        self._init_ui()
        self._connect_signals()
        QTimer.singleShot(200, self._check_versions)

    def _init_ui(self):
        # 创建滚动区域（qfw.ScrollArea 自带平滑滚动 + Fluent 滚动条）
        self.scroll_area = qfw.ScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # 创建滚动内容容器
        scroll_content = QWidget()
        
        # 让滚动区域和内容容器背景透明，以正确跟随深色/浅色主题
        scroll_content.setStyleSheet("QWidget { background: transparent; }")
        
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(4)

        # 设置滚动区域的内容
        self.scroll_area.setWidget(scroll_content)
        self.scroll_area.enableTransparentBackground()
        # 平滑滚动调参：duration 250ms（库默认 400 偏绵长）+ fps 120 更细腻
        set_smooth_scroll_params(self.scroll_area)
        
        # 将滚动区域添加到主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.scroll_area)

        # ===== 常规 =====
        _subtitle_style = "SubtitleLabel { font-weight: normal; }"
        self.setStyleSheet(_subtitle_style)
        layout.addWidget(qfw.SubtitleLabel("常规"))
        layout.addSpacing(12)

        autostart_card = qfw.CardWidget(self)
        autostart_card.setMinimumHeight(70)
        autostart_lay = QHBoxLayout(autostart_card)
        autostart_lay.setContentsMargins(15, 12, 15, 12)
        autostart_lay.addWidget(qfw.BodyLabel("开机自启"))
        autostart_lay.addStretch()
        self.autostart_switch = qfw.SwitchButton()
        self.autostart_switch.setChecked(False)
        autostart_lay.addWidget(self.autostart_switch)
        layout.addWidget(autostart_card)

        # 关闭主面板时
        close_action_card = qfw.CardWidget(self)
        close_action_card.setMinimumHeight(70)
        close_action_lay = QHBoxLayout(close_action_card)
        close_action_lay.setContentsMargins(15, 12, 15, 12)
        close_action_lay.addWidget(qfw.BodyLabel("关闭主面板时"))
        close_action_lay.addStretch()
        self.close_action_combo = qfw.ComboBox()
        self.close_action_combo.addItems(["最小化至托盘", "直接退出"])
        self.close_action_combo.setCurrentIndex(load_close_action())
        close_action_lay.addWidget(self.close_action_combo)
        layout.addWidget(close_action_card)

        # 任务完成后
        task_complete_card = qfw.CardWidget(self)
        task_complete_card.setMinimumHeight(70)
        task_complete_lay = QHBoxLayout(task_complete_card)
        task_complete_lay.setContentsMargins(15, 12, 15, 12)
        task_complete_lay.addWidget(qfw.BodyLabel("任务完成后"))
        task_complete_lay.addStretch()
        self.task_complete_combo = qfw.ComboBox()
        self.task_complete_combo.addItems(["不操作", "关闭工具箱", "关闭计算机"])
        self._last_task_complete_idx = load_task_complete_action()
        self.task_complete_combo.setCurrentIndex(self._last_task_complete_idx)
        task_complete_lay.addWidget(self.task_complete_combo)
        layout.addWidget(task_complete_card)

        # 系统通知（决策 40：主窗口不在焦点时任务完成/失败、嗅探/解析结果弹 Windows Toast）
        toast_card = qfw.CardWidget(self)
        toast_card.setMinimumHeight(70)
        toast_lay = QHBoxLayout(toast_card)
        toast_lay.setContentsMargins(15, 12, 15, 12)
        toast_lay.addWidget(qfw.BodyLabel("任务与嗅探/解析结果系统通知"))
        toast_lay.addStretch()
        self.toast_switch = qfw.SwitchButton()
        self.toast_switch.setChecked(True)
        toast_lay.addWidget(self.toast_switch)
        layout.addWidget(toast_card)

        layout.addSpacing(16)

        # ===== 浏览器扩展 =====
        layout.addWidget(qfw.SubtitleLabel("浏览器扩展"))
        layout.addSpacing(12)

        self.browser_toggle_card = qfw.CardWidget(self)
        self.browser_toggle_card.setMinimumHeight(70)
        browser_toggle_lay = QVBoxLayout(self.browser_toggle_card)
        browser_toggle_lay.setContentsMargins(15, 12, 15, 12)
        browser_toggle_lay.setSpacing(4)
        browser_toggle_row = QHBoxLayout()
        browser_toggle_row.addWidget(qfw.BodyLabel("允许浏览器扩展调用 Sookit"))
        browser_toggle_row.addStretch()
        self.browser_extension_switch = qfw.SwitchButton()
        self.browser_extension_switch.setChecked(self._browser_extension_enabled)
        self.browser_extension_switch.setToolTip(
            "此开关只控制 Sookit 是否接收请求；扩展自身启停由 Chrome 管理。")
        browser_toggle_row.addWidget(self.browser_extension_switch)
        browser_toggle_lay.addLayout(browser_toggle_row)
        self.browser_extension_paused_label = qfw.BodyLabel(
            "已暂停接收浏览器请求，可随时重新开启")
        self.browser_extension_paused_label.setVisible(not self._browser_extension_enabled)
        browser_toggle_lay.addWidget(self.browser_extension_paused_label)
        self.browser_extension_config_error_label = qfw.BodyLabel(
            f"无法读取浏览器扩展开关，已按关闭处理：{self._browser_extension_load_error}"
            if self._browser_extension_load_error else "")
        self.browser_extension_config_error_label.setWordWrap(True)
        self.browser_extension_config_error_label.setVisible(
            bool(self._browser_extension_load_error))
        browser_toggle_lay.addWidget(self.browser_extension_config_error_label)
        layout.addWidget(self.browser_toggle_card)

        self.browser_setup_card = qfw.CardWidget(self)
        browser_setup_lay = QVBoxLayout(self.browser_setup_card)
        browser_setup_lay.setContentsMargins(0, 0, 0, 0)
        browser_setup_lay.setSpacing(0)

        # 头部排版对齐设置页其它卡片（qfw.SettingCard）：70px 高、
        # 16px 图标 + 16px 间距 + 14px 标题 / 11px 说明，文本块垂直居中。
        browser_setup_header = QWidget(self.browser_setup_card)
        browser_setup_header.setFixedHeight(70)
        browser_setup_header_lay = QHBoxLayout(browser_setup_header)
        browser_setup_header_lay.setContentsMargins(16, 0, 15, 0)
        browser_setup_header_lay.setSpacing(0)
        browser_setup_header_lay.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        browser_setup_header_lay.addWidget(
            _ChromeLogo(browser_setup_header), 0, Qt.AlignmentFlag.AlignLeft)
        browser_setup_header_lay.addSpacing(16)
        browser_setup_text = QVBoxLayout()
        browser_setup_text.setContentsMargins(0, 0, 0, 0)
        browser_setup_text.setSpacing(0)
        browser_setup_text.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        browser_setup_title = QLabel("Chrome 扩展", browser_setup_header)
        qfw.FluentStyleSheet.SETTING_CARD.apply(browser_setup_title)
        browser_setup_description = _guide_text_label(
            "扩展已随安装包提供，首次使用需在 Chrome 中手动加载",
            word_wrap=False)
        browser_setup_text.addWidget(browser_setup_title, 0,
                                     Qt.AlignmentFlag.AlignLeft)
        browser_setup_text.addWidget(browser_setup_description, 0,
                                     Qt.AlignmentFlag.AlignLeft)
        browser_setup_header_lay.addLayout(browser_setup_text)
        browser_setup_header_lay.addSpacing(16)
        browser_setup_header_lay.addStretch(1)
        self.browser_install_guide_button = qfw.PrimaryPushButton("安装引导")
        self.browser_install_guide_button.setIcon(qfw.FluentIcon.DOWN)
        self.browser_install_guide_button.setFixedHeight(32)
        self.browser_install_guide_button.setMinimumWidth(112)
        browser_setup_header_lay.addWidget(self.browser_install_guide_button)
        browser_setup_lay.addWidget(browser_setup_header)

        # 引导区整体容器：折叠时连分隔线一起隐藏，卡片底部不留空档
        self.browser_guide_container = QWidget(self.browser_setup_card)
        guide_container_lay = QVBoxLayout(self.browser_guide_container)
        guide_container_lay.setContentsMargins(16, 0, 16, 16)
        guide_container_lay.setSpacing(0)
        guide_container_lay.addSpacing(14)
        self.browser_guide_divider = _GuideHLine(self.browser_guide_container)
        guide_container_lay.addWidget(self.browser_guide_divider)
        guide_container_lay.addSpacing(16)

        self.browser_guide_panel = QWidget(self.browser_guide_container)
        guide_container_lay.addWidget(self.browser_guide_panel)
        browser_guide_lay = QVBoxLayout(self.browser_guide_panel)
        browser_guide_lay.setContentsMargins(0, 0, 0, 0)
        browser_guide_lay.setSpacing(16)

        def add_guide_step(number, title, detail):
            row = QHBoxLayout()
            row.setSpacing(12)
            number_label = _GuideStepNumber(number, self.browser_guide_panel)
            number_label.setObjectName(f"browserGuideStepNumber{number}")
            self._guide_badges.append(number_label)
            row.addWidget(number_label, 0, Qt.AlignmentFlag.AlignTop)

            step_content = QWidget(self.browser_guide_panel)
            step_content.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            content = QVBoxLayout(step_content)
            content.setContentsMargins(0, 0, 0, 0)
            content.setSpacing(6)
            title_label = qfw.BodyLabel(title)
            title_label.setObjectName(f"browserGuideStepTitle{number}")
            title_font = title_label.font()
            title_font.setBold(True)
            title_label.setFont(title_font)
            content.addWidget(title_label)
            content.addWidget(_guide_detail_label(
                detail, f"browserGuideStepDetail{number}"))
            # 步骤内容必须整体作为一个控件加入行布局：直接嵌套布局时，
            # 值条的宽度上限会传染到行布局，导致序号与标题被挤到中间。
            row.addWidget(step_content, 1)
            browser_guide_lay.addLayout(row)
            return content

        def add_value_row(content, prefix_label, value_label, buttons,
                          max_width=0):
            row = QHBoxLayout()
            row.setSpacing(0)
            chip = _GuideValueChip(self.browser_guide_panel)
            if max_width:
                chip.setMaximumWidth(max_width)
            self._guide_chips.append(chip)
            chip_lay = QHBoxLayout(chip)
            chip_lay.setContentsMargins(10, 2, 6, 2)
            chip_lay.setSpacing(8)
            if prefix_label is not None:
                chip_lay.addWidget(prefix_label)
            chip_lay.addWidget(value_label)
            for button in buttons:
                chip_lay.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(chip, 0)
            row.addStretch(1)
            content.addLayout(row)
            return chip

        self._guide_badges = []
        self._guide_chips = []

        self.browser_copy_extensions_url_button = _guide_value_button(
            qfw.FluentIcon.COPY, "复制地址", "复制 Chrome 扩展页地址")
        content1 = add_guide_step(
            1, "打开扩展管理", "在 Chrome 地址栏输入下方地址")
        add_value_row(
            content1, None,
            _guide_text_label("chrome://extensions/", word_wrap=False),
            [self.browser_copy_extensions_url_button])

        add_guide_step(
            2, "开启开发者模式", "打开页面右上角的开发者模式开关")

        content3 = add_guide_step(
            3, "加载扩展", "点击“加载已解压的扩展程序”，选择扩展目录")
        self.browser_extension_path_prefix_label = _guide_text_label("扩展目录")
        self.browser_extension_path_label = _GuideValueLabel(
            "", max_width=640, parent=self.browser_guide_panel)
        self.browser_extension_path_label.setObjectName("contentLabel")
        self.browser_extension_path_label.setToolTip("")
        qfw.FluentStyleSheet.SETTING_CARD.apply(
            self.browser_extension_path_label)
        self.browser_copy_folder_button = _guide_value_button(
            qfw.FluentIcon.COPY, "复制路径", "复制扩展目录路径")
        self.browser_guide_open_folder_button = _guide_value_button(
            qfw.FluentIcon.FOLDER, "打开文件夹", "在文件管理器中打开扩展目录")
        add_value_row(
            content3, self.browser_extension_path_prefix_label,
            self.browser_extension_path_label,
            [self.browser_copy_folder_button,
             self.browser_guide_open_folder_button],
            max_width=820)

        add_guide_step(
            4, "返回 YouTube 验证", "刷新 YouTube 视频页并点击 Sookit")

        note_icon = QLabel(self.browser_guide_panel)
        note_icon.setPixmap(
            qfw.FluentIcon.INFO.icon(color=QColor(128, 128, 128)).pixmap(14, 14))
        note_icon.setFixedSize(14, 14)
        note_row = QHBoxLayout()
        note_row.setContentsMargins(40, 4, 0, 0)
        note_row.setSpacing(6)
        note_row.addWidget(note_icon, 0, Qt.AlignmentFlag.AlignTop)
        note_row.addWidget(_guide_text_label(
            "扩展更新后，在 Chrome 扩展页重新加载并刷新视频页"), 1)
        browser_guide_lay.addLayout(note_row)
        browser_setup_lay.addWidget(self.browser_guide_container)
        self._browser_guide_expanded = False
        self.browser_guide_container.setVisible(False)
        self.browser_guide_panel.setVisible(False)
        layout.addWidget(self.browser_setup_card)

        self.browser_components_card = qfw.CardWidget(self)
        browser_components_lay = QVBoxLayout(self.browser_components_card)
        browser_components_lay.setContentsMargins(15, 12, 15, 12)
        browser_components_lay.setSpacing(6)
        browser_components_row = QHBoxLayout()
        self.browser_components_status = qfw.BodyLabel("正在检查本机组件…")
        browser_components_row.addWidget(self.browser_components_status, 1)
        self.browser_components_refresh_button = qfw.PrimaryPushButton("重新检查")
        browser_components_row.addWidget(self.browser_components_refresh_button)
        browser_components_lay.addLayout(browser_components_row)
        self.browser_components_details = qfw.BodyLabel("")
        self.browser_components_details.setWordWrap(True)
        self.browser_components_details.setVisible(False)
        browser_components_lay.addWidget(self.browser_components_details)
        layout.addWidget(self.browser_components_card)
        self._refresh_browser_components()

        layout.addSpacing(16)

        # ===== 下载/更新 =====
        layout.addWidget(qfw.SubtitleLabel("依赖更新"))
        layout.addSpacing(12)

        self.yt_card = qfw.CardWidget(self)
        self.yt_card.setMinimumHeight(70)
        yt_lay = QHBoxLayout(self.yt_card)
        yt_lay.setContentsMargins(15, 12, 15, 12)
        self.yt_label = qfw.BodyLabel("yt-dlp  —  获取中…")
        self.yt_btn = qfw.PrimaryPushButton("下载/更新")
        yt_lay.addWidget(self.yt_label, 1)
        yt_lay.addWidget(self.yt_btn)
        layout.addWidget(self.yt_card)

        layout.addSpacing(16)

        # ===== 下载设置 =====
        layout.addWidget(qfw.SubtitleLabel("下载设置"))
        layout.addSpacing(12)

        # 并发分片数（决策 46）：HLS/DASH 分片流的并发下载数（yt-dlp `-N`）。
        # HLS 恒由 yt-dlp 内建 hlsnative 下载器负责（aria2c 不支持 m3u8_native），
        # 所以这张卡片与 aria2c 是否可用无关，不参与下面的灰化。
        self.fragments_card = qfw.CardWidget(self)
        self.fragments_card.setMinimumHeight(70)
        fragments_lay = QHBoxLayout(self.fragments_card)
        fragments_lay.setContentsMargins(15, 12, 15, 12)
        fragments_lay.addWidget(qfw.BodyLabel("yt-dlp 并发分片数"))
        fragments_lay.addStretch()
        self.fragments_combo = qfw.ComboBox()
        # 阶梯档位（1=顺序下载，其余按 2 的幂），默认 8。不用 1~16 连续档，
        # 是因为并发数没有「7 比 6 好」这种精细需求，档位少更易选。
        self.fragments_combo.addItems(['1', '2', '4', '8', '16'])
        self.fragments_combo.setCurrentText('8')
        fragments_lay.addWidget(self.fragments_combo)
        layout.addWidget(self.fragments_card)

        # aria2c 开关
        self.aria2c_switch_card = qfw.CardWidget(self)
        self.aria2c_switch_card.setMinimumHeight(70)
        aria2c_switch_lay = QHBoxLayout(self.aria2c_switch_card)
        aria2c_switch_lay.setContentsMargins(15, 12, 15, 12)
        aria2c_switch_lay.addWidget(qfw.BodyLabel("使用 aria2c 加速"))
        aria2c_switch_lay.addStretch()
        self.aria2c_switch = qfw.SwitchButton()
        self.aria2c_switch.setChecked(True)
        aria2c_switch_lay.addWidget(self.aria2c_switch)
        layout.addWidget(self.aria2c_switch_card)

        # aria2c 连接数
        self.connections_card = qfw.CardWidget(self)
        self.connections_card.setMinimumHeight(70)
        connections_lay = QHBoxLayout(self.connections_card)
        connections_lay.setContentsMargins(15, 12, 15, 12)
        connections_lay.addWidget(qfw.BodyLabel("aria2c 连接数"))
        connections_lay.addStretch()
        self.connections_combo = qfw.ComboBox()
        self.connections_combo.addItems([str(i) for i in range(4, 33, 4)])
        self.connections_combo.setCurrentIndex(3)  # 默认16
        connections_lay.addWidget(self.connections_combo)
        layout.addWidget(self.connections_card)

        layout.addSpacing(16)

        # ===== 个性化 =====
        layout.addWidget(qfw.SubtitleLabel("个性化"))
        layout.addSpacing(12)

        theme_card = qfw.SettingCard(qfw.FluentIcon.BRUSH, "应用主题", "切换浅色/深色模式", parent=self)
        theme_card.setFixedHeight(70)
        self.theme_combo = qfw.ComboBox()
        self.theme_combo.addItems(["浅色", "深色", "使用系统设置"])
        self.theme_combo.setCurrentIndex(2)
        theme_card.hBoxLayout.addSpacing(15)
        theme_card.hBoxLayout.addWidget(self.theme_combo)
        m = theme_card.hBoxLayout.contentsMargins()
        m.setRight(15)
        theme_card.hBoxLayout.setContentsMargins(m)
        layout.addWidget(theme_card)

        # 主题颜色选择
        color_card = qfw.SettingCard(qfw.FluentIcon.PALETTE, "主题颜色", "更改软件的强调色", parent=self)
        color_card.setFixedHeight(70)
        self.color_btn = qfw.PrimaryDropDownPushButton("主题颜色")
        self.color_menu = qfw.RoundMenu("主题颜色", self)
        self._color_actions = []
        for color_name in THEME_COLORS.keys():
            action = qfw.Action(color_name, self)
            action.setCheckable(True)
            self.color_menu.addAction(action)
            self._color_actions.append(action)
        self.color_btn.setMenu(self.color_menu)
        color_card.hBoxLayout.addSpacing(15)
        color_card.hBoxLayout.addWidget(self.color_btn)
        # 设置右侧边距
        m = color_card.hBoxLayout.contentsMargins()
        m.setRight(15)
        color_card.hBoxLayout.setContentsMargins(m)
        layout.addWidget(color_card)

        layout.addSpacing(16)

        # ===== 关于 =====
        layout.addWidget(qfw.SubtitleLabel("关于"))
        layout.addSpacing(12)

        about_card = qfw.CardWidget(self)
        about_card.setMinimumHeight(70)
        about_lay = QHBoxLayout(about_card)
        about_lay.setContentsMargins(15, 12, 15, 12)
        about_lay.addWidget(qfw.BodyLabel(f"{APP_NAME}  —  {APP_VERSION}"))
        about_lay.addStretch()
        self.check_update_btn = qfw.PrimaryPushButton("检查更新")
        about_lay.addWidget(self.check_update_btn)
        layout.addWidget(about_card)

        self.ff_card = qfw.CardWidget(self)
        self.ff_card.setMinimumHeight(70)
        ff_lay = QHBoxLayout(self.ff_card)
        ff_lay.setContentsMargins(15, 12, 15, 12)
        self.ff_label = qfw.BodyLabel("FFmpeg  —  获取中…")
        ff_lay.addWidget(self.ff_label, 1)
        layout.addWidget(self.ff_card)

        # aria2c 版本信息（已嵌入，无需下载按钮）
        self.aria2c_card = qfw.CardWidget(self)
        self.aria2c_card.setMinimumHeight(70)
        aria2c_lay = QHBoxLayout(self.aria2c_card)
        aria2c_lay.setContentsMargins(15, 12, 15, 12)
        self.aria2c_label = qfw.BodyLabel("aria2c  —  检测中…")
        aria2c_lay.addWidget(self.aria2c_label, 1)
        layout.addWidget(self.aria2c_card)

        # 按钮圆点 badge（DotInfoBadge 无文字，点击穿透，随卡片滚动；
        # error 红色在主题色按钮上醒目，默认 4px 太小故放大到 10px）
        self._yt_btn_dot = DotInfoBadge.error(
            parent=self.yt_card, target=self.yt_btn, position=InfoBadgePosition.TOP_RIGHT)
        self._yt_btn_dot.setFixedSize(10, 10)
        self._yt_btn_dot.setVisible(False)
        self._check_btn_dot = DotInfoBadge.error(
            parent=about_card, target=self.check_update_btn, position=InfoBadgePosition.TOP_RIGHT)
        self._check_btn_dot.setFixedSize(10, 10)
        self._check_btn_dot.setVisible(False)

        layout.addStretch()

    def _connect_signals(self):
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.color_menu.triggered.connect(self._on_color_changed)
        self.yt_btn.clicked.connect(lambda: self._update_ytdlp())
        # 下载设置信号连接
        self.fragments_combo.currentIndexChanged.connect(self._on_download_setting_changed)
        self.aria2c_switch.checkedChanged.connect(self._on_download_setting_changed)
        self.connections_combo.currentIndexChanged.connect(self._on_download_setting_changed)
        # 开机自启信号连接
        self.autostart_switch.checkedChanged.connect(self._on_autostart_changed)
        # 关闭行为信号连接
        self.close_action_combo.currentIndexChanged.connect(self._on_close_action_changed)
        # 任务完成后信号连接
        self.task_complete_combo.currentIndexChanged.connect(self._on_task_complete_changed)
        # 系统通知开关信号连接（决策 40）
        self.toast_switch.checkedChanged.connect(self._on_toast_notification_changed)
        # 浏览器扩展设置与引导
        self.browser_extension_switch.checkedChanged.connect(
            self._on_browser_extension_changed)
        self.browser_install_guide_button.clicked.connect(self._toggle_browser_guide)
        self.browser_guide_open_folder_button.clicked.connect(
            self._open_browser_extension_folder)
        self.browser_copy_extensions_url_button.clicked.connect(
            self._copy_chrome_extensions_url)
        self.browser_copy_folder_button.clicked.connect(self._copy_browser_extension_path)
        self.browser_components_refresh_button.clicked.connect(
            self._refresh_browser_components)
        # 检查更新按钮信号连接
        self.check_update_btn.clicked.connect(self._on_check_update)

    def _on_theme_changed(self, idx):
        mapping = {0: qfw.Theme.LIGHT, 1: qfw.Theme.DARK, 2: qfw.Theme.AUTO}
        qfw.setTheme(mapping[idx], save=True)
    
    def _on_color_changed(self, action):
        """主题颜色变更处理"""
        color_name = action.text()
        if color_name in THEME_COLORS:
            color = THEME_COLORS[color_name]
            qfw.setThemeColor(color)
            save_theme_color(color)
            # 更新按钮文本
            self.color_btn.setText(color_name)
            # 更新勾选状态
            for a in self._color_actions:
                a.setChecked(a == action)
    
    def _on_download_setting_changed(self):
        """下载设置改变时保存配置"""
        download_config = {
            'concurrent_fragments': int(self.fragments_combo.currentText()),
            'use_aria2c': self.aria2c_switch.isChecked(),
            'aria2c_connections': int(self.connections_combo.currentText()),
        }
        save_download_config(download_config)

    def _on_autostart_changed(self, checked: bool):
        """开机自启开关变更"""
        set_autostart(checked)

    def _on_close_action_changed(self, index: int):
        """关闭行为变更: 0=最小化至托盘, 1=直接退出"""
        save_close_action(index)

    def _on_task_complete_changed(self, index: int):
        """任务完成后变更: 0=不操作, 1=关闭工具箱, 2=关闭计算机"""
        # 如果从"关闭计算机"切换到其他选项，取消关机
        if hasattr(self, '_last_task_complete_idx') and self._last_task_complete_idx == 2 and index != 2:
            os.system("shutdown /a >nul 2>nul")
        self._last_task_complete_idx = index
        save_task_complete_action(index)

    def _on_toast_notification_changed(self, checked: bool):
        """系统通知开关变更（决策 40）"""
        save_toast_notification_enabled(checked)

    def _on_browser_extension_changed(self, checked: bool):
        """保存浏览器扩展开关；失败时恢复 UI 并提示原因。"""
        try:
            save_browser_extension_enabled(checked)
        except Exception as exc:
            was_blocked = self.browser_extension_switch.blockSignals(True)
            self.browser_extension_switch.setChecked(self._browser_extension_enabled)
            self.browser_extension_switch.blockSignals(was_blocked)
            show_infobar(
                self, "error", title="保存失败",
                content=f"无法保存浏览器扩展开关，已恢复原状态：{exc}", duration=6000)
            return

        self._browser_extension_enabled = checked
        self._browser_extension_load_error = None
        self.browser_extension_paused_label.setVisible(not checked)
        self.browser_extension_config_error_label.clear()
        self.browser_extension_config_error_label.setVisible(False)

    def _toggle_browser_guide(self):
        self._browser_guide_expanded = not self._browser_guide_expanded
        self.browser_guide_container.setVisible(self._browser_guide_expanded)
        self.browser_guide_panel.setVisible(self._browser_guide_expanded)
        self.browser_install_guide_button.setText(
            "收起引导" if self._browser_guide_expanded else "安装引导")
        self.browser_install_guide_button.setIcon(
            qfw.FluentIcon.UP if self._browser_guide_expanded
            else qfw.FluentIcon.DOWN)

    def _browser_extension_path(self):
        return get_extension_dir()

    def _copy_chrome_extensions_url(self):
        QApplication.clipboard().setText("chrome://extensions/")
        show_infobar(self, "success", title="已复制", content="Chrome 扩展页地址已复制。",
                     duration=2500)

    def _copy_browser_extension_path(self):
        path = self._browser_extension_path()
        QApplication.clipboard().setText(str(path))
        show_infobar(self, "success", title="已复制", content="扩展目录路径已复制。",
                     duration=2500)

    def _open_browser_extension_folder(self):
        path = self._browser_extension_path()
        if not path.is_dir():
            show_infobar(
                self, "error", title="找不到扩展目录",
                content=f"未找到扩展目录：{path}", duration=6000)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            show_infobar(
                self, "error", title="无法打开文件夹",
                content=f"系统未能打开扩展目录：{path}", duration=6000)

    def _refresh_browser_components(self):
        """重新读取本机扩展/桥接文件与 Chrome 注册项，不修改系统状态。"""
        extension_dir = self._browser_extension_path()
        if hasattr(self, "browser_extension_path_label"):
            # 前缀「扩展目录」单独成标签，路径只放路径本身，便于超长时省略显示。
            self.browser_extension_path_prefix_label.setText(
                "扩展目录" if extension_dir.is_dir() else "未找到扩展目录")
            self.browser_extension_path_label.setText(str(extension_dir))
            self.browser_extension_path_label.setToolTip(str(extension_dir))
        try:
            result = diagnose_browser_components()
        except Exception as exc:
            result = {"ok": False, "issues": [f"检查本机组件失败：{exc}"]}

        issues = list(dict.fromkeys(result.get("issues", [])))
        if result.get("ok") and not issues:
            self.browser_components_status.setText("扩展文件完整 · 浏览器桥接正常")
            self.browser_components_details.clear()
            self.browser_components_details.setVisible(False)
            return

        self.browser_components_status.setText("本机组件需要处理")
        self.browser_components_details.setText("\n".join(f"• {issue}" for issue in issues))
        self.browser_components_details.setVisible(True)

    # ========== 安装引导与本机组件诊断 ==========

    def _on_check_update(self):
        """「检查更新」按钮：复用主窗口更新流程（后台查询 → 结果回显）。

        检查在后台线程进行，期间按钮置灰 + 文案「检查中…」+ 常驻「检查中」提示条，
        完成后由主窗口 _on_check_done 回调 on_manual_check_finished 统一恢复。
        """
        win = self.window()
        if win is None or not hasattr(win, "check_update_manual"):
            show_infobar(self, "info", title="提示",
                         content="未找到主窗口，无法检查更新", duration=5000)
            return
        if getattr(self, "_manual_checking", False):
            return  # 已在检查中，避免叠加多条提示
        self._manual_checking = True
        self.check_update_btn.setEnabled(False)
        self.check_update_btn.setText("检查中…")
        self._check_progress_bar = show_infobar(
            self, "info", title="检查中",
            content=f"正在检查 {APP_NAME} 的新版本…", closable=False)
        win.check_update_manual()

    def on_manual_check_finished(self):
        """手动检查完成（主窗口 _on_check_done 回调）：恢复按钮、关闭「检查中」条"""
        self._manual_checking = False
        self.check_update_btn.setEnabled(True)
        self.check_update_btn.setText("检查更新")
        bar = getattr(self, "_check_progress_bar", None)
        if bar is not None:
            try:
                bar.close()
            except RuntimeError:
                pass
            self._check_progress_bar = None

    @staticmethod
    def _parse_ytdlp_version(text: str) -> str:
        """yt-dlp 版本：第一非空行"""
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("WARNING"):
                return line
        return ""

    @staticmethod
    def _parse_ffmpeg_version(text: str) -> str:
        """ffmpeg 版本：从 'ffmpeg version X.Y.Z' 中提取"""
        m = re.search(r'ffmpeg\s+version\s+(\S+)', text, re.IGNORECASE)
        return m.group(1) if m else ""

    def _check_versions(self):
        """并行检测工具的版本（yt-dlp 与 Deno 并行）"""
        self._version_threads = []
        self._version_workers = []   # 保活 worker，防止 GC
        self._yt_ver = None
        self._deno_ver = None
        source = get_ytdlp_source()
        # yt-dlp（PATH 全局或内置 tools/ 均可）
        if is_ytdlp_available():
            w = self._create_worker(build_ytdlp_cmd("--version"), self._parse_ytdlp_version)
            if w:
                self._version_workers.append(w)
                w.finished.connect(self._on_yt_version)
                w.thread().start()
            # 内置 yt-dlp 时才需检测 Deno（PATH 全局版不管 Deno，按需求）
            if source == "tools":
                dw = self._create_deno_worker()
                if dw:
                    self._version_workers.append(dw)
                    dw.finished.connect(self._on_deno_version)
                    dw.thread().start()
                else:
                    # worker 创建失败兜底：当作未检测到 Deno，避免版本标签卡在等待
                    self._deno_ver = ""
        else:
            self._yt_ver = ""
            # 未安装 yt-dlp：视同待处理，点亮圆点并同步主窗口 Badge 引导安装
            self._set_yt_dot(True)
            self._notify_ytdlp_update(True)
            self._render_yt_label()
        # ffmpeg（版本信息在 stderr）
        if check_ffmpeg():
            # 检查是否使用内嵌版本
            ffmpeg_path = get_ffmpeg_path()
            is_embedded = os.path.exists(ffmpeg_path)
            if is_embedded:
                w = self._create_worker([ffmpeg_path, "-version"], self._parse_ffmpeg_version)
            else:
                w = self._create_worker(["ffmpeg", "-version"], self._parse_ffmpeg_version)
            if w:
                self._version_workers.append(w)
                w.finished.connect(self._on_ff_version)
                w.thread().start()
        else:
            self.ff_label.setText("FFmpeg  —  未安装")
        # aria2c 版本检测；未检测到时灰化相关设置卡片（下载侧已有回退默认下载器的兜底）
        aria2c_ok, aria2c_ver = check_aria2c()
        self.aria2c_label.setText(f"aria2c  —  {aria2c_ver}")
        self.aria2c_switch_card.setEnabled(aria2c_ok)
        self.connections_card.setEnabled(aria2c_ok)
        
        # 加载常规配置
        self._load_general_config()
        # 加载下载配置
        self._load_download_config()
        # 加载主题颜色配置
        self._load_theme_config()

    def _load_download_config(self):
        """加载下载配置到 UI"""
        config = load_download_config()
        # load_download_config() 已把并发分片数归一成合法档位（决策 46），
        # 这里直接按字符串选中即可，无需要再兜底。
        self.fragments_combo.setCurrentText(str(config['concurrent_fragments']))
        self.aria2c_switch.setChecked(config['use_aria2c'])
        self.connections_combo.setCurrentText(str(config['aria2c_connections']))
    
    def _load_theme_config(self):
        """加载主题颜色配置到 UI"""
        current_color = load_theme_color()
        # 根据颜色值找到对应的名称
        for name, color in THEME_COLORS.items():
            if color == current_color:
                self.color_btn.setText(name)
                # 设置对应的勾选状态
                for action in self._color_actions:
                    action.setChecked(action.text() == name)
                break

    def _load_general_config(self):
        """加载常规配置到 UI（开机自启状态、任务完成后、系统通知）"""
        self.autostart_switch.setChecked(is_autostart())
        self.task_complete_combo.setCurrentIndex(load_task_complete_action())
        self.toast_switch.setChecked(load_toast_notification_enabled())

    def _create_worker(self, cmd, parser):
        """创建后台线程 worker，返回 worker 对象"""
        try:
            thread = QThread(self)
            from sookit.core.workers import SingleCmdWorker
            worker = SingleCmdWorker(cmd, parser)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            self._version_threads.append(thread)
            return worker
        except Exception:
            return None

    def _create_deno_worker(self):
        """创建后台线程 worker 检测内置 Deno 版本，返回 worker 对象"""
        try:
            thread = QThread(self)

            class _DenoWorker(QObject):
                finished = pyqtSignal(object)
                def run(s):
                    s.finished.emit(get_deno_current_version())

            worker = _DenoWorker()
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            self._version_threads.append(thread)
            return worker
        except Exception:
            return None

    @pyqtSlot(object)
    def _on_yt_version(self, v):
        self._yt_ver = v if v else ""
        self._render_yt_label()
        # 版本检测完成后，顺便查 PyPI 是否有新版（仅一次）
        if v and not self._yt_update_checked:
            self._yt_update_checked = True
            self._check_yt_update_needed(v)

    @pyqtSlot(object)
    def _on_deno_version(self, v):
        self._deno_ver = v if v else ""
        self._render_yt_label()

    def _render_yt_label(self):
        """组合渲染 yt-dlp 版本标签。

        - PATH 来源：只显示 yt-dlp（不管 Deno）
        - 内置来源：显示 yt-dlp + Deno（Deno 缺失显示「未安装」）
        - 未安装：显示「未安装」
        """
        source = get_ytdlp_source()
        if source == "tools":
            # 内置来源：需 yt-dlp 与 Deno 版本都检测完才渲染
            if self._yt_ver is None or self._deno_ver is None:
                return
            yt_txt = self._yt_ver if self._yt_ver else "未知"
            deno_txt = self._deno_ver if self._deno_ver else "未安装"
            self.yt_label.setText(f"yt-dlp（内置）  —  {yt_txt} / Deno  —  {deno_txt}")
        elif source == "path":
            yt_txt = self._yt_ver if self._yt_ver else "未知"
            self.yt_label.setText(f"yt-dlp（PATH）  —  {yt_txt}")
        else:
            self.yt_label.setText("yt-dlp  —  未安装")

    @pyqtSlot(object)
    def _on_ff_version(self, v):
        if v:
            self.ff_label.setText(f"FFmpeg  —  {v}")
        else:
            self.ff_label.setText("FFmpeg  —  未安装")

    def _check_yt_update_needed(self, current_ver: str):
        """后台通过 GitHub releases 重定向查最新版（与手动检查/更新同源），有新版本则弹窗"""
        class _LatestVersionWorker(QObject):
            done = pyqtSignal(str)
            def run(s):
                s.done.emit(get_ytdlp_latest_version())

        self._yt_current_ver = current_ver
        thread = QThread(self)
        w = _LatestVersionWorker()
        w.moveToThread(thread)
        thread.started.connect(w.run)
        w.done.connect(self._on_latest_version)
        w.done.connect(thread.quit)
        w.done.connect(w.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._version_workers.append(w)
        self._version_threads.append(thread)

    @staticmethod
    def _normalize_version(v: str) -> str:
        """标准化版本号：2026.03.17 → 2026.3.17（去前导零）"""
        try:
            return ".".join(str(int(p)) for p in v.split("."))
        except Exception:
            return v

    @pyqtSlot(str)
    def _on_latest_version(self, ver):
        """在主线程比较版本，有新版本则点亮圆点并弹常驻 InfoBar（含前往设置按钮，非阻塞）。

        PATH 来源例外：不点亮圆点/Badge、不弹「前往设置」条——PATH 副本由用户
        自行管理，设置页无代更新入口，「前往设置」会形成死循环引导（点按钮仍提示
        自行更新）；PATH 的新版提示由主窗口周期检查的全局 InfoBar 负责（2026-09-06 决策）。
        """
        if not ver:
            return
        cur = self._normalize_version(self._yt_current_ver)
        lat = self._normalize_version(ver)
        if not (lat and cur and lat > cur):
            return
        if get_ytdlp_source() == "path":
            return
        self._set_yt_dot(True)
        self._notify_ytdlp_update(True)
        if self._yt_new_version_bar is None:
            self._yt_new_version_bar = show_infobar(
                self, "warning", title="yt-dlp 有新版本",
                content=f"当前版本 {cur}，最新版本 {lat}。不更新可能导致视频嗅探失败，请前往设置页更新。",
                duration=-1)
            btn = qfw.PushButton("前往设置")
            btn.clicked.connect(self._go_to_settings)
            self._yt_new_version_bar.addWidget(btn)

    def _go_to_settings(self):
        win = self.window()
        if win is not None and hasattr(win, "switchTo"):
            win.switchTo(win.settings_page)

    # ========== 更新状态圆点（按钮 badge） ==========

    def _set_yt_dot(self, visible: bool):
        """控制 yt-dlp「下载/更新」按钮圆点显示"""
        dot = getattr(self, "_yt_btn_dot", None)
        if dot is not None:
            dot.setVisible(visible)

    def set_sookit_update_dot(self, visible: bool):
        """供主窗口调用：Sookit 有新版本时点亮「检查更新」按钮圆点"""
        dot = getattr(self, "_check_btn_dot", None)
        if dot is not None:
            dot.setVisible(visible)

    def set_ytdlp_update_dot(self, visible: bool):
        """供主窗口调用：yt-dlp 有新版本时点亮「下载/更新」按钮圆点"""
        self._set_yt_dot(visible)

    def _notify_ytdlp_update(self, available: bool):
        """把 yt-dlp 更新状态同步给主窗口（刷新导航"设置" Badge）"""
        win = self.window()
        if win is not None and hasattr(win, "notify_ytdlp_update"):
            win.notify_ytdlp_update(available)

    def _clear_yt_update_state(self):
        """更新完成/已最新：清圆点并同步主窗口"""
        self._set_yt_dot(False)
        self._notify_ytdlp_update(False)

    def close_yt_new_version_bar(self):
        """更新成功后关闭「有新版本」提示条（避免过时提示）"""
        bar = getattr(self, "_yt_new_version_bar", None)
        if bar is not None:
            try:
                bar.close()
            except RuntimeError:
                pass
            self._yt_new_version_bar = None

    def _update_ytdlp(self, skip_check: bool = False):
        """按来源三分支处理下载/更新：
        - path → 主动跑一次 PATH 版本检查并回显结果（有新版引导自行更新，不代下载）
        - tools → 先「检查中」查新版本（skip_check=True 时跳过，如自动更新路径已确认有新版），
                  确需更新才切「更新中」提权下载；查询失败如实弹错误并引导手动更新
        - None  → 未安装，直接「安装中」提权下载到 tools/yt-dlp/（含 Deno 运行时）
        """
        source = get_ytdlp_source()
        if source == "path":
            # PATH 副本由用户自行管理：按钮不做下载，改为主动检查一次并回显结果
            self.yt_btn.setEnabled(False)
            self.yt_btn.setText("检查中…")
            self.yt_label.setText("yt-dlp（PATH）  —  正在检查新版本…")
            if self._update_progress:
                self._update_progress.close()
            self._update_progress = show_infobar(
                self, "info", title="检查中",
                content="正在检查 PATH 中 yt-dlp 的新版本…", closable=False)
            self._start_path_check()
            return

        action = "更新" if source == "tools" else "安装"
        self.yt_btn.setEnabled(False)
        if source == "tools" and not skip_check:
            # 检查态：先普通权限查新版本，确需更新才提权下载
            self.yt_btn.setText("检查中…")
            self.yt_label.setText("yt-dlp  —  正在检查新版本…")
            if self._update_progress:
                self._update_progress.close()
            self._update_progress = show_infobar(
                self, "info", title="检查中",
                content="正在检查 yt-dlp（含 Deno 运行时）的新版本…", closable=False)
            self._start_check()
        else:
            # 未安装 / 已确认有新版本：直接进入下载态
            self.yt_btn.setText("下载中…")
            self.yt_label.setText(f"正在{action} yt-dlp…")
            if self._update_progress:
                self._update_progress.close()
            self._update_progress = show_infobar(
                self, "info", title=f"{action}中",
                content=f"正在{action} yt-dlp（含 Deno 运行时）…", closable=False)
            self._start_download()

    def _start_check(self):
        """后台线程检查 yt-dlp/Deno 是否需要更新（普通权限，不下载）"""
        class _YtdlpCheckWorker(QObject):
            done = pyqtSignal(object)
            def run(s):
                try:
                    s.done.emit(check_ytdlp_deno_update_needed())
                except Exception as e:  # noqa: BLE001 极端异常同样按查询失败处理
                    s.done.emit((False, "check_failed", str(e),
                                 False, "check_failed", str(e)))

        thread = QThread(self)
        w = _YtdlpCheckWorker()
        w.moveToThread(thread)
        thread.started.connect(w.run)
        w.done.connect(self._on_check_done)
        w.done.connect(thread.quit)
        w.done.connect(w.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._update_worker = w
        self._update_thread = thread

    @pyqtSlot(object)
    def _on_check_done(self, result):
        """检查完成：已最新→汇总提示；需更新→切「更新中」下载态；查询失败→错误提示引导手动更新"""
        yt_needed, deno_needed, yt_state, deno_state = result
        any_check_failed = yt_state == "check_failed" or deno_state == "check_failed"

        if any_check_failed and not yt_needed and not deno_needed:
            # 查询失败且没有确定要更新的组件：不下载，如实告知并引导自行更新
            if self._update_progress:
                self._update_progress.close()
                self._update_progress = None
            self.yt_btn.setEnabled(True)
            self.yt_btn.setText("下载/更新")
            self.yt_label.setText("yt-dlp / Deno  —  检查更新失败")
            show_infobar(self, "error", title="检查更新失败",
                                 content="无法从 GitHub 获取 yt-dlp/Deno 最新版本信息，请检查网络或代理后重试；"
                                         "也可前往 https://github.com/yt-dlp/yt-dlp/releases 手动下载，"
                                         "覆盖程序目录 tools\\yt-dlp\\ 下的 yt-dlp.exe")
            return

        if not yt_needed and not deno_needed:
            self._on_ytdlp_download_done((True, "up_to_date", "", True, "up_to_date", ""))
            return

        # 确需更新（含部分组件查询失败但另一组件确定需更新，失败组件交 updater 自行判断）
        if self._update_progress:
            self._update_progress.close()
        self.yt_btn.setText("下载中…")
        self.yt_label.setText("正在更新 yt-dlp…")
        self._update_progress = show_infobar(
            self, "info", title="更新中",
            content="正在更新 yt-dlp（含 Deno 运行时）…", closable=False)
        self._start_download()

    def _start_path_check(self):
        """后台线程检查 PATH 来源 yt-dlp 是否有新版本（只查不装，副本由用户自行更新）"""
        class _PathYtdlpCheckWorker(QObject):
            done = pyqtSignal(object)
            def run(s):
                try:
                    s.done.emit(check_path_ytdlp_update())
                except Exception as e:  # noqa: BLE001 极端异常按查询失败处理
                    s.done.emit(("failed", "", str(e)))

        thread = QThread(self)
        w = _PathYtdlpCheckWorker()
        w.moveToThread(thread)
        thread.started.connect(w.run)
        w.done.connect(self._on_path_check_done)
        w.done.connect(thread.quit)
        w.done.connect(w.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._update_worker = w
        self._update_thread = thread

    @pyqtSlot(object)
    def _on_path_check_done(self, result):
        """PATH 检查结果回显：有新版→带版本号引导自行更新；已最新→提示；
        查询失败→如实报错并引导手动更新（与 tools 查询失败同口径，不误报已最新）"""
        if self._update_progress:
            self._update_progress.close()
            self._update_progress = None
        self.yt_btn.setEnabled(True)
        self.yt_btn.setText("下载/更新")
        self._render_yt_label()

        status, current, latest = result
        if status == "newer":
            show_infobar(
                self, "warning", title="yt-dlp 有新版本",
                content=f"PATH 中的 yt-dlp 可更新：当前 {current}，最新 {latest}。"
                        "该副本由系统 PATH 管理，请自行更新（如 scoop update yt-dlp）。",
                duration=8000)
        elif status == "latest":
            show_infobar(
                self, "info", title="已是最新版本",
                content=f"PATH 中的 yt-dlp 已是最新版本（{current}）", duration=5000)
        elif status == "skipped":
            show_infobar(self, "info", title="提示",
                         content="未检测到 PATH 中的 yt-dlp", duration=5000)
        else:
            show_infobar(
                self, "error", title="检查更新失败",
                content="无法从 GitHub 获取 yt-dlp 最新版本信息，请检查网络或代理后重试；"
                        "也可前往 https://github.com/yt-dlp/yt-dlp/releases 手动更新 PATH 中的 yt-dlp")

    def _start_download(self):
        """后台线程提权调起 updater.exe 下载/更新 yt-dlp 与 Deno，
        进度经 yt_label 文字回显。返回 (yt_ok, yt_status, yt_err, deno_ok, deno_status, deno_err)"""
        class _YtdlpDownloadWorker(QObject):
            progress = pyqtSignal(str)
            done = pyqtSignal(object)
            def run(s):
                try:
                    result = launch_ytdlp_updater(lambda t: s.progress.emit(t))
                except Exception as e:  # noqa: BLE001
                    result = (False, "failed", str(e), False, "failed", str(e))
                s.done.emit(result)

        thread = QThread(self)
        w = _YtdlpDownloadWorker()
        w.moveToThread(thread)
        thread.started.connect(w.run)
        w.progress.connect(self._on_ytdlp_progress)
        w.done.connect(self._on_ytdlp_download_done)
        w.done.connect(thread.quit)
        w.done.connect(w.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._update_worker = w
        self._update_thread = thread

    @pyqtSlot(str)
    def _on_ytdlp_progress(self, text: str):
        """下载进度 → 直接更新 yt_label 文字（如：下载 yt-dlp.exe — 23% (4.0 MB / 17.3 MB)）"""
        self.yt_label.setText(text)

    @pyqtSlot(object)
    def _on_ytdlp_download_done(self, result):
        yt_ok, yt_status, yt_err, deno_ok, deno_status, deno_err = result
        if self._update_progress:
            self._update_progress.close()
            self._update_progress = None
        self.yt_btn.setEnabled(True)
        self.yt_btn.setText("下载/更新")

        # 组装各组件状态描述（用户主动取消不算失败，中性显示「已取消」）
        def _comp_state(name, ok, status):
            if status == "cancelled":
                return f"{name} 已取消"
            if not ok:
                return f"{name} 失败"
            return f"{name} 已更新" if status == "updated" else f"{name} 已最新"

        yt_state = _comp_state("yt-dlp", yt_ok, yt_status)
        deno_state = _comp_state("Deno", deno_ok, deno_status)
        any_updated = (yt_ok and yt_status == "updated") or (deno_ok and deno_status == "updated")
        all_up_to_date = (yt_ok and yt_status == "up_to_date") and (deno_ok and deno_status == "up_to_date")
        # 真失败 = 非取消的未成功；用户主动取消走中性「已取消」提示而非红色错误条
        any_failed = any((not ok) and status != "cancelled"
                         for ok, status in ((yt_ok, yt_status), (deno_ok, deno_status)))
        all_cancelled = yt_status == "cancelled" and deno_status == "cancelled"

        if all_up_to_date:
            # 两者都已最新
            self.yt_label.setText("yt-dlp / Deno  —  已是最新版本")
            self._clear_yt_update_state()
            show_infobar(self, "info", title="提示",
                         content="yt-dlp 与 Deno 均已是最新版本，无需更新",
                         duration=5000)
        elif any_failed:
            self.yt_label.setText("yt-dlp / Deno  —  更新异常")
            show_infobar(self, "error", title="部分更新失败",
                                 content=f"yt-dlp: {yt_state}\nDeno: {deno_state}"
                                         + (f"\n\nyt-dlp 错误: {yt_err}" if not yt_ok else "")
                                         + (f"\nDeno 错误: {deno_err}" if not deno_ok else ""))
        elif all_cancelled:
            # 全部组件均为用户取消：中性提示，不算失败
            self.yt_label.setText("yt-dlp / Deno  —  已取消")
            show_infobar(self, "info", title="已取消",
                         content="本次更新已取消，可随时重新发起", duration=5000)
        elif any_updated:
            show_infobar(self, "success", title="完成",
                         content=f"{yt_state}；{deno_state}", duration=5000)
            self.close_yt_new_version_bar()  # 更新成功，关闭「有新版本」提示条
            self._clear_yt_update_state()   # 更新成功，清圆点并同步主窗口 Badge
            # 装好后刷新主窗口及各页面「未找到 yt-dlp」提示（修复2）
            win = self.window()
            if win is not None and hasattr(win, "refresh_ytdlp_status"):
                win.refresh_ytdlp_status()
            # 系统通知（决策 40）：主窗口不在焦点时提示更新完毕
            if win is not None and hasattr(win, "notify_ytdlp_updated"):
                win.notify_ytdlp_updated()
        # 刷新版本
        QTimer.singleShot(1000, self._check_versions)