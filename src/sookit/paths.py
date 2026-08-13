"""
paths.py
统一资源路径定位：
- 运行时数据（config.json、completed_tasks.json）：%APPDATA%/Sookit
- 封面缓存（covers/）：%LOCALAPPDATA%/Sookit/covers
- 二进制工具（tools/）：程序目录（源码态为项目根，打包态为 exe 所在目录）
- 应用图标（960x960.png）：随代码包走，位于 src/sookit/assets/

打包分发（PyInstaller onedir）下无需改动，路径语义与源码态保持一致。
"""

import os
from pathlib import Path

# 项目根目录：src/sookit/paths.py 向上两级（源码态）
# 打包态下 __file__ 位于程序目录的 _internal，parents[2] 为程序根目录（exe 同级）
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _appdata_dir() -> Path:
    """返回 APPDATA 目录（未设置时回退到用户主目录）"""
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Sookit"


def _localappdata_dir() -> Path:
    """返回 LOCALAPPDATA 目录（未设置时回退到用户主目录）"""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "Sookit"


def get_data_dir() -> Path:
    """运行时数据目录（config.json、completed_tasks.json 等持久化数据）"""
    return Path(os.environ.get("VIDEOTOOLBOX_DATA_DIR", _appdata_dir()))


def get_cover_dir() -> Path:
    """视频封面缓存目录（可再生的临时缓存）"""
    return Path(os.environ.get("VIDEOTOOLBOX_COVER_DIR", _localappdata_dir() / "covers"))


def get_log_dir() -> Path:
    """运行日志目录（打包后 GUI 无控制台，日志落盘便于排查）"""
    return Path(os.environ.get("VIDEOTOOLBOX_LOG_DIR", _localappdata_dir() / "log"))


def get_ytdlp_dir() -> Path:
    """yt-dlp 安装目录（含 deno 运行时）。

    装到 %LOCALAPPDATA% 而非程序目录：yt-dlp 首次使用需自动下载，
    Program Files 下无写权限，故放到用户可写目录。
    """
    return Path(os.environ.get("VIDEOTOOLBOX_YTDLP_DIR", _localappdata_dir() / "tools" / "yt-dlp"))


def get_tools_dir() -> Path:
    """二进制工具目录（ffmpeg、aria2c 等）"""
    return Path(os.environ.get("VIDEOTOOLBOX_TOOLS_DIR", PROJECT_ROOT / "tools"))


def get_icon_path() -> Path:
    """应用图标路径（960x960.png，随代码包走）"""
    return Path(__file__).parent / "assets" / "960x960.png"
