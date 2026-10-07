"""
动图下载页 独立预览脚本（开发用，不参与打包）

在最小 FluentWindow 中只挂 GifPage（可附任务队列页观察入队），
用于在接入主窗口前先行测试 UI 与入队逻辑。页面代码与正式接入完全同一份。

运行：
    uv run python scripts/preview_gif_page.py          # 仅动图页
    uv run python scripts/preview_gif_page.py --queue  # 附带任务队列页
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

import qfluentwidgets as qfw
from qfluentwidgets import FluentIcon as FIF, NavigationItemPosition

from sookit.pages.gif_page import GifPage
from sookit.paths import get_icon_path


def main():
    with_queue = "--queue" in sys.argv

    app = QApplication(sys.argv)
    win = qfw.FluentWindow()
    win.setWindowTitle("Sookit · 动图页预览")
    win.resize(1000, 680)
    icon = get_icon_path()
    if icon:
        win.setWindowIcon(QIcon(str(icon)))

    gif_page = GifPage(win)
    gif_page.setObjectName("previewGifPage")
    win.addSubInterface(gif_page, FIF.DOWNLOAD, "动图下载")

    if with_queue:
        from sookit.pages.queue_page import QueuePage
        queue_page = QueuePage(win)
        queue_page.setObjectName("previewQueuePage")
        win.addSubInterface(queue_page, FIF.UPDATE, "任务队列",
                            position=NavigationItemPosition.BOTTOM)

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
