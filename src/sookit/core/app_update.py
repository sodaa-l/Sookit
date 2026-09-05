"""
core/app_update.py（原 core/updater.py，2026-09-05 更名避免与顶层 updater.py 混淆）
Sookit 自动更新核心逻辑：版本比较换算、最新版本查询、安装器下载、忽略版本记忆。

设计要点：
- 复用 ytdlp_utils 的 _version_from_latest_url（经 releases/latest HTML 重定向解析 tag，
  绕开 api.github.com 限流）与 _download_file（下载，已含 aria2c/urllib 双通道 + 回退 + certifi SSL context）。
- 版本比较规则（满足日期版 → 语义化版平滑过渡）：
  - 本地当前固定为 build.YYMMDD.rev 格式。
  - 远程若为 build.YYMMDD.x → 先比 YYMMDD，相同再比 rev。
  - 远程若为语义化版本号（如 1.0.0 / 0.1.0 / 0.0.1）→ 一律视为比日期版新（视为有更新）。
  - 两者皆为语义化 → 归一化后逐段数值比较。
  - 解析失败按"无更新"处理，避免误判弹窗骚扰。
- 忽略记忆：config.json 键 update.ignore_version，仅记录被忽略的远程版本号；
  出现更新的版本时重新弹窗。
"""

import json
import os
import re
import logging
import subprocess
import time
import urllib.request
from pathlib import Path

from sookit import APP_VERSION
from sookit.paths import get_data_dir
from sookit.core.config import load_ignored_update_version, save_ignored_update_version
from sookit.core.ytdlp_utils import (
    _version_from_latest_url, _download_file,
    get_certifi_ssl_context, _UA, DownloadCancelled,
)

# 分发仓库（GitHub Releases 源，由用户确认）
REPO = "sodaa-l/Sookit"

# Releases 最新版页面（检查失败时引导用户手动下载）
RELEASES_URL = f"https://github.com/{REPO}/releases/latest"

# 安装器文件名前缀（与 packaging/installer.iss 的 OutputBaseFilename 一致）
_SETUP_PREFIX = "Sookit-Setup-"

_logger = logging.getLogger("Sookit.updater")

# 日期版：build.YYMMDD.rev（如 build.260816.2）
_DATE_RE = re.compile(r"^build\.(\d{6})(?:\.(\d+))?$", re.IGNORECASE)
# 语义化版：1.0.0 / 0.1.0 / 0.0.1（纯数字点分，可带 v 前缀，已在 _github_latest_tag 去除）
_SEMVER_RE = re.compile(r"^\d+(?:\.\d+)*$")


def get_current_version() -> str:
    """当前本地版本号（读 APP_VERSION，如 build.260816.2）"""
    return APP_VERSION


def _is_semver(v: str) -> bool:
    return bool(_SEMVER_RE.match(v.strip()))


def _is_date_version(v: str) -> bool:
    return bool(_DATE_RE.match(v.strip()))


def _semver_tuple(v: str) -> tuple:
    """语义化版本 → 数值元组（如 '1.2.3' → (1,2,3)）。解析失败返回空元组"""
    try:
        return tuple(int(p) for p in v.strip().split("."))
    except ValueError:
        return ()


def is_newer(local_version: str, remote_version: str) -> bool:
    """判断 remote_version 是否比 local_version 新。

    返回 True 表示有更新。解析失败一律返回 False（保守，避免误弹窗）。
    """
    local = (local_version or "").strip()
    remote = (remote_version or "").strip()
    if not local or not remote:
        return False

    # 远程是语义化版本号 → 一律视为比日期版新（支持日期版平滑升级到语义化版）
    if _is_semver(remote):
        # 若本地也是语义化 → 正常比较；否则（本地是日期版或未知）→ 视为有更新
        if _is_semver(local):
            lt, rt = _semver_tuple(local), _semver_tuple(remote)
            if lt and rt:
                return rt > lt
            return False
        # 本地是日期版/未知格式 → 语义化版视为更新
        return True

    # 远程是日期版
    if _is_date_version(remote):
        ml = _DATE_RE.match(local)
        mr = _DATE_RE.match(remote)
        if ml and mr:
            ly, ry = int(ml.group(1)), int(mr.group(1))
            if ry != ly:
                return ry > ly
            # YYMMDD 相同 → 比较 rev（缺省 rev 按 0 处理）
            lrev = int(ml.group(2) or 0)
            rrev = int(mr.group(2) or 0)
            return rrev > lrev
        return False

    # 两者皆非语义化也非日期版（未知格式）→ 保守按无更新
    return False


def get_latest_version() -> str:
    """查询分发仓库最新 release tag（去 v 前缀），无 release / 失败返回空串。

    走 releases/latest 的 HTML 重定向（302 → /releases/tag/<版本>）解析 tag，
    绕开 api.github.com 的匿名限流（403 后 API 查不到）。失败返回空串。
    """
    return _version_from_latest_url(f"https://github.com/{REPO}/releases/latest")


def check_latest_version() -> tuple[str, str]:
    """后台线程调用：返回 (status, version)，四态区分检查结果。

    status:
    - "newer":   有新版本且未被忽略，version 为远程版本号
    - "ignored": 有新版本但等于用户忽略的版本，version 为该版本号
    - "latest":  无更新，version 为空串
    - "failed":  查询失败（网络错误/无 release），version 为空串

    注意 ignored 与 failed 的处理策略由 UI 层决定（自动检查 vs 手动检查），
    本函数只如实上报。
    """
    latest = get_latest_version()
    if not latest:
        return ("failed", "")
    if not is_newer(get_current_version(), latest):
        return ("latest", "")
    if latest == load_ignored_update_version():
        return ("ignored", latest)
    return ("newer", latest)


def get_ignored_version() -> str:
    """当前被忽略的版本号（config.json），未设置返回空串"""
    return load_ignored_update_version()


def set_ignored_version(version: str):
    """记录被忽略的版本号（config.json），持久化"""
    try:
        save_ignored_update_version(version)
    except Exception:
        _logger.warning("忽略版本持久化失败: %s", version, exc_info=True)


# ---------- 安装器下载 ----------

def _asset_version(tag: str) -> str:
    """安装器资产名/本地文件名用版本号：去掉 v 与 build. 前缀（build.260830.1 → 260830.1）。

    发布 tag 使用带 build. 前缀的完整版本号（与 APP_VERSION 一致，保证 is_newer 的日期版
    比较生效）；而安装包产物名由 installer.iss 的 MyAppVersion 生成、不带 build. 前缀，
    故下载时须剥离后再拼资产名与本地文件名。
    """
    clean = (tag or "").lstrip("vV")
    if clean.lower().startswith("build."):
        clean = clean[len("build."):]
    return clean


def _setup_asset_url(tag: str) -> tuple[str, str]:
    """根据 tag 组装安装器下载 URL。

    tag 为 release 版本号（可带 build. / v 前缀）。URL 优先尝试 v{tag}，同时返回 {tag} 备用；
    资产名剥离 build. 前缀（与 installer.iss 的 OutputBaseFilename 一致）。
    """
    base = f"https://github.com/{REPO}/releases/download"
    name = f"{_SETUP_PREFIX}{_asset_version(tag)}.exe"
    return base, name


def _file_sha256(path: Path) -> str:
    """计算文件 sha256（流式读取，返回小写 hex）"""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch_expected_sha256(base: str, tag: str, name: str) -> str | None:
    """拉取 release 资产 <name>.sha256，返回期望摘要（小写 hex）；获取失败返回 None。

    哈希文件由 CI 打包时生成（"<hex>  <文件名>"，与 sha256sum 兼容），与安装器资产同目录。
    获取失败（网络抖动/老版本 release 未上传）降级为不校验——不让哈希文件的偶发
    缺失导致更新整体不可用，仅记警告日志。
    """
    for prefix in ("v", ""):
        url = f"{base}/{prefix}{tag}/{name}.sha256"
        try:
            _logger.info("获取安装器 sha256: %s", url)
            ctx = get_certifi_ssl_context()
            req = urllib.request.Request(url, headers=_UA)
            with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
                text = resp.read(4096).decode("ascii", errors="ignore")
            m = re.search(r"\b[0-9a-fA-F]{64}\b", text)
            if m:
                return m.group(0).lower()
            _logger.warning("sha256 文件内容无法解析: %r", text[:200])
        except Exception as e:  # noqa: BLE001
            _logger.warning("获取 sha256 失败（%s），降级为不校验: %s", url, e)
    return None


def download_installer(tag: str, progress_cb=None, cancel_cb=None, on_proc=None) -> Path:
    """下载 Sookit 安装器到 %APPDATA%\\Sookit\\updates\\，返回绝对路径。

    tag 为 release 版本号（可带 build. / v 前缀）。先试 v{tag}，404/失败再试 {tag}；
    本地文件名与资产名剥离 build. 前缀（如 Sookit-Setup-260830.1.exe）。
    下载失败抛 RuntimeError（含可读信息），由 UI 层反馈。

    完整性保障：
    - sha256 校验：拉取 release 同目录的 <name>.sha256 与本地文件比对，
      新下载不匹配 → 删除并抛错；期望哈希不可得时降级为不校验（仅 size>0）。
    - skip-if-exists：dest 已存在且哈希匹配（或哈希不可得且 size>0）→ 直接返回，
      不重复下载。dest 由 _download_file 原子替换生成，存在即完整；文件名自带
      版本号，天然与旧版本隔离。此机制支撑"主程序中途退出后下次点下载直接接上"。
    - cancel_cb / on_proc 透传给 _download_file（updater.exe 取消按钮与进程终止兜底）。
    """
    if not tag:
        raise RuntimeError("版本号为空，无法下载安装器")
    dest_dir = get_data_dir() / "updates"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{_SETUP_PREFIX}{_asset_version(tag)}.exe"
    base, name = _setup_asset_url(tag)
    expected = _fetch_expected_sha256(base, tag, name)

    def _hash_ok() -> bool:
        """dest 的 sha256 是否与期望一致；无期望哈希时按 size>0 判定（降级）"""
        if expected is None:
            return dest.is_file() and dest.stat().st_size > 0
        return dest.is_file() and _file_sha256(dest) == expected

    # skip-if-exists：上次下载的成品直接复用（哈希不匹配则删掉重下，修复同 tag 重发旧内容）
    if dest.is_file():
        if _hash_ok():
            _logger.info("安装器已存在且校验通过，跳过下载: %s", dest)
            return dest
        _logger.warning("已存在的安装器与期望 sha256 不符，删除后重新下载: %s", dest)
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass

    last_err: Exception | None = None
    for prefix in ("v", ""):
        url = f"{base}/{prefix}{tag}/{name}"
        try:
            _logger.info("下载安装器: %s", url)
            _download_file(url, dest, f"下载安装器 {tag}", progress_cb,
                           cancel_cb=cancel_cb, on_proc=on_proc)
            if _hash_ok():
                return dest
            # 成功落盘但校验失败：删除坏文件并中止（重试另一前缀无意义，同一文件）
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass
            raise RuntimeError("安装器 sha256 校验失败，已删除损坏文件")
        except DownloadCancelled:
            raise
        except Exception as e:  # noqa: BLE001
            last_err = e
            # 尝试下一个 tag 前缀
    if last_err is not None:
        raise RuntimeError(f"安装器下载失败: {last_err}")
    raise RuntimeError("安装器下载失败：未生成有效文件")


def app_setup_result_path() -> str:
    """生成 app 安装包下载任务的结果文件路径（%APPDATA%\\Sookit 下唯一文件名）。

    与 yt-dlp 任务（结果放程序目录、供提权 updater 写）不同：app 安装包下载用
    非提权 updater.exe，目标与结果都在用户目录，两边可读可写。
    唯一名(时间戳+pid+随机)防并发冲突。
    """
    import random
    import time as _t
    unique = f"{int(_t.time() * 1000)}_{os.getpid()}_{random.randint(0, 99999)}"
    return str(get_data_dir() / f".app_setup_result_{unique}.json")


def _try_read_app_setup_result(result_path: str) -> dict | None:
    """读取 app 下载任务结果文件；不存在返回 None；读取后清理（损坏也清理并按失败返回）"""
    p = Path(result_path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        data = {"task": "app_setup", "ok": False, "status": "failed",
                "error": "读取下载器结果失败", "path": ""}
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass
    return data


def is_updater_available() -> bool:
    """updater.exe 是否存在（打包态 True；源码运行态 False，UI 层对下载请求静默处理）"""
    from sookit.core.ytdlp_utils import _updater_exe
    return _updater_exe().is_file()


def launch_app_setup_downloader(tag: str, timeout: float = 3600) -> tuple:
    """Sookit 侧：调起独立 updater.exe（非提权）下载 Sookit 安装包并等待结果。

    返回 (ok, status, error, path)。status:
    - "ok"        → path 为安装器绝对路径（已通过 sha256 校验）
    - "cancelled" → 用户在下载器小窗取消
    - "no_updater"→ updater.exe 不存在（源码运行态，UI 层静默处理）
    - "failed"    → 下载/校验失败或下载器崩溃，error 为可读原因

    流程：生成唯一结果路径（%APPDATA%）→ Popen 非提权启动
    updater.exe(--app-setup <tag> <结果路径>) → 轮询「结果文件 + updater 进程存活」，
    模式与 launch_ytdlp_updater 一致。主程序中途退出不影响下载（独立进程），
    结果文件无人消费时由 skip-if-exists 在下次下载时接上。
    """
    from sookit.core.ytdlp_utils import _updater_exe, _updater_process_alive

    result_path = app_setup_result_path()
    exe = _updater_exe()
    if not exe.is_file():
        _logger.warning("updater.exe 不存在（%s），无法调起下载", exe)
        return (False, "no_updater", "", "")
    try:
        subprocess.Popen(
            [str(exe), "--app-setup", str(tag), result_path],
            cwd=str(exe.parent),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except Exception as e:  # noqa: BLE001
        return (False, "failed", f"启动下载器失败: {e}", "")

    deadline = time.time() + timeout
    while time.time() < deadline:
        data = _try_read_app_setup_result(result_path)
        if data is not None:
            ok = bool(data.get("ok"))
            status = data.get("status", "failed" if not ok else "ok")
            return (ok, status, data.get("error", ""), data.get("path", ""))
        if not _updater_process_alive():
            # 进程已退出：最后再查一次结果（退出瞬间可能刚好写出），仍无则判失败
            data = _try_read_app_setup_result(result_path)
            if data is not None:
                ok = bool(data.get("ok"))
                status = data.get("status", "failed" if not ok else "ok")
                return (ok, status, data.get("error", ""), data.get("path", ""))
            return (False, "failed", "下载器已退出但未返回结果（可能被强行终止）", "")
        time.sleep(0.5)
    _try_read_app_setup_result(result_path)  # 超时也清理残留结果文件
    return (False, "failed", f"等待下载器返回超时（{int(timeout // 60)} 分钟）", "")
