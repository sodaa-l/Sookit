# 决策 22. 更新状态 Badge：导航双 badge + 设置页按钮圆点（2026-09-04）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 22」一一对应）

两层更新提示体系，数据源为 Sookit 自更新（`core/app_update.py`）与 yt-dlp/Deno（`ytdlp_utils.py`）：

- **导航"任务队列" badge**：`InfoBadge.attension` 文字胶囊，数字 = `get_active_tasks()` 数量，信号驱动（`task_added`/`task_completed`/`task_removed` → `_update_queue_badge`），归零隐藏。
- **导航"设置" badge**：`InfoBadge.error("")` 空文案红点，任一更新来源（Sookit 新版 / yt-dlp 有新版或未安装）命中即显示。状态由 `MainWindow._sookit_update`/`_ytdlp_update` 两个标志聚合；**尺寸动态取任务队列 badge 的 `height()`（17px）等高对齐**。Sookit 侧接线：启动/手动检查有新版点亮（与更新 Dialog 并存）、手动检查无新版清除、**「忽略此版本」清除（跟随静默，复用 `check_latest_version()` 的忽略过滤）**。
- **设置页按钮圆点**：`DotInfoBadge.error` 10px（`setFixedSize(10,10)`），挂 yt_card/about_card 上 target 按钮。「下载/更新」圆点由 SettingsPage 自管：`_on_latest_version` 有新版（仅 tools 来源，PATH 提前 return）或 `_check_versions` 未安装分支点亮，主窗口周期检查发现 tools 来源有新版时亦经 `set_ytdlp_update_dot` 点亮（见决策 28），`_on_ytdlp_download_done` 的 up_to_date/updated 分支清除（`_clear_yt_update_state` 同时经 `notify_ytdlp_update` 同步主窗口）；「检查更新」圆点由主窗口经 `set_sookit_update_dot` 控制。
- **配色坑**：`attension` 级 = 主题色，圆点贴在主题色 `PrimaryPushButton` 上会隐形——按钮圆点必须用 `error`/`warning` 级。
- **InfoBadge 定位坑（重要）**：`InfoBadgeManager` 只监听 **target** 的 Move/Resize 重定位，badge 自身 resize 不触发；且 `make()` 仅在创建时按当时尺寸定位一次。空文案 badge 初始宽度极小（~9px），`setFixedSize` 放大为左上角锚定 → 圆心右/下偏 ~4px。**修复：放大后手动 `badge.move(badge.manager.position())` 重算**。同理，文字 badge `setText` 后位数变化（如 9→10）需 `adjustSize()` + 重算位置。
- 页面级（SettingsPage → MainWindow）通信用 `self.window()` 弱引用回调（与 `_go_to_settings` 同模式），不引入信号耦合。
