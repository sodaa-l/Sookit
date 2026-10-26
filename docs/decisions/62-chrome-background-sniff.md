# 决策 62. Chrome 视频页按钮与后台嗅探

## 目标与入口

在 YouTube 桌面版视频操作栏加入 36px 圆角按钮，显示现有 Sookit 图标和 `Sookit` 字样，接近 DownSub 按钮的大小与间距。使用 YouTube 颜色变量适应亮暗主题，不更改站点原有按钮。

Chrome Manifest V3 扩展位于 `browser-extension/`。内容脚本监听 YouTube 的 `yt-navigate-finish`、浏览器返回和 DOM 操作栏重建，复用单个按钮；离开 `/watch?v=...` 视频页移除按钮。点击期间禁用并去重，异步回执属于旧视频时丢弃。

## 通信选择

采用 Chrome Native Messaging，避免 URL 协议处理器每次点击触发外部应用确认，也无需开放 HTTP 端口或另起常驻服务：

```text
YouTube 操作栏按钮 → 扩展后台 → sookit-native-host.exe
                                 ↓ Qt 本机通道
                           Sookit 已有实例
                                 ↓
                    YouTubePage.do_sniff（现有流程）
```

- 扩展只需 `nativeMessaging` 权限；在 `https://www.youtube.com/*` 注入。不提取页面 Cookie，不读剪贴板，也不转发播放器请求。
- 后台核对发送扩展 ID、顶层视频页和请求视频 ID；host 再核对 Chrome 传入的扩展 origin，Qt 接收端再次验证请求。
- 仅允许 `sniff` 动作和 HTTPS YouTube 单视频，统一成 `https://www.youtube.com/watch?v=<11位ID>`；剥离时间、播放列表等参数。Shorts、live 和短链可由协议层规范化，但本轮按钮仅覆盖桌面 `/watch` 页面。
- `core/browser_protocol.py` 不依赖 Qt。Native Messaging 和本机通道均使用 4 字节小端长度加 UTF-8 JSON，单帧上限 16KiB。
- 扩展清单固定公钥，对应 ID `inplhidhdgncdjkkmbfcocedkabbaghn`。安装目录变动不会改变 ID；不得单独更换公钥，否则必须同步 host 的 `allowed_origins` 和协议常量。该值以 `core/browser_protocol.py` 与打包 host 清单为准。

## 生命周期

`core/browser_ipc.py` 使用 `QLocalServer/QLocalSocket`（Windows 命名管道），名字包含当前用户摘要和 Windows 会话号，设置 `UserAccessOption`。服务在 `MainWindow` 初始化完成后监听，回调在 GUI 线程执行；不完整连接 5 秒后断开，退出时关闭监听与连接。

host 优先向现有实例发送；只有无法建立连接才用独立进程启动 `Sookit.exe --silent`，随后最多等待 30 秒就绪。已建立连接但回执超时不自动重发，以免同一请求被执行两次。沿用决策 12 的 `Local\Sookit` 互斥体；`--silent` 重复启动不再弹“已在运行”提示。

- 静默冷启动不显示主窗口；已有实例保留显隐与焦点，受理后切换内部导航到视频嗅探页。
- 浏览器请求复用 `YouTubePage.do_sniff()`：原有配置、格式与字幕、封面、代际校验、停止和超时逻辑一并生效。
- worker 仍在运行或计时器仍在等待回调时，返回忙碌提示，保留当前输入和结果；不终止现有任务。yt-dlp 缺失时直接返回安装提示。
- 网页成功提示表示“应用已接受并启动嗅探”。最终成功/失败沿用决策 40 的系统通知，服从通知开关和 Windows 勿扰模式；点击通知打开主窗口。
- `general.browser_extension_enabled` 默认开启。已有 GUI 在分发请求前检查该值；冷启动 host 在启动 GUI 前读取同一配置，关闭或配置无法确认时拒绝新浏览器请求。此闸门不取消既有任务，也不影响手动嗅探。

## 设置页诊断

设置页只读检查扩展清单及其文件引用、host 程序与清单、固定扩展 ID / `allowed_origins` 和注册路径；manifest 的 host path 可相对清单目录解析，但必须指向当前源码或打包布局的预期 host。源码模式还检查相邻 `source-launch.json` 的 JSON 结构、command 格式与入口文件、cwd 目录是否存在，不执行启动命令；打包安装模式不要求该源码配置。注册诊断遍历 HKCU/HKLM 的默认、32 位和 64 位视图，并识别 HKCU 开发注册遮蔽 HKLM 安装注册的情况。检查结果只说明本机文件与桥接注册路径，不代表扩展已在 Chrome 加载、启用或连接；不自动改注册表或 Chrome 用户配置，也不安装扩展。

设置页另提供浏览器请求接收开关与可展开的四步安装引导：打开 Chrome 扩展管理（可复制地址）→开启开发者模式→加载扩展目录（可复制完整路径或打开目录）→返回 YouTube 刷新并点击 Sookit。长路径单行省略显示，悬停提示和复制保留完整路径；步骤采用靠左的圆形编号，标题与说明分层，正文占用可用宽度，避免弹性留白将说明挤窄；以间距代替硬分隔线。打开目录入口集中在第 3 步，路径和复制、打开按钮置于同一个圆角区域（最大宽度 820px），并适配浅色和深色主题。扩展文件更新后需在 Chrome 扩展页重新加载并刷新视频页。

## 打包与注册

PyInstaller 将控制台子系统的 `sookit-native-host.exe` 与 GUI 放入同一个 `dist/Sookit/`，共享 `_internal`。host 的 stdout 只输出协议帧；二进制模式避免 Windows 换行改写。GUI 的三路标准流均接 `DEVNULL`，与 host 分离生存；host 用 `hide_console="hide-early"` 避免黑窗。

Inno 安装器附带扩展目录和 `com.sookit.browser.json`，在 HKLM 注册 `Software\Google\Chrome\NativeMessagingHosts\com.sookit.browser`，卸载删除该项。host 清单中的相对 exe 路径相对清单目录解析。扩展需用户在 Chrome 开发者模式“加载已解压的扩展”，不修改 Chrome 用户配置、不强制安装扩展。

源码开发运行 `uv run --no-sync python scripts/register_browser_bridge.py`：使用现有环境中的 PyInstaller 生成独立 host 和源码启动配置，注册 HKCU，无需管理员权限。产物在已忽略的 `build/browser-bridge/`。`--unregister` 仅在注册项仍指向本源码目录时删除，不动其他安装。HKCU 注册项优先于安装版 HKLM；切换安装版前应先撤销开发注册。

## 验证

- `uv run --no-sync python -X utf8 -m unittest discover -s tests -p test_browser_bridge.py -v`：协议、白名单、分片/畸形帧、实际 Qt 本机通道和跨进程通信、冷启动分支、忙碌和缺失依赖、退出清理。
- `uv run --no-sync python -X utf8 -m unittest discover -s tests -p test_browser_settings.py -v`：开关默认值/持久化/失败回滚、GUI 与冷启动拒绝、手动嗅探隔离、源码/打包诊断路径、注册遮蔽与设置页引导。
- `node --test tests/test_browser_extension.cjs`：执行实际扩展脚本，验证来源校验、按钮图文、去重、YouTube SPA 跳转、操作栏重建和旧回执丢弃；使用最小 DOM 适配层，不等同于真实 YouTube 页面验收。
- 运行仓库全部 Python 回归，验证原有字幕和任务取消流程。
- 真实 Chrome 手动加载扩展后仍需确认 YouTube 当前布局、Native Messaging 启动和系统通知表现；离线测试无法保证站点 DOM 永久不变。
