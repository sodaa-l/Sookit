"""
X Space 音频下载 页面
"""
from PyQt6.QtWidgets import QVBoxLayout, QGridLayout
from PyQt6.QtCore import Qt

import qfluentwidgets as qfw

from sookit.core.functions import Functions, is_ytdlp_available, DEFAULT_OUTPUT_DIR, ensure_output_dir
from sookit.pages.base import PageBase
from sookit.core.task_queue import TaskType


class XSpacePage(PageBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = qfw.TitleLabel("X Space 音频下载")
        layout.addWidget(title)
        layout.addWidget(self.create_caption_label("粘贴 X Space 链接，下载音频回放"))
        layout.addSpacing(10)

        # 检查 yt-dlp 可用性（PATH 全局或内置 tools/ 均可）
        if not is_ytdlp_available():
            qfw.InfoBar.warning(
                parent=self, title="依赖缺失",
                content="未找到 yt-dlp，X Space 功能不可用。请前往设置页下载安装",
                orient=Qt.Orientation.Horizontal, isClosable=True, duration=-1
            )

        grid = QGridLayout()
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)

        grid.addWidget(qfw.BodyLabel("Space 链接"), 0, 0)
        self.url_input = qfw.LineEdit()
        self.url_input.setPlaceholderText("https://x.com/.../spaces/... 或 https://twitter.com/.../spaces/...")
        grid.addWidget(self.url_input, 0, 1)

        self.out_dir = self.add_dir_row(grid, "输出目录", 1, "浏览")
        self.out_dir.setPlaceholderText(f"默认: /下载")

        grid.addWidget(qfw.BodyLabel("音频格式"), 2, 0)
        self.fmt_combo = qfw.ComboBox()
        self.fmt_combo.addItems(["M4A", "MP3", "AAC", "OPUS"])
        self.fmt_combo.setMinimumWidth(120)
        grid.addWidget(self.fmt_combo, 2, 1)

        layout.addLayout(grid)
        layout.addSpacing(10)

        btn = qfw.PrimaryPushButton("▶ 开始下载")
        btn.setFixedWidth(240)
        def do_download():
            url = self.url_input.text().strip()
            if not url:
                qfw.InfoBar.warning(
                    parent=self, title="提示", content="请先输入 Space 链接",
                    orient=Qt.Orientation.Horizontal, isClosable=True, duration=3000)
                return
            out_dir = self.out_dir.text().strip() or DEFAULT_OUTPUT_DIR
            fmt = self.fmt_combo.currentText().lower()
            # 构建元数据
            metadata = {
                'url': url,
                'out_dir': out_dir,
                'fmt': fmt,
            }
            # 从 URL 提取标题（简化）
            import re
            title_match = re.search(r'/spaces/([^/?]+)', url)
            title = f"X Space - {title_match.group(1)}" if title_match else "X Space 下载"
            self.run_queued_task(
                func=Functions.download_xspace,
                args=(url, out_dir, fmt),
                task_type=TaskType.YTDLP,
                title=title,
                metadata=metadata
            )
            qfw.InfoBar.info(parent=self, title="任务已加入队列", content="", duration=3000)
        btn.clicked.connect(do_download)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._setup_log_area(layout)