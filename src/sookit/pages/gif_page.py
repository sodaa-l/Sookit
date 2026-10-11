"""
X (Twitter) 动图下载 页面

输入 X 动图链接 → 点「解析」列出推文里的动图（卡片网格，可勾选）→
下载选中项并转成 GIF。

质量可选四档预设（core/gif_utils.GifQuality），或展开「自定义」卡片
自行指定宽度 / 帧率上限 / 色数 / dither（GifCustomParams）。

解析只读元数据（yt-dlp -J），不下载媒体；条目序号（1-based，原始条目列表次序）
在下载时由 `download_and_convert_gif(..., selected_indexes=[...])`（关键字专用形参，
经任务队列 kwargs 通道传递）拼成 `--playlist-items` 回传，故选中的必须与 yt-dlp
看到的次序一致。
"""
import os
import re
import urllib.request

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import (
    QIntValidator, QDoubleValidator, QPainter, QPixmap,
)
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QGridLayout, QWidget, QFrame,
)

import qfluentwidgets as qfw

from sookit.core.functions import (
    Functions, is_ytdlp_available, load_download_config,
    DEFAULT_OUTPUT_DIR,
)
from sookit.core.gif_utils import GifQuality, GifCustomParams, DITHER_CHOICES
from sookit.core.task_queue import TaskQueueManager, TaskType
from sookit.core.utils import get_certifi_ssl_context
from sookit.core.workers import GenericWorker
from sookit.pages.base import PageBase
from sookit.widgets.infobar import show_infobar
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


class GifItemCard(qfw.CardWidget):
    """单个动图条目卡片：封面 + 序号勾选，点击卡片切换勾选。

    不显示时长：条目能进列表的前提就是 `_looks_like_animated` 判定
    `duration in (None, 0)`（yt-dlp 不解码 animated_gif 的时长），
    所以「有时长才显示」的标签永远不可达，2026-10-11 复核后移除。
    """

    CARD_W = 200
    COVER_H = 112          # 200 * 9/16
    CARD_H = COVER_H + 52  # 上下留白 8 + 勾选行 + 间距

    def __init__(self, item: dict, parent=None):
        super().__init__(parent)
        self.item = item
        self.setFixedSize(self.CARD_W, self.CARD_H)
        self.setClickEnabled(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.cover = RoundedCoverWidget(self.CARD_W - 16, self.COVER_H)
        self.cover.setText("加载中...")
        # 封面是子控件，默认会吞掉点击；设穿透后点击落在卡片上统一处理勾选
        self.cover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.cover)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.check = qfw.CheckBox(f"动图 {item['index']}")
        self.check.stateChanged.connect(lambda _: self.update())
        row.addWidget(self.check)
        row.addStretch()

        layout.addLayout(row)

    def is_checked(self) -> bool:
        return self.check.isChecked()

    def set_checked(self, checked: bool):
        self.check.setChecked(checked)

    def mouseReleaseEvent(self, event):
        # 勾选框自身会吞掉点击事件，不经此处；卡片其余区域点击 = 切换勾选
        super().mouseReleaseEvent(event)
        self.check.setChecked(not self.check.isChecked())

    def paintEvent(self, event):
        """勾选时在卡片外圈描一条主题色边框，弱化"已选中"的辨识成本。"""
        super().paintEvent(event)
        if not self.is_checked():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        pen = painter.pen()
        pen.setColor(qfw.themeColor())
        pen.setWidth(2)
        painter.setPen(pen)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)


class GifPage(PageBase):
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = qfw.TitleLabel("X (Twitter) 动图下载")
        layout.addWidget(title)
        layout.addWidget(self.create_caption_label(
            "粘贴 X 动图链接，解析后勾选要下载的动图，自动转为 GIF"))
        layout.addSpacing(6)

        # 检查 yt-dlp 可用性（PATH 全局或内置 tools/ 均可）
        if not is_ytdlp_available():
            self._ytdlp_warning_bar = show_infobar(
                self, "warning", title="依赖缺失",
                content="未找到 yt-dlp，动图下载不可用。请前往设置页下载安装")
            self.add_goto_settings_button(self._ytdlp_warning_bar)

        # ---- 链接解析行 ----
        url_row = QHBoxLayout()
        self.url_input = qfw.LineEdit()
        self.url_input.setPlaceholderText(
            "粘贴 X/Twitter 动图链接，如 https://x.com/xxx/status/123")
        self.parse_btn = qfw.PrimaryPushButton("解析")
        self.parse_btn.setFixedWidth(100)
        self.parse_btn.clicked.connect(self._start_parse)
        self.reselect_btn = qfw.PushButton("全不选")
        self.reselect_btn.setFixedWidth(100)
        self.reselect_btn.setVisible(False)
        self.reselect_btn.clicked.connect(self._toggle_all)
        url_row.addWidget(self.url_input, stretch=1)
        url_row.addWidget(self.parse_btn)
        url_row.addWidget(self.reselect_btn)
        layout.addLayout(url_row)

        # ---- 表单 ----
        grid = QGridLayout()
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)

        lbl = qfw.BodyLabel("输出目录")
        self.out_dir = qfw.LineEdit()
        # 占位文案与嗅探页/直播监控页保持一致（默认目录即系统「下载」文件夹）
        self.out_dir.setPlaceholderText("默认: /下载")
        browse_btn = qfw.PushButton("浏览")
        browse_btn.setFixedWidth(100)
        browse_btn.clicked.connect(lambda: self.browse_dir(self.out_dir))
        grid.addWidget(lbl, 0, 0)
        grid.addWidget(self.out_dir, 0, 1)
        grid.addWidget(browse_btn, 0, 2)

        lbl = qfw.BodyLabel("编码质量")
        self.quality_combo = qfw.ComboBox()
        for q in (GifQuality.LOW, GifQuality.STANDARD, GifQuality.HIGH,
                  GifQuality.BEST, "自定义…"):
            self.quality_combo.addItem(q)
        self.quality_combo.setCurrentIndex(2)   # 默认高质量
        self.quality_combo.currentIndexChanged.connect(
            self._on_quality_changed)
        grid.addWidget(lbl, 1, 0)
        grid.addWidget(self.quality_combo, 1, 1)

        layout.addLayout(grid)

        # ---- 自定义参数卡片（选「自定义…」时显示）----
        self.custom_card = self._build_custom_card()
        self.custom_card.setVisible(False)
        layout.addWidget(self.custom_card)

        # ---- 动图选择区（解析成功后显示）----
        self.selection_hint = self.create_caption_label("")
        self.selection_hint.setVisible(False)
        layout.addWidget(self.selection_hint)

        self.items_scroll = qfw.ScrollArea()
        self.items_scroll.setWidgetResizable(True)
        self.items_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.items_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.items_scroll.setVisible(False)
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
        layout.addWidget(self.items_scroll, stretch=1)

        layout.addSpacing(6)
        self.dl_btn = qfw.PrimaryPushButton("▶ 下载并转 GIF")
        self.dl_btn.setFixedWidth(240)
        self.dl_btn.setEnabled(False)   # 解析出动图且至少勾选一项后才可点击
        self.dl_btn.clicked.connect(self._start_download)
        layout.addWidget(self.dl_btn, alignment=Qt.AlignmentFlag.AlignCenter)

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

    # -------- 自定义参数卡片 --------

    def _build_custom_card(self) -> QWidget:
        card = qfw.CardWidget()
        inner = QGridLayout(card)
        inner.setContentsMargins(16, 12, 16, 12)
        inner.setVerticalSpacing(10)
        inner.setColumnStretch(1, 1)

        hint = qfw.CaptionLabel(
            "自定义参数（不约束体积，按所填参数直接编码）")
        inner.addWidget(hint, 0, 0, 1, 2)

        lbl = qfw.BodyLabel("宽度 (px)")
        self.cust_width = qfw.LineEdit()
        self.cust_width.setPlaceholderText("如 480（高度按源宽高比自动计算）")
        self.cust_width.setValidator(QIntValidator(2, 99999))
        inner.addWidget(lbl, 1, 0)
        inner.addWidget(self.cust_width, 1, 1)

        lbl = qfw.BodyLabel("帧率上限")
        self.cust_fps = qfw.LineEdit()
        self.cust_fps.setPlaceholderText("如 30（源 ≤ 该值保持源帧率，超过则锁定）")
        dv = QDoubleValidator(0.1, 240, 2)
        dv.setNotation(QDoubleValidator.Notation.StandardNotation)
        self.cust_fps.setValidator(dv)
        inner.addWidget(lbl, 2, 0)
        inner.addWidget(self.cust_fps, 2, 1)

        lbl = qfw.BodyLabel("色数")
        self.cust_colors = qfw.ComboBox()
        for c in _COLOR_OPTIONS:
            self.cust_colors.addItem(str(c))
        self.cust_colors.setCurrentIndex(1)   # 默认 128
        inner.addWidget(lbl, 3, 0)
        inner.addWidget(self.cust_colors, 3, 1)

        lbl = qfw.BodyLabel("dither 算法")
        self.cust_dither = qfw.ComboBox()
        for label in _DITHER_LABELS:
            self.cust_dither.addItem(label, userData=_DITHER_LABELS[label])
        self.cust_dither.setCurrentIndex(0)   # 默认关闭（与预设档一致）
        inner.addWidget(lbl, 4, 0)
        inner.addWidget(self.cust_dither, 4, 1)

        return card

    def _on_quality_changed(self, index: int):
        is_custom = self.quality_combo.currentText() == "自定义…"
        self.custom_card.setVisible(is_custom)

    # -------- 解析 --------

    def _start_parse(self):
        url = self.url_input.text().strip()
        if not url:
            show_infobar(self, "warning", title="提示",
                         content="请输入动图链接", duration=3000)
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
        self.log("正在解析动图列表（读取标题/封面，不下载媒体）...")
        show_infobar(self, "info", title="正在解析",
                     content="正在读取推文里的动图列表", duration=3000)

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
        if not items:
            self.log("该链接没有动图（推文只含普通视频或图片）")
            self.selection_hint.setText("该链接没有动图")
            self.selection_hint.setVisible(True)
            self._update_dl_button()
            return
        self._parsed_url = self._parsing_url
        self.log(f"解析到 {len(items)} 个动图，默认全部勾选")
        self._build_cards(items)
        self._start_cover_load(seq)

    def _on_parse_error(self, seq: int, msg: str):
        if seq != self._parse_seq:
            return
        self.log(f"解析失败: {msg}")
        show_infobar(self, "error", title="解析失败", content=msg, duration=-1)

    # -------- 选择区 --------

    def _clear_items(self):
        self._items = []
        self._cards = {}
        self._cover_bytes = {}
        # takeAllWidgets 才会 deleteLater（removeAllWidgets 只是移出布局，
        # 控件仍是 content 的子控件、继续可见）
        self.items_flow.takeAllWidgets()
        self.items_scroll.setVisible(False)
        self.selection_hint.setVisible(False)
        self.reselect_btn.setVisible(False)
        self._update_dl_button()

    def _build_cards(self, items):
        self._items = items
        for item in items:
            card = GifItemCard(item)
            card.set_checked(True)   # 默认全部勾选
            card.check.stateChanged.connect(lambda _: self._update_dl_button())
            self._cards[item['index']] = card
            self.items_flow.addWidget(card)
        self.reselect_btn.setVisible(True)
        self.items_scroll.setVisible(True)
        self.selection_hint.setVisible(True)
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

    def _toggle_all(self):
        """「全选/全不选」：全勾选时取消全部，否则全勾选。"""
        total = len(self._cards)
        self._set_all_checked(not (total and len(self._selected_indexes()) == total))

    def _set_all_checked(self, checked: bool):
        for card in self._cards.values():
            card.set_checked(checked)
        self._update_dl_button()

    def _selected_indexes(self):
        return [i for i, c in self._cards.items() if c.is_checked()]

    def _update_dl_button(self):
        selected = self._selected_indexes()
        n = len(selected)
        self.dl_btn.setEnabled(n > 0)
        total = len(self._cards)
        # 按钮文案表示"点下去会发生什么"：全勾选时是「全不选」，否则是「全选」
        self.reselect_btn.setText("全不选" if n == total and total else "全选")
        if total:
            self.selection_hint.setText(f"共 {total} 个动图，已选 {n} 个")

    # -------- 入队 --------

    def _collect_custom_params(self):
        """读取自定义卡片参数并校验，非法时 InfoBar 提示并返回 None。"""
        w_raw = self.cust_width.text().strip()
        fps_raw = self.cust_fps.text().strip()
        if not w_raw:
            show_infobar(self, "warning", title="提示",
                         content="请填写自定义宽度", duration=3000)
            return None
        if not fps_raw:
            show_infobar(self, "warning", title="提示",
                         content="请填写帧率上限", duration=3000)
            return None
        try:
            params = GifCustomParams(
                width=int(w_raw),
                fps_cap=float(fps_raw),
                colors=_COLOR_OPTIONS[self.cust_colors.currentIndex()],
                dither=self.cust_dither.currentData() or "none",
            )
        except ValueError:
            show_infobar(self, "warning", title="提示",
                         content="宽度/帧率须为有效数字", duration=3000)
            return None
        try:
            params.validate()
        except RuntimeError as e:
            show_infobar(self, "warning", title="参数无效",
                         content=str(e), duration=5000)
            return None
        return params

    def _start_download(self):
        url = self.url_input.text().strip()
        if not url:
            show_infobar(self, "warning", title="提示",
                         content="请输入动图链接", duration=3000)
            return
        if not re.search(r'(?:x\.com|twitter\.com)/.+/status/\d+', url):
            show_infobar(self, "warning", title="提示",
                         content="请输入 X/Twitter 状态链接（含 /status/<id>）",
                         duration=4000)
            return
        if not self._cards:
            show_infobar(self, "warning", title="提示",
                         content="请先解析链接并选择要下载的动图", duration=3000)
            return
        if _strip_media_index(url) != self._parsed_url:
            # 条目序号来自上一次解析，链接已换 → 序号对不上，必须重解析
            show_infobar(self, "warning", title="提示",
                         content="链接已更改，请重新解析后再下载", duration=4000)
            return

        selected_indexes = self._selected_indexes()
        if not selected_indexes:
            show_infobar(self, "warning", title="提示",
                         content="请至少勾选一个动图", duration=3000)
            return

        is_custom = self.quality_combo.currentText() == "自定义…"
        quality = GifQuality.CUSTOM if is_custom else \
            self.quality_combo.currentText()
        custom_params = self._collect_custom_params() if is_custom else None
        if is_custom and custom_params is None:
            return    # 校验未通过，提示已弹出

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
                  download_config['aria2c_connections']),
            # 选中序号是关键字专用形参，走 kwargs 通道（args 只放位置参数）
            kwargs={'selected_indexes': selected_indexes},
            task_type=TaskType.GIF,
            title=title,
            metadata=metadata,
        )
        show_infobar(self, "info", title="任务已加入队列",
                     content=f"共 {len(selected_indexes)} 个动图，"
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
