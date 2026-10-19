# 决策 47. 嗅探页双视图：默认视图（选分辨率即下载）+ 表格视图（逐格式勾选）（2026-10-20）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 47」一一对应）

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
| **默认视图**（新，默认落点） | 给「我只想要这个视频」的用户 | 单列平铺档位，点一行 → 点「▶ 下载」 |
| **表格视图**（原表格） | 给「我要指定编码/容器」的用户 | 原样：5 列全格式表 + 互斥勾选 + 「▶ 下载并合并选中格式」 |

视图偏好存 `general.sniff_view`（`load_sniff_view()` / `save_sniff_view()`，决策 47
在 `core/config.py` 里新增的一对函数，与 `general` 段既有 `close_action` /
`task_complete_action` 同款写法）。非法值读时回退 `default`、写时直接忽略。

### 1. 默认视图的形态：单列全宽行，不是下拉

**否决下拉框**（初版方案）：下拉收起后用户看不到有哪些画质，等于把「选择成本」
换成了「发现成本」；且下拉的留白在小窗口里很难看。改为**单列平铺**，每行 68px：

```
┌──────────────────────────────────────────────┐
│ 最佳画质                                  ⬤  │  ← 副标题：2160p 60fps · vp9 + opus · webm · 约 1.0GB
├──────────────────────────────────────────────┤
│ 1080p 60fps                               ○  │
├──────────────────────────────────────────────┤
│ 720p                                      ○  │
└──────────────────────────────────────────────┘
    音频
┌──────────────────────────────────────────────┐
│ 仅下载音频 · 最佳音质                      ○  │
└──────────────────────────────────────────────┘
```

**与表格视图的视觉区分**（这是刻意的）：无表头、无网格线、无多选框。语义是
「**选一个**」而非「勾多个」，所以反馈控件是**单选圈**。

`src/sookit/widgets/option_row.py` 的 `OptionRow(QWidget)` 自绘整行：
- 类常量 `ROW_HEIGHT=68`、`RADIUS=8`、`PADDING=14`、`CIRCLE_D=18`；
- 选中：主题色 alpha 28 填充 + 1.4px 主题色描边 + 右侧实心圆 + 白对勾；
- 未选：白 alpha 190（暗色 18）填充 + 1.2px 空心圆；
- `setAttribute(WA_Hover)` + `enterEvent/leaveEvent` 切 hover 底色；
- `qconfig.themeChangedFinished` → `update()`，跟随主题；
- 整行可点（`mouseReleaseEvent` 左键且命中 `rect()` 才 `clicked.emit()`）。

**坑**：`QFontMetrics.elidedText` 的 width 参数必须是 `int`，传 float 直接
`TypeError: argument 3 has unexpected type 'float'`（本控件里用
`text_w = max(int(text_right - text_left), 20)`）。

### 2. 档位归并：`core/sniff_options.py`（纯函数，无 Qt）

`Functions.sniff_youtube` 新增三个结构化字段（**不再解析 `quality` 文本**）：
`height`、`fps`、`abr`（仅音频流，缺 `abr` 时用 `tbr`）。

`build_view_options(formats)` 返回 `{'video': [...], 'audio': [...]}`，每个
option 是 `{'id', 'kind', 'title', 'subtitle', 'spec'}`：

- **视频档**：按 `height` 去重（同一高度只出一行，该高度内 `max` by `(fps, filesize)`），
  取前 `MAX_VIDEO_ROWS = 5` 个高度，从高到低。
  - 「最佳画质」**不是**独立的 `bestvideo+bestaudio/best` 档，而是把**最高分辨率那一档
    改写标题**（`id='best'`、`title='最佳画质'`、副标题前置原分辨率）。
  - 原因：`bestvideo+bestaudio` 由 yt-dlp 自由配对，可能配出**跨编码族**组合
    （实测形态：av01-mp4 视频 + opus 音频）→ 只能兜底合 mkv。而决策 38 的 mkv 风险
    弹窗只服务于表格视图的人工勾选，默认视图不该出现这种后果。改写成「最高档」
    后，`spec` 是嗅探到的**精确 format_id 拼接**，落点确定。
- **音频档**：仅当存在**独立音频流**（`type == 音频流`）时才给。
  - 只有成品格式的站点（如 archive.org 的 `2|avi`）上，`bestaudio` 会退化成整个
    视频文件——「仅下载音频」就成了假承诺，所以此时 audio 列表为空。
  - 第一档「仅下载音频 · 最佳音质」；第二档仅在存在
    `abr > 0 且 abr < best_abr - 2` 的更小音轨时给，取这批里码率最高的，
    标题 `仅下载音频 · {int(round(abr))}kbps`。
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
| `core/sniff_options.py` | **新建**：档位归并 + 容器族判定（纯函数） |
| `widgets/option_row.py` | **新建**：`OptionRow` 自绘单选行控件 |
| `pages/youtube_page.py` | 右面板加 SegmentedWidget + 两视图容器；新增 `_on_view_changed`/`_apply_view`/`_clear_default_view`/`_section_label`/`_populate_default_view`/`_select_option`/`do_download_default`；四个复位点；初始化恢复偏好 |

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
   视频 5 档（最佳画质/1080p60/720p/480p/360p）+ 音频 2 档，
   `最佳画质` 副标题 `2160p 60fps · vp9 + opus · webm · 约 1.0GB`、`spec='313+251'`。
3. 边界形态：`muxed-only`（archive.org 式）→ video 仅 1 档、audio 为空；
   `audio-only` → video 为空、audio 仅 1 档；`sparse`（无 filesize/fps）不崩。
4. 容器推断矩阵实测：
   `vp9+opus→webm`、`av01+opus→webm`、`av01+mp4a→mp4`、`avc1+mp4a→mp4`、
   `avc1+opus→mkv`、`vp9+mp4a→mkv`、编码未知→`''`。
5. 真实 Windows 平台渲染截图（**不能用 `QT_QPA_PLATFORM=offscreen`**：找不到字体
   目录会让中文全部变方块）确认：默认视图档位/选中态/空态、表格视图无回归。
6. `_start_download` 复用性确认：接受 `313+251` 这样的精确选择器，无需改动。

## 相关决策

- 决策 24：表格视图的同类型互斥勾选 + 预勾选，**原样保留**，未受影响。
- 决策 38：合并落点预测 + mkv 风险弹窗。本决策**改进了其判定口径**（见 §5），
  弹窗仍只在表格视图出现。
- 决策 40：嗅探结果代际校验（`_sniff_seq`），默认视图填充在其之后，天然受保护。
- 决策 41：`core/functions.py` 不是兼容层——所以 `sniff_options.py` 直接从
  `core.functions` import `FormatType`，新代码不经门面再导出。
