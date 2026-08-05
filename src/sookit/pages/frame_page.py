"""
帧提取 页面
"""
import os

from PyQt6.QtWidgets import QVBoxLayout, QGridLayout
from PyQt6.QtCore import Qt

import qfluentwidgets as qfw

from sookit.core.functions import Functions, check_ffmpeg, sanitize_filename
from sookit.pages.base import PageBase


class FramePage(PageBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = qfw.TitleLabel("帧提取")
        layout.addWidget(title)
        layout.addWidget(self.create_caption_label("从视频中提取指定时间点的帧为图片"))
        if not check_ffmpeg():
            qfw.InfoBar.warning(
                parent=self, title="依赖缺失",
                content="FFmpeg 未安装，此功能不可用。请将 FFmpeg 放入 tools/ffmpeg/ 目录",
                orient=Qt.Orientation.Horizontal, isClosable=True, duration=-1)
        layout.addSpacing(10)

        grid = QGridLayout()
        grid.setVerticalSpacing(12)
        grid.setColumnStretch(1, 1)

        self.video = self.add_file_row(grid, "视频文件", 0, "浏览视频",
            "视频文件 (*.mp4 *.mkv *.avi *.mov)")
        self._drop_target = self.video  # 拖放文件自动填入
        self.time_input = self.add_time_input(grid, "时间点", 1, "例如 00:01:30 或 1:30")
        self.out = self.add_dir_row(grid, "输出目录", 2, "浏览")
        self.out.setPlaceholderText("留空则自动生成")

        # 图片格式选择
        self.fmt_combo = qfw.ComboBox()
        self.fmt_combo.addItems(["png", "jpg", "jpeg", "bmp", "webp", "tiff"])
        self.fmt_combo.setCurrentText("png")
        grid.addWidget(self.create_caption_label("图片格式"), 3, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addWidget(self.fmt_combo, 3, 1)

        layout.addLayout(grid)
        layout.addSpacing(10)

        btn = qfw.PrimaryPushButton("▶ 提取帧")
        btn.setFixedWidth(240)
        def do_extract():
            video = self.video.text()
            if not video:
                qfw.InfoBar.warning(
                    parent=self, title="提示", content="请先选择视频文件",
                    orient=Qt.Orientation.Horizontal, isClosable=True, duration=3000)
                return
            t = self.time_input.text().strip()
            if not t:
                qfw.InfoBar.warning(
                    parent=self, title="提示", content="请输入时间点",
                    orient=Qt.Orientation.Horizontal, isClosable=True, duration=3000)
                return
            fmt = self.fmt_combo.currentText()
            # 清理非法字符，生成合法文件名
            out = self.out.text() or os.path.splitext(video)[0] + f'_{sanitize_filename(t)}.{fmt}'
            self.run_task(Functions.extract_frame, (video, t, out, fmt))
        btn.clicked.connect(do_extract)
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._setup_log_area(layout)