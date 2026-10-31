"""浏览器下载入口隔离、封面 worker 回退及真实最佳画质下载验证。"""
import collections
import functools
import http.server
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from sookit.core.browser_downloads import BEST_VIDEO_FORMAT
from sookit.core.ffmpeg_utils import get_ffmpeg_path
from sookit.core.functions import Functions
from sookit.core.task_queue import TaskType
from sookit.core.workers import TaskWorker
from sookit.core.youtube_utils import build_thumbnails
from sookit.core.ytdlp_utils import build_ytdlp_cmd, get_ytdlp_cmd

URL = 'https://www.youtube.com/watch?v=Yb9H5vhWyHg'


class BrowserDownloadDispatchTests(unittest.TestCase):
    def page(self):
        from sookit.pages.youtube_page import YouTubePage
        page = SimpleNamespace(out_dir=object(), remote_cb=MagicMock(), read_path=MagicMock(),
                               run_queued_task=MagicMock(return_value='test-task'),
                               url_input=MagicMock(), do_sniff=MagicMock(),
                               _cover_download_workers=set())
        page._cover_extension = YouTubePage._cover_extension
        page._next_cover_download_path = functools.partial(
            YouTubePage._next_cover_download_path, page)
        page._start_cover_download = MagicMock()
        page.remote_cb.isChecked.return_value = True
        page.read_path.return_value = 'D:/output'
        return page

    def dispatch(self, page, action, available=True):
        from sookit.pages.youtube_page import YouTubePage
        with patch('sookit.pages.youtube_page.is_ytdlp_available', return_value=available), \
                patch('sookit.pages.youtube_page.load_download_config', return_value={
                    'concurrent_fragments': 4, 'use_aria2c': False, 'aria2c_connections': 8}):
            return YouTubePage.download_from_browser(page, URL + '&list=PLtest', action)

    def test_best_download_uses_own_url_and_preserves_current_sniff(self):
        page = self.page()
        page._sniff_worker = SimpleNamespace(isRunning=lambda: True)
        page._sniff_title = '另一个视频的标题'
        response = self.dispatch(page, 'download_best')
        self.assertTrue(response['ok'])
        self.assertEqual(response['task_id'], 'test-task')
        page.do_sniff.assert_not_called()
        page.url_input.setText.assert_not_called()
        page.url_input.text.assert_not_called()
        task = page.run_queued_task.call_args.kwargs
        self.assertEqual(task['task_type'], TaskType.YTDLP)
        self.assertEqual(task['metadata']['url'], URL)
        self.assertEqual(task['metadata']['out_dir'], 'D:/output')
        self.assertNotIn('另一个', task['title'])
        self.assertIs(task['func'], Functions.download_youtube)
        self.assertEqual(task['args'], (URL, BEST_VIDEO_FORMAT, 'D:/output', True, 4, False, 8))
        self.assertEqual(task['kwargs']['extra_args'], ['--ignore-config', '--no-playlist'])

    def test_cover_download_needs_no_ytdlp_and_uses_thumbnail_priority(self):
        page = self.page()
        response = self.dispatch(page, 'download_cover', available=False)
        self.assertTrue(response['ok'])
        self.assertEqual(
            response['message'],
            '封面下载已开始；高分辨率不可用时会自动尝试下一档，完成后请查看 Sookit')
        urls, path = page._start_cover_download.call_args.args
        self.assertEqual(urls, [thumbnail['url'] for thumbnail in build_thumbnails('Yb9H5vhWyHg')])
        self.assertEqual(path, os.path.join('D:/output', 'Yb9H5vhWyHg_cover.jpg'))
        page.run_queued_task.assert_not_called()
        page.do_sniff.assert_not_called()

    def test_cover_filename_adds_suffix_for_existing_and_active_downloads(self):
        from sookit.pages.youtube_page import YouTubePage
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'video_cover.jpg').touch()
            active_worker = MagicMock(path=str(root / 'video_cover_1.jpg'))
            page = SimpleNamespace(_cover_download_workers={active_worker})
            self.assertEqual(YouTubePage._next_cover_download_path(page, temporary, 'video_cover.jpg'),
                             str(root / 'video_cover_2.jpg'))

    def test_cover_download_worker_reference_is_released(self):
        from sookit.pages.youtube_page import YouTubePage
        worker = object()
        workers = {worker}
        page = SimpleNamespace(_cover_download_workers=workers,
                               sender=MagicMock(return_value=worker))
        YouTubePage._on_cover_download_worker_finished(page)
        self.assertEqual(workers, set())

    def test_missing_tool_or_invalid_action_never_queues(self):
        for action, available in [('download_best', False), ('exec', True)]:
            page = self.page()
            self.assertFalse(self.dispatch(page, action, available)['ok'])
            page.run_queued_task.assert_not_called()

    def test_empty_output_uses_existing_default(self):
        from sookit.pages.youtube_page import DEFAULT_OUTPUT_DIR, YouTubePage
        page = self.page()
        page.read_path.return_value = ''
        response = YouTubePage.download_from_browser(page, URL, 'download_cover')
        self.assertTrue(response['ok'])
        self.assertEqual(page._start_cover_download.call_args.args[1], os.path.join(
            DEFAULT_OUTPUT_DIR, 'Yb9H5vhWyHg_cover.jpg'))

    def test_gui_dispatches_downloads_without_changing_navigation_or_focus(self):
        from sookit.main_window import MainWindow
        for action in ('download_best', 'download_cover'):
            page = SimpleNamespace(download_from_browser=MagicMock(return_value={'ok': True}),
                                   sniff_from_browser=MagicMock())
            window = SimpleNamespace(youtube_page=page, switchTo=MagicMock(), show=MagicMock(),
                                     activateWindow=MagicMock())
            with patch('sookit.main_window.load_browser_extension_enabled', return_value=True):
                self.assertTrue(MainWindow.handle_browser_request(window, {'action': action, 'url': URL})['ok'])
            page.download_from_browser.assert_called_once_with(URL, action)
            page.sniff_from_browser.assert_not_called()
            window.switchTo.assert_not_called()
            window.show.assert_not_called()
            window.activateWindow.assert_not_called()
            page.download_from_browser.reset_mock()
            with patch('sookit.main_window.load_browser_extension_enabled', return_value=False):
                self.assertFalse(MainWindow.handle_browser_request(window, {'action': action, 'url': URL})['ok'])
            page.download_from_browser.assert_not_called()


class CoverDownloadWorkerTests(unittest.TestCase):
    def test_falls_back_in_order_and_removes_partial_file(self):
        from sookit.pages.youtube_page import YouTubePage
        urls = [thumbnail['url'] for thumbnail in build_thumbnails('Yb9H5vhWyHg')]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'Yb9H5vhWyHg_cover.jpg'
            attempts = []
            results = []

            def download(url, output_path, log=None):
                attempts.append(url)
                if url == urls[0]:
                    Path(output_path).write_bytes(b'partial first response')
                    raise RuntimeError('分辨率不可用')
                self.assertFalse(Path(output_path).exists())
                Path(output_path).write_bytes(b'second thumbnail')

            worker = YouTubePage.CoverDownloadWorker(urls, str(path))
            worker.download_finished.connect(lambda success, output: results.append((success, output)))
            with patch('sookit.pages.youtube_page.Functions.download_thumbnail', side_effect=download):
                worker.run()

            self.assertEqual(attempts, urls[:2])
            self.assertEqual(path.read_bytes(), b'second thumbnail')
            self.assertEqual(results, [(True, str(path))])

    def test_accepts_single_gui_thumbnail_url(self):
        from sookit.pages.youtube_page import YouTubePage
        url = build_thumbnails('Yb9H5vhWyHg')[0]['url']
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'Yb9H5vhWyHg_cover.jpg'
            attempts = []
            results = []

            def download(request_url, output_path, log=None):
                attempts.append(request_url)
                Path(output_path).write_bytes(b'gui thumbnail')

            worker = YouTubePage.CoverDownloadWorker(url, str(path))
            worker.download_finished.connect(lambda success, output: results.append((success, output)))
            with patch('sookit.pages.youtube_page.Functions.download_thumbnail', side_effect=download):
                worker.run()

            self.assertEqual(attempts, [url])
            self.assertEqual(path.read_bytes(), b'gui thumbnail')
            self.assertEqual(results, [(True, str(path))])

    def test_cleans_partial_file_when_every_resolution_fails(self):
        from sookit.pages.youtube_page import YouTubePage
        urls = [thumbnail['url'] for thumbnail in build_thumbnails('Yb9H5vhWyHg')[:2]]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'Yb9H5vhWyHg_cover.jpg'
            results = []

            def download(url, output_path, log=None):
                Path(output_path).write_bytes(b'partial response')
                raise RuntimeError('请求失败')

            worker = YouTubePage.CoverDownloadWorker(urls, str(path))
            worker.download_finished.connect(lambda success, output: results.append((success, output)))
            with patch('sookit.pages.youtube_page.Functions.download_thumbnail', side_effect=download):
                worker.run()

            self.assertEqual(results, [(False, str(path))])
            self.assertFalse(path.exists())


class FixtureHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.server.requests[self.path] += 1
        super().do_GET()

    def log_message(self, *args):
        pass


class RealBrowserDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not get_ytdlp_cmd():
            raise unittest.SkipTest('需要现有 yt-dlp')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sookit-browser-download-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = self.root / 'fixture'
        self.fixture.mkdir()
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        handler = functools.partial(FixtureHandler, directory=str(self.fixture))
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.server.requests = collections.Counter()
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.05), daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.info = {'id': 'Yb9H5vhWyHg', 'title': 'fixture',
                     'extractor': 'generic', 'extractor_key': 'Generic',
                     'webpage_url': self.base + '/watch',
                     'formats': [{'format_id': '18', 'url': self.base + '/media.mp4', 'ext': 'mp4'}]}
        self.info_path = self.fixture / 'fixture.info.json'
        self.logs = []

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def fixture_command(self, *args):
        self.info_path.write_text(json.dumps(self.info), encoding='utf-8')
        return build_ytdlp_cmd(*args, '--load-info-json', str(self.info_path))

    def test_best_quality_downloads_highest_video_and_audio_with_actual_merger(self):
        ffmpeg = get_ffmpeg_path()
        if not Path(ffmpeg).is_file():
            self.skipTest('需要现有 FFmpeg')
        for name, size in [('low', 32), ('high', 64)]:
            subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                            '-f', 'lavfi', '-i', f'color=c=red:s={size}x{size}:r=2',
                            '-t', '1', '-c:v', 'libx264', '-an', str(self.fixture / f'{name}.mp4')],
                           check=True, capture_output=True, timeout=30)
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                        '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono', '-t', '1',
                        '-c:a', 'aac', str(self.fixture / 'audio.m4a')],
                       check=True, capture_output=True, timeout=30)
        self.info['formats'] = [
            {'format_id': 'low', 'url': self.base + '/low.mp4', 'ext': 'mp4',
             'height': 32, 'vcodec': 'avc1', 'acodec': 'none'},
            {'format_id': 'high', 'url': self.base + '/high.mp4', 'ext': 'mp4',
             'height': 64, 'vcodec': 'avc1', 'acodec': 'none'},
            {'format_id': 'audio', 'url': self.base + '/audio.m4a', 'ext': 'm4a',
             'vcodec': 'none', 'acodec': 'mp4a', 'abr': 32}]
        self.info_path.write_text(json.dumps(self.info), encoding='utf-8')
        worker = TaskWorker('best-video', TaskType.YTDLP, Functions.download_youtube,
                            (URL, BEST_VIDEO_FORMAT, str(self.root), False, 1, False, 8),
                            workspace=str(self.workspace), kwargs={'extra_args': [
                                '--ignore-config', '--no-playlist', '--load-info-json', str(self.info_path)]})
        # load-info-json 负责提供本机链接；不额外提取外部 YouTube 页面。
        import sookit.core.functions as functions
        original = functions.run_ytdlp
        with patch.object(functions, 'run_ytdlp', side_effect=lambda cmd, *args, **kwargs:
                          original([arg for arg in cmd if arg != URL], *args, **kwargs)):
            worker.run()
        self.assertEqual(worker.error, '', worker.error)
        self.assertEqual(len(worker.output_files), 1)
        self.assertGreater(Path(worker.output_files[0]).stat().st_size, 0)
        self.assertEqual(self.server.requests['/low.mp4'], 0)
        self.assertGreater(self.server.requests['/high.mp4'], 0)
        self.assertGreater(self.server.requests['/audio.m4a'], 0)
        ffprobe = str(Path(ffmpeg).with_name('ffprobe.exe'))
        result = subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-of', 'json', worker.output_files[0]],
                                capture_output=True, text=True, check=True, timeout=20)
        streams = json.loads(result.stdout)['streams']
        self.assertEqual({stream['codec_type'] for stream in streams}, {'video', 'audio'})
        self.assertEqual(next(stream['height'] for stream in streams if stream['codec_type'] == 'video'), 64)


if __name__ == '__main__':
    unittest.main()
