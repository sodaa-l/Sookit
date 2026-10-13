# 决策 2. 统一任务队列单例

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 2」一一对应）

- `TaskQueueManager(QObject)` 单例，并发数控制 (默认 3)、暂停/恢复/取消、进度、日志。
- 已完成任务持久化到 `%APPDATA%\Sookit\completed_tasks.json`，JSON 损坏自动备份 `.bak`。
- 取消任务用 `_cancelling_workers` 保留 QThread 引用直到 `finished`，防 "QThread: Destroyed while thread is still running"。
