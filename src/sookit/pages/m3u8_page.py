"""
M3U8 → AAC / M4A 页面
"""
from PyQt6.QtWidgets import QVBoxLayout, QGridLayout
from PyQt6.QtCore import Qt

import qfluentwidgets as qfw

from sookit.core.functions import Functions, check_ffmpeg
from sookit.pages.base import PageBase
from sookit.core.task_queue import TaskType


class M3U8Page(PageBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = qfw.TitleLabel("M3U8 → AAC / M4A")
        layout.addWidget(title)
        layout.addWidget(self.create_caption_label("下载 M3U8 直播流/视频流并转为 AAC 音频"))
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

        self.url = self.add_file_row(grid, "M3U8 链接/文件", 0, "浏览",
            "M3U8文件 (*.m3u8);;所有文件 (*)")
        self._drop_target = self.url  # 拖放文件自动填入
        self.out = self.add_dir_row(grid, "输出目录", 1, "浏览")
        self.out.setPlaceholderText("留空则生成到当前目录")

        layout.addLayout(grid)
        layout.addSpacing(10)

        btn = qfw.PrimaryPushButton("▶ 开始转换")
        btn.setFixedWidth(240)
        btn.clicked.connect(lambda: self._start_download())
        layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._setup_log_area(layout)

    def _start_download(self):
        url = self.url.text().strip()
        if not url:
            qfw.InfoBar.warning(
                parent=self, title="提示", content="请输入 M3U8 链接或文件路径",
                orient=Qt.Orientation.Horizontal, isClosable=True, duration=3000)
            return
        out_raw = self.out.text().strip()
        if out_raw:
            if os.path.isdir(out_raw) or not os.path.splitext(out_raw)[1]:
                base = os.path.splitext(os.path.basename(url))[0] or 'output'
                out = os.path.join(out_raw, f"{base}.m4a")
            else:
                out = out_raw
        else:
            out = 'output.m4a'
        # 构建元数据
        import os
        filename = os.path.splitext(os.path.basename(url))[0]
        metadata = {
            'filename': filename,
            'url': url,
            'out': out,
        }
        self.run_queued_task(
            func=Functions.m3u8_to_aac,
            args=(url, out, '320k'),
            task_type=TaskType.M3U8,
            title=filename or "M3U8 下载",
            metadata=metadata
        )
        qfw.InfoBar.info(parent=self, title="任务已加入队列", content="", duration=3000)