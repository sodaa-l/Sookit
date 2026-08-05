"""
图片 + 音频 → 视频 页面
"""
import os

from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QGridLayout, QWidget, QFileDialog
from PyQt6.QtCore import Qt

import qfluentwidgets as qfw

from sookit.core.functions import Functions, check_ffmpeg
from sookit.pages.base import PageBase
from sookit.core.task_queue import TaskType


class MergePage(PageBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = qfw.TitleLabel("图片 + 音频 → 视频")
        layout.addWidget(title)
        layout.addWidget(self.create_caption_label("将图片与音频合并生成视频，支持单文件或批量处理"))
        layout.addSpacing(6)

        # 检查 ffmpeg 可用性
        if not check_ffmpeg():
            qfw.InfoBar.warning(
                parent=self, title="依赖缺失",
                content="FFmpeg 未安装，此功能不可用。请将 FFmpeg 放入 tools/ffmpeg/ 目录，或安装并添加到 PATH。",
                orient=Qt.Orientation.Horizontal, isClosable=True, duration=-1
            )

        mode_row = QHBoxLayout()
        mode_row.addWidget(qfw.BodyLabel("处理模式:"))
        self.mode_switch = qfw.ComboBox()
        self.mode_switch.addItems(["单文件模式", "批量模式"])
        self.mode_switch.setMinimumWidth(150)
        self.mode_switch.currentIndexChanged.connect(self._toggle_mode)
        mode_row.addWidget(self.mode_switch)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        self.single_widget = QWidget()
        single_grid = QGridLayout(self.single_widget)
        single_grid.setVerticalSpacing(12)
        single_grid.setColumnStretch(1, 1)

        self.img = self.add_file_row(single_grid, "图片路径", 0, "浏览图片",
            "图像文件 (*.jpg *.jpeg *.png)")
        self.audio = self.add_file_row(single_grid, "音频路径", 1, "浏览音频",
            "音频文件 (*.mp3 *.wav *.flac *.aac)")
        self.single_out = self.add_dir_row(single_grid, "输出目录", 2, "浏览")
        self.single_out.setPlaceholderText("留空则自动生成 (与音频同目录)")

        layout.addWidget(self.single_widget)

        self.batch_widget = QWidget()
        self.batch_widget.setVisible(False)
        batch_grid = QGridLayout(self.batch_widget)
        batch_grid.setVerticalSpacing(12)
        batch_grid.setColumnStretch(1, 1)

        batch_grid.addWidget(qfw.BodyLabel("图片来源"), 0, 0)
        self.batch_img = qfw.LineEdit()
        self.batch_img.setPlaceholderText("选择图片文件（所有音频共用）或图片目录（按文件名匹配）")
        batch_grid.addWidget(self.batch_img, 0, 1)
        btn_batch_img = qfw.PushButton("浏览")
        btn_batch_img.setFixedWidth(100)
        btn_batch_img.clicked.connect(self._browse_batch_img)
        batch_grid.addWidget(btn_batch_img, 0, 2)

        self.batch_audio = self.add_dir_row(batch_grid, "音频目录", 1, "浏览")
        self.batch_audio.setPlaceholderText("包含 MP3/WAV/FLAC/AAC 的目录")

        self.batch_out = self.add_dir_row(batch_grid, "输出目录", 2, "浏览")
        self.batch_out.setPlaceholderText("留空则自动在音频目录下创建 _output 文件夹")

        layout.addWidget(self.batch_widget)

        mode_row2 = QHBoxLayout()
        mode_row2.addWidget(qfw.BodyLabel("音频模式:"))
        self.audio_mode = qfw.ComboBox()
        self.audio_mode.addItems(["复制音频(快速)", "转码 48kHz/24bit FLAC(高质量)"])
        self.audio_mode.setMinimumWidth(250)
        mode_row2.addWidget(self.audio_mode)
        mode_row2.addStretch()
        layout.addLayout(mode_row2)

        layout.addSpacing(8)

        self.start_btn = qfw.PrimaryPushButton("▶ 开始合并")
        self.start_btn.setFixedWidth(240)
        self.start_btn.clicked.connect(self._start)
        layout.addWidget(self.start_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self._setup_log_area(layout)

    def _browse_batch_img(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择图片文件", "",
            "图像文件 (*.jpg *.jpeg *.png);;所有文件 (*)")
        if path:
            self.batch_img.setText(path)
            return
        path = QFileDialog.getExistingDirectory(self, "选择图片目录")
        if path:
            self.batch_img.setText(path)

    def _toggle_mode(self, idx):
        is_batch = (idx == 1)
        self.single_widget.setVisible(not is_batch)
        self.batch_widget.setVisible(is_batch)
        self.start_btn.setText("▶ 开始批量合并" if is_batch else "▶ 开始合并")

    def _start(self):
        audio_mode = 'transcode' if self.audio_mode.currentIndex() == 1 else 'copy'
        if self.mode_switch.currentIndex() == 0:
            # 单文件模式
            img = self.img.text().strip()
            audio = self.audio.text().strip()
            if not img or not audio:
                qfw.InfoBar.warning(
                    parent=self, title="提示", content="请选择图片和音频文件",
                    orient=Qt.Orientation.Horizontal, isClosable=True, duration=3000)
                return
            output = self.single_out.text().strip() or os.path.splitext(audio)[0] + '.mkv'
            filename = os.path.splitext(os.path.basename(audio))[0]
            metadata = {
                'filename': filename,
                'img': img,
                'audio': audio,
                'output': output,
                'audio_mode': audio_mode,
                'input_img': img,
            }
            self.run_queued_task(
                func=Functions.merge_image_audio,
                args=(img, audio, output, audio_mode),
                task_type=TaskType.FFMPEG,
                title=f"合并 - {filename}",
                metadata=metadata
            )
            qfw.InfoBar.info(parent=self, title="任务已加入队列", content="", duration=3000)
        else:
            # 批量模式
            batch_img = self.batch_img.text().strip()
            batch_audio = self.batch_audio.text().strip()
            if not batch_img or not batch_audio:
                qfw.InfoBar.warning(
                    parent=self, title="提示", content="请选择图片来源和音频目录",
                    orient=Qt.Orientation.Horizontal, isClosable=True, duration=3000)
                return
            out_dir = self.batch_out.text().strip() or \
                os.path.join(batch_audio or '.', '_output')
            filename = os.path.basename(batch_audio)
            # 批量模式：batch_img 可能是文件或目录，解析出具体图片路径做缩略图
            batch_img_val = self.batch_img.text().strip()
            if os.path.isfile(batch_img_val):
                first_img = batch_img_val
            elif os.path.isdir(batch_img_val):
                images = [f for f in os.listdir(batch_img_val)
                          if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.webp'))]
                first_img = os.path.join(batch_img_val, sorted(images)[0]) if images else batch_img_val
            else:
                first_img = batch_img_val
            metadata = {
                'filename': filename,
                'batch_img': batch_img_val,
                'batch_audio': batch_audio,
                'out_dir': out_dir,
                'audio_mode': audio_mode,
                'input_img': first_img,
            }
            self.run_queued_task(
                func=Functions.batch_merge_image_audio,
                args=(batch_img, batch_audio, out_dir, audio_mode),
                task_type=TaskType.FFMPEG,
                title=f"批量合并 - {filename}",
                metadata=metadata
            )
            qfw.InfoBar.info(parent=self, title="任务已加入队列", content="", duration=3000)