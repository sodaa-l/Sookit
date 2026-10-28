"""
视频嗅探下载 页面
"""
import os
import re
import glob
import urllib.request
from urllib.parse import urlparse

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QSplitter, QWidget, QFrame,
    QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt, QThread, QTimer, QByteArray, pyqtSignal, pyqtSlot

from sookit.core.workers import GenericWorker
from PyQt6.QtGui import QFont, QPixmap, QImage, QColor, QPainter

import qfluentwidgets as qfw

from sookit.core.functions import (
    Functions, FormatType, is_ytdlp_available, extract_youtube_id, format_duration, build_thumbnails,
    load_download_config, DEFAULT_OUTPUT_DIR
)
from sookit.core.config import (
    load_sniff_view, save_sniff_view, SNIFF_VIEW_DEFAULT, SNIFF_VIEW_TABLE
)
from sookit.core.sniff_options import (
    build_view_options, is_standard_pair,
    WEBM_VIDEO_CODECS, WEBM_AUDIO_CODECS, MP4_VIDEO_CODECS, MP4_AUDIO_CODECS,
)
from sookit.core.utils import set_smooth_scroll_params
from sookit.widgets.cover_image import CoverImageWidget
from sookit.widgets.combo_box import StaticComboBox
from sookit.widgets.infobar import show_infobar
from sookit.widgets.notifier import notify_result_notice
from sookit.widgets.option_grid import OptionFlowLayout
from sookit.widgets.option_row import OptionRow
from sookit.widgets.subtitle_panel import SubtitlePanel, CommentDownloadDialog
from sookit.core.subtitle_utils import download_subtitles, download_comments
from sookit.pages.base import PageBase
from sookit.core.task_queue import TaskType
from sookit.core.ffmpeg_utils import run_ytdlp
from sookit.core.ytdlp_utils import build_ytdlp_cmd
from sookit.core.utils import get_certifi_ssl_context
from sookit.paths import get_cover_dir


def _sniff_image_format(path):
    """读文件头魔数检测实际图片格式（仅兜底失败时用于日志说明，不做逻辑分支依据）"""
    try:
        with open(path, 'rb') as f:
            head = f.read(32)
    except OSError:
        return '未知'
    if head[:3] == b'\xff\xd8\xff':
        return 'JPEG'
    if head[:8] == b'\x89PNG\r\n\x1a\n':
        return 'PNG'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'WEBP'
    if len(head) >= 12 and head[4:8] == b'ftyp':
        brand = head[8:12].decode('ascii', errors='replace').lower()
        if brand.startswith('avi'):
            return 'AVIF'
        if brand in ('heic', 'heix', 'mif1', 'msf1', 'hevc', 'hevx'):
            return 'HEIF/HEIC'
        return brand.upper()
    if head[:4] in (b'II*\x00', b'MM\x00*'):
        return 'TIFF'
    if head[:2] == b'BM':
        return 'BMP'
    if head[:2] == b'\xff\x0a' or head[:12] == b'\x00\x00\x00\x0cJXL \r\n\x87\n':
        return 'JPEG XL'
    if head[:1] == b'<' and (b'<svg' in head[:64] or head[:5] == b'<?xml'):
        return 'SVG'
    if head[:4] == b'\x00\x00\x01\x00':
        return 'ICO'
    return '未知'


def _find_fallback_product(cache_dir, name):
    """遍历 fallback_{name}.* 兜底产物（排除 .img 原始落盘），返回第一个 QImage 可解的路径"""
    for p in glob.glob(os.path.join(str(cache_dir), f"fallback_{name}.*")):
        if p.endswith('.img'):
            continue
        if not QImage(p).isNull():
            return p
    return None


class _TableEdgeRule(QWidget):
    """格式表格自绘的 1px 边线（决策 56），竖向/横向都靠控件的长宽决定。

    qfw 表格的右/下边线只能靠 `setBorderVisible(True)` 那条外框补（决策 55），而外框
    是贴着控件四周画的：这张表为了滚动条贴窗口右缘（决策 49、54）铺满了整宽，列宽又
    只分到「可用宽度 - RIGHT_RESERVE」，外框一开就会把右侧留给滚动条的 24px 一起圈进
    框里 —— 右边线落在窗口边上而不是列尾，底边也被迫闭合。所以外框整条关掉，右竖线
    改由本控件画在「列尾」；有数据时底边不画任何东西，表格右下保持开口。

    另外两段空表专用的边线（0 行时 qfw 自己画不出来）：
    - 左边线平时由行号列（纵向表头）各格的 left border 连成，0 行时纵向表头宽度为 0，
      那条线整段消失；
    - 底边线本来就无来源，空表补上让框闭合；一有数据就撤掉，恢复右下开口。
    都见 `_sync_edge_rules()`。

    颜色/粗细与 qfw 表头格线同一口径（亮 `rgba(0,0,0,15)`、暗 `rgba(255,255,255,21)`），
    边线压在表头和数据行上，所以必须不挡鼠标。
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # 主题切换后重绘，配色读 qfw.isDarkTheme()（与 option_row/task_card 同一做法）
        qfw.qconfig.themeChangedFinished.connect(self.update)

    def paintEvent(self, event):
        color = QColor(255, 255, 255, 21) if qfw.isDarkTheme() else QColor(0, 0, 0, 15)
        QPainter(self).fillRect(self.rect(), color)


class YouTubePage(PageBase):
    """视频嗅探与下载——封面和格式左右分栏布局"""

    # 默认视图档位列表的左右留白（决策 50、53）。两侧都取固定值、不随滚动条
    # 显隐变化——滚动条一出现/消失卡片宽度就跳动，比「多空几像素」更难看。
    # 卡片不再设宽度上限（决策 51）：一旦封顶，窗口继续变宽时多出来的宽度会全部
    # 堆在右侧，左右留白就不再相等了。卡片铺满「可用宽度 / 2」，两列永远等宽。
    OPTION_MARGIN_X = 15
    # qfw 滚动条浮在 viewport 右缘、不占布局空间，但会盖住这 13px，
    # 右边距必须比它大，否则滚动条会压在卡片上。
    SCROLLBAR_RESERVE = 13
    # 右边距固定 24px = 让开滚动条 13px + 卡片到滚动条的呼吸空间 11px。
    OPTION_MARGIN_RIGHT = SCROLLBAR_RESERVE + 11

    def __init__(self, parent=None):
        super().__init__(parent)

        # ---- 内部状态 ----
        self._formats_data = []
        self._sniff_timed_out = False
        self._sniff_video_id = ''
        self._sniff_title = ''
        self._sniff_channel = ''
        self._sniff_duration = 0
        self._last_sniff_url = ''
        self._subtitle_url = ''  # 仅成功嗅探后绑定；不能用后来被编辑的输入框
        self._comment_tracks = []
        self._content_tab = 'media'
        self._media_view = SNIFF_VIEW_DEFAULT
        self._sniff_worker = None
        # 嗅探代际：每次开始/停止嗅探递增。回调携带发起时的序号，对不上即丢弃——
        # 覆盖「用户停止后仍有在途结果到达」的竞态与「旧嗅探结果覆盖新嗅探」两种情况
        # （见 docs/decisions/40-windows-toast-notifications.md 追加段）
        self._sniff_seq = 0
        self._cover_loader_worker = None
        self._cover_download_worker = None
        self._cover_data = None  # 封面原始字节数据（供任务队列复用）
        self._cover_data_seq = None  # 字幕任务只复用同一轮嗅探的封面
        self._sniff_thumbs = []  # 嗅探得到的封面列表（_populate_cover_combo 有效分支时保存，供入队复用）
        self._cover_fallback_worker = None  # 最终兜底 worker（落盘下载 + ffmpeg 转码）
        self._fallback_tried_urls = set()   # 已尝试过兜底的 URL（同 URL 会话内只试一次，防循环）
        self._ytdlp_warning_bar = None  # 「未找到 yt-dlp」常驻 infobar，装好后关闭
        self._format_updating = False  # 格式表填充/互斥反勾期间守卫，防止 itemChanged 递归误触发
        # 默认视图（决策 47）状态：档位列表、档位行控件、当前选中档位
        self._view_options = {'video': [], 'audio': []}
        self._option_rows = []
        self._option_flows = []
        self._selected_option = None
        self._restoring_view = False

        # ---- 主布局 ----
        # 右边距为 0：让右面板（及其中的默认视图滚动区）一直延伸到窗口右缘，
        # 滚动条才不会浮在卡片区中间（决策 49）。原来 20px 的右缩进由各行
        # 自己的右边距补回（URL 行 20、表格视图 24、档位列表 30），
        # 因此除滚动条位置外，其余控件的位置和宽度都与改动前一致。
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 0, 20)
        layout.setSpacing(10)

        title = qfw.TitleLabel("视频嗅探下载")
        layout.addWidget(title)
        layout.addWidget(self.create_caption_label("粘贴视频链接，嗅探所有可用格式，自由选择下载"))
        layout.addSpacing(6)

        # 检查 yt-dlp 可用性（PATH 全局或内置 tools/ 均可）
        if not is_ytdlp_available():
            self._ytdlp_warning_bar = show_infobar(self, "warning", title="依赖缺失",
                                                   content="未找到 yt-dlp，嗅探功能不可用。请前往设置页下载安装")
            self.add_goto_settings_button(self._ytdlp_warning_bar)

        # ---- URL 输入行 ----
        url_row = QHBoxLayout()
        # 补回主布局让出的 20px 右缩进，URL 行宽度与改动前一致
        url_row.setContentsMargins(0, 0, 20, 0)
        self.url_input = qfw.LineEdit()
        self.url_input.setPlaceholderText("粘贴视频链接...")
        # 蓝色嗅探按钮
        self.sniff_btn = qfw.PrimaryPushButton("嗅探")
        self.sniff_btn.setFixedWidth(100)
        self.sniff_btn.clicked.connect(self.do_sniff)
        # 灰色停止嗅探按钮（初始隐藏）
        self.stop_sniff_btn = qfw.PushButton("停止嗅探")
        self.stop_sniff_btn.setFixedWidth(100)
        self.stop_sniff_btn.clicked.connect(self.stop_sniff)
        self.stop_sniff_btn.setVisible(False)
        url_row.addWidget(self.url_input)
        url_row.addWidget(self.sniff_btn)
        url_row.addWidget(self.stop_sniff_btn)
        self.remote_cb = qfw.CheckBox("增强嗅探 (使用 ejs)")
        url_row.addWidget(self.remote_cb)
        layout.addLayout(url_row)

        # ---- 左右分栏 ----
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ====== 左面板：封面 + 标题 ======
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 8, 4)
        left_layout.setSpacing(6)

        # 视频标题 + 频道/时长
        self.cover_title = qfw.BodyLabel("")
        self.cover_title.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        self.cover_title.setWordWrap(True)
        self.cover_title.setMinimumHeight(40)
        left_layout.addWidget(self.cover_title)
        self.cover_meta = self.create_caption_label("")
        self.cover_meta.setFont(QFont("Microsoft YaHei", 10))
        self.cover_meta.setWordWrap(True)
        left_layout.addWidget(self.cover_meta)

        # 封面图片显示区域（自绘，始终 16:9，无黑边）
        self.cover_label = CoverImageWidget()
        left_layout.addWidget(self.cover_label, stretch=1)

        # 封面分辨率选择 + 下载按钮
        cover_control = QHBoxLayout()
        cover_control.addWidget(qfw.BodyLabel("封面分辨率:"))
        self.cover_combo = StaticComboBox()
        self.cover_combo.setMinimumWidth(180)
        self.cover_combo.setEnabled(False)
        cover_control.addWidget(self.cover_combo)
        cover_control.addSpacing(6)
        self.cover_btn = qfw.PushButton("下载封面")
        self.cover_btn.setEnabled(False)
        self.cover_btn.clicked.connect(self._download_cover)
        cover_control.addWidget(self.cover_btn)
        cover_control.addStretch()
        left_layout.addLayout(cover_control)

        # 下载目录行（保存到目录 + 浏览按钮）
        download_dir_row = QHBoxLayout()
        download_dir_row.addWidget(qfw.BodyLabel("保存目录:"))
        self.out_dir = qfw.LineEdit()
        self.out_dir.setPlaceholderText("下载保存目录 (默认: /下载)")
        browse_btn = qfw.PushButton("浏览")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(lambda: self.browse_dir(self.out_dir))
        download_dir_row.addWidget(self.out_dir, stretch=1)
        download_dir_row.addWidget(browse_btn)
        left_layout.addLayout(download_dir_row)

        # ====== 右面板：视图切换 + 默认视图 / 表格视图（决策 47） ======
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        # 右面板统一左留白（决策 52）：视图切换按钮、档位卡片、格式表格三者
        # 共用这一个内距，左边缘才对得齐。右侧内距仍为 0——默认视图的滚动区
        # 要贴到窗口右缘（决策 49）。
        right_layout.setContentsMargins(self.OPTION_MARGIN_X, 4, 0, 4)
        right_layout.setSpacing(8)

        # 内容切换与音视频视图互相独立，右缘与卡片共用固定留白。
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, self.OPTION_MARGIN_RIGHT, 0)
        self.content_segment = qfw.SegmentedWidget()
        media_item = self.content_segment.addItem('media', '音视频')
        subtitles_item = self.content_segment.addItem('subtitles', '字幕')
        segment_item_width = max(media_item.sizeHint().width(), subtitles_item.sizeHint().width()) + 24
        media_item.setFixedWidth(segment_item_width)
        subtitles_item.setFixedWidth(segment_item_width)
        self.content_segment.setCurrentItem('media')
        self.content_segment.currentItemChanged.connect(self._on_content_changed)
        self.content_segment.hide()  # 有字幕（包括自动字幕）或评论后才出现
        header_row.addWidget(self.content_segment)
        header_row.addStretch(1)
        self.view_button = qfw.PushButton("表格视图")
        self.view_button.clicked.connect(self._toggle_media_view)
        header_row.addWidget(self.view_button)
        right_layout.addLayout(header_row)

        # ---- 默认视图：单列平铺档位 + 一键下载 ----
        self.default_view = QWidget()
        default_layout = QVBoxLayout(self.default_view)
        default_layout.setContentsMargins(0, 0, 0, 0)
        default_layout.setSpacing(8)

        self.option_scroll = qfw.ScrollArea()
        self.option_scroll.setWidgetResizable(True)
        self.option_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.option_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.option_content = QWidget()
        self.option_content.setStyleSheet("QWidget { background: transparent; }")
        self.option_layout = QVBoxLayout(self.option_content)
        # 左留白已由右面板统一提供（决策 52），这里左边距归 0 才能让卡片与
        # 视图切换按钮、格式表格左边缘对齐。右侧固定 24px（决策 53），不随
        # 滚动条显隐变化。
        self.option_layout.setContentsMargins(0, 0, self.OPTION_MARGIN_RIGHT, 0)
        self.option_layout.setSpacing(6)
        self.option_layout.addStretch(1)
        self.option_scroll.setWidget(self.option_content)
        # 与队列页/GIF 页一致：qfw.ScrollArea 默认是不透明面板色，不置透明会露出灰底
        self.option_scroll.enableTransparentBackground()
        set_smooth_scroll_params(self.option_scroll)

        # 空态占位：默认视图是进入页面的落点，嗅探前只有一片空白会更像「坏了」。
        # 必须允许换行：否则这一整句会让右面板最小宽度涨到 322px，窗口变窄时
        # Qt 只能去压左面板（封面区被挤扁）——这正是「封面区域被压缩」的根因。
        self.option_empty_hint = qfw.BodyLabel("粘贴视频链接并点击「嗅探」，这里会列出可选画质")
        self.option_empty_hint.setWordWrap(True)
        self.option_empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.option_empty_hint.setStyleSheet("color: #9a9a9a; background: transparent;")
        default_layout.addWidget(self.option_empty_hint, stretch=1)
        default_layout.addWidget(self.option_scroll, stretch=1)
        self.option_scroll.setVisible(False)   # 有档位后才显示列表

        # 与表格视图的下载按钮同尺寸同位置（240×默认高，水平居中）
        self.default_download_btn = qfw.PrimaryPushButton("▶ 下载")
        self.default_download_btn.setFixedWidth(240)
        self.default_download_btn.setEnabled(False)  # 嗅探出档位后才可点
        self.default_download_btn.clicked.connect(self._download_current_content)
        # 按钮单独一行并留出 24px 右距 = 原主布局 20 + 右面板 4：
        # 右面板已贴到窗口右缘，不补回这个内距按钮整体会右移 12px（决策 49）
        default_btn_row = QHBoxLayout()
        default_btn_row.setContentsMargins(0, 0, 24, 0)
        default_btn_row.addWidget(self.default_download_btn,
                                  alignment=Qt.AlignmentFlag.AlignCenter)
        default_layout.addLayout(default_btn_row)

        # ---- 表格视图：原格式表格 + 合并下载 ----
        self.table_view = QWidget()
        table_layout = QVBoxLayout(self.table_view)
        # 表格铺满到窗口右缘（决策 54）：qfw 滚动条是表格的子控件，位置按表格
        # 自身宽度算（`move(width - 13, 1)`），表格不铺满它就只能停在里面。
        # 内容右侧的留白改由列宽分配让出（见 _FormatTable.RIGHT_RESERVE）。
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(6)

        # 格式表格 —— 自定义子类，resize 时按比例分配列宽
        class _FormatTable(qfw.TableWidget):
            # 列顺序与表头一致：选择 / ID / 类型 / 质量 / 编码 / 语言（决策 59 加了 ID 列）
            # 权重按「真机字体量出的最长文案 + 32px 单元格内距」定（决策 59）：
            # 类型「视频+音频」94px、质量「1080p 60fps」/「Default, high」111px、
            # 编码「avc1+aac」92px、语言「ko」47px，ID 保到能放下「http-2176」的 98px。
            # 1131 宽窗口下实际列宽 [55, 102, 102, 120, 97, 55]；超长 ID 靠省略号 + tooltip。
            COL_FACTORS = [40, 73, 73, 86, 70, 40]
            COL_MIN = 50
            # 右侧留白（决策 54）：滚动条浮在 viewport 右缘、不占布局空间，会盖住
            # 最右边 13px。列宽只分到「可用宽度 - 24」，于是这 24px 里 13px 让开
            # 滚动条、余 11px 与默认视图卡片的右边距同一口径（OPTION_MARGIN_RIGHT）。
            RIGHT_RESERVE = 24
            def resizeEvent(self, event):
                super().resizeEvent(event)
                self._distribute_columns()
            def updateGeometries(self):
                # 行号列（纵向表头）的宽度、滚动条的显隐都会改变 viewport 宽度，但 Qt
                # 不会为这些变化发 resizeEvent —— 只靠 resizeEvent 重算，列宽和右竖线
                # 会停在「表格还没填数据」时算出的值上（决策 56 补记：填完数据后竖线
                # 偏左 33px）。updateGeometries 是 QTableView 在几何重算后必调的口子。
                super().updateGeometries()
                if getattr(self, '_geometry_syncing', False):
                    return
                self._geometry_syncing = True
                try:
                    self._distribute_columns()
                finally:
                    self._geometry_syncing = False
            def _distribute_columns(self):
                total = sum(self.COL_FACTORS)
                avail = max(self.viewport().width() - self.RIGHT_RESERVE,
                            self.COL_MIN * len(self.COL_FACTORS))
                hdr = self.horizontalHeader()
                hdr.setStretchLastSection(False)
                for i in range(self.columnCount()):
                    hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
                for i, f in enumerate(self.COL_FACTORS):
                    w = max(int(avail * f / total), self.COL_MIN)
                    self.setColumnWidth(i, w)
                self._sync_edge_rules()
            def _sync_edge_rules(self):
                """把右侧 1px 竖线贴到「列尾」；空表时再补上左边线和底边线（决策 56）"""
                # 列宽是 viewport 内坐标，竖线是表格子控件：要补上行号列（纵向表头）宽度
                content_end = (self.verticalHeader().width()
                               + sum(self.columnWidth(i) for i in range(self.columnCount())))
                # 兜底：窗口极窄时 COL_MIN 托底可能让列宽总和顶到 viewport 边缘，
                # 竖线不许压到滚动条（控件右侧 13px 是滚动条的地盘）
                x = min(content_end, max(self.width() - 14, 0))
                self._place_edge_rule(self._edge_rule('_right_rule'), x, 0, 1, self.height())
                # 0 行时纵向表头宽度是 0 → 左边线整段消失，补一条；一旦有行号列，
                # 那条线由表头格子的 left border 画，再叠一条会把 15/255 的透明度加深
                if self.verticalHeader().width() > 0:
                    self._hide_edge_rule('_left_rule')
                else:
                    self._place_edge_rule(self._edge_rule('_left_rule'),
                                          0, 0, 1, self.height())
                # 底边线同样没有来源（外框已关、数据行不画边框）：空表时补一条让框闭合，
                # 有数据时撤掉 —— 「右下开口」只在有数据时才成立（用户要的是空表是个完整的框）
                if self.rowCount() > 0:
                    self._hide_edge_rule('_bottom_rule')
                else:
                    self._place_edge_rule(self._edge_rule('_bottom_rule'),
                                          0, self.height() - 1, x, 1)
            def _edge_rule(self, attr):
                """按属性名取/建一条边线子控件（同一张表上同时存在右、左、底三条）"""
                rule = getattr(self, attr, None)
                if rule is None:
                    rule = _TableEdgeRule(self)
                    setattr(self, attr, rule)
                return rule
            def _hide_edge_rule(self, attr):
                rule = getattr(self, attr, None)
                if rule is not None:
                    rule.hide()
            def _place_edge_rule(self, rule, x, y, w, h):
                # 只在位置真的变了时改几何：updateGeometries 每次滚动/重算都会调进来，
                # 无脑 setGeometry 会白白触发一次子控件重排
                if (rule.x(), rule.y(), rule.width(), rule.height()) != (x, y, w, h):
                    rule.setGeometry(x, y, w, h)
                # 窗口已经显示之后再动态 new 的子控件默认是隐藏的，得显式 show
                if not rule.isVisible():
                    rule.show()
                rule.raise_()
        self.format_table = _FormatTable()
        self.format_table.setColumnCount(6)
        self.format_table.setHorizontalHeaderLabels(
            ["选择", "ID", "类型", "质量", "编码", "语言"])
        self.format_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.format_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.format_table.setAlternatingRowColors(True)
        # 单元格文案一律单行省略号、不按词换行：ID（`hls-audio-128000-Audio`）与
        # 质量（`한국어 - original (original)`）这类长文案若允许换行，行高会被撑成
        # 两行、表格参差不齐；全文看 tooltip（决策 59 第二轮配平）
        self.format_table.setWordWrap(False)
        self.format_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        # 右下开口 + 右边线落在列尾（决策 56）：qfw 默认 `QTableView{border: none}`，
        # 表头格子自带的 1px 边线横向连成上边、行号列各格的 left border 连成左边；右/下
        # 两段原本靠 setBorderVisible(True) 的整条外框补（决策 55），但外框贴着控件四周
        # 画，会把右侧留给滚动条的 24px 留白一起圈进去、底边也随之闭合。这里显式关掉外框：
        # 右边线由 _TableEdgeRule 画在列尾；底边只在空表时补（那时是个完整的框），
        # 一有数据就撤掉、恢复右下开口（位置都在 _distribute_columns 里同步）。
        self.format_table.setBorderVisible(False)
        self.format_table.setMinimumHeight(150)
        self._fix_format_table_columns()
        # 同类型互斥勾选：勾选某项时自动取消同类型其他已勾项（"其他"类型不限）
        self.format_table.itemChanged.connect(self._on_format_item_changed)
        table_layout.addWidget(self.format_table, stretch=1)

        # 下载按钮
        self.download_btn = qfw.PrimaryPushButton("▶ 下载并合并选中格式")
        self.download_btn.setFixedWidth(240)
        self.download_btn.setEnabled(False)  # 嗅探完毕且有可用格式后才可点击
        self.download_btn.clicked.connect(self._download_current_content)
        # 右距 24 与默认视图的按钮行同口径（决策 54）：表格容器已铺满到窗口右缘，
        # 不在这一行补回 24px，按钮会比默认视图的「▶ 下载」右移 12px
        table_btn_row = QHBoxLayout()
        table_btn_row.setContentsMargins(0, 0, self.OPTION_MARGIN_RIGHT, 0)
        table_btn_row.addWidget(self.download_btn,
                                alignment=Qt.AlignmentFlag.AlignCenter)
        table_layout.addLayout(table_btn_row)

        right_layout.addWidget(self.default_view, stretch=1)
        right_layout.addWidget(self.table_view, stretch=1)
        self.table_view.setVisible(False)
        self.subtitle_panel = SubtitlePanel(right_margin=self.OPTION_MARGIN_RIGHT)
        self.subtitle_panel.download_requested.connect(self._download_current_content)
        self.subtitle_panel.comments_requested.connect(self._download_comments)
        right_layout.addWidget(self.subtitle_panel, stretch=1)
        self.subtitle_panel.hide()
        self.url_input.textChanged.connect(self._on_url_changed)

        # 组装 splitter
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 2)   # 左面板占比 2
        splitter.setStretchFactor(1, 4)   # 右面板占比 4（给表格更多空间）
        splitter.setMinimumHeight(250)
        layout.addWidget(splitter, stretch=1)

        # 日志区域
        self._setup_log_area(layout)

        # 监听队列下载失败，弹常显错误提示（需手动关闭）
        from sookit.core.task_queue import TaskQueueManager
        TaskQueueManager.instance().task_failed.connect(self._on_queue_task_failed)

        # 恢复上次使用的视图（决策 47）；恢复过程不落盘（currentItemChanged 会触发
        # _on_view_changed，其内部只在用户交互时保存，见该方法说明）
        self._restoring_view = True
        try:
            saved_view = load_sniff_view()
            self._apply_view(saved_view)
        finally:
            self._restoring_view = False

    # -------- 内容 / 音视频视图切换 --------
    def _on_view_changed(self, key):
        if key not in (SNIFF_VIEW_DEFAULT, SNIFF_VIEW_TABLE):
            return
        self._apply_view(key)
        if not self._restoring_view:
            save_sniff_view(key)

    def _toggle_media_view(self):
        self._on_view_changed(SNIFF_VIEW_TABLE if self._media_view == SNIFF_VIEW_DEFAULT
                              else SNIFF_VIEW_DEFAULT)

    def _apply_view(self, key):
        """视图偏好只属于音视频；切到字幕时保留档位和表格勾选。"""
        self._media_view = key
        self._on_content_changed(self._content_tab)

    def _on_content_changed(self, key):
        if key not in ('media', 'subtitles'):
            return
        self._content_tab = key
        media = key == 'media'
        self.default_view.setVisible(media and self._media_view == SNIFF_VIEW_DEFAULT)
        self.table_view.setVisible(media and self._media_view == SNIFF_VIEW_TABLE)
        self.subtitle_panel.setVisible(not media)
        self.view_button.setVisible(media)
        self.view_button.setText("表格视图" if self._media_view == SNIFF_VIEW_DEFAULT else "默认视图")

    def _invalidate_subtitles(self):
        self._subtitle_url = ''
        self._comment_tracks = []
        self.subtitle_panel.clear()
        self.content_segment.setCurrentItem('media')
        self.content_segment.hide()
        self._on_content_changed('media')

    def _on_url_changed(self, text):
        # 输入新链接不改写旧结果的归属；主动清空则连在途结果一起作废。
        if text.strip():
            return
        self._sniff_seq += 1
        self._invalidate_subtitles()
        if self._sniff_worker is not None and self._sniff_worker.isRunning():
            self._sniff_worker.request_stop()
        if hasattr(self, '_sniff_timer') and self._sniff_timer:
            self._sniff_timer.stop()
        self._reset_sniff_btn()

    def _download_current_content(self):
        if self._content_tab == 'subtitles':
            tracks = self.subtitle_panel.selected_tracks()
            formats = self.subtitle_panel.selected_formats()
            if self._subtitle_url and tracks and formats:
                self._queue_subtitle_task(download_subtitles, (tracks, formats), '字幕下载')
        elif self._media_view == SNIFF_VIEW_TABLE:
            self.do_download()
        else:
            self.do_download_default()

    def _download_comments(self):
        if not self._subtitle_url or not self._comment_tracks:
            return
        # 对话框打开后也须核对代际，避免嵌套事件循环收到新的嗅探结果。
        seq = self._sniff_seq
        dialog = CommentDownloadDialog(self._comment_tracks, self.window())
        try:
            if dialog.exec() and seq == self._sniff_seq and self._subtitle_url:
                track = dialog.selected_track()
                if track:
                    self._queue_subtitle_task(download_comments, (track,), '实时评论下载')
        finally:
            dialog.deleteLater()

    def _queue_subtitle_task(self, func, selection_args, label):
        url = self._subtitle_url
        if not url:
            return
        out_dir = self.read_path(self.out_dir) or DEFAULT_OUTPUT_DIR
        title = self._sniff_title or url
        metadata = {
            'filename': title, 'title': title, 'channel': self._sniff_channel,
            'duration': self._sniff_duration, 'url': url,
            'out': out_dir, 'out_dir': out_dir,
            'cover_url': self._sniff_thumbs[0]['url'] if self._sniff_thumbs else '',
        }
        if self._cover_data and self._cover_data_seq == self._sniff_seq:
            metadata['cover_data'] = self._cover_data
        self.run_queued_task(
            func=func, args=(url, out_dir, *selection_args),
            task_type=TaskType.YTDLP, title=f"{label} - {title}",
            metadata=metadata, kwargs={'remote': self.remote_cb.isChecked()})
        show_infobar(self, "info", title="任务已加入队列", content="", duration=3000)

    def refresh_ytdlp_status(self):
        """yt-dlp 装好后重新检测：若已可用则关闭「未找到 yt-dlp」提示"""
        if self._ytdlp_warning_bar is not None and is_ytdlp_available():
            try:
                self._ytdlp_warning_bar.close()
            except Exception:
                pass
            self._ytdlp_warning_bar = None

    def _on_queue_task_failed(self, task):
        """队列下载失败 → 弹常显错误提示（不自动消失，需手动关闭）。

        只处理**本页发起**的任务：task_failed 是全局信号，此前无过滤导致
        别的页面（如动图下载）的任务失败也在这里弹条。
        """
        if task.task_id not in self._owned_task_ids:
            return
        title = task.title or "下载任务失败"
        content = task.error or "任务执行失败，请查看日志"
        if len(content) > 200:
            content = content[:200] + "…"
        show_infobar(self, "error", title=title, content=content)

    # -------- 嗅探 --------
    def sniff_from_browser(self, url):
        """外部请求不打断已有嗅探，也不显示或激活窗口（决策 62）。"""
        from sookit.core.browser_protocol import normalize_video_url
        url = normalize_video_url(url)
        timer = getattr(self, '_sniff_timer', None)
        if ((timer is not None and timer.isActive())
                or (self._sniff_worker is not None and self._sniff_worker.isRunning())):
            return {'ok': False, 'error': 'Sookit 正在嗅探其他请求，请完成后再试'}
        if not is_ytdlp_available():
            return {'ok': False, 'error': 'Sookit 尚未安装 yt-dlp，请到设置页下载安装'}
        self.url_input.setText(url)
        self.do_sniff()
        return {'ok': True, 'message': 'Sookit 已在后台开始嗅探'}

    def download_from_browser(self, url, action):
        """浏览器下载使用自己的 URL 和任务参数，不读取当前嗅探结果。"""
        from sookit.core.browser_protocol import normalize_video_url
        from sookit.core.browser_downloads import BEST_VIDEO_FORMAT, download_best_cover
        url = normalize_video_url(url)
        if action not in ('download_best', 'download_cover'):
            return {'ok': False, 'error': '不支持的浏览器下载请求'}
        if not is_ytdlp_available():
            return {'ok': False, 'error': 'Sookit 尚未安装 yt-dlp，请到设置页下载安装'}
        out_dir = self.read_path(self.out_dir) or DEFAULT_OUTPUT_DIR
        remote = self.remote_cb.isChecked()
        video_id = extract_youtube_id(url)
        label = '最佳画质下载' if action == 'download_best' else '封面下载'
        metadata = {
            'url': url, 'out_dir': out_dir, 'browser_action': action,
            'cover_url': build_thumbnails(video_id)[0]['url'],
        }
        if action == 'download_best':
            config = load_download_config()
            func = Functions.download_youtube
            args = (url, BEST_VIDEO_FORMAT, out_dir, remote,
                    config['concurrent_fragments'], config['use_aria2c'],
                    config['aria2c_connections'])
            kwargs = {'extra_args': ['--ignore-config', '--no-playlist']}
            metadata['format_spec'] = BEST_VIDEO_FORMAT
        else:
            func = download_best_cover
            args = (url, out_dir, remote)
            kwargs = None
        task_id = self.run_queued_task(
            func=func, args=args, task_type=TaskType.YTDLP,
            title=f'{label} - {video_id}', metadata=metadata, kwargs=kwargs)
        return {'ok': True, 'task_id': task_id,
                'message': f'{label}已加入 Sookit 任务队列'}

    def do_sniff(self):
        self._invalidate_subtitles()
        if hasattr(self, '_sniff_timer') and self._sniff_timer:
            self._sniff_timer.stop()
        url = self.url_input.text().strip()
        if not url:
            show_infobar(self, "warning", title="提示", content="请先输入视频链接",
                         duration=3000)
            return
        # 切换按钮：隐藏蓝色嗅探，显示灰色停止嗅探
        self.sniff_btn.setVisible(False)
        self.stop_sniff_btn.setVisible(True)
        self.format_table.setRowCount(0)
        self._formats_data = []
        self._clear_default_view()  # 上一部视频的档位不能留给新的
        self.download_btn.setEnabled(False)  # 新嗅探开始，重置按钮可用性
        self.default_download_btn.setEnabled(False)
        self._sniff_timed_out = False
        # 新一轮嗅探：递增代际并捕获本轮序号，上一轮（含用户已停止但仍在途）的结果一律作废
        self._sniff_seq += 1
        seq = self._sniff_seq
        self._last_sniff_url = url
        # 清空封面
        self.cover_label.clearPixmap("嗅探中...")
        self.cover_title.setText("")
        # 同步作废上一部视频的元数据：本方法上面已把封面下拉框/预览切到新链接（同步取
        # ID），但标题要等嗅探完成才有——若用户在这期间点「下载封面」，文件名会错用上一
        # 部视频的标题（实测：封面是新的、文件名是旧的）。与 _on_sniff_error 的清空口径
        # 一致：嗅探没成功就没有可信标题，宁可回退到当前链接的 ID。
        self._sniff_title = ''
        self._sniff_channel = ''
        self._sniff_duration = 0
        self._cover_data = None
        self._cover_data_seq = None
        self._sniff_thumbs = []

        # 立即从 URL 提取 video_id，同步加载封面
        video_id = extract_youtube_id(url)
        if video_id:
            self._sniff_video_id = video_id
            # 立即构建封面分辨率列表并填充下拉框
            thumbs = build_thumbnails(video_id)
            if thumbs:
                self._populate_cover_combo(thumbs)
                self.log(f"找到 {len(thumbs)} 个封面分辨率")
            # 立即启动封面加载（异步）
            self._load_cover_image(video_id)

        # 创建时就持有引用并连接 finished：替换时才连接会漏掉已经发出的信号。
        if self._sniff_worker is not None and self._sniff_worker.isRunning():
            self._sniff_worker.request_stop()
        worker = GenericWorker(Functions.sniff_youtube, args=(url,), kwargs={'log': None})
        self._threads.append(worker)
        worker.finished.connect(self._on_sniff_worker_finished, Qt.ConnectionType.QueuedConnection)
        self._sniff_worker = worker
        worker.done.connect(lambda info, s=seq, u=url: self._on_sniff_done(s, info, u))
        worker.error.connect(lambda msg, s=seq: self._on_sniff_error(s, msg))
        worker.start()
        self.log("▶ 正在嗅探视频信息...")
        self._sniff_timer = QTimer(self)
        self._sniff_timer.setSingleShot(True)
        self._sniff_timer.timeout.connect(lambda s=seq: self._sniff_timeout(s))
        self._sniff_timer.start(60000)

    @pyqtSlot()
    def _on_sniff_worker_finished(self):
        # 显式排队到页面所属的 GUI 线程；不捕获 worker，重复通知也可安全忽略。
        worker = self.sender()
        if worker in self._threads:
            self._threads.remove(worker)

    def stop_sniff(self):
        # 代际失效在最前面且无条件执行：worker 可能已经跑完并把结果排进了信号队列，
        # 此时 request_stop() 拦不住它，只能靠序号丢弃；同时也保证「按钮已是停止态、
        # worker 其实已结束」时 UI 能复位（否则只剩 _on_sniff_* 里的复位会因序号不符而跳过）
        self._sniff_seq += 1
        self._invalidate_subtitles()
        if hasattr(self, '_sniff_timer') and self._sniff_timer and self._sniff_timer.isActive():
            self._sniff_timer.stop()
        if self._sniff_worker is not None and self._sniff_worker.isRunning():
            self._sniff_worker.request_stop()
            self._sniff_worker.wait(1000)
        self._reset_sniff_btn()
        self.log("嗅探已停止")
        show_infobar(self, "info", title="已停止", content="嗅探已停止",
                     duration=3000)

    def _reset_sniff_btn(self):
        # 切换按钮：显示蓝色嗅探，隐藏灰色停止嗅探
        self.stop_sniff_btn.setVisible(False)
        self.sniff_btn.setVisible(True)

    def _on_sniff_done(self, seq, info, sniff_url=None):
        if seq != self._sniff_seq:
            return  # 旧回调不能复位新一轮嗅探的按钮或计时器
        if hasattr(self, '_sniff_timer') and self._sniff_timer and self._sniff_timer.isActive():
            self._sniff_timer.stop()
        self._reset_sniff_btn()
        if self._sniff_timed_out:
            return
        self._subtitle_url = sniff_url if sniff_url is not None else self._last_sniff_url
        tracks = info.get('subtitle_tracks') or []
        self._comment_tracks = [dict(track) for track in (info.get('comment_tracks') or [])]
        self.subtitle_panel.set_tracks(tracks, self._comment_tracks)
        self.content_segment.setVisible(bool(tracks or self._comment_tracks))

        # 保存嗅探数据（video_id 可能已在 do_sniff 中设置）
        # 仅 YouTube 赋值，避免非 YouTube 的站点 ID（如 B 站 BV 号）污染 _sniff_video_id
        # （其消费方：_download_cover 文件名兜底、入队 cover_url、_on_sniff_failed 兜底加载）
        if not self._sniff_video_id:
            if (info.get('extractor') or '').lower().startswith('youtube'):
                self._sniff_video_id = info.get('id', '') or extract_youtube_id(self._last_sniff_url)
        self._sniff_title = info.get('title', '')
        self._sniff_channel = info.get('channel', '')
        self._sniff_duration = info.get('duration', 0)

        # ---- 左面板：显示标题 + 频道/时长 ----
        self.cover_title.setText(info.get('title', ''))
        duration = info.get('duration', 0)
        dur_str = format_duration(duration) if duration else '未知'
        self.cover_meta.setText(f"频道: {info.get('channel', '未知')}    时长: {dur_str}")

        # 如果嗅探结果中有更准确的封面信息，更新封面分辨率下拉框
        thumbs = info.get('thumbnails', [])
        if thumbs:
            self._populate_cover_combo(thumbs)

        # 嗅探前未显示封面的场景（非 YouTube 嗅探前无从取 URL）：
        # 嗅探完成后加载最佳封面显示（YouTube 嗅探前已显示，此处自动跳过）。
        # 已有进行中的加载线程时不再并发补载：isNull 只反映显示状态，快速路径
        # 的成功信号可能还在主线程队列里，此时并发补载会在其成功后留下一个
        # 冗余下载，晚到的失败会把刚显示的封面清掉
        loader_busy = (self._cover_loader_worker is not None
                       and self._cover_loader_worker.isRunning())
        if thumbs and self.cover_label._pixmap.isNull() and not loader_busy:
            self._load_cover_from_url(thumbs[0]['url'])

        # 填充格式表格
        fmts = info.get('formats', [])
        if not fmts:
            self._formats_data = []
            self.format_table.setRowCount(0)
            self._clear_default_view()
            self.default_download_btn.setEnabled(False)
            self.log("该视频暂无可用下载格式，但仍可下载封面")
            show_infobar(self, "info", title="提示",
                         content="暂无可用下载格式，但仍可下载封面", duration=4000)
            notify_result_notice(
                "嗅探完成（无可用格式）", self._sniff_title,
                f"频道: {info.get('channel', '未知')} · 时长: {dur_str} · 该视频暂无可用下载格式")
            return

        sorted_fmts = sorted(fmts, key=self._format_sort_key)
        self._formats_data = sorted_fmts
        self.format_table.setRowCount(len(sorted_fmts))

        # 系统通知（决策 40 扩展）：主窗口不在焦点时告知嗅探结果（窗口在前台时只留页面内反馈）
        notify_result_notice(
            "嗅探成功", self._sniff_title,
            f"频道: {info.get('channel', '未知')} · 时长: {dur_str} · 可用格式 {len(sorted_fmts)} 个")

        # 预勾选：有"视频+音频"勾首行（即最佳 VA）；否则勾最佳"视频流"+"音频流"各一。
        # 排序保证每类型首次出现的行即该类型最佳项（视频流按分辨率、音频流按码率降序）。
        first_row = {}
        for row, fmt in enumerate(sorted_fmts):
            first_row.setdefault(fmt['type'], row)
        if '视频+音频' in first_row:
            precheck_rows = {first_row['视频+音频']}
        else:
            precheck_rows = {first_row[t] for t in ('视频流', '音频流') if t in first_row}

        # 填充期间屏蔽互斥处理（setItem/setCheckState 会触发 itemChanged）
        self._format_updating = True
        try:
            for row, fmt in enumerate(sorted_fmts):
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setCheckState(Qt.CheckState.Checked if row in precheck_rows else Qt.CheckState.Unchecked)
                self.format_table.setItem(row, 0, item)
                # ID 列（决策 59）：原样显示 yt-dlp 的 format_id —— 用户能直接和
                # `yt-dlp -F` 的输出对号，也是合并下载选择器的构成单元。长 ID
                # （如 hls-audio-128000-Audio）列宽不够会被省略号截断，挂 tooltip 兜底。
                id_item = QTableWidgetItem(fmt['format_id'])
                id_item.setToolTip(fmt['format_id'])
                self.format_table.setItem(row, 1, id_item)
                self.format_table.setItem(row, 2, QTableWidgetItem(fmt['type']))
                # 质量列偶尔会被长文案（格式无码率时退回 format_note，如
                # 「한국어 - original (original)」）撑爆，列宽放不下时省略号截断，
                # 挂 tooltip 让悬停能看到全文（决策 59 补记）
                quality_item = QTableWidgetItem(fmt['quality'])
                quality_item.setToolTip(fmt['quality'])
                self.format_table.setItem(row, 3, quality_item)
                codec = fmt.get('vcodec', '')
                if fmt['type'] == '音频流':
                    codec = fmt.get('acodec', '')
                elif fmt['type'] == '视频+音频':
                    v = fmt.get('vcodec', '')
                    a = fmt.get('acodec', '')
                    codec = f"{v}+{a}" if v and a else (v or a)
                # yt-dlp 没报编码（字段缺失）：有音视频却空着会被当成填充失败，
                # 显式写「未知」；「其他」行（storyboard 等）本来就无编码，保持空。
                if not codec and fmt['type'] != '其他':
                    codec = '未知'
                self.format_table.setItem(row, 4, QTableWidgetItem(codec))
                lang = fmt.get('language', '')
                self.format_table.setItem(row, 5, QTableWidgetItem(lang))
        finally:
            self._format_updating = False

        # 填充完成后强制恢复列宽（Fixed 模式，防止勾选框撑宽"选择"列）
        self._fix_format_table_columns()

        # 默认视图档位（决策 47）：与表格同一份嗅探结果，各自渲染
        self._populate_default_view(sorted_fmts)

        self.download_btn.setEnabled(True)
        self.log(f"嗅探完成，共 {len(fmts)} 个格式")
        show_infobar(self, "success", title="完成",
                     content=f"找到 {len(fmts)} 个可用格式", duration=3000)

    @staticmethod
    def _parse_quality_value(q: str) -> int:
        """从 quality 字符串提取数值用于降序排序"""
        if not q:
            return 0
        q = q.strip().lower()
        # 分辨率 "1920x1080" → 取高度
        m = re.search(r'(\d+)\s*x\s*(\d+)', q)
        if m:
            return int(m.group(2))
        # "1080p", "2160p" 等
        m = re.search(r'(\d+)\s*p', q)
        if m:
            return int(m.group(1))
        # "4k", "2k" 等（值≤16视为分辨率k）
        m = re.search(r'(\d+)\s*k', q)
        if m:
            val = int(m.group(1))
            if val <= 16:
                return val * 540  # 4k→2160, 2k→1080
            else:
                return val  # 码率如 320k, 128k
        # 纯数字
        m = re.search(r'(\d+)', q)
        if m:
            return int(m.group(1))
        return 0

    def _format_sort_key(self, fmt):
        """排序：视频+音频(高→低分辨率) → 音频流(高→低码率) → 视频流(高→低分辨率) → 其他"""
        t = fmt['type']
        if FormatType.VIDEO_AUDIO in t:
            cat = 0
        elif t == FormatType.AUDIO_ONLY:
            cat = 1
        elif t == FormatType.VIDEO_ONLY:
            cat = 2
        else:
            cat = 3
        return (cat, -self._parse_quality_value(fmt['quality']))

    def _on_sniff_error(self, seq, err_msg):
        if seq != self._sniff_seq:
            return
        if hasattr(self, '_sniff_timer') and self._sniff_timer and self._sniff_timer.isActive():
            self._sniff_timer.stop()
        self._reset_sniff_btn()
        if self._sniff_timed_out:
            return
        self._invalidate_subtitles()
        self.log(f"嗅探失败: {err_msg}")
        # 嗅探失败没有可信格式，默认视图档位一并清空（表格同上）
        self._clear_default_view()
        self.default_download_btn.setEnabled(False)
        # 系统通知（决策 40 扩展）：失败时无条件通知（页面内 InfoBar 只在部分分支出现）
        notify_result_notice("嗅探失败", self._last_sniff_url, err_msg)
        # video_id 可能已在 do_sniff 中设置
        if not self._sniff_video_id:
            self._sniff_video_id = extract_youtube_id(self._last_sniff_url)
        self._sniff_title = ''
        self.cover_title.setText("封面下载（嗅探失败）")
        self.cover_meta.setText("视频信息获取失败，但仍可尝试下载封面")
        # 如果封面还未加载，尝试加载：只认「当前链接自己的 ID」
        # - 判空必须用 isNull()：PyQt6 的 QPixmap 未定义 __bool__，`not <QPixmap>` 恒为 False，
        #   本分支曾因此与下面的失败提示一起从未执行（见决策 40 追加段）
        # - 不能直接用 _sniff_video_id：它跨次嗅探保留且只在 YouTube 站点赋值，换到非 YouTube
        #   链接后仍指向上一个视频，用它补封面会加载错视频的封面（决策 35 同类问题）
        current_id = extract_youtube_id(self._last_sniff_url)
        if current_id and self.cover_label._pixmap.isNull():
            self._load_cover_image(current_id)
            self.log("已从 URL 提取封面选项，可尝试下载封面")
        # 失败提示与「补封面」无关：必须放在 if 之外，任何失败路径都要弹
        show_infobar(self, "error", title="嗅探失败", content=err_msg)

    def _sniff_timeout(self, seq=None):
        if seq is not None and seq != self._sniff_seq:
            return
        if self._sniff_worker and self._sniff_worker.isRunning():
            self._sniff_timed_out = True
            self._invalidate_subtitles()
            # 超时沿用 _sniff_timed_out 守卫而不递增代际：超时自身已给出反馈，
            # 随后到达的结果由该标记丢弃即可（与 stop_sniff 的序号机制分工不同）
            # 先尝试安全退出
            self._sniff_worker.request_stop()
            # 等待 2 秒，如果仍未退出则强制终止
            if not self._sniff_worker.wait(2000):
                self._sniff_worker.terminate()
            self._reset_sniff_btn()
            self.log("嗅探超时（60秒），请检查网络或链接是否有效")
            show_infobar(self, "error", title="超时", content="嗅探超时，请检查网络或链接")
            # 系统通知（决策 40 扩展）：超时归入失败类；_sniff_timed_out 守卫保证随后的
            # error 回调提前 return，同一次嗅探只会弹这一条
            notify_result_notice("嗅探超时", self._last_sniff_url,
                                 "60 秒内未返回视频信息，请检查网络或链接是否有效")

    # -------- 封面加载（异步下载并在左侧显示）--------
    def _load_cover_image(self, video_id):
        """后台下载 maxresdefault 封面并显示在左侧"""
        if not video_id:
            return
        thumbs = build_thumbnails(video_id)
        if not thumbs:
            return
        # 取最大分辨率封面
        cover_url = thumbs[0]['url']

        class CoverLoader(QThread):
            loaded = pyqtSignal(bytes)
            failed = pyqtSignal()

            def run(self):
                try:
                    req = urllib.request.Request(
                        cover_url,
                        headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=15, context=get_certifi_ssl_context()) as resp:
                        data = resp.read()
                    if data:
                        self.loaded.emit(data)
                    else:
                        self.failed.emit()
                except Exception:
                    self.failed.emit()

        worker = CoverLoader()
        worker._sniff_seq = self._sniff_seq
        # 带身份转发：过期 worker（已被新 loader 取代）的晚到结果直接丢弃，
        # 避免晚到的失败清掉新 loader 刚成功显示的封面（跨嗅探同理）
        worker.loaded.connect(lambda d, w=worker: self._on_cover_loaded(d, w))
        worker.failed.connect(lambda w=worker: self._on_cover_failed(w))
        worker.finished.connect(lambda w=worker: self._on_loader_finished(w))
        self._cover_loader_worker = worker
        worker.start()

    def _on_loader_finished(self, worker):
        """封面加载线程结束：仍是当前 worker 才清引用（供补载 busy 判断、防 GC）"""
        if self._cover_loader_worker is worker:
            self._cover_loader_worker = None

    def _on_cover_loaded(self, data, worker=None):
        """封面加载完成 - 保存数据并显示（过期 worker 的结果直接丢弃）"""
        if worker is not None and worker is not self._cover_loader_worker:
            return
        self._cover_data = data  # 保存供任务队列复用
        self._cover_data_seq = getattr(worker, '_sniff_seq', self._sniff_seq)
        pixmap = QPixmap()
        if pixmap.loadFromData(QByteArray(data)):
            # 交给 CoverImageWidget 的 paintEvent 按当前尺寸缩放绘制，无黑边
            self.cover_label.setPixmap(pixmap)
        else:
            self._cover_data = None
            self.cover_label.clearPixmap("封面加载失败")

    def _on_cover_failed(self, worker=None):
        """封面加载失败（过期 worker 的结果直接丢弃）"""
        if worker is not None and worker is not self._cover_loader_worker:
            return
        self._cover_data = None
        self.cover_label.clearPixmap("封面加载失败")
        self._maybe_start_cover_fallback()

    def _maybe_start_cover_fallback(self):
        """最终兜底：常规加载路径全部失败后，落盘下载最佳封面并显示。

        条件：嗅探到了封面列表、无进行中的兜底、同 URL 会话内未试过
        （防循环；失败 URL 不反复重试，跨嗅探保留）。
        """
        if not self._sniff_thumbs or self._cover_fallback_worker is not None:
            return
        best_url = self._sniff_thumbs[0]['url']
        if best_url in self._fallback_tried_urls:
            return
        self._fallback_tried_urls.add(best_url)
        self.log("常规封面加载失败，尝试最终兜底（落盘下载 + 转码）...")
        worker = self.CoverFallbackWorker(best_url)
        worker._sniff_seq = self._sniff_seq
        worker.resolved.connect(
            lambda data, msg, w=worker: self._on_cover_fallback_resolved(data, msg, w))
        worker.finished.connect(lambda w=worker: self._on_fallback_finished(w))
        self._cover_fallback_worker = worker
        worker.start()

    def _on_cover_fallback_resolved(self, data, msg, worker):
        """兜底结果：成功 → 显示并存 _cover_data；失败 → 保持占位并日志原因"""
        if self._cover_fallback_worker is not worker:
            return
        self._cover_fallback_worker = None
        if data:
            pixmap = QPixmap()
            if pixmap.loadFromData(QByteArray(data)):
                self._cover_data = data  # 供任务队列复用
                self._cover_data_seq = worker._sniff_seq
                self.cover_label.setPixmap(pixmap)
                self.log(f"封面兜底成功: {msg}")
                return
            msg = f"兜底字节无法解码: {msg}"
        self.cover_label.clearPixmap("封面加载失败")
        self.log(f"封面兜底失败: {msg}")

    def _on_fallback_finished(self, worker):
        """兜底线程结束：仍是当前 worker 才清引用（防 GC）"""
        if self._cover_fallback_worker is worker:
            self._cover_fallback_worker = None

    def _load_cover_from_url(self, cover_url):
        """按 URL 后台下载封面并显示（非 YouTube 站点嗅探后调用）。

        与 _load_cover_image 内的 CoverLoader 逻辑一致，但以 URL 为入参，
        不依赖 YouTube 视频 ID；_on_cover_loaded 会存 _cover_data 供任务队列复用。
        """
        if not cover_url:
            return

        class UrlCoverLoader(QThread):
            loaded = pyqtSignal(bytes)
            failed = pyqtSignal()

            def run(self):
                try:
                    req = urllib.request.Request(
                        cover_url,
                        headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=15, context=get_certifi_ssl_context()) as resp:
                        data = resp.read()
                    if data:
                        self.loaded.emit(data)
                    else:
                        self.failed.emit()
                except Exception:
                    self.failed.emit()

        worker = UrlCoverLoader()
        worker._sniff_seq = self._sniff_seq
        worker.loaded.connect(lambda d, w=worker: self._on_cover_loaded(d, w))
        worker.failed.connect(lambda w=worker: self._on_cover_failed(w))
        worker.finished.connect(lambda w=worker: self._on_loader_finished(w))
        self._cover_loader_worker = worker
        worker.start()

    # -------- 封面下载 Worker（独立 QThread，支持并行）--------
    class CoverFallbackWorker(QThread):
        """最终兜底 worker：常规 loader 全部失败后启动。两级链路：

        0. 缓存命中（遍历 fallback_{hash}.*，jpg/webp/png 均可）
        1. 落盘下载（Functions.download_thumbnail，即实测"能下载成功"的同款实现）
           → QImage 直接解码（覆盖网络抖动型失败）；**下载失败（防盗链 403）不中断**
        2. yt-dlp --write-thumbnail（防盗链兜底：yt-dlp 自带 extractor 的
           headers/cookies；--socket-timeout/--retries 保证进程时长有界）
           → 遍历产物 → QImage 解码。

        解码失败（如 AVIF 等 Qt 不支持的格式）统一放弃：保持占位 + 日志报
        魔数检测的实际格式。resolved(bytes|None, msg)。
        注意：线程内用 QImage 判断解码（可跨线程），QPixmap 仅主线程。
        """

        resolved = pyqtSignal(object, str)

        def __init__(self, url):
            super().__init__()
            self.url = url

        def run(self):
            import hashlib
            try:
                cache_dir = get_cover_dir()
                os.makedirs(cache_dir, exist_ok=True)
                name = hashlib.sha1(self.url.encode('utf-8')).hexdigest()[:16]
                raw_path = os.path.join(str(cache_dir), f"fallback_{name}.img")

                # 0. 缓存命中：遍历上次兜底产物（jpg/webp/png 均可，排除 .img）
                hit = _find_fallback_product(cache_dir, name)
                if hit:
                    with open(hit, 'rb') as f:
                        self.resolved.emit(f.read(), "缓存命中，加载成功")
                    return

                # 1. 落盘下载（与"下载封面"按钮同款实现）；防盗链 403 等失败
                #    不中断，落到 yt-dlp 兜底（yt-dlp 自带 extractor headers）。
                #    .img 为纯中间文件：不参与缓存命中，残留由启动清理兜底。
                data = None
                try:
                    Functions.download_thumbnail(self.url, raw_path)
                    if not QImage(raw_path).isNull():
                        with open(raw_path, 'rb') as f:
                            data = f.read()
                except Exception:
                    data = None

                if data:
                    self.resolved.emit(data, "落盘后直接加载成功")
                    return

                # 2. yt-dlp --write-thumbnail（防盗链兜底，最后一级）
                #    --socket-timeout/--retries 保证进程时长有界（约 1~2 分钟内）
                try:
                    template = os.path.join(str(cache_dir), f"fallback_{name}.%(ext)s")
                    cmd = build_ytdlp_cmd(
                        '--skip-download', '--no-playlist', '--no-warnings',
                        '--write-thumbnail', '--socket-timeout', '20', '--retries', '1',
                        '-o', template, self.url)
                    run_ytdlp(cmd, None)
                except Exception as e:
                    self.resolved.emit(None, f"yt-dlp 兜底失败: {e}")
                    return
                hit = _find_fallback_product(cache_dir, name)
                if hit:
                    with open(hit, 'rb') as f:
                        self.resolved.emit(f.read(), "yt-dlp 兜底加载成功")
                    return
                # 无可解产物：若有产物存在（可能格式 Qt 不支持，如 AVIF），日志报实际格式
                any_prod = [p for p in glob.glob(os.path.join(str(cache_dir), f"fallback_{name}.*"))
                            if not p.endswith('.img')]
                if any_prod:
                    self.resolved.emit(None, f"yt-dlp 产物无法解码（{_sniff_image_format(any_prod[0])}）")
                    return
                self.resolved.emit(None, "yt-dlp 兜底未产出可用封面")
            except Exception as e:
                self.resolved.emit(None, f"兜底失败: {e}")

    class CoverDownloadWorker(QThread):
        """封面下载专用线程，与视频下载并行执行"""
        log_signal = pyqtSignal(str)
        finished = pyqtSignal(bool)

        def __init__(self, url, path):
            super().__init__()
            self.url = url
            self.path = path

        def run(self):
            try:
                Functions.download_thumbnail(self.url, self.path, log=self.log_signal.emit)
                self.finished.emit(True)
            except Exception as e:
                self.log_signal.emit(f"封面下载失败: {e}")
                self.finished.emit(False)

    # -------- 封面下载（保存到本地）--------
    def _populate_cover_combo(self, thumbs):
        self.cover_combo.clear()
        if thumbs:
            # 保存列表供入队 cover_url 复用（YouTube = 硬编码 5 档，非 YouTube = yt-dlp 通用列表）
            self._sniff_thumbs = thumbs
            for i, t in enumerate(thumbs):
                w = t.get('width', 0) or 0
                h = t.get('height', 0) or 0
                tid = t.get('id', '') or ''
                if w and h:
                    # id 为非纯数字（如 maxresdefault）时附加标注；纯数字/缺失（通用列表）则只显示宽高
                    label = f"{w}x{h} ({tid})" if tid and not tid.isdigit() else f"{w}x{h}"
                elif i == 0:
                    label = "最佳分辨率"
                else:
                    label = f"备选分辨率 {i + 1}"
                # 注意：qfluentwidgets ComboBox.addItem(text, icon=None, userData=None)，
                # URL 必须以 userData 关键字传入（位置传参会落进 icon 形参导致 currentData() 恒为 None）
                self.cover_combo.addItem(label, userData=t['url'])
            self.cover_combo.setEnabled(True)
            self.cover_btn.setEnabled(True)
            self.log(f"找到 {len(thumbs)} 个封面分辨率")
        else:
            # 重置残留，避免上一视频的封面列表串入当前无封面场景
            self._sniff_thumbs = []
            self.cover_combo.addItem("无可用封面")
            self.cover_combo.setEnabled(False)
            self.cover_btn.setEnabled(False)

    def _download_cover(self):
        video_id = self._sniff_video_id or extract_youtube_id(self.url_input.text().strip())
        # 直接取下拉框当前项的 URL（userData），与显示内容天然一致；
        # 不按索引重建列表（通用列表项数与 build_thumbnails 不同，重建会索引错位）
        cover_url = self.cover_combo.currentData()
        if not cover_url:
            msg = "请先选择一个封面分辨率" if video_id else "该链接不支持封面获取"
            show_infobar(self, "warning", title="提示", content=msg, duration=3000)
            return

        out_dir = self.read_path(self.out_dir) or DEFAULT_OUTPUT_DIR
        # 扩展名按封面 URL 推断（白名单外默认 .jpg），通用站点封面可能是 png/webp/avif
        ext = os.path.splitext(urlparse(cover_url).path)[1].lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.avif'):
            ext = '.jpg'
        if self._sniff_title:
            safe_base = "".join(c for c in self._sniff_title if c.isalnum() or c in ' _-.,()[]').strip()
            filename = f"{safe_base or video_id or 'cover'}_cover{ext}"
        else:
            filename = f"{video_id or 'cover'}_cover{ext}"

        path = os.path.join(out_dir, filename)
        if os.path.exists(path):
            counter = 1
            while True:
                name, ext = os.path.splitext(filename)
                new_path = os.path.join(out_dir, f"{name}_{counter}{ext}")
                if not os.path.exists(new_path):
                    path = new_path
                    break
                counter += 1

        # 使用独立 QThread 下载封面，与视频下载并行
        worker = self.CoverDownloadWorker(cover_url, path)
        worker.log_signal.connect(self.log)
        worker.finished.connect(lambda ok: self._on_cover_download_done(ok, path))
        self._cover_download_worker = worker  # 保存引用防止 GC
        worker.start()
        self.log(f"▶ 开始下载封面: {filename}")
        show_infobar(self, "info", title="开始下载封面",
                     content=f"保存到: {filename}", duration=3000)

    def _on_cover_download_done(self, success, path):
        """封面下载完成回调"""
        if success:
            # 实测分辨率弥补下拉框无宽高档位的信息缺失（QPixmap 读图头，开销可忽略）
            pixmap = QPixmap(path)
            size_info = f"（实际分辨率 {pixmap.width()}x{pixmap.height()}）" if not pixmap.isNull() else ""
            self.log(f"✓ 封面下载完成: {path}{size_info}")
            show_infobar(self, "success", title="封面下载完成",
                         content=f"已保存到: {os.path.basename(path)}", duration=3000)
        else:
            self.log("✗ 封面下载失败")
            show_infobar(self, "error", title="封面下载失败",
                         content="请检查网络连接或链接是否有效")

    # -------- 默认视图：档位填充 / 选择 / 下载（决策 47）--------
    def _clear_default_view(self):
        """清空档位区并复位选择状态"""
        self._view_options = {'video': [], 'audio': []}
        self._option_rows = []   # [(OptionRow, option), ...] 保持与布局一致的顺序
        self._option_flows = []  # [OptionFlowLayout, ...] 每个分区一个网格
        self._selected_option = None
        while self.option_layout.count() > 1:  # 末尾保留 stretch
            item = self.option_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        # 回到空态：列表收起、占位提示接管
        self.option_scroll.setVisible(False)
        self.option_empty_hint.setVisible(True)

    def _section_label(self, text):
        label = qfw.CaptionLabel(text)
        label.setStyleSheet("color: #6b6b6b; font-weight: 600; background: transparent;")
        return label

    def _make_option_grid(self):
        """一个分区的卡片网格：固定两列，卡片随窗口变窄（决策 47）"""
        holder = QWidget()
        holder.setStyleSheet("QWidget { background: transparent; }")
        flow = OptionFlowLayout(parent=holder)
        flow.setHorizontalSpacing(10)
        flow.setVerticalSpacing(8)
        # 不设卡片宽度上限：上限会让宽窗口下多出的宽度全堆到右侧（决策 51）
        # 流式布局按宽度决定几列，高度只能由 heightForWidth 算 → holder 必须开启
        policy = holder.sizePolicy()
        policy.setHeightForWidth(True)
        holder.setSizePolicy(policy)
        self._option_flows.append(flow)
        return holder, flow

    def _populate_default_view(self, formats):
        """按嗅探结果生成「视频画质 / 音频」两组档位卡片"""
        self._clear_default_view()
        options = build_view_options(formats)
        self._view_options = options
        if not options['video'] and not options['audio']:
            self.default_download_btn.setEnabled(False)
            return
        # 有档位了：收起空态占位，露出列表
        self.option_empty_hint.setVisible(False)
        self.option_scroll.setVisible(True)
        insert_at = 0
        for title, opts in (("视频画质", options['video']), ("音频", options['audio'])):
            if not opts:
                continue
            self.option_layout.insertWidget(insert_at, self._section_label(title))
            insert_at += 1
            holder, flow = self._make_option_grid()
            for opt in opts:
                row = OptionRow(opt['title'], opt['subtitle'], opt['short_subtitle'])
                # 卡片宽度由网格对半分（见 OptionFlowLayout），不设上限（决策 51）
                row.clicked.connect(lambda o=opt: self._select_option(o))
                self._option_rows.append((row, opt))
                flow.addWidget(row)
            self.option_layout.insertWidget(insert_at, holder)
            insert_at += 1
        # 预选最高画质档：与表格视图预勾最佳项的口径一致，用户想换再点
        if options['video']:
            self._select_option(options['video'][0])
        elif options['audio']:
            self._select_option(options['audio'][0])
        self.default_download_btn.setEnabled(True)

    def _select_option(self, option):
        """全局互斥单选：视频档与音频档共用一个选中项"""
        self._selected_option = option
        for row, opt in self._option_rows:
            row.set_selected(opt is option)

    def do_download_default(self):
        """默认视图下载：直接用当前档位的 yt-dlp 选择器发起任务"""
        option = self._selected_option
        if not option:
            show_infobar(self, "warning", title="提示", content="请先选择一个画质或音质",
                         duration=3000)
            return
        self._start_download(option['spec'])

    # -------- 固定表格列宽 --------
    def _fix_format_table_columns(self):
        """Fixed 模式 + 按比例分配填满，每次 resize 自动重算"""
        self.format_table._distribute_columns()

    # -------- 格式表同类型互斥勾选 --------
    def _on_format_item_changed(self, item):
        """勾选某项时自动取消同类型其他已勾项；"其他"类型不限制"""
        if self._format_updating or item.column() != 0:
            return
        if item.checkState() != Qt.CheckState.Checked:
            return
        row = item.row()
        if row >= len(self._formats_data):
            return
        fmt_type = self._formats_data[row].get('type', '')
        if fmt_type not in (FormatType.VIDEO_AUDIO, FormatType.AUDIO_ONLY, FormatType.VIDEO_ONLY):
            return
        self._format_updating = True
        try:
            for r in range(self.format_table.rowCount()):
                if r == row:
                    continue
                other = self.format_table.item(r, 0)
                if (other and other.checkState() == Qt.CheckState.Checked
                        and r < len(self._formats_data)
                        and self._formats_data[r].get('type') == fmt_type):
                    other.setCheckState(Qt.CheckState.Unchecked)
        finally:
            self._format_updating = False

    # -------- 格式下载 --------
    def do_download(self):
        checked_fmts = []
        for row in range(self.format_table.rowCount()):
            item = self.format_table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                if row < len(self._formats_data):
                    checked_fmts.append(self._formats_data[row])

        if not checked_fmts:
            show_infobar(self, "warning", title="提示", content="请至少勾选一个格式",
                         duration=3000)
            return

        # 合并落点预测（决策 38）：非标编码组合会兜底合并为 mkv，而 hlsnative 直拼
        # 产物合 mkv 必失败（决策 33），弹窗引导改选标准组合，用户可选择强行继续
        if self._predict_mkv_merge(checked_fmts):
            # 文案手动预折行：qfw MessageBox 宽度取未折行文本的 sizeHint，
            # 不预折行会撑得极宽（实测 ~700px）
            box = qfw.MessageBox(
                "非标准编码组合",
                "当前勾选的「视频流 + 音频流」编码没有共同的标准容器\n"
                "可能导致下载失败\n"
                "\n"
                "建议改选：avc1/hevc/av01 + mp4a（合并为 MP4）\n"
                "或 vp9/av01 + opus（合并为 WEBM）\n"
                "\n"
                "仍要下载，还是返回修改勾选？",
                self.window())
            box.yesButton.setText("仍要下载")
            box.cancelButton.setText("返回修改")
            if not box.exec():
                return

        # '+' 拼接交 yt-dlp：视频流+音频流自动用 ffmpeg 合并为一个有声视频
        self._start_download('+'.join(f['format_id'] for f in checked_fmts))

    # 合并落点判定用编码族表（决策 38）已抽到 core/sniff_options.py，
    # 与默认视图的容器推断共用一份口径（原类属性保留为别名，避免外部引用断掉）
    _WEBM_VIDEO_CODECS = WEBM_VIDEO_CODECS
    _WEBM_AUDIO_CODECS = WEBM_AUDIO_CODECS
    _MP4_VIDEO_CODECS = MP4_VIDEO_CODECS
    _MP4_AUDIO_CODECS = MP4_AUDIO_CODECS

    @classmethod
    def _predict_mkv_merge(cls, fmts):
        """预测勾选组合是否可能兜底合并为 mkv（决策 38）。

        仅当恰好「一个视频流 + 一个音频流」（存在合并环节）时判定；任一编码
        未知/缺失时不判定（放行，避免误报）。判定规则：视频/音频编码同属
        webm 族（vp8/vp9/av01 + opus/vorbis）或同属 mp4 族（avc1/hevc/av01 +
        mp4a/aac）视为标准组合；其余组合（含跨族）视为可能落 mkv。
        """
        videos = [f for f in fmts if f.get('type') == FormatType.VIDEO_ONLY]
        audios = [f for f in fmts if f.get('type') == FormatType.AUDIO_ONLY]
        if len(fmts) != 2 or len(videos) != 1 or len(audios) != 1:
            return False
        return not is_standard_pair(videos[0].get('vcodec'), audios[0].get('acodec'))

    def _start_download(self, format_spec):
        url = self.url_input.text().strip()
        out_dir = self.read_path(self.out_dir)
        if not out_dir:
            out_dir = DEFAULT_OUTPUT_DIR

        self.log(f"下载格式: {format_spec}")
        self.log(f"保存到: {out_dir}")

        # 加载下载配置
        download_config = load_download_config()
        concurrent_fragments = download_config['concurrent_fragments']
        use_aria2c = download_config['use_aria2c']
        aria2c_connections = download_config['aria2c_connections']

        remote = self.remote_cb.isChecked()
        # 构建任务元数据
        metadata = {
            'title': self._sniff_title,
            'channel': self._sniff_channel,
            'duration': self._sniff_duration,
            'cover_url': '',
            'url': url,
            'format_spec': format_spec,
            'out_dir': out_dir,
        }
        # 传递封面数据（避免队列中重复下载）
        # cover_url 优先用嗅探得到的封面列表首项（任意站点通用，非 YouTube 也可入队显示封面）；
        # 无列表时兜底用视频 ID 构建（YouTube 现状行为）
        video_id = self._sniff_video_id or extract_youtube_id(url)
        if self._sniff_thumbs:
            metadata['cover_url'] = self._sniff_thumbs[0]['url']
        elif video_id:
            thumbs = build_thumbnails(video_id)
            if thumbs:
                metadata['cover_url'] = thumbs[0]['url']
        if hasattr(self, '_cover_data') and self._cover_data:
            metadata['cover_data'] = self._cover_data
        # 添加到任务队列
        self.run_queued_task(
            func=Functions.download_youtube,
            args=(url, format_spec, out_dir, remote, concurrent_fragments,
                  use_aria2c, aria2c_connections),
            task_type=TaskType.YTDLP,
            title=self._sniff_title or f"视频下载 - {video_id}",
            metadata=metadata
        )
        show_infobar(self, "info", title="任务已加入队列", content="", duration=3000)