# 决策 15. 功能裁剪记录（2026-08）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 15」一一对应）

移除 M3U8 下载、X Space 下载、图片+音频合并三个功能（页面 + Functions 方法 + `TaskType.M3U8` + `M3U8TaskCard`）。理由：均可被 yt-dlp 链路替代（X Space 页纯为 yt-dlp flag 包装；M3U8 裸 ffmpeg 无重试/续传/Headers）。旧 `completed_tasks.json` 中 `task_type="m3u8"` 记录在 `_load_completed_tasks` 的 try/except 中被安全跳过。未来若需"仅音频下载"，给 `download_youtube` 加可选 `-x --audio-format` 后处理即可覆盖。
