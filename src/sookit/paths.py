"""
paths.py
统一资源路径定位：
- config/ 运行时数据、tools/ 二进制：位于项目根目录（PROJECT_ROOT）
- 应用图标（960x960.png）：随代码包走，位于 src/sookit/assets/

未来如需打包分发，只需修改 get_data_dir()/get_tools_dir()/get_icon_path()
的默认值（如迁移到 %APPDATA%），其余代码零改动。
"""

import os
from pathlib import Path

# 项目根目录：src/sookit/paths.py 向上两级
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def get_data_dir() -> Path:
    """运行时数据目录（config.json、completed_tasks.json、covers/ 等可写数据）"""
    return Path(os.environ.get("VIDEOTOOLBOX_DATA_DIR", PROJECT_ROOT / "config"))


def get_tools_dir() -> Path:
    """二进制工具目录（ffmpeg、aria2c 等）"""
    return Path(os.environ.get("VIDEOTOOLBOX_TOOLS_DIR", PROJECT_ROOT / "tools"))


def get_icon_path() -> Path:
    """应用图标路径（960x960.png，随代码包走）"""
    return Path(__file__).parent / "assets" / "960x960.png"
