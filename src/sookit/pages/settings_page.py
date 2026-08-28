"""
设置 页面
"""
import os
import re
import json
import urllib.request

from PyQt6.QtWidgets import QVBoxLayout, QHBoxLayout, QWidget, QScrollArea, QFrame
from PyQt6.QtCore import Qt, QThread, QObject, pyqtSignal, pyqtSlot, QTimer

import qfluentwidgets as qfw

from sookit.core.functions import (
    is_ytdlp_available, get_ytdlp_source, build_ytdlp_cmd,
    launch_ytdlp_updater,
    get_ytdlp_current_version, get_deno_current_version,
    check_ytdlp_deno_update_needed,
    check_ffmpeg, check_aria2c, load_download_config, save_download_config,
    load_theme_color, save_theme_color, THEME_COLORS, get_ffmpeg_path,
    set_autostart, is_autostart, load_close_action, save_close_action,
    load_task_complete_action, save_task_complete_action,
)
from sookit.core.utils import get_scrollbar_style, get_certifi_ssl_context
from sookit import APP_NAME, APP_VERSION
from sookit.widgets.infobar import show_infobar


class SettingsPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._version_threads = []
        self._version_workers = []
        self._update_thread = None
        self._yt_current_ver = ""
        self._yt_update_checked = False
        self._update_progress = None
        # yt-dlp / Deno 版本检测结果（None=尚未返回；""=未安装/获取失败）
        self._yt_ver = None
        self._deno_ver = None
        self._init_ui()
        self._connect_signals()
        QTimer.singleShot(200, self._check_versions)

    def _init_ui(self):
        # 创建滚动区域
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        
        # 创建滚动内容容器
        scroll_content = QWidget()
        
        # 让滚动区域和内容容器背景透明，以正确跟随深色/浅色主题
        scroll_content.setStyleSheet("QWidget { background: transparent; }")
        
        # 设置滚动条样式（根据主题）
        self._update_scrollbar_style()
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(4)

        # 设置滚动区域的内容
        self.scroll_area.setWidget(scroll_content)
        
        # 将滚动区域添加到主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(self.scroll_area)

        # ===== 常规 =====
        _subtitle_style = "SubtitleLabel { font-weight: normal; }"
        self.setStyleSheet(_subtitle_style)
        layout.addWidget(qfw.SubtitleLabel("常规"))
        layout.addSpacing(12)

        autostart_card = qfw.CardWidget(self)
        autostart_card.setMinimumHeight(70)
        autostart_lay = QHBoxLayout(autostart_card)
        autostart_lay.setContentsMargins(15, 12, 15, 12)
        autostart_lay.addWidget(qfw.BodyLabel("开机自启"))
        autostart_lay.addStretch()
        self.autostart_switch = qfw.SwitchButton()
        self.autostart_switch.setChecked(False)
        autostart_lay.addWidget(self.autostart_switch)
        layout.addWidget(autostart_card)

        # 关闭主面板时
        close_action_card = qfw.CardWidget(self)
        close_action_card.setMinimumHeight(70)
        close_action_lay = QHBoxLayout(close_action_card)
        close_action_lay.setContentsMargins(15, 12, 15, 12)
        close_action_lay.addWidget(qfw.BodyLabel("关闭主面板时"))
        close_action_lay.addStretch()
        self.close_action_combo = qfw.ComboBox()
        self.close_action_combo.addItems(["最小化至托盘", "直接退出"])
        self.close_action_combo.setCurrentIndex(load_close_action())
        close_action_lay.addWidget(self.close_action_combo)
        layout.addWidget(close_action_card)

        # 任务完成后
        task_complete_card = qfw.CardWidget(self)
        task_complete_card.setMinimumHeight(70)
        task_complete_lay = QHBoxLayout(task_complete_card)
        task_complete_lay.setContentsMargins(15, 12, 15, 12)
        task_complete_lay.addWidget(qfw.BodyLabel("任务完成后"))
        task_complete_lay.addStretch()
        self.task_complete_combo = qfw.ComboBox()
        self.task_complete_combo.addItems(["不操作", "关闭工具箱", "关闭计算机"])
        self._last_task_complete_idx = load_task_complete_action()
        self.task_complete_combo.setCurrentIndex(self._last_task_complete_idx)
        task_complete_lay.addWidget(self.task_complete_combo)
        layout.addWidget(task_complete_card)

        layout.addSpacing(16)

        # ===== 下载/更新 =====
        layout.addWidget(qfw.SubtitleLabel("依赖更新"))
        layout.addSpacing(12)

        self.yt_card = qfw.CardWidget(self)
        self.yt_card.setMinimumHeight(70)
        yt_lay = QHBoxLayout(self.yt_card)
        yt_lay.setContentsMargins(15, 12, 15, 12)
        self.yt_label = qfw.BodyLabel("yt-dlp  —  获取中…")
        self.yt_btn = qfw.PrimaryPushButton("下载/更新")
        yt_lay.addWidget(self.yt_label, 1)
        yt_lay.addWidget(self.yt_btn)
        layout.addWidget(self.yt_card)

        layout.addSpacing(16)

        # ===== 下载设置 =====
        layout.addWidget(qfw.SubtitleLabel("下载设置"))
        layout.addSpacing(12)

        # 并发分片数
        fragments_card = qfw.CardWidget(self)
        fragments_card.setMinimumHeight(70)
        fragments_lay = QHBoxLayout(fragments_card)
        fragments_lay.setContentsMargins(15, 12, 15, 12)
        fragments_lay.addWidget(qfw.BodyLabel("并发分片数"))
        fragments_lay.addStretch()
        self.fragments_combo = qfw.ComboBox()
        self.fragments_combo.addItems([str(i) for i in range(1, 17)])
        self.fragments_combo.setCurrentIndex(9)  # 默认10
        fragments_lay.addWidget(self.fragments_combo)
        layout.addWidget(fragments_card)

        # aria2c 开关
        aria2c_switch_card = qfw.CardWidget(self)
        aria2c_switch_card.setMinimumHeight(70)
        aria2c_switch_lay = QHBoxLayout(aria2c_switch_card)
        aria2c_switch_lay.setContentsMargins(15, 12, 15, 12)
        aria2c_switch_lay.addWidget(qfw.BodyLabel("使用 aria2c 加速"))
        aria2c_switch_lay.addStretch()
        self.aria2c_switch = qfw.SwitchButton()
        self.aria2c_switch.setChecked(True)
        aria2c_switch_lay.addWidget(self.aria2c_switch)
        layout.addWidget(aria2c_switch_card)

        # aria2c 连接数
        connections_card = qfw.CardWidget(self)
        connections_card.setMinimumHeight(70)
        connections_lay = QHBoxLayout(connections_card)
        connections_lay.setContentsMargins(15, 12, 15, 12)
        connections_lay.addWidget(qfw.BodyLabel("aria2c 连接数"))
        connections_lay.addStretch()
        self.connections_combo = qfw.ComboBox()
        self.connections_combo.addItems([str(i) for i in range(4, 33, 4)])
        self.connections_combo.setCurrentIndex(3)  # 默认16
        connections_lay.addWidget(self.connections_combo)
        layout.addWidget(connections_card)

        layout.addSpacing(16)

        # ===== 个性化 =====
        layout.addWidget(qfw.SubtitleLabel("个性化"))
        layout.addSpacing(12)

        theme_card = qfw.SettingCard(qfw.FluentIcon.BRUSH, "应用主题", "切换浅色/深色模式", parent=self)
        theme_card.setFixedHeight(70)
        self.theme_combo = qfw.ComboBox()
        self.theme_combo.addItems(["浅色", "深色", "使用系统设置"])
        self.theme_combo.setCurrentIndex(2)
        theme_card.hBoxLayout.addSpacing(15)
        theme_card.hBoxLayout.addWidget(self.theme_combo)
        m = theme_card.hBoxLayout.contentsMargins()
        m.setRight(15)
        theme_card.hBoxLayout.setContentsMargins(m)
        layout.addWidget(theme_card)

        # 主题颜色选择
        color_card = qfw.SettingCard(qfw.FluentIcon.PALETTE, "主题颜色", "更改软件的强调色", parent=self)
        color_card.setFixedHeight(70)
        self.color_btn = qfw.PrimaryDropDownPushButton("主题颜色")
        self.color_menu = qfw.RoundMenu("主题颜色", self)
        self._color_actions = []
        for color_name in THEME_COLORS.keys():
            action = qfw.Action(color_name, self)
            action.setCheckable(True)
            self.color_menu.addAction(action)
            self._color_actions.append(action)
        self.color_btn.setMenu(self.color_menu)
        color_card.hBoxLayout.addSpacing(15)
        color_card.hBoxLayout.addWidget(self.color_btn)
        # 设置右侧边距
        m = color_card.hBoxLayout.contentsMargins()
        m.setRight(15)
        color_card.hBoxLayout.setContentsMargins(m)
        layout.addWidget(color_card)

        layout.addSpacing(16)

        # ===== 关于 =====
        layout.addWidget(qfw.SubtitleLabel("关于"))
        layout.addSpacing(12)

        about_card = qfw.CardWidget(self)
        about_card.setMinimumHeight(70)
        about_lay = QHBoxLayout(about_card)
        about_lay.setContentsMargins(15, 12, 15, 12)
        about_lay.addWidget(qfw.BodyLabel(f"{APP_NAME} {APP_VERSION}"))
        about_lay.addStretch()
        self.check_update_btn = qfw.PrimaryPushButton("检查更新")
        about_lay.addWidget(self.check_update_btn)
        layout.addWidget(about_card)

        self.ff_card = qfw.CardWidget(self)
        self.ff_card.setMinimumHeight(70)
        ff_lay = QHBoxLayout(self.ff_card)
        ff_lay.setContentsMargins(15, 12, 15, 12)
        self.ff_label = qfw.BodyLabel("FFmpeg  —  获取中…")
        ff_lay.addWidget(self.ff_label, 1)
        layout.addWidget(self.ff_card)

        # aria2c 版本信息（已嵌入，无需下载按钮）
        self.aria2c_card = qfw.CardWidget(self)
        self.aria2c_card.setMinimumHeight(70)
        aria2c_lay = QHBoxLayout(self.aria2c_card)
        aria2c_lay.setContentsMargins(15, 12, 15, 12)
        self.aria2c_label = qfw.BodyLabel("aria2c  —  检测中…")
        aria2c_lay.addWidget(self.aria2c_label, 1)
        layout.addWidget(self.aria2c_card)

        layout.addStretch()

    def _connect_signals(self):
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.color_menu.triggered.connect(self._on_color_changed)
        self.yt_btn.clicked.connect(lambda: self._update_ytdlp())
        # 下载设置信号连接
        self.fragments_combo.currentIndexChanged.connect(self._on_download_setting_changed)
        self.aria2c_switch.checkedChanged.connect(self._on_download_setting_changed)
        self.connections_combo.currentIndexChanged.connect(self._on_download_setting_changed)
        # 开机自启信号连接
        self.autostart_switch.checkedChanged.connect(self._on_autostart_changed)
        # 关闭行为信号连接
        self.close_action_combo.currentIndexChanged.connect(self._on_close_action_changed)
        # 任务完成后信号连接
        self.task_complete_combo.currentIndexChanged.connect(self._on_task_complete_changed)
        # 检查更新按钮信号连接
        self.check_update_btn.clicked.connect(self._on_check_update)

    def _on_theme_changed(self, idx):
        mapping = {0: qfw.Theme.LIGHT, 1: qfw.Theme.DARK, 2: qfw.Theme.AUTO}
        qfw.setTheme(mapping[idx], save=True)
        # 主题改变后更新滚动条样式
        QTimer.singleShot(100, self._update_scrollbar_style)
    
    def _on_color_changed(self, action):
        """主题颜色变更处理"""
        color_name = action.text()
        if color_name in THEME_COLORS:
            color = THEME_COLORS[color_name]
            qfw.setThemeColor(color)
            save_theme_color(color)
            # 更新按钮文本
            self.color_btn.setText(color_name)
            # 更新勾选状态
            for a in self._color_actions:
                a.setChecked(a == action)
    
    def _update_scrollbar_style(self):
        """根据当前主题更新滚动条样式"""
        self.scroll_area.setStyleSheet(get_scrollbar_style(qfw.isDarkTheme()))

    def _on_download_setting_changed(self):
        """下载设置改变时保存配置"""
        download_config = {
            'concurrent_fragments': int(self.fragments_combo.currentText()),
            'use_aria2c': self.aria2c_switch.isChecked(),
            'aria2c_connections': int(self.connections_combo.currentText()),
        }
        save_download_config(download_config)

    def _on_autostart_changed(self, checked: bool):
        """开机自启开关变更"""
        set_autostart(checked)

    def _on_close_action_changed(self, index: int):
        """关闭行为变更: 0=最小化至托盘, 1=直接退出"""
        save_close_action(index)

    def _on_task_complete_changed(self, index: int):
        """任务完成后变更: 0=不操作, 1=关闭工具箱, 2=关闭计算机"""
        # 如果从"关闭计算机"切换到其他选项，取消关机
        if hasattr(self, '_last_task_complete_idx') and self._last_task_complete_idx == 2 and index != 2:
            os.system("shutdown /a >nul 2>nul")
        self._last_task_complete_idx = index
        save_task_complete_action(index)

    def _on_check_update(self):
        """「检查更新」按钮：复用主窗口更新流程（后台查询 → 弹 Dialog）"""
        win = self.window()
        if win is not None and hasattr(win, "check_update_manual"):
            self.check_update_btn.setEnabled(False)
            # 手动检查完成后恢复按钮可用
            try:
                win.check_update_manual()
            finally:
                self.check_update_btn.setEnabled(True)
        else:
            show_infobar(self, "info", title="提示",
                         content="未找到主窗口，无法检查更新", duration=5000)

    @staticmethod
    def _parse_ytdlp_version(text: str) -> str:
        """yt-dlp 版本：第一非空行"""
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("WARNING"):
                return line
        return ""

    @staticmethod
    def _parse_ffmpeg_version(text: str) -> str:
        """ffmpeg 版本：从 'ffmpeg version X.Y.Z' 中提取"""
        m = re.search(r'ffmpeg\s+version\s+(\S+)', text, re.IGNORECASE)
        return m.group(1) if m else ""

    def _check_versions(self):
        """并行检测工具的版本（yt-dlp 与 Deno 并行）"""
        self._version_threads = []
        self._version_workers = []   # 保活 worker，防止 GC
        self._yt_ver = None
        self._deno_ver = None
        source = get_ytdlp_source()
        # yt-dlp（PATH 全局或内置 tools/ 均可）
        if is_ytdlp_available():
            w = self._create_worker(build_ytdlp_cmd("--version"), self._parse_ytdlp_version)
            if w:
                self._version_workers.append(w)
                w.finished.connect(self._on_yt_version)
                w.thread().start()
            # 内置 yt-dlp 时才需检测 Deno（PATH 全局版不管 Deno，按需求）
            if source == "tools":
                dw = self._create_deno_worker()
                if dw:
                    self._version_workers.append(dw)
                    dw.finished.connect(self._on_deno_version)
                    dw.thread().start()
                else:
                    # worker 创建失败兜底：当作未检测到 Deno，避免版本标签卡在等待
                    self._deno_ver = ""
        else:
            self._yt_ver = ""
            self._render_yt_label()
        # ffmpeg（版本信息在 stderr）
        if check_ffmpeg():
            # 检查是否使用内嵌版本
            ffmpeg_path = get_ffmpeg_path()
            is_embedded = os.path.exists(ffmpeg_path)
            if is_embedded:
                w = self._create_worker([ffmpeg_path, "-version"], self._parse_ffmpeg_version)
            else:
                w = self._create_worker(["ffmpeg", "-version"], self._parse_ffmpeg_version)
            if w:
                self._version_workers.append(w)
                w.finished.connect(self._on_ff_version)
                w.thread().start()
        else:
            self.ff_label.setText("FFmpeg  —  未安装")
        # aria2c 版本检测
        aria2c_ok, aria2c_ver = check_aria2c()
        if aria2c_ok:
            self.aria2c_label.setText(f"aria2c  —  {aria2c_ver}")
        else:
            self.aria2c_label.setText(f"aria2c  —  {aria2c_ver}")
        
        # 加载常规配置
        self._load_general_config()
        # 加载下载配置
        self._load_download_config()
        # 加载主题颜色配置
        self._load_theme_config()

    def _load_download_config(self):
        """加载下载配置到 UI"""
        config = load_download_config()
        self.fragments_combo.setCurrentText(str(config['concurrent_fragments']))
        self.aria2c_switch.setChecked(config['use_aria2c'])
        self.connections_combo.setCurrentText(str(config['aria2c_connections']))
    
    def _load_theme_config(self):
        """加载主题颜色配置到 UI"""
        current_color = load_theme_color()
        # 根据颜色值找到对应的名称
        for name, color in THEME_COLORS.items():
            if color == current_color:
                self.color_btn.setText(name)
                # 设置对应的勾选状态
                for action in self._color_actions:
                    action.setChecked(action.text() == name)
                break

    def _load_general_config(self):
        """加载常规配置到 UI（开机自启状态、任务完成后）"""
        self.autostart_switch.setChecked(is_autostart())
        self.task_complete_combo.setCurrentIndex(load_task_complete_action())

    def _create_worker(self, cmd, parser):
        """创建后台线程 worker，返回 worker 对象"""
        try:
            thread = QThread(self)
            from sookit.core.workers import SingleCmdWorker
            worker = SingleCmdWorker(cmd, parser)
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            self._version_threads.append(thread)
            return worker
        except Exception:
            return None

    def _create_deno_worker(self):
        """创建后台线程 worker 检测内置 Deno 版本，返回 worker 对象"""
        try:
            thread = QThread(self)

            class _DenoWorker(QObject):
                finished = pyqtSignal(object)
                def run(s):
                    s.finished.emit(get_deno_current_version())

            worker = _DenoWorker()
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            self._version_threads.append(thread)
            return worker
        except Exception:
            return None

    @pyqtSlot(object)
    def _on_yt_version(self, v):
        self._yt_ver = v if v else ""
        self._render_yt_label()
        # 版本检测完成后，顺便查 PyPI 是否有新版（仅一次）
        if v and not self._yt_update_checked:
            self._yt_update_checked = True
            self._check_yt_update_needed(v)

    @pyqtSlot(object)
    def _on_deno_version(self, v):
        self._deno_ver = v if v else ""
        self._render_yt_label()

    def _render_yt_label(self):
        """组合渲染 yt-dlp 版本标签。

        - PATH 来源：只显示 yt-dlp（不管 Deno）
        - 内置来源：显示 yt-dlp + Deno（Deno 缺失显示「未安装」）
        - 未安装：显示「未安装」
        """
        source = get_ytdlp_source()
        if source == "tools":
            # 内置来源：需 yt-dlp 与 Deno 版本都检测完才渲染
            if self._yt_ver is None or self._deno_ver is None:
                return
            yt_txt = self._yt_ver if self._yt_ver else "未知"
            deno_txt = self._deno_ver if self._deno_ver else "未安装"
            self.yt_label.setText(f"yt-dlp（内置）  —  {yt_txt} / Deno  —  {deno_txt}")
        elif source == "path":
            yt_txt = self._yt_ver if self._yt_ver else "未知"
            self.yt_label.setText(f"yt-dlp（PATH）  —  {yt_txt}")
        else:
            self.yt_label.setText("yt-dlp  —  未安装")

    @pyqtSlot(object)
    def _on_ff_version(self, v):
        if v:
            self.ff_label.setText(f"FFmpeg  —  {v}")
        else:
            self.ff_label.setText("FFmpeg  —  未安装")

    def _check_yt_update_needed(self, current_ver: str):
        """后台通过 PyPI JSON API 查最新版，有新版本则弹窗"""
        class _PyPIVersionWorker(QObject):
            done = pyqtSignal(object)
            def run(s):
                try:
                    url = "https://pypi.org/pypi/yt-dlp/json"
                    with urllib.request.urlopen(url, timeout=10, context=get_certifi_ssl_context()) as resp:
                        data = json.loads(resp.read().decode())
                    s.done.emit(data.get("info", {}).get("version", ""))
                except Exception:
                    s.done.emit("")

        self._yt_current_ver = current_ver
        thread = QThread(self)
        w = _PyPIVersionWorker()
        w.moveToThread(thread)
        thread.started.connect(w.run)
        w.done.connect(self._on_pypi_version)
        w.done.connect(thread.quit)
        w.done.connect(w.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._version_workers.append(w)
        self._version_threads.append(thread)

    @staticmethod
    def _normalize_version(v: str) -> str:
        """标准化版本号：2026.03.17 → 2026.3.17（去前导零）"""
        try:
            return ".".join(str(int(p)) for p in v.split("."))
        except Exception:
            return v

    @pyqtSlot(object)
    def _on_pypi_version(self, ver):
        """在主线程比较版本并决定是否弹窗（标准化后比较，避免前导零干扰）"""
        if not ver:
            return
        cur = self._normalize_version(self._yt_current_ver)
        lat = self._normalize_version(ver)
        if lat and cur and lat > cur:
            dialog = qfw.Dialog(
                "yt-dlp 有新版本",
                f"当前版本: {cur}    最新版本: {lat}\n"
                "不更新可能会导致视频嗅探失败，是否更新？",
                self)
            dialog.yesButton.setText("更新")
            dialog.cancelButton.setText("取消")
            if dialog.exec():
                self._update_ytdlp(skip_check=True)  # 已确认有新版本，跳过检查态直接更新

    def _update_ytdlp(self, skip_check: bool = False):
        """按来源三分支处理下载/更新：
        - path → 仅提示自行更新
        - tools → 先「检查中」查新版本（skip_check=True 时跳过，如自动更新路径已确认有新版），
                  确需更新才切「更新中」提权下载；查询失败如实弹错误并引导手动更新
        - None  → 未安装，直接「安装中」提权下载到 tools/yt-dlp/（含 Deno 运行时）
        """
        source = get_ytdlp_source()
        if source == "path":
            show_infobar(self, "info", title="提示",
                         content="检测到 PATH 中的全局 yt-dlp，请自行更新",
                         duration=6000)
            return

        action = "更新" if source == "tools" else "安装"
        self.yt_btn.setEnabled(False)
        if source == "tools" and not skip_check:
            # 检查态：先普通权限查新版本，确需更新才提权下载
            self.yt_btn.setText("检查中…")
            self.yt_label.setText("yt-dlp  —  正在检查新版本…")
            if self._update_progress:
                self._update_progress.close()
            self._update_progress = show_infobar(
                self, "info", title="检查中",
                content="正在检查 yt-dlp（含 Deno 运行时）的新版本…", closable=False)
            self._start_check()
        else:
            # 未安装 / 已确认有新版本：直接进入下载态
            self.yt_btn.setText("下载中…")
            self.yt_label.setText(f"正在{action} yt-dlp…")
            if self._update_progress:
                self._update_progress.close()
            self._update_progress = show_infobar(
                self, "info", title=f"{action}中",
                content=f"正在{action} yt-dlp（含 Deno 运行时）…", closable=False)
            self._start_download()

    def _start_check(self):
        """后台线程检查 yt-dlp/Deno 是否需要更新（普通权限，不下载）"""
        class _YtdlpCheckWorker(QObject):
            done = pyqtSignal(object)
            def run(s):
                try:
                    s.done.emit(check_ytdlp_deno_update_needed())
                except Exception as e:  # noqa: BLE001 极端异常同样按查询失败处理
                    s.done.emit((False, "check_failed", str(e),
                                 False, "check_failed", str(e)))

        thread = QThread(self)
        w = _YtdlpCheckWorker()
        w.moveToThread(thread)
        thread.started.connect(w.run)
        w.done.connect(self._on_check_done)
        w.done.connect(thread.quit)
        w.done.connect(w.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._update_worker = w
        self._update_thread = thread

    @pyqtSlot(object)
    def _on_check_done(self, result):
        """检查完成：已最新→汇总提示；需更新→切「更新中」下载态；查询失败→错误提示引导手动更新"""
        yt_needed, deno_needed, yt_state, deno_state = result
        any_check_failed = yt_state == "check_failed" or deno_state == "check_failed"

        if any_check_failed and not yt_needed and not deno_needed:
            # 查询失败且没有确定要更新的组件：不下载，如实告知并引导自行更新
            if self._update_progress:
                self._update_progress.close()
                self._update_progress = None
            self.yt_btn.setEnabled(True)
            self.yt_btn.setText("下载/更新")
            self.yt_label.setText("yt-dlp / Deno  —  检查更新失败")
            show_infobar(self, "error", title="检查更新失败",
                                 content="无法从 GitHub 获取 yt-dlp/Deno 最新版本信息，请检查网络或代理后重试；"
                                         "也可前往 https://github.com/yt-dlp/yt-dlp/releases 手动下载，"
                                         "覆盖程序目录 tools\\yt-dlp\\ 下的 yt-dlp.exe")
            return

        if not yt_needed and not deno_needed:
            self._on_ytdlp_download_done((True, "up_to_date", "", True, "up_to_date", ""))
            return

        # 确需更新（含部分组件查询失败但另一组件确定需更新，失败组件交 updater 自行判断）
        if self._update_progress:
            self._update_progress.close()
        self.yt_btn.setText("下载中…")
        self.yt_label.setText("正在更新 yt-dlp…")
        self._update_progress = show_infobar(
            self, "info", title="更新中",
            content="正在更新 yt-dlp（含 Deno 运行时）…", closable=False)
        self._start_download()

    def _start_download(self):
        """后台线程提权调起 updater.exe 下载/更新 yt-dlp 与 Deno，
        进度经 yt_label 文字回显。返回 (yt_ok, yt_status, yt_err, deno_ok, deno_status, deno_err)"""
        class _YtdlpDownloadWorker(QObject):
            progress = pyqtSignal(str)
            done = pyqtSignal(object)
            def run(s):
                try:
                    result = launch_ytdlp_updater(lambda t: s.progress.emit(t))
                except Exception as e:  # noqa: BLE001
                    result = (False, "failed", str(e), False, "failed", str(e))
                s.done.emit(result)

        thread = QThread(self)
        w = _YtdlpDownloadWorker()
        w.moveToThread(thread)
        thread.started.connect(w.run)
        w.progress.connect(self._on_ytdlp_progress)
        w.done.connect(self._on_ytdlp_download_done)
        w.done.connect(thread.quit)
        w.done.connect(w.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()
        self._update_worker = w
        self._update_thread = thread

    @pyqtSlot(str)
    def _on_ytdlp_progress(self, text: str):
        """下载进度 → 直接更新 yt_label 文字（如：下载 yt-dlp.exe — 23% (4.0 MB / 17.3 MB)）"""
        self.yt_label.setText(text)

    @pyqtSlot(object)
    def _on_ytdlp_download_done(self, result):
        yt_ok, yt_status, yt_err, deno_ok, deno_status, deno_err = result
        if self._update_progress:
            self._update_progress.close()
            self._update_progress = None
        self.yt_btn.setEnabled(True)
        self.yt_btn.setText("下载/更新")

        # 组装各组件状态描述
        def _comp_state(name, ok, status):
            if not ok:
                return f"{name} 失败"
            return f"{name} 已更新" if status == "updated" else f"{name} 已最新"

        yt_state = _comp_state("yt-dlp", yt_ok, yt_status)
        deno_state = _comp_state("Deno", deno_ok, deno_status)
        any_updated = (yt_ok and yt_status == "updated") or (deno_ok and deno_status == "updated")
        all_up_to_date = (yt_ok and yt_status == "up_to_date") and (deno_ok and deno_status == "up_to_date")
        any_failed = not yt_ok or not deno_ok

        if all_up_to_date:
            # 两者都已最新
            self.yt_label.setText("yt-dlp / Deno  —  已是最新版本")
            show_infobar(self, "info", title="提示",
                         content="yt-dlp 与 Deno 均已是最新版本，无需更新",
                         duration=5000)
        elif any_failed:
            self.yt_label.setText("yt-dlp / Deno  —  更新异常")
            show_infobar(self, "error", title="部分更新失败",
                                 content=f"yt-dlp: {yt_state}\nDeno: {deno_state}"
                                         + (f"\n\nyt-dlp 错误: {yt_err}" if not yt_ok else "")
                                         + (f"\nDeno 错误: {deno_err}" if not deno_ok else ""))
        elif any_updated:
            show_infobar(self, "success", title="完成",
                         content=f"{yt_state}；{deno_state}", duration=5000)
            # 装好后刷新主窗口及各页面「未找到 yt-dlp」提示（修复2）
            win = self.window()
            if win is not None and hasattr(win, "refresh_ytdlp_status"):
                win.refresh_ytdlp_status()
        # 刷新版本
        QTimer.singleShot(1000, self._check_versions)