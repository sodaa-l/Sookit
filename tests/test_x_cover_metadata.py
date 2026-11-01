"""X 视频缺失 poster 的异步元数据回填回归。"""
import json
import os
import subprocess
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from sookit.core.task_queue import Task, TaskQueueManager, TaskType
from sookit.core.youtube_utils import extract_x_poster_url, fetch_x_poster_url


MEDIUM_POSTER = 'https://pbs.twimg.com/media/abc123?format=jpg&name=medium'


def _twitter_info(url=MEDIUM_POSTER):
    return {
        'extractor': 'Twitter',
        'thumbnail': 'https://pbs.twimg.com/media/abc123?format=jpg&name=large',
        'thumbnails': [
            {'id': 'large', 'url': 'https://pbs.twimg.com/media/abc123?format=jpg&name=large',
             'width': 3840, 'height': 2160},
            {'id': 'medium', 'url': url, 'width': 1200, 'height': 675},
            {'id': 'small', 'url': 'https://pbs.twimg.com/media/abc123?format=jpg&name=small',
             'width': 640, 'height': 360},
        ],
    }


class XPosterMetadataTests(unittest.TestCase):
    def test_extract_prefers_twitter_medium_thumbnail(self):
        self.assertEqual(extract_x_poster_url(_twitter_info()), MEDIUM_POSTER)

    def test_extract_rejects_other_extractors_and_untrusted_hosts(self):
        info = _twitter_info()
        info['extractor'] = 'Youtube'
        self.assertEqual(extract_x_poster_url(info), '')

        for url in (
                'http://pbs.twimg.com/media/abc123?name=medium',
                'https://pbs.twimg.com.evil.example/media/abc123?name=medium',
                'https://evil.example/media/abc123'):
            with self.subTest(url=url):
                self.assertEqual(extract_x_poster_url(_twitter_info(url)), '')

    @patch('sookit.core.ytdlp_utils.build_ytdlp_cmd', return_value=['yt-dlp', 'args'])
    @patch('sookit.core.youtube_utils.subprocess.run')
    def test_fetch_uses_bounded_metadata_only_command(self, run, build_cmd):
        run.return_value = SimpleNamespace(
            returncode=0, stdout=json.dumps(_twitter_info()))
        url = 'https://twitter.com/user/status/123456789/video/1'

        self.assertEqual(fetch_x_poster_url(url), MEDIUM_POSTER)
        build_cmd.assert_called_once_with(
            '--dump-single-json', '--skip-download', '--ignore-config',
            '--no-playlist', '--no-warnings', '--quiet',
            'https://x.com/user/status/123456789')
        args, kwargs = run.call_args
        self.assertEqual(args, (['yt-dlp', 'args'],))
        self.assertTrue(kwargs['capture_output'])
        self.assertTrue(kwargs['text'])
        self.assertEqual(kwargs['timeout'], 30)

    @patch('sookit.core.youtube_utils.subprocess.run')
    def test_invalid_status_url_does_not_start_ytdlp(self, run):
        self.assertEqual(fetch_x_poster_url('http://example.com/video'), '')
        run.assert_not_called()

    @patch('sookit.core.ytdlp_utils.build_ytdlp_cmd', return_value=['yt-dlp', 'args'])
    @patch('sookit.core.youtube_utils.subprocess.run')
    def test_query_failures_return_empty(self, run, _build_cmd):
        cases = (
            SimpleNamespace(returncode=1, stdout=''),
            SimpleNamespace(returncode=0, stdout='not json'),
            subprocess.TimeoutExpired(cmd=['yt-dlp'], timeout=30),
        )
        for outcome in cases:
            with self.subTest(outcome=type(outcome).__name__):
                if isinstance(outcome, Exception):
                    run.side_effect = outcome
                    run.return_value = None
                else:
                    run.side_effect = None
                    run.return_value = outcome
                self.assertEqual(
                    fetch_x_poster_url('https://x.com/user/status/123456789'), '')


class LateCoverQueueTests(unittest.TestCase):
    def setUp(self):
        with patch.object(TaskQueueManager, '_load_completed_tasks'), \
                patch.object(TaskQueueManager, '_cleanup_stale_workspaces'):
            self.manager = TaskQueueManager()
        self.manager.max_concurrent = 0
        self.updated = []
        self.manager.task_updated.connect(self.updated.append)

    @staticmethod
    def make_task(task_id):
        return Task(task_id, TaskType.YTDLP, 'X video', lambda: None, (),
                    metadata={'filename': 'X video'})

    def test_active_task_receives_late_cover_metadata(self):
        task = self.make_task('active-x')
        self.manager.active_tasks[task.task_id] = task

        self.assertTrue(self.manager.update_task_cover_url(task.task_id, MEDIUM_POSTER))
        self.assertEqual(task.cover_url, MEDIUM_POSTER)
        self.assertEqual(task.metadata['cover_url'], MEDIUM_POSTER)
        self.assertEqual(self.updated, [task])

    def test_completed_task_receives_cover_and_persists_metadata(self):
        task = self.make_task('completed-x')
        self.manager.completed_tasks[task.task_id] = task

        with tempfile.TemporaryDirectory() as temp_dir:
            completed_file = os.path.join(temp_dir, 'completed_tasks.json')
            with patch('sookit.core.task_queue.COMPLETED_FILE', completed_file):
                self.assertTrue(
                    self.manager.update_task_cover_url(task.task_id, MEDIUM_POSTER))
                with open(completed_file, encoding='utf-8') as file:
                    saved = json.load(file)

        self.assertEqual(saved[0]['metadata']['cover_url'], MEDIUM_POSTER)
        self.assertEqual(task.cover_url, MEDIUM_POSTER)
        self.assertEqual(self.updated, [task])

    def test_missing_task_and_duplicate_url_are_ignored(self):
        self.assertFalse(
            self.manager.update_task_cover_url('missing', MEDIUM_POSTER))
        task = self.make_task('active-duplicate')
        task.cover_url = MEDIUM_POSTER
        task.metadata['cover_url'] = MEDIUM_POSTER
        self.manager.active_tasks[task.task_id] = task

        self.assertFalse(
            self.manager.update_task_cover_url(task.task_id, MEDIUM_POSTER))
        self.assertEqual(self.updated, [])


if __name__ == '__main__':
    unittest.main()
