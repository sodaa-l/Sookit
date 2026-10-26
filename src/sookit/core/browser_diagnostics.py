"""只读检查浏览器扩展资源与 Chrome Native Messaging 注册。"""
import base64
import hashlib
import json
import os
from pathlib import Path
import sys

from sookit.core.browser_protocol import EXTENSION_ID, EXTENSION_ORIGIN, HOST_NAME
from sookit.paths import PROJECT_ROOT

REGISTRY_KEY = rf'Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}'
_HOST_EXECUTABLE_NAME = 'sookit-native-host.exe'


def get_extension_dir(*, frozen=None, executable=None, project_root=None) -> Path:
    """返回扩展目录；打包态以 Sookit.exe 为基准，源码态以项目根为基准。"""
    if frozen is None:
        frozen = getattr(sys, 'frozen', False)
    if frozen:
        base = Path(executable or sys.executable).resolve().parent
    else:
        base = Path(project_root or PROJECT_ROOT).resolve()
    return base / 'browser-extension'


def get_expected_host_manifest(*, frozen=None, executable=None, project_root=None) -> Path:
    """返回当前运行形态对应的 host 清单路径。"""
    if frozen is None:
        frozen = getattr(sys, 'frozen', False)
    if frozen:
        base = Path(executable or sys.executable).resolve().parent
    else:
        base = Path(project_root or PROJECT_ROOT).resolve() / 'build' / 'browser-bridge'
    return base / f'{HOST_NAME}.json'


def _expected_host_executable(manifest_path: Path, *, frozen: bool) -> Path:
    """按注册清单所在的安装/源码布局返回应被引用的 host。"""
    host_dir = manifest_path.parent if frozen else manifest_path.parent / 'sookit-native-host'
    return host_dir / _HOST_EXECUTABLE_NAME


def read_registry_entries():
    """只读枚举 HKCU/HKLM 的默认、32 位与 64 位注册表视图。"""
    import winreg

    views = (
        (0, '默认视图'),
        (winreg.KEY_WOW64_32KEY, '32 位视图'),
        (winreg.KEY_WOW64_64KEY, '64 位视图'),
    )
    roots = ((winreg.HKEY_CURRENT_USER, 'HKCU'), (winreg.HKEY_LOCAL_MACHINE, 'HKLM'))
    entries = []
    for root, hive in roots:
        for view_flag, view_name in views:
            try:
                key = winreg.OpenKey(root, REGISTRY_KEY, 0, winreg.KEY_READ | view_flag)
            except FileNotFoundError:
                continue
            except OSError as exc:
                entries.append({
                    'hive': hive, 'view': view_name, 'manifest_path': '',
                    'error': f'无法读取 {hive} {view_name} 注册项：{exc}',
                })
                continue
            try:
                try:
                    value, _ = winreg.QueryValueEx(key, '')
                except FileNotFoundError:
                    entries.append({
                        'hive': hive, 'view': view_name, 'manifest_path': '',
                        'error': f'{hive} {view_name} 注册项缺少清单路径',
                    })
                else:
                    entries.append({
                        'hive': hive, 'view': view_name,
                        'manifest_path': str(value) if isinstance(value, str) else '',
                        'error': None if isinstance(value, str) and value else
                                 f'{hive} {view_name} 注册项的清单路径无效',
                    })
            except OSError as exc:
                entries.append({
                    'hive': hive, 'view': view_name, 'manifest_path': '',
                    'error': f'无法读取 {hive} {view_name} 注册值：{exc}',
                })
            finally:
                winreg.CloseKey(key)
    return entries


def _normalized_path(value: str) -> str:
    if not isinstance(value, (str, os.PathLike)):
        return ''
    try:
        return os.path.normcase(str(Path(value).resolve()))
    except (OSError, RuntimeError, TypeError, ValueError):
        try:
            return os.path.normcase(os.path.abspath(os.fspath(value)))
        except (OSError, TypeError, ValueError):
            return ''


def _extension_manifest_issues(extension_dir: Path) -> list[str]:
    manifest_path = extension_dir / 'manifest.json'
    if not extension_dir.is_dir():
        return [f'未找到扩展目录：{extension_dir}']
    if not manifest_path.is_file():
        return [f'缺少扩展清单：{manifest_path}']
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f'扩展清单无法读取：{manifest_path}（{exc}）']
    if not isinstance(manifest, dict):
        return [f'扩展清单格式无效：{manifest_path}']

    issues = []
    if manifest.get('manifest_version') != 3:
        issues.append(f'扩展清单不是 Manifest V3：{manifest_path}')
    key = manifest.get('key')
    try:
        digest = hashlib.sha256(base64.b64decode(key, validate=True)).digest()[:16]
        manifest_id = ''.join(chr(97 + (byte >> 4)) + chr(97 + (byte & 15))
                              for byte in digest)
    except (TypeError, ValueError):
        manifest_id = ''
    if manifest_id != EXTENSION_ID:
        issues.append(f'扩展清单固定 ID 不匹配（应为 {EXTENSION_ID}）')

    permissions = manifest.get('permissions')
    if not isinstance(permissions, list) or 'nativeMessaging' not in permissions:
        issues.append(f'扩展清单缺少 nativeMessaging 权限：{manifest_path}')

    references = []
    background = manifest.get('background')
    service_worker = background.get('service_worker') if isinstance(background, dict) else None
    if isinstance(service_worker, str) and service_worker.strip():
        references.append(service_worker)
    else:
        issues.append(f'扩展清单缺少 background.service_worker：{manifest_path}')

    content_scripts = manifest.get('content_scripts')
    has_content_script = False
    if isinstance(content_scripts, list):
        for script in content_scripts:
            if not isinstance(script, dict):
                continue
            matches = script.get('matches')
            js_files = script.get('js')
            valid_js = ([ref for ref in js_files if isinstance(ref, str) and ref.strip()]
                        if isinstance(js_files, list) else [])
            references.extend(valid_js)
            if isinstance(matches, list) and any(
                    isinstance(match, str) and match.strip() for match in matches):
                has_content_script = has_content_script or bool(valid_js)
            css_files = script.get('css')
            if isinstance(css_files, list):
                references.extend(ref for ref in css_files
                                  if isinstance(ref, str) and ref.strip())
    if not has_content_script:
        issues.append(f'扩展清单缺少有效的 content_scripts 匹配项或脚本：{manifest_path}')

    icons = manifest.get('icons')
    if isinstance(icons, dict):
        references.extend(ref for ref in icons.values() if isinstance(ref, str))
    resources = manifest.get('web_accessible_resources', [])
    if isinstance(resources, list):
        for group in resources:
            if isinstance(group, dict):
                group_resources = group.get('resources')
                if isinstance(group_resources, list):
                    references.extend(ref for ref in group_resources
                                      if isinstance(ref, str) and '*' not in ref)

    root = extension_dir.resolve()
    for reference in dict.fromkeys(references):
        try:
            candidate = (extension_dir / reference).resolve()
        except (OSError, RuntimeError, TypeError, ValueError):
            issues.append(f'扩展清单引用路径无效：{reference}')
            continue
        if candidate != root and root not in candidate.parents:
            issues.append(f'扩展清单引用了目录外文件：{reference}')
        elif not candidate.is_file():
            issues.append(f'扩展缺少清单引用文件：{candidate}')
    return issues


def _host_manifest_issues(manifest_path: str, *, expected_host: Path) -> list[str]:
    if not isinstance(manifest_path, (str, os.PathLike)) or not str(manifest_path).strip():
        return ['本机桥接清单路径无效']
    try:
        path = Path(manifest_path)
        if not path.is_file():
            return [f'注册清单不存在：{path}']
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return [f'本机桥接清单路径无效：{manifest_path}（{exc}）']
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f'本机桥接清单无法读取：{path}（{exc}）']
    if not isinstance(manifest, dict):
        return [f'本机桥接清单格式无效：{path}']

    issues = []
    if manifest.get('name') != HOST_NAME:
        issues.append(f'本机桥接清单名称错误：{path}')
    if manifest.get('type') != 'stdio':
        issues.append(f'本机桥接清单通信类型不是 stdio：{path}')
    if manifest.get('allowed_origins') != [EXTENSION_ORIGIN]:
        issues.append(f'本机桥接清单 allowed_origins 与固定扩展 ID 不一致：{path}')
    host_path = manifest.get('path')
    if not isinstance(host_path, str) or not host_path.strip():
        issues.append(f'本机桥接清单缺少 host 程序路径：{path}')
    else:
        try:
            executable = Path(host_path)
            if not executable.is_absolute():
                executable = path.parent / executable
            if not executable.is_file():
                issues.append(f'未找到本机 host 程序：{executable}')
            if _normalized_path(executable) != _normalized_path(expected_host):
                issues.append(
                    f'本机桥接清单 host 程序不是当前布局预期文件：{executable}'
                    f'（应为 {expected_host}）')
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            issues.append(f'本机桥接清单 host 程序路径无效：{host_path}（{exc}）')
    return issues


def _source_launch_issues(config_path: Path) -> list[str]:
    """只读校验冻结的源码桥接 host 冷启动所需配置。"""
    try:
        if not config_path.is_file():
            return [f'缺少源码启动配置：{config_path}']
    except (OSError, RuntimeError, ValueError) as exc:
        return [f'源码启动配置路径无效：{config_path}（{exc}）']
    try:
        config = json.loads(config_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f'源码启动配置无法读取：{config_path}（{exc}）']
    if not isinstance(config, dict):
        return [f'源码启动配置格式无效：{config_path}']

    issues = []
    command = config.get('command')
    if (not isinstance(command, list) or not command
            or not all(isinstance(arg, str) for arg in command)):
        issues.append(f'源码启动配置的 command 无效：{config_path}')
    else:
        try:
            if not Path(command[0]).is_file():
                issues.append(f'源码启动配置的 command 入口不存在：{command[0]}')
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            issues.append(f'源码启动配置的 command 入口无效：{command[0]}（{exc}）')

    cwd = config.get('cwd')
    if not isinstance(cwd, str):
        issues.append(f'源码启动配置缺少有效的 cwd：{config_path}')
    else:
        try:
            if not Path(cwd).is_dir():
                issues.append(f'源码启动配置的 cwd 目录不存在：{cwd}')
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            issues.append(f'源码启动配置的 cwd 无效：{cwd}（{exc}）')
    return issues


def diagnose_browser_components(*, registry_entries=None, frozen=None, executable=None,
                                project_root=None) -> dict:
    """检查本地扩展、host 清单/程序及当前安装形态的只读注册路径。"""
    runtime_frozen = getattr(sys, 'frozen', False) if frozen is None else frozen
    extension_dir = get_extension_dir(
        frozen=runtime_frozen, executable=executable, project_root=project_root)
    expected_manifest = get_expected_host_manifest(
        frozen=runtime_frozen, executable=executable, project_root=project_root)
    expected_host = _expected_host_executable(expected_manifest, frozen=bool(runtime_frozen))
    issues = _extension_manifest_issues(extension_dir)
    if not runtime_frozen:
        source_config = expected_host.with_name('source-launch.json')
        issues.extend(_source_launch_issues(source_config))

    try:
        entries = read_registry_entries() if registry_entries is None else registry_entries
    except Exception as exc:
        entries = []
        issues.append(f'无法读取 Chrome 本机桥接注册项：{exc}')

    entries = list(entries or [])
    for entry in entries:
        if not isinstance(entry, dict):
            issues.append('Chrome 本机桥接注册项数据格式无效')
            continue
        error = entry.get('error')
        if error:
            issues.append(error)
        elif (not isinstance(entry.get('manifest_path'), str)
              or not entry['manifest_path'].strip()):
            issues.append('Chrome 本机桥接注册项的清单路径无效')

    valid_entries = [entry for entry in entries if isinstance(entry, dict)
                     and not entry.get('error')
                     and isinstance(entry.get('manifest_path'), str)
                     and entry['manifest_path'].strip()]
    current_key = _normalized_path(str(expected_manifest))
    current_registered = any(
        _normalized_path(entry['manifest_path']) == current_key for entry in valid_entries)

    hkcu_entries = [entry for entry in valid_entries if entry.get('hive', '').upper() == 'HKCU']
    hklm_entries = [entry for entry in valid_entries if entry.get('hive', '').upper() == 'HKLM']
    preferred_entries = hkcu_entries or hklm_entries
    preferred_paths = {_normalized_path(entry['manifest_path']) for entry in preferred_entries}

    if not entries:
        issues.append('未发现 HKCU/HKLM 的 Chrome 本机桥接注册项')
    elif not valid_entries:
        if not any(isinstance(entry, dict) and entry.get('error') for entry in entries):
            issues.append('Chrome 本机桥接注册项没有有效的清单路径')
    elif len(preferred_paths) > 1:
        views = '、'.join(f"{entry.get('hive')} {entry.get('view')} → {entry['manifest_path']}"
                          for entry in preferred_entries)
        issues.append(f'本机桥接注册视图指向不同清单：{views}')
    elif preferred_paths and next(iter(preferred_paths)) != current_key:
        chosen = preferred_entries[0]
        issues.append(
            f"当前 {chosen.get('hive')} {chosen.get('view')} 注册项指向其他目录："
            f"{chosen['manifest_path']}（当前应为 {expected_manifest}）")
        if hkcu_entries and hklm_entries:
            issues.append('HKCU 注册项优先于 HKLM，可能遮蔽安装版桥接')

    manifests_to_check = {str(expected_manifest)}
    manifests_to_check.update(entry['manifest_path'] for entry in preferred_entries)
    for manifest_path in sorted(manifests_to_check, key=_normalized_path):
        issues.extend(_host_manifest_issues(
            manifest_path, expected_host=expected_host))

    return {
        'ok': not issues,
        'issues': issues,
        'extension_dir': str(extension_dir),
        'expected_manifest': str(expected_manifest),
        'registered_current': current_registered,
    }
