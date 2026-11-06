"""
直播 / Premiere 自动监控 页面
"""
import math
import os
import time

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QFrame, QStackedWidget
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QUrl

from PyQt6.QtGui import QDesktopServices

import qfluentwidgets as qfw

from sookit.core.functions import Functions, load_download_config, DEFAULT_OUTPUT_DIR
from sookit.core.browser_protocol import normalize_video_url
from sookit.core.ytdlp_utils import is_ytdlp_available
from sookit.core.youtube_utils import extract_youtube_id, fetch_youtube_metadata
from sookit.core.youtube_utils import build_thumbnails
from sookit.core.task_queue import TaskQueueManager, TaskType
from sookit.core.workers import MonitorWorker
from sookit.pages.base import PageBase
from sookit.widgets.infobar import show_infobar
from sookit.widgets.monitor_card import MonitorCard
from sookit.core.utils import set_smooth_scroll_params
from sookit.widgets.notifier import TOAST_TARGET_MONITOR, notify_result_notice


class MonitorPage(PageBase):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)

        title = qfw.TitleLabel("直播 / Premiere 自动监控")
        layout.addWidget(title)
        layout.addWidget(self.create_caption_label(
            "输入 YouTube 视频或频道链接，自动轮询，直播/Premiere 结束后自动下载最高画质"))
        layout.addSpacing(6)

        # 检查 yt-dlp 可用性（PATH 全局或内置 tools/ 均可）
        self._ytdlp_warning_bar = None
        if not is_ytdlp_available():
            self._ytdlp_warning_bar = show_infobar(self, "warning", title="依赖缺失",
                                                   content="未找到 yt-dlp，直播监控功能不可用。请前往设置页下载安装")
            self.add_goto_settings_button(self._ytdlp_warning_bar)

        url_row = QHBoxLayout()
        self.url_input = qfw.LineEdit()
        self.url_input.setPlaceholderText("YouTube 视频链接 或 频道主页链接 (@频道名)...")
        self.add_btn = qfw.PrimaryPushButton("+ 添加监控")
        self.add_btn.clicked.connect(self.add_task)
        url_row.addWidget(self.url_input, stretch=1)
        url_row.addWidget(self.add_btn)
        layout.addLayout(url_row)

        settings = QHBoxLayout()
        settings.addWidget(qfw.BodyLabel("轮询间隔:"))
        self.interval_combo = qfw.ComboBox()
        self.interval_combo.addItems(["30 秒", "60 秒"])
        self.interval_combo.setMinimumWidth(100)
        settings.addWidget(self.interval_combo)
        settings.addSpacing(15)

        settings.addWidget(qfw.BodyLabel("保存到:"))
        self.out_dir = qfw.LineEdit()
        self.out_dir.setPlaceholderText("默认: /下载")
        settings.addWidget(self.out_dir, stretch=1)
        browse_btn = qfw.PushButton("浏览")
        browse_btn.setFixedWidth(80)
        browse_btn.clicked.connect(lambda: self.browse_dir(self.out_dir))
        settings.addWidget(browse_btn)
        settings.addSpacing(10)

        self.remote_cb = qfw.CheckBox("增强嗅探 (使用 ejs)")
        settings.addWidget(self.remote_cb)
        settings.addStretch()
        layout.addLayout(settings)

        # 常驻展示槽：空态和卡片切换不推动上方输入区（决策 71）。
        self.task_stack = QStackedWidget()
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch()
        for text in ("还没有监控任务", "添加 YouTube 视频或频道链接，开始自动监控"):
            label = qfw.BodyLabel(text)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(label)
        empty_layout.addStretch()
        self.task_stack.addWidget(empty)

        self.task_scroll = qfw.ScrollArea()
        self.task_scroll.setWidgetResizable(True)
        self.task_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.task_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.task_content = QWidget()
        self.task_flow = qfw.AdaptiveFlowLayout(self.task_content, needAni=False, isTight=True)
        self.task_flow.setWidgetMinimumWidth(320)
        self.task_flow.setWidgetMaximumWidth(640)
        self.task_flow.setHorizontalSpacing(16)
        self.task_flow.setVerticalSpacing(24)
        # Fluent 滚动条浮在视口内，固定留位，避免显隐造成卡片跳宽。
        self.task_flow.setContentsMargins(6, 6, 24, 6)
        self.task_scroll.setWidget(self.task_content)
        self.task_scroll.enableTransparentBackground()
        set_smooth_scroll_params(self.task_scroll)
        self.task_stack.addWidget(self.task_scroll)
        layout.addWidget(self.task_stack, stretch=1)

        self._setup_log_area(layout)

        self._closing = False
        self._task_counter = 0
        self._workers = {}
        self._task_cards = {}
        self._sniff_workers = {}
        self._task_titles = {}
        self._task_info = {}
        self._wait_timers = {}
        self._countdown_timers = {}

        # 连接设置变化信号，实时更新所有运行中的 worker
        self.interval_combo.currentIndexChanged.connect(self._on_interval_changed)
        self.remote_cb.stateChanged.connect(self._on_remote_changed)
        self.out_dir.textChanged.connect(self._on_out_dir_changed)

        # 连接任务队列信号，同步下载完成/失败状态
        mgr = TaskQueueManager.instance()
        mgr.task_completed.connect(self._on_queue_task_completed)
        mgr.task_failed.connect(self._on_queue_task_failed)
        # 连接队列下载日志，使本页监控任务的下载过程显示到监控页终端
        mgr.task_log.connect(self._on_queue_task_log)

    def _is_channel_url(self, url):
        parts = url.lower().split('/')
        for p in parts:
            if p.startswith('@') or p in ('channel', 'c', 'user'):
                return True
        return False

    def add_task(self):
        url = self.url_input.text().strip()
        if not url:
            show_infobar(self, "warning", title="提示", content="请输入链接",
                         duration=3000)
            return

        self.url_input.clear()

        if self._is_channel_url(url):
            self.log(f"检测到频道链接，正在嗅探视频列表...")
            self._sniff_channel_and_add(url)
        else:
            self._task_counter += 1
            task_id = f"任务{self._task_counter}"
            self._add_single_task(task_id, url, url)

    def monitor_from_browser(self, url, video_metadata=None):
        """浏览器统一入口；网页信息仅作展示，状态始终由后台重新检测。"""
        if self._closing:
            return {'ok': False, 'error': '监控页正在关闭，请稍后重试'}
        try:
            url = normalize_video_url(url)
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
        video_id = extract_youtube_id(url)
        for task_id, info in self._task_info.items():
            if extract_youtube_id(info['url']) == video_id:
                return {'ok': True, 'task_id': task_id, 'duplicate': True,
                        'message': '此视频已在 Sookit 监控列表中'}
        if not is_ytdlp_available():
            return {'ok': False, 'error': 'yt-dlp 不可用，请先在设置页配置'}
        metadata = video_metadata if isinstance(video_metadata, dict) else {}
        title = metadata.get('title') or url
        channel = metadata.get('channel') or ''
        self._task_counter += 1
        task_id = f"任务{self._task_counter}"
        self._add_single_task(task_id, url, title)
        self._task_info[task_id]['channel'] = channel
        self._task_cards[task_id].set_metadata(title, channel)
        self.log(f"[{task_id}] 已从浏览器添加直播 / Premiere 监控")
        return {'ok': True, 'task_id': task_id,
                'message': '已添加到 Sookit 监控列表，结束后自动下载'}

    def _sniff_channel_and_add(self, url):
        self.add_btn.setEnabled(False)
        self.add_btn.setText("嗅探频道中...")

        class ChannelSniffWorker(QThread):
            done = pyqtSignal(list)
            error = pyqtSignal(str)
            def run(self):
                try:
                    result = Functions.sniff_channel(url, log=None)
                    self.done.emit(result.get('videos', []))
                except Exception as e:
                    self.error.emit(str(e))

        worker = ChannelSniffWorker()
        worker.done.connect(lambda v, u=url: self._on_channel_done(v, u))
        worker.error.connect(lambda e, u=url: self._on_channel_error(e, u))
        self._channel_sniff_worker = worker
        worker.start()

    def _on_channel_done(self, videos, url=""):
        self.add_btn.setEnabled(True)
        self.add_btn.setText("+ 添加监控")
        self._channel_sniff_worker = None
        if not videos:
            self.log("频道中未找到任何视频")
            # 系统通知（决策 40 扩展）：0 条对用户是「没拿到想要的结果」，单独提示
            notify_result_notice(
                "频道嗅探完成", url, "该频道中没有找到可添加的视频",
                target_page=TOAST_TARGET_MONITOR)
            return
        added = 0
        for v in videos:
            self._task_counter += 1
            task_id = f"任务{self._task_counter}"
            self._add_single_task(task_id, v['url'], v['title'])
            added += 1
        self.log(f"已从频道添加 {added} 个视频到监控列表")
        show_infobar(self, "success", title="完成",
                     content=f"已添加 {added} 个视频到监控列表", duration=3000)
        # 系统通知（决策 40 扩展）：主窗口不在焦点时告知频道嗅探结果
        notify_result_notice(
            "频道嗅探完成", url, f"已添加 {added} 个视频到监控列表",
            target_page=TOAST_TARGET_MONITOR)

    def _on_channel_error(self, err, url=""):
        self.add_btn.setEnabled(True)
        self.add_btn.setText("+ 添加监控")
        self._channel_sniff_worker = None
        self.log(f"频道嗅探失败: {err}")
        show_infobar(self, "error", title="嗅探失败", content=err, duration=5000)
        # 系统通知（决策 40 扩展）：主窗口不在焦点时告知频道嗅探失败
        notify_result_notice(
            "频道嗅探失败", url, err, target_page=TOAST_TARGET_MONITOR)

    def _add_single_task(self, task_id, url, title):
        card = MonitorCard(task_id, url, title)
        card.watch_requested.connect(self._watch_task)
        card.start_requested.connect(self._start_waiting_task)
        card.remove_requested.connect(self._remove_or_cancel_task)
        self._task_cards[task_id] = card
        self.task_flow.addWidget(card)
        self.task_stack.setCurrentIndex(1)

        self._task_titles[task_id] = title
        out_dir = self.read_path(self.out_dir) or DEFAULT_OUTPUT_DIR
        self._task_info[task_id] = {'url': url, 'out_dir': out_dir, 'title': title}

        self._sniff_and_start_worker(task_id, url, out_dir, title)

    def _sniff_and_start_worker(self, task_id, url, out_dir, title):
        class SniffWorker(QThread):
            done = pyqtSignal(dict)
            error = pyqtSignal(str)
            def __init__(self, url):
                super().__init__()
                self.url = url
            def run(self):
                try:
                    info = Functions.check_live_status(self.url)
                    self.done.emit(info)
                except Exception:
                    vid = extract_youtube_id(self.url)
                    if vid:
                        info = fetch_youtube_metadata(vid)
                        if info and info.get('title'):
                            self.done.emit(info)
                            return
                    self.error.emit("无法获取视频信息")

        worker = SniffWorker(url)
        worker.done.connect(
            lambda info: self._on_initial_sniff(task_id, url, out_dir, title, info))
        worker.error.connect(
            lambda err: self._start_waiting_task(task_id))
        self._sniff_workers[task_id] = worker
        worker.finished.connect(lambda: self._sniff_workers.pop(task_id, None))
        worker.start()

    def _watch_task(self, task_id):
        info = self._task_info.get(task_id)
        if info:
            QDesktopServices.openUrl(QUrl(info['url']))

    def _start_waiting_task(self, task_id):
        info = self._task_info.get(task_id)
        if info:
            self._start_monitor_now(task_id, info['url'], info['out_dir'], info['title'])

    def _remove_or_cancel_task(self, task_id):
        info = self._task_info.get(task_id, {})
        if info.get('queue_task_id'):
            self._cancel_download(task_id)
        else:
            self._remove_task(task_id)

    def _on_initial_sniff(self, task_id, url, out_dir, title, info):
        # 删除后迟到的嗅探结果不能复活任务，也不能更新相邻卡片。
        card = self._task_cards.get(task_id)
        if card is None or self._closing:
            return
        previous = self._task_info[task_id]
        real_title = info.get('title') or previous.get('title') or title
        channel = info.get('channel') or info.get('uploader') or previous.get('channel', '')
        scheduled = info.get('scheduled_start_time')
        self._task_info[task_id].update(
            title=real_title, duration=info.get('duration', ''),
            channel=channel, scheduled_start_time=scheduled)
        self._task_titles[task_id] = real_title
        card.set_metadata(real_title, channel, scheduled)

        status = info.get('live_status', 'unknown')
        scheduled = info.get('scheduled_start_time')
        now = time.time()

        if status == 'is_upcoming' and scheduled and scheduled > now:
            wait_secs = scheduled - now
            if wait_secs > 60:
                self.log(f"[{task_id}] 检测到预约直播 / Premiere，距开始还有 "
                         f"{int(wait_secs//60)} 分 {int(wait_secs%60)} 秒，届时自动开始")
            else:
                self.log(f"[{task_id}] 检测到预约直播 / Premiere，距开始还有 {int(wait_secs)} 秒")

            if wait_secs > 60:
                init_text = f"等待中（剩余 {math.ceil(wait_secs / 60)} 分钟）"
            else:
                init_text = f"等待中（剩余 {int(wait_secs)} 秒）"
            card.set_status(init_text)
            card.set_waiting(True)

            auto_timer = QTimer(self)
            auto_timer.setSingleShot(True)
            auto_timer.timeout.connect(lambda: self._start_waiting_task(task_id))
            auto_timer.start(int(wait_secs * 1000))
            self._wait_timers[task_id] = auto_timer

            count_timer = QTimer(self)
            count_timer.timeout.connect(
                lambda: self._update_countdown(task_id, scheduled))
            count_timer.start(1000)
            self._countdown_timers[task_id] = count_timer
        else:
            self._start_waiting_task(task_id)

    def _update_countdown(self, task_id, scheduled_time):
        if task_id not in self._task_cards:
            timer = self._countdown_timers.pop(task_id, None)
            if timer:
                timer.stop()
            return
        remaining = scheduled_time - time.time()
        if remaining <= 0:
            timer = self._countdown_timers.pop(task_id, None)
            if timer:
                timer.stop()
            return
        if remaining > 60:
            text = f"等待中（剩余 {math.ceil(remaining / 60)} 分钟）"
        else:
            text = f"等待中（剩余 {int(remaining)} 秒）"
        self._task_cards[task_id].set_status(text)

    def _start_monitor_now(self, task_id, url, out_dir, title=None):
        if task_id in self._workers:
            return

        if task_id in self._wait_timers:
            self._wait_timers.pop(task_id).stop()
        if task_id in self._countdown_timers:
            self._countdown_timers.pop(task_id).stop()

        card = self._task_cards.get(task_id)
        if card is None or self._closing:
            return
        card.set_waiting(False)
        card.set_status("监控中")
        interval = 30 if self.interval_combo.currentIndex() == 0 else 60
        remote = self.remote_cb.isChecked()

        # 加载下载配置
        download_config = load_download_config()
        concurrent_fragments = download_config['concurrent_fragments']
        use_aria2c = download_config['use_aria2c']
        aria2c_connections = download_config['aria2c_connections']

        worker = MonitorWorker(task_id, url, out_dir, interval, remote,
                              concurrent_fragments, use_aria2c, aria2c_connections)
        worker.log_signal.connect(lambda msg: self.log(msg))
        worker.status_signal.connect(self._update_task_status)
        worker.done_signal.connect(self._on_monitor_done)
        worker.download_ready.connect(self._on_download_ready)
        self._workers[task_id] = worker
        worker.start()
        self.log(f"[{task_id}] 开始监控 -> {title or url}")

    def _on_download_ready(self, task_id, url, out_dir, format_spec, config, info=None):
        """MonitorWorker 检测到可下载时，把下载任务提交给全局任务队列"""
        if self._closing or task_id not in self._task_cards:
            return
        # 停止并移除该 MonitorWorker（防重复提交）
        if task_id in self._workers:
            worker = self._workers.pop(task_id)
            worker.stop()
            worker.wait(2000)

        info = info or {}
        title = info.get('title') or self._task_info.get(task_id, {}).get('title') or url
        # 用检测到的真实标题回写，保证下载任务标题与界面一致
        if task_id in self._task_info:
            self._task_info[task_id]['title'] = title
            self._task_info[task_id]['duration'] = info.get('duration', '')
        if task_id in self._task_titles:
            self._task_titles[task_id] = title
        card = self._task_cards[task_id]
        task_info = self._task_info[task_id]
        channel = info.get('channel') or info.get('uploader') or task_info.get('channel', '')
        scheduled = info.get('scheduled_start_time') or task_info.get('scheduled_start_time')
        task_info.update(channel=channel, scheduled_start_time=scheduled)
        card.set_metadata(title, channel, scheduled)
        card.set_status("下载中")
        card.set_downloading(True)

        # 构建完整元数据，使队列卡片能展示标题/封面/频道/时长（参考嗅探页 youtube_page.py）
        metadata = {
            'title': title,
            'channel': channel,
            'duration': info.get('duration', ''),
            'cover_url': '',
            'url': url,
            'format_spec': format_spec,
            'out_dir': out_dir,
        }
        # 封面 URL：用视频 ID 生成（嗅探页同款用法）
        video_id = extract_youtube_id(url)
        if video_id:
            thumbs = build_thumbnails(video_id)
            if thumbs:
                metadata['cover_url'] = thumbs[0]['url']

        mgr = TaskQueueManager.instance()
        task = mgr.add_task(
            task_type=TaskType.YTDLP,
            title=title,
            func=Functions.download_youtube,
            args=(url, format_spec, out_dir, config.get('remote', False),
                  config.get('concurrent_fragments', 8),
                  config.get('use_aria2c', True),
                  config.get('aria2c_connections', 16)),
            metadata=metadata,
        )
        # 记录监控任务 -> 队列任务的映射
        self._task_info[task_id]['queue_task_id'] = task.task_id
        self.log(f"[{task_id}] 已提交下载任务到队列: {title}")

    def _cancel_download(self, task_id):
        """下载阶段取消：由统一移除入口取消队列任务，避免重复发送。"""
        self._remove_task(task_id)

    def stop_all_workers(self):
        """停止监控与倒计时，忽略退出后迟到的嗅探结果。"""
        self._closing = True
        for timers in (self._wait_timers, self._countdown_timers):
            for timer in timers.values():
                timer.stop()
            timers.clear()
        for card in self._task_cards.values():
            card.dispose()
        for task_id, worker in list(self._workers.items()):
            worker.stop()
            worker.wait(2000)
            del self._workers[task_id]

    def _update_task_status(self, task_id, status):
        card = self._task_cards.get(task_id)
        if card:
            card.set_status(status)

    def _on_monitor_done(self, task_id, success):
        """MonitorWorker 结束（现在仅用于轮询阶段的异常终止）"""
        if not success:
            if task_id not in self._task_cards:
                return
            self._task_cards[task_id].set_status("失败")
            show_infobar(self, "error", title="监控任务失败",
                     content=f"任务 [{task_id}] 执行失败")
        self._workers.pop(task_id, None)
        if not self._workers:
            self._check_auto_action()

    def _monitor_id_by_queue(self, queue_task_id):
        """根据队列任务 ID 反查监控 task_id（未找到返回 None）"""
        for tid, info in self._task_info.items():
            if info.get('queue_task_id') == queue_task_id:
                return tid
        return None

    def _on_queue_task_log(self, queue_task_id, msg):
        """把本页监控任务的队列下载日志转发到监控页终端"""
        tid = self._monitor_id_by_queue(queue_task_id)
        if tid is not None:
            self.log(msg)

    def _on_queue_task_completed(self, task):
        """队列下载完成：同步监控卡片状态并触发封面下载"""
        tid = self._monitor_id_by_queue(task.task_id)
        if tid is None:
            return
        card = self._task_cards.get(tid)
        if card:
            card.set_status("已完成")
            card.set_downloading(False)
        self._task_info[tid].pop('queue_task_id', None)
        info = self._task_info.get(tid)
        if info:
            self._download_best_thumbnail(tid, info['url'], info['out_dir'], info['title'])
        self.log(f"[{tid}] 队列下载完成")
        if not self._workers:
            self._check_auto_action()

    def _on_queue_task_failed(self, task):
        """队列下载失败：清理旧失败任务、显示等待重试、重启 MonitorWorker 按 interval 自动重试"""
        tid = self._monitor_id_by_queue(task.task_id)
        # 先清除映射，避免 remove_failed_task 触发的 task_removed 误删监控行
        if tid is not None:
            self._task_info[tid].pop('queue_task_id', None)
        # 清理旧 FAILED 队列任务，避免残留卡片
        TaskQueueManager.instance().remove_failed_task(task.task_id)
        if tid is None:
            return
        card = self._task_cards.get(tid)
        if card:
            card.set_status("等待重试")
            card.set_downloading(False)
        # 重启 MonitorWorker 继续轮询（等待一个 interval 再重新检测）
        info = self._task_info.get(tid)
        if not info:
            return
        # 清除旧的 queue_task_id，重新进入监控
        info.pop('queue_task_id', None)
        interval = 30 if self.interval_combo.currentIndex() == 0 else 60
        remote = self.remote_cb.isChecked()
        download_config = load_download_config()
        worker = MonitorWorker(
            tid, info['url'], info['out_dir'], interval, remote,
            download_config['concurrent_fragments'],
            download_config['use_aria2c'],
            download_config['aria2c_connections'])
        worker.log_signal.connect(lambda msg: self.log(msg))
        worker.status_signal.connect(self._update_task_status)
        worker.done_signal.connect(self._on_monitor_done)
        worker.download_ready.connect(self._on_download_ready)
        self._workers[tid] = worker
        worker.start()
        self.log(f"[{tid}] 下载失败，等待重试，{interval}秒后重新检测")

    def _download_best_thumbnail(self, task_id, url, out_dir, title):
        class CoverWorker(QThread):
            log_signal = pyqtSignal(str)
            done = pyqtSignal()

            def run(self):
                try:
                    thumbs = Functions.get_thumbnails_list(url, log=None)
                    if thumbs:
                        best = thumbs[0]
                        safe_title = "".join(c for c in title if c.isalnum() or c in ' _-.,()[]')
                        cover_path = os.path.join(out_dir, f"{safe_title}_cover.jpg")
                        Functions.download_thumbnail(best['url'], cover_path, log=None)
                        self.log_signal.emit(f"[{task_id}] 封面已自动下载: {cover_path}")
                    else:
                        self.log_signal.emit(f"[{task_id}] 未找到可用封面")
                except Exception as e:
                    self.log_signal.emit(f"[{task_id}] 封面自动下载失败: {e}")
                self.done.emit()

        worker = CoverWorker()
        worker.log_signal.connect(self.log)
        self._cover_workers = getattr(self, '_cover_workers', []) + [worker]
        # 使用 QTimer.singleShot 延迟移除，避免并发修改问题
        worker.done.connect(lambda: QTimer.singleShot(0, lambda: self._cover_workers.remove(worker) if worker in self._cover_workers else None))
        worker.start()

    def _on_interval_changed(self, index):
        """轮询间隔变化时，更新所有运行中的 worker"""
        interval = 30 if index == 0 else 60
        for task_id, worker in self._workers.items():
            worker.update_interval(interval)
            self.log(f"[{task_id}] 轮询间隔已更新为 {interval} 秒")

    def _on_remote_changed(self, state):
        """remote-components 变化时，更新所有运行中的 worker"""
        remote = state == Qt.CheckState.Checked.value
        for task_id, worker in self._workers.items():
            worker.update_remote(remote)
            self.log(f"[{task_id}] remote-components 已{'启用' if remote else '禁用'}")

    def _on_out_dir_changed(self, text):
        """保存目录变化时，更新所有运行中的 worker 和任务信息"""
        out_dir = text.strip() or DEFAULT_OUTPUT_DIR
        for task_id, worker in self._workers.items():
            worker.update_output_dir(out_dir)
            self.log(f"[{task_id}] 保存目录已更新为: {out_dir}")
        # 同时更新 _task_info 中的保存目录
        for task_id in self._task_info:
            self._task_info[task_id]['out_dir'] = out_dir

    def _remove_task(self, task_id):
        if task_id in self._wait_timers:
            self._wait_timers.pop(task_id).stop()
        if task_id in self._countdown_timers:
            self._countdown_timers.pop(task_id).stop()
        if task_id in self._workers:
            self._workers[task_id].stop()
            self._workers[task_id].wait(3000)
            del self._workers[task_id]
        # 若已进入下载阶段（存在队列任务），联动取消队列下载
        info = self._task_info.get(task_id)
        queue_id = info.get('queue_task_id') if info else None
        if queue_id:
            TaskQueueManager.instance().cancel_task(queue_id)
        card = self._task_cards.pop(task_id, None)
        if card:
            card.dispose()
            self.task_flow.removeWidget(card)
            card.hide()
            card.deleteLater()
        self.task_stack.setCurrentIndex(1 if self._task_cards else 0)
        self._task_titles.pop(task_id, None)
        self._task_info.pop(task_id, None)
        self.log(f"[{task_id}] 已移除")

    def refresh_ytdlp_status(self):
        """yt-dlp 装好后重新检测：若已可用则关闭「未找到 yt-dlp」提示"""
        if self._ytdlp_warning_bar is not None and is_ytdlp_available():
            try:
                self._ytdlp_warning_bar.close()
            except Exception:
                pass
            self._ytdlp_warning_bar = None
