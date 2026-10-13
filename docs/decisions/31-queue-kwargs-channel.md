# 决策 31. 任务队列通用 kwargs 通道（2026-10-11）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 31」一一对应）

背景：决策 30 需要 `download_and_convert_gif` 有一个**关键字专用**形参（`selected_indexes`），
但队列此前只有位置通道——`PageBase.run_queued_task` → `TaskQueueManager.add_task` → `Task` →
`TaskWorker` 一路只存 `args`，执行时再由 `TaskWorker` 固定注入
`log/on_process_created/on_progress`（YTDLP 另注入 `workspace`）。没有 kwargs 通道时关键字专用参数
根本传不进去；而把参数塞在位置参数中段，任何按旧签名写的调用点都会静默错位
（例：旧签名第 5 位是 `use_aria2c`，写 `False` 会落到新参数上 → 假值 → 页面勾选被静默忽略、全下）。

- **新增通道**：`run_queued_task(..., kwargs=None)` → `add_task(..., kwargs=None)` →
  `Task.kwargs`（默认 `{}`）→ `TaskWorker(..., kwargs=...)` → 四个执行分支统一改为
  `self.func(*self.args, **self.kwargs, log=..., ...)`。默认空 dict，既有任务
  （YTDLP / FFMPEG / GIF 的现有调用点）行为逐字不变。
- **保留名**：`workers.RESERVED_TASK_KWARGS = {log, on_process_created, on_progress, workspace}`
  是 TaskWorker 自己注入的名字，kwargs 通道不得占用。占用时 `TaskWorker.run()` 在**执行前**抛
  RuntimeError（"任务参数 kwargs 占用了 TaskWorker 注入的保留名: …"），落到既有 `except` → 任务失败
  + 明确报错，而不是底层的 `TypeError: got multiple values for argument 'log'`。校验刻意放在 `try`
  之内：否则异常逃出 `run()` 就不会 emit `finished_signal`，任务会卡在「进行中」。
- **不做的事**：`Worker`（`PageBase.run_task` 的单任务路径）不加 kwargs——它固定注入 `log`、只服务
  父类页面，没有这个需求；`Task` 持久化（`_task_to_dict`）本就不存 func/args/kwargs，已完成任务还原时
  用占位 `lambda: None`，与本次无关。
- **验证（2026-10-11 冒烟 E 节）**：GIF 任务经 `add_task(..., kwargs={'selected_indexes': [3]})`
  端到端送达任务函数（且三个回调仍被注入）；`kwargs={'log': 'x'}` → 任务失败且 `task.error` 含「保留名」；
  FFMPEG 任务 `Task.kwargs == {}`、行为不变；YTDLP 分支同步 `TaskWorker.run()` 空 kwargs 不报错。
