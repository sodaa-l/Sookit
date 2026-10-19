# coding:utf-8
"""
core/sniff_options.py
嗅探页「默认视图」选项生成（设计决策 47）。

把 `Functions.sniff_youtube` 产出的格式列表归并成「用户能直接理解并点选」的档位：

- 视频档：按 height 去重，最多 5 档；最高分辨率那一档改标题为「最佳画质」
- 音频档：最佳音质；存在更小的独立音频流时再给一档

每个档位都编译成**具体的 yt-dlp 选择器**（用嗅探到的 format_id 精确拼接），
因此行内副标题（编码/容器/大小估算）与实际下载到的流一致；同时按容器族挑
音轨（webm 视频配 webm/opus、mp4 视频配 m4a/mp4a），默认视图不会落到需要
「mkv 兜底合并」的跨族组合（决策 38 的弹窗只服务于表格视图的手工勾选）。

纯函数、无 Qt 依赖，便于离屏直接断言。
"""
from sookit.core.functions import FormatType
from sookit.core.ffmpeg_utils import format_filesize

MAX_VIDEO_ROWS = 5

# 合并落点判定用编码族表，与决策 38 的 mkv 兜底弹窗共用同一份口径。
# 编码字符串取嗅探结果的 codec 主名（'vp09.00.10.08' → 'vp09'，见
# Functions.sniff_youtube）。av01 双族：YouTube 的 av1 流 ext 有 webm/mp4 两种，
# 配 opus 合 webm、配 mp4a 合 mp4 均为标准组合。
WEBM_VIDEO_CODECS = frozenset({'vp8', 'vp9', 'vp09', 'av01', 'av1'})
WEBM_AUDIO_CODECS = frozenset({'opus', 'vorbis'})
MP4_VIDEO_CODECS = frozenset({'avc1', 'h264', 'h265', 'hev1', 'hevc', 'dvh1', 'dva1',
                              'av01', 'av1'})
MP4_AUDIO_CODECS = frozenset({'mp4a', 'aac', 'm4a'})

# 容器族（编码缺失时按 ext 兜底判定）
_WEBM_AUDIO_EXTS = ('webm',)
_MP4_AUDIO_EXTS = ('m4a', 'mp4')


def _size_text(filesize):
    return f"约 {format_filesize(filesize)}" if filesize else ''


def _join_meta(parts):
    return ' · '.join(p for p in parts if p)


def _pick_audio(formats):
    """最佳独立音频流（按码率降序，缺失归 0）"""
    audio = [f for f in formats if f.get('type') == FormatType.AUDIO_ONLY]
    return max(audio, key=lambda f: (f.get('abr') or 0, f.get('filesize') or 0), default=None)


# MP4_AUDIO_CODECS / WEBM_AUDIO_CODECS 等族表见文件头部。
# 单个编码可落的标准容器集合：av01/av1 双投放（webm 与 mp4 都有），
# 其余编码各归一族。
_CODEC_CONTAINERS = {}
for _c in ('vp8', 'vp9', 'vp09'):
    _CODEC_CONTAINERS[_c] = frozenset({'webm'})
for _c in ('av01', 'av1'):
    _CODEC_CONTAINERS[_c] = frozenset({'webm', 'mp4'})
for _c in ('avc1', 'h264', 'h265', 'hev1', 'hevc', 'dvh1', 'dva1'):
    _CODEC_CONTAINERS[_c] = frozenset({'mp4'})
for _c in ('opus', 'vorbis'):
    _CODEC_CONTAINERS[_c] = frozenset({'webm'})
for _c in ('mp4a', 'aac', 'm4a'):
    _CODEC_CONTAINERS[_c] = frozenset({'mp4'})


def _codec_containers(codec):
    """该编码能落的标准容器集合；未知编码返回空集（调用方据此不猜）"""
    return _CODEC_CONTAINERS.get((codec or '').strip().lower(), frozenset())


def is_standard_pair(vcodec, acodec):
    """视频 + 音频编码是否有共同的标准容器（决策 38 口径，未知编码视为放行）"""
    v = (vcodec or '').strip().lower()
    a = (acodec or '').strip().lower()
    if not v or not a:
        return True
    v_set = _codec_containers(v)
    a_set = _codec_containers(a)
    if not v_set or not a_set:
        return True   # 未知编码不判定，避免误报
    return bool(v_set & a_set)


def _merged_ext(vcodec, acodec, v_ext='', a_ext=''):
    """视频流 + 音频流 ffmpeg 合并后的实际容器。

    按「每个编码能落哪些标准容器，再取交集」判定，与决策 38 的
    `_predict_mkv_merge` 同源：
      - 交集只剩一种 → 该容器（av01 视频配 opus → webm，配 mp4a → mp4）
      - 交集为空 → mkv 兜底（avc1 视频配 opus 这种跨族组合）
      - 编码未知 → 不猜，返回 ''（副标题省略容器，宁可不说也不说错）
    """
    v = (vcodec or '').strip().lower()
    a = (acodec or '').strip().lower()
    if not v or not a:
        # 编码信息缺失时才退回按 ext 推断（成品格式等场景）
        v_ext = (v_ext or '').lower()
        a_ext = (a_ext or '').lower()
        if v_ext == 'webm' and a_ext in _WEBM_AUDIO_EXTS:
            return 'webm'
        if v_ext in ('mp4', 'm4v') and a_ext in _MP4_AUDIO_EXTS:
            return 'mp4'
        return ''
    v_set = _codec_containers(v)
    a_set = _codec_containers(a)
    if not v_set or not a_set:
        return ''   # 未知编码不猜容器
    common = v_set & a_set
    if not common:
        return 'mkv'
    if 'webm' in common and 'mp4' not in common:
        return 'webm'
    if 'mp4' in common and 'webm' not in common:
        return 'mp4'
    # 两种都行时看视频流自身的 ext（av01 的 mp4/webm 两种投放）
    return 'mp4' if (v_ext or '').lower() in ('mp4', 'm4v') else 'webm'


def _ext_family(ext):
    """容器族：webm 一家、mp4/m4a/m4v 一家、其余按自身"""
    ext = (ext or '').lower()
    if ext == 'webm':
        return 'webm'
    if ext in ('mp4', 'm4v', 'm4a'):
        return 'mp4'
    return ext or ''


def _pick_compatible_audio(formats, video_fmt):
    """与视频流合得来的音频流。

    优先级：① 与视频 ext 同容器族（合并即可，不必跨容器重封装）
            ② 与视频编码有共同标准容器（能落 webm/mp4，不落 mkv）
    同类里按码率取最高；都合不来时退回最佳音频（宁可落 mkv 也要有声）。
    """
    audio = [f for f in formats if f.get('type') == FormatType.AUDIO_ONLY]
    if not audio:
        return None
    vcodec = video_fmt.get('vcodec') or ''
    v_family = _ext_family(video_fmt.get('ext'))

    def score(f):
        same_ext = _ext_family(f.get('ext')) == v_family and bool(v_family)
        return (same_ext, is_standard_pair(vcodec, f.get('acodec')),
                f.get('abr') or 0, f.get('filesize') or 0)

    best = max(audio, key=score)
    if score(best)[0] or score(best)[1]:
        return best
    return _pick_audio(formats)


def _pick_video_at_height(formats, height):
    """该高度下最佳视频流；该高度只有成品格式时返回成品格式"""
    videos = [f for f in formats
              if f.get('type') == FormatType.VIDEO_ONLY and (f.get('height') or 0) == height]
    if videos:
        # 同高度多码流（如 1080p30 与 1080p60）：优先高帧率，再优先更大文件
        return max(videos, key=lambda f: (f.get('fps') or 0, f.get('filesize') or 0))
    muxed = [f for f in formats
             if f.get('type') == FormatType.VIDEO_AUDIO and (f.get('height') or 0) == height]
    return max(muxed, key=lambda f: (f.get('fps') or 0, f.get('filesize') or 0), default=None)


def _video_option(formats, height):
    """单个视频档：标题 / 副标题 / 精确选择器"""
    fmt = _pick_video_at_height(formats, height)
    if fmt is None:
        return None
    title = f"{height}p"
    if (fmt.get('fps') or 0) >= 50:
        title += f" {int(round(fmt.get('fps')))}fps"

    if fmt.get('type') == FormatType.VIDEO_AUDIO:
        codec = fmt.get('vcodec') or fmt.get('acodec') or ''
        return {
            'id': f"h{height}",
            'kind': 'video',
            'title': title,
            'subtitle': _join_meta([codec, fmt.get('ext'), _size_text(fmt.get('filesize'))]),
            'spec': fmt['format_id'],
        }

    audio_fmt = _pick_compatible_audio(formats, fmt)
    vcodec = fmt.get('vcodec') or '视频'
    if audio_fmt is None:
        return {
            'id': f"h{height}",
            'kind': 'video',
            'title': title,
            'subtitle': _join_meta([vcodec, fmt.get('ext'), '无音频流',
                                    _size_text(fmt.get('filesize'))]),
            'spec': fmt['format_id'],
        }
    codec = f"{vcodec} + {audio_fmt.get('acodec') or '音频'}"
    size = (fmt.get('filesize') or 0) + (audio_fmt.get('filesize') or 0)
    # 展示合并后的真实容器：同族 → webm/mp4；跨族（该高度没有同族音轨可配）→ mkv
    merged_ext = _merged_ext(fmt.get('vcodec'), audio_fmt.get('acodec'),
                             fmt.get('ext'), audio_fmt.get('ext'))
    return {
        'id': f"h{height}",
        'kind': 'video',
        'title': title,
        'subtitle': _join_meta([codec, merged_ext, _size_text(size)]),
        'spec': f"{fmt['format_id']}+{audio_fmt['format_id']}",
    }


def _audio_option(fmt, title, fallback_subtitle):
    if fmt is None:
        return None
    codec = fmt.get('acodec') or fmt.get('vcodec') or ''
    subtitle = _join_meta([codec, fmt.get('ext'), _size_text(fmt.get('filesize'))])
    return {
        'id': f"a_{fmt['format_id']}",
        'kind': 'audio',
        'title': title,
        'subtitle': subtitle or fallback_subtitle,
        'spec': fmt['format_id'],
    }


def build_view_options(formats):
    """把格式列表归并成默认视图的档位列表。

    返回::

        {'video': [option, ...], 'audio': [option, ...]}

    每个 option::

        {'id': str, 'kind': 'video'|'audio', 'title': str,
         'subtitle': str, 'spec': str}

    video 与 audio 在 UI 侧全局互斥（一次只下一样东西）。某组没有可用格式时
    该组为空列表（纯音频站点没有视频档；只有成品格式的站点没有独立音频档）。
    """
    formats = formats or []

    # ---- 视频档 ----
    # 按 height 去重；同一高度只会有一行（该高度内取最好的流）
    heights = sorted({f.get('height') or 0 for f in formats
                      if f.get('type') in (FormatType.VIDEO_ONLY, FormatType.VIDEO_AUDIO)
                      and (f.get('height') or 0) > 0}, reverse=True)

    video_options = []
    for height in heights[:MAX_VIDEO_ROWS]:
        option = _video_option(formats, height)
        if option:
            video_options.append(option)
    if video_options:
        # 「最佳画质」= 最高档本身，不另用 bestvideo+bestaudio/best 交给 yt-dlp 撞运气：
        # yt-dlp 可能配出跨编码族组合（如 av01-mp4 + opus）→ 只能兜底合 mkv，
        # 而决策 38 的 mkv 风险弹窗只服务表格视图的人工勾选，默认视图不该出现
        best = video_options[0]
        resolution = best['title']
        best['id'] = 'best'
        best['title'] = '最佳画质'
        best['subtitle'] = _join_meta([resolution, best['subtitle']])

    # ---- 音频档 ----
    # 只在确实有独立音频流时给出：只有成品格式的站点上 bestaudio 会退化成整个视频
    # 文件，「仅下载音频」就成了假承诺
    audio_options = []
    best_audio = _pick_audio(formats)
    if best_audio is not None:
        audio_options.append(_audio_option(best_audio, '仅下载音频 · 最佳音质',
                                           '只保留声音，转换为音频文件'))
        smaller = [f for f in formats
                   if f.get('type') == FormatType.AUDIO_ONLY
                   and (f.get('abr') or 0) > 0
                   and (f.get('abr') or 0) < (best_audio.get('abr') or 0) - 2]
        if smaller:
            # 要小文件但别太小：取更小那批里码率最高的一档
            low = max(smaller, key=lambda f: f.get('abr') or 0)
            abr = int(round(low.get('abr') or 0))
            audio_options.append(_audio_option(low, f'仅下载音频 · {abr}kbps',
                                               '只保留声音，文件更小'))

    return {'video': video_options, 'audio': audio_options}
