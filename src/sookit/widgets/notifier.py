"""
widgets/notifier.py
Windows 系统通知封装（见 DEVELOPMENT.md 决策 40）

主窗口不在焦点时，任务完成/失败通过 windows-toasts 弹系统 Toast：
- 交互通知必须用 InteractableWindowsToaster（按钮能力依赖可识别 AUMID）；
  默认 AUMID 为系统 Command Prompt，来源名以 attribution text 显示 "Sookit"
- ToastButton 自身无回调：按钮点击与正文点击统一走 Toast.on_activated，
  按 arguments 字段区分（正文点击 arguments 为 toast tag）
- on_activated 在 WinRT 回调线程触发，经 _ToastSignalBridge 的 Qt 信号
  转回主线程（跨线程 emit 自动走队列连接）
- 初始化/发送任何异常一律静默降级，通知失败不影响主流程
"""
from __future__ import annotations

import logging
import os

from PyQt6.QtCore import QObject, pyqtSignal

from sookit.core.config import load_toast_notification_enabled

logger = logging.getLogger("Sookit.notifier")

# 按钮 arguments 约定（on_activated 按 arguments 区分点击来源）
_OPEN_FOLDER_ARG = "open_folder"

# 自有 AUMID（HKCU 注册表注册，Electron/Tauri 同款方案）。
# 注册后通知显示「Sookit」品牌名+图标，且横幅/通知中心不被降级；
# 注册失败退回库默认的 cmd 借用身份（横幅可能被系统压制，见决策 40）。
_AUMID = "Sookit"
_AUMID_REG_KEY = r"Software\Classes\AppUserModelId\Sookit"

_toaster = None          # InteractableWindowsToaster 惰性单例（None=尚未初始化）
_toaster_failed = False  # 初始化失败标记（失败后本会话不再重试，避免刷异常日志）

_bridge = None


def _register_sookit_aumid() -> bool:
    """在 HKCU 注册自有 AUMID（幂等，已存在则覆盖同样的值）。成功返回 True"""
    try:
        import winreg

        from sookit.paths import get_icon_path
        key = winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _AUMID_REG_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, _AUMID)
            try:
                icon = get_icon_path()
                if icon:
                    winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon))
            except Exception:  # noqa: BLE001 图标缺失不影响通知功能
                pass
        finally:
            winreg.CloseKey(key)
        return True
    except OSError as e:
        logger.warning("注册 Sookit AUMID 失败，将退回系统借用身份: %s", e)
        return False


class _ToastSignalBridge(QObject):
    """把 WinRT 回调线程的事件转回 Qt 主线程"""

    open_path_requested = pyqtSignal(str)  # 请求打开文件夹
    activate_requested = pyqtSignal()      # 请求显示主窗口


def get_toast_bridge() -> _ToastSignalBridge:
    """获取全局信号桥（主窗口连接后接收 WinRT 回调线程的请求）"""
    global _bridge
    if _bridge is None:
        _bridge = _ToastSignalBridge()
    return _bridge


def _get_toaster():
    """惰性创建交互式 Toaster；失败返回 None 并记住，不再重试"""
    global _toaster, _toaster_failed
    if _toaster is not None or _toaster_failed:
        return _toaster
    try:
        from windows_toasts import InteractableWindowsToaster
        if _register_sookit_aumid():
            # 自有 AUMID：品牌名/图标由注册表 DisplayName/IconUri 提供
            _toaster = InteractableWindowsToaster("Sookit", notifierAUMID=_AUMID)
        else:
            # 兜底：库默认借用系统 cmd 身份，attribution text 补偿显示 "Sookit"
            _toaster = InteractableWindowsToaster("Sookit")
    except Exception as e:  # noqa: BLE001 WinRT 初始化失败（如无通知服务）一律降级
        _toaster_failed = True
        logger.warning("系统通知初始化失败，本次会话不再尝试: %s", e)
    return _toaster


def _resolve_open_dir(task) -> str:
    """「打开文件夹」按钮的目标目录：优先 output_dir，兜底产物文件所在目录"""
    output_dir = getattr(task, "output_dir", "") or ""
    if output_dir and os.path.isdir(output_dir):
        return output_dir
    for f in (getattr(task, "output_files", None) or []):
        d = os.path.dirname(f)
        if d and os.path.isdir(d):
            return d
    return ""


def _dispatch_activation(bridge: _ToastSignalBridge, args, open_dir: str):
    """on_activated 回调（WinRT 线程）：点「打开文件夹」→ 开目录；点正文 → 拉起主窗口"""
    arguments = getattr(args, "arguments", None)
    if arguments == _OPEN_FOLDER_ARG and open_dir:
        bridge.open_path_requested.emit(open_dir)
    else:
        bridge.activate_requested.emit()


def _show_task_toast(task, ok: bool) -> bool:
    """弹任务结果通知。返回是否已弹出（开关关闭/初始化失败/发送异常均返回 False）"""
    if not load_toast_notification_enabled():
        return False
    title = (getattr(task, "title", "") or "").strip() or "任务"
    try:
        from windows_toasts import Toast, ToastButton

        toaster = _get_toaster()
        if toaster is None:
            return False

        text_fields = ["任务完成" if ok else "任务失败", title]
        if not ok:
            err = (getattr(task, "error", "") or "").strip()
            if err:
                text_fields.append(err[:200])

        # 仅成功时带「打开文件夹」按钮（失败现场可能是已清理的 workspace/空目录）
        actions = []
        if ok:
            open_dir = _resolve_open_dir(task)
            if open_dir:
                actions.append(ToastButton(content="打开文件夹", arguments=_OPEN_FOLDER_ARG))
        else:
            open_dir = ""

        bridge = get_toast_bridge()
        toast = Toast(
            text_fields=text_fields,
            actions=actions,
            on_activated=lambda args: _dispatch_activation(bridge, args, open_dir),
        )
        toaster.show_toast(toast)
        return True
    except Exception as e:  # noqa: BLE001 通知失败不影响主流程
        logger.warning("系统通知发送失败: %s", e)
        return False


def notify_task_completed(task) -> bool:
    """任务完成通知（带「打开文件夹」按钮）"""
    return _show_task_toast(task, ok=True)


def notify_task_failed(task) -> bool:
    """任务失败通知（正文附错误摘要）"""
    return _show_task_toast(task, ok=False)


def _show_plain_toast(text_fields: list) -> bool:
    """无按钮的纯文本通知（更新类共用），点击正文拉起主窗口"""
    if not load_toast_notification_enabled():
        return False
    try:
        from windows_toasts import Toast

        toaster = _get_toaster()
        if toaster is None:
            return False
        bridge = get_toast_bridge()
        toast = Toast(
            text_fields=text_fields,
            on_activated=lambda args: bridge.activate_requested.emit(),
        )
        toaster.show_toast(toast)
        return True
    except Exception as e:  # noqa: BLE001 通知失败不影响主流程
        logger.warning("系统通知发送失败: %s", e)
        return False


def notify_update_notice(title: str, content: str) -> bool:
    """更新类通知（Sookit/yt-dlp 更新可用、安装包就绪、更新完毕共用，无按钮）"""
    title = (title or "").strip() or "Sookit"
    content = (content or "").strip()
    return _show_plain_toast([title, content] if content else [title])
