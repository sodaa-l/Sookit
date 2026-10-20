# coding:utf-8
"""
core/sniff_options.py
嗅探页「默认视图」选项生成（设计决策 47）。

把 `Functions.sniff_youtube` 产出的格式列表归并成「用户能直接理解并点选」的档位：

- 视频档：按 height 去重，最多 5 档；档位名只标分辨率与帧率
- 音频档：每个独立音轨一档，同码率的 drc/非 drc 变体归并，档位名就是码率
- 多语言（≥2 种音轨语言）：视频档按「分辨率 × 语言」各出一档，标题追加
  `・ja` 这样的语言码，音频档同码率的不同语言也各自成档（决策 58）
- 每个档位都同时给出完整副标题和「去编码」短副标题，窄卡片时由 UI 择优

每个档位都编译成**具体的 yt-dlp 选择器**（用嗅探到的 format_id 精确拼接），
因此行内副标题（编码/容器/大小估算）与实际下载到的流一致；同时按容器族挑
音轨（webm 视频配 webm/opus、mp4 视频配 m4a/mp4a），默认视图不会落到需要
「mkv 兜底合并」的跨族组合（决策 38 的弹窗只服务于表格视图的手工勾选）。

纯函数、无 Qt 依赖，便于离屏直接断言。
"""
from sookit.core.functions import FormatType
from sookit.core.ffmpeg_utils import format_filesize

MAX_VIDEO_ROWS = 5

# 多语言标签分隔符（决策 58）：标题形如「1080p 60fps・ja」，语言码取 yt-dlp 的
# `language` 原值（小写）。只有 ≥2 种语言时才加后缀，单语言保持原样。
LANG_SEP = '・'

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
    """大小估算。`约` 与数字间留一个空格：`约 1.0GB` 比 `约1.0GB` 好读，
    窄卡片放不下时由 OptionRow 按宽度压紧写法（而非逐行省略）。
    """
    return f"约 {format_filesize(filesize)}" if filesize else ''


def _join_meta(parts):
    """副标题拼装：`编码 · 容器 · 大小`。

    分隔符两侧留空格可读性好。卡片窄时由 OptionRow 先丢掉编码段（用各档位
    自己给的 `short_subtitle`），再压紧分隔符、降字号，而不是打省略号把
    大小截掉。
    """
    return ' · '.join(p for p in parts if p)


def _pick_audio(formats):
    """最佳独立音频流（按码率降序，缺失归 0）"""
    audio = [f for f in formats if f.get('type') == FormatType.AUDIO_ONLY]
    return max(audio, key=lambda f: (f.get('abr') or 0, f.get('filesize') or 0), default=None)


def _lang_tag(fmt):
    """该格式的语言码（小写）；没有语言信息时为 ''"""
    return str(fmt.get('language') or '').strip().lower()


def _is_auto_dubbed(fmt):
    """是否是 YouTube 的「自动配音」音轨（yt-dlp 在 format_note 里标 `dubbed-auto`）。

    yt-dlp 只把这两种配音写进 `format_note`（且只写在这种 m3u8 抽出来的
    `233-*` / `234-*` 音轨上）：

    - `American English - dubbed-auto` → 机器翻译的自动配音
    - `日本語 - dubbed`                → 人工/官方配音，属于作者提供的音轨，要保留

    同一条配音在 DASH 音轨（`139-*` / `140-*` / `249-*` / `250-*` / `251-*`）上
    只写语言名（`English (US), medium`），**不带任何配音标记** —— 所以单看某一条
    音轨分不出来，得先按语言汇总（见 `_auto_dubbed_languages()`，决策 60）。
    """
    return 'dubbed-auto' in str(fmt.get('format_note') or '').lower()


def _auto_dubbed_languages(formats):
    """被自动配音占用的语言集合（小写语言码）。

    只要某个语言有一条音轨被标成 `dubbed-auto`，该语言的**所有**音轨都算自动
    配音：`233-0`（`- dubbed-auto`）与 `139-0` / `140-0` … 是同一份机翻音轨的不同
    容器，前者只出现在 m3u8 音轨上。人工配音（`- dubbed`）不会进这个集合。
    """
    langs = set()
    for fmt in formats:
        if _is_auto_dubbed(fmt):
            tag = _lang_tag(fmt)
            if tag:
                langs.add(tag)
    return langs


def _multi_languages(formats):
    """多语言视频的语言码列表（原声优先，其次按语言码字典序）。

    单语言或没有语言信息时返回 `[]`——默认视图不该给每条普通视频都挂一个
    「・ko」，只有真正多语言时才需要让用户区分（决策 58）。

    排序依据 yt-dlp 的 `language_preference`：原声/默认音轨是正数（本例
    ko=10、ja=-1），与 yt-dlp 自己的默认选轨一致，预选第一档即「最高画质+
    原声」。
    """
    prefs = {}
    for fmt in formats:
        if fmt.get('type') != FormatType.AUDIO_ONLY:
            continue
        tag = _lang_tag(fmt)
        if not tag:
            continue
        pref = fmt.get('language_preference') or 0
        prefs[tag] = max(prefs.get(tag, pref), pref)
    if len(prefs) < 2:
        return []
    return sorted(prefs, key=lambda tag: (-prefs[tag], tag))


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


def _pick_compatible_audio(formats, video_fmt, language=None):
    """与视频流合得来的音频流。

    优先级：① 与视频 ext 同容器族（合并即可，不必跨容器重封装）
            ② 与视频编码有共同标准容器（能落 webm/mp4，不落 mkv）
    同类里按码率取最高；都合不来时退回同族里码率最高的音频（宁可落 mkv 也要有声）。

    给了 `language` 时只在**该语言**的音轨里挑：多语言视频一旦在本语言内找不到
    合适音轨，就只能退到本语言里码率最高的那条，绝不能换成别的语言（那等于把
    音轨下错，决策 58）。
    """
    audio = [f for f in formats if f.get('type') == FormatType.AUDIO_ONLY]
    if language:
        audio = [f for f in audio if _lang_tag(f) == language]
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
    return max(audio, key=lambda f: (f.get('abr') or 0, f.get('filesize') or 0))


def _pick_video_at_height(formats, height, language=None):
    """该高度下最佳视频流；该高度只有成品格式时返回成品格式。

    独立视频流本身不带语言（所有语言共用同一个画面），只有**成品格式**
    （视频+音频一体）才可能按语言分开，`language` 给定时在成品里只认该语言
    （语言为空的成品视为通用）（决策 58）。
    """
    videos = [f for f in formats
              if f.get('type') == FormatType.VIDEO_ONLY and (f.get('height') or 0) == height]
    if videos:
        # 同高度多码流（如 1080p30 与 1080p60）：优先高帧率，再优先更大文件
        return max(videos, key=lambda f: (f.get('fps') or 0, f.get('filesize') or 0))
    muxed = [f for f in formats
             if f.get('type') == FormatType.VIDEO_AUDIO and (f.get('height') or 0) == height]
    if language:
        muxed = [f for f in muxed if _lang_tag(f) in ('', language)]
    return max(muxed, key=lambda f: (f.get('fps') or 0, f.get('filesize') or 0), default=None)


def _video_option(formats, height, language=None):
    """单个视频档：标题 / 副标题 / 精确选择器（`language` 非空时只配该语言音轨）"""
    fmt = _pick_video_at_height(formats, height, language)
    if fmt is None:
        return None
    title = f"{height}p"
    if (fmt.get('fps') or 0) >= 50:
        title += f" {int(round(fmt.get('fps')))}fps"
    option_id = f"h{height}"
    if language:
        title += f"{LANG_SEP}{language}"
        option_id += f"_{language}"

    if fmt.get('type') == FormatType.VIDEO_AUDIO:
        codec = fmt.get('vcodec') or fmt.get('acodec') or ''
        return {
            'id': option_id,
            'kind': 'video',
            'title': title,
            'subtitle': _join_meta([codec, fmt.get('ext'), _size_text(fmt.get('filesize'))]),
            'short_subtitle': _join_meta([fmt.get('ext'), _size_text(fmt.get('filesize'))]),
            'spec': fmt['format_id'],
        }

    audio_fmt = _pick_compatible_audio(formats, fmt, language)
    vcodec = fmt.get('vcodec') or '视频'
    if audio_fmt is None:
        return {
            'id': option_id,
            'kind': 'video',
            'title': title,
            'subtitle': _join_meta([vcodec, fmt.get('ext'), '无音频流',
                                    _size_text(fmt.get('filesize'))]),
            'short_subtitle': _join_meta([fmt.get('ext'), '无音频流',
                                          _size_text(fmt.get('filesize'))]),
            'spec': fmt['format_id'],
        }
    # 编码对用紧凑写法（不加号两侧空格）：两列卡片只有 ~235px 宽，
    # 副标题要在一行里放下「编码 + 容器 + 大小」
    codec = f"{vcodec}+{audio_fmt.get('acodec') or '音频'}"
    size = (fmt.get('filesize') or 0) + (audio_fmt.get('filesize') or 0)
    # 展示合并后的真实容器：同族 → webm/mp4；跨族（该高度没有同族音轨可配）→ mkv
    merged_ext = _merged_ext(fmt.get('vcodec'), audio_fmt.get('acodec'),
                             fmt.get('ext'), audio_fmt.get('ext'))
    return {
        'id': option_id,
        'kind': 'video',
        'title': title,
        'subtitle': _join_meta([codec, merged_ext, _size_text(size)]),
        # 卡片窄时丢掉的正是这一段编码对，只留容器 + 大小（决策 47）
        'short_subtitle': _join_meta([merged_ext, _size_text(size)]),
        'spec': f"{fmt['format_id']}+{audio_fmt['format_id']}",
    }


def _audio_tracks(formats, languages=()):
    """独立音轨去重后按码率降序返回（默认视图的音频档来源）。

    YouTube 同一码率常有多个变体：`140` 与 `140-drc`、`251` 与 `251-drc`、
    甚至 mp4a/opus 双编码同码率。逐条平铺会把列表塞满重复项，所以按
    「码率取整到 kbps」归并，同码率里优先非 drc（动态范围压缩版音质更差）、
    再优先更大的文件。

    `languages` 非空（多语言视频）时归并键里再带上语言：否则同一码率的 ja/ko
    会并成一条，另一种语言整档消失（决策 58）。

    这里**不做码率阈值筛选**：曾按「最高码率 - 2」筛第二档，导致 129.5kbps
    的 mp4a 因为高于阈值被整批丢掉、第二档回落到 65kbps（用户实际遇到的
    问题）。音频档本来就少，全列出更好选。
    """
    tracks = [f for f in formats if f.get('type') == FormatType.AUDIO_ONLY]
    groups = {}
    for fmt in tracks:
        lang = _lang_tag(fmt) if languages else ''
        abr = fmt.get('abr') or 0
        if abr <= 0:
            # 码率未知的独立音轨（少见）：单独成档，别丢弃
            groups[('unk', lang, fmt.get('format_id'))] = [fmt]
            continue
        groups.setdefault((int(round(abr)), lang), []).append(fmt)

    picked = []
    for key, group in groups.items():
        if key[0] == 'unk':               # 码率未知组
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
    short = _join_meta([fmt.get('ext'), _size_text(fmt.get('filesize'))])
    return {
        'id': f"a_{fmt['format_id']}",
        'kind': 'audio',
        'title': title,
        'subtitle': subtitle or fallback_subtitle,
        'short_subtitle': short or fallback_subtitle,
        'spec': fmt['format_id'],
    }


def build_view_options(formats):
    """把格式列表归并成默认视图的档位列表。

    返回::

        {'video': [option, ...], 'audio': [option, ...]}

    每个 option::

        {'id': str, 'kind': 'video'|'audio', 'title': str,
         'subtitle': str, 'short_subtitle': str, 'spec': str}

    `short_subtitle` 是去掉编码段（`vp9+opus`）的副标题，只留容器与大小；
    卡片变窄时 `OptionRow` 优先用它，窗口再窄也不会把大小挤成省略号（决策 47）。

    video 与 audio 在 UI 侧全局互斥（一次只下一样东西）。某组没有可用格式时
    该组为空列表（纯音频站点没有视频档；只有成品格式的站点没有独立音频档）。
    """
    formats = formats or []
    # 自动配音音轨不进默认视图（决策 60）：先看 `format_note` 里的 `dubbed-auto`
    # 找出被机翻配音占用的**语言**，再把该语言的音轨（含不带标记的 DASH 变体）
    # 一起筛掉。人工配音（`- dubbed`，如日配）不算自动配音，予以保留。
    # 从源头筛掉，下游的语言归类、音轨配对、音频档归并、多语言拆分都自动一致。
    # 表格视图走的是另一份原始数据，仍会照常列出这些行供手动选择。
    auto_dubbed_langs = _auto_dubbed_languages(formats)
    formats = [f for f in formats
               if not (_is_auto_dubbed(f) or _lang_tag(f) in auto_dubbed_langs)]

    # 多语言（≥2 种音轨语言）的站点：视频档按「分辨率 × 语言」各出一档，
    # 否则一张卡只能悄悄绑死某一种语言、其他语言在默认视图完全看不到（决策 58）
    languages = _multi_languages(formats)

    # ---- 视频档 ----
    # 按 height 去重；同一高度只会有一行（该高度内取最好的流）
    heights = sorted({f.get('height') or 0 for f in formats
                      if f.get('type') in (FormatType.VIDEO_ONLY, FormatType.VIDEO_AUDIO)
                      and (f.get('height') or 0) > 0}, reverse=True)

    video_options = []
    for height in heights[:MAX_VIDEO_ROWS]:
        for lang in (languages or (None,)):
            option = _video_option(formats, height, lang)
            if option:
                video_options.append(option)
    # 视频档一律标分辨率（最高档也不叫「最佳画质」）：档位名只描述这一行是什么，
    # 让用户横向比较；「哪个最好」由排序（高分辨率在前）+ 默认预选首行表达即可。

    # ---- 音频档 ----
    # 只在确实有独立音频流时给出：只有成品格式的站点上 bestaudio 会退化成整个视频
    # 文件，「仅下载音频」就成了假承诺
    audio_options = []
    for fmt in _audio_tracks(formats, languages):
        abr = int(round(fmt.get('abr') or 0))
        # 档位名就是码率，不写「最佳音质」：用户要的是「这条多大、多好」，
        # 而不是替用户下结论；同码率下不同编码各自可见，方便挑容器。
        # 码率未知的音轨（YouTube 233/234 这类）退回 quality（= yt-dlp 的
        # format_note，如「Default, low」），否则两张卡会同名「音频」（决策 57 补记）。
        title = f'{abr}kbps' if abr else (fmt.get('quality') or fmt.get('acodec') or '音频')
        tag = _lang_tag(fmt) if languages else ''
        if tag:
            title = f'{title}{LANG_SEP}{tag}'
        audio_options.append(_audio_option(fmt, title, '只保留声音，转换为音频文件'))

    return {'video': video_options, 'audio': audio_options}
