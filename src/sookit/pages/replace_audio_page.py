"""
音频覆盖 页面
"""
import os

from PyQt6.QtWidgets import QVBoxLayout, QGridLayout
from PyQt6.QtCore import Qt

import qfluentwidgets as qfw

from sookit.core.functions import Functions, check_ffmpeg
from sookit.pages.base import PageBase
from sookit.core.task_queue import TaskType


class ReplaceAudioPage(PageBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = qfw.TitleLabel("音频覆盖")
        layout.addWidget(title)
        layout.addWidget(self.create_caption_label("用外部音频替换视频中的原始音轨"))
        layout.addSpacing(10)

        # 检查 ffmpeg 可用性
        if not check_ffmpeg():
            qfw.InfoBar.warning(
                parent=self, title="依赖缺失",
                content="FFmpeg 未安装，此功能不可用。请将 FFmpeg 放入 tools/ffmpeg/ 目录，或安装并添加到 PATH。",
                orient=Qt.Orientation.Horizontal, isClosable=True, duration=-1
            )

        grid = QGridLayout()
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)

        self.video = self.add_file_row(grid, "视频文件", 0, "浏览视频",
            "视频文件 (*.mp4 *.mkv *.avi *.mov)")
        self.audio = self.add_file_row(grid, "音频文件", 1, "浏览音频",
            "音频文件 (*.mp3 *.wav *.flac *.aac)")
        self.out = self.add_dir_row(grid, "输出目录", 2, "浏览")
        self.out.setPlaceholderText("留空则自动生成")

        grid.addWidget(qfw.BodyLabel("模式"), 3, 0)
        self.mode = qfw.ComboBox()
        self.mode.addItems(["直接覆盖(快速)", "转码为 FLAC 后覆盖(高质量)"])
        self.mode.setMinimumWidth(280)
        grid.addWidget(self.mode, 3, 1)

        layout.addLayout(grid)
        layout.addSpacing(10)

        btn = qfw.PrimaryPushButton("▶ 覆盖音频")
        btn.setFixedWidth(240)
        btn.clicked.connect(lambda: self._start_replace())
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._setup_log_area(layout)

    def _start_replace(self):
        video = self.video.text().strip()
        audio = self.audio.text().strip()
        if not video or not audio:
            qfw.InfoBar.warning(
                parent=self, title="提示", content="请选择视频和音频文件",
                orient=Qt.Orientation.Horizontal, isClosable=True, duration=3000)
            return
        out_raw = self.out.text().strip()
        if out_raw:
            if os.path.isdir(out_raw) or not os.path.splitext(out_raw)[1]:
                base = os.path.splitext(os.path.basename(video))[0]
                out = os.path.join(out_raw, f"{base}_newaudio.mkv")
            else:
                out = out_raw
        else:
            out = os.path.splitext(video)[0] + '_newaudio.mkv'
        mode = 'direct' if self.mode.currentIndex() == 0 else 'transcode'
        filename = os.path.splitext(os.path.basename(video))[0]
        metadata = {
            'filename': filename,
            'video': video,
            'audio': audio,
            'out': out,
            'mode': mode,
            'input_video': video,
        }
        self.run_queued_task(
            func=Functions.replace_audio,
            args=(video, audio, out, mode),
            task_type=TaskType.FFMPEG,
            title=f"音频覆盖 - {filename}",
            metadata=metadata
        )
        qfw.InfoBar.info(parent=self, title="任务已加入队列", content="", duration=3000)