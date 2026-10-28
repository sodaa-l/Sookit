"""静默冷启动回归：GBK 标准输出与 Windows 首次显示尺寸。"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

URL = 'https://www.youtube.com/watch?v=Yb9H5vhWyHg'


class BrowserStartupTests(unittest.TestCase):
    def test_sniff_acceptance_and_timeout_survive_gbk_or_missing_stdout(self):
        from sookit.pages.base import PageBase
        from sookit.pages import youtube_page

        # 模拟 Native Host 使用 DEVNULL 的冷启动，以及无控制台的 GUI 发行版。
        gbk_stdout = io.TextIOWrapper(io.BytesIO(), encoding='gbk', errors='strict')
        self.addCleanup(gbk_stdout.close)
        for stdout in (gbk_stdout, None):
            with self.subTest(stdout=stdout):
                page = MagicMock()
                page._sniff_timer = None
                page._sniff_worker = None
                page._sniff_seq = 0
                page._threads = []
                page.url_input.text.return_value = URL
                page.log.side_effect = lambda msg: PageBase.log(page, msg)
                page.do_sniff.side_effect = lambda: youtube_page.YouTubePage.do_sniff(page)
                worker = MagicMock()
                timer = MagicMock()
                with patch('sys.stdout', stdout), \
                        patch.object(youtube_page, 'is_ytdlp_available', return_value=True), \
                        patch.object(youtube_page, 'extract_youtube_id', return_value=None), \
                        patch.object(youtube_page, 'GenericWorker', return_value=worker), \
                        patch.object(youtube_page, 'QTimer', return_value=timer), \
                        self.assertLogs('Sookit', level='INFO') as logs:
                    response = youtube_page.YouTubePage.sniff_from_browser(page, URL)
                self.assertTrue(response['ok'])
                worker.start.assert_called_once_with()
                timer.start.assert_called_once_with(60000)
                self.assertEqual(logs.records[-1].getMessage(), '▶ 正在嗅探视频信息...')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows 原生窗口尺寸回归')
    def test_native_window_and_infobars_normal_and_delayed_first_show(self):
        env = dict(os.environ, QT_QPA_PLATFORM='windows')
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--geometry-probe'],
            cwd=Path(__file__).resolve().parents[1], env=env,
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(next(line.removeprefix('GEOMETRY=')
                                 for line in result.stdout.splitlines()
                                 if line.startswith('GEOMETRY=')))
        for case in report:
            with self.subTest(silent=case['silent']):
                self.assertEqual(case['first_show'], [1131, 731])
                self.assertEqual(case['reshow'], [1240, 800])
                self.assertTrue(case['hidden_before_show'])
                self.assertTrue(case['centered'])
                self.assertEqual(case['infobar_checks'], 7)


def run_geometry_probe():
    """独立原生 Qt 进程，只测量控件尺寸，不截图、不联网、不写用户数据。"""
    from PyQt6.QtCore import Qt, QEvent, QEventLoop, QTimer
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import QApplication
    from qfluentwidgets import PushButton, SplashScreen
    from sookit.core import config
    from sookit.core.task_queue import TaskQueueManager
    from sookit.main_window import MainWindow
    from sookit.pages.settings_page import SettingsPage
    from sookit.paths import get_icon_path
    from sookit.widgets.infobar import show_infobar

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication([])
    app.setStyle('Fusion')
    app.setFont(QFont('Microsoft YaHei', 10))
    report = []

    def wait_for_animations():
        loop = QEventLoop()
        QTimer.singleShot(350, loop.quit)
        loop.exec()

    def assert_aligned(bars):
        y = 24
        for bar in bars:
            assert bar.isVisible(), bar.title
            expected = (bar.parent().width() - bar.width() - 24, y)
            actual = (bar.x(), bar.y())
            assert actual == expected, (bar.title, actual, expected)
            y += bar.height() + 16

    with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
        stack.enter_context(patch.object(config, 'get_config_path',
                                         return_value=str(Path(temp) / 'config.json')))
        config._config_cache = None
        for method in ('_load_completed_tasks', '_cleanup_stale_workspaces'):
            stack.enter_context(patch.object(TaskQueueManager, method))
        stack.enter_context(patch.object(MainWindow, '_setup_task_toast'))
        stack.enter_context(patch.object(MainWindow, '_check_update_at_startup'))
        stack.enter_context(patch.object(SettingsPage, '_check_versions'))
        for silent in (True, False):
            window = MainWindow()
            splash = SplashScreen(str(get_icon_path()), window)
            if silent:
                splash.close()
            else:
                window._center_on_screen()
            # 静默模式在首次 show 前处理旧的原生窗口消息，普通模式立即 show。
            if silent:
                for _ in range(5):
                    app.processEvents()
            hidden = not window.isVisible()
            window._on_path_ytdlp_check_done(('newer', '1', '2'))
            bars = [window._path_ytdlp_bar,
                    show_infobar(window, 'warning', title='更新失败',
                                 content='这是验证静默启动后位置的很长提示，' * 8),
                    show_infobar(window, 'info', title='后台任务', content='等待下载')]
            bars[1].addWidget(PushButton('前往设置', bars[1]))
            page_bar = show_infobar(window.youtube_page, 'error',
                                    title='嗅探失败', content='请稍后重试')
            hidden_page_bar = show_infobar(window.settings_page, 'success',
                                           title='已保存', content='设置已更新')
            closed_bar = show_infobar(window, 'info', title='已过期', content='应在显示前删除')
            closed_bar.close()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
            if silent:
                wait_for_animations()
            window.show()
            wait_for_animations()
            assert_aligned(bars)
            assert_aligned([page_bar])
            infobar_checks = 1
            first_show = [window.width(), window.height()]
            geo = QApplication.primaryScreen().availableGeometry()
            centered = (abs(window.x() - (geo.x() + (geo.width() - window.width()) // 2)) <= 2
                        and abs(window.y() - (geo.y() + (geo.height() - window.height()) // 2)) <= 2)
            bars.append(show_infobar(window, 'success', title='已加入队列', content='后台下载已开始'))
            window.resize(1240, 800)  # 新提示仍在滑入，旧终点不可覆盖新位置。
            wait_for_animations()
            assert_aligned(bars)
            assert_aligned([page_bar])
            infobar_checks += 1
            bars.pop(0).close()  # 上方提示关闭会触发其余提示的收拢动画。
            window.resize(1280, 820)
            wait_for_animations()
            assert_aligned(bars)
            infobar_checks += 1
            window.switchTo(window.settings_page)
            wait_for_animations()
            assert_aligned([hidden_page_bar])
            infobar_checks += 1
            window.switchTo(window.youtube_page)
            window.resize(1240, 800)
            wait_for_animations()
            assert_aligned(bars)
            assert_aligned([page_bar])
            infobar_checks += 1
            window.hide()
            wait_for_animations()
            window.show()
            wait_for_animations()
            assert_aligned(bars)
            assert_aligned([page_bar])
            infobar_checks += 1
            # 回调排队后立即销毁条，不得访问已释放的 Qt 对象。
            closed_bar = show_infobar(window, 'error', title='立即关闭', content='销毁回调')
            closed_bar.close()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
            wait_for_animations()
            assert_aligned(bars)
            infobar_checks += 1
            report.append({'silent': silent, 'hidden_before_show': hidden,
                           'first_show': first_show, 'centered': centered,
                           'reshow': [window.width(), window.height()],
                           'infobar_checks': infobar_checks})
            window.hide()
            window.deleteLater()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    print('GEOMETRY=' + json.dumps(report), flush=True)


if __name__ == '__main__':
    if '--geometry-probe' in sys.argv:
        run_geometry_probe()
    else:
        unittest.main()
