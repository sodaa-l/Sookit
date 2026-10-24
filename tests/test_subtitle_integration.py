"""真实 yt-dlp/FFmpeg 与任务队列生命周期集成验证。

运行：uv run --no-sync python -m unittest discover -s tests -p test_subtitle_integration.py -v
仅请求本机 HTTP；应用配置、队列记录、缓存和下载回退目录均隔离至临时目录。
"""
import collections
import copy
import functools
import http.server
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QEventLoop, QTimer
from PyQt6.QtWidgets import QApplication

from sookit.core import config, ffmpeg_utils, subtitle_utils, task_queue
from sookit.core.task_queue import TaskQueueManager, TaskStatus, TaskType
from sookit.core.workers import TaskWorker


class IsolatedQtTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 使用 QApplication，允许主代理随后在同一进程运行 QWidget 测试。
        with patch.dict(os.environ, {'QT_QPA_PLATFORM': 'offscreen'}):
            cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='sookit-subtitle-case-')
        self.addCleanup(temporary.cleanup)
        self.case_dir = Path(temporary.name)
        data = self.case_dir / 'data'
        data.mkdir()
        isolated_config = {'general': {'toast_notification': False,
                                       'task_complete_action': 0}}
        (data / 'config.json').write_text(json.dumps(isolated_config), encoding='utf-8')
        overrides = {
            'VIDEOTOOLBOX_DATA_DIR': str(data),
            'VIDEOTOOLBOX_COVER_DIR': str(self.case_dir / 'covers'),
            'VIDEOTOOLBOX_LOG_DIR': str(self.case_dir / 'log'),
            'APPDATA': str(self.case_dir / 'appdata'),
            'LOCALAPPDATA': str(self.case_dir / 'localappdata'),
            'USERPROFILE': str(self.case_dir / 'home'),
            'HOME': str(self.case_dir / 'home'),
            'XDG_CACHE_HOME': str(self.case_dir / 'cache'),
            'XDG_CONFIG_HOME': str(self.case_dir / 'config'),
        }
        self.enterContext(patch.dict(os.environ, overrides))
        self.enterContext(patch.object(config, '_config_cache', isolated_config))
        self.enterContext(patch.object(config, '_config_cache_dirty', False))
        self.enterContext(patch.object(task_queue, 'COMPLETED_FILE',
                                      str(data / 'completed_tasks.json')))
        self.enterContext(patch.object(task_queue, 'ACTIVE_WORKSPACES_FILE',
                                      str(data / 'active_workspaces.json')))
        self.enterContext(patch.object(task_queue, 'COVER_CACHE_DIR',
                                      str(self.case_dir / 'covers')))

    def wait_until(self, predicate, timeout_ms=30000):
        """让队列的 Qt 信号在主线程正常派发，超时由单次定时器保证。"""
        if predicate():
            return
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(10)
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        poll.start()
        deadline.start(timeout_ms)
        loop.exec()
        poll.stop()
        deadline.stop()
        self.assertTrue(predicate(), f'异步阶段未在 {timeout_ms} ms 内完成')


class WorkerCancellationTests(IsolatedQtTests):
    def run_thread(self, worker):
        self.addCleanup(lambda: worker.wait(5000))
        worker.start()
        self.assertTrue(worker.wait(5000), 'TaskWorker 线程未退出')

    def test_cancel_before_execution_never_calls_function(self):
        func = Mock()
        worker = TaskWorker('cancel-before', TaskType.YTDLP, func, ())
        worker.cancel()
        self.run_thread(worker)
        func.assert_not_called()
        self.assertEqual(worker.output_files, [])

    def test_cancel_between_stages_stops_and_cleans_recreated_workspace(self):
        workspace = self.case_dir / 'workspace'
        workspace.mkdir()
        stages = []

        def task(*, log, workspace, **kwargs):
            log('第一条字幕已下载')
            worker.cancel()
            Path(workspace).mkdir(exist_ok=True)
            (Path(workspace) / 'race.tmp').write_text('late', encoding='utf-8')
            log('准备第二条字幕')
            stages.append('second')
            return []

        worker = TaskWorker('cancel-between', TaskType.YTDLP, task, (),
                            workspace=str(workspace))
        self.run_thread(worker)
        self.assertEqual(stages, [])
        self.assertFalse(workspace.exists())
        self.assertEqual(worker.error, '')

    def test_cancel_without_delete_keeps_workspace(self):
        artifact = self.case_dir / 'keep.part'
        artifact.write_text('partial', encoding='utf-8')
        worker = TaskWorker('keep', TaskType.YTDLP, Mock(), (), workspace=str(self.case_dir))
        worker.cancel(delete_part=False)
        self.run_thread(worker)
        self.assertTrue(artifact.exists())

    def test_process_created_after_cancel_is_terminated(self):
        worker = TaskWorker('late-process', TaskType.YTDLP, Mock(), ())
        worker.cancel()
        process = Mock(pid=123456789)
        with patch('sookit.core.workers._kill_process_tree') as kill:
            with self.assertRaisesRegex(RuntimeError, '任务已取消'):
                worker._on_process_created(process)
        kill.assert_called_once_with(process.pid)
        process.terminate.assert_called_once()


class _FixtureHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.server.requests[self.path] += 1
        if self.path == '/slow.vtt':
            # 首块已发送后才通知测试；剩余响应由取消后的 release 事件释放。
            first = b'WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nSlow subtitle\n\n'
            self.send_response(200)
            self.send_header('Content-Type', 'text/vtt')
            self.send_header('Content-Length', str(len(first) + 1024 * 1024))
            self.end_headers()
            try:
                self.wfile.write(first)
                self.wfile.flush()
                self.server.slow_started.set()
                self.server.slow_release.wait(15)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            finally:
                self.server.slow_finished.set()
            return
        super().do_GET()

    def log_message(self, *args):
        pass


class RealSubtitlePipelineTests(IsolatedQtTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from sookit.core.ytdlp_utils import get_ytdlp_cmd
        cls.ytdlp_cmd = get_ytdlp_cmd()
        if not cls.ytdlp_cmd:
            raise unittest.SkipTest('本机未安装 yt-dlp，无法运行真实下载器集成验证')
        cls.ffmpeg = ffmpeg_utils.get_ffmpeg_path()
        if not Path(cls.ffmpeg).is_file():
            cls.ffmpeg = shutil.which('ffmpeg')
        if not cls.ffmpeg:
            raise unittest.SkipTest('本机未安装 FFmpeg，无法运行真实转换集成验证')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        for name, cmd in [('yt-dlp', cls.ytdlp_cmd + ['--ignore-config', '--version']),
                          ('FFmpeg', [cls.ffmpeg, '-version'])]:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=20, creationflags=flags,
                                    check=True)
            print(f'{name}: {result.stdout.splitlines()[0]}', flush=True)

    def setUp(self):
        super().setUp()
        self.fixture = self.case_dir / 'fixture'
        self.fixture.mkdir()
        self.fixture.joinpath('en.vtt').write_text(
            'WEBVTT\n\n00:00:00.000 --> 00:00:01.500\n<b>Hello</b> &amp; world\n\n'
            '00:00:02.000 --> 00:00:03.000\nSecond paragraph\n', encoding='utf-8')
        self.fixture.joinpath('auto.vtt').write_text(
            'WEBVTT\n\n00:00:00.000 --> 00:00:01.500\nAutomatic words\n', encoding='utf-8')
        self.comment_bytes = '{"chat": "你好", "items": [1, 2]}\n'.encode('utf-8')
        self.fixture.joinpath('chat.json').write_bytes(self.comment_bytes)
        self.fixture.joinpath('captions.ass').write_text(
            '[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n\n'
            '[V4+ Styles]\n'
            'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, '
            'OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, '
            'ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, '
            'MarginL, MarginR, MarginV, Encoding\n'
            'Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,'
            '0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n\n'
            '[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, '
            'MarginV, Effect, Text\n'
            'Dialogue: 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,{\\b1}ASS hello{\\b0}\\N第二行\n'
            'Dialogue: 0,0:00:04.00,0:00:05.00,Default,,0,0,0,,Another paragraph\n',
            encoding='utf-8')
        self.fixture.joinpath('invalid.ass').write_text('invalid subtitle input\n', encoding='utf-8')
        handler = functools.partial(_FixtureHandler, directory=str(self.fixture))
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.server.requests = collections.Counter()
        self.server.slow_started = threading.Event()
        self.server.slow_release = threading.Event()
        self.server.slow_finished = threading.Event()
        self.server_thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.05), daemon=True)
        self.server_thread.start()
        self.addCleanup(self.stop_server)
        base = f'http://127.0.0.1:{self.server.server_port}'
        self.base = base
        self.info = {
            'id': 'fixture', 'title': '多语言 subtitle fixture',
            'extractor': 'generic', 'extractor_key': 'Generic',
            # yt-dlp 在字幕失败时可能重提取网页，该回退也必须留在本机。
            'webpage_url': base + '/watch',
            'url': base + '/media.mp4', 'ext': 'mp4',
            'formats': [{'format_id': '18', 'url': base + '/media.mp4', 'ext': 'mp4'}],
            'subtitles': {
                'en': [{'ext': 'vtt', 'url': base + '/en.vtt', 'name': 'English'}],
                'zh-Hans': [{'ext': 'srt', 'data':
                    '1\n00:00:00,000 --> 00:00:01,500\n你好，世界\n\n', 'name': '简体中文'}],
                'live_chat': [{'ext': 'json', 'url': base + '/chat.json', 'name': '实时评论'}],
            },
            'automatic_captions': {
                'en': [{'ext': 'vtt', 'url': base + '/auto.vtt', 'name': 'English auto'}],
            },
        }
        self.info_path = self.fixture / 'fixture.info.json'
        self.destination = self.case_dir / 'finished'
        self.destination.mkdir()
        self.processes = []
        self.worker_thread_ids = []
        self.tasks = []
        self.logs = []
        # 直接构造独立 manager，既走真实实现又不替换任何共享单例。
        self.manager = TaskQueueManager()
        self.manager.max_concurrent = 1
        self.manager.task_log.connect(lambda task_id, msg: self.logs.append(msg))
        self.completions, self.failures = [], []
        self.manager.task_completed.connect(self.completions.append)
        self.manager.task_failed.connect(self.failures.append)
        self.addCleanup(self.stop_workers)
        self.runner = self.enterContext(patch.object(
            subtitle_utils, 'run_ytdlp', side_effect=self.real_fixture_runner))

    def stop_server(self):
        self.server.slow_release.set()
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)
        self.assertFalse(self.server_thread.is_alive())

    def stop_workers(self):
        self.server.slow_release.set()
        self.manager.max_concurrent = 0
        for task in self.tasks:
            if task.worker and task.worker.isRunning():
                task.worker.cancel()
                self.assertTrue(task.worker.wait(10000), '测试清理时工作线程未退出')
        for process in self.processes:
            if process.poll() is None:
                from sookit.core.workers import _kill_process_tree
                _kill_process_tree(process.pid)
                if process.poll() is None:
                    process.kill()
            process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
        self.app.processEvents()

    def real_fixture_runner(self, cmd, *args, **kwargs):
        # 唯一替换点是元数据入口；命令构造、yt-dlp 子进程、HTTP 下载和清单均真实。
        cmd = [arg for arg in cmd if arg != 'https://fixture.invalid/watch']
        cmd.extend(['--load-info-json', str(self.info_path), '--no-cache-dir',
                    '--socket-timeout', '5', '--retries', '0', '--fragment-retries', '0',
                    '--proxy', ''])
        callback = kwargs.get('on_process_created')

        def created(process):
            self.processes.append(process)
            self.worker_thread_ids.append(threading.get_ident())
            if callback:
                callback(process)

        kwargs['on_process_created'] = created
        return ffmpeg_utils.run_ytdlp(cmd, *args, **kwargs)

    def write_info(self):
        self.info_path.write_text(json.dumps(self.info, ensure_ascii=False), encoding='utf-8')

    def enqueue(self, *, tracks=None, formats=None, comment=None):
        self.write_info()
        if comment is None:
            func = subtitle_utils.download_subtitles
            kwargs = {'tracks': tracks or subtitle_utils.build_subtitle_tracks(self.info)['subtitle_tracks'],
                      'formats': formats or ['srt', 'txt', 'vtt']}
        else:
            func = subtitle_utils.download_comments
            kwargs = {'track': comment}
        task = self.manager.add_task(
            TaskType.YTDLP, '字幕本地集成', func,
            ('https://fixture.invalid/watch', str(self.destination)),
            metadata={'out_dir': str(self.destination)}, kwargs=kwargs)
        self.tasks.append(task)
        return task

    def await_task(self, task, expected):
        self.wait_until(lambda: task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED))
        self.assertTrue(task.worker.wait(5000), '\n'.join(self.logs))
        self.app.processEvents()
        self.assertEqual(task.status, expected, '\n'.join(self.logs))
        self.assertTrue(self.worker_thread_ids)
        self.assertTrue(all(tid != threading.get_ident() for tid in self.worker_thread_ids))
        self.assertTrue(all(p.poll() is not None for p in self.processes))
        return [Path(p) for p in task.worker.output_files]

    def assert_clean_workspace(self, task):
        self.assertRegex(Path(task.workspace).name, r'^\._sookit_tmp_[0-9A-HJKMNP-TV-Z]{26}$')
        self.assertEqual(Path(task.workspace).parent, self.destination)
        self.assertFalse(Path(task.workspace).exists())
        registry = json.loads(Path(task_queue.ACTIVE_WORKSPACES_FILE).read_text(encoding='utf-8'))
        self.assertEqual(registry, [])
        self.assertEqual(self.server.requests['/media.mp4'], 0)
        self.assertFalse(list(self.destination.glob('._*')))

    def final_paths(self, task, count):
        self.assertIn(task, self.manager.get_completed_tasks())
        self.assertNotIn(task, self.manager.get_active_tasks())
        self.assertEqual(task.progress, 100)
        self.assertEqual(task.error, '')
        self.assertEqual(task.worker.error, '')
        self.assertEqual(len(task.worker.output_files), count)
        paths = [self.destination / Path(p).name for p in task.worker.output_files]
        self.assertTrue(all(p.is_file() and p.stat().st_size > 0 for p in paths))
        self.assertEqual(Path(task.output_path), paths[0])
        persisted = json.loads(Path(task_queue.COMPLETED_FILE).read_text(encoding='utf-8'))
        self.assertIn(task.task_id, [item['task_id'] for item in persisted])
        self.assert_clean_workspace(task)
        return paths

    def test_real_multiple_languages_sources_formats_and_queue_finalization(self):
        task = self.enqueue()
        self.assertEqual(task.status, TaskStatus.RUNNING)
        self.assertTrue(Path(task.workspace).is_dir())
        registry = json.loads(Path(task_queue.ACTIVE_WORKSPACES_FILE).read_text(encoding='utf-8'))
        self.assertEqual(registry, [task.workspace])
        # 未登记的临时文件不能进入最终交付。
        (Path(task.workspace) / 'private.tmp').write_text('temporary', encoding='utf-8')
        self.await_task(task, TaskStatus.COMPLETED)
        paths = self.final_paths(task, 9)
        self.assertEqual(len({p.name for p in paths}), 9)
        self.assertEqual(len(list(self.destination.iterdir())), 9)
        self.assertEqual(self.runner.call_count, 3)
        self.assertEqual(self.server.requests['/en.vtt'], 1)
        self.assertEqual(self.server.requests['/auto.vtt'], 1)
        self.assertEqual(self.server.requests['/chat.json'], 0)
        texts = '\n'.join(p.read_text(encoding='utf-8-sig') for p in paths if p.suffix == '.txt')
        self.assertIn('Hello & world', texts)
        self.assertIn('你好，世界', texts)
        self.assertIn('Automatic words', texts)
        self.assertNotIn('-->', texts)
        self.assertNotIn('<b>', texts)
        self.assertEqual(self.completions, [task])
        self.assertEqual(self.failures, [])

    def test_real_comment_preserves_original_json(self):
        comment = subtitle_utils.build_subtitle_tracks(self.info)['comment_tracks'][0]
        task = self.enqueue(comment=comment)
        self.await_task(task, TaskStatus.COMPLETED)
        paths = self.final_paths(task, 1)
        self.assertEqual(paths[0].suffix, '.json')
        self.assertEqual(paths[0].read_bytes(), self.comment_bytes)
        self.assertEqual(self.runner.call_count, 1)
        self.assertEqual(self.server.requests['/chat.json'], 1)
        self.assertEqual(len(list(self.destination.iterdir())), 1)

    def ass_tracks(self, filename='captions.ass'):
        self.info['subtitles'] = {'ja': [{'ext': 'ass', 'url': self.base + '/' + filename}]}
        self.info['automatic_captions'] = {}
        return subtitle_utils.build_subtitle_tracks(self.info)['subtitle_tracks']

    def real_ffmpeg_runner(self, cmd, log, **kwargs):
        callback = kwargs.get('on_process_created')

        def created(process):
            self.processes.append(process)
            self.worker_thread_ids.append(threading.get_ident())
            if callback:
                callback(process)

        kwargs['on_process_created'] = created
        return ffmpeg_utils.run_ffmpeg(cmd, log, **kwargs)

    def test_real_ass_uses_ffmpeg_and_exports_three_formats(self):
        tracks = self.ass_tracks()
        with patch.object(subtitle_utils, 'run_ffmpeg', side_effect=self.real_ffmpeg_runner) as convert:
            task = self.enqueue(tracks=tracks)
            self.await_task(task, TaskStatus.COMPLETED)
        paths = self.final_paths(task, 3)
        self.assertEqual(convert.call_count, 1)
        self.assertEqual(self.runner.call_count, 1)
        self.assertEqual(len(self.processes), 2)
        self.assertEqual(self.server.requests['/captions.ass'], 1)
        text = next(p for p in paths if p.suffix == '.txt').read_text(encoding='utf-8')
        self.assertEqual(text, 'ASS hello\n第二行\n\nAnother paragraph\n')
        srt = next(p for p in paths if p.suffix == '.srt').read_text(encoding='utf-8')
        self.assertIn('00:00:01,000 --> 00:00:02,500', srt)
        self.assertNotIn('{\\b', srt)
        self.assertEqual(len(list(self.destination.iterdir())), 3)

    def test_real_later_http_failure_publishes_nothing_and_cleans_workspace(self):
        self.info['automatic_captions']['en'][0]['url'] = self.base + '/missing.vtt'
        task = self.enqueue()
        self.await_task(task, TaskStatus.FAILED)
        self.assertEqual(task.worker.output_files, [])
        self.assertIn('404', task.error)
        self.assertEqual(self.server.requests['/en.vtt'], 1)
        self.assertEqual(self.server.requests['/missing.vtt'], 1)
        self.assertEqual(self.failures, [task])
        self.assertEqual(self.completions, [])
        self.assertIn(task, self.manager.get_active_tasks())
        self.assertEqual(self.manager.get_completed_tasks(), [])
        self.assertFalse(Path(task_queue.COMPLETED_FILE).exists())
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assert_clean_workspace(task)

    def test_real_invalid_ass_fails_ffmpeg_and_cleans_workspace(self):
        with patch.object(subtitle_utils, 'run_ffmpeg', side_effect=self.real_ffmpeg_runner) as convert:
            task = self.enqueue(tracks=self.ass_tracks('invalid.ass'))
            self.await_task(task, TaskStatus.FAILED)
        self.assertEqual(convert.call_count, 1)
        self.assertIn('ffmpeg', task.error.lower())
        self.assertEqual(task.worker.output_files, [])
        self.assertEqual(self.failures, [task])
        self.assertFalse(Path(task_queue.COMPLETED_FILE).exists())
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assert_clean_workspace(task)

    def test_real_cancel_during_http_response_kills_process_and_cleans_workspace(self):
        self.info['subtitles']['en'][0]['url'] = self.base + '/slow.vtt'
        task = self.enqueue()
        self.wait_until(self.server.slow_started.is_set)
        self.assertTrue(task.worker.isRunning())
        self.assertIsNone(self.processes[0].poll())
        self.manager.cancel_task(task.task_id)
        self.server.slow_release.set()
        self.wait_until(lambda: not task.worker.isRunning(), timeout_ms=10000)
        self.assertTrue(task.worker.wait(1000))
        self.app.processEvents()
        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertEqual(task.worker.error, '')
        self.assertEqual(task.worker.output_files, [])
        self.assertIsNotNone(self.processes[0].poll())
        self.assertEqual(self.runner.call_count, 1)
        self.assertEqual(self.server.requests['/auto.vtt'], 0)
        self.assertEqual(self.completions, [])
        self.assertEqual(self.failures, [])
        self.assertEqual(self.manager.get_active_tasks(), [])
        self.assertEqual(self.manager.get_completed_tasks(), [])
        self.assertNotIn(task.worker, self.manager._cancelling_workers)
        self.assertFalse(Path(task_queue.COMPLETED_FILE).exists())
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assert_clean_workspace(task)

    def test_real_cancel_during_ffmpeg_kills_process_and_cleans_workspace(self):
        conversion_started = threading.Event()
        release_callback = threading.Event()
        self.addCleanup(release_callback.set)
        tracks = self.ass_tracks()

        def paced_conversion(cmd, log, **kwargs):
            # 只给本次真实 ffmpeg 输入加读取节流，避免微型 fixture 在取消前已结束。
            cmd = list(cmd)
            cmd.insert(cmd.index('-i'), '-re')
            callback = kwargs.get('on_process_created')

            def created(process):
                self.processes.append(process)
                self.worker_thread_ids.append(threading.get_ident())
                callback(process)
                conversion_started.set()
                if not release_callback.wait(15):
                    raise RuntimeError('测试未及时释放 FFmpeg 创建回调')

            kwargs['on_process_created'] = created
            return ffmpeg_utils.run_ffmpeg(cmd, log, **kwargs)

        with patch.object(subtitle_utils, 'run_ffmpeg', side_effect=paced_conversion) as convert:
            task = self.enqueue(tracks=tracks)
            try:
                self.wait_until(conversion_started.is_set)
                self.assertEqual(len(self.processes), 2)
                self.assertIsNone(self.processes[-1].poll())
                self.manager.cancel_task(task.task_id)
            finally:
                release_callback.set()
            self.wait_until(lambda: not task.worker.isRunning(), timeout_ms=10000)
            self.assertTrue(task.worker.wait(1000))
            self.app.processEvents()
        self.assertEqual(convert.call_count, 1)
        self.assertEqual(task.status, TaskStatus.CANCELLED)
        self.assertEqual(task.worker.error, '')
        self.assertEqual(task.worker.output_files, [])
        self.assertTrue(all(p.poll() is not None for p in self.processes))
        self.assertEqual(self.completions, [])
        self.assertEqual(self.failures, [])
        self.assertEqual(self.manager.get_active_tasks(), [])
        self.assertEqual(self.manager.get_completed_tasks(), [])
        self.assertNotIn(task.worker, self.manager._cancelling_workers)
        self.assertFalse(Path(task_queue.COMPLETED_FILE).exists())
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assert_clean_workspace(task)

    def test_real_queue_dispatches_next_task_and_preserves_existing_file(self):
        tracks = subtitle_utils.build_subtitle_tracks(self.info)['subtitle_tracks'][:1]
        first = self.enqueue(tracks=tracks, formats=['txt'])
        second = self.enqueue(tracks=copy.deepcopy(tracks), formats=['txt'])
        self.assertEqual(second.status, TaskStatus.WAITING)
        self.assertEqual(second.workspace, '')
        self.wait_until(lambda: second.status in (TaskStatus.COMPLETED, TaskStatus.FAILED))
        self.assertTrue(first.worker.wait(5000))
        self.await_task(second, TaskStatus.COMPLETED)
        self.assertEqual(first.status, TaskStatus.COMPLETED)
        self.assertNotEqual(first.workspace, second.workspace)
        self.assertNotEqual(first.output_path, second.output_path)
        self.assertTrue(Path(second.output_path).stem.endswith('_1'))
        self.assertEqual(Path(first.output_path).read_bytes(), Path(second.output_path).read_bytes())
        self.assertEqual(len(list(self.destination.iterdir())), 2)
        self.assertEqual(self.server.requests['/en.vtt'], 2)
        self.assertEqual(self.completions, [first, second])
        self.assertEqual(self.failures, [])
        self.assert_clean_workspace(first)
        self.assert_clean_workspace(second)
        persisted = json.loads(Path(task_queue.COMPLETED_FILE).read_text(encoding='utf-8'))
        self.assertEqual(len(persisted), 2)


if __name__ == '__main__':
    unittest.main()
