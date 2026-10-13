# 决策 34. 下载失败时的诊断回放（2026-10-11）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 34」一一对应）

**动机**：决策 33 那次排查真正卡住的地方是**日志看不到 ffmpeg 报了什么** ——
应用给 yt-dlp 传了 `--no-warnings`，`FFmpegMergerPP` 失败时只抛一句
`ERROR: Postprocessing: Conversion failed!`；ffmpeg 的命令行与 stderr 只有 yt-dlp 加 `-v`
才打印。没有这些信息，只能靠「复现下载 + `-v --keep-video`」手工定位（这次就是这么做的）。

**做法**：`core/ffmpeg_utils.run_ytdlp` 新增 `diagnostics=False` 形参，
`Functions.download_youtube` 传 `True`：
- 诊断模式下给 yt-dlp 加 `-v`，输出按行缓冲进 `deque(maxlen=300)`；
- **噪声行不进日志**：`[debug] ...`、ffmpeg 组件日志（`[https @ 0000020874d31040] Opening ... for reading`）、
  `frame= ...` 等逐请求/逐帧输出。开了 `-v` 或用 ffmpeg 下载 HLS 后这些会**每个分片刷一行**，
  放进日志会把 1MB×3 的轮转日志冲掉（`_is_ytdlp_noise()` 判定，注意 fp 地址是**裸十六进制、没有 `0x`**）；
- 任务失败时把缓冲的**最后 40 行**回放进日志（标题「↓ yt-dlp 详细输出尾部（诊断）：」），
  成功则只字不留；
- `_error_lines` 顺手加了 20 行上限（`-v` 下 WARNING 可能变多）。

**验证（2026-10-11）**：
- 19 项断言：噪声判定（6 类噪声过滤 / 4 类正常行保留）；失败路径（有回放、回放含 verbose 级细节、
  实时日志无 `[debug]`、保留 `ERROR:`）；成功路径（5 秒 HLS 真下载：产物正常、无回放、
  日志无 `[debug]` / `[https @ ...]` / `frame=` 噪声、进度行仍在）。
- **真实失败回放**：用 `extra_args=["--hls-prefer-native"]` 覆盖修复、故意走回出问题的老路径
  （`251+270` 合 mkv），日志里直接出现了当初必须手工复现才能拿到的 ffmpeg 原始报错：
  `[matroska @ ...] Timestamps are unset in a packet for stream 1` →
  `Can't write packet with unknown timestamp` → `Error muxing a packet` → `Conversion failed!`，
  还带上了 `Output #0, matroska, to '...temp.mkv'` 与 `yt_dlp.postprocessor.ffmpeg` 调用栈。
