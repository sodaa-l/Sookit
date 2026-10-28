"""浏览器菜单的封面下载；产物由现有任务队列交付。"""
from pathlib import Path

from sookit.core.browser_protocol import normalize_video_url
from sookit.core.ffmpeg_utils import run_ytdlp
from sookit.core.ytdlp_utils import build_ytdlp_cmd

BEST_VIDEO_FORMAT = 'bestvideo*+bestaudio/best'
_IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png', '.webp', '.avif'}


def download_best_cover(url, output_dir, remote_components, log=None,
                        on_process_created=None, workspace=None):
    """只下载最佳可用原图；独占 workspace 避免把旧封面误报为成功。"""
    url = normalize_video_url(url)
    if not workspace:
        raise ValueError('封面下载需要独立任务工作目录')
    root = Path(workspace)
    root.mkdir(parents=True, exist_ok=True)
    video_id = url.rsplit('=', 1)[1]
    basename = f'{video_id}_cover'
    cmd = build_ytdlp_cmd(
        '--ignore-config', '--no-playlist', '--skip-download', '--no-simulate',
        '--write-thumbnail', '--no-overwrites',
        '--socket-timeout', '20', '--retries', '1', '--newline',
        '-o', str(root / f'{basename}.%(ext)s'))
    if remote_components:
        cmd.extend(['--remote-components', 'ejs:github'])
    cmd.append(url)
    # skip-download 不触发 after_move；按独占目录内固定模板验收，不依赖 stdout 路径。
    # yt-dlp 只写最佳可用封面，最高档不可用时自行尝试下一档，保留原图格式。
    run_ytdlp(cmd, log, on_process_created=on_process_created, diagnostics=True)
    files = [path for path in root.glob(f'{basename}.*')
             if path.suffix.lower() in _IMAGE_SUFFIXES and path.is_file() and path.stat().st_size]
    if len(files) != 1:
        raise RuntimeError('yt-dlp 未生成唯一的非空封面文件')
    return [str(files[0])]
