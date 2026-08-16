"""
core/updater.py
Sookit 自动更新核心逻辑：版本比较换算、最新版本查询、安装器下载、忽略版本记忆。

设计要点：
- 复用 ytdlp_utils 的 _github_latest_tag（查询 GitHub releases/latest）与 _download_file
  （下载，已含 aria2c/urllib 双通道 + 回退 + certifi SSL context）。
- 版本比较规则（满足日期版 → 语义化版平滑过渡）：
  - 本地当前固定为 build.YYMMDD.rev 格式。
  - 远程若为 build.YYMMDD.x → 先比 YYMMDD，相同再比 rev。
  - 远程若为语义化版本号（如 1.0.0 / 0.1.0 / 0.0.1）→ 一律视为比日期版新（视为有更新）。
  - 两者皆为语义化 → 归一化后逐段数值比较。
  - 解析失败按"无更新"处理，避免误判弹窗骚扰。
- 忽略记忆：config.json 键 update.ignore_version，仅记录被忽略的远程版本号；
  出现更新的版本时重新弹窗。
"""

import re
import logging
from pathlib import Path

from sookit import APP_VERSION
from sookit.paths import get_data_dir
from sookit.core.config import load_ignored_update_version, save_ignored_update_version
from sookit.core.ytdlp_utils import _github_latest_tag, _download_file

# 分发仓库（GitHub Releases 源，由用户确认）
REPO = "sodaa-l/Sookit"

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
    """查询分发仓库最新 release tag（去 v 前缀），无 release / 失败返回空串"""
    return _github_latest_tag(REPO)


def check_latest_version() -> str | None:
    """后台线程调用：返回比当前新且未被忽略的远程版本号；无更新/查询失败返回 None。

    - 查询失败（网络错误/无 release）→ 返回 None（不打扰用户）。
    - 有新版本但等于忽略版本 → 返回 None。
    """
    latest = get_latest_version()
    if not latest:
        return None
    if not is_newer(get_current_version(), latest):
        return None
    if latest == load_ignored_update_version():
        return None
    return latest


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

def _setup_asset_url(tag: str) -> str:
    """根据 tag 组装安装器下载 URL（兼容带/不带 v 前缀的 tag）。

    tag 为 release 的纯版本号（已去 v）。URL 优先尝试 v{tag}，同时返回 {tag} 备用。
    """
    base = f"https://github.com/{REPO}/releases/download"
    name = f"{_SETUP_PREFIX}{tag}.exe"
    return base, name


def download_installer(tag: str, progress_cb=None) -> Path:
    """下载 Sookit 安装器到 %APPDATA%\\Sookit\\updates\\，返回绝对路径。

    tag 为 release 的纯版本号。兼容 tag 带/不带 v 前缀：先试 v{tag}，404/失败再试 {tag}。
    下载失败抛 RuntimeError（含可读信息），由 UI 层反馈。
    """
    if not tag:
        raise RuntimeError("版本号为空，无法下载安装器")
    dest_dir = get_data_dir() / "updates"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{_SETUP_PREFIX}{tag}.exe"
    base, name = _setup_asset_url(tag)

    last_err: Exception | None = None
    for prefix in ("v", ""):
        url = f"{base}/{prefix}{tag}/{name}"
        try:
            _logger.info("下载安装器: %s", url)
            _download_file(url, dest, f"下载安装器 {tag}", progress_cb)
            if dest.is_file() and dest.stat().st_size > 0:
                return dest
        except Exception as e:  # noqa: BLE001
            last_err = e
            # 尝试下一个 tag 前缀
    if last_err is not None:
        raise RuntimeError(f"安装器下载失败: {last_err}")
    raise RuntimeError("安装器下载失败：未生成有效文件")
