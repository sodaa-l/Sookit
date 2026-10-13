# 决策 30. 「动图下载」页接 UI + 自定义编码质量（2026-10-07）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 30」一一对应）

背景：决策 29 只做了内核 + 队列；本次接 UI。产品决策：**不做嗅探阶段**，用户输入 X 链接直接进入
下载流程，下载仍交 yt-dlp（此前调研结论：syndication/fxtwitter 双源嗅探 + 直链下载方案已验证可行，
见会话记录，暂不启用）。

**页面**（`pages/gif_page.py`，导航位于直播监控之后，`FIF.DOWNLOAD`）：
- 页内标题「X (Twitter) 动图下载」（**导航栏条目仍为「动图下载」**）；输出目录占位文案
  「默认: /下载」（与嗅探页/直播监控页统一，默认目录即系统「下载」文件夹——
  `functions.DEFAULT_OUTPUT_DIR` 由 `SHGetKnownFolderPath(FOLDERID_Downloads)` 取得，失败回退 `~/Downloads`）；
- URL 输入框 + 输出目录（空 = `DEFAULT_OUTPUT_DIR`）+ 质量下拉（四档预设 + 自定义…）；
- 选「自定义…」页内展开 `CardWidget` 参数卡：宽度（`QIntValidator`，高度按源宽高比自动）、
  帧率上限（`QDoubleValidator`，**上限语义**：源 ≤ 值保持源帧率，超过锁值，与预设档一致）、
  色数下拉（256/128/64/32/24/16）、dither 下拉（none/bayer/floyd_steinberg/sierra2_4a）；
- 入队前校验：URL 须含 `x.com|twitter.com` + `/status/\d+`；自定义参数非法 InfoBar 提示不入队。

**内核扩展**（`convert_to_gif` 新增关键字参数 `custom_params`）：
- 新增 `GifCustomParams(width, fps_cap, colors, dither)` 与 `DITHER_CHOICES`，`validate()` 校验；
- 自定义模式：跳过预算搜索/MAE 色数决策，仿最佳质量档单次编码，不约束体积；
  宽度超过源宽度时收敛到源宽度；quality 忽略并强制 `GifQuality.CUSTOM`；
- `_encode_gif` 的 dither 参数化（默认 `'none'`），**预设档产物与 `auto_gif.py` 基准 md5 逐字节一致**（实测）。

**组合函数**（`Functions.download_and_convert_gif(url, output_dir, quality, custom_params, use_aria2c, connections, log, on_process_created, on_progress)`）：
- 先剥 `/video/N`、`/photo/N` 路径后缀——yt-dlp 对 animated_gif 遇 `/video/N` 报 `Media #N is not a video`；
- `-f "b[ext=mp4]/b"` 下载到 `tempfile.mkdtemp(prefix="sookit_gif_dl_")`；**成功**转码后随目录清理
  （输出目录只留 .gif）；**失败**把已下载 MP4 移入输出目录保留便于排查；
- 一条推文含多个视频时逐个转码，返回单文件 str / 多文件 list（TaskWorker 两分支均已支持）；
- 进度：下载阶段保持 0（GIF 任务不走 YTDLP 进度解析），转码把 0~100 映射到 30~100（多视频均分区间）；
- GIF 任务不启用 TaskQueue workspace（仅 YTDLP 启用），中间 MP4 由函数自管临时目录，与 convert_to_gif 的 tmpdir 同款语义。

**UI 先测后接**：`scripts/preview_gif_page.py` 在最小 FluentWindow 中只挂 GifPage
（`--queue` 附带任务队列页），`uv run python scripts/preview_gif_page.py` 直接运行，页面代码与正式接入同一份。

**验证**：标准档 md5 回归通过（真实 X 动图样本）；自定义模式 ffprobe 复核
（240×240、帧率上限语义正确、dither 改变产物）；端到端 `/video/1` 链接剥离 + MP4 清理 + 进度映射通过；
offscreen 页面构造/卡片显隐/参数校验/主窗口导入通过；GBK 环境（未设 PYTHONUTF8）日志无编码异常。

**队列显示复用下载任务（2026-10-07 追加）**：此前动图任务走 `TaskCardBase`（无封面）、标题固定为
`动图下载 - <status_id>`，与视频下载任务不一致。改为：
- 入队前先用 `GenericWorker` 跑 `pages/gif_page._fetch_gif_meta`：`Functions.sniff_youtube`
  （`yt-dlp -J --no-playlist`，URL 先按 `_strip_media_index` 剥离 `/video/N`、`/photo/N`）
  取 `title/channel/duration/thumbnails[0].url`，随后在同一后台线程把缩略图字节下载进
  `metadata['cover_data']`（失败只留 `cover_url`，卡片自行异步下载）；期间禁用下载按钮 +
  InfoBar「正在获取动图信息」，完成后自动入队。**仅取元数据，不下载媒体**，仍无嗅探阶段。
- 元数据获取失败不阻断：日志 + InfoBar 提示后按 `动图下载 - <status_id>` 兜底入队，metadata 只带
  原有 `url/out_dir/quality/filename`。
- `create_task_card` 中 `TaskType.GIF` 复用 `YtDlpTaskCard`；已完成卡片 `CompletedThumbnailCard`
  本就是通用组件（`CoverAreaWidget` 的 `cover_data → cover_cache → input_img → input_video → cover_url`
  链路 + `task.title`）。因此动图任务在队列中与下载任务同款：进行中带封面 + 视频标题，完成后走同一套缩略图渲染。
- 已知取舍：每次入队多一次 yt-dlp 元数据请求 + 一张缩略图下载（约 1~3 秒才入队）；多动图推文因
  `--no-playlist` 只取首条条目的标题/封面，转码仍按既有逻辑产出多个 GIF。

**验证（本次追加）**：`QT_QPA_PLATFORM=offscreen` 冒烟 24 项断言全通过（meta 字段映射 / 封面字节下载失败
只留 `cover_url` / `thumbnails` 为空回退 `info['thumbnail']` / `/video/N`、`/photo/1` 剥离 /
`TaskType.GIF → YtDlpTaskCard` / 已完成卡片标题取 `task.title` 且频道时长同款 / 入队 metadata 保留
`url/out_dir/quality/filename` 并携带 `cover_data` / 元数据失败回退 `动图下载 - <status_id>` 且不写封面字段 /
按钮恢复），GBK 与 UTF-8 双环境各跑一遍；新增日志文本经 `encode('gbk')` 自检通过
（顺带确认既有 `base.PageBase.log` 的 `▶` / `✅` 在 GBK 控制台仍不可编码——**既有问题，本次未动**）。

**playlist 下钻 + output_path 回填修复（2026-10-07 真实链接实测追加）**：用真实 X 链接
（单推文含 3 个视频）在真实主窗口（离屏）端到端实测，暴露两个问题并修复：
- **yt-dlp 对多条目推文恒返回 playlist**（`--no-playlist` 对 twitter extractor 无效），顶层只有
  title/channel，`thumbnails`/`duration` 都在 `entries[*]` 里 → `sniff_youtube`（只看顶层）封面永远为空。
  新增 `Functions.fetch_media_meta(url)`：配置为**不加 `--no-playlist`**，playlist 形态下
  title/channel 取顶层（推文文本/作者）、duration 与缩略图下钻 `entries[0]`，非 playlist 形态全取顶层；
  缩略图站点分流抽为模块级 `_thumbnails_for(info)`，与 `sniff_youtube` 共用（嗅探页行为零变化，
  刻意不在 `sniff_youtube` 内处理 playlist——那会把"多条目推文的 formats 语义 / `-f` 作用范围"
  这个未定义的产品问题引入主功能页）。动图页改调 `fetch_media_meta`。
- **既有缺陷**：`TaskQueueManager._on_finished` 的 metadata 兜底回填会把 GIF 任务（无 workspace、
  metadata 只有 `out_dir`）的**目录**当成 `output_path` → 已完成卡片「打开文件」打开目录、
  `_is_batch_task()` 误判批量任务而跳过删除确认。改为在 workspace 分支之后**优先用
  `worker.output_files[0]`**（回退 `worker._output_path`）回填，再走 metadata 兜底；
  GIF/FFMPEG 任务由此与下载任务行为对齐。

**实测验证（2026-10-07）**：真实主窗口 + 真实链接端到端 14 项断言全通过——标题为真实视频标题
（非 `动图下载 - <id>` 兜底）、`cover_url` = `pbs.twimg.com/...?name=medium`、`cover_data` 51846 字节、
进行中卡片 `YtDlpTaskCard` 封面 pixmap 270×152、完成后 `CompletedThumbnailCard` 封面 1200×675、
`output_path` 指向真实 `#1_2.gif`；另离线 14 项断言覆盖 `fetch_media_meta` 的 playlist/单条目形态与
`sniff_youtube` 回归；6 项断言覆盖 `output_path` 回填优先级（GIF 真实产物 / FFMPEG 不变 / 无产物回退 /
YTDLP workspace 优先且删除调用不变）。测试用 `VIDEOTOOLBOX_DATA_DIR/COVER_DIR/LOG_DIR` 重定向到临时目录，
不写真实 completed_tasks.json。

**只处理动图（2026-10-07 用户实测追加）**：此前 `-f "b[ext=mp4]/b"` 会下载 playlist 的全部条目，
同一条推文里的普通视频也被转成 GIF（实测「2 动图 + 1 个 4K/23.8s 视频」中视频被转出 9.60MB GIF）。
新增 `_looks_like_animated(entry)`：X 的 animated_gif 条目 `duration` 为 None 且 `resolution`/`width`/`height`
全空，普通视频有 `duration` 且有分辨率（误判方向刻意保守：多下一个动图，不漏真动图）。
`download_and_convert_gif` 在下载前先 `-J` 判定条目，用 `--playlist-items` 只下载动图
（`download_youtube` 因此新增可选参数 `extra_args`，默认 None，既有下载任务行为不变）；
**整条链接无动图时直接抛错**（任务失败并提示"该链接没有动图"），不再静默转换视频。
日志阶段号相应为 ①识别条目 ②下载动图 ③转码。实测同一链接：
`共 3 个条目，其中动图 2 个，已跳过 1 个普通视频` → 只产出 2 个 GIF。
