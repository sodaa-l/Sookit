"""Chrome Native Messaging 桥接入口，stdout 只写长度前缀 JSON。"""
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

from sookit.core.browser_protocol import (
    EXTENSION_ORIGIN, MAX_MESSAGE_BYTES, decode_message, encode_message, validate_request,
)


def _read_exact(stream, size):
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            raise ValueError('Chrome 消息不完整')
        chunks.extend(chunk)
    return bytes(chunks)


def read_message(stream):
    header = _read_exact(stream, 4)
    size = struct.unpack('<I', header)[0]
    if not 0 < size <= MAX_MESSAGE_BYTES:
        raise ValueError('Chrome 消息长度无效')
    return decode_message(header + _read_exact(stream, size))


def app_command():
    if getattr(sys, 'frozen', False):
        executable = Path(sys.executable).resolve().with_name('Sookit.exe')
        source_config = executable.with_name('source-launch.json')
        if source_config.is_file():
            import json
            config = json.loads(source_config.read_text(encoding='utf-8'))
            command, cwd = config['command'], Path(config['cwd'])
            if (not isinstance(command, list) or not command
                    or not all(isinstance(arg, str) for arg in command)
                    or not Path(command[0]).is_file() or not cwd.is_dir()):
                raise ValueError('源码启动配置无效，请重新注册浏览器桥接')
            return command, cwd
        if not executable.is_file():
            raise FileNotFoundError('未找到 Sookit.exe，请重新安装 Sookit')
        return [str(executable), '--silent'], executable.parent
    return [sys.executable, '-m', 'sookit', '--silent'], Path(__file__).resolve().parents[2]


def forward_request(request):
    from PyQt6.QtCore import QCoreApplication
    from sookit.core.browser_ipc import send_request
    app = QCoreApplication.instance() or QCoreApplication([])
    response = send_request(request)
    if response is not None:
        return response

    # 冷启动路径必须在启动 GUI 前读取持久设置；读取异常也失败关闭。
    from sookit.core.config import load_browser_extension_enabled
    try:
        if not load_browser_extension_enabled():
            return {'ok': False, 'error': '浏览器扩展调用已暂停'}
    except Exception as exc:
        return {'ok': False, 'error': f'无法确认浏览器扩展开关：{exc}'}

    command, cwd = app_command()
    # 独立于 Chrome host 存活；stdin/stdout 不继承 Native Messaging 管道。
    options = {}
    if os.name == 'nt':
        options['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(command, cwd=str(cwd), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **options)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        response = send_request(request, timeout_ms=1000)
        if response is not None:
            return response
        time.sleep(0.15)
    raise TimeoutError('Sookit 启动超时，请先手动启动后重试')


def main():
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    try:
        if len(sys.argv) < 2 or sys.argv[1] != EXTENSION_ORIGIN:
            raise ValueError('仅允许 Sookit Chrome 扩展连接')
        request = validate_request(read_message(sys.stdin.buffer))
        response = forward_request(request)
    except Exception as exc:
        print(f'Sookit browser bridge: {exc}', file=sys.stderr)
        response = {'ok': False, 'error': str(exc) or 'Sookit 本机通信失败'}
    sys.stdout.buffer.write(encode_message(response))
    sys.stdout.buffer.flush()


if __name__ == '__main__':
    main()
