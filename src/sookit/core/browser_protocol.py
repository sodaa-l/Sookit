"""浏览器桥接的消息约定与链接白名单（不依赖 Qt）。"""
import json
import re
import struct
from urllib.parse import parse_qs, urlsplit

HOST_NAME = 'com.sookit.browser'
EXTENSION_ID = 'inplhidhdgncdjkkmbfcocedkabbaghn'
EXTENSION_ORIGIN = f'chrome-extension://{EXTENSION_ID}/'
MAX_MESSAGE_BYTES = 16 * 1024
MAX_VIDEO_TITLE_LENGTH = 500
MAX_VIDEO_CHANNEL_LENGTH = 200
MAX_VIDEO_DURATION_SECONDS = 7 * 24 * 60 * 60
MAX_VIDEO_COVER_URL_LENGTH = 2048
BROWSER_ACTIONS = frozenset({'sniff', 'download_best', 'download_cover', 'parse_gif'})
# 控制类动作不携带网页链接：launch 只给 Chrome host 用，status 是本机通道唯一放行的
# 控制动作（扩展面板的连接探测），两者都不进入页面分派逻辑。
CONTROL_ACTIONS = frozenset({'status', 'launch'})
LOCAL_CONTROL_ACTIONS = frozenset({'status'})


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


def normalize_x_status_url(value: str) -> str:
    """仅接受 HTTPS X/Twitter 状态链接，并去掉媒体序号。"""
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError('状态链接无效')
    try:
        parts = urlsplit(value)
        host = (parts.hostname or '').lower()
        if (parts.scheme != 'https' or host not in {
                'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com'}
                or parts.username or parts.password or parts.port not in (None, 443)):
            raise ValueError('仅支持 HTTPS X 状态链接')
        if not re.fullmatch(
                r'/(?:[^/]+/status/|i/web/status/)\d+(?:/(?:video|photo)/\d+)?/?',
                parts.path):
            raise ValueError('没有找到有效的 X 状态链接')
    except (ValueError, TypeError) as exc:
        raise ValueError('仅支持 HTTPS X 状态链接') from exc
    path = re.sub(r'/(?:video|photo)/\d+/?$', '', parts.path).rstrip('/')
    return f'https://x.com{path}'


def normalize_browser_url(value: str, action: str) -> str:
    """按浏览器动作选择 YouTube 或 X 单帖 URL 边界。"""
    if action == 'parse_gif':
        return normalize_x_status_url(value)
    if action == 'download_cover':
        return normalize_video_url(value)
    if action not in ('sniff', 'download_best'):
        raise ValueError('不支持的浏览器请求')
    try:
        return normalize_video_url(value)
    except ValueError as youtube_error:
        try:
            return normalize_x_status_url(value)
        except ValueError:
            raise youtube_error


def _clean_display_text(value: object, max_length: int) -> str:
    if not isinstance(value, str):
        return ''
    value = re.sub(r'[\x00-\x1f\x7f]', ' ', value)
    return ' '.join(value.split())[:max_length]


def _validated_video_metadata(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    metadata = {}
    title = _clean_display_text(value.get('title'), MAX_VIDEO_TITLE_LENGTH)
    channel = _clean_display_text(value.get('channel'), MAX_VIDEO_CHANNEL_LENGTH)
    if title:
        metadata['title'] = title
    if channel:
        metadata['channel'] = channel
    duration = value.get('duration')
    if type(duration) is int and 0 < duration <= MAX_VIDEO_DURATION_SECONDS:
        metadata['duration'] = duration
    cover_url = value.get('cover_url')
    if isinstance(cover_url, str) and len(cover_url) <= MAX_VIDEO_COVER_URL_LENGTH:
        try:
            parts = urlsplit(cover_url)
            if (parts.scheme == 'https' and parts.hostname
                    and parts.hostname.lower() == 'pbs.twimg.com'
                    and not parts.username and not parts.password
                    and parts.port in (None, 443) and parts.path.startswith('/')):
                metadata['cover_url'] = cover_url
        except ValueError:
            pass
    return metadata


def validate_request(message: object, *, allowed=BROWSER_ACTIONS) -> dict:
    if (not isinstance(message, dict) or not isinstance(message.get('action'), str)
            or message['action'] not in allowed):
        raise ValueError('不支持的浏览器请求')
    action = message['action']
    if action in CONTROL_ACTIONS:
        return {'action': action}
    request = {'action': action,
               'url': normalize_browser_url(message.get('url'), action)}
    if request['action'] in ('download_best', 'download_cover'):
        metadata = _validated_video_metadata(message.get('video_metadata'))
        if metadata:
            request['video_metadata'] = metadata
    return request


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
