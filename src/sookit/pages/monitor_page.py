"""
直播 / Premiere 自动监控 页面
"""
import os
import time

from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer

import qfluentwidgets as qfw

from sookit.core.functions import Functions, is_ytdlp_available, extract_youtube_id, fetch_youtube_metadata, load_download_config, DEFAULT_OUTPUT_DIR, ensure_output_dir
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
        if not is_ytdlp_available():
            qfw.InfoBar.warning(
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
        self.task_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
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

            self.task_table.item(row, 0).setText(
                f"等待中 ({int(wait_secs//60)}分{int(wait_secs%60)}秒)")

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
            count_timer.start(10000)
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
        mins = int(remaining // 60)
        secs = int(remaining % 60)
        self.task_table.item(self._task_rows[task_id], 0).setText(
            f"等待中 ({mins}分{secs}秒)")

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
        self._workers[task_id] = worker
        worker.start()
        self.log(f"[{task_id}] 开始监控 -> {title or url}")

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
        if task_id in self._task_rows:
            row = self._task_rows[task_id]
            self.task_table.item(row, 0).setText("已完成" if success else "失败")
        if success and task_id in self._task_info:
            info = self._task_info[task_id]
            self._download_best_thumbnail(task_id, info['url'], info['out_dir'], info['title'])
        else:
            qfw.InfoBar.error(parent=self, title="监控任务失败", content=f"任务 [{task_id}] 执行失败")
        self._workers.pop(task_id, None)
        if not self._workers:
            self._check_auto_action()

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