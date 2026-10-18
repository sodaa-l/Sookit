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

**组合函数**（`Functions.download_and_convert_gif(url, output_dir, quality, custom_params, use_aria2c, connections, log, on_process_created, on_progress, *, selected_indexes=None)`；`selected_indexes` 为 2026-10-11 追加的末位关键字专用参数，见下文与决策 31）：
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

> **2026-10-19 起本段口径已被决策 44 改写**：动图页现在也把同一条推文里的普通视频列进
> 列表（显示「视频 N」、默认不勾选、勾选后下载前弹窗确认）。下面「只处理动图」记录的是
> 当时的问题与判定函数 `_looks_like_animated` 的由来；`_looks_like_animated` 本身原样保留，
> 只是在其上新增了 `_entry_kind` 一档。阅读实现时以决策 44 为准。

**只处理动图（2026-10-07 用户实测追加）**：此前 `-f "b[ext=mp4]/b"` 会下载 playlist 的全部条目，
同一条推文里的普通视频也被转成 GIF（实测「2 动图 + 1 个 4K/23.8s 视频」中视频被转出 9.60MB GIF）。
新增 `_looks_like_animated(entry)`：X 的 animated_gif 条目 `duration` 为 None 且 `resolution`/`width`/`height`
全空，普通视频有 `duration` 且有分辨率（误判方向刻意保守：多下一个动图，不漏真动图）。
`download_and_convert_gif` 在下载前先 `-J` 判定条目，用 `--playlist-items` 只下载动图
（`download_youtube` 因此新增可选参数 `extra_args`，默认 None，既有下载任务行为不变）；
**整条链接无动图时直接抛错**（任务失败并提示"该链接没有动图"），不再静默转换视频。
日志阶段号相应为 ①识别条目 ②下载动图 ③转码。实测同一链接：
`共 3 个条目，其中动图 2 个，已跳过 1 个普通视频` → 只产出 2 个 GIF。

**动图页两阶段选择（2026-10-11 追加，改写上面的「点按钮即入队」流程）**：

产品口径调整为：**先解析列出推文里的动图 → 在卡片网格里勾选 → 只下载并转码选中项**。
（上面「不做嗅探阶段」指的是**不预先嗅探格式**、下载仍交 yt-dlp；本次的「解析」只读条目元数据列表，
同样不下载媒体文件，两条口径不冲突。）

- **`Functions.fetch_gif_items(url)`**（新增，**取代已删除的 `Functions.fetch_media_meta`**）：
  一次完整 `yt-dlp -J`（不加 `--no-playlist`，twitter 多条目推文恒返回 playlist）→
  `entries or [info]` → 逐条目按 `_looks_like_animated` 过滤，**只列动图**；返回
  `{title, channel, items:[{index, url, title, duration, cover_url}]}`。
  **`index` 保留原始条目列表的 1-based 次序**（不是筛选后的下标）——下载时它以
  `--playlist-items` 回传，必须与 yt-dlp 看到的条目列表一致。链接无动图返回空 items
  由页面提示，**不抛错**（与「解析失败」是两码事）。
- **`download_and_convert_gif(..., *, selected_indexes=None)`**（2026-10-11 二次调整：原形参名
  `items`，位置在 `custom_params` 之后——位置参数中段插入，按旧签名写的调用点会静默错位）：
  `selected_indexes` = 页面选中的原始条目序号（1-based；None = 该链接全部动图）。实现上先与
  `_looks_like_animated` 求交集（序号可能来自过期解析，交集可防把普通视频也转成 GIF；
  交集为空抛「选中的条目不是动图」），再拼 `--playlist-items` 只下选中项。
  **已改为末位关键字专用参数**：任务队列为此新增通用 kwargs 通道（见决策 31），
  动图页经 `kwargs={'selected_indexes': [...]}` 传入，从根上杜绝按位置传参的静默错位。
- **页面**（`pages/gif_page.py`）：URL 行（输入 + 解析 + 全选/全不选）→ 表单（输出目录/质量/自定义卡片）
  → 选择区 → 下载按钮 → 日志区。
  - `GifItemCard`（`qfw.CardWidget`，200px 固定宽）：封面 `RoundedCoverWidget`
    （设 `WA_TransparentForMouseEvents` 让点击穿透到卡片）+ `CheckBox` + 选中态主题色圆角描边；
    点卡片空白处切换勾选（勾选框自身吞掉点击事件，不会重复切换）。卡片 `setFixedSize` 以配合
    `AdaptiveFlowLayout` 只用 `sizeHint().height()` 定高的约束（同 ElidedLabel 那条）。
  - 封面由 `_CoverLoader(QThread)` **串行**下载（单条 15s 超时），`loaded/failed` 回主线程填图；
    已下载字节存 `_cover_bytes`，入队时复用进 `metadata['cover_data']`（取头一个勾选条目），
    队列卡片不再发第二次网络请求。
  - `_parse_seq` 解析代际丢弃过期回调；`_parsed_url` 与输入框现链接比对，**改链接后必须重新解析**
    （序号归属上一次解析结果）；`_retired` set 持有被停线程引用直到 `finished`（防运行中被 GC，同决策 2）。
  - 空的 `_selected_indexes()` 时下载按钮禁用；解析出 0 个动图时只提示不入队。
- **已知取舍与遗留（2026-10-11 复核）**：
  1. 解析与下载各跑一次 `yt-dlp -J`（同链接两次元数据请求），换来「序号永不过期」；
  2. 动图条目 `duration` 恒为 None/0——这正是 `_looks_like_animated` 的判定前提，故卡片「时长」标签
     不可达，**已移除**（原实现留的 `if duration:` 分支实测永不进入）；
  3. `_cover_bytes` 仍保留全部条目的封面字节（实际只用首个勾选条目）；
  4. 退出路径未清理本页线程（`quit_app()` 只停 monitor worker + 取消队列任务）。复核时的离屏实验：
     构造页面后不经事件循环直接 `sys.exit`，既有 `YoutubePage` 3/3 崩、`GifPage` 0/5 崩；
     `GifPage` 解析过（有线程在跑）时 3~4/8 崩；带 150ms 事件循环再 `quit` 的近似真实路径 1/5 崩，
     显式 `request_stop()` + `wait()` 后仍 1/5 崩。**结论：不是本次改动引入**（既有 YouTube 页更严重），
     根因未定位，另案排查，本次不改。
  5. 解析报 `Unable to download JSON metadata: EOF occurred in violation of protocol (_ssl.c:1007)`
     = **网络层**，不是提取器/代码问题：yt-dlp 到 X 的 TLS 连接被中途重置。2026-10-11 用户实测，
     同一 yt-dlp（PATH 上 `scoop` 的 2026.08.19）前一条 X 链接完整走完
     `guest token → GraphQL JSON → m3u8`，后一条直接 TLS EOF，重试即恢复。应用无代理支持
     （全仓库无 `proxy`），需要时靠 `ALL_PROXY`/`HTTPS_PROXY` 环境变量或全局/TUN 代理覆盖子进程。

**验证（2026-10-11 离线冒烟，`QT_QPA_PLATFORM=offscreen`，全程 monkeypatch 不触网）**：
52 项断言分五节全通过 ——
A 页面状态机（解析回调建卡 3 张 / 默认全选 / 按钮可用性随勾选数 / 全选↔全不选 / 计数文案）；
B 封面字节注入后上屏 + 勾选子集入队的 `args` 与 `kwargs`（args 固定 6 个位置参数、不再含选中序号；
`kwargs['selected_indexes'] == [2]`；`metadata['cover_data']` 复用解析期字节；metadata 保留
`filename/out_dir/duration/cover_url`）+ 改链接后拒绝入队；
C 重新解析清理旧卡片（`_cards` 清空 / 布局 `count()==0` / `_cover_bytes` 清空 / 网格隐藏 / 新卡片对象）；
D 内核 `selected_indexes` 过滤（`[3]` → `--playlist-items 3` 且单文件返回 str；`[3,2]` → `'2,3'`
（恒按条目序，与勾选顺序无关）；`None` → 全部动图；`[1]` 普通视频 → 抛「选中的条目不是动图」；
无动图链接 → 抛「该链接没有动图」；**按位置传第 10 个参数、或用旧名 `items=` 均直接 TypeError**）；
E 队列 kwargs 通道（GIF 任务经 `add_task(..., kwargs={'selected_indexes': [3]})` 端到端送达任务函数，
且 `log/on_process_created/on_progress` 仍被注入；占用保留名 `log` → 任务失败且报错含「保留名」；
FFMPEG 任务 `Task.kwargs == {}`、行为不变；YTDLP 分支同步 `TaskWorker.run()` 空 kwargs 不报错）。
UTF-8 环境 3 次、GBK（cp936，未设 `PYTHONUTF8`）3 次，退出码均为 0、断言零失败。
**真实链接端到端尚未复测**（2026-10-07 的 14 项断言针对已删除的 `fetch_media_meta` 流程）。

**2026-10-19 追加（决策 44）**：动图页现在也列出同一条推文里的普通视频（显示「视频 N」、默认
不勾选、勾选后点「下载」先弹确认框）。因此本文中这些表述已失效，读实现请以
[决策 44](44-gif-page-video-items.md) 为准：

| 本文旧表述 | 现状 |
|---|---|
| `fetch_gif_items` 「只列动图」 | 列出动图 + 视频，返回项多 `kind` / `video_no`，**排序在数据层完成（动图在前）** |
| `items` 结构 `{index, url, title, duration, cover_url}` | 多 `kind`、`video_no` |
| `download_and_convert_gif` 与 `_looks_like_animated` 求交集（防把视频也转成 GIF） | 改与 `_entry_kind` 求交集（放行视频、只挡 `None` 档）；空交集文案改「选中的条目无法转换」 |
| 解析出 0 个动图时只提示不入队 | 改为「0 个动图或视频」才提示 |
| 卡片默认全选 | 动图默认勾选、**视频默认不勾选**；「全选」只作用动图 |

`_looks_like_animated` 本身原样保留，仍是「是不是动图」的唯一判定。
