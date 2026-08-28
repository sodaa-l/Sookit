"""
字幕烧录 页面
"""
import os

from PyQt6.QtWidgets import QVBoxLayout, QGridLayout
from PyQt6.QtCore import Qt

import qfluentwidgets as qfw

from sookit.core.functions import Functions, check_ffmpeg
from sookit.pages.base import PageBase
from sookit.widgets.infobar import show_infobar
from sookit.core.task_queue import TaskType


class SubtitlePage(PageBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = qfw.TitleLabel("字幕烧录")
        layout.addWidget(title)
        layout.addWidget(self.create_caption_label("将 ASS/SRT 字幕永久烧录到视频画面中"))
        layout.addSpacing(10)

        # 检查 ffmpeg 可用性
        if not check_ffmpeg():
            show_infobar(self, "warning", title="依赖缺失",
                                 content="FFmpeg 未安装，此功能不可用。请将 FFmpeg 放入 tools/ffmpeg/ 目录，或安装并添加到 PATH。",
                                 duration=-1)

        grid = QGridLayout()
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)

        self.video = self.add_file_row(grid, "视频文件", 0, "浏览视频",
            "视频文件 (*.mp4 *.mkv *.avi *.mov)")
        self.sub = self.add_file_row(grid, "字幕文件", 1, "浏览字幕",
            "字幕文件 (*.ass *.srt);;所有文件 (*)")
        self.out = self.add_dir_row(grid, "输出目录", 2, "浏览")
        self.out.setPlaceholderText("留空则自动生成")

        layout.addLayout(grid)
        layout.addSpacing(10)

        btn = qfw.PrimaryPushButton("▶ 开始烧录")
        btn.setFixedWidth(240)
        btn.clicked.connect(lambda: self._start_burn())
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._setup_log_area(layout)

    def _start_burn(self):
        video = self.video.text().strip()
        sub = self.sub.text().strip()
        if not video or not sub:
            show_infobar(self, "warning", title="提示", content="请选择视频和字幕文件",
                         duration=3000)
            return
        # 处理输出路径：如果只选了目录，自动拼接文件名
        out_raw = self.out.text().strip()
        if out_raw:
            if os.path.isdir(out_raw) or not os.path.splitext(out_raw)[1]:
                base = os.path.splitext(os.path.basename(video))[0]
                out = os.path.join(out_raw, f"{base}_sub.mkv")
            else:
                out = out_raw
        else:
            out = os.path.splitext(video)[0] + '_sub.mkv'
        # 构建元数据
        filename = os.path.splitext(os.path.basename(video))[0]
        metadata = {
            'filename': filename,
            'video': video,
            'sub': sub,
            'out': out,
            'input_video': video,
        }
        self.run_queued_task(
            func=Functions.burn_subtitles,
            args=(video, sub, out, 'software'),
            task_type=TaskType.FFMPEG,
            title=f"字幕烧录 - {filename}",
            metadata=metadata
        )
        show_infobar(self, "info", title="任务已加入队列", content="", duration=3000)