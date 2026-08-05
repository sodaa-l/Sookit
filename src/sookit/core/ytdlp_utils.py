"""
core/ytdlp_utils.py
yt-dlp 集中管理：来源解析、命令组装、内置安装与更新。

设计原则：
- PATH 中的全局 yt-dlp 优先（尊重用户自行管理，如 scoop/pipx 安装）。
- 未检测到 PATH 版本时，回退到项目内置 tools/yt-dlp/yt-dlp.exe。
- 内置版本捆绑 EJS 求解器脚本，需配套 Deno 运行时（tools/yt-dlp/deno.exe）。
  调用时通过 --js-runtimes deno:<绝对路径> 显式指定，开箱即用支持 YouTube JS 挑战。
- 下载安装/更新全部写入项目 tools/ 目录，不修改 PATH、不写注册表。
"""

import os
import shutil
import zipfile
import urllib.error
import urllib.request
from pathlib import Path
from shutil import which as _which

from sookit.paths import get_tools_dir

# ---------- 下载源 ----------
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


# ---------- 路径 ----------

def _ytdlp_dir() -> Path:
    return get_tools_dir() / "yt-dlp"


def get_ytdlp_exe_path() -> Path:
    """项目内置 yt-dlp 可执行文件路径（tools/yt-dlp/yt-dlp.exe）"""
    return _ytdlp_dir() / "yt-dlp.exe"


def get_ytdlp_deno_path() -> Path | None:
    """内置 Deno 运行时路径（tools/yt-dlp/deno.exe），不存在返回 None"""
    p = _ytdlp_dir() / "deno.exe"
    return p if p.is_file() else None


# ---------- 来源解析 ----------

def get_ytdlp_cmd() -> list[str] | None:
    """返回当前生效的 yt-dlp 命令前缀（PATH 优先 → tools/ 回退），均不可用返回 None"""
    exe = _which("yt-dlp")
    if exe:
        return [exe]
    p = get_ytdlp_exe_path()
    if p.is_file():
        return [str(p)]
    return None


def get_ytdlp_source() -> str | None:
    """yt-dlp 来源: 'path' | 'tools' | None"""
    if _which("yt-dlp"):
        return "path"
    if get_ytdlp_exe_path().is_file():
        return "tools"
    return None


def is_ytdlp_available() -> bool:
    """当前是否有可用的 yt-dlp（PATH 或内置均可）"""
    return get_ytdlp_cmd() is not None


# ---------- 命令组装 ----------

def build_ytdlp_cmd(*args: str) -> list[str]:
    """组装 yt-dlp 命令列表。

    内置来源且 deno.exe 存在时，自动追加 --js-runtimes deno:<绝对路径>，
    确保 YouTube JavaScript 挑战求解器可用；PATH 来源不干预（由用户自管理，
    yt-dlp 会自动发现 PATH 中的 deno）。
    """
    cmd = get_ytdlp_cmd()
    if cmd is None:
        raise RuntimeError("未找到 yt-dlp，请在设置页下载安装")
    extra = []
    if get_ytdlp_source() == "tools":
        deno = get_ytdlp_deno_path()
        if deno is not None:
            extra = ["--js-runtimes", f"deno:{deno}"]
    return cmd + extra + list(args)


# ---------- 下载安装 ----------

def _format_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _download_file(url: str, dest: Path, label: str, progress_cb=None) -> None:
    """流式下载到临时文件，完成后 os.replace 原子替换。

    progress_cb(text) 进度回调，text 形如 "下载 yt-dlp.exe — 23% (4.0 MB / 17.3 MB)"。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers=_UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total:
                        pct = downloaded * 100 // total
                        progress_cb(
                            f"{label} — {pct}% ({_format_size(downloaded)} / {_format_size(total)})"
                        )
        os.replace(tmp, dest)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _extract_deno(zip_path: Path) -> None:
    """从 deno zip 中仅解出 deno.exe 到同目录"""
    dest_dir = zip_path.parent
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if os.path.basename(name).lower() == "deno.exe":
                with zf.open(name) as src, open(dest_dir / "deno.exe", "wb") as dst:
                    shutil.copyfileobj(src, dst)
                return
    raise RuntimeError("Deno 压缩包中未找到 deno.exe")


def download_ytdlp_bundle(progress_cb=None) -> Path:
    """下载安装/更新内置 yt-dlp 到 tools/yt-dlp/。

    - 总是下载最新 yt-dlp.exe 覆盖（官方自包含二进制，捆绑 EJS 求解器脚本）
    - deno.exe 缺失时才下载 Deno 运行时（zip 解压仅取 deno.exe），已存在则保留
    - 下载到临时文件 + os.replace 原子替换，避免半成品损坏
    - progress_cb(text) 每块下载进度回显；解压阶段回调 "正在解压 Deno…"

    网络/解压失败抛 RuntimeError（含可读信息），由 UI 层反馈。
    """
    try:
        exe_path = get_ytdlp_exe_path()
        _download_file(YTDLP_URL, exe_path, "下载 yt-dlp.exe", progress_cb)
        if get_ytdlp_deno_path() is None:
            zip_path = _ytdlp_dir() / "deno.zip"
            if progress_cb:
                progress_cb("正在下载 Deno 运行时…")
            try:
                _download_file(DENO_URL, zip_path, "下载 Deno", progress_cb)
                if progress_cb:
                    progress_cb("正在解压 Deno…")
                _extract_deno(zip_path)
            finally:
                zip_path.unlink(missing_ok=True)
        return exe_path
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络下载失败: {e.reason}") from e
    except OSError as e:
        raise RuntimeError(f"下载失败: {e}") from e
    except zipfile.BadZipFile as e:
        raise RuntimeError(f"Deno 压缩包损坏: {e}") from e
