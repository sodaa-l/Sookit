"""浏览器下载入口隔离、最佳封面选择及真实 yt-dlp 本机下载验证。"""
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

from sookit.core import browser_downloads
from sookit.core.browser_downloads import BEST_VIDEO_FORMAT, download_best_cover
from sookit.core.ffmpeg_utils import get_ffmpeg_path
from sookit.core.functions import Functions
from sookit.core.task_queue import TaskType
from sookit.core.workers import TaskWorker
from sookit.core.ytdlp_utils import build_ytdlp_cmd, get_ytdlp_cmd

URL = 'https://www.youtube.com/watch?v=Yb9H5vhWyHg'


class BrowserDownloadDispatchTests(unittest.TestCase):
    def page(self):
        page = SimpleNamespace(out_dir=object(), remote_cb=MagicMock(), read_path=MagicMock(),
                               run_queued_task=MagicMock(return_value='test-task'),
                               url_input=MagicMock(), do_sniff=MagicMock())
        page.remote_cb.isChecked.return_value = True
        page.read_path.return_value = 'D:/output'
        return page

    def dispatch(self, page, action, available=True):
        from sookit.pages.youtube_page import YouTubePage
        with patch('sookit.pages.youtube_page.is_ytdlp_available', return_value=available), \
                patch('sookit.pages.youtube_page.load_download_config', return_value={
                    'concurrent_fragments': 4, 'use_aria2c': False, 'aria2c_connections': 8}):
            return YouTubePage.download_from_browser(page, URL + '&list=PLtest', action)

    def test_downloads_own_their_url_and_preserve_current_sniff(self):
        for action in ('download_best', 'download_cover'):
            page = self.page()
            page._sniff_worker = SimpleNamespace(isRunning=lambda: True)
            page._sniff_title = '另一个视频的标题'
            response = self.dispatch(page, action)
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
            if action == 'download_best':
                self.assertIs(task['func'], Functions.download_youtube)
                self.assertEqual(task['args'], (URL, BEST_VIDEO_FORMAT, 'D:/output', True, 4, False, 8))
                self.assertEqual(task['kwargs']['extra_args'], ['--ignore-config', '--no-playlist'])
            else:
                self.assertIs(task['func'], download_best_cover)
                self.assertEqual(task['args'], (URL, 'D:/output', True))

    def test_missing_tool_or_invalid_action_never_queues(self):
        for action, available in [('download_best', False), ('download_cover', False), ('exec', True)]:
            page = self.page()
            self.assertFalse(self.dispatch(page, action, available)['ok'])
            page.run_queued_task.assert_not_called()

    def test_empty_output_uses_existing_default(self):
        from sookit.pages.youtube_page import DEFAULT_OUTPUT_DIR
        page = self.page()
        page.read_path.return_value = ''
        self.dispatch(page, 'download_cover')
        self.assertEqual(page.run_queued_task.call_args.kwargs['args'][1], DEFAULT_OUTPUT_DIR)

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


class CoverArtifactTests(unittest.TestCase):
    def test_requires_workspace_and_only_accepts_unique_nonempty_current_cover(self):
        with self.assertRaisesRegex(ValueError, '工作目录'):
            download_best_cover(URL, '.', False)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'other_cover.jpg').write_bytes(b'old')
            cases = [({}, False), ({'Yb9H5vhWyHg_cover.jpg': b''}, False),
                     ({'Yb9H5vhWyHg_cover.jpg.part': b'partial'}, False),
                     ({'Yb9H5vhWyHg_cover.webp': b'original'}, True)]
            for artifacts, success in cases:
                def run(*args, **kwargs):
                    for name, data in artifacts.items():
                        (root / name).write_bytes(data)
                with self.subTest(artifacts=artifacts), \
                        patch.object(browser_downloads, 'build_ytdlp_cmd', side_effect=lambda *args: ['yt-dlp', *args]), \
                        patch.object(browser_downloads, 'run_ytdlp', side_effect=run):
                    if success:
                        self.assertEqual(download_best_cover(URL, temporary, False, workspace=temporary),
                                         [str(root / 'Yb9H5vhWyHg_cover.webp')])
                    else:
                        with self.assertRaisesRegex(RuntimeError, '非空封面'):
                            download_best_cover(URL, temporary, False, workspace=temporary)
                for name in artifacts:
                    (root / name).unlink()


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

    def real_cover(self, **kwargs):
        # 只把最后的 URL 替换为本机元数据，执行实际 run_ytdlp 和真实下载器。
        original = browser_downloads.run_ytdlp
        def run(cmd, *args, **options):
            self.assertEqual(cmd[-1], URL)
            return original(cmd[:-1], *args, **options)
        with patch.object(browser_downloads, 'build_ytdlp_cmd', side_effect=self.fixture_command), \
                patch.object(browser_downloads, 'run_ytdlp', side_effect=run):
            try:
                return download_best_cover(URL, str(self.root), False, workspace=str(self.workspace),
                                           log=self.logs.append, **kwargs)
            except RuntimeError as error:
                raise RuntimeError(f"{error}\n" + '\n'.join(self.logs)) from error

    def test_best_thumbnail_is_original_and_video_is_never_requested(self):
        for name, content in [('small.jpg', b'small original'), ('large.webp', b'largest original')]:
            (self.fixture / name).write_bytes(content)
        self.info['thumbnails'] = [
            {'url': self.base + '/small.jpg', 'width': 320, 'height': 180},
            {'url': self.base + '/large.webp', 'width': 1280, 'height': 720}]
        processes = []
        outputs = self.real_cover(on_process_created=processes.append)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(Path(outputs[0]).read_bytes(), b'largest original')
        self.assertEqual(Path(outputs[0]).suffix, '.webp')
        self.assertEqual(self.server.requests['/large.webp'], 1)
        self.assertEqual(self.server.requests['/small.jpg'], 0)
        self.assertEqual(self.server.requests['/media.mp4'], 0)
        self.assertEqual(processes[0].returncode, 0)
        self.assertTrue(processes[0].stdout.closed)

    def test_best_thumbnail_404_falls_back_and_no_thumbnail_fails(self):
        (self.fixture / 'small.jpg').write_bytes(b'fallback original')
        self.info['thumbnails'] = [
            {'url': self.base + '/small.jpg', 'width': 320, 'height': 180},
            {'url': self.base + '/missing.jpg', 'width': 1280, 'height': 720}]
        outputs = self.real_cover()
        self.assertEqual(Path(outputs[0]).read_bytes(), b'fallback original')
        self.assertGreater(self.server.requests['/missing.jpg'], 0)
        Path(outputs[0]).unlink()
        self.info['thumbnails'] = []
        with self.assertRaisesRegex(RuntimeError, '非空封面'):
            self.real_cover()
        self.assertEqual(self.server.requests['/media.mp4'], 0)

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
