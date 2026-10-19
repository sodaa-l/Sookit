"""
X (Twitter) 动图下载 页面

输入 X 推文链接 → 点「解析」列出推文里的动图与普通视频（卡片网格，可勾选）→
下载选中项并转成 GIF。

条目类型（2026-10-19，见 docs/decisions/44-gif-page-video-items.md）：
- 动图显示「动图 N」、默认勾选，N 为原始条目序号；
- 普通视频显示「视频 1/2/...」（按视频出现顺序独立编号）、默认不勾选，
  勾选后点「下载」会先弹确认框，确认后才入队；
- 列表顺序固定为「动图在前、视频在后」，同一类型内保持原始条目次序。
输出 GIF 的文件名仍取 yt-dlp 下载到的 mp4 标题，与显示名无关。

版式（2026-10-13 重构）：标题/说明 → 参数卡（链接·输出目录·编码质量）→
选择区操作行（计数·全选动图·下载）→ 展示区。展示区是「空态占位 / 卡片网格」
二选一的堆叠槽位，也是页面唯一的伸缩项，故解析前后整页零位移。

质量可选四档预设（core/gif_utils.GifQuality），或「自定义…」——参数填在
浮层（_CustomParamsView）里，不插进页面布局，否则展开时会把卡片展示区挤没；
确认后质量行显示参数摘要与「调整参数」按钮（仅该档出现），点它可重开浮层。
视频条目沿用页面所选质量档，不特殊强制「最佳质量」。

解析只读元数据（yt-dlp -J），不下载媒体；条目序号（1-based，原始条目列表次序）
在下载时由 `download_and_convert_gif(..., selected_indexes=[...])`（关键字专用形参，
经任务队列 kwargs 通道传递）拼成 `--playlist-items` 回传，故选中的必须与 yt-dlp
看到的次序一致。
"""
import os
import re
import urllib.request

from PyQt6.QtCore import Qt, QThread, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QIntValidator, QDoubleValidator, QColor, QFont, QFontMetrics,
    QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QStackedWidget,
    QWidget, QFrame,
)

import qfluentwidgets as qfw
from qfluentwidgets import qconfig

from sookit.core.functions import (
    Functions, is_ytdlp_available, load_download_config,
    DEFAULT_OUTPUT_DIR,
)
from sookit.core.gif_utils import GifQuality, GifCustomParams
from sookit.core.task_queue import TaskQueueManager, TaskType
from sookit.core.utils import get_certifi_ssl_context, set_smooth_scroll_params
from sookit.core.workers import GenericWorker
from sookit.pages.base import PageBase
from sookit.widgets.infobar import show_infobar
from sookit.widgets.notifier import notify_result_notice
from sookit.widgets.task_card import RoundedCoverWidget

# 色数固定选项（GIF 调色板常用档位，含内核 COLOR_CANDIDATES）
_COLOR_OPTIONS = (256, 128, 64, 32, 24, 16)
# dither 显示文本 → 内核取值（paletteuse 算法）
_DITHER_LABELS = {
    "关闭（无抖动）": "none",
    "bayer（有序抖动）": "bayer",
    "floyd_steinberg（误差扩散）": "floyd_steinberg",
    "sierra2_4a（误差扩散）": "sierra2_4a",
}
# 质量下拉里的「自定义…」项（注意与内核取值 GifQuality.CUSTOM == "自定义" 不同：
# 下拉项带省略号表示“点了会弹浮层”，入队时另按 GifQuality.CUSTOM 传）
_CUSTOM_ITEM = "自定义…"


def _strip_media_index(url: str) -> str:
    """剥离 /video/N、/photo/N 路径后缀（口径与 Functions.download_and_convert_gif 一致）。

    解析同样要剥离，否则 yt-dlp 对 animated_gif 遇 /video/N 会报
    "Media #N is not a video"（见 DEVELOPMENT.md 决策 30）。
    """
    return re.sub(r'/(?:video|photo)/\d+', '', url)


def _download_cover_bytes(cover_url: str) -> bytes:
    """同步下载封面字节（仅在后台线程调用）。"""
    req = urllib.request.Request(cover_url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=15,
                                context=get_certifi_ssl_context()) as resp:
        return resp.read()


class _CoverLoader(QThread):
    """按条目序号逐个下载封面字节（串行，避免一次并发几十个连接）。"""

    loaded = pyqtSignal(int, bytes)   # (条目序号, 封面字节)
    failed = pyqtSignal(int)          # (条目序号)

    def __init__(self, items, parent=None):
        super().__init__(parent)
        self.items = list(items)
        self._stop = False

    def request_stop(self):
        self._stop = True

    def run(self):
        for item in self.items:
            if self._stop:
                return
            url = item.get('cover_url')
            if not url:
                self.failed.emit(item['index'])
                continue
            try:
                data = _download_cover_bytes(url)
            except Exception:
                if not self._stop:
                    self.failed.emit(item['index'])
                continue
            if self._stop:
                return
            if data:
                self.loaded.emit(item['index'], data)
            else:
                self.failed.emit(item['index'])


class _GifCover(RoundedCoverWidget):
    """动图卡封面：在封面自身这一层叠选中/悬停反馈。

    必须画在封面控件里而不是卡片上：子控件在父控件之后绘制，画在卡片
    `paintEvent` 里的蒙版会被封面整块盖掉（实测看不到任何选中反馈，决策 37）。

    选中态只有「右上角对勾 + 标题转主题色」——**不再叠主题色遮罩**（2026-10-13
    用户要求：遮罩会影响看动图本身）；悬停仍是轻微变暗（与已完成页同款反馈）。
    """

    RADIUS = 8                      # 与已完成页封面同款圆角

    def __init__(self, w, h, parent=None):
        super().__init__(w, h, parent)
        self._checked = False
        self._hovered = False

    def set_feedback(self, checked: bool, hovered: bool):
        if (checked, hovered) == (self._checked, self._hovered):
            return
        self._checked, self._hovered = checked, hovered
        self.update()

    def refresh_theme(self):
        """主题切换后强制重绘：徽章配色读 `qfw.isDarkTheme()`，状态位没变时
        `set_feedback` 会提前返回、留下上一个主题的颜色。"""
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not (self._checked or self._hovered):
            return

        rect = self.rect()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self._hovered:
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(rect), self.RADIUS, self.RADIUS)
            painter.setClipPath(clip)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 40))
            painter.drawRect(rect)

        if self._checked:
            self._draw_check_badge(painter, rect)

    def _draw_check_badge(self, painter, rect):
        """右上角圆形对勾：主题色圆底 + 勾线 + 1px 半透明描边（自绘，不依赖图标资源）。

        勾线颜色随主题：浅色主题的主题色是深青（#009faa），白勾对比强；深色主题的
        主题色是高亮青（#29f1ff，亮度 0.78），白勾几乎糊在圆底上，改用近黑色勾线
        （2026-10-19 用户口径：深色模式下勾要是黑色）。
        """
        radius = 11
        cx = rect.right() - 10 - radius
        cy = rect.top() + 10 + radius

        painter.setClipping(False)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(qfw.themeColor())
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

        ring_color = QColor(255, 255, 255, 150)
        mark_color = QColor(255, 255, 255)
        if qfw.isDarkTheme():
            # 深色下圆底本身很亮，白描边/白勾都失去对比，一并换成暗色系
            ring_color = QColor(0, 0, 0, 90)
            mark_color = QColor(26, 26, 26)

        # 半透明描边：浅色/同色系封面上把圆底和背景分开（描边压在填充之上，
        # 外沿比填充外扩 0.5px，视觉尺寸几乎不变；线宽 1px 再细就糊在抗锯齿里了）
        ring = QPen(ring_color)
        ring.setWidthF(1.0)
        painter.setPen(ring)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

        pen = QPen(mark_color)
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        mark = QPainterPath()
        mark.moveTo(cx - 5, cy)
        mark.lineTo(cx - 1.5, cy + 3.5)
        mark.lineTo(cx + 5, cy - 4)
        painter.drawPath(mark)


class GifItemCard(QWidget):
    """单个动图条目卡片：圆角封面 + 序号，点击整卡切换勾选。

    视觉口径对齐任务队列「已完成」卡片（`CompletedThumbnailCard` + `CoverAreaWidget`）：
    无卡片底色、16:9 圆角(8)封面、封面下文字。选中不再画主题色描边（原绿框），
    改为右上角主题色圆底白色对勾；悬停时封面轻微变暗。

    不显示时长：条目能进列表的前提是 `_entry_kind` 判定为动图或视频，
    动图 `duration in (None, 0)`（yt-dlp 不解码 animated_gif 的时长），
    所以「有时长才显示」的标签对动图永远不可达，2026-10-11 复核后移除；
    2026-10-19 起视频条目也进列表，但时长属于视频的常规信息、与「动图能否
    显示时长」是两件事，仍不在此处显示（见决策 44）。

    卡片必须 setFixedSize：AdaptiveFlowLayout 只用 sizeHint().height() 定高，
    且会按 setWidgetMinimumWidth/MaximumWidth 覆写宽度（见 DEVELOPMENT.md 决策 30）。
    """

    toggled = pyqtSignal()          # 勾选状态变化（页面据此刷新计数与按钮）

    CARD_W = 240
    COVER_H = 135                   # 240 * 9/16
    _MARGIN = 6                     # 与 CompletedThumbnailCard 同款内边距
    _GAP = 6
    _TITLE_FONT = QFont("Microsoft YaHei", 11)

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.item = item
        self.kind = item.get('kind', 'gif')     # 'gif' | 'video'（决策 44）
        # 默认勾选口径：动图全选（原口径不变），视频默认不选——视频转 GIF
        # 体积大、耗时长，必须是用户显式选择的动作。
        self._checked = self.kind != 'video'
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.cover = _GifCover(self.CARD_W - 2 * self._MARGIN, self.COVER_H)
        self.cover.setText("加载中...")
        # 封面是子控件，默认会吞掉点击；设穿透后点击落在卡片上统一处理勾选
        self.cover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        # 显示名：动图沿用「动图 N」（N 为原始条目序号）；视频按视频出现顺序
        # 显示「视频 1/2/...」（video_no 由 functions.fetch_gif_items 给出），
        # 这样同一条推文里动图、视频各自从 1 开始编号，视觉上更自然。
        if self.kind == 'video':
            self.title = QLabel(f"视频 {item.get('video_no') or item['index']}")
        else:
            self.title = QLabel(f"动图 {item['index']}")
        self.title.setFont(self._TITLE_FONT)
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(self._MARGIN, self._MARGIN,
                                  self._MARGIN, self._MARGIN)
        layout.setSpacing(self._GAP)
        layout.addWidget(self.cover)
        layout.addWidget(self.title)

        # 高度按控件自身字体度量（类属性 QFont 的全局度量可能与控件上下文差 1px）
        title_h = QFontMetrics(self.title.font()).height()
        self.setFixedSize(self.CARD_W,
                          self._MARGIN * 2 + self.COVER_H + self._GAP + title_h)

        qconfig.themeChanged.connect(self._refresh_colors)
        self._refresh_colors()

    def _refresh_colors(self, *_):
        """标题配色：选中 = 主题色，未选中 = 正文色（随主题切换刷新）。"""
        if self._checked:
            self.title.setStyleSheet(f"color: {qfw.themeColor().name()};")
        else:
            dark = qfw.isDarkTheme()
            self.title.setStyleSheet(f"color: {'white' if dark else 'black'};")
        self.cover.set_feedback(self._checked, self._hovered)
        self.cover.refresh_theme()

    def is_checked(self) -> bool:
        return self._checked

    def set_checked(self, checked: bool):
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self._refresh_colors()
        self.toggled.emit()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_checked(not self._checked)
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self._hovered = True
        self.cover.set_feedback(self._checked, True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.cover.set_feedback(self._checked, False)
        super().leaveEvent(event)


class _CustomParamsView(qfw.FlyoutViewBase):
    """「自定义编码参数」浮层内容。

    参数填在浮层里（不插进页面布局）：内联展开会挤掉下面的动图展示区。
    **没有确定/取消按钮**：关闭浮层（点框外任意位置）= 确定，故提示语要写清这一点。
    页面侧只读 `params()` 的返回值——宽度/帧率没填或非法时返回 (None, 原因)。
    """

    def __init__(self, params: GifCustomParams = None, parent=None):
        super().__init__(parent)
        self.setFixedWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        layout.addWidget(qfw.StrongBodyLabel("自定义编码参数"))
        hint = qfw.CaptionLabel(
            "填好后点击框外任意位置即生效；不约束体积，宽度超过源宽度时收敛到源宽度")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(10)
        form.setColumnStretch(1, 1)

        form.addWidget(qfw.BodyLabel("宽度 (px)"), 0, 0)
        self.width_edit = qfw.LineEdit()
        self.width_edit.setPlaceholderText("必填，如 480（高度按源宽高比自动计算）")
        self.width_edit.setValidator(QIntValidator(2, 99999, self))
        form.addWidget(self.width_edit, 0, 1)

        form.addWidget(qfw.BodyLabel("帧率上限"), 1, 0)
        self.fps_edit = qfw.LineEdit()
        self.fps_edit.setPlaceholderText("必填，如 30（源 ≤ 该值保持源帧率）")
        dv = QDoubleValidator(0.1, 240, 2, self)
        dv.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.fps_edit.setValidator(dv)
        form.addWidget(self.fps_edit, 1, 1)

        form.addWidget(qfw.BodyLabel("色数"), 2, 0)
        self.colors_combo = qfw.ComboBox()
        for c in _COLOR_OPTIONS:
            self.colors_combo.addItem(str(c))
        form.addWidget(self.colors_combo, 2, 1)

        form.addWidget(qfw.BodyLabel("dither"), 3, 0)
        self.dither_combo = qfw.ComboBox()
        for label in _DITHER_LABELS:
            # 注意：qfw ComboBox 的 userData 必须关键字传（位置传参会落进 icon 形参）
            self.dither_combo.addItem(label, userData=_DITHER_LABELS[label])
        form.addWidget(self.dither_combo, 3, 1)

        layout.addLayout(form)
        # 没有「确定/取消」按钮：关闭（点框外）即确认，故这里不排按钮行

        self._fill(params)

    def _fill(self, params: GifCustomParams = None):
        """有已确认参数则预填（便于微调/原样关掉），否则宽度与帧率留空由用户填。

        默认不预填 480/30：那是编造的默认值，会让用户以为不填就会按它编码。
        """
        colors = params.colors if params else 128
        dither = params.dither if params else "none"

        idx = self.colors_combo.findText(str(colors))
        self.colors_combo.setCurrentIndex(idx if idx >= 0 else 1)
        for i in range(self.dither_combo.count()):
            if self.dither_combo.itemData(i) == dither:
                self.dither_combo.setCurrentIndex(i)
                break

        if params is None:
            return
        self.width_edit.setText(str(params.width))
        self.fps_edit.setText("%g" % float(params.fps_cap))

    def params(self):
        """读表单 → (GifCustomParams, None)；非法时 (None, 原因)。"""
        w_raw = self.width_edit.text().strip()
        fps_raw = self.fps_edit.text().strip()
        if not w_raw:
            return None, "请填写自定义宽度"
        if not fps_raw:
            return None, "请填写帧率上限"
        try:
            params = GifCustomParams(
                width=int(w_raw),
                fps_cap=float(fps_raw),
                colors=_COLOR_OPTIONS[self.colors_combo.currentIndex()],
                dither=self.dither_combo.currentData() or "none",
            )
        except ValueError:
            return None, "宽度/帧率须为有效数字"
        try:
            params.validate()
        except RuntimeError as e:
            return None, str(e)
        return params, None


class GifPage(PageBase):
    _DEFAULT_QUALITY_INDEX = 2      # 「高质量」
    _CUSTOM_INDEX = 4               # 「自定义…」

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ytdlp_warning_bar = None  # 「未找到 yt-dlp」常驻 infobar，装好后关闭
        self._parse_worker = None       # 当前解析线程（GenericWorker）
        self._cover_worker = None       # 当前封面串行下载线程（_CoverLoader）
        self._retired = set()           # 已停止但仍在运行的线程：持有引用直到 finished，防运行中被回收
        self._parse_seq = 0             # 解析代际（丢弃过期回调）
        self._parsed_url = None         # 最近一次解析成功的链接（改链接后需重新解析）
        self._parsing_url = None        # 进行中解析的链接
        self._cover_bytes = {}          # 条目序号 -> 封面字节（解析期已下载，入队复用）
        self._last_title = ''
        self._last_channel = ''
        self._items = []                # 最近一次解析的动图条目
        self._cards = {}                # 条目序号 -> GifItemCard
        self._custom_params = None      # 已确认的自定义参数（GifCustomParams）
        self._custom_flyout = None      # 打开中的自定义参数浮层
        # 已提交的档位：预设档点选即提交，「自定义…」只有浮层点「确定」才提交。
        # 浮层被取消/点空白处关闭时回退到这里（浮层本身不落任何页面级状态，
        # 是否确认记在浮层实例上，避免状态在页面上残留）。
        self._committed_quality_index = self._DEFAULT_QUALITY_INDEX

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        layout.addWidget(qfw.TitleLabel("X (Twitter) 动图下载"))
        layout.addWidget(self.create_caption_label(
            "粘贴 X 链接，解析后勾选要下载的动图（视频默认不选），自动转为 GIF"))

        # 检查 yt-dlp 可用性（PATH 全局或内置 tools/ 均可）
        if not is_ytdlp_available():
            self._ytdlp_warning_bar = show_infobar(
                self, "warning", title="依赖缺失",
                content="未找到 yt-dlp，动图下载不可用。请前往设置页下载安装")
            self.add_goto_settings_button(self._ytdlp_warning_bar)

        # ---- 参数卡：链接 / 输出目录 / 编码质量 ----
        layout.addSpacing(2)
        card = qfw.CardWidget()
        grid = QGridLayout(card)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(0, 56)

        self.url_input = qfw.LineEdit()
        self.url_input.setPlaceholderText(
            "粘贴 X/Twitter 推文链接，如 https://x.com/xxx/status/123")
        self.url_input.setClearButtonEnabled(True)
        self.url_input.returnPressed.connect(self._start_parse)
        self.parse_btn = qfw.PrimaryPushButton("解析")
        self.parse_btn.setIcon(qfw.FluentIcon.SEARCH)
        self.parse_btn.setFixedWidth(96)
        self.parse_btn.clicked.connect(self._start_parse)
        grid.addWidget(qfw.BodyLabel("动图链接"), 0, 0)
        grid.addWidget(self.url_input, 0, 1)
        grid.addWidget(self.parse_btn, 0, 2)

        self.out_dir = qfw.LineEdit()
        # 占位文案与嗅探页/直播监控页保持一致（默认目录即系统「下载」文件夹）
        self.out_dir.setPlaceholderText("默认: /下载")
        self.out_dir.setClearButtonEnabled(True)
        browse_btn = qfw.PushButton("浏览")
        browse_btn.setFixedWidth(96)
        browse_btn.clicked.connect(lambda: self.browse_dir(self.out_dir))
        grid.addWidget(qfw.BodyLabel("输出目录"), 1, 0)
        grid.addWidget(self.out_dir, 1, 1)
        grid.addWidget(browse_btn, 1, 2)

        self.quality_combo = qfw.ComboBox()
        for q in (GifQuality.LOW, GifQuality.STANDARD, GifQuality.HIGH,
                  GifQuality.BEST, _CUSTOM_ITEM):
            self.quality_combo.addItem(q)
        self.quality_combo.setCurrentIndex(self._DEFAULT_QUALITY_INDEX)
        self.quality_combo.setMinimumWidth(180)
        self.quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        # 自定义参数摘要 + 重开浮层的按钮（仅「自定义…」档出现）。
        # 本行必须装在**定高容器**里：按需显隐会让行高在 27↔32px 之间跳，
        # 把下面的卡片区推动几像素。
        self.custom_summary = qfw.CaptionLabel("")
        self.custom_edit_btn = qfw.PushButton("调整参数")
        self.custom_edit_btn.setToolTip("打开自定义编码参数")
        self.custom_edit_btn.clicked.connect(lambda: self._open_custom_flyout())
        quality_row = QWidget()
        row = QHBoxLayout(quality_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.quality_combo)
        row.addWidget(self.custom_summary)
        row.addWidget(self.custom_edit_btn)
        row.addStretch(1)
        quality_row.setFixedHeight(max(self.quality_combo.sizeHint().height(),
                                       self.custom_edit_btn.sizeHint().height()))
        grid.addWidget(qfw.BodyLabel("编码质量"), 2, 0)
        grid.addWidget(quality_row, 2, 1)
        self._update_custom_summary()

        layout.addWidget(card)

        # ---- 选择区操作行（计数 + 全选动图 + 下载）----
        # 全选键从链接栏移到这里（与「选中」语义放一起）；本行始终存在，
        # 卡片区是页面唯一的伸缩项（下方 view_stack），故解析前后位置不变。
        # 文案随作用域切换：「全选动图」/「全不选」（决策 44）。
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.selection_hint = self.create_caption_label("")
        action_row.addWidget(self.selection_hint)
        action_row.addStretch(1)
        self.reselect_btn = qfw.PushButton("全选动图")
        self.reselect_btn.setFixedWidth(96)
        self.reselect_btn.setEnabled(False)     # 解析出卡片后才可用
        self.reselect_btn.clicked.connect(self._toggle_all)
        self.dl_btn = qfw.PrimaryPushButton("下载并转 GIF")
        self.dl_btn.setIcon(qfw.FluentIcon.DOWNLOAD)
        self.dl_btn.setFixedWidth(180)
        self.dl_btn.setEnabled(False)   # 解析出动图且至少勾选一项后才可点击
        self.dl_btn.clicked.connect(self._start_download)
        action_row.addWidget(self.reselect_btn)
        action_row.addWidget(self.dl_btn)
        layout.addLayout(action_row)

        # ---- 展示区：空态占位 / 卡片网格 二选一 ----
        # 两页共用一个伸缩槽位，切换只换内容不改几何：解析前后零位移（决策 37）。
        # （隐藏的控件不参与 QBoxLayout 分配：若只隐藏网格，多余空白会被平均
        #   分摊给布局里其余控件，表现为整页被撑开、解析完成瞬间弹回顶部。）
        self.empty_state = self._build_empty_state()
        self.items_scroll = qfw.ScrollArea()
        self.items_scroll.setWidgetResizable(True)
        self.items_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.items_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setStyleSheet("QWidget { background: transparent; }")
        self.items_flow = qfw.AdaptiveFlowLayout(
            content, needAni=False, isTight=True)
        self.items_flow.setWidgetMinimumWidth(GifItemCard.CARD_W)
        self.items_flow.setWidgetMaximumWidth(GifItemCard.CARD_W)
        self.items_flow.setHorizontalSpacing(16)
        self.items_flow.setVerticalSpacing(16)
        self.items_flow.setContentsMargins(4, 4, 4, 4)
        self.items_scroll.setWidget(content)
        self.items_scroll.enableTransparentBackground()
        # 平滑滚动与任务队列页同款（库默认 400ms 偏绵长）
        set_smooth_scroll_params(self.items_scroll)

        self.view_stack = QStackedWidget()
        self.view_stack.setFrameShape(QFrame.Shape.NoFrame)
        self.view_stack.addWidget(self.empty_state)     # 0：空态占位
        self.view_stack.addWidget(self.items_scroll)    # 1：卡片网格
        layout.addWidget(self.view_stack, stretch=1)

        self._setup_log_area(layout)

        # 本页发起的任务失败 → 在本页弹常显错误提示（需手动关闭）
        TaskQueueManager.instance().task_failed.connect(self._on_task_failed)

    def _on_task_failed(self, task):
        """本页发起的动图任务失败 → 弹常显错误提示（不自动消失）。

        task_failed 是全局信号，按 _owned_task_ids 过滤，只提示本页任务。
        """
        if task.task_id not in self._owned_task_ids:
            return
        title = task.title or "动图下载失败"
        content = task.error or "任务执行失败，请查看日志"
        if len(content) > 200:
            content = content[:200] + "…"
        show_infobar(self, "error", title=title, content=content)

    # -------- 空态 / 自定义参数浮层 --------

    def _build_empty_state(self) -> QWidget:
        """空态占位：解析前占住卡片区，说明这里会出现什么。"""
        box = QWidget()
        outer = QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        outer.addStretch(1)

        icon = QLabel()
        color = QColor("#8a8a8a") if qfw.isDarkTheme() else QColor("#b0b0b0")
        icon.setPixmap(qfw.FluentIcon.DOWNLOAD.icon(color=color).pixmap(44, 44))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(icon)

        tip = qfw.BodyLabel("还没有解析条目")
        tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(tip)

        # 与上一行同为 BodyLabel（字号一致）
        hint = qfw.BodyLabel("请粘贴 X 链接并解析")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(hint)

        outer.addStretch(1)
        return box

    def _show_cards(self, show: bool):
        """在「空态占位」与「卡片网格」间切换（同一伸缩槽位，几何不变）。"""
        self.view_stack.setCurrentIndex(1 if show else 0)

    def _on_quality_changed(self, index: int):
        """质量下拉：选「自定义…」弹参数浮层，预设档直接提交并刷新摘要。

        已确认过的自定义参数不清空（切回自定义时浮层用它预填），非自定义态下
        它不参与入队（见 `_start_download`）。
        """
        if self.quality_combo.currentText() == _CUSTOM_ITEM:
            self._open_custom_flyout()
            return
        self._committed_quality_index = index
        self._update_custom_summary()

    def _open_custom_flyout(self):
        """在质量下拉上弹「自定义参数」浮层。

        两个入口：下拉里选「自定义…」，以及质量行的「调整参数」按钮——浮层点
        空白处即关（= 确定），没有这个按钮就只能反复切档位回来才能重开。
        """
        if self._custom_flyout is not None and self._custom_flyout.isVisible():
            return
        view = _CustomParamsView(self._custom_params)
        flyout = qfw.Flyout.make(
            view, target=self.quality_combo,
            aniType=qfw.FlyoutAnimationType.DROP_DOWN)
        flyout.closed.connect(lambda f=flyout: self._on_custom_flyout_closed(f))
        self._custom_flyout = flyout

    def _on_custom_flyout_closed(self, flyout):
        """浮层关闭 **即确定**（浮层里没有确定/取消按钮）。

        参数填全了 → 提交（同时把档位落回「自定义…」，blockSignals 防再弹浮层）。
        没填全（宽度/帧率默认留空，用户没填）→ 不提交：已提交过自定义参数就沿用旧的，
        否则回退到上一个预设档并提示原因，免得下拉"莫名跳回去"。
        """
        if self._custom_flyout is flyout:
            self._custom_flyout = None

        params, err = flyout.view.params()
        if params is not None:
            self._custom_params = params
            self._committed_quality_index = self._CUSTOM_INDEX
            self.quality_combo.blockSignals(True)
            self.quality_combo.setCurrentIndex(self._CUSTOM_INDEX)
            self.quality_combo.blockSignals(False)
            self._update_custom_summary()
            return

        if self._committed_quality_index == self._CUSTOM_INDEX:
            # 旧参数仍然生效，保持自定义档原样
            self._update_custom_summary()
            return

        self.quality_combo.blockSignals(True)
        self.quality_combo.setCurrentIndex(self._committed_quality_index)
        self.quality_combo.blockSignals(False)
        self._update_custom_summary()
        show_infobar(self, "warning", title="自定义参数未生效",
                     content=f"{err}，已保持原档位", duration=4000)

    def _update_custom_summary(self):
        """刷新质量行右侧的摘要与「调整参数」按钮（两者仅自定义档出现）。

        显隐都发生在定高容器里（见 `__init__` 的 quality_row），故不改变行高。
        """
        active = (self._custom_params is not None
                  and self.quality_combo.currentText() == _CUSTOM_ITEM)
        self.custom_summary.setVisible(active)
        self.custom_edit_btn.setVisible(active)
        if not active:
            return
        p = self._custom_params
        self.custom_summary.setText(
            f"{p.width}px · {p.fps_cap:g}fps · {p.colors} 色 · {p.dither}")

    # -------- 解析 --------

    def _start_parse(self):
        url = self.url_input.text().strip()
        if not url:
            show_infobar(self, "warning", title="提示",
                         content="请输入链接", duration=3000)
            return
        if not re.search(r'(?:x\.com|twitter\.com)/.+/status/\d+', url):
            show_infobar(self, "warning", title="提示",
                         content="请输入 X/Twitter 状态链接（含 /status/<id>）",
                         duration=4000)
            return

        clean_url = _strip_media_index(url)
        self._parse_seq += 1
        seq = self._parse_seq
        self._retire_workers()
        self._clear_items()
        self._parsed_url = None
        self._parsing_url = clean_url   # 解析期间用户可能改输入框，序号归属以它为准
        self.parse_btn.setEnabled(False)
        self.log("正在解析条目列表（读取标题/封面，不下载媒体）...")
        show_infobar(self, "info", title="正在解析",
                     content="正在读取推文里的动图/视频列表", duration=3000)

        worker = GenericWorker(Functions.fetch_gif_items, args=(clean_url,))
        worker.done.connect(lambda data, s=seq: self._on_parse_done(s, data))
        worker.error.connect(lambda msg, s=seq: self._on_parse_error(s, msg))
        worker.finished.connect(
            lambda w=worker, s=seq: self._on_parse_worker_finished(s, w))
        self._parse_worker = worker
        worker.start()

    def _retire_workers(self):
        """停掉上一轮解析/封面线程：请求停止 + 保留引用到线程真正结束。

        QThread 在运行中被 Python 回收会触发 "Destroyed while thread is
        still running"（见 DEVELOPMENT.md 决策 2 的同款处理）。两个 worker
        都有 request_stop（GenericWorker 置 _stop_flag 后不再 emit 信号）。
        """
        for old in (self._cover_worker, self._parse_worker):
            if old is None:
                continue
            old.request_stop()
            if old.isRunning():
                self._retired.add(old)
                old.finished.connect(lambda w=old: self._retired.discard(w))
        self._cover_worker = None
        self._parse_worker = None

    def _on_parse_worker_finished(self, seq, worker):
        """解析线程结束：释放引用、恢复按钮（仅当仍是当前代际）"""
        if self._parse_worker is worker:
            self._parse_worker = None
        if seq == self._parse_seq:
            self.parse_btn.setEnabled(True)

    def _on_parse_done(self, seq: int, data: dict):
        if seq != self._parse_seq:
            return    # 过期回调（用户已重新解析）
        items = data.get('items') or []
        self._last_title = data.get('title') or ''
        self._last_channel = data.get('channel') or ''
        n_gif, n_video = self._count_items(items)
        if not items:
            self.log("该链接没有动图或视频（推文只含图片）")
            self.selection_hint.setText("该链接没有动图或视频")
            self._update_dl_button()
            # 系统通知（决策 40 扩展）：0 条对用户是「没拿到想要的结果」，单独提示
            notify_result_notice("未找到动图", self._last_title or self._parsing_url,
                                 "该链接没有动图或视频（推文只含图片）")
            return
        self._parsed_url = self._parsing_url
        # 列表顺序由 fetch_gif_items 保证「动图在前、视频在后」（决策 44）
        if n_video:
            self.log(f"解析到 {n_gif} 个动图、{n_video} 个视频；"
                     f"动图默认勾选，视频需手动勾选")
        else:
            self.log(f"解析到 {n_gif} 个动图，默认全部勾选")
        self._build_cards(items)
        self._start_cover_load(seq)
        # 系统通知（决策 40 扩展）：主窗口不在焦点时告知解析结果
        summary = (f"共解析到 {n_gif} 个动图、{n_video} 个视频" if n_video
                   else f"共解析到 {n_gif} 个动图")
        notify_result_notice("解析成功", self._last_title or self._parsing_url,
                             summary)

    @staticmethod
    def _count_items(items):
        """解析结果的动图/视频计数（决策 44）：返回 (n_gif, n_video)。"""
        n_video = sum(1 for i in items if i.get('kind') == 'video')
        return len(items) - n_video, n_video

    def _on_parse_error(self, seq: int, msg: str):
        if seq != self._parse_seq:
            return
        self.log(f"解析失败: {msg}")
        show_infobar(self, "error", title="解析失败", content=msg, duration=-1)
        # 系统通知（决策 40 扩展）：主窗口不在焦点时告知解析失败
        notify_result_notice("解析失败", self._parsing_url, msg)

    # -------- 选择区 --------

    def _clear_items(self):
        self._items = []
        self._cards = {}
        self._cover_bytes = {}
        # takeAllWidgets 才会 deleteLater（removeAllWidgets 只是移出布局，
        # 控件仍是 content 的子控件、继续可见）
        self.items_flow.takeAllWidgets()
        self._show_cards(False)
        self.selection_hint.setText("")
        self._update_dl_button()

    def _build_cards(self, items):
        self._items = items
        for item in items:
            card = GifItemCard(item)
            # 默认勾选口径在卡片里按 kind 决定：动图勾选、视频不勾选（决策 44），
            # 这里不能再统一 set_checked(True) 否则会把视频也选上
            card.toggled.connect(self._update_dl_button)
            self._cards[item['index']] = card
            self.items_flow.addWidget(card)
        self._show_cards(True)
        self._update_dl_button()

    def _start_cover_load(self, seq: int):
        worker = _CoverLoader(self._items)
        worker.loaded.connect(
            lambda index, data, s=seq: self._on_cover_loaded(s, index, data))
        worker.failed.connect(
            lambda index, s=seq: self._on_cover_failed(s, index))
        self._cover_worker = worker
        worker.start()

    def _on_cover_loaded(self, seq: int, index: int, data: bytes):
        if seq != self._parse_seq:
            return
        card = self._cards.get(index)
        if card is None:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            card.cover.setText("封面无法解码")
            return
        card.cover.setPixmap(pixmap)
        # 留存字节供入队复用：队列卡片走 cover_data 分支，不必再下一次网络
        self._cover_bytes[index] = data

    def _on_cover_failed(self, seq: int, index: int):
        if seq != self._parse_seq:
            return
        card = self._cards.get(index)
        if card is not None:
            card.cover.setText("封面加载失败")

    def _gif_indexes(self):
        """「全选动图」的作用域：有动图时只到动图，纯视频链接才退回全部条目。"""
        return [i for i, c in self._cards.items() if c.kind != 'video'] \
            or list(self._cards)

    def _toggle_all(self):
        """「全选动图/全不选」，口径见决策 44：

        - 「全选动图」只勾动图，**不碰视频**（视频必须由用户手动勾选并确认，全选不得
          悄悄把视频带上）；
        - 「全不选」清空全部（含用户手动勾上的视频），否则按钮会变成“点不干净”。
        """
        scope = self._gif_indexes()
        all_on = bool(scope) and all(self._cards[i].is_checked() for i in scope)
        self._set_all_checked(not all_on)

    def _set_all_checked(self, checked: bool):
        # 勾选时跳过视频：视频只能手动勾（决策 44）。纯视频链接没有动图可选，
        # 此时作用域退回全部条目，按钮文案也退回「全选」，仍然可用。
        has_gif = any(c.kind != 'video' for c in self._cards.values())
        for card in self._cards.values():
            if checked and has_gif and card.kind == 'video':
                continue
            card.set_checked(checked)
        self._update_dl_button()

    def _selected_indexes(self):
        return [i for i, c in self._cards.items() if c.is_checked()]

    def _update_dl_button(self):
        selected = self._selected_indexes()
        n = len(selected)
        total = len(self._cards)
        self.dl_btn.setEnabled(n > 0)
        self.reselect_btn.setEnabled(total > 0)
        # 按钮文案表示"点下去会发生什么"：作用域内全勾选时是「全不选」，否则「全选动图」
        # （纯视频链接没有动图可选、作用域退回全部条目，此时仍用「全选」）。
        scope = self._gif_indexes()
        all_on = bool(scope) and all(self._cards[i].is_checked() for i in scope)
        if all_on:
            self.reselect_btn.setText("全不选")
        elif any(c.kind != 'video' for c in self._cards.values()):
            self.reselect_btn.setText("全选动图")
        else:
            self.reselect_btn.setText("全选")
        if total:
            n_gif, n_video = self._count_kinds()
            if n_gif and n_video:
                head = f"共 {n_gif} 个动图、{n_video} 个视频"
            elif n_video:
                head = f"共 {n_video} 个视频"
            else:
                head = f"共 {n_gif} 个动图"
            self.selection_hint.setText(f"{head} · 已选 {n} 个")

    def _count_kinds(self, indexes=None):
        """统计卡片按类型计数（决策 44）。indexes 为空则统计全部卡片。"""
        cards = self._cards if indexes is None else {
            i: self._cards[i] for i in indexes if i in self._cards}
        n_gif = sum(1 for c in cards.values() if c.kind != 'video')
        return n_gif, len(cards) - n_gif

    # -------- 入队 --------

    def _start_download(self):
        url = self.url_input.text().strip()
        if not url:
            show_infobar(self, "warning", title="提示",
                         content="请输入链接", duration=3000)
            return
        if not re.search(r'(?:x\.com|twitter\.com)/.+/status/\d+', url):
            show_infobar(self, "warning", title="提示",
                         content="请输入 X/Twitter 状态链接（含 /status/<id>）",
                         duration=4000)
            return
        if not self._cards:
            show_infobar(self, "warning", title="提示",
                         content="请先解析链接并选择要下载的条目", duration=3000)
            return
        if _strip_media_index(url) != self._parsed_url:
            # 条目序号来自上一次解析，链接已换 → 序号对不上，必须重解析
            show_infobar(self, "warning", title="提示",
                         content="链接已更改，请重新解析后再下载", duration=4000)
            return

        selected_indexes = self._selected_indexes()
        if not selected_indexes:
            show_infobar(self, "warning", title="提示",
                         content="请至少勾选一个动图或视频", duration=3000)
            return

        # 质量档取值与兜底校验排在确认弹窗之前：校验若失败就当场返回，不该让用户
        # 先点了「仍要转 GIF」再被打回——弹窗是确认动作，不是校验动作。
        is_custom = self.quality_combo.currentText() == _CUSTOM_ITEM
        quality = GifQuality.CUSTOM if is_custom else \
            self.quality_combo.currentText()
        custom_params = self._custom_params if is_custom else None
        if is_custom and custom_params is None:
            # 兜底：正常交互下到不了——浮层（Qt.Popup 窗口）会吞掉外部点击，用户
            # 点「下载」时只会先收起浮层、档位随之回退到预设档（见
            # _on_custom_flyout_closed）。这里防的是信号被绕过等异常路径；一旦漏掉，
            # 下游 convert_to_gif 会在"下载完整个视频之后"才抛「未知的编码质量档位」。
            show_infobar(self, "warning", title="提示",
                         content="请先点「调整参数」确认自定义参数", duration=4000)
            return

        # 视频转 GIF 的确认（决策 44）：视频默认不勾选，是用户显式勾上的，但视频
        # 转 GIF 体积大、耗时长（尤其 4K 长视频），下载前最后一次确认。取消则整单
        # 返回、不入队，由用户自行取消勾选后再点下载。
        n_gif, n_video = self._count_kinds(selected_indexes)
        if n_video:
            detail = (f"其中 {n_video} 个为普通视频" if n_gif
                      else f"选中的 {n_video} 个均为普通视频")
            # 文案手动预折行：qfw MessageBox 宽度取未折行文本的 sizeHint，
            # 不预折行会撑得极宽（实测 ~700px，见 youtube_page 同类弹窗）
            box = qfw.MessageBox(
                "视频将转为 GIF",
                f"共选中 {len(selected_indexes)} 个条目，{detail}。\n"
                "\n"
                "视频转 GIF 会明显增大体积并耗时较长，\n"
                "分辨率越高、时长越长越耗时。\n"
                "\n"
                "仍要转为 GIF，还是返回取消勾选视频？",
                self.window())
            box.yesButton.setText("仍要转 GIF")
            box.cancelButton.setText("返回修改")
            if not box.exec():
                return

        out_dir = self.read_path(self.out_dir) or DEFAULT_OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)

        m = re.search(r'/status/(\d+)', url)
        status_id = m.group(1) if m else url

        self._enqueue(url, status_id, out_dir, quality, custom_params,
                      selected_indexes)

    def _enqueue(self, url, status_id, out_dir, quality, custom_params,
                 selected_indexes):
        """把动图任务加入队列。

        metadata 字段按视频下载任务口径（title/channel/duration/cover_url/
        cover_data），供 Task/TaskCard 复用下载任务的显示逻辑——解析阶段已经
        取到这些信息，无需再跑一次 yt-dlp。

        `selected_indexes` 是关键字专用形参，只能经 run_queued_task 的 kwargs
        通道传给任务函数（args 是纯位置通道，见 DEVELOPMENT.md 决策 31）。
        """
        title = self._last_title or f"动图下载 - {status_id}"
        # 任务卡片封面：优先勾选的首个条目（与「以首条为代表」的既有口径一致）
        first = next((i for i in self._items
                      if i['index'] in set(selected_indexes)), None)

        download_config = load_download_config()
        metadata = {
            'url': url,
            'out_dir': out_dir,
            'quality': quality,
            'filename': status_id,
            'title': self._last_title,
            'channel': self._last_channel,
            'duration': (first or {}).get('duration') or 0,
            'cover_url': (first or {}).get('cover_url') or '',
        }
        # 解析期已下载的封面字节直接复用，队列卡片无需再发一次请求
        if first and self._cover_bytes.get(first['index']):
            metadata['cover_data'] = self._cover_bytes[first['index']]

        self.run_queued_task(
            func=Functions.download_and_convert_gif,
            args=(url, out_dir, quality, custom_params,
                  download_config['use_aria2c'],
                  download_config['aria2c_connections'],
                  download_config['concurrent_fragments']),
            # 选中序号是关键字专用形参，走 kwargs 通道（args 只放位置参数）
            kwargs={'selected_indexes': selected_indexes},
            task_type=TaskType.GIF,
            title=title,
            metadata=metadata,
        )
        show_infobar(self, "info", title="任务已加入队列",
                     content=f"共 {len(selected_indexes)} 个条目，"
                             f"下载完成后自动转码为 GIF",
                     duration=3000)

    def refresh_ytdlp_status(self):
        """yt-dlp 装好后重新检测：若已可用则关闭「未找到 yt-dlp」提示"""
        if self._ytdlp_warning_bar is not None and is_ytdlp_available():
            try:
                self._ytdlp_warning_bar.close()
            except Exception:
                pass
            self._ytdlp_warning_bar = None
