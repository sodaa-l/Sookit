# 决策 33. HLS（m3u8）流改用 ffmpeg 下载（2026-10-11）

> ⚠️ **2026-10-15 起已被[决策 38](38-merge-target-prediction.md)修订**：`download_youtube`
> 不再加 `--hls-prefer-ffmpeg`，改由嗅探页预测合并落点 + mkv 风险弹窗引导。
> 本文的根因分析（hlsnative 坏包、mkv 才致命）仍然成立，是决策 38 的依据。

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 33」一一对应）

**症状**：某 1080p YouTube 视频用 `-f 251+270` 下载，下载阶段一切正常，到合并阶段失败：
`[Merger] Merging formats into "...mkv"` 紧接 `ERROR: Postprocessing: Conversion failed!`；
应用日志只有这一句（传了 `--no-warnings`，ffmpeg 的 stderr 只有 yt-dlp 加 `-v` 才会打印）。

**根因（用 `-v --keep-video` 复现定位）**：该视频只有 HLS 格式（yt-dlp 走了 visionOS 客户端，
只返回 m3u8），`hlsnative` 把 40 个分片直拼成 `...f270.mp4`（**内容是 MPEG-TS**），
每个分片交界处残留一个**无 PTS/DTS 的包**（实测 40 分片 → 40 个 `pts=N/A dts=N/A`）。
合并进 mkv 时 Matroska 封装器直接失败：

```
[matroska] Timestamps are unset in a packet for stream 1 ...
[matroska] Can't write packet with unknown timestamp
[vost#0:1/copy] Error submitting a packet to the muxer: Invalid argument
Conversion failed!
```

为什么只有 mkv 中招：yt-dlp 按两条流的编解码挑容器 —— `251`(webm/opus) + `270`(mp4/h264)
没有共同的标准容器（mp4 不标准地装 opus、webm 装不了 h264），落到 matroska 兜底；
而 mp4 / TS 封装器容忍缺时间戳的包（历史日志里同一链接 `140+270 → .mp4` 一直成功，
这正是「换成 m4a 音频就没事」的原因）。

**排除项（均实测，mkv 下全部失败）**：`-fflags +genpts`、`+igndts`、`avoid_negative_ts make_zero`、
`copyts`(+`start_at_zero`)、`max_interleave_delta 0`、`-vsync 0`、`-strict -2/unofficial`、
`-copyinkf`、mkv `-live 1`、`-muxdelay/-muxpreload 0`、`setts` 条件式补时间戳；
换 ffmpeg 构建（gyan essentials 8.1.2 与 full 8.1.1 都试过）也无效。
唯一可行的是「让视频先经过一次 mp4 封装」（时间戳被归一化，实测之后合 mkv rc=0），
但 yt-dlp 的下载→合并流程里插不进中间步骤。

**修法**：`Functions.download_youtube` 在 **ffmpeg 可用**时追加 `--hls-prefer-ffmpeg`，
让 HLS 交给 ffmpeg 边下边转封装 —— 时间戳在下载阶段就被规范化。
DASH / 直链不受影响（该参数只作用于 HLS）；ffmpeg 不可用时**不加**，
避免把「本来能下、不需要合并」的流变成报错。

**验证（2026-10-11）**：
- 离线：ffmpeg 下载出的 HLS 产物 **0 个缺 PTS 包**（hlsnative 产物 40 个），且该产物与 opus 音频
  合并进 mkv `rc=0`；同期 14 种 ffmpeg 参数组合在 mkv 下全部失败（对照）。
- 命令拼装 7 项断言：flag 存在、位置在 URL 与 `extra_args` 之前、无 ffmpeg 时不加、
  格式与输出模板不变、GIF 路径（带 `--playlist-items`）同样带上。
- **端到端**：直接调用应用自己的 `Functions.download_youtube(url, "251+270", …)` 实跑（约 70MB），
  合并成功、返回 `.mkv` 69.6MB、ffprobe 两条流（opus + h264 1920x1080）；
  修复前同一组合连续两次都停在合并阶段。

**遗留**（同日由决策 34 解决）：合并类失败时应用日志看不到 ffmpeg 的 stderr
（yt-dlp 只在 `-v` 下打印），排查成本高。
