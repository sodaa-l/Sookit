"""浏览器桥接回归：协议、Qt 本机通道、冷启动和不打断已有嗅探。"""
import io
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import struct
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid

from PyQt6.QtNetwork import QLocalSocket
from PyQt6.QtWidgets import QApplication

from sookit.core.browser_protocol import (
    EXTENSION_ID, EXTENSION_ORIGIN, MAX_MESSAGE_BYTES,
    decode_message, encode_message, normalize_video_url, validate_request,
)
from sookit.core.browser_ipc import BrowserRequestServer, send_request
from sookit import native_host

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://www.youtube.com/watch?v=Yb9H5vhWyHg'
REQUEST = {'action': 'sniff', 'url': URL}


class ProtocolTests(unittest.TestCase):
    def test_normalizes_video_links_without_playlist_or_time(self):
        for url in (URL + '&list=PLtest&t=12', 'https://youtu.be/Yb9H5vhWyHg',
                    'https://m.youtube.com/shorts/Yb9H5vhWyHg',
                    'https://www.youtube.com/live/Yb9H5vhWyHg'):
            with self.subTest(url=url):
                self.assertEqual(normalize_video_url(url), URL)

    def test_rejects_non_video_and_untrusted_urls(self):
        for url in (None, '', 'file:///C:/Windows', 'http://www.youtube.com/watch?v=Yb9H5vhWyHg',
                    'https://youtube.com.evil.test/watch?v=Yb9H5vhWyHg',
                    'https://youtube.com@evil.test/watch?v=Yb9H5vhWyHg',
                    'https://www.youtube.com:444/watch?v=Yb9H5vhWyHg',
                    'https://www.youtube.com/playlist?list=PLtest', URL + '&v=aaaaaaaaaaa',
                    'https://www.youtube.com/watch?v=--exec', 'x' * 4097):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    normalize_video_url(url)

    def test_only_sniff_action_and_url_forwarded(self):
        self.assertEqual(validate_request(dict(REQUEST, command='evil')), REQUEST)
        for value in ([], {}, {'action': 'exec', 'url': URL}):
            with self.assertRaises(ValueError):
                validate_request(value)

    def test_utf8_frames_and_invalid_lengths(self):
        value = {'ok': False, 'error': '正在嗅探，请稍后'}
        frame = encode_message(value)
        self.assertEqual(struct.unpack('<I', frame[:4])[0], len(frame) - 4)
        self.assertEqual(decode_message(frame), value)
        for frame in (b'', struct.pack('<I', 0), struct.pack('<I', MAX_MESSAGE_BYTES + 1),
                      encode_message(REQUEST) + b'extra', struct.pack('<I', 2) + b'[]'):
            with self.assertRaises(ValueError):
                decode_message(frame)

    def test_native_stream_reads_partial_chunks_and_eof(self):
        class Fragmented(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 3))
        self.assertEqual(native_host.read_message(Fragmented(encode_message(REQUEST))), REQUEST)
        with self.assertRaises(ValueError):
            native_host.read_message(io.BytesIO(encode_message(REQUEST)[:-1]))
        with self.assertRaises(ValueError):
            native_host.read_message(io.BytesIO(struct.pack('<I', MAX_MESSAGE_BYTES + 1)))

    def test_manifest_identity_and_packaged_origin_match(self):
        import base64
        import hashlib
        manifest = json.loads((ROOT / 'browser-extension/manifest.json').read_text(encoding='utf-8'))
        digest = hashlib.sha256(base64.b64decode(manifest['key'], validate=True)).digest()[:16]
        actual = ''.join(chr(97 + (byte >> 4)) + chr(97 + (byte & 15)) for byte in digest)
        self.assertEqual(actual, EXTENSION_ID)
        native = json.loads((ROOT / 'packaging/com.sookit.browser.json').read_text(encoding='utf-8'))
        self.assertEqual(native['allowed_origins'], [EXTENSION_ORIGIN])
        self.assertEqual(manifest['permissions'], ['nativeMessaging'])
        for icon in manifest['icons'].values():
            self.assertTrue((ROOT / 'browser-extension' / icon).is_file())

    def test_native_host_rejects_unapproved_extension_without_qt_or_gui_stdout(self):
        process = subprocess.run([sys.executable, '-m', 'sookit.native_host',
                                  'chrome-extension://wrong/'], input=encode_message(REQUEST),
                                 capture_output=True, timeout=10, cwd=str(ROOT))
        self.assertEqual(process.returncode, 0, process.stderr)
        response = decode_message(process.stdout)
        self.assertFalse(response['ok'])
        self.assertIn('仅允许', response['error'])


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_existing_instance_does_not_start_another_process(self):
        with patch('sookit.core.browser_ipc.send_request', return_value={'ok': True}) as send, \
                patch.object(native_host.subprocess, 'Popen') as launch:
            self.assertEqual(native_host.forward_request(REQUEST), {'ok': True})
            send.assert_called_once_with(REQUEST)
            launch.assert_not_called()

    def test_cold_start_waits_for_ready_and_detaches_stdio(self):
        with patch('sookit.core.browser_ipc.send_request', side_effect=[None, None, {'ok': True}]), \
                patch.object(native_host, 'app_command', return_value=(['Sookit.exe', '--silent'], ROOT)), \
                patch.object(native_host.subprocess, 'Popen') as launch, \
                patch.object(native_host.time, 'sleep'):
            self.assertTrue(native_host.forward_request(REQUEST)['ok'])
            launch.assert_called_once()
            self.assertEqual(launch.call_args.args[0], ['Sookit.exe', '--silent'])
            self.assertEqual(launch.call_args.kwargs['stdin'], subprocess.DEVNULL)
            self.assertEqual(launch.call_args.kwargs['stdout'], subprocess.DEVNULL)
            self.assertNotIn('shell', launch.call_args.kwargs)

    def test_connected_timeout_is_not_retried_or_started_twice(self):
        with patch('sookit.core.browser_ipc.send_request', side_effect=TimeoutError('未确认')), \
                patch.object(native_host.subprocess, 'Popen') as launch:
            with self.assertRaises(TimeoutError):
                native_host.forward_request(REQUEST)
            launch.assert_not_called()

    def test_busy_response_is_preserved_without_restarting(self):
        response = {'ok': False, 'error': '忙'}
        with patch('sookit.core.browser_ipc.send_request', return_value=response), \
                patch.object(native_host.subprocess, 'Popen') as launch:
            self.assertEqual(native_host.forward_request(REQUEST), response)
            launch.assert_not_called()


class LocalChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.name = 'Sookit.Test.' + uuid.uuid4().hex
        self.handler = MagicMock(return_value={'ok': True, 'message': '后台嗅探已启动'})
        self.server = BrowserRequestServer(self.handler, name=self.name)
        self.assertTrue(self.server.listen())
        self.addCleanup(self.server.close)

    def settle_until(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.002)
        self.assertTrue(predicate(), '本机通道没有在时限内返回')

    def connect(self):
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        self.assertTrue(socket.waitForConnected(1000))
        self.addCleanup(socket.abort)
        self.app.processEvents()
        return socket

    def response(self, socket):
        self.settle_until(lambda: socket.bytesAvailable() >= 4)
        frame = bytearray(bytes(socket.readAll()))
        size = struct.unpack('<I', frame[:4])[0]
        deadline = time.monotonic() + 3
        while len(frame) < size + 4 and time.monotonic() < deadline:
            self.app.processEvents()
            frame.extend(bytes(socket.readAll()))
        return decode_message(bytes(frame))

    def test_fragmented_request_is_dispatched_once(self):
        socket = self.connect()
        frame = encode_message(REQUEST)
        socket.write(frame[:2])
        socket.flush()
        self.app.processEvents()
        self.handler.assert_not_called()
        socket.write(frame[2:])
        socket.flush()
        self.assertTrue(self.response(socket)['ok'])
        self.handler.assert_called_once_with(REQUEST)

    def test_bad_size_or_url_never_reaches_ui(self):
        for frame in (struct.pack('<I', MAX_MESSAGE_BYTES + 1),
                      encode_message({'action': 'sniff', 'url': 'https://evil.test/'}),
                      struct.pack('<I', 1) + b'{'):
            socket = self.connect()
            socket.write(frame)
            socket.flush()
            self.assertFalse(self.response(socket)['ok'])
        self.handler.assert_not_called()

    def test_client_process_roundtrip(self):
        code = ('from PyQt6.QtCore import QCoreApplication; '
                'from sookit.core.browser_ipc import send_request; '
                'import json,sys; app=QCoreApplication([]); '
                'print(json.dumps(send_request(json.loads(sys.argv[2]), name=sys.argv[1])))')
        process = subprocess.Popen([sys.executable, '-c', code, self.name, json.dumps(REQUEST)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(ROOT))
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        self.settle_until(lambda: process.poll() is not None, 5)
        stdout, stderr = process.communicate(timeout=2)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertTrue(json.loads(stdout)['ok'])
        self.handler.assert_called_once_with(REQUEST)

    def test_close_drops_incomplete_connections_and_releases_name(self):
        socket = self.connect()
        socket.write(b'\x05')
        socket.flush()
        self.app.processEvents()
        self.server.close()
        self.settle_until(lambda: socket.state() == QLocalSocket.LocalSocketState.UnconnectedState)
        replacement = BrowserRequestServer(self.handler, name=self.name)
        self.assertTrue(replacement.listen())
        replacement.close()
        self.handler.assert_not_called()

    def test_absent_instance_returns_none(self):
        self.assertIsNone(send_request(REQUEST, name=self.name + '.missing', timeout_ms=50))


class PageDispatchTests(unittest.TestCase):
    def page(self, running=False, pending=False):
        return SimpleNamespace(_sniff_timer=SimpleNamespace(isActive=lambda: pending),
                               _sniff_worker=SimpleNamespace(isRunning=lambda: running),
                               url_input=MagicMock(), do_sniff=MagicMock())

    def dispatch(self, page, url=URL, available=True):
        from sookit.pages.youtube_page import YouTubePage
        with patch('sookit.pages.youtube_page.is_ytdlp_available', return_value=available):
            return YouTubePage.sniff_from_browser(page, url)

    def test_busy_or_pending_callback_preserves_existing_state(self):
        for running, pending in ((True, False), (False, True)):
            page = self.page(running, pending)
            self.assertFalse(self.dispatch(page)['ok'])
            page.url_input.setText.assert_not_called()
            page.do_sniff.assert_not_called()

    def test_missing_dependency_is_reported_before_changing_page(self):
        page = self.page()
        self.assertFalse(self.dispatch(page, available=False)['ok'])
        page.url_input.setText.assert_not_called()
        page.do_sniff.assert_not_called()

    def test_idle_request_uses_existing_sniff_without_showing_window(self):
        page = self.page()
        self.assertTrue(self.dispatch(page)['ok'])
        page.url_input.setText.assert_called_once_with(URL)
        page.do_sniff.assert_called_once_with()

    def test_main_window_dispatch_only_navigates_on_accept(self):
        from sookit.main_window import MainWindow
        window = SimpleNamespace(youtube_page=SimpleNamespace(sniff_from_browser=MagicMock()),
                                 switchTo=MagicMock())
        window.youtube_page.sniff_from_browser.return_value = {'ok': False}
        self.assertFalse(MainWindow.handle_browser_request(window, REQUEST)['ok'])
        window.switchTo.assert_not_called()
        window.youtube_page.sniff_from_browser.return_value = {'ok': True}
        self.assertTrue(MainWindow.handle_browser_request(window, REQUEST)['ok'])
        window.switchTo.assert_called_once_with(window.youtube_page)
        window._quitting = True
        self.assertFalse(MainWindow.handle_browser_request(window, REQUEST)['ok'])


if __name__ == '__main__':
    unittest.main()
