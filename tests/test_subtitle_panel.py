"""字幕 UI 离屏回归：无联网、无真实队列、无配置写入。

运行：uv run --no-sync python -m unittest discover -s tests -p test_subtitle_panel.py
"""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QObject, QPoint, Qt, pyqtSignal
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication, QLabel

from sookit.core.config import SNIFF_VIEW_DEFAULT, SNIFF_VIEW_TABLE
from sookit.core.task_queue import TaskType
from sookit.core.workers import RESERVED_TASK_KWARGS
from sookit.pages.youtube_page import YouTubePage
from sookit.widgets.subtitle_panel import SubtitlePanel, CommentDownloadDialog


def track(language, source='platform', name=None, formats=None):
    return {'key': f'{source}:{language}', 'language': language,
            'name': name or language, 'source': source,
            'formats': formats or ['vtt'],
            'bucket': 'automatic_captions' if source == 'automatic' else 'subtitles',
            'title': 'Original track title'}


PLATFORM = track('en-US', name='English (United States)')
CHINESE = track('zh-Hant', name='繁體中文')
AUTOMATIC = track('en-US', 'automatic', 'English 自动翻译')
COMMENT = track('live_chat', 'comment', '实时评论', ['json'])
FORMATS = [
    {'format_id': '18', 'ext': 'mp4', 'type': '视频+音频', 'quality': '360p',
     'vcodec': 'avc1', 'acodec': 'aac', 'height': 360, 'fps': 30},
    {'format_id': '22', 'ext': 'mp4', 'type': '视频+音频', 'quality': '720p',
     'vcodec': 'avc1', 'acodec': 'aac', 'height': 720, 'fps': 30},
]


class FakeSniffWorker(QObject):
    done = pyqtSignal(object)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, func, args=None, kwargs=None):
        super().__init__()
        self.args = args
        self.running = False
        self.stopped = False

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def request_stop(self):
        self.stopped = True

    def wait(self, timeout):
        return True

    def terminate(self):
        self.running = False


class QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font_path = 'C:/Windows/Fonts/msyh.ttc'
        if os.path.exists(font_path):
            QFontDatabase.addApplicationFont(font_path)
            cls.app.setFont(QFont('Microsoft YaHei', 9))

    def settle(self):
        self.app.processEvents()
        self.app.processEvents()

    def dispose(self, widget):
        widget.close()
        widget.deleteLater()
        self.settle()


class SubtitlePanelTests(QtCase):
    def setUp(self):
        self.panel = SubtitlePanel()
        self.panel.resize(600, 520)
        self.panel.show()
        self.panel.set_tracks([PLATFORM, CHINESE, AUTOMATIC], [COMMENT])
        self.settle()
        self.addCleanup(self.dispose, self.panel)

    def selected_keys(self):
        return {t['key'] for t in self.panel.selected_tracks()}

    def test_source_labels_and_original_language_tags(self):
        texts = [label.text() for label in self.panel.findChildren(QLabel)]
        self.assertIn('English (United States)', texts)
        self.assertIn('en-US · 平台字幕', texts)
        self.assertIn('en-US · 自动字幕/翻译', texts)
        self.assertFalse(any('人工' in text for text in texts))
        self.assertEqual(set(self.panel._visible_keys), {PLATFORM['key'], CHINESE['key']})
        self.assertEqual(self.panel.selected_formats(), ['srt'])
        self.assertFalse(self.panel.download_button.isEnabled())
        self.assertIn('选择一条', self.panel.download_hint.text())

    def test_filter_preserves_selection_and_all_only_changes_visible(self):
        self.panel.search_input.setText('EN-us')
        self.panel.select_all.click()
        self.assertEqual(self.selected_keys(), {PLATFORM['key']})
        self.panel.search_input.setText('繁體')
        self.assertEqual(self.selected_keys(), {PLATFORM['key']})
        self.panel.select_all.click()
        self.assertEqual(self.selected_keys(), {PLATFORM['key'], CHINESE['key']})
        self.panel.select_all.click()
        self.assertEqual(self.selected_keys(), {PLATFORM['key']})
        self.panel.search_input.clear()
        self.assertEqual(self.selected_keys(), {PLATFORM['key']})
        self.assertFalse(self.panel.select_all.isChecked())
        self.panel.search_input.setText('unmatched-language')
        self.assertFalse(self.panel.select_all.isEnabled())
        self.assertIn('没有匹配', self.panel.empty_hint.text())
        self.assertEqual(self.panel.download_count(), 1)

    def test_automatic_off_clears_hidden_selections_even_under_search(self):
        self.panel.automatic_action.setChecked(True)
        self.panel._rows[AUTOMATIC['key']][1].setChecked(True)
        self.panel._rows[PLATFORM['key']][1].setChecked(True)
        self.panel.search_input.setText('zh-Hant')
        self.panel.automatic_action.setChecked(False)
        self.assertEqual(self.selected_keys(), {PLATFORM['key']})
        self.panel.automatic_action.setChecked(True)
        self.assertEqual(self.selected_keys(), {PLATFORM['key']})

    def test_automatic_only_empty_hint_and_button(self):
        self.panel.set_tracks([AUTOMATIC])
        self.assertEqual(self.panel.empty_hint.text(), '发现1条自动字幕')
        self.assertFalse(self.panel.show_automatic_button.isHidden())
        self.assertTrue(self.panel.track_scroll.isHidden())
        self.panel.show_automatic_button.click()
        self.assertTrue(self.panel.automatic_action.isChecked())
        self.assertEqual(self.panel._visible_keys, [AUTOMATIC['key']])
        self.assertFalse(self.panel.track_scroll.isHidden())

    def test_formats_count_disable_and_download_signal(self):
        self.panel.select_all.click()
        self.assertEqual(self.panel.download_count(), 2)
        self.panel.format_checks['txt'].click()
        self.panel.format_checks['vtt'].click()
        self.assertEqual(self.panel.download_count(), 6)
        self.assertIn('6', self.panel.download_button.text())
        self.assertIn('2 条字幕 × 3 种格式 = 6', self.panel.download_hint.text())
        requested = MagicMock()
        self.panel.download_requested.connect(requested)
        self.panel.download_button.click()
        requested.assert_called_once_with()
        for check in self.panel.format_checks.values():
            check.setChecked(False)
        self.assertEqual(self.panel.download_count(), 0)
        self.assertFalse(self.panel.download_button.isEnabled())
        self.assertIn('输出格式', self.panel.download_hint.text())

    def test_more_menu_comment_discovery_and_new_sniff_reset(self):
        self.assertFalse(self.panel.automatic_action.isChecked())
        self.assertEqual(self.panel.automatic_action.text(), '显示自动字幕（含自动翻译）')
        self.assertTrue(self.panel.comments_action.isEnabled())
        requested = MagicMock()
        self.panel.comments_requested.connect(requested)
        self.panel.comments_action.trigger()
        requested.assert_called_once_with()
        self.panel.automatic_action.setChecked(True)
        self.panel.select_all.click()
        self.panel.search_input.setText('English')
        self.panel.set_tracks([AUTOMATIC], [COMMENT])
        self.assertFalse(self.panel.automatic_action.isChecked())
        self.assertFalse(self.panel.selected_tracks())
        self.assertEqual(self.panel.search_input.text(), '')
        self.panel.set_tracks([], [COMMENT])
        self.assertIn('更多内容', self.panel.empty_hint.text())
        self.assertTrue(self.panel.comments_action.isEnabled())
        self.assertFalse(self.panel.download_button.isEnabled())
        self.panel.clear()
        self.assertFalse(self.panel.comments_action.isEnabled())
        self.assertFalse(self.panel.selected_tracks())
        self.assertFalse(self.panel.automatic_action.isChecked())

    def test_many_tracks_batch_selection_refreshes_once_and_keeps_manual_toggles(self):
        tracks = [track(f'lang-{i}', 'platform' if i < 316 else 'automatic')
                  for i in range(632)]
        self.panel.set_tracks(tracks)
        self.panel.automatic_action.setChecked(True)
        changed = MagicMock()
        self.panel.selection_changed.connect(changed)
        self.panel.select_all.click()
        changed.assert_called_once_with()
        self.assertEqual(len(self.panel.selected_tracks()), 632)
        self.assertTrue(all(check.isChecked() for _, check in self.panel._rows.values()))
        changed.reset_mock()
        self.panel.automatic_action.setChecked(False)
        changed.assert_called_once_with()
        self.assertEqual(len(self.panel.selected_tracks()), 316)
        self.assertTrue(all(not self.panel._rows[t['key']][1].isChecked()
                            for t in tracks[316:]))
        changed.reset_mock()
        self.panel._rows[tracks[0]['key']][1].click()
        changed.assert_called_once_with()
        self.assertEqual(len(self.panel.selected_tracks()), 315)
        self.panel.select_all.click()
        self.assertEqual(len(self.panel.selected_tracks()), 316)
        self.panel.select_all.click()
        self.assertFalse(self.panel.selected_tracks())

    def test_automatic_in_platform_bucket_stays_automatic_and_passes_all_fields(self):
        automatic = dict(AUTOMATIC, key='subtitles:ai-en-US', bucket='subtitles',
                         extractor_detail={'keep': True})
        self.panel.set_tracks([PLATFORM, automatic])
        self.assertEqual(self.panel._visible_keys, [PLATFORM['key']])
        self.panel.automatic_action.setChecked(True)
        self.panel.select_all.click()
        self.assertEqual(self.panel.selected_tracks(), [PLATFORM, automatic])
        self.panel.automatic_action.setChecked(False)
        self.assertEqual(self.panel.selected_tracks(), [PLATFORM])

    def test_comments_dialog_keeps_original_format_and_tracks_separate(self):
        parent = YouTubePageTests.make_parent()
        self.addCleanup(self.dispose, parent)
        xml = track('comments', 'comment', '弹幕', ['xml'])
        dialog = CommentDownloadDialog([COMMENT, xml], parent)
        self.addCleanup(self.dispose, dialog)
        self.assertIn('JSON', dialog.track_combo.itemText(0))
        self.assertIn('XML', dialog.track_combo.itemText(1))
        dialog.track_combo.setCurrentIndex(1)
        self.assertEqual(dialog.selected_track(), xml)
        self.assertFalse(dialog.findChildren(type(self.panel.format_checks['srt'])))


class YouTubePageTests(QtCase):
    @staticmethod
    def make_parent():
        from PyQt6.QtWidgets import QWidget
        parent = QWidget()
        parent.resize(1000, 700)
        return parent

    def setUp(self):
        self.save_view = self.enter_patch('sookit.pages.youtube_page.save_sniff_view')
        self.enter_patch('sookit.pages.youtube_page.load_sniff_view', return_value=SNIFF_VIEW_DEFAULT)
        self.enter_patch('sookit.pages.youtube_page.is_ytdlp_available', return_value=True)
        self.enter_patch('sookit.core.task_queue.TaskQueueManager.instance')
        self.enter_patch('sookit.pages.youtube_page.show_infobar')
        self.enter_patch('sookit.pages.youtube_page.notify_result_notice')
        self.enter_patch('sookit.pages.youtube_page.YouTubePage.log')
        self.enter_patch('sookit.pages.youtube_page.GenericWorker', FakeSniffWorker)
        self.page = YouTubePage()
        self.page.resize(1131, 731)
        self.page.show()
        self.settle()
        self.page.run_queued_task = MagicMock()
        self.addCleanup(self.dispose, self.page)

    def enter_patch(self, target, *args, **kwargs):
        patcher = patch(target, *args, **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def sniff(self, tracks=None, comments=None, formats=None, url='https://example.com/one'):
        self.page.url_input.setText(url)
        self.page.do_sniff()
        worker = self.page._sniff_worker
        worker.running = False
        worker.done.emit({'title': 'Example', 'channel': 'Channel', 'duration': 123,
                          'formats': FORMATS if formats is None else formats,
                          'subtitle_tracks': tracks or [], 'comment_tracks': comments or [],
                          'thumbnails': []})
        self.settle()
        return worker

    def show_subtitles(self):
        self.page.content_segment.setCurrentItem('subtitles')
        self.settle()

    def test_initial_hidden_tab_and_no_persistence_on_restore(self):
        self.assertTrue(self.page.content_segment.isHidden())
        self.assertEqual(self.page._content_tab, 'media')
        self.assertFalse(self.page.default_view.isHidden())
        self.assertTrue(self.page.subtitle_panel.isHidden())
        self.assertEqual(self.page.view_button.text(), '表格视图')
        self.save_view.assert_not_called()
        self.sniff()
        self.assertTrue(self.page.content_segment.isHidden())

    def test_subtitle_tab_for_platform_automatic_or_comments(self):
        for subtitles, comments in (([PLATFORM], []), ([AUTOMATIC], []), ([], [COMMENT])):
            with self.subTest(subtitles=subtitles, comments=comments):
                self.sniff(subtitles, comments, formats=[])
                self.assertFalse(self.page.content_segment.isHidden())
                self.assertEqual(self.page._content_tab, 'media')
                self.show_subtitles()
                self.assertFalse(self.page.subtitle_panel.isHidden())
                self.assertTrue(self.page.view_button.isHidden())
                self.assertTrue(self.page.default_view.isHidden())
                self.assertTrue(self.page.table_view.isHidden())

    def test_switching_preserves_all_independent_selections(self):
        self.sniff([PLATFORM, CHINESE])
        option = self.page._option_rows[-1][1]
        self.page._select_option(option)
        self.page.format_table.item(1, 0).setCheckState(Qt.CheckState.Checked)
        checked = [self.page.format_table.item(r, 0).checkState() for r in range(2)]
        self.page.view_button.click()
        self.assertEqual(self.page._media_view, SNIFF_VIEW_TABLE)
        self.assertEqual(self.page.view_button.text(), '默认视图')
        self.show_subtitles()
        self.page.subtitle_panel._rows[PLATFORM['key']][1].setChecked(True)
        self.page.subtitle_panel.search_input.setText('zh')
        self.page.subtitle_panel.format_checks['txt'].setChecked(True)
        self.page.content_segment.setCurrentItem('media')
        self.assertFalse(self.page.table_view.isHidden())
        self.assertIs(self.page._selected_option, option)
        self.assertEqual([self.page.format_table.item(r, 0).checkState() for r in range(2)], checked)
        self.show_subtitles()
        self.assertEqual(self.page.subtitle_panel.selected_tracks(), [PLATFORM])
        self.assertEqual(self.page.subtitle_panel.search_input.text(), 'zh')
        self.assertEqual(self.page.subtitle_panel.selected_formats(), ['srt', 'txt'])
        self.assertEqual(self.page.subtitle_panel.download_count(), 2)
        self.save_view.assert_called_once_with(SNIFF_VIEW_TABLE)

    def test_right_view_button_aligns_with_grid_and_table_edges_unchanged(self):
        self.sniff([PLATFORM])
        for width in (966, 1131, 1440):
            with self.subTest(width=width):
                self.page.resize(width, 731)
                self.settle()
                button_right = self.page.view_button.mapTo(self.page, QPoint(self.page.view_button.width(), 0)).x()
                card, _ = self.page._option_rows[1]
                card_right = card.mapTo(self.page, QPoint(card.width(), 0)).x()
                self.assertLessEqual(abs(button_right - card_right), 1)
                segment_left = self.page.content_segment.mapTo(self.page, QPoint()).x()
                card_left = self.page._option_rows[0][0].mapTo(self.page, QPoint()).x()
                self.assertEqual(segment_left, card_left)
                self.page.view_button.click()
                self.settle()
                table = self.page.format_table
                self.assertEqual(table.mapTo(self.page, QPoint(table.width(), 0)).x(), self.page.width())
                self.assertTrue(table._bottom_rule.isHidden())
                self.assertFalse(table.property('isBorderVisible'))
                expected = table.verticalHeader().width() + sum(table.columnWidth(i) for i in range(6))
                self.assertLessEqual(abs(table._right_rule.x() - expected), 1)
                self.page.view_button.click()

    def test_subtitle_geometry_keeps_right_margin_scroll_extent_and_bottom_button(self):
        tracks = [track(f'lang-{i}', name='字幕语言 ' * 8) for i in range(24)]
        self.sniff(tracks)
        self.show_subtitles()
        panel = self.page.subtitle_panel
        for width in (966, 1131, 1440):
            with self.subTest(width=width):
                self.page.resize(width, 731)
                self.settle()
                self.assertEqual(self.page.width(), width)
                scroll_right = panel.track_scroll.mapTo(
                    self.page, QPoint(panel.track_scroll.width(), 0)).x()
                self.assertEqual(scroll_right, width)
                for key in panel._visible_keys:
                    row = panel._rows[key][0]
                    row_right = row.mapTo(self.page, QPoint(row.width(), 0)).x()
                    self.assertEqual(width - row_right, self.page.OPTION_MARGIN_RIGHT)
                tools_right = panel.more_button.mapTo(
                    self.page, QPoint(panel.more_button.width(), 0)).x()
                self.assertEqual(width - tools_right, self.page.OPTION_MARGIN_RIGHT)
                button = panel.download_button
                self.assertEqual((button.width(), button.height()), (240, 32))
                panel_bottom = panel.mapTo(self.page, QPoint(0, panel.height())).y()
                button_bottom = button.mapTo(self.page, QPoint(0, button.height())).y()
                self.assertEqual(button_bottom, panel_bottom)
                button_center = button.mapTo(self.page, QPoint(button.width() // 2, 0)).x()
                panel_left = panel.mapTo(self.page, QPoint()).x()
                expected_center = (panel_left + width - self.page.OPTION_MARGIN_RIGHT) / 2
                self.assertLessEqual(abs(button_center - expected_center), 1)
                self.assertGreater(panel.track_scroll.verticalScrollBar().maximum(), 0)

    def test_subtitle_empty_directory_uses_default_and_metadata_matches_snapshot(self):
        self.sniff([PLATFORM])
        self.show_subtitles()
        self.page.subtitle_panel.select_all.click()
        with patch('sookit.pages.youtube_page.DEFAULT_OUTPUT_DIR', 'D:/Default subtitles'):
            self.page.subtitle_panel.download_button.click()
        call = self.page.run_queued_task.call_args.kwargs
        self.assertEqual(call['args'][1], 'D:/Default subtitles')
        metadata = call['metadata']
        self.assertEqual(metadata['out'], call['args'][1])
        self.assertEqual(metadata['out_dir'], call['args'][1])
        self.assertEqual(metadata['url'], call['args'][0])
        self.assertEqual(metadata['channel'], 'Channel')
        self.assertEqual(metadata['duration'], 123)
        self.assertEqual(metadata['title'], 'Example')
        self.assertEqual(call['args'][2][0]['bucket'], 'subtitles')
        self.assertEqual(call['args'][2][0]['title'], 'Original track title')

    def test_subtitle_metadata_excludes_previous_sniff_cover_and_late_cover_data(self):
        self.sniff([PLATFORM])
        previous_seq = self.page._sniff_seq
        self.page._cover_data = b'previous-cover'
        self.page._cover_data_seq = previous_seq
        self.page._sniff_thumbs = [{'url': 'https://example.com/previous-cover.jpg'}]
        self.sniff([CHINESE], url='https://example.com/next')
        self.assertIsNone(self.page._cover_data)
        self.assertFalse(self.page._sniff_thumbs)
        old_loader = MagicMock()
        old_loader._sniff_seq = previous_seq
        self.page._cover_loader_worker = old_loader
        with patch('sookit.pages.youtube_page.QPixmap'), patch.object(self.page.cover_label, 'setPixmap'):
            self.page._on_cover_loaded(b'late-previous-cover', old_loader)
        self.assertEqual(self.page._cover_data_seq, previous_seq)
        self.show_subtitles()
        self.page.subtitle_panel.select_all.click()
        self.page.subtitle_panel.download_button.click()
        metadata = self.page.run_queued_task.call_args.kwargs['metadata']
        self.assertEqual(metadata['cover_url'], '')
        self.assertNotIn('cover_data', metadata)
        self.assertEqual(metadata['url'], 'https://example.com/next')

    def test_subtitle_queue_uses_captured_url_formats_metadata_and_no_reserved_kwargs(self):
        self.sniff([PLATFORM, CHINESE])
        self.page._cover_data = b'cover-test'
        self.page._cover_data_seq = self.page._sniff_seq
        self.page.url_input.setText('https://example.com/edited-after-sniff')
        self.page.out_dir.setText('"D:/Downloads"')
        self.page.remote_cb.setChecked(True)
        self.show_subtitles()
        self.page.subtitle_panel.select_all.click()
        self.page.subtitle_panel.format_checks['txt'].setChecked(True)
        self.page.subtitle_panel.download_button.click()
        self.page.run_queued_task.assert_called_once()
        call = self.page.run_queued_task.call_args.kwargs
        self.assertEqual(call['func'].__name__, 'download_subtitles')
        self.assertEqual(call['task_type'], TaskType.YTDLP)
        self.assertEqual(call['args'][0], 'https://example.com/one')
        self.assertEqual(call['args'][1], os.path.normpath('D:/Downloads'))
        self.assertEqual(call['args'][2], [PLATFORM, CHINESE])
        self.assertEqual(call['args'][3], ['srt', 'txt'])
        self.assertEqual(call['kwargs'], {'remote': True})
        self.assertFalse(set(call['kwargs']) & RESERVED_TASK_KWARGS)
        self.assertEqual(call['metadata']['filename'], 'Example')
        self.assertEqual(call['metadata']['out'], call['args'][1])
        self.assertEqual(call['metadata']['out_dir'], call['args'][1])
        self.assertEqual(call['metadata']['cover_data'], b'cover-test')

    def test_comment_only_discoverable_and_queues_original_track(self):
        self.sniff([], [COMMENT], formats=[])
        self.page.url_input.setText('https://example.com/edited')
        self.show_subtitles()
        panel = self.page.subtitle_panel
        self.assertIn('实时评论', panel.empty_hint.text())
        self.assertFalse(panel.download_button.isEnabled())
        with patch('sookit.pages.youtube_page.CommentDownloadDialog') as factory:
            dialog = factory.return_value
            dialog.exec.return_value = True
            dialog.selected_track.return_value = COMMENT
            panel.comments_action.trigger()
        self.page.run_queued_task.assert_called_once()
        call = self.page.run_queued_task.call_args.kwargs
        self.assertEqual(call['func'].__name__, 'download_comments')
        self.assertEqual(call['args'][0], 'https://example.com/one')
        self.assertEqual(call['args'][2:], (COMMENT,))
        self.assertEqual(call['kwargs'], {'remote': False})
        self.assertEqual(call['task_type'], TaskType.YTDLP)
        self.assertEqual(call['metadata']['filename'], 'Example')

    def test_comment_dialog_cancel_or_replaced_sniff_does_not_queue(self):
        self.sniff([], [COMMENT])
        with patch('sookit.pages.youtube_page.CommentDownloadDialog') as factory:
            dialog = factory.return_value
            dialog.exec.return_value = False
            self.page._download_comments()
            dialog.selected_track.assert_not_called()
            dialog.exec.side_effect = lambda: (self.page.do_sniff() or True)
            self.page._download_comments()
        self.page.run_queued_task.assert_not_called()

    def test_clear_input_invalidates_results_and_in_flight_generation(self):
        self.sniff([PLATFORM], [COMMENT])
        self.show_subtitles()
        self.page.subtitle_panel.select_all.click()
        self.page.url_input.clear()
        self.assertEqual(self.page._subtitle_url, '')
        self.assertTrue(self.page.content_segment.isHidden())
        self.assertEqual(self.page._content_tab, 'media')
        self.assertFalse(self.page.subtitle_panel.selected_tracks())
        self.page._download_comments()
        self.page._download_current_content = MagicMock()
        self.page.run_queued_task.assert_not_called()
        self.page.url_input.setText('https://example.com/in-flight')
        self.page.do_sniff()
        worker = self.page._sniff_worker
        self.page.url_input.clear()
        worker.done.emit({'subtitle_tracks': [PLATFORM]})
        self.assertEqual(self.page._subtitle_url, '')
        self.assertTrue(self.page.content_segment.isHidden())

    def test_new_sniff_stop_failure_and_timeout_clear_subtitle_state(self):
        for action in ('new', 'stop', 'failure', 'timeout'):
            with self.subTest(action=action):
                self.sniff([PLATFORM, AUTOMATIC], [COMMENT])
                self.show_subtitles()
                panel = self.page.subtitle_panel
                panel.automatic_action.setChecked(True)
                panel.select_all.click()
                if action == 'new':
                    self.page.do_sniff()
                elif action == 'stop':
                    self.page.stop_sniff()
                elif action == 'failure':
                    self.page._on_sniff_error(self.page._sniff_seq, 'test failure')
                else:
                    self.page._sniff_worker.running = True
                    self.page._sniff_timeout()
                self.assertEqual(self.page._subtitle_url, '')
                self.assertFalse(panel.selected_tracks())
                self.assertFalse(panel.automatic_action.isChecked())
                self.assertTrue(self.page.content_segment.isHidden())
                self.assertFalse(panel.download_button.isEnabled())
                self.page._queue_subtitle_task(MagicMock(), (), 'test')
                self.page.run_queued_task.assert_not_called()

    def test_stale_callbacks_do_not_reset_current_sniff_ui_or_timer(self):
        self.page.url_input.setText('https://example.com/old')
        self.page.do_sniff()
        old = self.page._sniff_worker
        self.page.url_input.setText('https://example.com/new')
        self.page.do_sniff()
        old.done.emit({'subtitle_tracks': [PLATFORM]})
        old.error.emit('old failure')
        self.assertTrue(self.page.sniff_btn.isHidden())
        self.assertFalse(self.page.stop_sniff_btn.isHidden())
        self.assertTrue(self.page._sniff_timer.isActive())
        self.assertEqual(self.page._subtitle_url, '')
        self.assertTrue(self.page.content_segment.isHidden())
        self.page.stop_sniff()
        self.page._sniff_worker.done.emit({'subtitle_tracks': [PLATFORM]})
        self.assertEqual(self.page._subtitle_url, '')

    def test_sniff_workers_retained_until_queued_finished_even_when_not_running(self):
        self.page.url_input.setText('https://example.com/old')
        self.page.do_sniff()
        old = self.page._sniff_worker
        self.assertIn(old, self.page._threads)
        old.running = False
        self.page.do_sniff()
        current = self.page._sniff_worker
        self.assertIn(old, self.page._threads)
        self.assertIn(current, self.page._threads)
        old.finished.emit()
        old.finished.emit()  # 重复通知不应在 list.remove 中抛错。
        self.assertIn(old, self.page._threads)  # 显式排队，发射时不直接清理。
        self.settle()
        self.assertNotIn(old, self.page._threads)
        self.assertIn(current, self.page._threads)
        self.assertIs(self.page._sniff_worker, current)
        self.assertTrue(self.page._sniff_timer.isActive())
        current.running = False
        current.finished.emit()
        self.settle()
        self.assertNotIn(current, self.page._threads)

    def test_old_timeout_signal_cannot_stop_new_sniff(self):
        self.page.url_input.setText('https://example.com/old')
        self.page.do_sniff()
        old_timer = self.page._sniff_timer
        self.page.url_input.setText('https://example.com/current')
        self.page.do_sniff()
        current = self.page._sniff_worker
        old_timer.timeout.emit()
        self.assertFalse(current.stopped)
        self.assertFalse(self.page._sniff_timed_out)
        self.assertTrue(self.page._sniff_timer.isActive())
        self.assertTrue(self.page.sniff_btn.isHidden())
        self.assertFalse(self.page.stop_sniff_btn.isHidden())

    def test_media_dispatch_is_single_and_keeps_original_actions(self):
        self.sniff([PLATFORM])
        with patch.object(self.page, 'do_download_default') as default, patch.object(self.page, 'do_download') as table:
            self.page.default_download_btn.click()
            default.assert_called_once_with()
            table.assert_not_called()
            self.page.view_button.click()
            self.page.download_btn.click()
            table.assert_called_once_with()
            default.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
