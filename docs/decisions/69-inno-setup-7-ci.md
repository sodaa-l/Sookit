# 69. 线上固定 Inno Setup 7 并保留默认防护

日期：2026-11-05

## 问题与证据

安装结束勾选运行 Sookit 时，系统 PATH 中的 Scoop yt-dlp/FFmpeg 可能无法启动；退出后从桌面重新启动则恢复正常。本机安装版进程启用了 `ProcessRedirectionTrustPolicy`（策略编号 16，`EnforceRedirectionTrust=1`），源码进程没有启用。

在短命测试进程中单独开启该策略后，原本正常的 Scoop shim 变为退出码 1，报告 `Shim: Could not determine if target is a GUI app. Assuming console.`，随后报告无法创建目标进程。访问 Scoop 的 `current` junction 返回 `ERROR_UNTRUSTED_MOUNT_POINT`（448），直接运行真实版本目录中的程序仍成功。问题来自安装器启动子进程时继承的 RedirectionGuard，不是工具本身损坏。

Inno Setup 6.7.4 已修复常规安装路径下的意外继承。7.1.1 也包含该修复，但仍有上游明确记录的边界：**安装器预先以管理员身份启动，或使用 `PrivilegesRequired=lowest` 时，子进程仍可能继承该策略**。升级不能被描述为覆盖所有启动方式。

## 决策

- 线上 Release 从不固定的 Chocolatey `innosetup` 和固定的 Inno Setup 6 路径，改为官方 **Inno Setup 7.1.1 x64**；本机编译器版本不决定线上版本。
- 固定官方 release 地址，下载到 runner 临时目录，安装前校验 SHA-256：`8a5a6711c3766a6e5fb74f280ba0e3551e719ce94538d901fdbd1f8f9920b3f0`。摘要来源为官方 GitHub release asset 的 `digest` 字段。
- 使用官方 `/PORTABLE=1` 静默便携模式解包到 runner 临时目录（不注册长期安装或文件关联），检查安装退出码、`ISCC.exe` 存在和`ISCC.exe --version` 输出严格为 `7.1.1`（官方二进制文件版本资源为 `0.0.0.0`，不能用于版本判断）。通过 `INNO_SETUP_ISCC` 将绝对路径传给编译步骤；不依赖 PATH，也不回退 runner 预装编译器。哈希、安装、版本或编译失败即终止。
- **保留 RedirectionGuard 默认防护**：不添加 `RedirectionGuard=no`，不修改系统缓解策略或 Scoop shim；安装脚本的权限、组件选择和 `postinstall` 行为维持原约定。
- 更新固定版本时必须同时核对官方摘要、ISCC `--version` 输出、现有中文语言文件/安装脚本兼容性及上游 postinstall 继承限制。

## 验收边界

工作流和 PowerShell 静态检查、临时最小输入的真实 ISCC 编译可验证构建配置及安装脚本兼容性；不能替代完整 PyInstaller 产物和安装后首次启动的端到端验收。正式包应在正常双击安装并经 UAC 提权后勾选运行，核对 PATH 中 Scoop 工具的版本查询，并单独观察预先管理员启动的已知限制。

本次验证：官方安装包实际 SHA-256 匹配；在含空格的临时路径中，以便携模式执行工作流的编译器准备步骤，`--version` 返回 `7.1.1`；8 段多行 PowerShell 脚本语法检查通过；原安装脚本、中文语言文件、图标及浏览器扩展配合临时最小可执行文件占位输入完成 ISCC 编译。未运行生成的测试安装器，未进行正式包首次启动验收。编译器对现有脚本的管理员安装涉及 `%APPDATA%`/`%LOCALAPPDATA%` 提示 `UsedUserAreasWarning`；本次保持该既有清理行为，不抑制警告。

## 官方依据

- [Inno Setup 7.1.1 release](https://github.com/jrsoftware/issrc/releases/tag/is-7_1_1)
- [Inno Setup 7 变更记录](https://jrsoftware.org/files/is7-whatsnew.htm)
- [Inno Setup 6.7.4 变更记录](https://jrsoftware.org/files/is6-whatsnew.htm#6.7.4)
- [RedirectionGuard 指令说明](https://jrsoftware.org/ishelp/topic_setup_redirectionguard.htm)
