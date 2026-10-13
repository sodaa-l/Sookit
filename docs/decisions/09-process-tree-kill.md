# 决策 9. 进程树终止：taskkill /T /F

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 9」一一对应）

Sookit 下载任务用 `taskkill /PID <launcher> /T /F` 递归终止整个进程树（yt-dlp launcher → real yt-dlp → aria2c）。

- **不按进程名全局杀**，只针对本任务 PID 树，避免误伤其他任务/updater。
- `_kill_process_tree(pid)` 封装（workers.py），taskkill 失败/进程已退静默。
- **不用 Job Object**：实测孙进程不继承 Job、无法事后 Assign（err=5），故用 taskkill。
