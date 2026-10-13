# 决策 36. 关闭 yt-dlp 的格式可用性探测（`--no-check-formats`）（2026-10-13）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 36」一一对应）

**症状**：下载某**新发布**视频的 HLS 格式（`-f 140-drc+270`）时任务失败，且**重试必现**：

```
[info] Testing format 270
[hlsnative] Downloading m3u8 manifest
[hlsnative] Total fragments: 3
[download] Destination: D:\VideoToolBox\tmp0au_dcw_.tmp
[download] Got error: EOF occurred in violation of protocol (_ssl.c:1007)
ERROR: fragment 1 not found, unable to continue
WARNING: Unable to download format 270. Skipping...
ERROR: [youtube] ...: Requested format is not available
```

**根因（逐段读 yt-dlp 2026.08.19 源码定位）**：

1. **谁触发探测**：`extractor/youtube/_video.py` L4555–4561 —— **上传 ≤2 天**的视频，其所有
   `m3u8_native`(HLS) 格式被打 `__needs_testing`。`YoutubeDL._build_selector_function` 里只有
   `has_drm or __needs_testing` 的格式才会进 `_check_formats()`；老视频、DASH 格式都不探测。
2. **探测做什么**：`_check_formats` 用 `dl(temp_file, f, test=True)` **真下一个分片**
   （`downloader/hls.py` L301「We only download the first fragment during the test」），
   同时兼作 DRM 预检（L110–122）。成功才放行。
3. **失败即弃用**：`_check_formats` 把**任何** `DownloadError/OSError/ValueError/网络异常` 都吞成
   `success=False` → 该格式被永久丢弃 → 显式 `-f` 选择无法成立 → `Requested format is not
   available`。**一次瞬时网络错误 = 整个任务失败**；且探测结果不跨次缓存（只在本次运行的
   `__working` 里）→ 故障窗口内**每次重试都会再死一次**（这就是"重试也一样"的原因）。
4. **为什么探测不吃我们的参数**：`dl(..., test=True)`（`YoutubeDL.py` L3288+）会**另建一套精简
   params**（只有 `test/quiet/noprogress/nopart/...`），`hls_prefer_native` 与
   `external_downloader` 都不在其中 → 下载器选择落回 `PROTOCOL_MAP['m3u8_native'] = HlsFD`
   （内建下载器 = **Python 原生 ssl**）。即：**正式下载已走 ffmpeg（决策 33），探测仍走内建**——
   探测是全链路里唯一还用 Python ssl 碰 HLS 的环节。

**决策**：`Functions.download_youtube` 的下载命令固定加 `--no-check-formats`，关掉这轮探测。理由：

1. 本 App 的格式由用户从嗅探表**显式勾选**，探测「自动跳过不可用格式」的收益基本用不上；
2. 去掉一次**冗余下载**（新视频的每个 HLS 格式都白探一次）；
3. 去掉「抖动即整单失败」的放大机制——这是本次用户实际踩到的形态；
4. 顺带消灭探测往**进程当前目录**写 `tmpXXXX.tmp` 的残留（无 `--paths temp:` 时落 CWD；
   仓库根目录那个 0 字节 `tmpmedf0uy0.tmp` 就是它的遗留）。

**代价**：yt-dlp 不再预筛 DRM/坏格式，问题推迟到正式下载阶段暴露（DRM 仍有明确文案
`This format is DRM protected; Try selecting another format...`）。

**验证（2026-10-13，用的就是那条**新发布**视频的 HLS 行）**：

- 离线命令拼装 14 项断言全过：`--no-check-formats` 恒在；`--hls-prefer-ffmpeg` 仍随 ffmpeg
  可用性出现/消失；`extra_args` 与 url 的相对顺序、`diagnostics=True` 均不变。
- E2E 走 App 自身代码路径（`Functions.download_youtube(..., "270", ...)`，含 App 给子进程设的
  `SSL_CERT_FILE` / `--encoding utf-8` / `-v` 诊断）：**5.1s 下载成功**，产物 mp4 非空；
  App 日志里 **`Testing format` 与 `hlsnative` 双双消失**（探测与原生拼分片都没了）。
- 旁证：改进前同一台机器上同命令跑 8 次（3 次完整下载 + 6 次探测压测）**全部成功**——说明该探测
  「平时隐形、失败时致命」；用户环境那一窗口内它在分片请求上稳定遇到 TLS EOF
  （`_ssl.c:1007`，与决策 30 追加段第 5 条 X 链接那次同类），故重试必现。
