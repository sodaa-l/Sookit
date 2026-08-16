"""
sookit/__main__.py
程序入口：main() 启动函数（原单文件启动脚本的依赖检查 + 启动块）
支持两种启动方式：uv run sookit（console script）或 python -m sookit
"""

import os
import sys


# 单实例互斥体名（与 packaging/installer.iss 的 AppMutex 保持一致）
# 限定 Local 会话（普通权限），供 Inno 安装/卸载检测程序是否在运行。
_SINGLE_INSTANCE_MUTEX = "Local\\Sookit"


def _acquire_single_instance():
    """创建/占用单实例命名互斥体；返回句柄（int）表示持有成功，返回 None 表示已有实例。

    供安装/卸载器（Inno AppMutex）检测本程序是否在运行：Sookit 运行期间该互斥体一直存在。
    """
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX)
    if not handle:
        # 创建失败（罕见）不阻塞启动
        return None
    # ERROR_ALREADY_EXISTS = 183
    if kernel32.GetLastError() == 183:
        kernel32.CloseHandle(handle)
        ctypes.windll.user32.MessageBoxW(
            None,
            "Sookit 已在运行，请勿重复启动。",
            "Sookit",
            0x40 | 0x1000,  # MB_ICONINFORMATION | MB_SETFOREGROUND
        )
        return None
    return handle


def main():
    # ---------- 单实例互斥体 ----------
    # 必须在创建 QApplication 之前检测：已有实例则提示并退出，不启动界面。
    _mutex = _acquire_single_instance()
    if _mutex is None:
        sys.exit(0)

    # ---------- 依赖检查 ----------
    # PyQt6 - 核心依赖，缺失时弹窗提示
    try:
        from PyQt6.QtWidgets import QApplication, QSystemTrayIcon
        from PyQt6.QtCore import Qt, QTimer, QSize
        from PyQt6.QtGui import QFont, QIcon
        PYQT6_AVAILABLE = True
    except ImportError:
        PYQT6_AVAILABLE = False
        print("错误: 缺少 PyQt6，请运行: pip install PyQt6")
        sys.exit(1)

    # qfluentwidgets - 核心依赖，缺失时弹窗提示
    try:
        import qfluentwidgets as qfw
        from qfluentwidgets import SystemTrayMenu, Action, SplashScreen
        QFLUENTWIDGETS_AVAILABLE = True
    except ImportError:
        QFLUENTWIDGETS_AVAILABLE = False
        print("错误: 缺少 qfluentwidgets，请运行: pip install qfluentwidgets")
        sys.exit(1)

    # 依赖检查通过后，再导入主窗口（避免包 import 时产生 sys.exit 副作用）
    from sookit import APP_NAME
    from sookit.paths import get_icon_path, get_log_dir
    from sookit.main_window import MainWindow

    # 配置日志：同时输出到 stderr（开发态可见）和日志文件（%LOCALAPPDATA%\Sookit\log\sookit.log，便于发布后排查）
    import logging
    from logging.handlers import RotatingFileHandler

    _handlers = [logging.StreamHandler()]
    try:
        _log_dir = get_log_dir()
        _log_dir.mkdir(parents=True, exist_ok=True)
        _handlers.append(
            RotatingFileHandler(
                _log_dir / "sookit.log",
                maxBytes=1 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
        )
    except OSError:
        pass  # 日志目录不可用时仅保留控制台输出，不影响程序启动

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=_handlers,
    )

    # 未捕获异常写入日志，保证崩溃也能留痕
    def _excepthook(exc_type, exc_value, exc_tb):
        logging.getLogger("Sookit").critical(
            "未捕获异常", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _excepthook

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Microsoft YaHei", 10))

    # 获取图标路径
    icon_path = str(get_icon_path())

    # 先创建主窗口（不显示）
    window = MainWindow()

    # 创建启动画面（parent 设为主窗口，会自动跟随主窗口大小）
    splash = SplashScreen(icon_path, window)
    splash.setIconSize(QSize(128, 128))

    # 静默启动判断
    silent_launch = "--silent" in sys.argv

    # 创建系统托盘图标
    tray_icon = QSystemTrayIcon(QIcon(icon_path), app)
    tray_icon.setToolTip(APP_NAME)

    # 托盘右键菜单（Fluent 风格）
    tray_menu = SystemTrayMenu(parent=window)
    tray_menu.addAction(
        Action(qfw.FluentIcon.FULL_SCREEN, "显示主窗口", triggered=lambda: (window.show(), window.activateWindow()))
    )
    tray_menu.addSeparator()
    tray_menu.addAction(
        Action(qfw.FluentIcon.CLOSE, "退出", triggered=window.quit_app)
    )
    tray_icon.setContextMenu(tray_menu)

    # 双击托盘图标切换窗口显隐
    def on_tray_activated(reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            if window.isVisible():
                window.hide()
            else:
                window.show()
                window.activateWindow()

    tray_icon.activated.connect(on_tray_activated)
    tray_icon.show()

    # 静默模式下不显示窗口，否则正常显示
    if not silent_launch:
        window._center_on_screen()
        # 先显示主窗口，SplashScreen 会自动覆盖在上面
        window.show()
        app.processEvents()
        # 延迟关闭启动画面，确保主窗口已完全渲染
        QTimer.singleShot(500, splash.close)
    else:
        splash.close()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
