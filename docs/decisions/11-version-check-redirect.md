# 决策 11. 版本检查走 releases 重定向

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 11」一一对应）

`get_ytdlp_latest_version()`/`get_deno_latest_version()` 用 `_version_from_latest_url`：
- 从 GitHub `releases/latest` 的 **302 重定向 Location** 解析版本，绕开 `api.github.com` 匿名限流（403）。
- 只读重定向、**不下载 body**（`_VersionResolved` 异常中断）。
- 兼容 `/releases/download/<v>/<file>` 与 `/releases/tag/<v>` 两种路径。
