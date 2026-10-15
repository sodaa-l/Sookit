# 决策 38. 合并落点预测 + mkv 风险弹窗，移除全局 --hls-prefer-ffmpeg（2026-10-15）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 38」一一对应）
> 修订[决策 33](33-hls-prefer-ffmpeg.md)。

## 背景

决策 33 起所有下载任务在 ffmpeg 可用时一律加 `--hls-prefer-ffmpeg`，让 HLS 流由
ffmpeg 边下边转封装（时间戳归一化），代价是：

1. **ffmpeg 单连接下载 HLS，慢**，且 `--downloader aria2c -x 16` 完全不作用于这一段；
2. **ffmpeg HLS 阶段零进度输出**：ffmpeg 的逐帧统计行（`frame= ... time= ...`）被
   `run_ytdlp` 的噪声过滤器全部丢弃，UI 进度冻结、日志无动静，实测一个 1.5GB 的
   HLS 视频整个下载期间表现为「卡死」（2026-10-15 用户报告，实为可见性问题）。

同时实测确认：决策 33 的坏包问题只在合并落点为 **mkv** 时致命（mp4/TS 封装器容忍
无时间戳的包）；而落点由编码对决定——vp9+opus → webm、h264+aac → mp4 都安全，
opus+h264、aac+vp9 这类跨族组合才会兜底进 mkv。

## 修订内容

**移除 `Functions.download_youtube` 里全局的 `--hls-prefer-ffmpeg`**，HLS 回到
yt-dlp 原生下载器/aria2c（进度可见性随之恢复），非标组合的风险改在 UI 层拦截：

- `YouTubePage.do_download` 在入队前调用 `_predict_mkv_merge` 预测合并落点：
  仅当恰好勾了「一个视频流 + 一个音频流」时判定；视频/音频编码同族
  （webm 族：vp8/vp9/vp09/av01 + opus/vorbis；mp4 族：avc1/h264/h265/hevc/av01 +
  mp4a/aac/m4a；av01 双族——YouTube 的 av1 流 ext 有 webm/mp4 两种，配 opus 合
  webm、配 mp4a 合 mp4 均为标准组合）放行，已知且跨族的组合弹窗；
- 弹窗（qfw `MessageBox`）说明没有共同标准容器、可能导致下载失败，引导改选
  avc1/hevc/av01 + mp4a 或 vp9/av01 + opus（与表格「编码」列展示名一致）；
  带「仍要下载 / 返回修改」两个按钮，用户明确选择后可强行继续；
  文案手动预折行——qfw MessageBox 宽度取未折行文本的 sizeHint，不预折行会撑得
  极宽（实测 ~700px）；
- 只勾单一格式 / 「视频+音频」合并流 / 编码未知（空、不认识的 codec 主名）→
  不判定、放行（宁漏报不误报）。

## 各路径的处置依据

| 路径 | 处置 | 依据 |
| --- | --- | --- |
| 嗅探页下载 | 预测 + 弹窗引导 | 格式由用户手选，可能跨族 |
| 监控页下载 | 直接放行 | `bestvideo+bestaudio/best` 自动选格式，默认排序偏好 vp9.2/vp9 视频 + opus 音频 → webm；vp9 缺失时 h264+m4a → mp4，不会落跨族组合 |
| GIF 下载 | 直接放行 | `b[ext=mp4]/b` 单格式选择，无合并环节；X 不产 HLS |

## 验证（2026-10-15）

- 命令拼装断言：`download_youtube` 函数体不再追加 `--hls-prefer-ffmpeg`，
  也不再依赖 `check_ffmpeg()` 判断；`--no-check-formats`（决策 36）保留；
- 判定函数用例：vp9+opus、vp09+opus、av01+opus、av01+mp4a、h264+aac、
  avc1+mp4a、avc1+aac 放行；h264+opus、avc1+opus、vp9+mp4a、vp9+aac 拦截；
  编码缺失 / 单格式 / 合并流 / 含「其他」类型不拦截；
- **端到端待验证**：需重启 Sookit 后实测（下载 `-f 251-drc+614` 确认 HLS 流走
  原生下载器且 `[download]` 进度行出现、合并落点 webm 成功）。

## 遗留

- 监控页理论上仍可能遇到（罕见）vp9 全缺失且仅 opus 音频可用的视频 → mkv 兜底
  → 合并失败；概率极低，暂不处理，失败时决策 34 的诊断回放可定位；
- 「其他」类型格式（无视频/音频编码）参与勾选时不判定，跨族组合理论上仍可
  手工拼出，依赖决策 34 失败诊断兜底。
