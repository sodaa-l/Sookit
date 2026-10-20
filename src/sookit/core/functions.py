"""
core/functions.py
功能类和相关工具函数（精简版，具体实现已拆分至子模块）
"""

import json
import os
import sys
import subprocess
import tempfile
import urllib.request

# ---------- 依赖检查 ----------
# yt-dlp - 可选依赖，PATH 全局安装优先，未检测到则回退项目内置 tools/yt-dlp/yt-dlp.exe。
# 统一由 core/ytdlp_utils 动态解析（安装内置版后无需重启即生效）。


def _run_ytdlp_json(url, *extra_args, timeout=180):
    """调用 yt-dlp（PATH 优先 → 内置回退）以 -J 模式输出 JSON 并解析返回。

    用于嗅探、直播状态检查、频道列表等场景（替代内嵌的 yt_dlp Python 库）。
    """
    cmd = build_ytdlp_cmd('-J', '--no-warnings', '--encoding', 'utf-8')
    cmd.extend(extra_args)
    cmd.append(url)
    env = os.environ.copy()
    try:
        import certifi
        env['SSL_CERT_FILE'] = certifi.where()
    except ImportError:
        pass
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=timeout, env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
    except FileNotFoundError:
        raise RuntimeError("未找到 yt-dlp，请在设置页下载安装")
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()
                           or f"yt-dlp 退出码: {result.returncode}")
    return json.loads(result.stdout)

# ---------- 从子模块导入（供本文件内部使用 + 保留页面仍在使用的聚合入口）----------

from sookit.core.ffmpeg_utils import (
    get_ffmpeg_path, check_ffmpeg,
    format_duration, format_filesize,
    run_ffmpeg, run_ytdlp,
    get_aria2c_path, check_aria2c,
)
from sookit.core.youtube_utils import (
    extract_youtube_id, build_thumbnails,
    normalize_thumbnails, fetch_youtube_metadata,
)
from sookit.core.gif_utils import convert_to_gif
from sookit.core.config import (
    load_download_config, save_download_config,
    load_close_action, save_close_action,
    load_task_complete_action, save_task_complete_action,
    load_toast_notification_enabled, save_toast_notification_enabled,
    THEME_COLORS, load_theme_color, save_theme_color,
    set_autostart, is_autostart,
)
from sookit.core.utils import get_certifi_ssl_context
from sookit.core.ytdlp_utils import (
    get_ytdlp_source, is_ytdlp_available,
    build_ytdlp_cmd,
    get_deno_current_version,
    get_ytdlp_latest_version,
    check_ytdlp_deno_update_needed, launch_ytdlp_updater,
    check_path_ytdlp_update, check_tools_ytdlp_update,
)
from sookit.core.app_update import (
    get_current_version,
    check_latest_version, set_ignored_version,
    RELEASES_URL,
    is_updater_available, launch_app_setup_downloader,
)


# ---------- 通用函数 ----------

import ctypes
from ctypes import wintypes

class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]

FOLDERID_Downloads = GUID(0x374DE290, 0x123F, 0x4565,
                          (0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))

def _get_default_download_dir():
    """通过 Windows API 获取用户下载目录"""
    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32

    _SHGetKnownFolderPath = shell32.SHGetKnownFolderPath
    _SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p)
    ]
    _SHGetKnownFolderPath.restype = ctypes.HRESULT

    _CoTaskMemFree = ole32.CoTaskMemFree
    _CoTaskMemFree.argtypes = [ctypes.c_void_p]
    _CoTaskMemFree.restype = None

    pszPath = ctypes.c_wchar_p()
    hr = _SHGetKnownFolderPath(
        ctypes.byref(FOLDERID_Downloads), 0, None, ctypes.byref(pszPath))
    if hr < 0:
        raise ctypes.WinError(hr)
    path = pszPath.value
    _CoTaskMemFree(pszPath)
    return path

try:
    DEFAULT_OUTPUT_DIR = _get_default_download_dir()
except Exception:
    DEFAULT_OUTPUT_DIR = os.path.join(os.path.expanduser("~"), "Downloads")

def ensure_output_dir(path=None):
    """确保输出目录存在"""
    d = path or DEFAULT_OUTPUT_DIR
    os.makedirs(d, exist_ok=True)
    return d

def sanitize_path(path):
    return os.path.normpath(path.strip().strip('"').strip("'"))


# ---------- 格式类型常量 ----------

class FormatType:
    """格式类型常量 (替代中文字符串比较)"""
    VIDEO_AUDIO = "视频+音频"
    AUDIO_ONLY = "音频流"
    VIDEO_ONLY = "视频流"
    OTHER = "其他"


# ---------- 缩略图分流（嗅探 / 卡片元数据共用）----------

def _thumbnails_for(info: dict) -> list:
    """按站点分流取封面列表。

    YouTube 用固定档位表（build_thumbnails），其它站点走 yt-dlp 通用缩略图
    （normalize_thumbnails，含 twitter/X 的 medium 单档特判）。嗅探与卡片
    元数据两条路径共用，避免分流规则各写一份。
    """
    if (info.get('extractor') or '').lower().startswith('youtube'):
        return build_thumbnails(info.get('id', ''))
    return normalize_thumbnails(info)


def _looks_like_animated(entry: dict) -> bool:
    """判定 yt-dlp 条目是否为 X 的 animated_gif（动图）。

    yt-dlp 不透出 media type，只能用条目字段做启发式。实测（2026-10-07，
    x.com 单推文「2 动图 + 1 普通视频」）：
      - animated_gif 条目：duration 为 None、format_id 为单一直链 'http'、
        resolution/vcodec 等全空（yt-dlp 不解码其元数据）；
      - 普通视频条目：duration 有值，resolution/vcodec 齐全。
    因此判定为「无时长 且 无分辨率信息」。误判方向刻意保守：把没解析出时长的
    条目当成动图去下载，只会多产出一个 GIF，不会漏掉真动图。
    """
    if entry.get('duration') not in (None, 0):
        return False
    if entry.get('resolution') or entry.get('width') or entry.get('height'):
        return False
    return True


def _entry_kind(entry: dict):
    """把 yt-dlp 条目粗分为 'gif'（X 动图）/ 'video'（普通视频）/ None（不入列表）。

    `_looks_like_animated` 只回答「是不是动图」；动图页自 2026-10-19 起还要把
    同一条推文里的普通视频也列出来（显示名「视频 N」，默认不勾选，勾选后下载前
    弹窗确认），故需要第二档判定。见 docs/decisions/44-gif-page-video-items.md。

    判定口径（与 `_looks_like_animated` 共享同一份实测事实）：
      - 无时长 + 无分辨率 → 动图（理由见上，判定函数本身不改）；
      - 有明确时长 → 视频；
      - 有分辨率但无时长 → None（罕见条目，既不列动图也不列视频）。

    为什么判视频只认「有明确时长」：X 的**图片**条目同样没有时长、没有分辨率，
    与动图在元数据上无法区分——它们会沿用旧口径落进 'gif' 档（下载时由 yt-dlp
    的格式串 `b[ext=mp4]/b` 自然筛掉，不是本次引入的行为）。因此只有
    `duration not in (None, 0)` 才敢认定视频，**误判方向刻意保守**：解析不出时长
    的视频会被当成动图（多下一个动图，不会漏），绝不会把图片伪造成「视频 N」。
    """
    if _looks_like_animated(entry):
        return 'gif'
    if entry.get('duration') not in (None, 0):
        return 'video'
    return None


def _codec_name(codec):
    """把 yt-dlp 的 codec 字段归一成「展示用的短名」。

    口径与 `sniff_youtube` 的分类保持一致：`'none'` 表示「这条流不存在」、
    字段缺失/None 表示「未知」，两种情况都展示为空；其余取 '.' 前的部分
    （'avc1.64001F' → 'avc1'，'mp4a.40.2' → 'mp4a'）。
    """
    if not codec or codec == 'none':
        return ''
    return str(codec).split('.')[0]


# ---------- 功能类 ----------

class Functions:
    @staticmethod
    def burn_subtitles(video, subtitle, output, encoder='software', log=None,
                       on_process_created=None):
        """烧录字幕到视频 - 通过切换工作目录规避驱动器冒号分隔符问题"""
        if encoder == 'software':
            vcodec = ['-c:v', 'libx265', '-preset', 'slow', '-crf', '18']
        else:
            vcodec = ['-c:v', 'h264_nvenc', '-preset', 'p6', '-tune', 'll', '-cq', '23']
        ffmpeg = get_ffmpeg_path()
        if not os.path.exists(ffmpeg):
            ffmpeg = "ffmpeg"
        # 只使用字幕文件名（不含路径），彻底规避 ffmpeg subtitles 滤镜将
        # 路径中的 ':' 误判为选项分隔符的问题（如 E: 被拆为 filename=E）
        # 通过 os.chdir 切换到字幕所在目录，ffmpeg 用相对路径即可找到文件
        sub_dir = os.path.dirname(subtitle)
        sub_name = os.path.basename(subtitle)
        filter_graph = f"subtitles={sub_name}"
        with tempfile.NamedTemporaryFile(
                mode='w', suffix='.ff', delete=False, encoding='utf-8') as f:
            f.write(filter_graph)
            filter_script = f.name
        cmd = ([ffmpeg, '-i', video, '-filter_script:v', filter_script]
               + vcodec + ['-c:a', 'copy', output])
        orig_cwd = os.getcwd()
        try:
            os.chdir(sub_dir)
            return run_ffmpeg(cmd, log, None, on_process_created)
        finally:
            os.chdir(orig_cwd)
            try:
                os.unlink(filter_script)
            except Exception:
                pass

    @staticmethod
    def replace_audio(video, audio, output, mode='direct', log=None,
                      on_process_created=None):
        temp = None
        ffmpeg = get_ffmpeg_path()
        if not os.path.exists(ffmpeg):
            ffmpeg = "ffmpeg"
        if mode == 'direct':
            pass  # ac = ['-c:a', 'copy']
        else:
            temp = tempfile.NamedTemporaryFile(suffix='.flac', delete=False).name
            cmd1 = [ffmpeg, '-i', audio, '-c:a', 'flac', '-ar', '48000',
                    '-sample_fmt', 's32', '-y', temp]
            run_ffmpeg(cmd1, log, None, on_process_created)
            audio = temp
        cmd = [ffmpeg, '-y', '-i', video, '-i', audio, '-c:v', 'copy',
               '-c:a', 'copy', '-map', '0:v:0', '-map', '1:a:0', '-shortest', output]
        try:
            return run_ffmpeg(cmd, log, None, on_process_created)
        finally:
            if mode != 'direct' and temp and os.path.exists(temp):
                os.unlink(temp)

    @staticmethod
    def extract_audio(video, output, log=None, on_process_created=None):
        ffmpeg = get_ffmpeg_path()
        if not os.path.exists(ffmpeg):
            ffmpeg = "ffmpeg"
        cmd = [ffmpeg, '-y', '-i', video, '-vn', '-acodec', 'copy', output]
        return run_ffmpeg(cmd, log, None, on_process_created)

    @staticmethod
    def convert_to_gif(video, output, *args, **kwargs):
        """MP4 → GIF（按 quality 档位自动决策帧率/色数/分辨率）。

        参数透传给 core/gif_utils.convert_to_gif（第 3 位为 quality 档位）。
        返回最终 GIF 路径。
        """
        from sookit.core.gif_utils import convert_to_gif as _impl
        return _impl(video, output, *args, **kwargs)

    @staticmethod
    def download_and_convert_gif(url, output_dir, quality, custom_params=None,
                                 use_aria2c=True, aria2c_connections=16,
                                 concurrent_fragments=8,
                                 log=None, on_process_created=None,
                                 on_progress=None, *, selected_indexes=None):
        """下载 X 动图并转成 GIF（组合任务：yt-dlp 下载 MP4 → ffmpeg 转码 GIF）。

        Args:
            url: X 状态链接。/video/N、/photo/N 路径后缀会被自动剥离
                （yt-dlp 对 animated_gif 条目遇 /video/N 会报
                "Media #N is not a video"，见 DEVELOPMENT.md 决策 30）
            output_dir: GIF 输出目录
            quality: GifQuality 四档之一（custom_params 非空时被忽略）
            custom_params: GifCustomParams（自定义档参数），None 表示预设档
            use_aria2c / aria2c_connections: 传给 yt-dlp 的下载器选项
            concurrent_fragments: 分片并发数（`-N`），透传给 `download_youtube`；
                动图/视频常是 HLS 或 DASH 分片流，同样受益（见决策 46）
            log / on_process_created / on_progress: Sookit 任务约定回调
                （TaskWorker GIF 分支固定传入这三个 kwargs）
            selected_indexes: **关键字专用**（任务队列经 kwargs 通道传入，见
                DEVELOPMENT.md 决策 31）。只转换这些条目序号（1-based，对应
                **原始条目列表**的次序，None = 该链接下的全部动图与视频）。序号来自
                `fetch_gif_items` 返回的 items（页面只收集被勾选那些的 index），
                此处再与条目类型判定求交集——序号可能来自过期的解析结果，不过滤
                会把同一条推文里的图片等无法转码的条目也纳入。
        条目口径（决策 44）：先按 `_entry_kind` 判定 playlist 各条目，用
        `--playlist-items` 只下载「动图 + 普通视频」条目（图片被排除）；
        `selected_indexes` 非空时再按页面勾选收窄。普通视频自 2026-10-19 起
        允许下载转 GIF，但必须由用户在页面上显式勾选（默认不勾选、勾了先弹确认），
        任务函数本身不再额外拦截。整条链接既无动图也无视频时直接抛错（任务失败
        并提示）。
        返回最终 GIF 路径：单文件 str，含多个条目时为 list。

        进度约定：下载阶段无法解析为百分比（GIF 任务不走 YTDLP 进度解析），
        保持 0；转码阶段把 convert_to_gif 的 0~100 映射到 30~100
        （多视频时按文件数均分区间）。
        """
        import re as _re
        import shutil as _shutil

        Functions._check_ytdlp()
        url = (url or "").strip()
        if not url:
            raise RuntimeError("链接为空")
        if not output_dir:
            raise RuntimeError("未指定输出目录")

        clean_url = _re.sub(r'/(?:video|photo)/\d+', '', url)
        if clean_url != url and log:
            log(f"链接含媒体序号后缀，已剥离: {clean_url}")

        # ---- ① 条目识别：动图 + 普通视频（2026-10-19 起视频也纳入，见决策 44）----
        # 历史：此前只挑动图、跳过同一条推文里的普通视频（实测推文「2 动图 + 1 个
        # 4K 视频」会把视频也转成 GIF）。现在视频由页面显式勾选才下载：默认不勾选，
        # 勾了会先弹窗确认，故此处放行视频条目，但仍沿用 `_entry_kind` 过滤掉图片。
        if log:
            log("[GIF] ① 正在识别动图/视频条目（yt-dlp）...")
        try:
            info = _run_ytdlp_json(clean_url)
        except Exception as e:
            raise RuntimeError(f"识别动图条目失败: {e}")
        entries = [e for e in (info.get('entries') or []) if e] or [info]
        kinds = {i + 1: _entry_kind(e) for i, e in enumerate(entries)}
        convertible = [i for i, k in kinds.items() if k is not None]
        if not convertible:
            raise RuntimeError("该链接没有动图或视频（推文只含图片），已跳过")
        # selected_indexes 为页面选中的原始条目序号：与「可转换条目」求交集，
        # 避免过期序号把图片等无法转码的条目也纳入下载范围
        if selected_indexes:
            wanted = [i for i in convertible if i in set(selected_indexes)]
            if not wanted:
                raise RuntimeError("选中的条目无法转换（可能链接已更换），请重新解析")
            skipped = len(convertible) - len(wanted)
            if log and skipped:
                log(f"[GIF] 已按选择过滤：跳过 {skipped} 个未选中的条目")
            downloadable = wanted
        else:
            downloadable = convertible
        # 选中清单里动图/视频各自计数，用于日志与「批量转码可能很慢」的提示
        n_gif = sum(1 for i in downloadable if kinds.get(i) == 'gif')
        n_video = sum(1 for i in downloadable if kinds.get(i) == 'video')
        extra_args = None
        if len(downloadable) < len(entries):
            extra_args = ['--playlist-items',
                          ','.join(str(i) for i in downloadable)]
            if log:
                log(f"[GIF] 共 {len(entries)} 个条目，将下载 {len(downloadable)} 个"
                    f"（动图 {n_gif} 个、视频 {n_video} 个）")
        elif log:
            log(f"[GIF] 共 {len(entries)} 个条目，全部下载"
                f"（动图 {n_gif} 个、视频 {n_video} 个）")
        if log and n_video:
            log(f"[GIF] 注意：其中 {n_video} 个为普通视频，转 GIF 可能体积较大、耗时较长")

        # 中间 MP4 下载到独立临时目录：成功随目录清理（输出目录只留 .gif），
        # 失败时移入输出目录保留便于排查
        tmpdir = tempfile.mkdtemp(prefix="sookit_gif_dl_")
        mp4s = []
        try:
            if log:
                log("[GIF] ② 开始下载动图（yt-dlp）...")
            downloaded = Functions.download_youtube(
                clean_url, "b[ext=mp4]/b", tmpdir, remote_components=False,
                use_aria2c=use_aria2c, aria2c_connections=aria2c_connections,
                concurrent_fragments=concurrent_fragments,
                log=log, on_process_created=on_process_created,
                extra_args=extra_args)
            mp4s = [p for p in (downloaded or []) if p and os.path.isfile(p)]
            if not mp4s:
                raise RuntimeError("下载完成但未找到文件")
            if log:
                log(f"[GIF] ③ 下载完成，共 {len(mp4s)} 个动图，开始转码")

            def _gif_path(mp4):
                stem = os.path.splitext(os.path.basename(mp4))[0]
                stem = "".join(c for c in stem if c not in '\\/:*?"<>|').strip() or "gif"
                cand = os.path.join(output_dir, stem + ".gif")
                n = 1
                while os.path.exists(cand):
                    n += 1
                    cand = os.path.join(output_dir, f"{stem}_{n}.gif")
                return cand

            n_files = len(mp4s)
            gifs = []
            for i, mp4 in enumerate(mp4s):
                lo = 30.0 + 70.0 * i / n_files
                hi = 30.0 + 70.0 * (i + 1) / n_files

                def _map_progress(p, lo=lo, hi=hi):
                    if on_progress:
                        p = max(0.0, min(100.0, float(p)))
                        on_progress(lo + (hi - lo) * p / 100.0)

                if log and n_files > 1:
                    log(f"[GIF] 转码第 {i + 1}/{n_files} 个: {os.path.basename(mp4)}")
                gifs.append(convert_to_gif(
                    mp4, _gif_path(mp4), quality, custom_params=custom_params,
                    log=log, on_process_created=on_process_created,
                    on_progress=_map_progress))
            return gifs[0] if len(gifs) == 1 else gifs
        except Exception:
            kept = []
            for p in mp4s:
                if p and os.path.isfile(p):
                    try:
                        dst = os.path.join(output_dir, os.path.basename(p))
                        _shutil.move(p, dst)
                        kept.append(dst)
                    except OSError:
                        pass
            if kept and log:
                log("[GIF] 任务失败，源 MP4 已保留在输出目录: "
                    + ", ".join(os.path.basename(k) for k in kept))
            raise
        finally:
            _shutil.rmtree(tmpdir, ignore_errors=True)

    # ---------- YouTube 嗅探与下载 ----------
    @staticmethod
    def _check_ytdlp():
        """检查 yt-dlp 是否可用（PATH 全局或内置 tools/ 均可）"""
        if not is_ytdlp_available():
            raise RuntimeError("未找到 yt-dlp，请在设置页下载安装")

    @staticmethod
    def sniff_youtube(url, log=None):
        Functions._check_ytdlp()
        if log: log(f"正在嗅探: {url}")
        try:
            info = _run_ytdlp_json(url, '--no-playlist')
        except Exception as e:
            raise RuntimeError(f"嗅探失败: {e}")

        formats = []
        seen = set()
        for f in info.get('formats', []):
            fid = f.get('format_id', '')
            if fid in seen:
                continue
            seen.add(fid)
            # yt-dlp 的 codec 口径：只有**字面量 'none'** 表示「这条流不存在」，
            # 字段缺失 / 为 None 表示「未知」，一律按「有」处理 —— 与 yt-dlp 自己
            # 的 `-f bestaudio`、`-f best` 选择器同一套判定。X(Twitter) 正踩在
            # 这上面：
            #   hls-audio-*：vcodec='none'、**acodec 字段缺失** → 独立音轨
            #   http-*     ：两个字段都缺失（渐进式成品，实测 h264+aac 双流）→ 成品
            # 旧写法 `f.get('acodec', 'none')` 把「缺失」当成「没有」，X 的音轨
            # 全被判成「其他」，默认视图因此误报「无音频流」（决策 57）。
            vcodec = f.get('vcodec')
            acodec = f.get('acodec')
            has_video = vcodec != 'none'
            has_audio = acodec != 'none'
            if has_video and has_audio:
                fmt_type = '视频+音频'
            elif has_video:
                fmt_type = '视频流'
            elif has_audio:
                fmt_type = '音频流'
            else:
                fmt_type = '其他'

            height = f.get('height', 0) or 0
            tbr = f.get('tbr', 0) or 0
            if fmt_type == '视频流' and height:
                quality = f"{height}p"
                if f.get('fps'):
                    quality += f" {int(f.get('fps', 0))}fps"
            elif fmt_type == '音频流' and tbr:
                quality = f"{int(tbr)}kbps"
            elif fmt_type == '视频+音频' and (height or tbr):
                quality = f"{height}p" if height else f"{int(tbr)}kbps"
            else:
                # 分辨率/码率都没有可展示的值时，退回 yt-dlp 自己的 format_note，
                # 免得表格里「质量」列一片空白。典型：YouTube 的 233/234 是
                # m3u8 抽出来的音轨，acodec 为 null、abr 也没有，只有备注
                # 「Default, low / Default, high」（决策 57 补记）。
                quality = str(f.get('format_note') or '')

            filesize = f.get('filesize') or f.get('filesize_approx', 0)
            if filesize:
                size_str = format_filesize(filesize)
            else:
                size_str = ''

            formats.append({
                'format_id': fid,
                'ext': f.get('ext', ''),
                'type': fmt_type,
                'quality': quality,
                'language': f.get('language', '') or f.get('language_code', '') or '',
                # 多语言视频（YouTube 多音轨）的语言偏好：yt-dlp 给原声/默认音轨
                # 一个正数（ko=10、ja=-1），默认视图据此把原声排在最前（决策 58）
                'language_preference': f.get('language_preference') or 0,
                'vcodec': _codec_name(vcodec),
                'acodec': _codec_name(acodec),
                'size_str': size_str,
                'filesize': filesize,
                # 结构化字段（决策 47）：默认视图按分辨率/帧率/码率归并档位，不再解析
                # quality 文本。height 为 0 表示未知（如 HLS 成片、storyboard）
                'height': height,
                'fps': int(f.get('fps') or 0),
                # yt-dlp 的原始备注：默认视图用它筛「自动配音」音轨
                # （format_note 里带 `dubbed-auto`，决策 60）
                'format_note': str(f.get('format_note') or ''),
                'abr': int(f.get('abr') or tbr) if fmt_type == '音频流' else 0,
            })

        # YouTube：用视频 ID 构建固定档位列表（现状行为不变）
        # 非 YouTube：用 yt-dlp 通用封面列表（extractor 通用字段）
        valid_thumbs = _thumbnails_for(info)

        if log: log(f"嗅探完成: {info.get('title', '')}, 共 {len(formats)} 个格式, {len(valid_thumbs)} 个封面")

        return {
            'title': info.get('title', ''),
            'duration': info.get('duration', 0),
            'channel': info.get('channel', '') or info.get('uploader', ''),
            'id': info.get('id', ''),
            'extractor': info.get('extractor', ''),
            'thumbnail': info.get('thumbnail', ''),
            'formats': formats,
            'thumbnails': valid_thumbs,
        }

    @staticmethod
    def fetch_gif_items(url, log=None):
        """列出链接里的动图与普通视频条目（动图页选择用，不下载媒体）。

        返回::

            {
              'title': 推文文本（顶层优先，回退首条条目）,
              'channel': 作者,
              'items': [
                  {'index': 原始条目序号(1-based), 'kind': 'gif' | 'video',
                   'url': 条目链接, 'title': 条目标题, 'duration': 时长,
                   'width'/'height': 分辨率(仅视频有), 'cover_url': 封面},
                  ...
              ],
            }

        **排序：动图在前、视频在后**（各自保持原始条目次序）。页面把列表顺序直接
        当作网格顺序，因此动图永远排在连续的前段、视频排在后段——用户勾选动图
        的顺序与视觉一致。`index` 仍是**原始条目序号**，不受排序影响。

        分类走 `_entry_kind`，与 `download_and_convert_gif` 同一口径；`None` 档的
        条目不入列表。**index 保留原始条目列表的次序**——下载
        时它以 `--playlist-items` 回传，序号必须与 yt-dlp 看到的条目列表一致，
        不能是筛选/排序后的下标。
        链接既无动图也无视频时返回空 items（由页面提示，不抛错——与「解析失败」
        是两码事）。
        """
        Functions._check_ytdlp()
        info = _run_ytdlp_json(url)

        entries = [e for e in (info.get('entries') or []) if e]
        top = entries[0] if entries else info
        title = info.get('title', '') or top.get('title', '')
        channel = ((info.get('channel', '') or info.get('uploader', ''))
                   or top.get('channel', '') or top.get('uploader', ''))

        gifs, videos = [], []
        for i, entry in enumerate(entries or [info]):
            kind = _entry_kind(entry)
            if kind is None:
                continue
            thumbs = _thumbnails_for(entry)
            item = {
                'index': i + 1,
                'kind': kind,
                'url': entry.get('url') or entry.get('webpage_url') or '',
                'title': entry.get('title') or '',
                'duration': entry.get('duration') or 0,
                'cover_url': ((thumbs[0]['url'] if thumbs else '')
                              or entry.get('thumbnail', '')),
            }
            (gifs if kind == 'gif' else videos).append(item)

        items = gifs + videos
        # 视频的显示编号按**视频出现顺序**从 1 开始（与动图各自独立计数）
        for n, item in enumerate(videos, 1):
            item['video_no'] = n
        if log:
            log(f"条目解析完成: {title}, 共 {len(gifs)} 个动图、"
                f"{len(videos)} 个视频")
        return {'title': title, 'channel': channel, 'items': items}

    @staticmethod
    def download_youtube(url, format_spec, output_dir, remote_components,
                        concurrent_fragments=8,
                        use_aria2c=True, aria2c_connections=16,
                        log=None, process_ref=None, on_process_created=None,
                        workspace=None, extra_args=None):
        """下载视频/动图到 output_dir（workspace 非空时先下到 workspace）。

        concurrent_fragments: HLS/DASH 分片流的并发分片数（yt-dlp
            `-N/--concurrent-fragments`）。HLS 走 yt-dlp 内建 hlsnative 下载器，
            aria2c 不参与（其 SUPPORTED_PROTOCOLS 不含 m3u8_native），所以分片流
            的提速只能靠这个参数；aria2c 的 `-x/-s` 只对单文件 HTTP 分段生效。
            见 DEVELOPMENT.md 决策 46。
        extra_args: 额外的 yt-dlp 参数（如 `--playlist-items 1,2` 只挑部分条目），
            在 URL 之前追加；默认 None 不追加（保持既有下载任务行为不变）。
        """
        Functions._check_ytdlp()
        # workspace 非空时输出到独立临时目录（下载完成后由 Sookit 移动到 output_dir）
        out = workspace or output_dir
        output_template = os.path.join(out, '%(title)s.%(ext)s')
        cmd = build_ytdlp_cmd('-f', format_spec, '-o', output_template, '--newline',
                              '--no-overwrites')
        # HLS（m3u8）流不再强制走 ffmpeg 下载。原决策 33 为规避 hlsnative 直拼产物
        # 的「无 PTS/DTS 坏包」在 mkv 合并时必失败，一律加 --hls-prefer-ffmpeg；
        # 2026-10-15 起修订（决策 38）：嗅探页改为「预测合并落点 + mkv 风险弹窗引导
        # 用户选标准组合」，监控页走 bestvideo+bestaudio 自动选格式不会落到非标组合，
        # 故全局移除该参数——顺带恢复 HLS 阶段的下载进度可见性。
        # 关掉 yt-dlp 的「格式可用性探测」（--check-formats）：上传 ≤2 天的新视频，其所有
        # m3u8_native(HLS) 格式会被提取器打上 __needs_testing，yt-dlp 下载前会先下第一个分片
        # 试一次，而**探测失败即把该格式当作不可用丢弃**（success=False → Skipping →
        # Requested format is not available），一次瞬时错误就能让整个任务失败。
        # 该探测还是全链路里**唯一仍然走 Python 原生 ssl 去碰 HLS** 的环节：dl(test=True)
        # 会另建一套精简 params，把 --hls-prefer-ffmpeg / --downloader 全部丢掉，所以正式
        # 下载已经交给 ffmpeg（决策 33），探测仍走内建下载器——2026-10-13 用户实测正是死在
        # 这里（TLS EOF 且重试必现）。本 App 的格式由用户从嗅探表显式勾选，探测那点"自动
        # 跳过不可用格式"的收益用不上。详见 DEVELOPMENT.md 决策 36。
        cmd.append('--no-check-formats')
        # HLS/DASH 分片流并发下载（决策 46）。aria2c 的 SUPPORTED_PROTOCOLS 不含
        # m3u8_native，HLS 恒由 yt-dlp 内建 hlsnative 下载器负责，`--downloader`
        # 对它无效，因此分片流提速只能靠 -N。1 是 yt-dlp 自身的默认值（顺序下载），
        # 显式传等于没传，故只在 > 1 时追加，保持「设成 1 = 关掉并发」的直观语义。
        if concurrent_fragments and concurrent_fragments > 1:
            cmd.extend(['-N', str(concurrent_fragments)])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append(url)
        
        if use_aria2c:
            aria2c_path = get_aria2c_path()
            if os.path.exists(aria2c_path):
                # 传完整路径，让 yt-dlp 直接调用项目内置 aria2c（而非 PATH 中的全局 aria2c）
                cmd.extend(['--downloader', aria2c_path])
                cmd.extend(['--downloader-args', f'aria2c:-x {aria2c_connections} -s {aria2c_connections}'])
                if log: log(f"使用 aria2c 下载（项目内置），连接数: {aria2c_connections}")
            else:
                if log: log("警告: aria2c.exe 不存在，使用默认下载器")
        
        if not log:
            cmd.append('-q')
        if remote_components:
            cmd.extend(['--remote-components', 'ejs:github'])
        if log: log(f"运行: {' '.join(cmd)}")
        # diagnostics=True：给 yt-dlp 加 -v 并把输出缓冲起来，失败时回放尾部
        # （合并/封装类失败只有 -v 才有 ffmpeg 的 stderr，见 DEVELOPMENT.md 决策 34）
        return run_ytdlp(cmd, log, process_ref, on_process_created,
                         diagnostics=True)

    @staticmethod
    def check_live_status(url, log=None):
        Functions._check_ytdlp()
        try:
            info = _run_ytdlp_json(url)
            ls = info.get('live_status', 'unknown')
            if log: log(f"[check] {info.get('title','')} -> {ls}")
            return {
                'live_status': ls,
                'title': info.get('title', ''),
                'channel': info.get('channel') or info.get('uploader') or '',
                'is_live': info.get('is_live', False),
                'duration': info.get('duration'),
                'scheduled_start_time': info.get('scheduled_start_time'),
            }
        except Exception as e:
            # yt-dlp 对未开始的 premiere 会报错（"This live event will begin..."），
            # 降级到 HTTP 解析方式
            if log: log(f"[check] yt-dlp 失败，降级到 HTTP 解析: {e}")
            vid = extract_youtube_id(url)
            if vid:
                info = fetch_youtube_metadata(vid)
                if info and info.get('title'):
                    if log: log(f"[check] HTTP 解析成功: {info.get('title')} -> {info.get('live_status')}")
                    return info
            raise RuntimeError(f"检测失败: {e}")

    @staticmethod
    def sniff_channel(url, log=None):
        Functions._check_ytdlp()
        try:
            info = _run_ytdlp_json(url, '--flat-playlist')
            entries = info.get('entries', [])
            if not entries:
                raise RuntimeError("未找到频道视频列表")
            videos = []
            for entry in entries:
                vid_url = entry.get('url') or entry.get('webpage_url') or \
                          f"https://www.youtube.com/watch?v={entry.get('id', '')}"
                videos.append({
                    'url': vid_url,
                    'title': entry.get('title', '未命名'),
                    'id': entry.get('id', ''),
                })
            if log: log(f"频道嗅探完成: {info.get('title','')}, 共 {len(videos)} 个视频")
            return {'title': info.get('title', ''), 'videos': videos}
        except Exception as e:
            raise RuntimeError(f"频道嗅探失败: {e}")

    @staticmethod
    def get_thumbnails_list(url, log=None):
        vid = extract_youtube_id(url)
        if not vid:
            if log: log(f"无法从 URL 提取 video_id: {url}")
            return []
        result = build_thumbnails(vid)
        if log: log(f"找到 {len(result)} 个封面分辨率")
        return result

    @staticmethod
    def download_thumbnail(cover_url, output_path, log=None):
        try:
            if log: log(f"下载封面: {cover_url}")
            req = urllib.request.Request(cover_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15, context=get_certifi_ssl_context()) as resp, \
                    open(output_path, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
            if log: log(f"封面已保存: {output_path}")
            return True
        except Exception as e:
            raise RuntimeError(f"封面下载失败: {e}")
