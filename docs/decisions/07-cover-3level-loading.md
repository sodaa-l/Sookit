# 决策 7. 封面三级加载策略 (FfmpegTaskCard)

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 7」一一对应）

cover_data (内存) → cover_cache (磁盘) → input_img / input_video (异步截帧) → fallback 图标。
