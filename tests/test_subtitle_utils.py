"""Offline regression tests for subtitle discovery, manifests and local exports."""

import io
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from sookit.core import ffmpeg_utils, subtitle_utils as subtitles
from sookit.core.functions import Functions


URL = 'https://example.test/watch/123'
VTT = ('WEBVTT\n\nNOTE test metadata\nignored\n\n'
       'cue-one\n00:01.000 --> 00:02.500 align:start\n'
       '<v Speaker><c.green>Hello &amp; <i>world</i></c><br>line two\n\n'
       '00:00:03.000 --> 00:00:04.000\nHello &amp; world\nline two\n')


def track(language='en', source='platform', formats=None, bucket=None):
    bucket = bucket or ('automatic_captions' if source == 'automatic' else 'subtitles')
    return {'key': f'{bucket}:{language}', 'language': language, 'name': language,
            'source': source, 'formats': formats or ['vtt'], 'bucket': bucket,
            'title': 'Video title'}


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'output'
        self.output.mkdir()
        self.workspace = self.output / 'workspace'
        self.workspace.mkdir()
        self.commands = []
        self.ext = 'vtt'
        self.content = VTT.encode()
        self.manifest_edit = lambda data, source: data
        self.omit_manifest = False
        self.empty_source = False
        self.addCleanup(patch.stopall)
        patch.object(subtitles, 'build_ytdlp_cmd', side_effect=lambda *args: ['yt-dlp', *args]).start()
        self.runner = patch.object(subtitles, 'run_ytdlp', side_effect=self.fake_download).start()

    def fake_download(self, cmd, log, **kwargs):
        self.commands.append((cmd, kwargs))
        source_root = Path(cmd[cmd.index('-o') + 1]).parent
        source = source_root / ('source.' + self.ext)
        source.write_bytes(b'' if self.empty_source else self.content)
        language = self.current_language
        data = {language: {'filepath': str(source), 'ext': self.ext}}
        data = self.manifest_edit(data, source)
        if not self.omit_manifest:
            Path(kwargs['subtitle_manifest']).write_text(json.dumps(data) + '\n', encoding='utf-8')
        if kwargs.get('on_process_created'):
            kwargs['on_process_created']('process')
        return []

    def download(self, selected=None, formats=None, **kwargs):
        selected = selected or [track()]
        self.current_language = selected[0]['language']
        return subtitles.download_subtitles(URL, str(self.output), selected,
                                             formats or ['srt', 'txt', 'vtt'],
                                             workspace=str(self.workspace), **kwargs)

    def test_one_fetch_exports_three_formats_and_only_outputs_remain(self):
        callbacks, progress = [], []
        files = self.download(on_process_created=callbacks.append, on_progress=progress.append)
        self.assertEqual(self.runner.call_count, 1)
        self.assertEqual({Path(path).suffix for path in files}, {'.srt', '.txt', '.vtt'})
        self.assertEqual(set(self.workspace.iterdir()), {Path(path) for path in files})
        txt = next(Path(path) for path in files if path.endswith('.txt')).read_text(encoding='utf-8')
        self.assertEqual(txt, 'Hello & world\nline two\n\nHello & world\nline two\n')
        srt = next(Path(path) for path in files if path.endswith('.srt')).read_text(encoding='utf-8')
        self.assertIn('1\n00:00:01,000 --> 00:00:02,500\nHello &amp; world', srt)
        self.assertEqual(callbacks, ['process'])
        self.assertEqual(progress, [100])

    def test_command_uses_exact_escaped_tag_and_sniffed_url(self):
        language = 'zh-Hans.test+[original]'
        self.download([track(language)], remote=True)
        cmd, kwargs = self.commands[0]
        for arg in ('--ignore-config', '--no-playlist', '--skip-download', '--no-simulate',
                    '--write-subs', '--no-write-auto-subs'):
            self.assertIn(arg, cmd)
        regex = cmd[cmd.index('--sub-langs') + 1]
        self.assertEqual(regex, '^' + re.escape(language) + '$')
        self.assertIsNotNone(re.fullmatch(regex, language))
        self.assertIsNone(re.fullmatch(regex, language + 'extra'))
        self.assertEqual(cmd[-1], URL)
        self.assertNotIn('--', cmd)  # run_ytdlp appends its own --print-to-file option.
        self.assertEqual(cmd[cmd.index('--remote-components') + 1], 'ejs:github')
        self.assertTrue(Path(kwargs['subtitle_manifest']).is_relative_to(self.workspace))

    def test_same_language_two_sources_have_separate_commands_and_names(self):
        files = self.download([track(), track(source='automatic')], ['txt'])
        self.assertEqual(self.runner.call_count, 2)
        self.assertEqual(len(files), 2)
        self.assertNotEqual(files[0], files[1])
        platform, automatic = (cmd for cmd, _ in self.commands)
        self.assertIn('--write-subs', platform)
        self.assertIn('--no-write-auto-subs', platform)
        self.assertIn('--write-auto-subs', automatic)
        self.assertIn('--no-write-subs', automatic)
        self.assertIn('_platform_', files[0])
        self.assertIn('_automatic_', files[1])

    def test_bilibili_ai_uses_ordinary_bucket(self):
        self.download([track(source='automatic', bucket='subtitles')], ['txt'])
        self.assertIn('--write-subs', self.commands[0][0])
        self.assertIn('--no-write-auto-subs', self.commands[0][0])

    def test_missing_empty_null_or_wrong_language_manifest_fail(self):
        mutations = [lambda data, source: {}, lambda data, source: None,
                     lambda data, source: {'fr': data['en']},
                     lambda data, source: {**data, 'fr': data['en']},
                     lambda data, source: {'en': {}}]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                self.manifest_edit = mutation
                with self.assertRaises(RuntimeError):
                    self.download()
                self.assertEqual(list(self.workspace.iterdir()), [])

    def test_exit_zero_without_manifest_fails(self):
        self.omit_manifest = True
        with self.assertRaisesRegex(RuntimeError, '未返回字幕清单'):
            self.download()
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_missing_empty_external_and_wrong_extension_sources_fail(self):
        outside = self.root / 'outside.vtt'
        outside.write_text(VTT, encoding='utf-8')
        for kind in ('missing', 'empty', 'external', 'ext'):
            with self.subTest(kind=kind):
                self.empty_source = kind == 'empty'
                def mutate(data, source):
                    if kind == 'missing':
                        source.unlink()
                    elif kind == 'external':
                        data['en']['filepath'] = str(outside)
                    elif kind == 'ext':
                        data['en']['ext'] = 'srt'
                    return data
                self.manifest_edit = mutate
                with self.assertRaises(RuntimeError):
                    self.download()
                self.assertEqual(list(self.workspace.iterdir()), [])

    def test_actual_extension_is_checked_after_preference_fallback(self):
        self.ext = 'srt'
        self.content = b'1\n00:00:01,000 --> 00:00:02,000\nFallback subtitle\n'
        files = self.download([track(formats=['vtt', 'srt'])], ['txt'])
        self.assertEqual(Path(files[0]).read_text(encoding='utf-8'), 'Fallback subtitle\n')
        cmd = self.commands[0][0]
        self.assertEqual(cmd[cmd.index('--sub-format') + 1], 'vtt/srt')

    def test_export_failure_leaves_no_published_files(self):
        self.content = b'WEBVTT\n\ninvalid data\n'
        with self.assertRaises(RuntimeError):
            self.download()
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_later_track_failure_does_not_publish_first_track(self):
        original = self.fake_download
        def fail_second(cmd, log, **kwargs):
            if self.commands:
                raise RuntimeError('second download failed')
            return original(cmd, log, **kwargs)
        self.runner.side_effect = fail_second
        with self.assertRaisesRegex(RuntimeError, 'second download failed'):
            self.download([track(), track(source='automatic')], ['txt'])
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_non_native_format_uses_managed_ffmpeg_and_checks_output(self):
        self.ext = 'ass'
        self.content = b'ASS input'
        callback = lambda process: None
        def convert(cmd, log, **kwargs):
            Path(cmd[-1]).write_text(VTT, encoding='utf-8')
        with patch.object(subtitles, 'run_ffmpeg', side_effect=convert) as ffmpeg:
            files = self.download([track(formats=['ass'])], ['txt'], on_process_created=callback)
            self.assertEqual(ffmpeg.call_count, 1)
            self.assertIs(ffmpeg.call_args.kwargs['on_process_created'], callback)
            self.assertTrue(Path(files[0]).is_file())
        for effect in (RuntimeError('conversion failed'), None):
            with self.subTest(effect=effect), patch.object(subtitles, 'run_ffmpeg', side_effect=effect):
                with self.assertRaises(RuntimeError):
                    self.download([track(formats=['ass'])])

    def test_raw_comments_preserve_bytes_and_use_no_converter(self):
        self.ext = 'json'
        self.content = b'{"raw":"chat", "duplicates":[1,1]}\n'
        self.current_language = 'live_chat'
        with patch.object(subtitles, 'run_ffmpeg') as ffmpeg:
            files = subtitles.download_comments(URL, str(self.output),
                track('live_chat', 'comment', ['json']), workspace=str(self.workspace))
        ffmpeg.assert_not_called()
        self.assertEqual(len(files), 1)
        self.assertEqual(Path(files[0]).read_bytes(), self.content)
        self.assertEqual(list(self.workspace.iterdir()), [Path(files[0])])

    def test_xml_danmaku_preserves_raw_file(self):
        self.ext, self.content, self.current_language = 'xml', b'<i><d p="1">hi</d></i>', 'danmaku'
        files = subtitles.download_comments(URL, str(self.output),
            track('danmaku', 'comment', ['xml']), workspace=str(self.workspace))
        self.assertEqual(Path(files[0]).read_bytes(), self.content)

    def test_comment_cannot_enter_subtitle_conversion(self):
        with self.assertRaises(ValueError):
            self.download([track('live_chat', 'comment', ['json'])])
        self.runner.assert_not_called()

    def test_invalid_format_and_duplicate_tracks_fail(self):
        with self.assertRaises(ValueError):
            self.download(formats=['pdf'])
        self.runner.assert_not_called()
        with self.assertRaises(ValueError):
            self.download([track(), track()])
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_direct_download_isolated_and_does_not_overwrite_existing(self):
        self.current_language = 'en'
        first = subtitles.download_subtitles(URL, str(self.output), [track()], ['txt'])
        second = subtitles.download_subtitles(URL, str(self.output), [track()], ['txt'])
        self.assertNotEqual(first, second)
        self.assertEqual(len([path for path in self.output.iterdir() if path.is_file()]), 2)
        self.assertFalse(any(path.name.startswith('._subtitle_') for path in self.output.iterdir()))

    def test_empty_queue_workspace_uses_output_dir(self):
        self.current_language = 'en'
        files = subtitles.download_subtitles(
            URL, str(self.output), [track()], ['txt'], workspace='')
        self.assertEqual(len(files), 1)
        self.assertEqual(Path(files[0]).parent, self.output.resolve())
        self.assertEqual(Path(files[0]).read_text(encoding='utf-8'),
                         'Hello & world\nline two\n\nHello & world\nline two\n')
        self.assertEqual(list(self.workspace.iterdir()), [])
        self.assertFalse(any(path.name.startswith('._subtitle_') for path in self.output.iterdir()))

    def test_cancellation_checkpoints_stop_later_work_and_publication(self):
        stages = ('正在准备字幕下载', '正在下载字幕轨道', '正在解析字幕文本',
                  '正在导出字幕: txt', '正在保存字幕产物')
        for stage in stages:
            with self.subTest(stage=stage):
                self.runner.reset_mock()
                cancellation = RuntimeError('任务已取消')
                def log(message):
                    if message.startswith(stage):
                        raise cancellation
                with patch.object(subtitles, '_read_cues', wraps=subtitles._read_cues) as read, \
                     self.assertRaises(RuntimeError) as raised:
                    self.download(formats=['srt', 'txt', 'vtt'], log=log)
                self.assertIs(raised.exception, cancellation)
                self.assertEqual(list(self.workspace.iterdir()), [])
                self.assertEqual(self.runner.call_count, 0 if stage in stages[:2] else 1)
                self.assertEqual(read.call_count, 0 if stage in stages[:3] else 1)

    def test_cancellation_before_conversion_does_not_launch_ffmpeg(self):
        self.ext, self.content = 'ass', b'ASS input'
        cancellation = RuntimeError('任务已取消')
        def log(message):
            if message.startswith('正在本地转换字幕源格式'):
                raise cancellation
        with patch.object(subtitles, 'run_ffmpeg') as ffmpeg, \
             self.assertRaises(RuntimeError) as raised:
            self.download([track(formats=['ass'])], log=log)
        self.assertIs(raised.exception, cancellation)
        ffmpeg.assert_not_called()
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_cancellation_between_tracks_does_not_start_second_download(self):
        cancellation = RuntimeError('任务已取消')
        def log(message):
            if message.startswith('正在下载字幕轨道 (2/2)'):
                raise cancellation
        with self.assertRaises(RuntimeError) as raised:
            self.download([track(), track(source='automatic')], ['txt'], log=log)
        self.assertIs(raised.exception, cancellation)
        self.assertEqual(self.runner.call_count, 1)
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_cancellation_before_comment_copy_does_not_export(self):
        self.ext, self.content, self.current_language = 'json', b'{"raw":"chat"}', 'live_chat'
        cancellation = RuntimeError('任务已取消')
        def log(message):
            if message.startswith('正在导出原始评论'):
                raise cancellation
        with patch.object(subtitles.shutil, 'copyfile') as copy, \
             self.assertRaises(RuntimeError) as raised:
            subtitles.download_comments(URL, str(self.output),
                track('live_chat', 'comment', ['json']), workspace=str(self.workspace), log=log)
        self.assertIs(raised.exception, cancellation)
        copy.assert_not_called()
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_cancelled_workspace_is_not_recreated(self):
        self.workspace.rmdir()
        with self.assertRaises(FileNotFoundError):
            self.download()
        self.assertFalse(self.workspace.exists())


class DiscoveryTests(unittest.TestCase):
    def test_empty_or_missing_buckets(self):
        for info in ({}, {'subtitles': None, 'automatic_captions': {}}):
            self.assertEqual(subtitles.build_subtitle_tracks(info),
                             {'subtitle_tracks': [], 'comment_tracks': []})

    def test_origin_classification_stable_keys_and_available_formats(self):
        info = {'title': 'Title', 'language': 'ja', 'subtitles': {
            'en': [{'ext': 'vtt', 'url': 'url', 'name': 'English'},
                   {'ext': 'srt', 'data': 'subtitle'}, {'ext': 'vtt', 'url': 'url'},
                   {'ext': 'ttml'}, {'url': 'no extension'}],
            'zh-CN': [{'ext': 'srt', 'data': 'caption', 'name': '中文（AI生成）'}],
            'zh': [{'ext': 'srt', 'url': 'url', 'name': '中文（自动生成）'}],
            'live_chat': [{'ext': 'json', 'url': 'chat'}],
            'danmaku': [{'ext': 'xml', 'url': 'comments'}],
            'empty': [{'ext': 'srt', 'data': ''}],
        }, 'automatic_captions': {
            'en': [{'ext': 'vtt', 'url': 'url?tlang=en', 'name': 'English'}],
            'en-ja': [{'ext': 'vtt', 'url': 'url?tlang=en-ja',
                       'name': 'English from Japanese'}],
            'en-zh-Hans': [{'ext': 'vtt', 'url': 'url?tlang=en-zh-Hans',
                            'name': 'English from Chinese (Simplified)'}],
            'ja-orig': [{'ext': 'vtt', 'url': 'url', 'name': 'Japanese (Original)'}],
            'fr': [{'ext': 'vtt', 'url': 'url', 'name': 'French'}],
        }}
        result = subtitles.build_subtitle_tracks(info)
        self.assertEqual(len(result['subtitle_tracks']), 5)
        self.assertEqual(len(result['comment_tracks']), 2)
        platform, ai, automatic_name, automatic_bucket, original_bucket = \
            result['subtitle_tracks']
        self.assertEqual(platform['source'], 'platform')
        self.assertEqual(platform['name'], 'English')
        self.assertEqual(platform['formats'], ['vtt', 'srt'])
        self.assertEqual(ai['source'], 'automatic')
        self.assertEqual(ai['bucket'], 'subtitles')
        self.assertEqual(automatic_name['source'], 'automatic')
        self.assertEqual(automatic_name['automatic_kind'], 'generated')
        self.assertEqual(automatic_bucket['source'], 'automatic')
        self.assertEqual(automatic_bucket['language'], 'en-ja')
        self.assertEqual(automatic_bucket['automatic_kind'], 'translated')
        self.assertEqual(original_bucket['automatic_kind'], 'generated')
        retained_keys = {track['key'] for track in result['subtitle_tracks']}
        self.assertNotIn('automatic_captions:en', retained_keys)
        self.assertNotIn('automatic_captions:en-zh-Hans', retained_keys)
        self.assertNotIn('automatic_captions:fr', retained_keys)
        self.assertNotEqual(platform['key'], automatic_bucket['key'])
        self.assertTrue(all(item['source'] == 'comment' for item in result['comment_tracks']))
        self.assertEqual(result, subtitles.build_subtitle_tracks(info))

    def test_sniff_expands_automatic_translations_and_updates_result(self):
        info = {'title': 'Test', 'formats': [],
                'automatic_captions': {'en': [{'ext': 'vtt', 'url': 'caption'}]}}
        with patch.object(Functions, '_check_ytdlp'), \
             patch('sookit.core.functions._run_ytdlp_json', return_value=info) as sniff, \
             patch('sookit.core.functions._thumbnails_for', return_value=[]):
            result = Functions.sniff_youtube(URL)
        sniff.assert_called_once_with(URL, '--no-playlist', '--write-auto-subs')
        self.assertEqual(result['subtitle_tracks'][0]['source'], 'automatic')
        self.assertEqual(result['comment_tracks'], [])


class RunnerTests(unittest.TestCase):
    def run_fake(self, code=0, mode=False, manifest=None, logfile=None):
        class Process:
            def __init__(self, cmd, **kwargs):
                self.stdout = io.StringIO(logfile or '')
                self.returncode = code
                self.waited = False
                print_index = cmd.index('--print-to-file')
                self.template = cmd[print_index + 1]
                output = Path(cmd[print_index + 2])
                if manifest is not None:
                    output.write_text(manifest, encoding='utf-8')
                self.cmd = cmd
            def poll(self):
                return self.returncode
            def wait(self, timeout=None):
                self.waited = True
                return self.returncode
        with tempfile.TemporaryDirectory() as temporary:
            callback = []
            kwargs = {'subtitle_manifest': str(Path(temporary) / 'manifest.jsonl')} if mode else {}
            with patch.object(ffmpeg_utils.subprocess, 'Popen', side_effect=Process) as popen:
                result = ffmpeg_utils.run_ytdlp(['yt-dlp', '--skip-download', URL], None,
                                               on_process_created=callback.append, **kwargs)
            return result, popen.call_args.args[0], callback

    def test_subtitle_mode_uses_after_video_and_not_after_move(self):
        result, cmd, callback = self.run_fake(mode=True, manifest='{}\n')
        self.assertEqual(result, [])
        self.assertIn('after_video:%(requested_subtitles)j', cmd)
        self.assertNotIn('after_move:filepath', cmd)
        self.assertEqual(len(callback), 1)

    def test_completed_process_is_waited_and_stdout_closed(self):
        for mode in (False, True):
            with self.subTest(subtitle_mode=mode):
                _, _, callback = self.run_fake(mode=mode)
                self.assertTrue(callback[0].waited)
                self.assertTrue(callback[0].stdout.closed)

    def test_callback_cancellation_reaps_process_and_keeps_original_exception(self):
        for kind in ('created', 'log', 'path', 'interrupt'):
            with self.subTest(callback=kind), tempfile.TemporaryDirectory() as temporary:
                process = Mock()
                process.pid = 12345
                process.returncode = None
                process.stdout = io.StringIO(
                    'ERROR: Sign in to confirm you are not a bot\n'
                    '[download] Destination: source.vtt\n')
                process.poll.side_effect = lambda: process.returncode
                def kill():
                    process.returncode = -9
                process.kill.side_effect = kill
                process.wait.return_value = -9
                cancellation = KeyboardInterrupt() if kind == 'interrupt' else RuntimeError('任务已取消')
                def cancel(*args):
                    raise cancellation
                kwargs = {'subtitle_manifest': str(Path(temporary) / 'manifest.jsonl'),
                          'diagnostics': True}
                if kind in ('created', 'interrupt'):
                    kwargs['on_process_created'] = cancel
                    logger = None
                elif kind == 'log':
                    def logger(message):
                        if message.startswith('[download]'):
                            cancel()
                else:
                    logger = None
                    kwargs['on_path'] = cancel
                with patch.object(ffmpeg_utils.subprocess, 'Popen', return_value=process), \
                     patch.object(ffmpeg_utils.subprocess, 'run') as taskkill, \
                     self.assertRaises(type(cancellation)) as raised:
                    ffmpeg_utils.run_ytdlp(['yt-dlp', URL], logger, **kwargs)
                self.assertIs(raised.exception, cancellation)
                process.kill.assert_called_once_with()
                process.wait.assert_called_once_with(timeout=5)
                self.assertTrue(process.stdout.closed)
                if ffmpeg_utils.sys.platform == 'win32':
                    self.assertEqual(taskkill.call_args.args[0],
                                     ['taskkill', '/PID', '12345', '/T', '/F'])
                else:
                    taskkill.assert_not_called()

    def test_subtitle_nonzero_fails_despite_existing_artifact_and_manifest(self):
        with tempfile.NamedTemporaryFile() as artifact:
            artifact.write(b'nonempty subtitle artifact')
            artifact.flush()
            for code in (1, 7):
                with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, '退出码'):
                    self.run_fake(code=code, mode=True, manifest='{}\n',
                                  logfile='[download] Destination: ' + artifact.name + '\n')

    def test_subtitle_logs_do_not_become_output_files(self):
        with tempfile.NamedTemporaryFile() as artifact:
            result, _, _ = self.run_fake(mode=True,
                logfile='[download] Destination: ' + artifact.name + '\n')
        self.assertEqual(result, [])

    def test_legacy_mode_retains_after_move_and_nonzero_tolerance(self):
        with tempfile.TemporaryDirectory() as temporary:
            artifact = Path(temporary) / 'video.mp4'
            artifact.write_bytes(b'video')
            result, cmd, _ = self.run_fake(code=1, manifest=str(artifact) + '\n')
            self.assertEqual(result, [str(artifact)])
            self.assertIn('after_move:filepath', cmd)
            self.assertNotIn('after_video:%(requested_subtitles)j', cmd)


class ParserTests(unittest.TestCase):
    def test_inline_timestamps_tags_entities_and_repeated_cues(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'test.vtt'
            path.write_text('WEBVTT\n\n00:00.000 --> 00:01.000\n'
                            '<00:00.100><c>Hello</c> &lt;literal&gt;\n\n'
                            '00:01.000 --> 00:02.000\nHello &lt;literal&gt;\n', encoding='utf-8')
            cues = subtitles._read_cues(path)
            self.assertEqual([cue[2] for cue in cues], ['Hello <literal>', 'Hello <literal>'])
            files = subtitles._export(path, 'vtt', ['vtt', 'txt'], Path(temporary) / 'export', None, None)
            self.assertIn('&lt;literal&gt;', files[0].read_text(encoding='utf-8'))
            self.assertEqual(files[1].read_text(encoding='utf-8'),
                             'Hello <literal>\n\nHello <literal>\n')

    def test_youtube_vtt_whitespace_lines_after_timing_stay_with_cue(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'youtube.vtt'
            path.write_text('WEBVTT\nKind: captions\nLanguage: ja\n\n'
                            '00:00:27.519 --> 00:00:57.270 align:start position:0%\n \n'
                            '<00:00:27.720><c>First</c>\n\n'
                            '00:00:57.270 --> 00:00:57.280 align:start position:0%\n \n \n\n'
                            '00:00:57.280 --> 00:00:59.549 align:start position:0%\n \n'
                            '<00:00:57.600><c>Second</c>\n', encoding='utf-8')
            cues = subtitles._read_cues(path)
            self.assertEqual([cue[2] for cue in cues], ['First', 'Second'])

    def test_literal_tags_and_entities_round_trip_in_all_exports(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.vtt'
            source.write_text('WEBVTT\n\n00:00.000 --> 00:01.000\n'
                              '<b>Styled</b> &lt;b&gt;literal&lt;/b&gt; &amp; '
                              '&amp;amp; &lt;00:00.500&gt;\nsecond line\n', encoding='utf-8')
            payload = 'Styled <b>literal</b> & &amp; <00:00.500>\nsecond line'
            files = subtitles._export(source, 'vtt', ['srt', 'txt', 'vtt'],
                                      root / 'export', None, None)
            for output in files:
                with self.subTest(format=output.suffix):
                    text = output.read_text(encoding='utf-8')
                    if output.suffix == '.txt':
                        self.assertEqual(text, payload + '\n')
                    else:
                        self.assertIn('Styled &lt;b&gt;literal&lt;/b&gt; &amp; '
                                      '&amp;amp; &lt;00:00.500&gt;', text)
                        self.assertEqual(subtitles._read_cues(output)[0][2], payload)

    def test_identifiers_sharing_header_prefix_are_not_dropped(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'test.vtt'
            path.write_text('WEBVTT\n\nNOTEworthy\n00:00.000 --> 00:01.000\nFirst\n\n'
                            'STYLE-example\n00:01.000 --> 00:02.000\nSecond\n', encoding='utf-8')
            self.assertEqual([cue[2] for cue in subtitles._read_cues(path)], ['First', 'Second'])

    def test_invalid_time_and_empty_payload_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'test.srt'
            for text in ('1\n00:01:01,000 --> 00:00:00,000\nhi',
                         '1\n00:99:01,000 --> 01:00:00,000\nhi',
                         '1\n00:00:00,000 --> 00:00:01,000\n<b></b>'):
                path.write_text(text, encoding='utf-8')
                with self.subTest(text=text), self.assertRaises(RuntimeError):
                    subtitles._read_cues(path)


if __name__ == '__main__':
    unittest.main()
