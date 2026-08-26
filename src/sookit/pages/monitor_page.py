"""
直播 / Premiere 自动监控 页面
"""
import math
import os
import time

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer

import qfluentwidgets as qfw

from sookit.core.functions import Functions, is_ytdlp_available, extract_youtube_id, fetch_youtube_metadata, load_download_config, DEFAULT_OUTPUT_DIR, ensure_output_dir
from sookit.core.youtube_utils import build_thumbnails
from sookit.core.task_queue import TaskQueueManager, TaskType
from sookit.core.workers import MonitorWorker
from sookit.pages.base import PageBase


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
            self._ytdlp_warning_bar = qfw.InfoBar.warning(
                parent=self, title="依赖缺失",
                content="未找到 yt-dlp，直播监控功能不可用。请前往设置页下载安装",
                orient=Qt.Orientation.Horizontal, isClosable=True, duration=-1
            )

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

        layout.addWidget(qfw.BodyLabel("监控列表:"))
        self.task_table = qfw.TableWidget()
        self.task_table.setColumnCount(3)
        self.task_table.setHorizontalHeaderLabels(["状态", "标题 / 链接", "操作"])
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.task_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.task_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.task_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.task_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        # ResizeToContents 不统计 setCellWidget 的按钮，需显式固定列宽（立即开始 80 + 删除 60 + spacing）
        self.task_table.setColumnWidth(2, 160)
        self.task_table.setAlternatingRowColors(True)
        self.task_table.setMinimumHeight(200)
        layout.addWidget(self.task_table, stretch=1)

        self._setup_log_area(layout)

        self._task_counter = 0
        self._workers = {}
        self._task_rows = {}
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
            qfw.InfoBar.warning(
                parent=self, title="提示", content="请输入链接",
                orient=Qt.Orientation.Horizontal, isClosable=True, duration=3000)
            return

        self.url_input.clear()

        if self._is_channel_url(url):
            self.log(f"检测到频道链接，正在嗅探视频列表...")
            self._sniff_channel_and_add(url)
        else:
            self._task_counter += 1
            task_id = f"任务{self._task_counter}"
            self._add_single_task(task_id, url, url)

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
        worker.done.connect(lambda v: self._on_channel_done(v))
        worker.error.connect(lambda e: self._on_channel_error(e))
        self._channel_sniff_worker = worker
        worker.start()

    def _on_channel_done(self, videos):
        self.add_btn.setEnabled(True)
        self.add_btn.setText("+ 添加监控")
        self._channel_sniff_worker = None
        if not videos:
            self.log("频道中未找到任何视频")
            return
        added = 0
        for v in videos:
            self._task_counter += 1
            task_id = f"任务{self._task_counter}"
            self._add_single_task(task_id, v['url'], v['title'])
            added += 1
        self.log(f"已从频道添加 {added} 个视频到监控列表")
        qfw.InfoBar.success(
            parent=self, title="完成",
            content=f"已添加 {added} 个视频到监控列表",
            orient=Qt.Orientation.Horizontal, isClosable=True, duration=3000)

    def _on_channel_error(self, err):
        self.add_btn.setEnabled(True)
        self.add_btn.setText("+ 添加监控")
        self._channel_sniff_worker = None
        self.log(f"频道嗅探失败: {err}")
        qfw.InfoBar.error(
            parent=self, title="嗅探失败", content=err,
            orient=Qt.Orientation.Horizontal, isClosable=True, duration=5000)

    def _add_single_task(self, task_id, url, title):
        row = self.task_table.rowCount()
        self.task_table.insertRow(row)

        self.task_table.setItem(row, 0, QTableWidgetItem("嗅探中..."))
        self.task_table.setItem(row, 1, QTableWidgetItem(title))
        btn_widget = QWidget()
        btn_layout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(4)
        del_btn = qfw.PushButton("删除")
        del_btn.setFixedWidth(60)
        del_btn.clicked.connect(lambda: self._remove_task(task_id))
        btn_layout.addWidget(del_btn)
        self.task_table.setCellWidget(row, 2, btn_widget)

        self._task_rows[task_id] = row
        self._task_titles[task_id] = title
        out_dir = self.out_dir.text().strip() or DEFAULT_OUTPUT_DIR
        self._task_info[task_id] = {'url': url, 'out_dir': out_dir, 'title': title}

        self._sniff_and_start_worker(task_id, url, out_dir, title, row)

    def _sniff_and_start_worker(self, task_id, url, out_dir, title, row):
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
            lambda info: self._on_initial_sniff(task_id, url, out_dir, title, row, info))
        worker.error.connect(
            lambda err: self._start_monitor_now(task_id, url, out_dir, title))
        self._sniff_worker = worker
        worker.start()

    def _on_initial_sniff(self, task_id, url, out_dir, title, row, info):
        # 用真实视频标题更新第 1 列（单条 URL 添加时此处 title 原本是链接），并同步元信息
        real_title = info.get('title') or title
        item = self.task_table.item(row, 1)
        if item:
            item.setText(real_title)
        if task_id in self._task_info:
            self._task_info[task_id]['title'] = real_title
            self._task_info[task_id]['duration'] = info.get('duration', '')
        if task_id in self._task_titles:
            self._task_titles[task_id] = real_title

        status = info.get('live_status', 'unknown')
        scheduled = info.get('scheduled_start_time')
        now = time.time()

        if status == 'is_upcoming' and scheduled and scheduled > now:
            wait_secs = scheduled - now
            if wait_secs > 60:
                self.log(f"[{task_id}] 检测到预定首播，距开始还有 "
                         f"{int(wait_secs//60)} 分 {int(wait_secs%60)} 秒，届时自动开始")
            else:
                self.log(f"[{task_id}] 检测到预定首播，距开始还有 {int(wait_secs)} 秒")

            if wait_secs > 60:
                init_text = f"等待中（剩余 {math.ceil(wait_secs / 60)} 分钟）"
            else:
                init_text = f"等待中（剩余 {int(wait_secs)} 秒）"
            self.task_table.item(row, 0).setText(init_text)

            start_btn = qfw.PushButton("立即开始")
            start_btn.setFixedWidth(80)
            start_btn.clicked.connect(
                lambda checked=False, tid=task_id: self._start_monitor_now(
                    tid, self._task_info[tid]['url'],
                    self._task_info[tid]['out_dir'],
                    self._task_info[tid]['title']))
            btn_widget = self.task_table.cellWidget(row, 2)
            btn_widget.layout().insertWidget(0, start_btn)

            auto_timer = QTimer(self)
            auto_timer.setSingleShot(True)
            auto_timer.timeout.connect(
                lambda: self._start_monitor_now(
                    task_id, url, out_dir, title))
            auto_timer.start(int(wait_secs * 1000))
            self._wait_timers[task_id] = auto_timer

            count_timer = QTimer(self)
            count_timer.timeout.connect(
                lambda: self._update_countdown(task_id, row, scheduled))
            count_timer.start(1000)
            self._countdown_timers[task_id] = count_timer
        else:
            self._start_monitor_now(task_id, url, out_dir, title)

    def _update_countdown(self, task_id, row, scheduled_time):
        if task_id not in self._task_rows:
            self._countdown_timers.pop(task_id, None)
            return
        remaining = scheduled_time - time.time()
        if remaining <= 0:
            self._countdown_timers.pop(task_id, None)
            return
        if remaining > 60:
            text = f"等待中（剩余 {math.ceil(remaining / 60)} 分钟）"
        else:
            text = f"等待中（剩余 {int(remaining)} 秒）"
        item = self.task_table.item(self._task_rows[task_id], 0)
        if item.text() == text:
            return
        item.setText(text)

    def _start_monitor_now(self, task_id, url, out_dir, title=None):
        if task_id in self._workers:
            return

        if task_id in self._wait_timers:
            self._wait_timers.pop(task_id).stop()
        if task_id in self._countdown_timers:
            self._countdown_timers.pop(task_id).stop()

        if task_id not in self._task_rows:
            return

        row = self._task_rows[task_id]

        btn_widget = self.task_table.cellWidget(row, 2)
        if btn_widget:
            layout = btn_widget.layout()
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item and item.widget():
                    w = item.widget()
                    if isinstance(w, qfw.PushButton) and w.text() == "立即开始":
                        w.deleteLater()
                        layout.removeWidget(w)
                        break

        self.task_table.item(row, 0).setText("监控中")
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
        # 同步更新表格第 1 列（显示标题而非链接）
        if task_id in self._task_rows:
            item = self.task_table.item(self._task_rows[task_id], 1)
            if item:
                item.setText(title)
        self.task_table.item(self._task_rows[task_id], 0).setText("下载中")

        # 替换操作按钮：下载阶段改为"取消"（取消队列任务）
        btn_widget = self.task_table.cellWidget(self._task_rows[task_id], 2)
        if btn_widget:
            layout = btn_widget.layout()
            for i in range(layout.count()):
                item = layout.itemAt(i)
                if item and item.widget() and isinstance(item.widget(), qfw.PushButton):
                    w = item.widget()
                    if w.text() == "删除":
                        w.setText("取消")
                        w.clicked.disconnect()
                        w.clicked.connect(
                            lambda checked=False, tid=task_id: self._cancel_download(tid))
                        break

        # 构建完整元数据，使队列卡片能展示标题/封面/频道/时长（参考嗅探页 youtube_page.py）
        metadata = {
            'title': title,
            'channel': info.get('channel', ''),
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
                  config.get('concurrent_fragments', 10),
                  config.get('use_aria2c', True),
                  config.get('aria2c_connections', 16)),
            metadata=metadata,
        )
        # 记录监控任务 -> 队列任务的映射
        self._task_info[task_id]['queue_task_id'] = task.task_id
        self.log(f"[{task_id}] 已提交下载任务到队列: {title}")

    def _cancel_download(self, task_id):
        """下载阶段取消：取消队列下载任务并移除监控行"""
        info = self._task_info.get(task_id)
        queue_id = info.get('queue_task_id') if info else None
        if queue_id:
            TaskQueueManager.instance().cancel_task(queue_id)
            self.log(f"[{task_id}] 已请求取消队列下载任务")
        # 移除监控行（内部会清映射并触发队列 cancel_task，任务已移除则安全返回）
        self._remove_task(task_id)

    def stop_all_workers(self):
        """停止所有监控 worker（供主窗口退出时调用）"""
        for task_id, worker in list(self._workers.items()):
            worker.stop()
            worker.wait(2000)
            del self._workers[task_id]

    def _update_task_status(self, task_id, status):
        if task_id in self._task_rows:
            row = self._task_rows[task_id]
            self.task_table.item(row, 0).setText(status)

    def _on_monitor_done(self, task_id, success):
        """MonitorWorker 结束（现在仅用于轮询阶段的异常终止）"""
        if not success:
            if task_id in self._task_rows:
                row = self._task_rows[task_id]
                self.task_table.item(row, 0).setText("失败")
            qfw.InfoBar.error(parent=self, title="监控任务失败", content=f"任务 [{task_id}] 执行失败")
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
        """队列下载完成：同步监控表格状态并触发封面下载"""
        tid = self._monitor_id_by_queue(task.task_id)
        if tid is None:
            return
        if tid in self._task_rows:
            self.task_table.item(self._task_rows[tid], 0).setText("已完成")
            # 操作按钮由"取消"恢复为"删除"
            btn_widget = self.task_table.cellWidget(self._task_rows[tid], 2)
            if btn_widget:
                layout = btn_widget.layout()
                for i in range(layout.count()):
                    item = layout.itemAt(i)
                    if item and item.widget() and isinstance(item.widget(), qfw.PushButton):
                        w = item.widget()
                        if w.text() == "取消":
                            w.setText("删除")
                            w.clicked.disconnect()
                            w.clicked.connect(
                                lambda checked=False, t=tid: self._remove_task(t))
                            break
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
        if tid in self._task_rows:
            self.task_table.item(self._task_rows[tid], 0).setText("等待重试")
            # 操作按钮由"取消"恢复为"删除"
            btn_widget = self.task_table.cellWidget(self._task_rows[tid], 2)
            if btn_widget:
                layout = btn_widget.layout()
                for i in range(layout.count()):
                    item = layout.itemAt(i)
                    if item and item.widget() and isinstance(item.widget(), qfw.PushButton):
                        w = item.widget()
                        if w.text() == "取消":
                            w.setText("删除")
                            w.clicked.disconnect()
                            w.clicked.connect(
                                lambda checked=False, t=tid: self._remove_task(t))
                            break
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
        if task_id in self._task_rows:
            row = self._task_rows[task_id]
            self.task_table.removeRow(row)
            del self._task_rows[task_id]
            for tid, r in list(self._task_rows.items()):
                if r > row:
                    self._task_rows[tid] = r - 1
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