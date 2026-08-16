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

import json
import os
import re
import shutil
import subprocess
import zipfile
import urllib.error
import urllib.request
from pathlib import Path
from shutil import which as _which

from sookit.core.utils import get_certifi_ssl_context
from sookit.paths import get_ytdlp_dir
from sookit.core.config import load_download_config
from sookit.core.ffmpeg_utils import get_aria2c_path

# ---------- 下载源 ----------
YTDLP_URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
DENO_URL = "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


# ---------- 路径 ----------

def _ytdlp_dir() -> Path:
    return get_ytdlp_dir()


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
    """下载文件到临时文件，完成后 os.replace 原子替换。

    progress_cb(text) 进度回调，text 形如 "下载 yt-dlp.exe — 23% (4.0 MB / 17.3 MB)"。

    下载器选择（依据设置页下载配置）：
    - use_aria2c 开 → 优先用内置 aria2c 多连接下载（-x/-s 取 aria2c_connections），
      aria2c 不存在或下载失败时回退 urllib 单线程。
    - use_aria2c 关 → 直接用 urllib 单线程。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")

    cfg = load_download_config()
    use_aria2c = bool(cfg.get('use_aria2c', True))
    connections = int(cfg.get('aria2c_connections', 16) or 16)

    if use_aria2c:
        aria2c = get_aria2c_path()
        if os.path.exists(aria2c):
            try:
                _download_file_with_aria2c(aria2c, url, tmp, label, progress_cb, connections)
                os.replace(tmp, dest)
                return
            except Exception:
                # aria2c 下载失败 → 回退 urllib 单线程
                tmp.unlink(missing_ok=True)
                if progress_cb:
                    progress_cb(f"{label} — aria2c 下载失败，改用单线程重试…")

    _download_file_with_urllib(url, tmp, label, progress_cb)
    os.replace(tmp, dest)


def _download_file_with_aria2c(aria2c: str, url: str, tmp: Path, label: str,
                               progress_cb, connections: int) -> None:
    """用 aria2c 多连接下载。失败抛异常，由调用方决定是否回退。

    aria2c 进度输出形如：
      [#abcde 4.0MiB/17.3MiB(23%) CN:16 DL:5.0MiB]
    通过 --summary-interval 定期打印汇总行，解析其中的已下载/总量/百分比。
    """
    cmd = [
        aria2c,
        "-x", str(connections),
        "-s", str(connections),
        "-k", "1M",
        "--summary-interval", "1",
        "--console-log-level", "notice",
        "--file-allocation", "none",
        "--allow-overwrite=true",
        "--auto-file-renaming=false",
        "--dir", str(tmp.parent),
        "--out", tmp.name,
        url,
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    total = None
    done = 0
    buf = bytearray()
    _PROGRESS_RE = re.compile(r"\[#[a-zA-Z0-9]+\s+([\d.]+)([KMG]?i?B)/([\d.]+)([KMG]?i?B)\s*\((\d+)%\)")
    while True:
        chunk = proc.stdout.read(8192)
        if not chunk:
            break
        buf.extend(chunk)
        # 以 \r 或 \n 切分行（aria2c 用 \r 重绘进度条，不换行）
        while True:
            cr = buf.find(b"\r")
            nl = buf.find(b"\n")
            if cr == -1 and nl == -1:
                break
            pos = cr if nl == -1 or (cr != -1 and cr < nl) else nl
            line = bytes(buf[:pos]).decode("utf-8", errors="replace").strip()
            # 跳过 \r\n 中的 \n（若上一分隔符是 \r）
            if buf[pos:pos + 1] == b"\r" and buf[pos + 1:pos + 2] == b"\n":
                del buf[:pos + 2]
            else:
                del buf[:pos + 1]
            if not line:
                continue
            m = _PROGRESS_RE.search(line)
            if m:
                try:
                    done = _parse_size(m.group(1), m.group(2))
                    total = _parse_size(m.group(3), m.group(4))
                    pct = int(m.group(5))
                except ValueError:
                    continue
                if progress_cb and total:
                    progress_cb(f"{label} — {pct}% ({_format_size(done)} / {_format_size(total)})")
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"aria2c 退出码 {proc.returncode}")
    if not tmp.is_file() or tmp.stat().st_size == 0:
        raise RuntimeError("aria2c 未生成有效文件")


def _parse_size(num: str, unit: str) -> int:
    """aria2c 人类可读大小 → 字节数。如 ('4.0','MiB') → 4194304"""
    n = float(num)
    u = unit.lower().replace("ib", "").replace("b", "")
    mult = {"": 1, "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4}
    return int(n * mult.get(u, 1))


def _download_file_with_urllib(url: str, dest: Path, label: str, progress_cb=None) -> None:
    """urllib 单线程流式下载到临时文件（原实现）"""
    req = urllib.request.Request(url, headers=_UA)
    # 用 certifi 证书包构造 SSL context，解决 PyInstaller 打包态下
    # Python 默认证书路径（C:\Program Files\Common Files\SSL\...）不存在导致验证失败的问题
    ctx = get_certifi_ssl_context()
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            downloaded = 0
            with open(dest, "wb") as f:
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
    except Exception:
        dest.unlink(missing_ok=True)
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


# ---------- 版本查询 ----------

def _normalize_version(v: str) -> str:
    """语义化版本归一化：2026.03.17 → 2026.3.17（去各段前导零）"""
    try:
        return ".".join(str(int(p)) for p in v.strip().strip("vV").split("."))
    except Exception:
        return v.strip().strip("vV")


def _github_latest_tag(repo: str) -> str:
    """查 GitHub 仓库 releases/latest 的 tag（去前缀 v），失败返回空串"""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    req = urllib.request.Request(url, headers={"User-Agent": "Sookit"})
    try:
        with urllib.request.urlopen(req, timeout=10, context=get_certifi_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        return data.get("tag_name", "").strip("vV")
    except Exception:
        return ""


def get_ytdlp_latest_version() -> str:
    """GitHub 上 yt-dlp 最新发布版本号（无前缀 v），失败返回空串"""
    return _github_latest_tag("yt-dlp/yt-dlp")


def get_deno_latest_version() -> str:
    """GitHub 上 Deno 最新发布版本号（无前缀 v），失败返回空串"""
    return _github_latest_tag("denoland/deno")


def _run_version_cmd(exe: Path, pattern: str) -> str:
    """运行 <exe> --version 取版本号，失败返回空串。
    pattern 形如 deno 版本正则，取第一个捕获组；yt-dlp 直接取第一非空行。
    """
    try:
        r = subprocess.run(
            [str(exe), "--version"], capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except Exception:
        return ""
    raw = (r.stdout + "\n" + r.stderr).strip()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("WARNING"):
            continue
        if pattern:
            import re
            m = re.search(pattern, line)
            if m:
                return m.group(1)
        else:
            return line
    return ""


def get_ytdlp_current_version() -> str:
    """内置 yt-dlp 当前版本号，不存在或失败返回空串"""
    exe = get_ytdlp_exe_path()
    if not exe.is_file():
        return ""
    return _run_version_cmd(exe, "")


def get_deno_current_version() -> str:
    """内置 Deno 当前版本号（deno --version 首行 'deno x.y.z'），不存在或失败返回空串"""
    exe = _ytdlp_dir() / "deno.exe"
    if not exe.is_file():
        return ""
    return _run_version_cmd(exe, r"deno\s+([\d.]+)")


def download_ytdlp(progress_cb=None, check_latest=True) -> str:
    """下载安装/更新内置 yt-dlp 到 tools/yt-dlp/（仅 yt-dlp，与 Deno 相互独立）。

    返回值：
    - "up_to_date": 已是最新版本，未执行下载
    - "updated":    已完成下载/更新

    流程（check_latest=True 时）：查本地版本 vs GitHub 最新版比对，
    已最新返回 "up_to_date"；需更新则下载。版本信息不足（未安装/查询失败）
    时按原逻辑下载。check_latest=False 强制重新下载。

    网络/下载失败抛 RuntimeError（含可读信息），由 UI 层反馈。
    """
    try:
        exe_path = get_ytdlp_exe_path()
        ytdlp_present = exe_path.is_file()
        need_update = True

        if check_latest:
            cur = get_ytdlp_current_version() if ytdlp_present else ""
            latest = get_ytdlp_latest_version()
            if cur and latest:
                # 都能拿到版本时，基于最新版判断是否需要更新
                need_update = _normalize_version(cur) < _normalize_version(latest)
            else:
                # 版本信息不足（未安装、查询失败）→ 按原逻辑下载
                need_update = True

        if need_update:
            _download_file(YTDLP_URL, exe_path, "下载 yt-dlp.exe", progress_cb)

        return "updated" if need_update else "up_to_date"
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络下载失败: {e.reason}") from e
    except OSError as e:
        raise RuntimeError(f"下载失败: {e}") from e


def download_deno(progress_cb=None, check_latest=True) -> str:
    """下载安装/更新内置 Deno 运行时到 tools/yt-dlp/（仅 Deno，与 yt-dlp 相互独立）。

    返回值：
    - "up_to_date": 已是最新版本，未执行下载
    - "updated":    已完成下载/更新

    流程（check_latest=True 时）：查本地版本 vs GitHub 最新版比对，
    已最新返回 "up_to_date"；需更新则下载并解压。Deno 缺失（首次安装）或
    版本查询失败时按原逻辑下载。check_latest=False 强制重新下载。

    网络/解压失败抛 RuntimeError（含可读信息），由 UI 层反馈。
    """
    try:
        need_update = False
        if check_latest:
            cur = get_deno_current_version()
            latest = get_deno_latest_version()
            if cur and latest:
                need_update = _normalize_version(cur) < _normalize_version(latest)
            else:
                # Deno 缺失（首次安装）或版本查询失败 → 需要下载
                need_update = True
        else:
            need_update = True

        if need_update:
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

        return "updated" if need_update else "up_to_date"
    except urllib.error.URLError as e:
        raise RuntimeError(f"网络下载失败: {e.reason}") from e
    except OSError as e:
        raise RuntimeError(f"下载失败: {e}") from e
    except zipfile.BadZipFile as e:
        raise RuntimeError(f"Deno 压缩包损坏: {e}") from e


def download_ytdlp_bundle(progress_cb=None, check_latest=True) -> str:
    """兼容包装：顺序执行 download_ytdlp 与 download_deno（各自独立判断）。

    任一组件失败会抛 RuntimeError 中断；返回 "up_to_date"（两者都最新）或
    "updated"（至少一个被更新）。
    """
    y = download_ytdlp(progress_cb, check_latest)
    d = download_deno(progress_cb, check_latest)
    if y == "up_to_date" and d == "up_to_date":
        return "up_to_date"
    return "updated"
