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
from pathlib import Path

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

# ---------- 从子模块导入并重新导出（保持向后兼容）----------

from sookit.core.ffmpeg_utils import (
    get_ffmpeg_path, get_ffprobe_path, check_ffmpeg,
    get_video_duration, format_duration, format_filesize,
    run_ffmpeg, run_ytdlp,
    get_aria2c_path, check_aria2c,
)
from sookit.core.youtube_utils import (
    extract_youtube_id, YOUTUBE_THUMBNAILS, build_thumbnails,
    normalize_thumbnails, fetch_youtube_metadata,
)
from sookit.core.gif_utils import convert_to_gif
from sookit.core.config import (
    get_config_path, load_config, save_config,
    load_download_config, save_download_config,
    load_close_action, save_close_action,
    load_task_complete_action, save_task_complete_action,
    THEME_COLORS, DEFAULT_THEME_COLOR, load_theme_color, save_theme_color,
    set_autostart, is_autostart,
)
from sookit.core.utils import get_certifi_ssl_context
from sookit.core.ytdlp_utils import (
    get_ytdlp_cmd, get_ytdlp_source, is_ytdlp_available,
    get_ytdlp_deno_path, build_ytdlp_cmd, download_ytdlp_bundle,
    download_ytdlp, download_deno, get_ytdlp_exe_path,
    get_ytdlp_current_version, get_deno_current_version,
    get_ytdlp_latest_version, get_deno_latest_version,
    check_ytdlp_deno_update_needed, launch_ytdlp_updater,
    check_path_ytdlp_update, check_tools_ytdlp_update,
)
from sookit.core.app_update import (
    is_newer, get_current_version, get_latest_version,
    check_latest_version, get_ignored_version, set_ignored_version,
    download_installer, RELEASES_URL,
    is_updater_available, launch_app_setup_downloader,
)

# deprecated: import 时快照，请改用 is_ytdlp_available()（动态检测，内置版安装后无需重启即生效）
YTDLP_AVAILABLE = is_ytdlp_available()


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

def sanitize_filename(name):
    """去除 Windows 文件名非法字符 \\ / : * ? \" < > |，替换为 _"""
    import re
    return re.sub(r'[\\/:*?"<>|]', '_', name).strip()


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
    def download_and_convert_gif(url, output_dir, quality,
                                 custom_params=None,
                                 use_aria2c=True, aria2c_connections=16,
                                 log=None, on_process_created=None,
                                 on_progress=None):
        """下载 X 动图并转成 GIF（组合任务：yt-dlp 下载 MP4 → ffmpeg 转码 GIF）。

        Args:
            url: X 状态链接。/video/N、/photo/N 路径后缀会被自动剥离
                （yt-dlp 对 animated_gif 条目遇 /video/N 会报
                "Media #N is not a video"，见 DEVELOPMENT.md 决策 30）
            output_dir: GIF 输出目录
            quality: GifQuality 四档之一（custom_params 非空时被忽略）
            custom_params: GifCustomParams（自定义档参数），None 表示预设档
            use_aria2c / aria2c_connections: 传给 yt-dlp 的下载器选项
            log / on_process_created / on_progress: Sookit 任务约定回调
                （TaskWorker GIF 分支固定传入这三个 kwargs）
        只处理动图：先按 `_looks_like_animated` 判定 playlist 各条目，用
        `--playlist-items` 只下载动图条目（同一条推文里的普通视频既不下载也不
        转码）；整条链接不含动图时直接抛错（任务失败并提示）。
        返回最终 GIF 路径：单文件 str，含多个动图时为 list。

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

        # ---- ① 条目识别：只挑动图，跳过同一条推文里的普通视频 ----
        # （实测反馈：推文「2 动图 + 1 个 4K 视频」会把视频也转成 GIF）
        if log:
            log("[GIF] ① 正在识别动图条目（yt-dlp）...")
        try:
            info = _run_ytdlp_json(clean_url)
        except Exception as e:
            raise RuntimeError(f"识别动图条目失败: {e}")
        entries = [e for e in (info.get('entries') or []) if e] or [info]
        animated = [i + 1 for i, e in enumerate(entries)
                    if _looks_like_animated(e)]
        if not animated:
            raise RuntimeError("该链接没有动图（推文只含普通视频或图片），已跳过")
        extra_args = None
        if len(animated) < len(entries):
            extra_args = ['--playlist-items',
                          ','.join(str(i) for i in animated)]
            if log:
                log(f"[GIF] 共 {len(entries)} 个条目，其中动图 {len(animated)} 个，"
                    f"已跳过 {len(entries) - len(animated)} 个普通视频")
        elif log:
            log(f"[GIF] 共 {len(entries)} 个条目，全部为动图")

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
            vcodec = f.get('vcodec', 'none')
            acodec = f.get('acodec', 'none')
            if vcodec != 'none' and acodec != 'none':
                fmt_type = '视频+音频'
            elif vcodec != 'none':
                fmt_type = '视频流'
            elif acodec != 'none':
                fmt_type = '音频流'
            else:
                fmt_type = '其他'

            height = f.get('height', 0) or 0
            tbr = f.get('tbr', 0) or 0
            if fmt_type == '视频流' and height:
                quality = f"{height}p"
                if f.get('fps'):
                    quality += f" {int(f.get('fps', 0))}fps"
            elif fmt_type == '音频流':
                quality = f"{int(tbr)}kbps" if tbr else ''
            elif fmt_type == '视频+音频':
                quality = f"{height}p" if height else f"{int(tbr)}kbps"
            else:
                quality = str(f.get('format_note', ''))

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
                'vcodec': vcodec.split('.')[0] if vcodec != 'none' else '',
                'acodec': acodec.split('.')[0] if acodec != 'none' else '',
                'size_str': size_str,
                'filesize': filesize,
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
    def fetch_media_meta(url, log=None):
        """取一条媒体的卡片元数据：title / channel / duration / cover_url。

        与 sniff_youtube 的差异（用于任务队列卡片显示，见 DEVELOPMENT.md 决策 30）：
        - **不加 `--no-playlist`**：X 单推文多视频时 yt-dlp 返回 playlist
          （twitter extractor 对多条目推文恒返回 playlist，`--no-playlist` 无效），
          顶层只有 title/channel，duration 与 thumbnails 都在 entries 里；
        - 不构建 formats 列表（卡片只需要标题/频道/时长/封面）。

        playlist 形态：title/channel 取顶层（推文文本 / 作者），duration 与缩略图取
        entries[0]（多条目推文的卡片以首条为代表）；非 playlist 形态全取顶层。
        缩略图分流口径与 sniff_youtube 一致（共用 `_thumbnails_for`）。
        """
        Functions._check_ytdlp()
        info = _run_ytdlp_json(url)

        entries = [e for e in (info.get('entries') or []) if e]
        if entries:
            first = entries[0]
            title = info.get('title', '') or first.get('title', '')
            channel = ((info.get('channel', '') or info.get('uploader', ''))
                       or first.get('channel', '') or first.get('uploader', ''))
            duration = first.get('duration') or 0
            valid_thumbs = _thumbnails_for(first)
        else:
            title = info.get('title', '')
            channel = info.get('channel', '') or info.get('uploader', '')
            duration = info.get('duration') or 0
            valid_thumbs = _thumbnails_for(info)

        cover_url = ((valid_thumbs[0]['url'] if valid_thumbs else '')
                     or info.get('thumbnail', ''))
        if log:
            log(f"元数据获取完成: {title}, {len(valid_thumbs)} 个封面")
        return {
            'title': title,
            'channel': channel,
            'duration': duration,
            'cover_url': cover_url,
        }

    @staticmethod
    def download_youtube(url, format_spec, output_dir, remote_components,
                        use_aria2c=True, aria2c_connections=16,
                        log=None, process_ref=None, on_process_created=None,
                        workspace=None, extra_args=None):
        """下载视频/动图到 output_dir（workspace 非空时先下到 workspace）。

        extra_args: 额外的 yt-dlp 参数（如 `--playlist-items 1,2` 只挑部分条目），
            在 URL 之前追加；默认 None 不追加（保持既有下载任务行为不变）。
        """
        Functions._check_ytdlp()
        # workspace 非空时输出到独立临时目录（下载完成后由 Sookit 移动到 output_dir）
        out = workspace or output_dir
        output_template = os.path.join(out, '%(title)s.%(ext)s')
        cmd = build_ytdlp_cmd('-f', format_spec, '-o', output_template, '--newline',
                              '--no-overwrites')
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
        return run_ytdlp(cmd, log, process_ref, on_process_created)

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
