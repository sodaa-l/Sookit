# 决策 25. Sookit 自更新：四态检查 + 常驻 InfoBar 取代模态 Dialog（2026-09-05）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 25」一一对应）

背景：`build.260830.1` 用户点「检查更新」无反应——检查其实成功，但 `prompt_update` 里 `qfw.MessageBox.addWidget()` 不存在（AttributeError），槽内未捕获异常被全局钩子吞掉，Dialog 没建出来。趁修复重构整个检查回显流程：

- **`core/updater.py` 更名 `core/app_update.py`**：与顶层 `sookit/updater.py`（updater.exe 入口）重名易混淆。updater.exe 入口文件名与 spec 的 Analysis 对应且 exe 名不变，故改 core 侧；引用点仅 `functions.py` 一处 import（main_window 经 functions 再导出）。
- **`check_latest_version()` 改四态返回 `(status, version)`**：`newer` / `ignored` / `latest` / `failed`。旧签名返回 `None` 混合了"查询失败/已最新/已忽略"三义，导致**网络失败被误报为"已是最新版本"**。状态如实上报，处理策略由 UI 层决定。
- **UI 分派 `_on_check_done(result, manual)`（四态 × 自动/手动）**：`newer` → 常驻条（点亮 Badge）；`ignored` → 手动视同新版弹条（文案注明"此前已忽略"），自动静默；`latest` → 手动提示已最新，自动静默；`failed` → 手动弹失败条引导 GitHub，自动静默。
- **模态 Dialog → 常驻 InfoBar**（`_show_update_bar`，warning 级挂**主窗口**全局可见，duration=-1）：`PrimaryPushButton「下载更新」`→ 关条进 `_do_update`；`PushButton「忽略此版本」`→ 写忽略 + 关条 + 清 Badge。点 X 仅关条不写忽略。引用 `self._update_bar` 防重复检查叠加，新建前先关旧条。
- **失败条 `_show_update_failed_bar`**：warning 常驻 +「前往 GitHub 下载」按钮 `QDesktopServices.openUrl(RELEASES_URL)`（releases/latest 页）。**检查失败不得报成功**。
- 更新流程全部 InfoBar（下载进度/下载完成/失败）parent 统一为主窗口 `self`（原 `_info_parent()` 挂检查时的当前页面，切页即不可见）。
- 坑：qfw `MessageBox` 没有 `addWidget`（那是 QLayout/QMessageBox 的 API）；qfw 对话框加自定义按钮要走 `buttonLayout.insertWidget` 或改用 `MessageBoxBase.viewLayout`——本方案直接弃用 Dialog，规避该坑。
- **周期自动检查循环（借鉴 Cherry Studio AppUpdaterService 调度策略，2026-09-05）**：启动首查后不再"查一次就完"，`_schedule_next_auto_check` 用 `QTimer.singleShot` 自重臂成循环——成功 → 正常周期 4h ± 15% 随机抖动（防大量客户端集中打更新源）；失败 → 指数退避 5/10/20/40min 封顶 1h（刻意短于正常周期，瞬态故障尽快恢复；计数成功即清零，仅自动检查的 failed 驱动，手动检查不干扰）。自动检查全程静默（仅未被忽略的新版弹条），同版本常驻条已在展示时（`_update_bar_kind` 记录）跳过重建，防周期检查反复关条重建闪烁。定时器挂主窗口，随窗口销毁失效，无需显式清理。
