"""
core/youtube_utils.py
YouTube 视频 ID 提取、缩略图构建、元数据获取
"""

import json
import re
import time
import urllib.request


# ---------- YouTube 视频 ID 提取 ----------

def extract_youtube_id(url):
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    hostname = (parsed.hostname or '').lower()
    path = parsed.path

    if '/watch' in path and hostname in ('www.youtube.com', 'm.youtube.com', 'youtube.com'):
        qs = parse_qs(parsed.query)
        if 'v' in qs:
            return qs['v'][0]

    if 'youtu.be' in hostname:
        return path.lstrip('/').split('/')[0]

    for prefix in ('/live/', '/embed/', '/shorts/', '/v/'):
        if prefix in path:
            return path.split(prefix)[1].split('/')[0]

    if hostname in ('www.youtube.com', 'm.youtube.com', 'youtube.com'):
        parts = path.strip('/').split('/')
        if parts and parts[0] not in ('watch', ''):
            return parts[-1]

    return None


# ---------- YouTube 缩略图 ----------

YOUTUBE_THUMBNAILS = [
    {'id': 'maxresdefault', 'width': 1280, 'height': 720},
    {'id': 'sddefault',     'width': 640,  'height': 480},
    {'id': 'hqdefault',     'width': 480,  'height': 360},
    {'id': 'mqdefault',     'width': 320,  'height': 180},
    {'id': 'default',       'width': 120,  'height': 90},
]


def build_thumbnails(video_id):
    if not video_id:
        return []
    return [
        {**t, 'url': f'https://i.ytimg.com/vi/{video_id}/{t["id"]}.jpg'}
        for t in YOUTUBE_THUMBNAILS
    ]


# ---------- YouTube 元数据获取 ----------

def _parse_yt_initial_data(html):
    m = None
    for pattern in (r'ytInitialPlayerResponse\s*=\s*({.*?});', r'var ytInitialPlayerResponse\s*=\s*({.*?});'):
        m = re.search(pattern, html, re.DOTALL)
        if m:
            break
    if not m:
        return None
    return json.loads(m.group(1))


def fetch_youtube_metadata(video_id):
    url = f"https://www.youtube.com/watch?v={video_id}"
    req = urllib.request.Request(
        url,
        headers={'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/120.0.0.0 Safari/537.36')})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode('utf-8', errors='replace')
    except Exception:
        return None

    player = _parse_yt_initial_data(html)
    if not player:
        return None

    vd = player.get('videoDetails') or {}
    microformat = player.get('microformat') or {}
    mfr = microformat.get('playerMicroformatRenderer') or {}
    live_details = mfr.get('liveBroadcastDetails') or {}

    if live_details:
        if live_details.get('isLiveNow', False):
            live_status = 'is_live'
        else:
            start_time = live_details.get('startTimestamp', '')
            if start_time:
                try:
                    from datetime import datetime
                    st = datetime.fromisoformat(start_time.replace('Z', '+00:00')).timestamp()
                    live_status = 'was_live' if st < time.time() else 'is_upcoming'
                except Exception:
                    live_status = 'is_upcoming'
            else:
                live_status = 'is_upcoming'
    else:
        live_status = 'not_live'

    scheduled_ts = None
    start_time = live_details.get('startTimestamp', '')
    if start_time:
        try:
            from datetime import datetime
            scheduled_ts = int(datetime.fromisoformat(start_time.replace('Z', '+00:00')).timestamp())
        except Exception:
            pass

    return {
        'title': vd.get('title', ''),
        'live_status': live_status,
        'scheduled_start_time': scheduled_ts,
        'is_live': live_details.get('isLiveNow', False),
    }
