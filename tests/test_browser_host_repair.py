"""实际 host 能力探测与旧开发桥接重建回归，不操作本机注册表。"""
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_browser_settings as fixtures
from sookit.core.browser_diagnostics import (
    _host_capability_issues, _rebuild_source_host,
    diagnose_browser_components, repair_browser_registration,
)
from sookit.core.browser_protocol import (
    BROWSER_ACTIONS, HOST_CONTROL_ACTIONS, HOST_NAME, decode_message, encode_message,
)


class HostRepairTests(unittest.TestCase):
    setUp = fixtures.BrowserDiagnosticsTests.setUp
    tearDown = fixtures.BrowserDiagnosticsTests.tearDown
    _extension = fixtures.BrowserDiagnosticsTests._extension
    _host_manifest = fixtures.BrowserDiagnosticsTests._host_manifest
    _source_launch_config = fixtures.BrowserDiagnosticsTests._source_launch_config
    _fake_registry = fixtures.BrowserDiagnosticsTests._fake_registry

    def _source_components(self):
        self._extension(self.root / 'browser-extension')
        manifest = self._host_manifest(
            self.root / 'build' / 'browser-bridge' / f'{HOST_NAME}.json',
            host_relative=Path('sookit-native-host') / 'sookit-native-host.exe')
        self._source_launch_config(manifest.parent / 'sookit-native-host' / 'sookit-native-host.exe')
        return manifest

    def test_outdated_source_host_is_rebuilt_and_verified_before_registration(self):
        manifest = self._source_components()
        options = dict(frozen=False, project_root=self.root)
        fake = self._fake_registry({('HKCU', 0): str(manifest)})
        self._capability_issues.return_value = ['桥接程序过旧']
        with patch.dict(sys.modules, {'winreg': fake}):
            before = diagnose_browser_components(**options)
            self.assertFalse(before['ok'])
            self.assertTrue(before['can_repair'])
            def rebuild(root):
                self.assertEqual(root, self.root)
                fake.CreateKeyEx.assert_not_called()
                self._capability_issues.return_value = []
            with patch('sookit.core.browser_diagnostics._rebuild_source_host', side_effect=rebuild) as build:
                repair_browser_registration(**options)
            build.assert_called_once_with(self.root)
            self.assertTrue(diagnose_browser_components(**options)['ok'])
        fake.CreateKeyEx.assert_called_once()

    def test_failed_rebuild_or_still_outdated_host_never_writes_registry(self):
        self._source_components()
        self._capability_issues.return_value = ['桥接程序过旧']
        for failure in (RuntimeError('构建失败'), None):
            with self.subTest(failure=failure):
                fake = self._fake_registry()
                with patch.dict(sys.modules, {'winreg': fake}), patch(
                        'sookit.core.browser_diagnostics._rebuild_source_host', side_effect=failure):
                    with self.assertRaises(RuntimeError):
                        repair_browser_registration(frozen=False, project_root=self.root)
                fake.CreateKeyEx.assert_not_called()

    def test_outdated_packaged_host_requests_reinstall_without_rebuild_or_registry_write(self):
        self._extension(self.root / 'browser-extension')
        manifest = self._host_manifest(self.root / f'{HOST_NAME}.json')
        self._capability_issues.return_value = ['桥接程序过旧']
        fake = self._fake_registry({('HKCU', 0): str(manifest)})
        with patch.dict(sys.modules, {'winreg': fake}), patch(
                'sookit.core.browser_diagnostics._rebuild_source_host') as build:
            result = diagnose_browser_components(frozen=True, executable=self.root / 'Sookit.exe')
            self.assertFalse(result['ok'])
            self.assertFalse(result['can_repair'])
            self.assertIn('重新安装', '；'.join(result['issues']))
            with self.assertRaisesRegex(RuntimeError, '重新安装'):
                repair_browser_registration(frozen=True, executable=self.root / 'Sookit.exe')
        build.assert_not_called()
        fake.CreateKeyEx.assert_not_called()


class HostCapabilityTests(unittest.TestCase):
    def test_probe_requires_all_actions_and_handles_old_broken_or_hung_hosts(self):
        required = sorted(BROWSER_ACTIONS | HOST_CONTROL_ACTIONS)
        for actions, broken in [(required, False), ([x for x in required if x != 'monitor'], True),
                                ('monitor', True), (['monitor', {}], True), (None, True)]:
            with self.subTest(actions=actions), patch('sookit.core.browser_diagnostics.subprocess.run',
                    return_value=SimpleNamespace(returncode=0, stdout=encode_message({'actions': actions}))) as run:
                self.assertEqual(bool(_host_capability_issues(Path('host.exe'))), broken)
                self.assertEqual(run.call_args.args[0], ['host.exe', '--bridge-capabilities'])
                self.assertEqual(run.call_args.kwargs['input'], b'')
                self.assertEqual(run.call_args.kwargs['timeout'], 3)
        for failure in (OSError('broken'), subprocess.TimeoutExpired('host', 3)):
            with patch('sookit.core.browser_diagnostics.subprocess.run', side_effect=failure):
                self.assertTrue(_host_capability_issues(Path('host.exe')))
        for output in (b'old host', encode_message({'ok': False, 'error': '仅允许 Sookit Chrome 扩展连接'})):
            with patch('sookit.core.browser_diagnostics.subprocess.run',
                       return_value=SimpleNamespace(returncode=0, stdout=output)):
                self.assertTrue(_host_capability_issues(Path('host.exe')))

    def test_source_rebuild_uses_build_only_and_propagates_build_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'scripts' / 'register_browser_bridge.py'
            script.parent.mkdir()
            script.write_text('# test', encoding='utf-8')
            with patch('sookit.core.browser_diagnostics.subprocess.run',
                       return_value=SimpleNamespace(returncode=0)) as run:
                _rebuild_source_host(root)
                self.assertEqual(run.call_args.args[0], [sys.executable, str(script), '--build-only'])
            with patch('sookit.core.browser_diagnostics.subprocess.run',
                       return_value=SimpleNamespace(returncode=1, stdout=b'', stderr=b'build failed')):
                with self.assertRaisesRegex(RuntimeError, 'build failed'):
                    _rebuild_source_host(root)

    def test_real_capabilities_command_is_read_only_without_gui_or_origin(self):
        process = subprocess.run([sys.executable, '-m', 'sookit.native_host', '--bridge-capabilities'],
                                 input=b'', capture_output=True, timeout=10, cwd=str(fixtures.ROOT))
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(set(decode_message(process.stdout)['actions']), BROWSER_ACTIONS | HOST_CONTROL_ACTIONS)


if __name__ == '__main__':
    unittest.main()
