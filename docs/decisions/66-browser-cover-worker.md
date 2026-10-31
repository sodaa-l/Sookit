# 决策 66. 浏览器封面逐档直接下载（2026-10-31）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)

## 背景

决策 64 的初版让扩展封面使用 yt-dlp 的 `--write-thumbnail` 并进入任务队列。嗅探页已有专用封面下载流程：直接下载所选 thumbnail URL，并通过 `CoverDownloadWorker` 和 `_on_cover_download_done` 提示结果。扩展封面应复用这条 YouTube 封面路径，同时按最佳到最低分辨率回退。

## 决策

- 浏览器请求开关及 YouTube URL 校验通过后，封面动作提取 video ID，按 `build_thumbnails(video_id)` 返回顺序将 URL 列表交给 `YouTubePage.CoverDownloadWorker`。每档调用 `Functions.download_thumbnail`；当前档失败或没有生成非空文件时，清理部分文件并尝试下一档。全部失败后复用现有封面失败提示。
- 输出文件按 `<视频ID>_cover.<URL扩展名>` 命名。已存在或被运行中 worker 占用的名称都视为冲突，依次追加 `_1`、`_2` 等序号。浏览器动作使用请求本身的视频 ID，不依赖嗅探页的当前视频状态。
- `_cover_download_workers` 持有所有并发 worker，并在 QThread 自带的 `finished` 信号后释放引用。下载结果用单独的 `download_finished(success, path)` 信号回到页面，复用 `_on_cover_download_done` 的成功/失败 InfoBar 与系统结果通知；通知遵循现有开关和主窗口失焦规则。
- 浏览器封面直接下载，不进入 `TaskType.YTDLP` 队列。扩展提示封面下载已开始，高分辨率不可用时会自动尝试下一档；完成或失败由共享回调提供 InfoBar 与系统结果通知。
- `download_best` 保持决策 64 的既有实现：使用 `BEST_VIDEO_FORMAT`，仍经 yt-dlp 任务队列下载。扩展任务在 Worker 启动时发送开始系统通知，完成/失败沿用任务队列通知。`browser_downloads.py` 只保留该格式常量，不再负责封面下载。

## 验证覆盖

`tests/test_browser_downloads.py` 覆盖封面动作在 yt-dlp 不可用时仍直接启动、缩略图 URL 顺序回退、GUI 单 URL 兼容、失败残留清理、并发路径冲突、完成后释放 worker 引用，以及最佳画质继续入队并保留真实 yt-dlp/FFmpeg 下载验证。封面 worker 测试使用 mock，不依赖 yt-dlp 或外网。

针对性命令：`uv run --no-sync python -X utf8 -m unittest discover -s tests -p test_browser_downloads.py -v`。
