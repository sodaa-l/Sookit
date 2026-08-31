"""
core/youtube_utils.py
YouTube 视频 ID 提取、缩略图构建、元数据获取
"""

import json
import re
import time
import urllib.request

from sookit.core.utils import get_certifi_ssl_context


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


def normalize_thumbnails(info):
    """从 yt-dlp info dict 提取通用封面列表（任意站点）。

    输出结构与 build_thumbnails 一致：[{'id', 'width', 'height', 'url'}]，页面层无感知。
    分站点策略（实测依据见各分支注释）：

    - twitter/X：仅输出 medium 单档并保留其标注宽高，id 置空（页面文案只显示
      分辨率，如 "1200x675"）。实测（2026-09-01，5 条视频 25 档）：X 的
      width/height 是按惯例推算的预测值，仅 medium 档标注与实际一致（5/5），
      thumb/small 系统性偏小、large/orig 系统性虚标（宣称 3840x2160 实际
      1200x675）；且同视频各档 URL 下载结果完全相同（X 忽略 ?name= 参数）。
      无 medium 档时回退通用逻辑。

    - 通用路径：完整 URL 去重（不做 base 归并——X 等站点的尺寸档靠 query
      区分，base 归并会毁掉档位；YouTube 的 ?sqp= 变体场景已走硬编码表，
      不进此函数）；preference 降序 stable sort（None 视为最低）；以顶层
      `thumbnail` 字段（extractor 认定的最佳封面 URL）定位最佳项移到首位，
      不在列表中则插入首位；丢弃 width/height（通用路径无可靠宽高来源，
      页面文案自动落入「最佳封面 / 备选封面 N」分支）。
    """
    extractor = (info.get('extractor') or '').lower()

    # ---- twitter/X：medium 单档特判 ----
    if extractor.startswith('twitter'):
        medium = next(
            (t for t in (info.get('thumbnails') or [])
             if t.get('id') == 'medium' and t.get('url')),
            None)
        if medium is not None:
            return [{
                'id': '',
                'width': medium.get('width'),
                'height': medium.get('height'),
                'url': medium['url'],
            }]

    # ---- 通用路径 ----
    def _pref(t):
        p = t.get('preference')
        return p if isinstance(p, (int, float)) else float('-inf')

    # 完整 URL 去重（保序）
    deduped = []
    seen = set()
    for t in (info.get('thumbnails') or []):
        u = t.get('url')
        if not u or u in seen:
            continue
        seen.add(u)
        deduped.append(t)

    # preference 降序（stable sort，None 最低）
    deduped.sort(key=_pref, reverse=True)

    # 以 thumbnail 字段定位最佳项：在列表中则移到首位（UI 约定 thumbs[0] 为
    # 最佳），不在列表中则构造新项插入首位
    best_url = info.get('thumbnail')
    if best_url:
        idx = next((i for i, t in enumerate(deduped) if t['url'] == best_url), -1)
        if idx > 0:
            deduped.insert(0, deduped.pop(idx))
        elif idx < 0:
            deduped.insert(0, {'url': best_url})

    # 丢弃 width/height（页面文案自动落入「最佳封面 / 备选封面 N」分支）
    return [
        {
            'id': t.get('id') or '',
            'width': None,
            'height': None,
            'url': t['url'],
        }
        for t in deduped
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
        with urllib.request.urlopen(req, timeout=15, context=get_certifi_ssl_context()) as resp:
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
