"""嗅探页字幕选择；仅管理展示与选择，下载由页面交给任务队列。"""

from PyQt6.QtCore import Qt, QSignalBlocker, pyqtSignal
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QStyle, QStyleOptionButton, QVBoxLayout, QWidget
import qfluentwidgets as qfw
from qfluentwidgets.components.widgets.check_box import CheckBoxIcon

from sookit.core.utils import set_smooth_scroll_params


_AUTO_TRANSLATION_LANGUAGE_ORDER = ('zh-hans', 'zh-hant', 'en')


def _track_sort_key(track):
    if track.get('source') == 'automatic':
        if track.get('automatic_kind') == 'generated':
            return 0, 0
        if track.get('automatic_kind') == 'translated':
            language = str(track.get('language') or '').casefold()
            for index, preferred in enumerate(_AUTO_TRANSLATION_LANGUAGE_ORDER):
                if language == preferred or language.startswith(preferred + '-'):
                    return 1, index
            return 1, len(_AUTO_TRANSLATION_LANGUAGE_ORDER)
        return 2, 0
    return 3, 0


class CommentDownloadDialog(qfw.Dialog):
    """实时评论使用独立选择器，保留站点提供的 JSON / XML 等原格式。"""

    def __init__(self, tracks, parent=None):
        super().__init__(
            "下载实时评论",
            "按站点提供的原格式保存，实时评论不转换为字幕。",
            parent)
        self._tracks = [dict(track) for track in tracks]
        self.titleLabel.hide()
        self.track_combo = qfw.ComboBox()
        for track in self._tracks:
            formats = " / ".join(str(ext).upper() for ext in track.get('formats', []))
            self.track_combo.addItem(
                f"{track.get('name') or track['language']} · {track['language']}"
                f" · {formats or '原格式'}")
        self.textLayout.addWidget(self.track_combo)
        self.yesButton.setText("加入下载队列")
        self.cancelButton.setText("取消")
        self.yesButton.setEnabled(bool(self._tracks))
        self.vBoxLayout.activate()
        size_hint = self.vBoxLayout.sizeHint()
        self.setFixedSize(max(360, size_hint.width()), max(240, size_hint.height()))

    def selected_track(self):
        index = self.track_combo.currentIndex()
        return dict(self._tracks[index]) if 0 <= index < len(self._tracks) else None


class CircularCheckBox(qfw.CheckBox):
    """字幕轨道选择框，保留 Fluent 状态色并绘制圆形指示器。"""

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        option = QStyleOptionButton()
        option.initFrom(self)
        rect = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
        painter.setPen(self._borderColor())
        painter.setBrush(self._backgroundColor())
        painter.drawEllipse(rect)

        if not self.isEnabled():
            painter.setOpacity(0.8)

        if self.checkState() == Qt.CheckState.Checked:
            CheckBoxIcon.ACCEPT.render(painter, rect)
        elif self.checkState() == Qt.CheckState.PartiallyChecked:
            CheckBoxIcon.PARTIAL_ACCEPT.render(painter, rect)


class SubtitlePanel(QWidget):
    """逐轨选择、可见项全选与字幕输出格式；切换内容 tab 不重建面板。"""

    download_requested = pyqtSignal()
    comments_requested = pyqtSignal()
    selection_changed = pyqtSignal()

    def __init__(self, parent=None, right_margin=24):
        super().__init__(parent)
        self._tracks = []
        self._comment_tracks = []
        self._selected_keys = set()
        self._rows = {}
        self._visible_keys = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        tools = QHBoxLayout()
        tools.setSpacing(18)
        tools.setContentsMargins(0, 0, right_margin, 0)
        self.search_input = qfw.SearchLineEdit()
        self.search_input.setPlaceholderText("搜索字幕语言或语言码")
        self.search_input.setMinimumWidth(0)
        self.search_input.textChanged.connect(self._apply_filter)
        tools.addWidget(self.search_input, 1)
        automatic_controls = QHBoxLayout()
        automatic_controls.setContentsMargins(0, 0, 0, 0)
        automatic_controls.setSpacing(0)
        self.automatic_label = qfw.BodyLabel("显示自动生成字幕")
        automatic_controls.addWidget(self.automatic_label)
        self.automatic_switch = qfw.SwitchButton(indicatorPos=qfw.IndicatorPosition.RIGHT)
        self.automatic_switch.setOffText("")
        self.automatic_switch.setOnText("")
        self.automatic_switch.setSpacing(0)
        self.automatic_switch.hBox.setContentsMargins(0, 0, 0, 0)
        self.automatic_switch.checkedChanged.connect(self._on_automatic_toggled)
        automatic_controls.addWidget(self.automatic_switch)
        tools.addLayout(automatic_controls)
        layout.addLayout(tools)

        selection_row = QHBoxLayout()
        selection_row.setContentsMargins(0, 0, right_margin, 0)
        self.select_all = qfw.CheckBox("全选")
        self.select_all.clicked.connect(self._select_visible)
        selection_row.addWidget(self.select_all)
        selection_row.addStretch()
        self.selection_label = qfw.CaptionLabel()
        selection_row.addWidget(self.selection_label)
        layout.addLayout(selection_row)

        self.track_scroll = qfw.ScrollArea()
        self.track_scroll.setWidgetResizable(True)
        self.track_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.track_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.track_scroll.enableTransparentBackground()
        set_smooth_scroll_params(self.track_scroll)
        content = QWidget()
        content.setStyleSheet("QWidget { background: transparent; }")
        self.track_layout = QVBoxLayout(content)
        self.track_layout.setContentsMargins(0, 0, right_margin, 0)
        self.track_layout.setSpacing(8)
        self.track_layout.addStretch(1)
        self.track_scroll.setWidget(content)
        layout.addWidget(self.track_scroll, 1)

        self.empty_widget = QWidget()
        empty_layout = QVBoxLayout(self.empty_widget)
        empty_layout.setContentsMargins(0, 0, right_margin, 0)
        empty_layout.addStretch()
        self.empty_hint = qfw.BodyLabel()
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setStyleSheet("color: #9a9a9a; background: transparent;")
        empty_layout.addWidget(self.empty_hint)
        empty_layout.addStretch()
        layout.addWidget(self.empty_widget, 1)

        formats_row = QHBoxLayout()
        formats_row.setContentsMargins(0, 0, right_margin, 0)
        formats_row.addWidget(qfw.BodyLabel("输出格式:"))
        self.format_checks = {}
        for ext in ('srt', 'txt', 'vtt'):
            check = qfw.CheckBox(ext.upper())
            check.setChecked(ext == 'srt')
            check.toggled.connect(self._sync_selection)
            self.format_checks[ext] = check
            formats_row.addWidget(check)
        formats_row.addStretch()
        self.comments_button = qfw.PushButton("下载实时评论")
        self.comments_button.clicked.connect(self.comments_requested.emit)
        formats_row.addWidget(self.comments_button)
        layout.addLayout(formats_row)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, right_margin, 0)
        self.download_button = qfw.PrimaryPushButton()
        self.download_button.setFixedSize(240, 32)
        self.download_button.clicked.connect(self.download_requested.emit)
        buttons.addWidget(self.download_button, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(buttons)
        self.clear()

    def set_tracks(self, tracks, comment_tracks=None):
        self._tracks = sorted(
            (dict(track) for track in tracks
             if track.get('source') in ('platform', 'automatic')),
            key=_track_sort_key)
        self._comment_tracks = [dict(track) for track in (comment_tracks or [])]
        self._selected_keys.clear()
        self.automatic_switch.blockSignals(True)
        self.automatic_switch.setChecked(False)
        self.automatic_switch.blockSignals(False)
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.comments_button.setEnabled(bool(self._comment_tracks))
        self.automatic_switch.setEnabled(any(t['source'] == 'automatic' for t in self._tracks))
        while self.track_layout.count() > 1:
            widget = self.track_layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self._rows = {}
        for track in self._tracks:
            row = qfw.CardWidget()
            row.setCursor(Qt.CursorShape.PointingHandCursor)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(12, 9, 12, 9)
            check = CircularCheckBox()
            check.toggled.connect(lambda checked, key=track['key']: self._set_selected(key, checked))
            row.clicked.connect(check.toggle)
            row_layout.addWidget(check)
            text_layout = QVBoxLayout()
            text_layout.setSpacing(2)
            name = qfw.BodyLabel(track.get('name') or track['language'])
            name.setWordWrap(True)
            name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            text_layout.addWidget(name)
            if track['source'] == 'platform':
                source = '平台字幕'
            else:
                source = {
                    'generated': '自动生成字幕',
                    'translated': '机翻字幕',
                }.get(track.get('automatic_kind'), '自动字幕/翻译')
            details = qfw.CaptionLabel(f"{track['language']} · {source}")
            details.setWordWrap(True)
            details.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            text_layout.addWidget(details)
            row_layout.addLayout(text_layout, 1)
            self.track_layout.insertWidget(self.track_layout.count() - 1, row)
            self._rows[track['key']] = (row, check)
        self._apply_filter()

    def clear(self):
        self.set_tracks([], [])

    def selected_tracks(self):
        return [dict(track) for track in self._tracks if track['key'] in self._selected_keys]

    def selected_formats(self):
        return [ext for ext, check in self.format_checks.items() if check.isChecked()]

    def download_count(self):
        return len(self._selected_keys) * len(self.selected_formats())

    def _set_selected(self, key, checked):
        if checked:
            self._selected_keys.add(key)
        else:
            self._selected_keys.discard(key)
        self._sync_selection()

    def _select_visible(self, checked):
        # 批量操作只汇总一次；逐轨触发会对数百条字幕反复扫描可见列表。
        for key in self._visible_keys:
            with QSignalBlocker(self._rows[key][1]):
                self._rows[key][1].setChecked(checked)
        if checked:
            self._selected_keys.update(self._visible_keys)
        else:
            self._selected_keys.difference_update(self._visible_keys)
        self._sync_selection()

    def _on_automatic_toggled(self, checked):
        if not checked:
            for track in self._tracks:
                if track['source'] == 'automatic':
                    key = track['key']
                    with QSignalBlocker(self._rows[key][1]):
                        self._rows[key][1].setChecked(False)
                    self._selected_keys.discard(key)
        self._apply_filter()

    def _apply_filter(self, *_):
        query = self.search_input.text().strip().casefold()
        self._visible_keys = []
        automatic = self.automatic_switch.isChecked()
        available = [track for track in self._tracks
                     if automatic or track['source'] != 'automatic']
        for track in self._tracks:
            visible = ((automatic or track['source'] != 'automatic')
                       and (not query or query in
                            f"{track.get('name', '')} {track['language']}".casefold()))
            self._rows[track['key']][0].setVisible(visible)
            if visible:
                self._visible_keys.append(track['key'])
        has_visible = bool(self._visible_keys)
        self.track_scroll.setVisible(has_visible)
        self.empty_widget.setVisible(not has_visible)
        automatic_count = sum(t['source'] == 'automatic' for t in self._tracks)
        only_hidden_automatic = not available and bool(automatic_count)
        if only_hidden_automatic:
            text = f"发现{automatic_count}条自动字幕"
        elif query and available:
            text = "没有匹配的字幕语言，请更换搜索词"
        elif self._comment_tracks:
            text = "未发现字幕；可用“输出格式”右侧的“下载实时评论”按钮单独下载"
        else:
            text = "未发现可下载字幕"
        self.empty_hint.setText(text)
        self._sync_selection()

    def _sync_selection(self, *_):
        self.select_all.setEnabled(bool(self._visible_keys))
        self.select_all.setChecked(bool(self._visible_keys) and
                                   all(key in self._selected_keys for key in self._visible_keys))
        tracks = len(self._selected_keys)
        formats = len(self.selected_formats())
        count = tracks * formats
        self.selection_label.setText(f"已选 {tracks} 条 · 当前显示 {len(self._visible_keys)} 条")
        self.download_button.setText(f"▶ 下载字幕（{count}）")
        self.download_button.setEnabled(bool(count))
        self.selection_changed.emit()
