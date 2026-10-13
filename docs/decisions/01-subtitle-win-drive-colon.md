# 决策 1. 字幕烧录：Windows 驱动器冒号问题

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 1」一一对应）

**现象**: FFmpeg subtitles 滤镜将 `E:/path/sub.ass` 中的 `E:` 误解析为选项名。

**最终方案**: `os.chdir()` 到字幕所在目录，filtergraph 只用文件名。

**代价**: 影响全局 cwd，`finally` 块保证恢复。
