"""
动图下载 页面

输入 X 动图链接 → yt-dlp 下载 MP4 → 自动转码 GIF（不入队列嗅探，直接下载）。
质量可选四档预设（core/gif_utils.GifQuality），或展开「自定义」卡片
自行指定宽度 / 帧率上限 / 色数 / dither（GifCustomParams）。
"""
import os
import re

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIntValidator, QDoubleValidator
from PyQt6.QtWidgets import QVBoxLayout, QGridLayout, QWidget

import qfluentwidgets as qfw

from sookit.core.functions import (
    Functions, is_ytdlp_available, load_download_config,
    DEFAULT_OUTPUT_DIR,
)
from sookit.core.gif_utils import GifQuality, GifCustomParams, DITHER_CHOICES
from sookit.core.task_queue import TaskType
from sookit.pages.base import PageBase
from sookit.widgets.infobar import show_infobar

# 色数固定选项（GIF 调色板常用档位，含内核 COLOR_CANDIDATES）
_COLOR_OPTIONS = (256, 128, 64, 32, 24, 16)
# dither 显示文本 → 内核取值（paletteuse 算法）
_DITHER_LABELS = {
    "关闭（无抖动）": "none",
    "bayer（有序抖动）": "bayer",
    "floyd_steinberg（误差扩散）": "floyd_steinberg",
    "sierra2_4a（误差扩散）": "sierra2_4a",
}


class GifPage(PageBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._ytdlp_warning_bar = None  # 「未找到 yt-dlp」常驻 infobar，装好后关闭

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = qfw.TitleLabel("动图下载")
        layout.addWidget(title)
        layout.addWidget(self.create_caption_label(
            "粘贴 X 动图链接，下载后自动转为 GIF"))
        layout.addSpacing(6)

        # 检查 yt-dlp 可用性（PATH 全局或内置 tools/ 均可）
        if not is_ytdlp_available():
            self._ytdlp_warning_bar = show_infobar(
                self, "warning", title="依赖缺失",
                content="未找到 yt-dlp，动图下载不可用。请前往设置页下载安装")
            self.add_goto_settings_button(self._ytdlp_warning_bar)

        # ---- 表单 ----
        grid = QGridLayout()
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)

        lbl = qfw.BodyLabel("动图链接")
        self.url_input = qfw.LineEdit()
        self.url_input.setPlaceholderText(
            "粘贴 X/Twitter 动图链接，如 https://x.com/xxx/status/123")
        grid.addWidget(lbl, 0, 0)
        grid.addWidget(self.url_input, 0, 1)

        lbl = qfw.BodyLabel("输出目录")
        self.out_dir = qfw.LineEdit()
        self.out_dir.setPlaceholderText(f"默认 {DEFAULT_OUTPUT_DIR}")
        browse_btn = qfw.PushButton("浏览")
        browse_btn.setFixedWidth(100)
        browse_btn.clicked.connect(lambda: self.browse_dir(self.out_dir))
        grid.addWidget(lbl, 1, 0)
        grid.addWidget(self.out_dir, 1, 1)
        grid.addWidget(browse_btn, 1, 2)

        lbl = qfw.BodyLabel("编码质量")
        self.quality_combo = qfw.ComboBox()
        for q in (GifQuality.LOW, GifQuality.STANDARD, GifQuality.HIGH,
                  GifQuality.BEST, "自定义…"):
            self.quality_combo.addItem(q)
        self.quality_combo.setCurrentIndex(2)   # 默认高质量
        self.quality_combo.currentIndexChanged.connect(
            self._on_quality_changed)
        grid.addWidget(lbl, 2, 0)
        grid.addWidget(self.quality_combo, 2, 1)

        layout.addLayout(grid)

        # ---- 自定义参数卡片（选「自定义…」时显示）----
        self.custom_card = self._build_custom_card()
        self.custom_card.setVisible(False)
        layout.addWidget(self.custom_card)

        layout.addSpacing(6)
        btn = qfw.PrimaryPushButton("▶ 下载并转 GIF")
        btn.setFixedWidth(240)
        btn.clicked.connect(self._start_download)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._setup_log_area(layout)

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

        is_custom = self.quality_combo.currentText() == "自定义…"
        quality = GifQuality.CUSTOM if is_custom else \
            self.quality_combo.currentText()
        custom_params = self._collect_custom_params() if is_custom else None
        if is_custom and custom_params is None:
            return    # 校验未通过，提示已弹出

        out_dir = self.out_dir.text().strip() or DEFAULT_OUTPUT_DIR
        os.makedirs(out_dir, exist_ok=True)

        # 下载配置（aria2c 开关与连接数）沿用设置页
        download_config = load_download_config()

        m = re.search(r'/status/(\d+)', url)
        status_id = m.group(1) if m else url
        self.run_queued_task(
            func=Functions.download_and_convert_gif,
            args=(url, out_dir, quality, custom_params,
                  download_config['use_aria2c'],
                  download_config['aria2c_connections']),
            task_type=TaskType.GIF,
            title=f"动图下载 - {status_id}",
            metadata={
                'url': url,
                'out_dir': out_dir,
                'quality': quality,
                'filename': status_id,
            }
        )
        show_infobar(self, "info", title="任务已加入队列",
                     content="下载完成后自动转码为 GIF", duration=3000)

    def refresh_ytdlp_status(self):
        """yt-dlp 装好后重新检测：若已可用则关闭「未找到 yt-dlp」提示"""
        if self._ytdlp_warning_bar is not None and is_ytdlp_available():
            try:
                self._ytdlp_warning_bar.close()
            except Exception:
                pass
            self._ytdlp_warning_bar = None
