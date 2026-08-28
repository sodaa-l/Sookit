"""
音频提取 页面
"""
import os

from PyQt6.QtWidgets import QVBoxLayout, QGridLayout
from PyQt6.QtCore import Qt

import qfluentwidgets as qfw

from sookit.core.functions import Functions, check_ffmpeg
from sookit.pages.base import PageBase
from sookit.widgets.infobar import show_infobar
from sookit.core.task_queue import TaskType


class ExtractAudioPage(PageBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = qfw.TitleLabel("音频提取")
        layout.addWidget(title)
        layout.addWidget(self.create_caption_label("从视频文件中提取原始音频流"))
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
        self._drop_target = self.video  # 拖放文件自动填入
        self.out = self.add_dir_row(grid, "输出目录", 1, "浏览")
        self.out.setPlaceholderText("留空则自动生成 (与视频同目录)")

        layout.addLayout(grid)
        layout.addSpacing(10)

        btn = qfw.PrimaryPushButton("▶ 提取音频")
        btn.setFixedWidth(240)
        btn.clicked.connect(lambda: self._start_extract())
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._setup_log_area(layout)

    def _start_extract(self):
        video = self.video.text().strip()
        if not video:
            show_infobar(self, "warning", title="提示", content="请选择视频文件",
                         duration=3000)
            return
        out_raw = self.out.text().strip()
        if out_raw:
            if os.path.isdir(out_raw) or not os.path.splitext(out_raw)[1]:
                base = os.path.splitext(os.path.basename(video))[0]
                out = os.path.join(out_raw, f"{base}.m4a")
            else:
                out = out_raw
        else:
            out = os.path.splitext(video)[0] + '.m4a'
        filename = os.path.splitext(os.path.basename(video))[0]
        metadata = {
            'filename': filename,
            'video': video,
            'out': out,
        }
        self.run_queued_task(
            func=Functions.extract_audio,
            args=(video, out),
            task_type=TaskType.FFMPEG,
            title=f"音频提取 - {filename}",
            metadata=metadata
        )
        show_infobar(self, "info", title="任务已加入队列", content="", duration=3000)