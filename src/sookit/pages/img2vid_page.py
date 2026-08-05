"""
图片 → 10秒视频 页面
"""
import os

from PyQt6.QtWidgets import QVBoxLayout, QGridLayout
from PyQt6.QtCore import Qt

import qfluentwidgets as qfw

from sookit.core.functions import Functions, check_ffmpeg
from sookit.pages.base import PageBase
from sookit.core.task_queue import TaskType


class Img2VidPage(PageBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = qfw.TitleLabel("图片 → 10秒视频")
        layout.addWidget(title)
        layout.addWidget(self.create_caption_label("将静态图片转为 10 秒短视频，循环播放"))
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

        self.img = self.add_file_row(grid, "图片路径", 0, "浏览图片",
            "图像文件 (*.jpg *.jpeg *.png)")
        self._drop_target = self.img  # 拖放文件自动填入
        self.out = self.add_dir_row(grid, "输出目录", 1, "浏览")
        self.out.setPlaceholderText("留空则自动生成 (与图片同目录)")

        layout.addLayout(grid)
        layout.addSpacing(10)

        btn = qfw.PrimaryPushButton("▶ 生成 10 秒视频")
        btn.setFixedWidth(240)
        btn.clicked.connect(lambda: self._start_convert())
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._setup_log_area(layout)

    def _start_convert(self):
        img = self.img.text().strip()
        if not img:
            qfw.InfoBar.warning(
                parent=self, title="提示", content="请选择图片文件",
                orient=Qt.Orientation.Horizontal, isClosable=True, duration=3000)
            return
        out_raw = self.out.text().strip()
        if out_raw:
            if os.path.isdir(out_raw) or not os.path.splitext(out_raw)[1]:
                base = os.path.splitext(os.path.basename(img))[0]
                out = os.path.join(out_raw, f"{base}_10s.mp4")
            else:
                out = out_raw
        else:
            out = os.path.splitext(img)[0] + '_10s.mp4'
        filename = os.path.splitext(os.path.basename(img))[0]
        metadata = {
            'filename': filename,
            'img': img,
            'out': out,
            'input_img': img,
        }
        self.run_queued_task(
            func=Functions.img2vid_10s,
            args=(img, out, 10, 30),
            task_type=TaskType.FFMPEG,
            title=f"图片转视频 - {filename}",
            metadata=metadata
        )
        qfw.InfoBar.info(parent=self, title="任务已加入队列", content="", duration=3000)