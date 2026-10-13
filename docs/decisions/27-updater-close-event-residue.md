# 决策 27. updater 点 X 关窗后进程残留：closeEvent ignore 毒化 QDialog 关闭状态（2026-09-05）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 27」一一对应）

**现象**：updater.exe 下载中途点 X 关闭窗口（与点「取消」按钮不同）后进程不退出（无窗口静默残留），但结果文件已正常写出（Sookit 照常提示安装）；Inno 安装器经 Restart Manager 检测到该进程锁定 `{app}\updater.exe`，尝试关闭失败 → 安装失败。多次点 X 累积多个残留进程。宿主机 `taskkill`（不带 /F，WM_CLOSE）100% 复现。

**根因（最小复现实锤，PyQt6 6.11）**：`closeEvent` 里 `e.ignore() + hide()` 拒绝关闭后，Qt 内部 `QWidgetPrivate::data.is_closing` 残留为 True。此后 `_finish()` 的 `accept()` → `done()` → `QDialogPrivate::close()` 检测 is_closing 走 `else { q->hide(); }` 分支——**纯隐藏，不走 `QWidget::close()`、不触发 lastWindowClosed**，`app.exec()` 永不返回，进程无窗挂死。且挂死后**一切关闭请求（含安装器 WM_CLOSE）被 `close_helper` 开头的 `if (data.is_closing) return true` 短路成 no-op**——外部（安装器）无法关闭。点「取消」按钮与下载自然完成不经 closeEvent，状态干净，故无此问题。

**修复**：`_finish()` 里 `accept()` 之后显式 `QApplication.quit()`——直接退出事件循环，不依赖 `quitOnLastWindowClosed` 隐式链，覆盖所有收尾路径（自然完成 / 取消 / 点 X 转取消）。`main()` 与 `_main_app_setup()` 在 `app.exec()` 返回后补 `[EXIT]` 日志行（此前日志无"进程退出"记录，无法区分"结果已写但进程未退"）。

**教训**：QDialog 的"忽略关闭 + 稍后自行收尾"模式（closeEvent 里 ignore）**必须配显式 `QApplication.quit()` 兜底**；仅依赖 accept/lastWindowClosed 隐式链，在 closeEvent 被 ignore 过一次后即永久失效（is_closing 不复位）。验证三连：offscreen 最小复现（ignore+hide → accept 挂死；accept+quit 正常返回）→ 打包态 WM_CLOSE 场景（修复前残留 202MB 进程，修复后 15s 内退出且结果 JSON 正常）→ 完成路径回归（下载完成自动退出无变化）。
