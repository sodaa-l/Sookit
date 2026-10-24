# 决策 47. 嗅探页双视图：默认视图（选分辨率即下载）+ 表格视图（逐格式勾选）（2026-10-20）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 47」一一对应）
>
> 2026-10-24 入口修订：顶部切换改为「音视频 / 字幕」，音视频的默认 / 表格视图移到右上单按钮，`general.sniff_view` 偏好及档位逻辑保留。下文记录原设计，当前入口见[决策 61](61-sniff-subtitle-download.md)。

## 背景

嗅探页原来的右面板只有一张格式表：5 列（选择/类型/质量/编码/语言）平铺 yt-dlp
返回的**全部**格式。一个 YouTube 视频常返回 20~30 条（多分辨率 × 多编码 ×
仅视频/仅音频/成品），普通用户面对这张表面临三个问题：

1. **不知道该勾哪个**：要理解「视频流 + 音频流必须各挑一个才能合并」这件事，
   而表格看起来像「勾一个就行」。
2. **选项噪声大**：同一个 1080p 可能同时有 av01/vp9/avc1 三行、60fps 与 30fps 各一行。
3. **决策 38 的 mkv 弹窗**：勾错编码族时弹「非标准编码组合」，本质是让用户
   替程序承担选择后果。

但表格视图本身是有价值的——它就是「自选格式」的专业入口，不该删。

## 结论

右面板顶部加 `qfw.SegmentedWidget` 两个视图（沿用 `queue_page` 的视图切换先例）：

| 视图 | 定位 | 交互 |
|---|---|---|
| **默认视图**（新，默认落点） | 给「我只想要这个视频」的用户 | 卡片平铺档位（最多两列，窄了一列），点一张 → 点「▶ 下载」 |
| **表格视图**（原表格） | 给「我要指定编码/容器」的用户 | 原样：5 列全格式表 + 互斥勾选 + 「▶ 下载并合并选中格式」 |

视图偏好存 `general.sniff_view`（`load_sniff_view()` / `save_sniff_view()`，决策 47
在 `core/config.py` 里新增的一对函数，与 `general` 段既有 `close_action` /
`task_complete_action` 同款写法）。非法值读时回退 `default`、写时直接忽略。

### 1. 默认视图的形态：卡片平铺（固定两列），不是下拉

**否决下拉框**（初版方案）：下拉收起后用户看不到有哪些画质，等于把「选择成本」
换成了「发现成本」；且下拉的留白在小窗口里很难看。改为**平铺卡片**，每张 68px 高：

**否决「单列全宽行」**（第二版方案）：单列铺满整栏时，右面板右侧会留出大片空白
（1180px 窗口下每行右侧空掉三分之一），视觉上像没做完。改为**一排放两张**。

窗口变窄时**不退回单列**：窗口本身有最小宽度（`main_window.setMinimumSize(966, 644)`），
右面板可用宽始终在 450px 上下，根本窄不到「一列才放得下」的程度——单列回退既不
会生效、又白增分支。窄下去由卡片自己丢信息（副标题先丢编码段，见后文），
保住容器和大小。这与「否决单列全宽行」并不矛盾：宽窗口不会变成一行一张，
窄窗口也不会。

```
 视频画质
┌───────────────────┐ ┌───────────────────┐
│ 2160p 60fps     ⬤ │ │ 1080p 60fps     ○ │  ← 副标题：vp9+opus · webm · 约 1.0GB
└───────────────────┘ └───────────────────┘
┌───────────────────┐ ┌───────────────────┐
│ 720p            ○ │ │ 480p            ○ │
└───────────────────┘ └───────────────────┘
 音频
┌───────────────────┐ ┌───────────────────┐
│ 129kbps         ○ │ │ 65kbps          ○ │  ← 副标题：mp4a · m4a · 约 89.3MB
└───────────────────┘ └───────────────────┘
```

**档位名一律只描述「这一行是什么」，不用「最佳画质/最佳音质」这种评价性文案**：
评价性文案让各档无法横向比较（「最佳」到底是多少？），也让用户没法在
「画质稍差但小一半」之间做取舍。哪个更好交给**排序**（高分辨率/高码率在前）
和**默认预选首行**表达。

**固定两列，不做单列回退**（`OptionFlowLayout.MAX_COLUMNS = 2`）：

```
minWidth = (available - (MAX_COLUMNS-1)*space) // MAX_COLUMNS
```

窗口本身有最小宽度（主窗口 `setMinimumSize(966, 644)`），右面板可用宽始终在
450px 上下，根本窄不到「一列才放得下」的程度——设个单列回退既从不生效、又白增
分支。所以列数直接钉死 2 列：卡片宽度跟着可用宽度一起变，窄下去由卡片自己
丢信息（见下节副标题策略），而不是掉回一列。

**不直接用 `qfw.AdaptiveFlowLayout`**：它的列数只由固定的 `widgetMinimumWidth`
决定，表达不了「固定两列」——宽窗口下会排成三列、四列。`OptionFlowLayout`
按可用宽度反算出父类算法需要的 minWidth 喂给 `setWidgetMinimumWidth()`：

```
cardsPerRow = (available + space) // (minWidth + space)
→ minWidth = (available - (MAX_COLUMNS-1)*space) // MAX_COLUMNS
```

**卡片宽度由布局强制分配**：`AdaptiveFlowLayout._doLayout` 里
`targetSize = QSize(cardWidth, item.sizeHint().height())`，`cardWidth` 由
`available / columns` 算出，**忽略 widget 自身 `sizeHint().width()`**。
所以 `OptionRow.MAX_WIDTH` 只能通过 `flow.setWidgetMaximumWidth()` 生效，
`row.setMaximumWidth()` 仅在极宽窗口下兜底。

**卡片宽度不设上限**（~~宽上限 400px~~ 已由决策 51 取消）：卡片宽度恒为
「可用宽度 / 2」，两列永远等宽、左右留白恒等。曾经有过 400px 上限，但宽窗口下
多出来的宽度会全部堆到右侧，左右留白就不相等了。
注意**不要**给 `insertWidget` 传 `alignment=AlignLeft`：`OptionRow` 是自绘控件，
默认 `sizeHint` 无效 `(-1,-1)`，带对齐会被收缩成 0 宽而完全看不见——所以
`OptionRow` 必须实现按内容估算的 `sizeHint()`（`MIN_WIDTH=320`，无上限）。

**副标题：窄了先丢编码段，不是先压紧、更不是省略号**（`OptionRow._fit_subtitle`）：

窗口窄下去时按这个顺序让文字适应，**越靠前越优先牺牲**：

| 顺序 | 做法 | 例子 | 最宽用例实测 |
| --- | --- | --- | --- |
| 0（宽） | 带编码，分隔符留空格 | `vp9+opus · webm · 约 1.0GB` | 186 |
| 1 | **丢掉编码段**，只留容器 + 大小，分隔符照旧留空格 | `webm · 约 1.0GB` | 118 |
| 2 | 压紧分隔符 | `webm·约 1.0GB` | 87 |
| 3 | 降 8pt | `webm·约 1.0GB`（8pt） | 80 |
| 4（最紧） | 省「约」+ 8pt | `webm·1.0GB`（8pt） | — |

第 1 档的门槛 `OptionRow.SUBTITLE_FULL_MIN_WIDTH = 190`：带编码写法最宽 186px，
门槛取 190 是留 4px 余量——**低于门槛直接跳到「去编码」起点**（而不是先压紧
分隔符）。这是用户实测后的取舍：窗口窄时宁可少显示一项信息（编码），也不要
把整行文字挤成一团；容器和大小是选档位时真正要看的。留余量的原因：卡在 186
时同一屏里最长的行只要差 1px 就会单独掉到「去编码」档，看起来像各行没对齐。

各档位的短副标题由生成器预先生成（`sniff_options` 里每个 option 带
`subtitle` 和 `short_subtitle` 两份），UI 只按宽度择优，不做字符串裁剪。

起点档只由**文字区宽度**（`text_w`）落在哪一档决定，**不逐行按内容长度各自
决定**——同一网格里卡片等宽，若有的行 `a · b` 有的行 `a·b`，看起来像没对齐。
起点写法若本行仍放不下，才继续往更紧的写法退一步。

实测（**真实 `FluentWindow`** + `YouTubePage`，`addSubInterface` 后取卡片宽——
注意窗口宽不等于页面宽，左侧导航栏占约 49px，直接影响卡片宽度）：

| 窗口宽 | 页面宽 | 卡片 | `text_w` | 档位 | 副标题 |
| --- | --- | --- | --- | --- | --- |
| 1131（默认） | 1082 | 271 | 214 | 0 | `vp9+opus · webm · 约 1008.0MB` 9pt，need=186 |
| 1005 | 956 | 235 | 178 | 1 | `webm · 约 1008.0MB` 9pt，need=118 |
| 976 | 927 | 221 | 164 | 1 | 同上 |
| 966（最小） | 917 | 216 | 159 | 1 | 同上 |
| 900 | 851 | 183 | 126 | 1 | 同上 |

即只有默认窗口尺寸（1131px）能显示编码段；用户习惯把窗口缩到 1000px 上下时
全部落「去编码」档，写法一致。门槛取 190 是留余量（带编码最宽 186px），
避免 `text_w` 恰好卡在 186 上下时同一屏里最长的行单独掉档。

预算公式（`OptionRow` 与卡片宽度的关系）：
`text_w = card - 2 - 2*PADDING - CIRCLE_D - 12`（`PADDING=12`、`CIRCLE_D=18`），
即 `card 271 → 214`、`card 235 → 178`、`card 221 → 164`、`card 400 → 344`。
**注意这里按整行宽度算，不再单独占右侧一列放字节数**——两列时那一列会被省成省略号。

**与表格视图的视觉区分**（这是刻意的）：无表头、无网格线、无多选框。语义是
「**选一个**」而非「勾多个」，所以反馈控件是**单选圈**。

`src/sookit/widgets/option_row.py` 的 `OptionRow(QWidget)` 自绘整行：
- 类常量 `ROW_HEIGHT=68`、`RADIUS=8`、`PADDING=12`、`CIRCLE_D=18`、
  `MIN_WIDTH=320`、`MAX_WIDTH=400`、`SUBTITLE_PT_SIZES=(9, 8)`、
  `SUBTITLE_FULL_MIN_WIDTH=190`；
- 构造参数 `(title, subtitle, short_subtitle, right_text)`：`short_subtitle` 是
  去掉编码段的短版，由 `sniff_options` 预先生成；
- 选中：主题色 alpha 28 填充 + 1.4px 主题色描边 + 右侧实心圆 + 白对勾；
- 未选：白 alpha 190（暗色 18）填充 + 1.2px 空心圆；
- `setAttribute(WA_Hover)` + `enterEvent/leaveEvent` 切 hover 底色；
- `qconfig.themeChangedFinished` → `update()`，跟随主题；
- 整行可点（`mouseReleaseEvent` 左键且命中 `rect()` 才 `clicked.emit()`）。

`src/sookit/widgets/option_grid.py` 的 `OptionFlowLayout(AdaptiveFlowLayout)`：
`MAX_COLUMNS=2`、重写 `_doLayout(rect, move)` 把列数钉死在 2 列。

**坑**：`QFontMetrics.elidedText` 的 width 参数必须是 `int`，传 float 直接
`TypeError: argument 3 has unexpected type 'float'`（本控件里用
`text_w = max(int(text_right - text_left), 20)`）。

**坑**：`OptionFlowLayout` 的宿主 `holder` 必须 `sizePolicy().setHeightForWidth(True)`，
否则流式布局算出的多行高度不会传导给 `QVBoxLayout`，卡片会重叠。

### 2. 档位归并：`core/sniff_options.py`（纯函数，无 Qt）

`Functions.sniff_youtube` 新增三个结构化字段（**不再解析 `quality` 文本**）：
`height`、`fps`、`abr`（仅音频流，缺 `abr` 时用 `tbr`）。

`build_view_options(formats)` 返回 `{'video': [...], 'audio': [...]}`，每个
option 是 `{'id', 'kind', 'title', 'subtitle', 'spec'}`：

- **视频档**：按 `height` 去重（同一高度只出一行，该高度内 `max` by `(fps, filesize)`），
  取前 `MAX_VIDEO_ROWS = 5` 个高度，从高到低。**最高档也不叫「最佳画质」**，
  一律标分辨率和帧率（`720p` / `1080p 60fps`）。
  - 不设独立的 `bestvideo+bestaudio/best` 档：该选择器由 yt-dlp 自由配对，可能配出
    **跨编码族**组合（实测形态：av01-mp4 视频 + opus 音频）→ 只能兜底合 mkv。而决策 38
    的 mkv 风险弹窗只服务于表格视图的人工勾选，默认视图不该出现这种后果。每档的
    `spec` 都是嗅探到的**精确 format_id 拼接**，落点确定。
- **音频档**：仅当存在**独立音频流**（`type == 音频流`）时才给。
  - 只有成品格式的站点（如 archive.org 的 `2|avi`）上，`bestaudio` 会退化成整个
    视频文件——「仅下载音频」就成了假承诺，所以此时 audio 列表为空。
  - 音频档由 `_audio_tracks(formats)` 归并：按 `int(round(abr))` 分组，**同码率只出一档**
    （yt-dlp 常给同一码率的 drc 与非 drc 两个变体，逐条平铺会塞满重复项），同组优先
    非 drc 变体、再比 filesize；`abr <= 0` 的用 `format_id` 单独成档、不丢弃；
    最后按码率降序排。标题 `f'{abr}kbps'`（无码率时退回 `acodec`）。
  - **不做码率阈值筛选**，全部独立音轨都要能选到。第一版曾筛
    `abr < best_abr - 2` 取次高，实测把 129.5kbps 的 mp4a 整批吃掉
    （它高于 128.6-2），用户能选的最低只剩 65kbps。
- 视频与音频在 UI 侧**全局互斥单选**（`_select_option` 遍历所有行，只有一个 `True`）：
  一次下载只产出一样东西。

### 3. `spec` 用精确 format_id，不用通配选择器

档位编译成 `f"{video_id}+{audio_id}"`（如 `313+251`），直接交给既有的
`_start_download(format_spec)` —— 该函数本就接受任意 yt-dlp 选择器，因此
**下载链路零改动**，也天然继承决策 8（独立 workspace）、36（`--no-check-formats`）、
46（`-N`）等全部既有行为。

通配选择器（`bestvideo[height<=1080]+bestaudio`）只在早期原型里用过，弃用原因：
行内副标题显示的编码/容器/大小必须与实际下载到的流**一致**，否则就是骗人；
而 `bestvideo[...]` 选中哪条流取决于 yt-dlp 的运行时排序，无法预先写进副标题。

只有「该高度只有成品格式」时 `spec` 是单个 format_id（如 `2`）。

### 4. 音轨挑选：先看容器族，再看编码族

`_pick_compatible_audio(formats, video_fmt)` 的优先级：

1. **与视频 ext 同容器族**（webm↔webm、mp4/m4v↔m4a/mp4）→ 合并即可，不必跨容器重封装；
2. 与视频编码有**共同标准容器**（`is_standard_pair`）；
3. 都不满足 → 取最佳音轨（宁可落 mkv 也要有声）。

同优先级内按 `(abr, filesize)` 取最大。实测效果：1080p 那档是 av01(mp4)，音轨
从「最高码率 opus」改为「mp4a」——否则会为了 32kbps 的码率差白做一次 webm 重封装。

### 5. 容器族判定：每个编码能落哪些容器，取交集

**这是本次改动对决策 38 的实质修订点。** 原来 `_predict_mkv_merge` 用「同族判定」：

```python
webm_ok = v in WEBM_VIDEO_CODECS and a in WEBM_AUDIO_CODECS
mp4_ok  = v in MP4_VIDEO_CODECS  and a in MP4_AUDIO_CODECS
```

这对 `av01 + opus` 判为「非标准（落 mkv）」，但实际上 **av01 两种容器都能装**，
av01 + opus 合 webm 是完全标准的组合。改用「每编码 → 可用容器集合，取交集」：

```
vp8/vp9/vp09   → {webm}          opus/vorbis → {webm}
av01/av1       → {webm, mp4}     mp4a/aac/m4a → {mp4}
avc1/h264/...  → {mp4}
```

- 交集非空 → 标准组合，落交集里那个容器（两种都行时看视频流自身 ext）；
- 交集为空 → mkv 兜底（如 avc1 + opus）；
- **未知编码 → 不判定**（`is_standard_pair` 返回 True 放行，`_merged_ext` 返回 `''`
  让副标题省略容器）：宁可不说，也不能说错。

编码族表从 `YouTubePage` 类属性**上提到 `core/sniff_options.py`** 作为模块常量
（`WEBM_VIDEO_CODECS` 等），`YouTubePage` 保留类属性别名指向同一份对象，
`_predict_mkv_merge` 改为直接调 `is_standard_pair` —— **两处口径必须同源**，
否则会出现「表格视图弹了 mkv 警告，默认视图却写着 webm」。

### 6. 空态与状态复位

默认视图是进入页面的**落点**，嗅探前若只有一片空白 + 置灰按钮会像「坏了」。
加 `option_empty_hint`（居中灰字「粘贴视频链接并点击「嗅探」，这里会列出可选画质」），
与 `option_scroll` 二选一显隐（`_clear_default_view()` 收起列表露出提示，
`_populate_default_view` 反之）。

**四个复位点**（与表格视图同步，避免残留上一部视频的档位）：
`do_sniff`（新嗅探开始）、`_on_sniff_done` 无格式分支、`_on_sniff_error`、
`_populate_default_view` 入口。前三处同时 `default_download_btn.setEnabled(False)`。

### 7. 视图偏好恢复时不落盘

`SegmentedWidget.setCurrentItem()` **会发出 `currentItemChanged`**，初始化时若直接
调它，会把「刚读出来的值」再写一遍配置（无害但脏）。用 `_restoring_view` 守卫：

```python
self._restoring_view = True
try:
    saved_view = load_sniff_view()
    self.view_segment.setCurrentItem(saved_view)
    self._apply_view(saved_view)
finally:
    self._restoring_view = False
```

`_on_view_changed` 里 `if not self._restoring_view: save_sniff_view(key)`。

`_apply_view(key)` 用 `setVisible` 切两个 `QWidget`（**不是** `QStackedWidget`，沿用
`queue_page._on_segment_changed` 的做法）。

## 改动文件

| 文件 | 改动 |
|---|---|
| `core/config.py` | 新增 `SNIFF_VIEW_DEFAULT`/`SNIFF_VIEW_TABLE` + `load_sniff_view()`/`save_sniff_view()` |
| `core/functions.py` | `sniff_youtube` 的 format dict 新增 `height`/`fps`/`abr` |
| `core/sniff_options.py` | **新建**：档位归并 + 容器族判定（纯函数）；每个 option 带 `subtitle` 与 `short_subtitle`（去编码版） |
| `widgets/option_row.py` | **新建**：`OptionRow` 自绘单选行控件（`_fit_subtitle` 按宽度丢编码/压紧/降字号） |
| `widgets/option_grid.py` | **新建**：`OptionFlowLayout` 固定两列网格（`MAX_COLUMNS=2`，不退回单列） |
| `pages/youtube_page.py` | 右面板加 SegmentedWidget + 两视图容器；新增 `_on_view_changed`/`_apply_view`/`_clear_default_view`/`_section_label`/`_populate_default_view`/`_select_option`/`do_download_default`；四个复位点；初始化恢复偏好；`_make_option_grid()`（卡片不设宽度上限，决策 51）；`_option_flows` 状态与复位；`option_scroll.enableTransparentBackground()`；空态文案 `setWordWrap(True)`；两个下载按钮统一 240×32 居中 |

## 风险

1. **`sniff_youtube` 的 dict 多三个键**：所有消费方按 key 取用，多键不影响；但
   若有代码把整个 dict 序列化后比对长度，需留意（已 grep 确认无此用法）。
2. **默认视图的档位是「估算」**：`filesize` 缺失时副标题省略大小；`height` 未知的
   流（HLS 成片、storyboard）不进视频档——不会崩，但可能某一档缺失。
3. **决策 38 的判定口径变化**：`av01 + opus` / `av01 + mp4a` 不再提示 mkv 风险
   （实际也不会落 mkv）。这是修正而非放宽；`avc1 + opus`、`vp9 + mp4a` 仍会提示。
4. **预览脚本依赖**：`.tmp_render_sniff.py` 等临时文件已在提交前删除，不入库。

## 验证

1. `compileall` 全部改动文件通过。
2. `build_view_options` 离屏断言（7 条伪造格式，覆盖 YouTube 全形态）：
   视频 5 档（2160p60/1080p60/720p/480p/360p）+ 音频 2 档，
   最高档副标题 `2160p 60fps · vp9+opus · webm · 约 1.0GB`、`spec='313+251'`。
   音频归并另用 10 条含 drc 变体的真实形态数据验证：10 条 → 4 档
   （129kbps/128kbps/65kbps/48kbps），无重复项、无遗漏。
3. 边界形态：`muxed-only`（archive.org 式）→ video 仅 1 档、audio 为空；
   `audio-only` → video 为空、audio 仅 1 档；`sparse`（无 filesize/fps）不崩。
4. 容器推断矩阵实测：
   `vp9+opus→webm`、`av01+opus→webm`、`av01+mp4a→mp4`、`avc1+mp4a→mp4`、
   `avc1+opus→mkv`、`vp9+mp4a→mkv`、编码未知→`''`。
5. 真实 Windows 平台渲染截图（**不能用 `QT_QPA_PLATFORM=offscreen`**：找不到字体
   目录会让中文全部变方块）确认：默认视图档位/选中态/空态、表格视图无回归。
6. `_start_download` 复用性确认：接受 `313+251` 这样的精确选择器，无需改动。
7. **右栏不挤压左栏**：实测 `page.minimumSizeHint().width()` 加入默认视图后未上升
   （空态文案 `setWordWrap(True)` 使其 `minimumSizeHint` 从 322 降到 42）；窗口从
   1005 逐级缩到 740，`QSplitter` 左栏恒为 412px，封面区不受影响。这是首版漏掉
   换行后真实出现过的回归：右面板最小宽度被撑大 → 窄窗口下 Qt 只能压左栏。
8. **两个视图的下载按钮同尺寸**：默认视图与表格视图均为 240×32、水平居中
   （首版默认视图按钮是 `setFixedHeight(40)` + 撑满整栏的 487×40，明显偏大）。
9. **无灰底**：`option_scroll` 调用 `enableTransparentBackground()` 后
   `styleSheet` 为 `QScrollArea{border: none; background: transparent}`，
   与队列页/GIF 页/设置页一致（`qfw.ScrollArea` 默认是不透明面板色）。
10. **固定两列，窄窗口也不退回单列**：976px 窗口下卡片 242×68（右栏可用宽
    494px）；窗口从 1005 逐级缩到 700，`OptionFlowLayout` 始终排 2 列
    （实测 `cols=[2,2]` 全程不变），卡片跟着变窄 242→127。主窗口
    `setMinimumSize(966, 644)` 限制了最小宽度，实际窄不到「一列才放得下」。
    卡片宽度由布局 `available/columns` 强制分配，`row.setMaximumWidth(400)`
    只在极宽窗口兜底；`minimumSizeHint` 仍为 709px（与 master 一致，未上升）。
11. **副标题按宽度逐级降级、从不出现省略号**（真实 `FluentWindow` 实测）：
    默认窗口 1131px → 卡片 271px、文字区 214px → 第 0 档带编码 9pt
    （`vp9+opus · webm · 约 1008.0MB`，need=186）；1005px → 178px、
    976px → 164px、最小窗口 966px → 159px、900px → 126px，全部落到第 1 档
    **丢掉编码段**（`webm · 约 1008.0MB`，need=118）；再窄到文字区 81px 才落到
    8pt 压紧档（`webm·1008.0MB`）。全程「约」后有空格、无 `…`。这是用户实测
    后的要求：宁可少显示编码，也不要把整行挤成一团。
    门槛取 `SUBTITLE_FULL_MIN_WIDTH=190`（带编码最宽 186px + 4px 余量）：
    卡在 186 时同一屏里最长的行差 1px 就会单独掉档，看起来像各行没对齐。
12. **两种卡片副标题同屏写法一致**：起点档只由 `text_w` 决定，不逐行按内容长度
    决定，所以同一网格里各行的分隔符写法统一（改前会出现有的 `a · b` 有的
    `a·b`，看起来像没对齐）。

## 相关决策

- 决策 24：表格视图的同类型互斥勾选 + 预勾选，**原样保留**，未受影响。
- 决策 38：合并落点预测 + mkv 风险弹窗。本决策**改进了其判定口径**（见 §5），
  弹窗仍只在表格视图出现。
- 决策 40：嗅探结果代际校验（`_sniff_seq`），默认视图填充在其之后，天然受保护。
- 决策 41：`core/functions.py` 不是兼容层——所以 `sniff_options.py` 直接从
  `core.functions` import `FormatType`，新代码不经门面再导出。
