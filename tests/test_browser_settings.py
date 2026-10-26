"""浏览器扩展设置、诊断与热/冷请求门控回归。"""
import json
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QEvent, QTimer
from PyQt6.QtWidgets import QApplication, QLabel
import qfluentwidgets as qfw

from sookit import native_host
from sookit.core import config
from sookit.core.browser_diagnostics import (
    REGISTRY_KEY, diagnose_browser_components, get_expected_host_manifest,
    get_extension_dir, read_registry_entries,
)
from sookit.core.browser_protocol import EXTENSION_ORIGIN, HOST_NAME
from sookit.main_window import MainWindow
from sookit.pages.settings_page import SettingsPage

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://www.youtube.com/watch?v=Yb9H5vhWyHg'
REQUEST = {'action': 'sniff', 'url': URL}


class ConfigSettingTests(unittest.TestCase):
    def setUp(self):
        self._old_cache = config._config_cache
        self._old_error = config._config_load_error
        self._temp = tempfile.TemporaryDirectory()
        self.path = Path(self._temp.name) / 'user-data' / 'config.json'
        self._path_patch = patch.object(config, 'get_config_path', return_value=str(self.path))
        self._path_patch.start()
        self._reset_cache()

    def tearDown(self):
        self._path_patch.stop()
        with config._config_lock:
            config._config_cache = self._old_cache
            config._config_load_error = self._old_error
        self._temp.cleanup()

    def _reset_cache(self):
        with config._config_lock:
            config._config_cache = None
            config._config_load_error = None

    def test_default_enabled_and_persistence_preserve_other_configuration(self):
        self.assertTrue(config.load_browser_extension_enabled())
        self.path.parent.mkdir(parents=True)
        initial = {'general': {'theme': 'dark', 'close_action': 1},
                   'download': {'concurrent_fragments': 8}}
        self.path.write_text(json.dumps(initial), encoding='utf-8')
        self._reset_cache()

        config.save_browser_extension_enabled(False)

        stored = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertFalse(stored['general']['browser_extension_enabled'])
        self.assertEqual(stored['general']['theme'], 'dark')
        self.assertEqual(stored['download'], initial['download'])
        self._reset_cache()  # emulate a separate process with its own lazy cache
        self.assertFalse(config.load_browser_extension_enabled())

    def test_corrupt_configuration_fails_closed_for_browser_setting(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{broken json', encoding='utf-8')
        self.assertEqual(config.load_config(), {})  # unrelated app settings retain legacy fallback
        with self.assertRaisesRegex(RuntimeError, '配置文件损坏'):
            config.load_browser_extension_enabled()

    def test_read_failure_and_invalid_setting_values_fail_closed(self):
        with patch('builtins.open', side_effect=PermissionError('access denied')):
            with self.assertRaisesRegex(RuntimeError, '读取配置文件失败'):
                config.load_browser_extension_enabled()

        for malformed in ({'general': []},
                          {'general': {'browser_extension_enabled': 'false'}}):
            with self.subTest(malformed=malformed):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(malformed), encoding='utf-8')
                self._reset_cache()
                with self.assertRaises(RuntimeError):
                    config.load_browser_extension_enabled()

    def test_save_failure_keeps_previous_cache_and_file(self):
        self.path.parent.mkdir(parents=True)
        initial = {'general': {'browser_extension_enabled': True}, 'other': 7}
        original_text = json.dumps(initial)
        self.path.write_text(original_text, encoding='utf-8')
        self._reset_cache()
        self.assertTrue(config.load_browser_extension_enabled())

        with patch.object(config.os, 'replace', side_effect=OSError('read only')):
            with self.assertRaisesRegex(RuntimeError, '保存配置文件失败'):
                config.save_browser_extension_enabled(False)

        self.assertTrue(config.load_browser_extension_enabled())
        self.assertEqual(self.path.read_text(encoding='utf-8'), original_text)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])


class RequestGateTests(unittest.TestCase):
    def test_hot_gui_rejects_disabled_or_unreadable_setting_before_dispatch(self):
        for setting in (False, RuntimeError('配置文件损坏')):
            page = SimpleNamespace(sniff_from_browser=MagicMock(), stop_sniff=MagicMock())
            window = SimpleNamespace(_quitting=False, youtube_page=page, switchTo=MagicMock())
            patcher = patch('sookit.main_window.load_browser_extension_enabled',
                            side_effect=setting if isinstance(setting, Exception) else None,
                            return_value=setting if isinstance(setting, bool) else True)
            with patcher:
                response = MainWindow.handle_browser_request(window, REQUEST)
            self.assertFalse(response['ok'])
            page.sniff_from_browser.assert_not_called()
            page.stop_sniff.assert_not_called()
            window.switchTo.assert_not_called()

    def test_hot_gui_preserves_busy_response_without_stopping_existing_work(self):
        busy = {'ok': False, 'error': '正在嗅探其他请求'}
        page = SimpleNamespace(sniff_from_browser=MagicMock(return_value=busy),
                               stop_sniff=MagicMock())
        window = SimpleNamespace(_quitting=False, youtube_page=page, switchTo=MagicMock())
        with patch('sookit.main_window.load_browser_extension_enabled', return_value=True):
            response = MainWindow.handle_browser_request(window, REQUEST)
        self.assertEqual(response, busy)
        page.sniff_from_browser.assert_called_once_with(URL)
        page.stop_sniff.assert_not_called()
        window.switchTo.assert_not_called()

    def test_cold_disabled_or_unreadable_setting_never_launches_gui(self):
        for setting in (False, RuntimeError('读取配置失败')):
            with self.subTest(setting=setting):
                with patch('sookit.core.browser_ipc.send_request', return_value=None), \
                        patch('sookit.core.config.load_browser_extension_enabled',
                              side_effect=setting if isinstance(setting, Exception)
                              else None, return_value=setting if isinstance(setting, bool)
                              else True), \
                        patch.object(native_host, 'app_command') as command, \
                        patch.object(native_host.subprocess, 'Popen') as launch:
                    response = native_host.forward_request(REQUEST)
                self.assertFalse(response['ok'])
                command.assert_not_called()
                launch.assert_not_called()

    def test_manual_sniff_path_does_not_read_browser_setting(self):
        from sookit.pages import youtube_page

        page = MagicMock()
        page.url_input.text.return_value = URL
        page._sniff_timer = None
        page._sniff_worker = None
        page._sniff_seq = 0
        page._threads = []
        worker = MagicMock()
        timer = MagicMock()
        with patch.object(youtube_page, 'extract_youtube_id', return_value=None), \
                patch.object(youtube_page, 'GenericWorker', return_value=worker), \
                patch.object(youtube_page, 'QTimer', return_value=timer), \
                patch.object(config, 'load_browser_extension_enabled',
                             side_effect=AssertionError('manual sniff checked browser setting')):
            youtube_page.YouTubePage.do_sniff(page)
        page.url_input.text.assert_called_once_with()
        worker.start.assert_called_once_with()


class BrowserDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self):
        self._temp.cleanup()

    def _extension(self, target):
        shutil.copytree(ROOT / 'browser-extension', target)
        return target

    def _host_manifest(self, path, *, origin=EXTENSION_ORIGIN, host_relative=None):
        path.parent.mkdir(parents=True, exist_ok=True)
        host = path.parent / (host_relative or 'sookit-native-host.exe')
        host.parent.mkdir(parents=True, exist_ok=True)
        host.write_bytes(b'test host')
        path.write_text(json.dumps({
            'name': HOST_NAME,
            'description': 'test bridge',
            'path': str(host),
            'type': 'stdio',
            'allowed_origins': [origin],
        }), encoding='utf-8')
        return path

    def _source_launch_config(self, host_executable, payload=None):
        config_path = host_executable.with_name('source-launch.json')
        if payload is None:
            payload = {
                'command': [sys.executable, '-m', 'sookit', '--silent'],
                'cwd': str(self.root),
            }
        config_path.write_text(json.dumps(payload), encoding='utf-8')
        return config_path

    def test_source_and_packaged_layouts_resolve_their_expected_components(self):
        source = self.root / 'source'
        self._extension(source / 'browser-extension')
        source_manifest = self._host_manifest(
            source / 'build' / 'browser-bridge' / f'{HOST_NAME}.json',
            host_relative=Path('sookit-native-host') / 'sookit-native-host.exe')
        source_host = (source_manifest.parent / 'sookit-native-host'
                       / 'sookit-native-host.exe')
        self._source_launch_config(source_host, {
            'command': [sys.executable, '-m', 'sookit', '--silent'],
            'cwd': str(source),
        })
        manifest = json.loads(source_manifest.read_text(encoding='utf-8'))
        manifest['path'] = os.path.relpath(source_host, source_manifest.parent)
        source_manifest.write_text(json.dumps(manifest), encoding='utf-8')
        source_result = diagnose_browser_components(
            registry_entries=[{'hive': 'HKCU', 'view': '默认视图',
                               'manifest_path': str(source_manifest)}],
            frozen=False, project_root=source)
        self.assertTrue(source_result['ok'], source_result['issues'])
        self.assertEqual(Path(source_result['extension_dir']), source / 'browser-extension')
        self.assertEqual(Path(source_result['expected_manifest']), source_manifest)

        install = self.root / 'install'
        executable = install / 'Sookit.exe'
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b'test app')
        self._extension(install / 'browser-extension')
        packaged_manifest = self._host_manifest(install / f'{HOST_NAME}.json')
        manifest = json.loads(packaged_manifest.read_text(encoding='utf-8'))
        manifest['path'] = os.path.relpath(
            install / 'sookit-native-host.exe', packaged_manifest.parent)
        packaged_manifest.write_text(json.dumps(manifest), encoding='utf-8')
        packaged_result = diagnose_browser_components(
            registry_entries=[{'hive': 'HKLM', 'view': '64 位视图',
                               'manifest_path': str(packaged_manifest)}],
            frozen=True, executable=executable)
        self.assertTrue(packaged_result['ok'], packaged_result['issues'])
        self.assertEqual(Path(packaged_result['extension_dir']), install / 'browser-extension')
        self.assertEqual(Path(packaged_result['expected_manifest']), packaged_manifest)

    def test_existing_host_at_wrong_path_is_reported_for_source_and_package(self):
        for frozen in (False, True):
            with self.subTest(frozen=frozen):
                root = self.root / ('install' if frozen else 'source')
                self._extension(root / 'browser-extension')
                if frozen:
                    executable = root / 'Sookit.exe'
                    executable.parent.mkdir(parents=True, exist_ok=True)
                    executable.write_bytes(b'test app')
                    manifest_path = root / f'{HOST_NAME}.json'
                    expected_host = root / 'sookit-native-host.exe'
                else:
                    executable = None
                    manifest_path = root / 'build' / 'browser-bridge' / f'{HOST_NAME}.json'
                    expected_host = (manifest_path.parent / 'sookit-native-host'
                                     / 'sookit-native-host.exe')
                expected_host.parent.mkdir(parents=True, exist_ok=True)
                expected_host.write_bytes(b'expected host')
                if not frozen:
                    self._source_launch_config(expected_host, {
                        'command': [sys.executable, '-m', 'sookit', '--silent'],
                        'cwd': str(root),
                    })
                manifest = self._host_manifest(
                    manifest_path, host_relative=Path('wrong-host.exe'))
                result = diagnose_browser_components(
                    registry_entries=[{'hive': 'HKLM', 'view': '默认视图',
                                       'manifest_path': str(manifest)}],
                    frozen=frozen, executable=executable, project_root=root)
                self.assertTrue(any('不是当前布局预期文件' in issue
                                    for issue in result['issues']), result['issues'])

    def test_source_launch_configuration_errors_are_reported_without_raising(self):
        source = self.root / 'source'
        self._extension(source / 'browser-extension')
        manifest = self._host_manifest(
            source / 'build' / 'browser-bridge' / f'{HOST_NAME}.json',
            host_relative=Path('sookit-native-host') / 'sookit-native-host.exe')
        host = manifest.parent / 'sookit-native-host' / 'sookit-native-host.exe'
        config_path = host.with_name('source-launch.json')
        registry_entries = [{'hive': 'HKCU', 'view': '默认视图',
                             'manifest_path': str(manifest)}]
        cases = (
            (None, '缺少源码启动配置'),
            ('{broken json', '源码启动配置无法读取'),
            ([], '源码启动配置格式无效'),
            ({'command': 'python', 'cwd': str(source)}, 'command 无效'),
            ({'command': [None], 'cwd': str(source)}, 'command 无效'),
            ({'command': [str(source / 'missing-python.exe')], 'cwd': str(source)},
             'command 入口不存在'),
            ({'command': [sys.executable], 'cwd': None}, '缺少有效的 cwd'),
            ({'command': [sys.executable], 'cwd': str(source / 'missing-cwd')},
             'cwd 目录不存在'),
        )
        for index, (payload, expected_issue) in enumerate(cases):
            with self.subTest(case=index, payload=payload):
                if payload is None:
                    config_path.unlink(missing_ok=True)
                elif isinstance(payload, str):
                    config_path.write_text(payload, encoding='utf-8')
                else:
                    config_path.write_text(json.dumps(payload), encoding='utf-8')
                result = diagnose_browser_components(
                    registry_entries=registry_entries, frozen=False, project_root=source)
                self.assertTrue(any(expected_issue in issue for issue in result['issues']),
                                result['issues'])

    def test_missing_extension_directory_is_reported_without_creating_it(self):
        root = self.root / 'missing'
        expected = get_extension_dir(frozen=False, project_root=root)
        result = diagnose_browser_components(
            registry_entries=[], frozen=False, project_root=root)
        self.assertFalse(expected.exists())
        self.assertTrue(any(str(expected) in issue and '未找到扩展目录' in issue
                            for issue in result['issues']))

    def test_stale_hkcu_registration_masks_valid_hklm_install_path(self):
        install = self.root / 'install'
        executable = install / 'Sookit.exe'
        install.mkdir()
        executable.write_bytes(b'test app')
        self._extension(install / 'browser-extension')
        expected = self._host_manifest(install / f'{HOST_NAME}.json')
        stale = self.root / 'old-dev' / f'{HOST_NAME}.json'
        result = diagnose_browser_components(
            registry_entries=[
                {'hive': 'HKCU', 'view': '默认视图', 'manifest_path': str(stale)},
                {'hive': 'HKLM', 'view': '64 位视图', 'manifest_path': str(expected)},
            ], frozen=True, executable=executable)
        self.assertTrue(result['registered_current'])
        self.assertTrue(any('HKCU 注册项优先于 HKLM' in issue for issue in result['issues']))
        self.assertTrue(any('指向其他目录' in issue for issue in result['issues']))
        self.assertTrue(any('注册清单不存在' in issue for issue in result['issues']))

    def test_wrong_allowed_origin_is_reported(self):
        install = self.root / 'install'
        executable = install / 'Sookit.exe'
        install.mkdir()
        executable.write_bytes(b'test app')
        self._extension(install / 'browser-extension')
        expected = self._host_manifest(install / f'{HOST_NAME}.json',
                                       origin='chrome-extension://wrong/')
        result = diagnose_browser_components(
            registry_entries=[{'hive': 'HKLM', 'view': '默认视图',
                               'manifest_path': str(expected)}],
            frozen=True, executable=executable)
        self.assertTrue(any('allowed_origins 与固定扩展 ID 不一致' in issue
                            for issue in result['issues']))

    def test_missing_core_manifest_fields_and_bad_references_are_reported(self):
        install = self.root / 'install'
        executable = install / 'Sookit.exe'
        install.mkdir()
        executable.write_bytes(b'test app')
        extension_dir = self._extension(install / 'browser-extension')
        manifest_path = extension_dir / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['background']['service_worker'] = '../outside.js'
        manifest['content_scripts'][0]['js'] = ['missing.js']
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        host_manifest = self._host_manifest(install / f'{HOST_NAME}.json')
        result = diagnose_browser_components(
            registry_entries=[{'hive': 'HKLM', 'view': '默认视图',
                               'manifest_path': str(host_manifest)}],
            frozen=True, executable=executable)
        self.assertTrue(any('目录外文件' in issue for issue in result['issues']))
        self.assertTrue(any('缺少清单引用文件' in issue for issue in result['issues']))

        manifest['manifest_version'] = 2
        manifest['permissions'] = []
        manifest['background'] = {}
        manifest['content_scripts'] = []
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        result = diagnose_browser_components(
            registry_entries=[{'hive': 'HKLM', 'view': '默认视图',
                               'manifest_path': str(host_manifest)}],
            frozen=True, executable=executable)
        for expected in ('不是 Manifest V3', '缺少 nativeMessaging 权限',
                         '缺少 background.service_worker', '缺少有效的 content_scripts'):
            self.assertTrue(any(expected in issue for issue in result['issues']), expected)

    def test_registry_reader_enumerates_both_hives_and_all_views_read_only(self):
        class FakeWinreg:
            HKEY_CURRENT_USER = object()
            HKEY_LOCAL_MACHINE = object()
            KEY_READ = 0x20019
            KEY_WOW64_32KEY = 0x0200
            KEY_WOW64_64KEY = 0x0100

            def __init__(self):
                self.opened = []

            def OpenKey(self, root, key, reserved, access):
                self.opened.append((root, key, reserved, access))
                return root, access

            def QueryValueEx(self, handle, value_name):
                return r'C:\Sookit\com.sookit.browser.json', 'REG_SZ'

            def CloseKey(self, handle):
                return None

        fake = FakeWinreg()
        with patch.dict(sys.modules, {'winreg': fake}):
            entries = read_registry_entries()
        self.assertEqual(len(entries), 6)
        self.assertEqual({entry['hive'] for entry in entries}, {'HKCU', 'HKLM'})
        self.assertEqual({entry['view'] for entry in entries},
                         {'默认视图', '32 位视图', '64 位视图'})
        self.assertTrue(all(call[1] == REGISTRY_KEY for call in fake.opened))


class BrowserSettingsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _page(self):
        patches = [
            patch('sookit.pages.settings_page.load_browser_extension_enabled', return_value=True),
            patch('sookit.pages.settings_page.load_close_action', return_value=0),
            patch('sookit.pages.settings_page.load_task_complete_action', return_value=0),
            patch('sookit.pages.settings_page.diagnose_browser_components',
                  return_value={'ok': True, 'issues': []}),
            patch.object(QTimer, 'singleShot'),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        page = SettingsPage()

        def cleanup_page():
            page.deleteLater()
            QApplication.sendPostedEvents(page, QEvent.Type.DeferredDelete)

        self.addCleanup(cleanup_page)
        return page

    def test_default_state_guide_steps_copy_and_component_summary(self):
        page = self._page()
        self.assertTrue(page.browser_extension_switch.isChecked())
        self.assertEqual(page.browser_components_status.text(),
                         '扩展文件完整 · 浏览器桥接正常')
        step_titles = [
            page.browser_guide_panel.findChild(
                QLabel, f'browserGuideStepTitle{number}')
            for number in range(1, 5)
        ]
        self.assertEqual([label.text() for label in step_titles], [
            '打开扩展管理', '开启开发者模式', '加载扩展', '返回 YouTube 验证',
        ])
        step_details = [
            page.browser_guide_panel.findChild(
                QLabel, f'browserGuideStepDetail{number}')
            for number in range(1, 5)
        ]
        self.assertEqual([label.text() for label in step_details], [
            '在 Chrome 地址栏输入 chrome://extensions/',
            '打开页面右上角的开发者模式开关',
            '点击“加载已解压的扩展程序”，选择扩展目录',
            '刷新 YouTube 视频页并点击 Sookit',
        ])
        self.assertTrue(all(label.font().bold() for label in step_titles))
        labels = [label.text() for label in page.findChildren(QLabel)]
        header_description_text = (
            '扩展已随安装包提供，首次使用需在 Chrome 中手动加载')
        self.assertIn(header_description_text, labels)
        header_description = next(
            label for label in page.findChildren(QLabel)
            if label.text() == header_description_text)
        self.assertIn('font: 11px', header_description.styleSheet())
        self.assertTrue(any(
            '扩展更新后，在 Chrome 扩展页重新加载并刷新视频页' in text
            for text in labels))
        self.assertFalse(page._browser_guide_expanded)
        self.assertTrue(page.browser_guide_panel.isHidden())
        self.assertFalse(any('已完成安装' in text for text in labels))
        self.assertFalse(any('在 YouTube 视频页点击 Sookit，即可在后台进行嗅探。' in text
                             for text in labels))
        self.assertFalse(any(term in text for text in labels
                             for term in ('Chrome 已安装', 'Chrome 已启用', 'Chrome 已连接')))
        with patch('sookit.pages.settings_page.diagnose_browser_components',
                   return_value={'ok': True, 'issues': []}) as diagnose:
            page.browser_components_refresh_button.click()
        diagnose.assert_called_once_with()

        page.browser_install_guide_button.click()
        self.assertTrue(page._browser_guide_expanded)
        self.assertFalse(page.browser_guide_panel.isHidden())
        self.assertTrue(all(not label.isHidden() for label in step_titles))
        self.assertEqual(page.browser_install_guide_button.text(), '收起引导')
        page.browser_install_guide_button.click()
        self.assertFalse(page._browser_guide_expanded)
        self.assertTrue(page.browser_guide_panel.isHidden())
        self.assertEqual(page.browser_install_guide_button.text(), '安装引导')

        clipboard = MagicMock()
        with patch('sookit.pages.settings_page.QApplication.clipboard',
                   return_value=clipboard), patch('sookit.pages.settings_page.show_infobar'):
            page.browser_copy_extensions_url_button.click()
        clipboard.setText.assert_called_once_with('chrome://extensions/')

        with tempfile.TemporaryDirectory() as directory, \
                patch.object(page, '_browser_extension_path', return_value=Path(directory)), \
                patch('sookit.pages.settings_page.QApplication.clipboard',
                      return_value=clipboard), \
                patch('sookit.pages.settings_page.show_infobar'):
            page.browser_copy_folder_button.click()
        self.assertEqual(clipboard.setText.call_args.args[0], directory)

    def test_guide_layout_stays_within_card_and_elides_long_path(self):
        page = self._page()
        page.browser_install_guide_button.click()
        long_path = Path(
            'D:/VideoToolBox/' + '/'.join(['long-folder-name-1234567890'] * 18)
            + '/browser-extension')
        with patch.object(page, '_browser_extension_path', return_value=long_path), \
                patch('sookit.pages.settings_page.diagnose_browser_components',
                      return_value={'ok': True, 'issues': []}):
            page._refresh_browser_components()

        path_label = page.browser_extension_path_label
        self.assertEqual(path_label.objectName(), 'contentLabel')
        for theme, expected_color in (
                (qfw.Theme.LIGHT, 'rgb(96, 96, 96)'),
                (qfw.Theme.DARK, 'rgb(208, 208, 208)')):
            with self.subTest(theme=theme):
                qfw.FluentStyleSheet.SETTING_CARD.apply(path_label, theme)
                style = path_label.styleSheet()
                self.assertIn('QLabel#contentLabel', style)
                self.assertIn(f'color: {expected_color};', style)
                self.assertIn('font: 11px', style)

        clipboard = MagicMock()
        with patch.object(page, '_browser_extension_path', return_value=long_path), \
                patch('sookit.pages.settings_page.QApplication.clipboard',
                      return_value=clipboard), \
                patch('sookit.pages.settings_page.show_infobar'):
            page.browser_copy_folder_button.click()
        clipboard.setText.assert_called_once_with(str(long_path))

        page.show()
        for width in (966, 1131, 1600):
            with self.subTest(width=width):
                page.resize(width, 731)
                QApplication.processEvents()
                panel = page.browser_guide_panel
                self.assertFalse(panel.isHidden())
                title_labels = [
                    panel.findChild(QLabel, f'browserGuideStepTitle{number}')
                    for number in range(1, 5)
                ]
                title_y = [label.mapTo(panel, label.rect().topLeft()).y()
                           for label in title_labels]
                self.assertEqual(title_y, sorted(title_y))

                for number in range(1, 5):
                    number_label = panel.findChild(
                        QLabel, f'browserGuideStepNumber{number}')
                    title_label = panel.findChild(
                        QLabel, f'browserGuideStepTitle{number}')
                    number_x = number_label.mapTo(
                        panel, number_label.rect().topLeft()).x()
                    title_x = title_label.mapTo(
                        panel, title_label.rect().topLeft()).x()
                    self.assertLessEqual(number_x, 2)
                    self.assertAlmostEqual(
                        title_x - number_x - number_label.width(), 10, delta=2)

                displayed_path = QLabel.text(path_label)
                self.assertEqual(path_label.toolTip(), str(long_path))
                self.assertIn(str(long_path), path_label.text())
                self.assertIn('…', displayed_path)
                self.assertLess(len(displayed_path), len(path_label.text()))

                controls = (
                    path_label,
                    page.browser_copy_folder_button,
                    page.browser_guide_open_folder_button,
                )
                control_x = []
                for control in controls:
                    point = control.mapTo(panel, control.rect().topLeft())
                    control_x.append(point.x())
                    self.assertGreater(control.width(), 0)
                    self.assertGreaterEqual(point.x(), 0)
                    self.assertLessEqual(point.x() + control.width(), panel.width())
                self.assertGreaterEqual(
                    control_x[1] - control_x[0] - path_label.width(), 0)
                self.assertLessEqual(
                    control_x[1] - control_x[0] - path_label.width(), 12)

                step1_detail = panel.findChild(
                    QLabel, 'browserGuideStepDetail1')
                step1_detail_x = step1_detail.mapTo(
                    panel, step1_detail.rect().topLeft()).x()
                copy_url_x = page.browser_copy_extensions_url_button.mapTo(
                    panel, page.browser_copy_extensions_url_button.rect().topLeft()).x()
                self.assertGreaterEqual(
                    copy_url_x - step1_detail_x - step1_detail.width(), 0)
                self.assertLessEqual(
                    copy_url_x - step1_detail_x - step1_detail.width(), 8)

                path_group_start = path_label.mapTo(
                    panel, path_label.rect().topLeft()).x()
                path_group_end = (
                    page.browser_guide_open_folder_button.mapTo(
                        panel, page.browser_guide_open_folder_button.rect().topLeft()).x()
                    + page.browser_guide_open_folder_button.width())
                path_group_width = path_group_end - path_group_start
                self.assertLessEqual(path_group_width, 840)
                if width == 1600:
                    self.assertGreaterEqual(path_group_width, 700)

    def test_switch_success_saves_and_displays_paused_status(self):
        page = self._page()
        with patch('sookit.pages.settings_page.save_browser_extension_enabled') as save:
            page.browser_extension_switch.setChecked(False)
            save.assert_called_once_with(False)
        self.assertFalse(page.browser_extension_switch.isChecked())
        self.assertEqual(page.browser_extension_paused_label.text(),
                         '已暂停接收浏览器请求，可随时重新开启')
        self.assertFalse(page.browser_extension_paused_label.isHidden())

    def test_switch_persistence_failure_reverts_and_folder_open_requires_existing_path(self):
        page = self._page()
        with patch('sookit.pages.settings_page.save_browser_extension_enabled',
                   side_effect=OSError('disk full')), \
                patch('sookit.pages.settings_page.show_infobar') as notice:
            page.browser_extension_switch.setChecked(False)
        self.assertTrue(page.browser_extension_switch.isChecked())
        notice.assert_called_once()
        self.assertIn('disk full', notice.call_args.kwargs['content'])

        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / 'browser-extension'
            existing.mkdir()
            for button in (page.browser_open_folder_button,
                           page.browser_guide_open_folder_button):
                with patch.object(page, '_browser_extension_path', return_value=existing), \
                        patch('sookit.pages.settings_page.QDesktopServices.openUrl',
                              return_value=True) as open_url:
                    button.click()
                open_url.assert_called_once()

            missing = Path(directory) / 'missing-extension'
            with patch.object(page, '_browser_extension_path', return_value=missing), \
                    patch('sookit.pages.settings_page.QDesktopServices.openUrl') as open_url, \
                    patch('sookit.pages.settings_page.show_infobar') as notice:
                page._open_browser_extension_folder()
            self.assertFalse(missing.exists())
            open_url.assert_not_called()
            self.assertIn(str(missing), notice.call_args.kwargs['content'])


if __name__ == '__main__':
    unittest.main()
