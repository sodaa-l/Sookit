# 决策 5. 暂停/恢复使用 Windows 原生 API

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 5」一一对应）

`NtSuspendProcess` / `NtResumeProcess` (ntdll.dll) 挂起/恢复子进程（Python 无 posix signal）。
