"""浏览器桥接的消息约定与链接白名单（不依赖 Qt）。"""
import json
import re
import struct
from urllib.parse import parse_qs, urlsplit

HOST_NAME = 'com.sookit.browser'
EXTENSION_ID = 'inplhidhdgncdjkkmbfcocedkabbaghn'
EXTENSION_ORIGIN = f'chrome-extension://{EXTENSION_ID}/'
MAX_MESSAGE_BYTES = 16 * 1024
BROWSER_ACTIONS = frozenset({'sniff', 'download_best', 'download_cover'})


def normalize_video_url(value: str) -> str:
    """仅接受 YouTube 单视频链接，剥离播放列表、时间等附带参数。"""
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError('视频链接无效')
    try:
        parts = urlsplit(value)
        if (parts.scheme != 'https' or parts.username or parts.password
                or parts.port not in (None, 443)):
            raise ValueError('仅支持 HTTPS YouTube 视频链接')
        host = (parts.hostname or '').lower()
        if host == 'youtu.be':
            video_id = parts.path.removeprefix('/')
        elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
            if parts.path == '/watch':
                ids = parse_qs(parts.query).get('v', [])
                video_id = ids[0] if len(ids) == 1 else ''
            elif parts.path.startswith(('/shorts/', '/live/')):
                video_id = parts.path.split('/')[2]
            else:
                video_id = ''
        else:
            raise ValueError('仅支持 YouTube 视频链接')
    except (ValueError, TypeError) as exc:
        raise ValueError('仅支持 HTTPS YouTube 视频链接') from exc
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise ValueError('没有找到有效的 YouTube 视频 ID')
    return f'https://www.youtube.com/watch?v={video_id}'


def validate_request(message: object) -> dict:
    if (not isinstance(message, dict) or not isinstance(message.get('action'), str)
            or message['action'] not in BROWSER_ACTIONS):
        raise ValueError('不支持的浏览器请求')
    return {'action': message['action'], 'url': normalize_video_url(message.get('url'))}


def encode_message(message: dict) -> bytes:
    payload = json.dumps(message, ensure_ascii=False).encode('utf-8')
    if not 0 < len(payload) <= MAX_MESSAGE_BYTES:
        raise ValueError('消息过长')
    return struct.pack('<I', len(payload)) + payload


def decode_message(frame: bytes) -> dict:
    if len(frame) < 4:
        raise ValueError('消息头不完整')
    size = struct.unpack('<I', frame[:4])[0]
    if not 0 < size <= MAX_MESSAGE_BYTES or len(frame) != size + 4:
        raise ValueError('消息长度无效')
    value = json.loads(frame[4:].decode('utf-8'))
    if not isinstance(value, dict):
        raise ValueError('消息必须是对象')
    return value
