"""浏览器监控分派与轮询阶段验证；不触网、不截图。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from sookit.main_window import MainWindow
from sookit.core.workers import MonitorWorker

URL = 'https://www.youtube.com/watch?v=abcdefghijk'


class BrowserMonitorDispatchTests(unittest.TestCase):
    def window(self, response):
        monitor = SimpleNamespace(monitor_from_browser=MagicMock(return_value=response))
        return SimpleNamespace(_quitting=False, monitor_page=monitor, switchTo=MagicMock(),
                               _raise_window=MagicMock(), show=MagicMock(), activateWindow=MagicMock())

    def test_success_and_duplicate_select_monitor_without_raising_window(self):
        for duplicate in (False, True):
            response = {'ok': True, 'task_id': '任务1', 'duplicate': duplicate}
            window = self.window(response)
            metadata = {'title': '网页标题', 'channel': '频道'}
            with patch('sookit.main_window.load_browser_extension_enabled', return_value=True):
                actual = MainWindow.handle_browser_request(window, {
                    'action': 'monitor', 'url': URL, 'video_metadata': metadata})
            self.assertEqual(actual, response)
            window.monitor_page.monitor_from_browser.assert_called_once_with(URL, video_metadata=metadata)
            window.switchTo.assert_called_once_with(window.monitor_page)
            window._raise_window.assert_not_called()
            window.show.assert_not_called()
            window.activateWindow.assert_not_called()

    def test_failure_pause_and_quit_do_not_switch_or_add(self):
        for enabled, quitting, result in ((True, False, {'ok': False, 'error': 'yt-dlp 不可用'}),
                                          (False, False, {'ok': True}), (True, True, {'ok': True})):
            window = self.window(result)
            window._quitting = quitting
            with patch('sookit.main_window.load_browser_extension_enabled', return_value=enabled):
                actual = MainWindow.handle_browser_request(window, {'action': 'monitor', 'url': URL})
            self.assertFalse(actual['ok'])
            window.switchTo.assert_not_called()
            if not enabled or quitting:
                window.monitor_page.monitor_from_browser.assert_not_called()


class MonitorPhaseTests(unittest.TestCase):
    def test_waits_through_upcoming_live_unknown_then_submits_once(self):
        worker = MonitorWorker('test', URL, 'output', 0, False)
        downloads = []
        states = []
        worker.download_ready.connect(lambda *args: downloads.append(args))
        worker.status_signal.connect(lambda tid, status: states.append(status))
        with patch('sookit.core.workers.Functions.check_live_status', side_effect=[
                {'live_status': status} for status in ('is_upcoming', 'is_live', 'unknown', 'post_live')]) as check:
            worker.run()
        self.assertEqual(check.call_count, 4)
        self.assertEqual(states, ['等待中', '直播中', 'unknown', '下载中'])
        self.assertEqual(len(downloads), 1)
        self.assertEqual(downloads[0][:3], ('test', URL, 'output'))

    def test_stop_during_wait_prevents_download(self):
        worker = MonitorWorker('test', URL, 'output', 0, False)
        ready = MagicMock()
        worker.download_ready.connect(ready)
        def check(url):
            worker.stop()
            return {'live_status': 'is_upcoming'}
        with patch('sookit.core.workers.Functions.check_live_status', side_effect=check) as probe:
            worker.run()
        probe.assert_called_once()
        ready.assert_not_called()


if __name__ == '__main__':
    unittest.main()
