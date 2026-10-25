"""只对当前 Windows 用户和会话开放的本机请求通道。"""
import hashlib
import logging
import os
import struct
from time import monotonic

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from sookit.core.browser_protocol import (
    MAX_MESSAGE_BYTES, decode_message, encode_message, validate_request,
)

logger = logging.getLogger(__name__)


def server_name() -> str:
    import getpass
    session = 0
    if os.name == 'nt':
        import ctypes
        value = ctypes.c_ulong()
        if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(value)):
            raise OSError('无法确定当前 Windows 会话')
        session = value.value
    user = hashlib.sha256(getpass.getuser().encode('utf-8')).hexdigest()[:16]
    return f'Sookit.Browser.{user}.{session}'


class BrowserRequestServer(QObject):
    """异步收帧；handler 始终在 Qt 主线程执行，每连接只受理一条请求。"""
    def __init__(self, handler, parent=None, name=None):
        super().__init__(parent)
        self._handler = handler
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept)
        self._connections = {}
        self.name = name or server_name()

    def listen(self) -> bool:
        return self._server.listen(self.name)

    def close(self):
        self._server.close()
        for socket in list(self._connections):
            socket.abort()
            self._forget(socket)

    def _accept(self):
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            timer = QTimer(socket)
            timer.setSingleShot(True)
            timer.timeout.connect(socket.abort)
            self._connections[socket] = bytearray()
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._forget(s))
            timer.start(5000)
            self._read(socket)

    def _forget(self, socket):
        self._connections.pop(socket, None)
        socket.deleteLater()

    def _read(self, socket):
        buffer = self._connections.get(socket)
        if buffer is None:
            return
        buffer.extend(bytes(socket.readAll()))
        if len(buffer) > MAX_MESSAGE_BYTES + 4:
            self._reply(socket, {'ok': False, 'error': '消息过长'})
            return
        if len(buffer) < 4:
            return
        size = struct.unpack('<I', buffer[:4])[0]
        if not 0 < size <= MAX_MESSAGE_BYTES:
            self._reply(socket, {'ok': False, 'error': '消息长度无效'})
            return
        if len(buffer) < size + 4:
            return
        try:
            request = validate_request(decode_message(bytes(buffer)))
            response = self._handler(request)
        except (ValueError, UnicodeError) as exc:
            response = {'ok': False, 'error': str(exc)}
        except Exception:
            logger.exception('浏览器请求处理失败')
            response = {'ok': False, 'error': 'Sookit 处理请求失败，请查看日志'}
        self._reply(socket, response)

    def _reply(self, socket, response):
        self._connections.pop(socket, None)
        socket.write(encode_message(response))
        socket.disconnectFromServer()


def send_request(request: dict, *, name=None, timeout_ms=1500):
    """桥接进程使用；None 表示尚未监听，已连接的错误不重发。"""
    socket = QLocalSocket()
    socket.connectToServer(name or server_name())
    if not socket.waitForConnected(timeout_ms):
        socket.abort()
        return None
    try:
        socket.write(encode_message(request))
        if socket.bytesToWrite() and not socket.waitForBytesWritten(timeout_ms):
            raise TimeoutError('无法向 Sookit 发送链接')
        frame = bytearray()
        deadline = monotonic() + timeout_ms / 1000
        while True:
            frame.extend(bytes(socket.readAll()))
            if len(frame) > MAX_MESSAGE_BYTES + 4:
                raise ValueError('Sookit 响应过长')
            if len(frame) >= 4:
                size = struct.unpack('<I', frame[:4])[0]
                if not 0 < size <= MAX_MESSAGE_BYTES:
                    raise ValueError('Sookit 响应长度无效')
                if len(frame) >= size + 4:
                    return decode_message(bytes(frame))
            remaining = int((deadline - monotonic()) * 1000)
            if remaining <= 0 or not socket.waitForReadyRead(remaining):
                if socket.bytesAvailable():
                    continue
                raise TimeoutError('Sookit 未确认接收链接，请稍后重试')
    finally:
        socket.abort()
