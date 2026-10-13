# 决策 13. 关闭 Sookit 确认

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 13」一一对应）

`closeEvent` 有 RUNNING 任务时弹 `qfw.Dialog`「关闭 Sookit 会停止正在进行的任务，是否关闭？」（yesButton「关闭」/cancelButton「取消」），确认后 `quit_app` → `cancel_all()`（默认 `delete_part=True`，终止进程树 + 删各任务 workspace）。
