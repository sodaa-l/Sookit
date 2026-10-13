# 决策 19. 封面最终兜底 CoverFallbackWorker（2026-09-01，含 --write-thumbnail 防盗链级）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 19」一一对应）

两类目标场景：①"能下载不能显示"——显示 loader 与 `download_thumbnail` 的 HTTP 层逐字相同（UA/超时/SSL），唯一稳定差异是**解码**（下载纯写字节必成功，显示需 Qt 解码；本机 Qt 不支持 avif/heic/heif/jxl）；②**防盗链**——封面 CDN 直连 403，需 extractor 维护的 headers/cookies 才能拿到。

常规 loader 全失败后（`_on_cover_failed` 末尾触发），两级链路：
1. 缓存命中：遍历 `covers/fallback_{sha1(url)[:16]}.*`（`_find_fallback_product`，排除 `.img`）→ QImage 可解即加载（重复嗅探免重下）；
2. `Functions.download_thumbnail` 落盘 `.img`（与"下载封面"按钮同款实现）→ `QImage` 直接解码（覆盖网络抖动型失败，<1s）；**下载失败（防盗链 403）不中断**，落到 yt-dlp；
3. `yt-dlp --skip-download --write-thumbnail --socket-timeout 20 --retries 1`（`build_ytdlp_cmd` 自动带 deno）→ 遍历产物 → QImage 解码；
4. 产物 QImage 解不了（如 AVIF）→ **统一放弃**：保持占位 + 日志报 `_sniff_image_format` 魔数检测的实际格式（`yt-dlp 产物无法解码（AVIF）`）。

**yt-dlp 缩略图机制要点（实测）**：
- `--print after_move:filepath` 与 `--write-thumbnail` 组合下 **stdout 恒空**（`--print` 隐含 simulate，after_move 阶段不触发）——产物路径不能靠 print，用固定 `-o` 模板 + 遍历产物文件；
- 不用 `--convert-thumbnails jpg`：该转码由 yt-dlp 内部调 ffmpeg 执行，**查找权在 yt-dlp**（`--ffmpeg-location` > PATH），需显式传内置 ffmpeg 路径；改遍历产物后无需此依赖（webp/png/jpg Qt 原生可解）。对比：下载后的音视频合并正常，是因为合并由 Sookit 自己拿 `get_ffmpeg_path()` 绝对路径调 `run_ffmpeg`——查找主体不同；
- `--socket-timeout 20 --retries 1` 保证 yt-dlp 进程时长有界（最坏约 1~2 分钟），否则网络黑洞时进程无限挂起。

约束与边界：**QThread.run 内用 QImage 判断解码（QImage 可跨线程），QPixmap 仅主线程**；`run_ytdlp` 是阻塞同步调用；同 URL 会话内只兜底一次（`_fallback_tried_urls` 防循环）；`.img` 为纯中间文件不参与缓存命中，残留由启动清理兜底（`__main__._cleanup_fallback_covers()` 启动时清 `fallback_*`，与任务封面 `{task_id}.jpg` 命名空间隔离）；已知无解：SVG 打包态缺 qsvg 插件且 ffmpeg 默认构建无 SVG 解码器（影响面极小，维持占位+日志）；AVIF 等极端格式**不做转码**（用户决策：放弃，日志报格式即可），`_download_cover` 扩展名白名单已含 `.avif`（下载保留真实扩展名）。
