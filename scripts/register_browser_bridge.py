"""为源码开发生成并注册当前用户的 Chrome 本机桥接。

用法：uv run --no-sync python scripts/register_browser_bridge.py [--unregister]
仅改 HKCU 的 Sookit Chrome NativeMessagingHosts 项，不需要管理员权限。
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from sookit.core.browser_protocol import EXTENSION_ORIGIN, HOST_NAME


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--unregister', action='store_true')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('此注册脚本仅支持 Windows Chrome')
    import winreg
    key = rf'Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}'
    directory = ROOT / 'build' / 'browser-bridge'
    manifest_path = directory / f'{HOST_NAME}.json'
    if args.unregister:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                current, _ = winreg.QueryValueEx(handle, '')
            if Path(current).resolve() != manifest_path.resolve():
                raise RuntimeError('注册项已指向其他安装，本脚本不会删除它')
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            pass
        print('已移除当前源码目录的 Chrome 本机桥接注册项')
        return
    # 打包独立的控制台 host：避免依赖 Chrome 对 bat/cmd 启动方式的兼容性。
    subprocess.run([
        sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir',
        '--console', '--hide-console', 'hide-early', '--name', 'sookit-native-host',
        '--paths', str(ROOT / 'src'),
        '--distpath', str(directory), '--workpath', str(directory / 'work'),
        '--specpath', str(directory), str(ROOT / 'src/sookit/native_host.py'),
    ], cwd=str(ROOT), check=True)
    # 源码开发的 host 已冻结，需从配置读取独立的源码启动命令。
    executable = directory / 'sookit-native-host' / 'sookit-native-host.exe'
    launch = {'command': [sys.executable, '-m', 'sookit', '--silent'], 'cwd': str(ROOT)}
    (executable.parent / 'source-launch.json').write_text(
        json.dumps(launch, ensure_ascii=False, indent=2), encoding='utf-8')
    manifest = {'name': HOST_NAME, 'description': 'Sookit development browser bridge',
                'path': str(executable), 'type': 'stdio', 'allowed_origins': [EXTENSION_ORIGIN]}
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
        winreg.SetValueEx(handle, '', 0, winreg.REG_SZ, str(manifest_path))
    print(f'本机桥接已注册：{manifest_path}')
    print(f'在 chrome://extensions 开启开发者模式，加载已解压的扩展：{ROOT / "browser-extension"}')


if __name__ == '__main__':
    main()
