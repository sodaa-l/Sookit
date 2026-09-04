"""
视频嗅探下载 页面
"""
import os
import re
import glob
import urllib.request
from urllib.parse import urlparse

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QSplitter, QWidget, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt, QThread, QTimer, QRectF, QByteArray, pyqtSignal

from sookit.core.workers import GenericWorker
from PyQt6.QtGui import QFont, QPixmap, QImage, QPainter, QColor, QPainterPath

import qfluentwidgets as qfw

from sookit.core.functions import (
    Functions, FormatType, is_ytdlp_available, extract_youtube_id, format_duration, build_thumbnails,
    load_download_config, DEFAULT_OUTPUT_DIR, ensure_output_dir
)
from sookit.widgets.cover_image import CoverImageWidget
from sookit.widgets.combo_box import StaticComboBox
from sookit.widgets.infobar import show_infobar
from sookit.pages.base import PageBase
from sookit.core.task_queue import TaskType
from sookit.core.ffmpeg_utils import run_ytdlp
from sookit.core.ytdlp_utils import build_ytdlp_cmd
from sookit.core.utils import get_certifi_ssl_context
from sookit.paths import get_cover_dir


def _sniff_image_format(path):
    """读文件头魔数检测实际图片格式（仅兜底失败时用于日志说明，不做逻辑分支依据）"""
    try:
        with open(path, 'rb') as f:
            head = f.read(32)
    except OSError:
        return '未知'
    if head[:3] == b'\xff\xd8\xff':
        return 'JPEG'
    if head[:8] == b'\x89PNG\r\n\x1a\n':
        return 'PNG'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'WEBP'
    if len(head) >= 12 and head[4:8] == b'ftyp':
        brand = head[8:12].decode('ascii', errors='replace').lower()
        if brand.startswith('avi'):
            return 'AVIF'
        if brand in ('heic', 'heix', 'mif1', 'msf1', 'hevc', 'hevx'):
            return 'HEIF/HEIC'
        return brand.upper()
    if head[:4] in (b'II*\x00', b'MM\x00*'):
        return 'TIFF'
    if head[:2] == b'BM':
        return 'BMP'
    if head[:2] == b'\xff\x0a' or head[:12] == b'\x00\x00\x00\x0cJXL \r\n\x87\n':
        return 'JPEG XL'
    if head[:1] == b'<' and (b'<svg' in head[:64] or head[:5] == b'<?xml'):
        return 'SVG'
    if head[:4] == b'\x00\x00\x01\x00':
        return 'ICO'
    return '未知'


def _find_fallback_product(cache_dir, name):
    """遍历 fallback_{name}.* 兜底产物（排除 .img 原始落盘），返回第一个 QImage 可解的路径"""
    for p in glob.glob(os.path.join(str(cache_dir), f"fallback_{name}.*")):
        if p.endswith('.img'):
            continue
        if not QImage(p).isNull():
            return p
    return None


class YouTubePage(PageBase):
    """视频嗅探与下载——封面和格式左右分栏布局"""
    def __init__(self, parent=None):
        super().__init__(parent)

        # ---- 内部状态 ----
        self._formats_data = []
        self._sniff_timed_out = False
        self._sniff_video_id = ''
        self._sniff_title = ''
        self._sniff_channel = ''
        self._sniff_duration = 0
        self._last_sniff_url = ''
        self._sniff_worker = None
        self._cover_loader_worker = None
        self._cover_download_worker = None
        self._cover_data = None  # 封面原始字节数据（供任务队列复用）
        self._sniff_thumbs = []  # 嗅探得到的封面列表（_populate_cover_combo 有效分支时保存，供入队复用）
        self._cover_fallback_worker = None  # 最终兜底 worker（落盘下载 + ffmpeg 转码）
        self._fallback_tried_urls = set()   # 已尝试过兜底的 URL（同 URL 会话内只试一次，防循环）
        self._ytdlp_warning_bar = None  # 「未找到 yt-dlp」常驻 infobar，装好后关闭
        self._format_updating = False  # 格式表填充/互斥反勾期间守卫，防止 itemChanged 递归误触发

        # ---- 主布局 ----
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = qfw.TitleLabel("视频嗅探下载")
        layout.addWidget(title)
        layout.addWidget(self.create_caption_label("粘贴视频链接，嗅探所有可用格式，自由选择下载"))
        layout.addSpacing(6)

        # 检查 yt-dlp 可用性（PATH 全局或内置 tools/ 均可）
        if not is_ytdlp_available():
            self._ytdlp_warning_bar = show_infobar(self, "warning", title="依赖缺失",
                                                   content="未找到 yt-dlp，嗅探功能不可用。请前往设置页下载安装")
            self.add_goto_settings_button(self._ytdlp_warning_bar)

        # ---- URL 输入行 ----
        url_row = QHBoxLayout()
        self.url_input = qfw.LineEdit()
        self.url_input.setPlaceholderText("粘贴视频链接...")
        # 蓝色嗅探按钮
        self.sniff_btn = qfw.PrimaryPushButton("嗅探")
        self.sniff_btn.setFixedWidth(100)
        self.sniff_btn.clicked.connect(self.do_sniff)
        # 灰色停止嗅探按钮（初始隐藏）
        self.stop_sniff_btn = qfw.PushButton("停止嗅探")
        self.stop_sniff_btn.setFixedWidth(100)
        self.stop_sniff_btn.clicked.connect(self.stop_sniff)
        self.stop_sniff_btn.setVisible(False)
        url_row.addWidget(self.url_input)
        url_row.addWidget(self.sniff_btn)
        url_row.addWidget(self.stop_sniff_btn)
        self.remote_cb = qfw.CheckBox("增强嗅探 (使用 ejs)")
        url_row.addWidget(self.remote_cb)
        layout.addLayout(url_row)

        # ---- 左右分栏 ----
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # ====== 左面板：封面 + 标题 ======
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 8, 4)
        left_layout.setSpacing(6)

        # 视频标题 + 频道/时长
        self.cover_title = qfw.BodyLabel("")
        self.cover_title.setFont(QFont("Microsoft YaHei", 12, QFont.Weight.Bold))
        self.cover_title.setWordWrap(True)
        self.cover_title.setMinimumHeight(40)
        left_layout.addWidget(self.cover_title)
        self.cover_meta = self.create_caption_label("")
        self.cover_meta.setFont(QFont("Microsoft YaHei", 10))
        self.cover_meta.setWordWrap(True)
        left_layout.addWidget(self.cover_meta)

        # 封面图片显示区域（自绘，始终 16:9，无黑边）
        self.cover_label = CoverImageWidget()
        left_layout.addWidget(self.cover_label, stretch=1)

        # 封面分辨率选择 + 下载按钮
        cover_control = QHBoxLayout()
        cover_control.addWidget(qfw.BodyLabel("封面分辨率:"))
        self.cover_combo = StaticComboBox()
        self.cover_combo.setMinimumWidth(180)
        self.cover_combo.setEnabled(False)
        cover_control.addWidget(self.cover_combo)
        cover_control.addSpacing(6)
        self.cover_btn = qfw.PushButton("下载封面")
        self.cover_btn.setEnabled(False)
        self.cover_btn.clicked.connect(self._download_cover)
        cover_control.addWidget(self.cover_btn)
        cover_control.addStretch()
        left_layout.addLayout(cover_control)

        # 下载目录行（保存到目录 + 浏览按钮）
        download_dir_row = QHBoxLayout()
        download_dir_row.addWidget(qfw.BodyLabel("保存目录:"))
        self.out_dir = qfw.LineEdit()
        self.out_dir.setPlaceholderText("下载保存目录 (默认: /下载)")
        browse_btn = qfw.PushButton("浏览")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(lambda: self.browse_dir(self.out_dir))
        download_dir_row.addWidget(self.out_dir, stretch=1)
        download_dir_row.addWidget(browse_btn)
        left_layout.addLayout(download_dir_row)

        # ====== 右面板：格式表格 + 下载控制 ======
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 4, 4, 4)
        right_layout.setSpacing(6)

        # 格式表格 —— 自定义子类，resize 时按比例分配列宽
        class _FormatTable(qfw.TableWidget):
            COL_FACTORS = [44, 90, 80, 100, 55]
            COL_MIN = 50
            def resizeEvent(self, event):
                super().resizeEvent(event)
                self._distribute_columns()
            def _distribute_columns(self):
                total = sum(self.COL_FACTORS)
                avail = self.viewport().width()
                hdr = self.horizontalHeader()
                hdr.setStretchLastSection(False)
                for i in range(self.columnCount()):
                    hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
                for i, f in enumerate(self.COL_FACTORS):
                    w = max(int(avail * f / total), self.COL_MIN)
                    self.setColumnWidth(i, w)
        self.format_table = _FormatTable()
        self.format_table.setColumnCount(5)
        self.format_table.setHorizontalHeaderLabels(["选择", "类型", "质量", "编码", "语言"])
        self.format_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.format_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.format_table.setAlternatingRowColors(True)
        self.format_table.setMinimumHeight(150)
        self._fix_format_table_columns()
        # 同类型互斥勾选：勾选某项时自动取消同类型其他已勾项（"其他"类型不限）
        self.format_table.itemChanged.connect(self._on_format_item_changed)
        right_layout.addWidget(self.format_table, stretch=1)

        # 下载按钮
        self.download_btn = qfw.PrimaryPushButton("▶ 下载选中格式")
        self.download_btn.setFixedWidth(240)
        self.download_btn.clicked.connect(self.do_download)
        right_layout.addWidget(self.download_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        # 组装 splitter
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 2)   # 左面板占比 2
        splitter.setStretchFactor(1, 4)   # 右面板占比 4（给表格更多空间）
        splitter.setMinimumHeight(250)
        layout.addWidget(splitter, stretch=1)

        # 日志区域
        self._setup_log_area(layout)

        # 监听队列下载失败，弹常显错误提示（需手动关闭）
        from sookit.core.task_queue import TaskQueueManager
        TaskQueueManager.instance().task_failed.connect(self._on_queue_task_failed)

    def refresh_ytdlp_status(self):
        """yt-dlp 装好后重新检测：若已可用则关闭「未找到 yt-dlp」提示"""
        if self._ytdlp_warning_bar is not None and is_ytdlp_available():
            try:
                self._ytdlp_warning_bar.close()
            except Exception:
                pass
            self._ytdlp_warning_bar = None

    def _on_queue_task_failed(self, task):
        """队列下载失败 → 弹常显错误提示（不自动消失，需手动关闭）"""
        title = task.title or "下载任务失败"
        content = task.error or "任务执行失败，请查看日志"
        if len(content) > 200:
            content = content[:200] + "…"
        show_infobar(self, "error", title=title, content=content)

    # -------- 嗅探 --------
    def do_sniff(self):
        url = self.url_input.text().strip()
        if not url:
            show_infobar(self, "warning", title="提示", content="请先输入视频链接",
                         duration=3000)
            return
        # 切换按钮：隐藏蓝色嗅探，显示灰色停止嗅探
        self.sniff_btn.setVisible(False)
        self.stop_sniff_btn.setVisible(True)
        self.format_table.setRowCount(0)
        self._formats_data = []
        self._sniff_timed_out = False
        self._last_sniff_url = url
        # 清空封面
        self.cover_label.clearPixmap("嗅探中...")
        self.cover_title.setText("")

        # 立即从 URL 提取 video_id，同步加载封面
        video_id = extract_youtube_id(url)
        if video_id:
            self._sniff_video_id = video_id
            # 立即构建封面分辨率列表并填充下拉框
            thumbs = build_thumbnails(video_id)
            if thumbs:
                self._populate_cover_combo(thumbs)
                self.log(f"找到 {len(thumbs)} 个封面分辨率")
            # 立即启动封面加载（异步）
            self._load_cover_image(video_id)

        self._sniff_worker = GenericWorker(
            Functions.sniff_youtube, args=(url,), kwargs={'log': None})
        self._sniff_worker.done.connect(self._on_sniff_done)
        self._sniff_worker.error.connect(self._on_sniff_error)
        self._sniff_worker.start()
        self.log("▶ 正在嗅探视频信息...")
        self._sniff_timer = QTimer(self)
        self._sniff_timer.setSingleShot(True)
        self._sniff_timer.timeout.connect(self._sniff_timeout)
        self._sniff_timer.start(60000)

    def stop_sniff(self):
        if hasattr(self, '_sniff_timer') and self._sniff_timer and self._sniff_timer.isActive():
            self._sniff_timer.stop()
        if self._sniff_worker and self._sniff_worker.isRunning():
            self._sniff_worker.request_stop()
            self._sniff_worker.wait(1000)
            self._reset_sniff_btn()
            self.log("嗅探已停止")
            show_infobar(self, "info", title="已停止", content="嗅探已停止",
                         duration=3000)

    def _reset_sniff_btn(self):
        # 切换按钮：显示蓝色嗅探，隐藏灰色停止嗅探
        self.stop_sniff_btn.setVisible(False)
        self.sniff_btn.setVisible(True)

    def _on_sniff_done(self, info):
        if hasattr(self, '_sniff_timer') and self._sniff_timer and self._sniff_timer.isActive():
            self._sniff_timer.stop()
        self._reset_sniff_btn()
        if self._sniff_timed_out:
            return

        # 保存嗅探数据（video_id 可能已在 do_sniff 中设置）
        # 仅 YouTube 赋值，避免非 YouTube 的站点 ID（如 B 站 BV 号）污染 _sniff_video_id
        # （其消费方：_download_cover 文件名兜底、入队 cover_url、_on_sniff_failed 兜底加载）
        if not self._sniff_video_id:
            if (info.get('extractor') or '').lower().startswith('youtube'):
                self._sniff_video_id = info.get('id', '') or extract_youtube_id(self._last_sniff_url)
        self._sniff_title = info.get('title', '')
        self._sniff_channel = info.get('channel', '')
        self._sniff_duration = info.get('duration', 0)

        # ---- 左面板：显示标题 + 频道/时长 ----
        self.cover_title.setText(info.get('title', ''))
        duration = info.get('duration', 0)
        dur_str = format_duration(duration) if duration else '未知'
        self.cover_meta.setText(f"频道: {info.get('channel', '未知')}    时长: {dur_str}")

        # 如果嗅探结果中有更准确的封面信息，更新封面分辨率下拉框
        thumbs = info.get('thumbnails', [])
        if thumbs:
            self._populate_cover_combo(thumbs)

        # 嗅探前未显示封面的场景（非 YouTube 嗅探前无从取 URL）：
        # 嗅探完成后加载最佳封面显示（YouTube 嗅探前已显示，此处自动跳过）。
        # 已有进行中的加载线程时不再并发补载：isNull 只反映显示状态，快速路径
        # 的成功信号可能还在主线程队列里，此时并发补载会在其成功后留下一个
        # 冗余下载，晚到的失败会把刚显示的封面清掉
        loader_busy = (self._cover_loader_worker is not None
                       and self._cover_loader_worker.isRunning())
        if thumbs and self.cover_label._pixmap.isNull() and not loader_busy:
            self._load_cover_from_url(thumbs[0]['url'])

        # 填充格式表格
        fmts = info.get('formats', [])
        if not fmts:
            self._formats_data = []
            self.format_table.setRowCount(0)
            self.log("该视频暂无可用下载格式，但仍可下载封面")
            show_infobar(self, "info", title="提示",
                         content="暂无可用下载格式，但仍可下载封面", duration=4000)
            return

        sorted_fmts = sorted(fmts, key=self._format_sort_key)
        self._formats_data = sorted_fmts
        self.format_table.setRowCount(len(sorted_fmts))

        # 填充期间屏蔽互斥处理（setItem/setCheckState 会触发 itemChanged）
        self._format_updating = True
        try:
            for row, fmt in enumerate(sorted_fmts):
                item = QTableWidgetItem()
                item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                is_best = (fmt['type'] == '视频+音频' and '1080' in fmt['quality']) or \
                          (fmt['type'] == '视频+音频' and row == 0)
                item.setCheckState(Qt.CheckState.Checked if is_best else Qt.CheckState.Unchecked)
                self.format_table.setItem(row, 0, item)
                self.format_table.setItem(row, 1, QTableWidgetItem(fmt['type']))
                self.format_table.setItem(row, 2, QTableWidgetItem(fmt['quality']))
                codec = fmt.get('vcodec', '')
                if fmt['type'] == '音频流':
                    codec = fmt.get('acodec', '')
                elif fmt['type'] == '视频+音频':
                    v = fmt.get('vcodec', '')
                    a = fmt.get('acodec', '')
                    codec = f"{v}+{a}" if v and a else (v or a)
                self.format_table.setItem(row, 3, QTableWidgetItem(codec))
                lang = fmt.get('language', '')
                self.format_table.setItem(row, 4, QTableWidgetItem(lang))
        finally:
            self._format_updating = False

        # 填充完成后强制恢复列宽（Fixed 模式，防止勾选框撑宽"选择"列）
        self._fix_format_table_columns()

        self.log(f"嗅探完成，共 {len(fmts)} 个格式")
        show_infobar(self, "success", title="完成",
                     content=f"找到 {len(fmts)} 个可用格式", duration=3000)

    @staticmethod
    def _parse_quality_value(q: str) -> int:
        """从 quality 字符串提取数值用于降序排序"""
        if not q:
            return 0
        q = q.strip().lower()
        # 分辨率 "1920x1080" → 取高度
        m = re.search(r'(\d+)\s*x\s*(\d+)', q)
        if m:
            return int(m.group(2))
        # "1080p", "2160p" 等
        m = re.search(r'(\d+)\s*p', q)
        if m:
            return int(m.group(1))
        # "4k", "2k" 等（值≤16视为分辨率k）
        m = re.search(r'(\d+)\s*k', q)
        if m:
            val = int(m.group(1))
            if val <= 16:
                return val * 540  # 4k→2160, 2k→1080
            else:
                return val  # 码率如 320k, 128k
        # 纯数字
        m = re.search(r'(\d+)', q)
        if m:
            return int(m.group(1))
        return 0

    def _format_sort_key(self, fmt):
        """排序：视频+音频(高→低分辨率) → 音频流(高→低码率) → 视频流(高→低分辨率) → 其他"""
        t = fmt['type']
        if FormatType.VIDEO_AUDIO in t:
            cat = 0
        elif t == FormatType.AUDIO_ONLY:
            cat = 1
        elif t == FormatType.VIDEO_ONLY:
            cat = 2
        else:
            cat = 3
        return (cat, -self._parse_quality_value(fmt['quality']))

    def _on_sniff_error(self, err_msg):
        if hasattr(self, '_sniff_timer') and self._sniff_timer and self._sniff_timer.isActive():
            self._sniff_timer.stop()
        self._reset_sniff_btn()
        if self._sniff_timed_out:
            return
        self.log(f"嗅探失败: {err_msg}")
        # video_id 可能已在 do_sniff 中设置
        if not self._sniff_video_id:
            self._sniff_video_id = extract_youtube_id(self._last_sniff_url)
        self._sniff_title = ''
        self.cover_title.setText("封面下载（嗅探失败）")
        self.cover_meta.setText("视频信息获取失败，但仍可尝试下载封面")
        # 如果封面还未加载，尝试加载
        if self._sniff_video_id and not self.cover_label._pixmap:
            self._load_cover_image(self._sniff_video_id)
            self.log("已从 URL 提取封面选项，可尝试下载封面")
            show_infobar(self, "error", title="嗅探失败", content=err_msg)

    def _sniff_timeout(self):
        if self._sniff_worker and self._sniff_worker.isRunning():
            self._sniff_timed_out = True
            # 先尝试安全退出
            self._sniff_worker.request_stop()
            # 等待 2 秒，如果仍未退出则强制终止
            if not self._sniff_worker.wait(2000):
                self._sniff_worker.terminate()
            self._reset_sniff_btn()
            self.log("嗅探超时（60秒），请检查网络或链接是否有效")
            show_infobar(self, "error", title="超时", content="嗅探超时，请检查网络或链接")

    # -------- 封面加载（异步下载并在左侧显示）--------
    def _load_cover_image(self, video_id):
        """后台下载 maxresdefault 封面并显示在左侧"""
        if not video_id:
            return
        thumbs = build_thumbnails(video_id)
        if not thumbs:
            return
        # 取最大分辨率封面
        cover_url = thumbs[0]['url']

        class CoverLoader(QThread):
            loaded = pyqtSignal(bytes)
            failed = pyqtSignal()

            def run(self):
                try:
                    req = urllib.request.Request(
                        cover_url,
                        headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=15, context=get_certifi_ssl_context()) as resp:
                        data = resp.read()
                    if data:
                        self.loaded.emit(data)
                    else:
                        self.failed.emit()
                except Exception:
                    self.failed.emit()

        worker = CoverLoader()
        # 带身份转发：过期 worker（已被新 loader 取代）的晚到结果直接丢弃，
        # 避免晚到的失败清掉新 loader 刚成功显示的封面（跨嗅探同理）
        worker.loaded.connect(lambda d, w=worker: self._on_cover_loaded(d, w))
        worker.failed.connect(lambda w=worker: self._on_cover_failed(w))
        worker.finished.connect(lambda w=worker: self._on_loader_finished(w))
        self._cover_loader_worker = worker
        worker.start()

    def _on_loader_finished(self, worker):
        """封面加载线程结束：仍是当前 worker 才清引用（供补载 busy 判断、防 GC）"""
        if self._cover_loader_worker is worker:
            self._cover_loader_worker = None

    def _on_cover_loaded(self, data, worker=None):
        """封面加载完成 - 保存数据并显示（过期 worker 的结果直接丢弃）"""
        if worker is not None and worker is not self._cover_loader_worker:
            return
        self._cover_data = data  # 保存供任务队列复用
        pixmap = QPixmap()
        if pixmap.loadFromData(QByteArray(data)):
            # 交给 CoverImageWidget 的 paintEvent 按当前尺寸缩放绘制，无黑边
            self.cover_label.setPixmap(pixmap)
        else:
            self._cover_data = None
            self.cover_label.clearPixmap("封面加载失败")

    def _on_cover_failed(self, worker=None):
        """封面加载失败（过期 worker 的结果直接丢弃）"""
        if worker is not None and worker is not self._cover_loader_worker:
            return
        self._cover_data = None
        self.cover_label.clearPixmap("封面加载失败")
        self._maybe_start_cover_fallback()

    def _maybe_start_cover_fallback(self):
        """最终兜底：常规加载路径全部失败后，落盘下载最佳封面并显示。

        条件：嗅探到了封面列表、无进行中的兜底、同 URL 会话内未试过
        （防循环；失败 URL 不反复重试，跨嗅探保留）。
        """
        if not self._sniff_thumbs or self._cover_fallback_worker is not None:
            return
        best_url = self._sniff_thumbs[0]['url']
        if best_url in self._fallback_tried_urls:
            return
        self._fallback_tried_urls.add(best_url)
        self.log("常规封面加载失败，尝试最终兜底（落盘下载 + 转码）...")
        worker = self.CoverFallbackWorker(best_url)
        worker.resolved.connect(
            lambda data, msg, w=worker: self._on_cover_fallback_resolved(data, msg, w))
        worker.finished.connect(lambda w=worker: self._on_fallback_finished(w))
        self._cover_fallback_worker = worker
        worker.start()

    def _on_cover_fallback_resolved(self, data, msg, worker):
        """兜底结果：成功 → 显示并存 _cover_data；失败 → 保持占位并日志原因"""
        if self._cover_fallback_worker is not worker:
            return
        self._cover_fallback_worker = None
        if data:
            pixmap = QPixmap()
            if pixmap.loadFromData(QByteArray(data)):
                self._cover_data = data  # 供任务队列复用
                self.cover_label.setPixmap(pixmap)
                self.log(f"封面兜底成功: {msg}")
                return
            msg = f"兜底字节无法解码: {msg}"
        self.cover_label.clearPixmap("封面加载失败")
        self.log(f"封面兜底失败: {msg}")

    def _on_fallback_finished(self, worker):
        """兜底线程结束：仍是当前 worker 才清引用（防 GC）"""
        if self._cover_fallback_worker is worker:
            self._cover_fallback_worker = None

    def _load_cover_from_url(self, cover_url):
        """按 URL 后台下载封面并显示（非 YouTube 站点嗅探后调用）。

        与 _load_cover_image 内的 CoverLoader 逻辑一致，但以 URL 为入参，
        不依赖 YouTube 视频 ID；_on_cover_loaded 会存 _cover_data 供任务队列复用。
        """
        if not cover_url:
            return

        class UrlCoverLoader(QThread):
            loaded = pyqtSignal(bytes)
            failed = pyqtSignal()

            def run(self):
                try:
                    req = urllib.request.Request(
                        cover_url,
                        headers={'User-Agent': 'Mozilla/5.0'})
                    with urllib.request.urlopen(req, timeout=15, context=get_certifi_ssl_context()) as resp:
                        data = resp.read()
                    if data:
                        self.loaded.emit(data)
                    else:
                        self.failed.emit()
                except Exception:
                    self.failed.emit()

        worker = UrlCoverLoader()
        worker.loaded.connect(lambda d, w=worker: self._on_cover_loaded(d, w))
        worker.failed.connect(lambda w=worker: self._on_cover_failed(w))
        worker.finished.connect(lambda w=worker: self._on_loader_finished(w))
        self._cover_loader_worker = worker
        worker.start()

    # -------- 封面下载 Worker（独立 QThread，支持并行）--------
    class CoverFallbackWorker(QThread):
        """最终兜底 worker：常规 loader 全部失败后启动。两级链路：

        0. 缓存命中（遍历 fallback_{hash}.*，jpg/webp/png 均可）
        1. 落盘下载（Functions.download_thumbnail，即实测"能下载成功"的同款实现）
           → QImage 直接解码（覆盖网络抖动型失败）；**下载失败（防盗链 403）不中断**
        2. yt-dlp --write-thumbnail（防盗链兜底：yt-dlp 自带 extractor 的
           headers/cookies；--socket-timeout/--retries 保证进程时长有界）
           → 遍历产物 → QImage 解码。

        解码失败（如 AVIF 等 Qt 不支持的格式）统一放弃：保持占位 + 日志报
        魔数检测的实际格式。resolved(bytes|None, msg)。
        注意：线程内用 QImage 判断解码（可跨线程），QPixmap 仅主线程。
        """

        resolved = pyqtSignal(object, str)

        def __init__(self, url):
            super().__init__()
            self.url = url

        def run(self):
            import hashlib
            try:
                cache_dir = get_cover_dir()
                os.makedirs(cache_dir, exist_ok=True)
                name = hashlib.sha1(self.url.encode('utf-8')).hexdigest()[:16]
                raw_path = os.path.join(str(cache_dir), f"fallback_{name}.img")

                # 0. 缓存命中：遍历上次兜底产物（jpg/webp/png 均可，排除 .img）
                hit = _find_fallback_product(cache_dir, name)
                if hit:
                    with open(hit, 'rb') as f:
                        self.resolved.emit(f.read(), "缓存命中，加载成功")
                    return

                # 1. 落盘下载（与"下载封面"按钮同款实现）；防盗链 403 等失败
                #    不中断，落到 yt-dlp 兜底（yt-dlp 自带 extractor headers）。
                #    .img 为纯中间文件：不参与缓存命中，残留由启动清理兜底。
                data = None
                try:
                    Functions.download_thumbnail(self.url, raw_path)
                    if not QImage(raw_path).isNull():
                        with open(raw_path, 'rb') as f:
                            data = f.read()
                except Exception:
                    data = None

                if data:
                    self.resolved.emit(data, "落盘后直接加载成功")
                    return

                # 2. yt-dlp --write-thumbnail（防盗链兜底，最后一级）
                #    --socket-timeout/--retries 保证进程时长有界（约 1~2 分钟内）
                try:
                    template = os.path.join(str(cache_dir), f"fallback_{name}.%(ext)s")
                    cmd = build_ytdlp_cmd(
                        '--skip-download', '--no-playlist', '--no-warnings',
                        '--write-thumbnail', '--socket-timeout', '20', '--retries', '1',
                        '-o', template, self.url)
                    run_ytdlp(cmd, None)
                except Exception as e:
                    self.resolved.emit(None, f"yt-dlp 兜底失败: {e}")
                    return
                hit = _find_fallback_product(cache_dir, name)
                if hit:
                    with open(hit, 'rb') as f:
                        self.resolved.emit(f.read(), "yt-dlp 兜底加载成功")
                    return
                # 无可解产物：若有产物存在（可能格式 Qt 不支持，如 AVIF），日志报实际格式
                any_prod = [p for p in glob.glob(os.path.join(str(cache_dir), f"fallback_{name}.*"))
                            if not p.endswith('.img')]
                if any_prod:
                    self.resolved.emit(None, f"yt-dlp 产物无法解码（{_sniff_image_format(any_prod[0])}）")
                    return
                self.resolved.emit(None, "yt-dlp 兜底未产出可用封面")
            except Exception as e:
                self.resolved.emit(None, f"兜底失败: {e}")

    class CoverDownloadWorker(QThread):
        """封面下载专用线程，与视频下载并行执行"""
        log_signal = pyqtSignal(str)
        finished = pyqtSignal(bool)

        def __init__(self, url, path):
            super().__init__()
            self.url = url
            self.path = path

        def run(self):
            try:
                Functions.download_thumbnail(self.url, self.path, log=self.log_signal.emit)
                self.finished.emit(True)
            except Exception as e:
                self.log_signal.emit(f"封面下载失败: {e}")
                self.finished.emit(False)

    # -------- 封面下载（保存到本地）--------
    def _populate_cover_combo(self, thumbs):
        self.cover_combo.clear()
        if thumbs:
            # 保存列表供入队 cover_url 复用（YouTube = 硬编码 5 档，非 YouTube = yt-dlp 通用列表）
            self._sniff_thumbs = thumbs
            for i, t in enumerate(thumbs):
                w = t.get('width', 0) or 0
                h = t.get('height', 0) or 0
                tid = t.get('id', '') or ''
                if w and h:
                    # id 为非纯数字（如 maxresdefault）时附加标注；纯数字/缺失（通用列表）则只显示宽高
                    label = f"{w}x{h} ({tid})" if tid and not tid.isdigit() else f"{w}x{h}"
                elif i == 0:
                    label = "最佳分辨率"
                else:
                    label = f"备选分辨率 {i + 1}"
                # 注意：qfluentwidgets ComboBox.addItem(text, icon=None, userData=None)，
                # URL 必须以 userData 关键字传入（位置传参会落进 icon 形参导致 currentData() 恒为 None）
                self.cover_combo.addItem(label, userData=t['url'])
            self.cover_combo.setEnabled(True)
            self.cover_btn.setEnabled(True)
            self.log(f"找到 {len(thumbs)} 个封面分辨率")
        else:
            # 重置残留，避免上一视频的封面列表串入当前无封面场景
            self._sniff_thumbs = []
            self.cover_combo.addItem("无可用封面")
            self.cover_combo.setEnabled(False)
            self.cover_btn.setEnabled(False)

    def _download_cover(self):
        video_id = self._sniff_video_id or extract_youtube_id(self.url_input.text().strip())
        # 直接取下拉框当前项的 URL（userData），与显示内容天然一致；
        # 不按索引重建列表（通用列表项数与 build_thumbnails 不同，重建会索引错位）
        cover_url = self.cover_combo.currentData()
        if not cover_url:
            msg = "请先选择一个封面分辨率" if video_id else "该链接不支持封面获取"
            show_infobar(self, "warning", title="提示", content=msg, duration=3000)
            return

        out_dir = self.out_dir.text().strip() or DEFAULT_OUTPUT_DIR
        # 扩展名按封面 URL 推断（白名单外默认 .jpg），通用站点封面可能是 png/webp/avif
        ext = os.path.splitext(urlparse(cover_url).path)[1].lower()
        if ext not in ('.jpg', '.jpeg', '.png', '.webp', '.avif'):
            ext = '.jpg'
        if self._sniff_title:
            safe_base = "".join(c for c in self._sniff_title if c.isalnum() or c in ' _-.,()[]').strip()
            filename = f"{safe_base or video_id or 'cover'}_cover{ext}"
        else:
            filename = f"{video_id or 'cover'}_cover{ext}"

        path = os.path.join(out_dir, filename)
        if os.path.exists(path):
            counter = 1
            while True:
                name, ext = os.path.splitext(filename)
                new_path = os.path.join(out_dir, f"{name}_{counter}{ext}")
                if not os.path.exists(new_path):
                    path = new_path
                    break
                counter += 1

        # 使用独立 QThread 下载封面，与视频下载并行
        worker = self.CoverDownloadWorker(cover_url, path)
        worker.log_signal.connect(self.log)
        worker.finished.connect(lambda ok: self._on_cover_download_done(ok, path))
        self._cover_download_worker = worker  # 保存引用防止 GC
        worker.start()
        self.log(f"▶ 开始下载封面: {filename}")
        show_infobar(self, "info", title="开始下载封面",
                     content=f"保存到: {filename}", duration=3000)

    def _on_cover_download_done(self, success, path):
        """封面下载完成回调"""
        if success:
            # 实测分辨率弥补下拉框无宽高档位的信息缺失（QPixmap 读图头，开销可忽略）
            pixmap = QPixmap(path)
            size_info = f"（实际分辨率 {pixmap.width()}x{pixmap.height()}）" if not pixmap.isNull() else ""
            self.log(f"✓ 封面下载完成: {path}{size_info}")
            show_infobar(self, "success", title="封面下载完成",
                         content=f"已保存到: {os.path.basename(path)}", duration=3000)
        else:
            self.log("✗ 封面下载失败")
            show_infobar(self, "error", title="封面下载失败",
                         content="请检查网络连接或链接是否有效")

    # -------- 固定表格列宽 --------
    def _fix_format_table_columns(self):
        """Fixed 模式 + 按比例分配填满，每次 resize 自动重算"""
        self.format_table._distribute_columns()

    # -------- 格式表同类型互斥勾选 --------
    def _on_format_item_changed(self, item):
        """勾选某项时自动取消同类型其他已勾项；"其他"类型不限制"""
        if self._format_updating or item.column() != 0:
            return
        if item.checkState() != Qt.CheckState.Checked:
            return
        row = item.row()
        if row >= len(self._formats_data):
            return
        fmt_type = self._formats_data[row].get('type', '')
        if fmt_type not in (FormatType.VIDEO_AUDIO, FormatType.AUDIO_ONLY, FormatType.VIDEO_ONLY):
            return
        self._format_updating = True
        try:
            for r in range(self.format_table.rowCount()):
                if r == row:
                    continue
                other = self.format_table.item(r, 0)
                if (other and other.checkState() == Qt.CheckState.Checked
                        and r < len(self._formats_data)
                        and self._formats_data[r].get('type') == fmt_type):
                    other.setCheckState(Qt.CheckState.Unchecked)
        finally:
            self._format_updating = False

    # -------- 格式下载 --------
    def do_download(self):
        checked = []
        checked_video = []
        checked_audio = []
        for row in range(self.format_table.rowCount()):
            item = self.format_table.item(row, 0)
            if item and item.checkState() == Qt.CheckState.Checked:
                if row < len(self._formats_data):
                    fmt = self._formats_data[row]
                    checked.append(fmt['format_id'])
                    if fmt['type'] == '视频流':
                        checked_video.append(fmt['format_id'])
                    elif fmt['type'] == '音频流':
                        checked_audio.append(fmt['format_id'])

        if not checked:
            show_infobar(self, "warning", title="提示", content="请至少勾选一个格式",
                         duration=3000)
            return

        # 视频流+音频流各一个 → TeachingTip 询问是否合并
        if len(checked_video) == 1 and len(checked_audio) == 1 and len(checked) == 2:
            from qfluentwidgets import TeachingTip, TeachingTipView, TeachingTipTailPosition
            from PyQt6.QtWidgets import QHBoxLayout, QWidget
            from PyQt6.QtGui import QFont

            view = TeachingTipView(
                title="",
                content="勾选了一个视频流格式和一个音频流格式。\n是否将它们合并为一个有声视频？",
                isClosable=True,
            )
            view.setMinimumWidth(280)
            font = view.contentLabel.font()
            font.setPointSize(font.pointSize() + 2)
            view.contentLabel.setFont(font)

            # 按钮容器
            btn_widget = QWidget()
            btn_layout = QHBoxLayout(btn_widget)
            btn_layout.setContentsMargins(0, 0, 0, 0)

            btn_yes = qfw.PrimaryPushButton("是（合并）")
            btn_no = qfw.PushButton("否（分开下载）")
            btn_yes.setFixedWidth(130)
            btn_no.setFixedWidth(130)
            btn_layout.addStretch()
            btn_layout.addWidget(btn_yes)
            btn_layout.addWidget(btn_no)
            btn_layout.addStretch()

            view.addWidget(btn_widget, 0, Qt.AlignmentFlag.AlignCenter)

            tip = TeachingTip.make(
                view=view,
                target=self.download_btn,
                duration=-1,
                tailPosition=TeachingTipTailPosition.BOTTOM,
                parent=self,
            )
            view.closed.connect(tip.close)

            def _on_yes():
                tip.close()
                self._start_download('+'.join(checked))

            def _on_no():
                tip.close()
                self._start_download(','.join(checked))

            btn_yes.clicked.connect(_on_yes)
            btn_no.clicked.connect(_on_no)
        else:
            self._start_download('+'.join(checked))

    def _start_download(self, format_spec):
        url = self.url_input.text().strip()
        out_dir = self.out_dir.text().strip()
        if not out_dir:
            out_dir = DEFAULT_OUTPUT_DIR

        self.log(f"下载格式: {format_spec}")
        self.log(f"保存到: {out_dir}")

        # 加载下载配置
        download_config = load_download_config()
        concurrent_fragments = download_config['concurrent_fragments']
        use_aria2c = download_config['use_aria2c']
        aria2c_connections = download_config['aria2c_connections']

        remote = self.remote_cb.isChecked()
        # 构建任务元数据
        metadata = {
            'title': self._sniff_title,
            'channel': self._sniff_channel,
            'duration': self._sniff_duration,
            'cover_url': '',
            'url': url,
            'format_spec': format_spec,
            'out_dir': out_dir,
        }
        # 传递封面数据（避免队列中重复下载）
        # cover_url 优先用嗅探得到的封面列表首项（任意站点通用，非 YouTube 也可入队显示封面）；
        # 无列表时兜底用视频 ID 构建（YouTube 现状行为）
        video_id = self._sniff_video_id or extract_youtube_id(url)
        if self._sniff_thumbs:
            metadata['cover_url'] = self._sniff_thumbs[0]['url']
        elif video_id:
            thumbs = build_thumbnails(video_id)
            if thumbs:
                metadata['cover_url'] = thumbs[0]['url']
        if hasattr(self, '_cover_data') and self._cover_data:
            metadata['cover_data'] = self._cover_data
        # 添加到任务队列
        self.run_queued_task(
            func=Functions.download_youtube,
            args=(url, format_spec, out_dir, remote, concurrent_fragments, use_aria2c, aria2c_connections),
            task_type=TaskType.YTDLP,
            title=self._sniff_title or f"视频下载 - {video_id}",
            metadata=metadata
        )
        show_infobar(self, "info", title="任务已加入队列", content="", duration=3000)