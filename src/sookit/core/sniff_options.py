# coding:utf-8
"""
core/sniff_options.py
嗅探页「默认视图」选项生成（设计决策 47）。

把 `Functions.sniff_youtube` 产出的格式列表归并成「用户能直接理解并点选」的档位：

- 视频档：按 height 去重，最多 5 档；档位名只标分辨率与帧率
- 音频档：每个独立音轨一档，同码率的 drc/非 drc 变体归并，档位名就是码率

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


def _audio_tracks(formats):
    """独立音轨去重后按码率降序返回（默认视图的音频档来源）。

    YouTube 同一码率常有多个变体：`140` 与 `140-drc`、`251` 与 `251-drc`、
    甚至 mp4a/opus 双编码同码率。逐条平铺会把列表塞满重复项，所以按
    「码率取整到 kbps」归并，同码率里优先非 drc（动态范围压缩版音质更差）、
    再优先更大的文件。

    这里**不做码率阈值筛选**：曾按「最高码率 - 2」筛第二档，导致 129.5kbps
    的 mp4a 因为高于阈值被整批丢掉、第二档回落到 65kbps（用户实际遇到的
    问题）。音频档本来就少，全列出更好选。
    """
    tracks = [f for f in formats if f.get('type') == FormatType.AUDIO_ONLY]
    groups = {}
    for fmt in tracks:
        abr = fmt.get('abr') or 0
        if abr <= 0:
            # 码率未知的独立音轨（少见）：单独成档，别丢弃
            groups[('unk', fmt.get('format_id'))] = [fmt]
            continue
        groups.setdefault(int(round(abr)), []).append(fmt)

    picked = []
    for key, group in groups.items():
        if isinstance(key, tuple):        # 码率未知组
            picked.extend(group)
            continue
        best = max(group, key=lambda f: ('drc' not in str(f.get('format_id') or ''),
                                         f.get('filesize') or 0))
        picked.append(best)
    picked.sort(key=lambda f: (f.get('abr') or 0, f.get('filesize') or 0), reverse=True)
    return picked


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
    # 视频档一律标分辨率（最高档也不叫「最佳画质」）：档位名只描述这一行是什么，
    # 让用户横向比较；「哪个最好」由排序（高分辨率在前）+ 默认预选首行表达即可。

    # ---- 音频档 ----
    # 只在确实有独立音频流时给出：只有成品格式的站点上 bestaudio 会退化成整个视频
    # 文件，「仅下载音频」就成了假承诺
    audio_options = []
    for fmt in _audio_tracks(formats):
        abr = int(round(fmt.get('abr') or 0))
        # 档位名就是码率，不写「最佳音质」：用户要的是「这条多大、多好」，
        # 而不是替用户下结论；同码率下不同编码各自可见，方便挑容器。
        title = f'{abr}kbps' if abr else (fmt.get('acodec') or '音频')
        audio_options.append(_audio_option(fmt, title, '只保留声音，转换为音频文件'))

    return {'video': video_options, 'audio': audio_options}
