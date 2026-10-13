# 决策 12. 单实例互斥体 + 安装/卸载检测

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 12」一一对应）

- `__main__.py` 创建 `Local\Sookit` 互斥体，重复启动提示并退出。
- `installer.iss` 的 `AppMutex=Local\Sookit`，安装/卸载时弹窗询问（不自动关闭）。
