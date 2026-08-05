"""
sookit/__main__.py
程序入口：main() 启动函数（原单文件启动脚本的依赖检查 + 启动块）
支持两种启动方式：uv run sookit（console script）或 python -m sookit
"""

import os
import sys


def main():
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
    from sookit.paths import get_icon_path
    from sookit.main_window import MainWindow

    # 配置日志
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

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
