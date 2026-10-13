# 决策 18. 封面 loader 代际校验（2026-09-01）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 18」一一对应）

**症状**：封面出现后瞬间消失/跨嗅探错配。根因：`_on_sniff_done` 补载条件只看 `_pixmap.isNull()`（快速路径的成功信号还在主线程队列时误判"无封面"）→ 同 URL 并发双 loader → 晚到的失败无差别 `clearPixmap` 清掉刚成功的成果。

修复（通用模式，新增异步 UI 加载时照此办理）：
- 信号连接带 worker 身份：`worker.failed.connect(lambda w=worker: self._on_cover_failed(w))`，回调内 `worker is not self._cover_loader_worker` 直接丢弃；
- 补载/重试前检查 `_cover_loader_worker.isRunning()`，避免同 URL 并发；
- QThread `finished` 连接清理引用（防运行中被 GC + 保证 busy 判断准确）。
