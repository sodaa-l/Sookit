# 决策 10. updater.exe 独立下载器

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 10」一一对应）

yt-dlp/Deno 下载更新从 Sookit 解耦为独立 `updater.exe`：

- Sookit 只查版本（`check_ytdlp_deno_update_needed`），需更新才 `launch_ytdlp_updater` 提权调起。
- 通信：Sookit 生成 result_path → runas 启动 `updater.exe --ytdlp-updater-gui <result_path>` → updater 写结果 JSON → Sookit 轮询（终止条件：**结果文件出现或 updater.exe 进程消失**；进程消失 + 无结果 = 下载器崩溃/被强杀，立即判失败；结果文件读取后删除，避免累积 `.ytdlp_updater_result_*.json`，坏 JSON 同样清理）。
- aria2c 设置自动跟随（复用 `download_ytdlp`/`download_deno`）。
- **updater 取消的 QThread 竞态崩溃（2026-08 修复）**：`done.emit` 同时触发 `_on_done`/`thread.quit`/`finished→deleteLater`，与 `_poll_cancel`（QTimer 100ms）存在生命周期竞态——QThread C++ 对象被删后访问 `isRunning()` 抛 RuntimeError，主线程 QTimer 回调未捕获异常 → updater 静默崩溃（无结果文件、Sookit 超时）。修复：不连接 `deleteLater`（QThread 随对话框销毁回收）+ `_thread_alive()` 防御（RuntimeError 按线程已结束处理）。
- updater 取消/关闭（2026-08 重构为非阻塞）：
  - `_download_file_with_aria2c` 用 reader 线程 + `queue.get(timeout=0.2)` 非阻塞读 stdout，取消不被 read 阻塞；
  - `_on_cancel` **不阻塞 GUI 线程**：设标志 + 显示"正在取消"后立即返回，`QTimer` 每 100ms 轮询线程状态（`_poll_cancel`）；
  - 超时兜底（20s）：先 `force_terminate()` 杀 aria2c 进程树 → `wait(5000)` → **done 信号的真实结果优先**于硬编码 cancelled（解决"取消瞬间下载恰好完成、文件已被替换"竞态）→ 确实卡死才写 cancelled 关窗；
  - `closeEvent` 未完成时 `ignore()` + `hide()`，后台写完结果文件再退出（Sookit 不会空等超时）；
  - 取消确认后直接关窗，不显示"已取消"文字（停留时间太短无可读性）。
- `_DownloadWorker` 通过 `on_proc=self.set_proc` 记录当前 aria2c Popen；`force_terminate` 先 terminate，仍存活则 `taskkill /PID <specific_pid> /T /F`（不按进程名全局杀）。
