# 决策 40. 主窗口不在焦点时的 Windows 系统通知（2026-10-14）

## 背景与需求

Sookit 支持 `--silent` 启动与「最小化至托盘」关闭行为，任务在后台完成/失败时用户无感知，
必须切回窗口才能看到 InfoBar。需求：**主窗口不在焦点**（最小化、隐藏、被其他应用遮挡）时，
任务完成/失败通过 Windows 10/11 原生 Toast 通知告知用户；窗口在焦点时维持 InfoBar 不变。

## 选型：windows-toasts（1.3.1，winrt-* 投影版）

| 候选 | 结论 |
| --- | --- |
| `QSystemTrayIcon.showMessage()` | 零依赖但无按钮/进度条等交互能力，不满足「打开文件夹」需求 |
| **windows-toasts** | 采用。1.3.1 起底层已从弃用的 winsdk 迁移到微软官方 `winrt-*` 投影包；维护活跃、有正规版本发布、正经处理 AUMID |
| win11toast | 功能最广（TTS/OCR）但无 release 流程、issue 积压、默认借 PowerShell 身份；两项独有功能对本项目无用，不取 |
| Windows App SDK `AppNotificationManager` | 需 WinAppSDK 运行时部署 + Bootstrap 初始化，AUMID 前提与经典 API 相同，本地通知功能无增量，成本收益不匹配 |

## 实现要点

- **覆盖事件（2026-10-14 定版）**：任务完成/失败 + 更新类四事件——Sookit 有新版本
  （`_set_sookit_update`）、Sookit 安装包就绪（`_on_setup_download_done` ok 分支）、
  yt-dlp 需要更新/未安装（`notify_ytdlp_update(True)`）、yt-dlp/Deno 更新完毕
  （`notify_ytdlp_updated`，设置页 `_on_ytdlp_download_done` 成功分支回调）。
  更新类为无按钮纯文本（`notify_update_notice`），点击正文统一拉起主窗口。
- **焦点判断**：所有通知统一以「主窗口是否持有焦点」为准（`QApplication.activeWindow() is 主窗口`），
  不焦点才弹。主窗口内的事件自行判定；页面侧的事件经 `notifier._should_notify()` 统一判定
  （基准窗口由 `set_main_window()` 注册，见下方 2026-10-16 追加段）。
- **更新通知去重**：Sookit 按版本号（周期检查每 4h 重复触发，同版本只弹一次，
  状态清除即重置）；yt-dlp 用 `open` 标记（清除后允许再次提示）。
- **任务通知触发**：`task_completed` / `task_failed` 信号（`TaskQueueManager` 单例），
  两者都是全局监听（同 `queue_page` 定位，不按发起页过滤——通知本来就只在窗口不可见时出现，
  不存在跨页打扰）。
- **交互通知必须用 `InteractableWindowsToaster`**：`WindowsToaster` 对按钮/输入框只告警不渲染。
  按钮能力要求可识别 AUMID，见下条。
- **自有 AUMID（HKCU 注册表注册，2026-10-14 实测踩坑后引入）**：最初用库默认的 cmd 借用身份
  （`{1AC14E77-...}\cmd.exe` + attribution text 显示 "Sookit"），实测被系统降级——`show_toast`
  不报错、`ToastNotificationManager.history.get_history_with_id(AUMID)` 里有记录，
  但**横幅和通知中心都不出现**。修复：向 `HKCU\Software\Classes\AppUserModelId\Sookit`
  写 `DisplayName`（+ `IconUri` 指向应用图标）注册自有身份（Electron/Tauri 同款方案，
  无需开始菜单快捷方式），Toaster 用 `notifierAUMID="Sookit"` 创建；注册失败退回 cmd 借用身份。
  遗留项：卸载残留一个无害注册表键，installer.iss 后续可加 `[Registry]` + `uninsdeletekey` 收编。
- **`ToastButton` 无独立回调**：按钮点击与正文点击统一走 `Toast.on_activated`，按
  `args.arguments` 区分（正文点击 arguments 是 toast tag）。约定 `open_folder` 为打开产物目录。
- **回调线程转发**：`on_activated` 在 WinRT 回调线程触发，不能直接碰 Qt 控件。
  `notifier._ToastSignalBridge`（QObject）持 `open_path_requested` / `activate_requested` 信号，
  跨线程 emit 自动走队列连接，主窗口接到后开目录 / `show()+activateWindow()`。
- **目标目录解析**：**仅成功通知**带「打开文件夹」按钮（用户定版：失败现场可能是已清理的
  workspace/空目录，不给按钮）；目标优先 `task.output_dir`（完成后产物已移入），
  兜底 `output_files[0]` 所在目录，再兜底 `task.output_path`（2026-10-15 补：FFmpeg/GIF
  类任务没有前两者，产物路径只回填到 `output_path`；该字段可能是文件也可能是目录）；
  都没有则不带按钮。
- **开关**：`general.toast_notification`（默认开），设置页「任务与嗅探/解析结果系统通知」SwitchButton
  （2026-10-16 起文案覆盖嗅探/解析，之前为「任务完成/失败时系统通知」），
  经 `config.load/save_toast_notification_enabled()` 读写。
- **打包**：`notifier.py` 内惰性 import，`sookit.spec` 的 `hiddenimports` 显式钉住
  `windows_toasts` 与 `winrt` / `winrt.windows.foundation(.collections)` /
  `winrt.windows.data.xml.dom` / `winrt.windows.ui.notifications`，防止模块图漏收 pyd。

## 追加：嗅探 / 解析 / 频道嗅探结果纳入通知（2026-10-16）

主窗口不在焦点时，以下三类**非任务队列**流程的成功/失败也弹系统通知——它们走
`GenericWorker`/`QThread`，没有 `Task` 对象，因此由页面在回调处直接调
`notifier.notify_result_notice(headline, title, detail)`（无按钮，点击正文拉起主窗口）。

| 流程 | 触发点 | 成功 | 失败 / 0 结果 |
| --- | --- | --- | --- |
| 视频嗅探 | `youtube_page._on_sniff_done` | 「嗅探成功」+ 标题 + 频道/时长/格式数 | 无可用格式 → 「嗅探完成（无可用格式）」单独一条 |
| 视频嗅探 | `youtube_page._on_sniff_error` / `_sniff_timeout` | — | 「嗅探失败」/「嗅探超时」+ 链接 + 错误摘要（截 200 字） |
| 动图解析 | `gif_page._on_parse_done` | 「解析成功」+ 标题 + 动图数 | 0 条 → 「未找到动图」 |
| 动图解析 | `gif_page._on_parse_error` | — | 「解析失败」+ 链接 + 错误 |
| 频道嗅探 | `monitor_page._on_channel_done` | 「频道嗅探完成」+ 已添加 N 个视频 | 频道无视频 → 同 headline，详情说明 |
| 频道嗅探 | `monitor_page._on_channel_error` | — | 「频道嗅探失败」+ 错误 |

- **焦点闸门收口**：`notifier.set_main_window(window)` 由 `main_window._setup_task_toast()`
  注册基准窗口，`notifier._should_notify()` 统一判「开关开 且 主窗口不在焦点」；
  页面无需自建焦点判断，也不必各自 `hasattr(window, ...)` 探测。
- **主动取消不通知**：`youtube_page.stop_sniff`（用户点「停止嗅探」）不弹，与任务取消口径一致。
- **嗅探页引入代际序号 `_sniff_seq`（2026-10-16 折修）**：`do_sniff` / `stop_sniff` 各递增一次，
  `done`/`error` 的 connect 用 `lambda ..., s=seq` 捕获本轮序号，回调首部比对不符即丢弃。
  动机是 `GenericWorker` 的固有竞态——`run()` 里「检查 `_stop_flag`」与「`emit` 跨线程信号」
  之间有时间缝：用户恰在此刻点停止（或信号已排队）时，`request_stop()` 拦不住那条在途结果，
  旧实现只有 `_sniff_timed_out`（仅超时置位）这一个守卫，停止后仍可能弹通知或填格式表。
  与动图页 `_parse_seq` 同构，顺带消除「旧嗅探结果覆盖新嗅探」。
  两个易踩点：① `stop_sniff` 的序号递增**无条件**执行（不能只在 `isRunning()` 时递增）；
  ② 回调里的复位动作（停计时器 / 恢复按钮）必须放在序号比对**之前**，否则过期回调
  会被整体跳过、把 UI 卡在「停止嗅探」态。
  超时路径**仍用** `_sniff_timed_out`（不递增代际）：超时自身已给出反馈，两者分工不同。
- **超时只弹一次**：`_sniff_timeout` 先置 `_sniff_timed_out`，随后到达的 `_on_sniff_error`
  提前 return，不会出现「超时 + 失败」两条。
- **代际校验**：动图解析 `_parse_seq` 与嗅探 `_sniff_seq` 先行，过期结果不误弹。
- **文案结构**：`headline`（结果行）+ `title`（视频/推文标题或链接）+ 可选 `detail`；
  标题取不到时回退 URL，保证通知至少有结果行。
- **嗅探失败 InfoBar 移出「补封面」分支（2026-10-16 二次折修）**：原先 `show_infobar(...)`
  缩进在 `if self._sniff_video_id and not self.cover_label._pixmap:` 内部，失败提示成了补封面的赠品。
  进一步实测发现根因比缩进更硬：**`not <QPixmap>` 在 PyQt6 下恒为 False**——`QPixmap` 未定义
  `__bool__`（`bool(QPixmap())` 与 `bool(QPixmap(2,2))` 都为 True），判空只能用 `isNull()`，
  因此该分支**任何情况都不执行**，这条 InfoBar 自 2026-08-29「InfoBar 统一入口」迁移以来
  **从未弹出过**。修复：把 `show_infobar(...)` 移到 `if` 之外（全项目仅此一处用了 `not pixmap`，
  其余 11 处判空均正确用了 `isNull()`）。
- 遗留（本次未改）：`_load_cover_image` + 「已从 URL 提取封面选项」日志仍在该 `if` 内，受同一个
  恒假条件影响而永不执行。是否把条件改成 `self.cover_label._pixmap.isNull()`（复活该兜底）需单独确认：
  复活后 YouTube 链接嗅探失败会额外尝试一次封面加载，非 YouTube 的 ID 会在 `_load_cover_image`
  内被 `build_thumbnails` 拦下，代价可控但属行为变化。

## 防御性设计

- Toaster 惰性单例 + `_toaster_failed` 标记：WinRT 初始化失败（无通知服务等）本会话不再重试，不刷日志。
- 发送/回调全链路 try-except 静默降级：通知属于锦上添花，任何异常不得影响任务主流程。

## 已知限制

- 用户开启勿扰模式（含全屏自动规则）时横幅不弹、仅进通知中心——系统策略，应用侧不应绕过。
- 进程退出（用户选择「直接退出」）后未点击的通知按钮失效（Toast 激活依赖进程存活）；
  Sookit 常驻托盘场景下影响极小。
- 诊断工具链备忘：winrt 投影的 `ToastNotificationManager.history` 是属性（对象），AUMID 变体方法为
  `get_history_with_id(AUMID)`（不是 `get_history(AUMID)`）。
