# 决策 28. yt-dlp 按生效来源挂进主窗口更新周期检查（2026-09-06）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 28」一一对应）

背景：主窗口自动检查周期（决策 25）原本只查 Sookit 自身；yt-dlp 的检查仅设置页构造时跑一次（提示条挂隐藏页面，进入设置页才可见），长会话中 yt-dlp 发新版无感知。PATH 来源副本由用户自行管理（scoop/pip 等），应用无法也不应代更新。

**检查层（`ytdlp_utils.py`，经 functions 再导出）**：

- `check_path_ytdlp_update()` / `check_tools_ytdlp_update()` 返回 `(status, current, latest)` 四态：`newer` / `latest` / `failed` / `skipped`；
- **两函数按 `get_ytdlp_source()` 互斥**：仅当前生效来源真正跑 `--version` 子进程 + GitHub 请求，另一来源直接 `skipped`（tools 侧 exe 不存在同样 `skipped`，未安装引导归设置页）；
- 复用 `_run_version_cmd` / `get_ytdlp_latest_version` / `_normalize_version`，与既有检查同源同口径。

**主窗口接线**：`_CheckWorker` 在 Sookit 检查后串行追加两个 yt-dlp 检查，done 载荷扩为 `(sookit, path_ytdlp, tools_ytdlp)` 三元组，共用同一 4h ± 抖动/指数退避循环（**yt-dlp 检查失败不影响 Sookit 退避计数**）。结果分派 `_on_path_ytdlp_check_done` / `_on_tools_ytdlp_check_done`：`newer` 才动作，`latest/failed/skipped` 一律静默；会话内同版本防重（`_path_ytdlp_notified` / `_tools_ytdlp_notified`，不写 config，重启后重新提示一次），出现更新版本时关旧条弹新条。

**通知策略（产品决策）**：

- **PATH**：仅主窗口常驻 InfoBar「检测到 PATH 中的 yt-dlp 可更新，请自行更新」（无按钮、**不点亮 Badge/圆点**）——提示即全部职责，更新动作完全归用户；
- **tools**：点亮导航设置 Badge（`notify_ytdlp_update(True)`）+ 「下载/更新」按钮圆点（新增公开方法 `SettingsPage.set_ytdlp_update_dot`）+ 常驻 InfoBar（含版本号与「前往设置」按钮）——tools 可由应用内提权更新，与设置页自检同口径引导；**收口链路**：更新成功 → `_clear_yt_update_state` → `notify_ytdlp_update(False)` → 关闭该条并清防重状态（挂在 notify 回调上天然覆盖所有成功清除路径；取消/失败不清，提示保留）。

**设置页同步调整**：

- `_on_latest_version` 对 PATH 来源**提前 return**：不亮圆点/Badge、不弹「前往设置」条——该条对 PATH 是死循环引导（去设置页点「下载/更新」仍回到"请自行更新"）；PATH 新版提示单一归主窗口；
- `_update_ytdlp` 的 PATH 分支从"仅弹 6s 固定提示"（死按钮）改为**主动检查并回显**（`_start_path_check` / `_on_path_check_done`，worker 模式与 tools `_start_check` 一致，复用 `_update_worker/_update_thread` 引用）：`newer` → warning 8s（当前/最新版本号 + 引导自行更新）、`latest` → info 5s、`failed` → error 常驻（同 tools 查询失败口径，不误报已最新）、`skipped` → info 5s。结果条用短时而非常驻——PATH 的常驻提醒单一归主窗口，避免双条叠加。

验证：py_compile/lint 零错；冒烟 `check_path_ytdlp_update()` → `('latest', '2026.08.19', '2026.08.19')`、`check_tools_ytdlp_update()` → `('skipped', '', '')`（PATH 来源下互斥正确）。
