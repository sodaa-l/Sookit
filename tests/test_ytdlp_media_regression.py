"""真实 yt-dlp 音视频回归；合成素材及 HTTP 服务均限于临时目录。"""
import functools
import hashlib
import http.server
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

from sookit.core.ffmpeg_utils import get_ffmpeg_path
from sookit.core.functions import Functions
from sookit.core.ytdlp_utils import get_ytdlp_cmd


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class MediaRegressionTests(unittest.TestCase):
    def test_real_media_sniff_download_and_final_path_are_preserved(self):
        ffmpeg = get_ffmpeg_path()
        if not Path(ffmpeg).is_file():
            ffmpeg = shutil.which('ffmpeg')
        if not ffmpeg or not get_ytdlp_cmd():
            self.skipTest('需要现有 yt-dlp 和 FFmpeg')
        with tempfile.TemporaryDirectory(prefix='sookit-media-regression-') as temporary:
            root = Path(temporary)
            media = root / 'sample.mp4'
            subprocess.run(
                [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                 '-f', 'lavfi', '-i', 'color=c=red:s=32x32:r=2',
                 '-f', 'lavfi', '-i', 'anullsrc=r=8000:cl=mono', '-t', '1',
                 '-c:v', 'mpeg4', '-c:a', 'aac', str(media)],
                check=True, capture_output=True, timeout=30)
            handler = functools.partial(QuietHandler, directory=str(root))
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f'http://127.0.0.1:{server.server_port}/sample.mp4'
                info = Functions.sniff_youtube(url)
                self.assertTrue(info['formats'])
                self.assertEqual(info['subtitle_tracks'], [])
                self.assertEqual(info['comment_tracks'], [])
                output_dir = root / 'download'
                workspace = root / 'workspace'
                output_dir.mkdir()
                workspace.mkdir()
                processes = []
                outputs = Functions.download_youtube(
                    url, info['formats'][0]['format_id'], str(output_dir), False,
                    use_aria2c=False, workspace=str(workspace),
                    extra_args=['--ignore-config', '--no-playlist', '--socket-timeout', '10'],
                    on_process_created=processes.append)
                self.assertEqual(len(outputs), 1)
                output = Path(outputs[0])
                self.assertEqual(output.parent, workspace)
                self.assertEqual(output.suffix, '.mp4')
                self.assertEqual(hashlib.sha256(output.read_bytes()).digest(),
                                 hashlib.sha256(media.read_bytes()).digest())
                self.assertEqual(len(processes), 1)
                self.assertEqual(processes[0].poll(), 0)
                self.assertTrue(processes[0].stdout.closed)
                self.assertEqual(list(output_dir.iterdir()), [])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
