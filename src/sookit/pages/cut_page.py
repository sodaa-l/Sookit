"""
视频裁切 页面
"""
import os

from PyQt6.QtWidgets import QVBoxLayout, QGridLayout
from PyQt6.QtCore import Qt

import qfluentwidgets as qfw

from sookit.core.functions import Functions, check_ffmpeg, sanitize_filename
from sookit.pages.base import PageBase
from sookit.core.task_queue import TaskType


class CutPage(PageBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = qfw.TitleLabel("视频裁切")
        layout.addWidget(title)
        layout.addWidget(self.create_caption_label("按时间范围裁切视频片段（无损快速裁切）"))
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
        self._drop_target = self.video  # 拖放文件自动填入
        self.start_time = self.add_time_input(grid, "开始时间", 1, "例如 00:01:30 或 1:30")
        self.end_time = self.add_time_input(grid, "结束时间", 2, "例如 00:05:00 或 5:00")
        self.out = self.add_dir_row(grid, "输出目录", 3, "浏览")
        self.out.setPlaceholderText("留空则自动生成")

        layout.addLayout(grid)
        layout.addSpacing(10)

        btn = qfw.PrimaryPushButton("▶ 开始裁切")
        btn.setFixedWidth(240)
        def do_cut():
            video = self.video.text()
            if not video:
                qfw.InfoBar.warning(
                    parent=self, title="提示", content="请先选择视频文件",
                    orient=Qt.Orientation.Horizontal, isClosable=True, duration=3000)
                return
            start = self.start_time.text().strip() or "00:00"
            end = self.end_time.text().strip()
            if not end:
                qfw.InfoBar.warning(
                    parent=self, title="提示", content="请输入结束时间",
                    orient=Qt.Orientation.Horizontal, isClosable=True, duration=3000)
                return
            out = self.out.text() or os.path.splitext(video)[0] + f'_{sanitize_filename(start)}_{sanitize_filename(end)}.mkv'
            # 构建元数据
            filename = os.path.splitext(os.path.basename(video))[0]
            metadata = {
                'filename': filename,
                'video': video,
                'out': out,
                'start': start,
                'end': end,
                'input_video': video,
            }
            self.run_queued_task(
                func=Functions.cut_video,
                args=(video, out, start, end, 'copy'),
                task_type=TaskType.FFMPEG,
                title=f"裁切 - {filename}",
                metadata=metadata
            )
            qfw.InfoBar.info(parent=self, title="任务已加入队列", content="", duration=3000)
        btn.clicked.connect(do_cut)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._setup_log_area(layout)