"""
core/config.py
配置文件管理、主题颜色、开机自启
"""

import os
import sys
import json
import threading

from sookit.paths import get_data_dir

# 配置缓存 + 锁（并发安全）
_config_cache = None
_config_cache_dirty = False
_config_lock = threading.Lock()


# ---------- 配置文件路径 ----------

def get_config_path():
    """获取配置文件路径"""
    return str(get_data_dir() / 'config.json')


def load_config():
    """加载完整配置文件（带惰性缓存）"""
    global _config_cache
    config_path = get_config_path()
    with _config_lock:
        if _config_cache is not None:
            return _config_cache
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                _config_cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            _config_cache = {}
        return _config_cache


def save_config(config):
    """保存完整配置文件（同步写入并更新缓存）"""
    global _config_cache, _config_cache_dirty
    config_path = get_config_path()
    with _config_lock:
        _config_cache = config
        _config_cache_dirty = True
        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            _config_cache_dirty = False
        except OSError as e:
            _config_cache_dirty = False
            raise RuntimeError(f"保存配置文件失败: {e}")


# ---------- 下载配置 ----------

# 并发分片数的合法档位（决策 46）。设置页下拉只给这几档，配置层也按同一口径
# 归一，保证「UI 显示的 = 实际生效的」——否则 UI 把旧值 10 显示成 8、下载却仍用
# 10，用户看到的行为和设置对不上。
FRAGMENT_TIERS = (1, 2, 4, 8, 16)
FRAGMENT_DEFAULT = 8


def normalize_fragments(value):
    """把任意并发分片数归一到 FRAGMENT_TIERS 之一。

    规则：≤1 → 1；其余**向下取档**（不超过用户原设定，绝不静默抬高带宽占用）；
    超出上限 → 16；非整数/脏值 → 默认 8。
    """
    try:
        n = int(value)
    except (TypeError, ValueError):
        return FRAGMENT_DEFAULT
    if n <= 1:
        return 1
    for tier in reversed(FRAGMENT_TIERS):
        if n >= tier:
            return tier
    return 1


def load_download_config():
    """加载下载配置"""
    config = load_config()
    download_config = config.get('download', {})
    return {
        'concurrent_fragments': normalize_fragments(
            download_config.get('concurrent_fragments', FRAGMENT_DEFAULT)),
        'use_aria2c': download_config.get('use_aria2c', True),
        'aria2c_connections': download_config.get('aria2c_connections', 16),
    }


def save_download_config(download_config):
    """保存下载配置"""
    config = load_config()
    config['download'] = download_config
    save_config(config)


# ---------- 关闭行为配置 ----------

def load_close_action():
    """加载关闭行为配置: 0=最小化至托盘, 1=直接退出"""
    config = load_config()
    general = config.get('general', {})
    return general.get('close_action', 1)


def save_close_action(action: int):
    """保存关闭行为配置"""
    config = load_config()
    if 'general' not in config:
        config['general'] = {}
    config['general']['close_action'] = action
    save_config(config)


# ---------- 任务完成后配置 ----------

def load_task_complete_action():
    """加载任务完成后配置: 0=不操作, 1=关闭工具箱, 2=关闭计算机"""
    config = load_config()
    general = config.get('general', {})
    return general.get('task_complete_action', 0)


def save_task_complete_action(action: int):
    """保存任务完成后配置"""
    config = load_config()
    if 'general' not in config:
        config['general'] = {}
    config['general']['task_complete_action'] = action
    save_config(config)


# ---------- 系统通知配置（决策 40） ----------

def load_toast_notification_enabled():
    """加载系统通知开关: 任务完成/失败且主窗口不在焦点时弹 Windows 系统通知（默认开）"""
    config = load_config()
    general = config.get('general', {})
    return general.get('toast_notification', True)


def save_toast_notification_enabled(enabled: bool):
    """保存系统通知开关"""
    config = load_config()
    if 'general' not in config:
        config['general'] = {}
    config['general']['toast_notification'] = enabled
    save_config(config)


# ---------- 嗅探页视图偏好（决策 47） ----------

SNIFF_VIEW_DEFAULT = 'default'   # 默认视图：直接选分辨率/音质下载
SNIFF_VIEW_TABLE = 'table'       # 表格视图：逐格式勾选（原交互）


def load_sniff_view():
    """加载嗅探页上次使用的视图: 'default' | 'table'（默认「默认视图」）"""
    config = load_config()
    general = config.get('general', {})
    view = general.get('sniff_view', SNIFF_VIEW_DEFAULT)
    return view if view in (SNIFF_VIEW_DEFAULT, SNIFF_VIEW_TABLE) else SNIFF_VIEW_DEFAULT


def save_sniff_view(view: str):
    """保存嗅探页视图偏好（非法值不落盘，避免污染配置）"""
    if view not in (SNIFF_VIEW_DEFAULT, SNIFF_VIEW_TABLE):
        return
    config = load_config()
    if 'general' not in config:
        config['general'] = {}
    config['general']['sniff_view'] = view
    save_config(config)


# ---------- 主题颜色管理 ----------

THEME_COLORS = {
    "道奇蓝": "#0098ff",
    "薄荷绿": "#3A9B8A",
}

DEFAULT_THEME_COLOR = "#3A9B8A"  # 薄荷绿


def load_theme_color():
    """加载主题颜色配置"""
    config = load_config()
    qfw_config = config.get('QFluentWidgets', {})
    color = qfw_config.get('ThemeColor', DEFAULT_THEME_COLOR)
    # 移除可能的 alpha 前缀 (如 #ff0098ff -> #0098ff)
    if len(color) == 9 and color.startswith('#'):
        color = '#' + color[3:]
    return color


def save_theme_color(color):
    """保存主题颜色配置"""
    config = load_config()
    if 'QFluentWidgets' not in config:
        config['QFluentWidgets'] = {}
    # 添加 alpha 前缀以匹配 QFluentWidgets 格式
    config['QFluentWidgets']['ThemeColor'] = '#ff' + color[1:]
    save_config(config)


# ---------- 开机自启管理 ----------

import winreg

_AUTOSTART_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_AUTOSTART_APP_KEY = "Sookit"


def set_autostart(enable: bool):
    """启用/禁用开机自启（Windows 注册表）"""
    if getattr(sys, 'frozen', False):
        exe = sys.executable  # 打包 exe 场景
        cmd = f'"{exe}" --silent'
    else:
        exe = sys.executable  # 源码/editable 场景，用 python -m 启动保证包可定位
        cmd = f'"{exe}" -m sookit --silent'
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY_PATH, 0, winreg.KEY_SET_VALUE)
    if enable:
        winreg.SetValueEx(key, _AUTOSTART_APP_KEY, 0, winreg.REG_SZ, cmd)
    else:
        try:
            winreg.DeleteValue(key, _AUTOSTART_APP_KEY)
        except FileNotFoundError:
            pass
    winreg.CloseKey(key)
    # 同步写入 config.json
    config = load_config()
    if 'general' not in config:
        config['general'] = {}
    config['general']['autostart'] = enable
    save_config(config)


def is_autostart() -> bool:
    """检查开机自启是否已启用"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY_PATH, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, _AUTOSTART_APP_KEY)
        winreg.CloseKey(key)
        return True
    except (FileNotFoundError, OSError):
        return False


# ---------- 自动更新忽略版本 ----------

def load_ignored_update_version():
    """加载用户忽略的更新版本号（'ignore_update_version' 键），未设置返回空串"""
    config = load_config()
    update = config.get('update', {})
    return update.get('ignore_version', '')


def save_ignored_update_version(version: str):
    """保存用户忽略的更新版本号（'update.ignore_version' 键）"""
    config = load_config()
    if 'update' not in config:
        config['update'] = {}
    config['update']['ignore_version'] = version
    save_config(config)
