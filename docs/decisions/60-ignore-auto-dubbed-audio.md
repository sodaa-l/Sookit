# 决策 60. 默认视图忽略 `dubbed-auto` 自动配音音轨（2026-10-21）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 NN」一一对应）

## 背景

用户给了一个 YouTube Shorts（`https://youtube.com/shorts/ynbOueUQJRA`）：默认视图里
出现了两条音频档，标题是 `American English - dubbed-auto・en-us`，视频档还被拆成
`zh-hant` / `en-us` 两套。要求「默认视图忽略所有带 **dubbed-auto** 标签的音频流」。

用户随后补充了关键事实：那个 Shorts 的 en-US 音轨**全都是自动配音**（只是 yt-dlp 只在
m3u8 的那两条上打了标记），而另一个视频（`Yb9H5vhWyHg`）里的日语音轨是**真人配音**，
不该被一起丢掉 —— 所以判定不能只看「是不是原声」，要认「是不是机翻配音」。

## 根因

YouTube 会给部分视频挂**配音音轨**，yt-dlp 把「这条是机翻配音还是人工配音」写在
`format_note` 里，且**只写在这种 m3u8 抽出来的 `233-*` / `234-*` 音轨上**：

| 视频 | 音轨 | language | format_note | 实际内容 |
|---|---|---|---|---|
| Shorts | `233-0` | en-US | `American English - dubbed-auto` | 机翻自动配音 |
| Shorts | `233-1` | zh-Hant | `zh-Hant - original (original)` | 原声 |
| Shorts | `140-0` | en-US | `English (US), medium` | **同一份自动配音**的 DASH 变体（无标记） |
| ja 视频 | `233-0` | ja | `日本語 - dubbed` | 人工配音 |
| ja 视频 | `140-0` | ja | `Japanese, medium` | **同一份人工配音**的 DASH 变体（无标记） |

而同一条配音在 DASH 音轨（`139-*` / `140-*` / `249-*` / `250-*` / `251-*`）上只写语言名，
**不带任何配音标记** —— 单看某一条音轨无法判断，只能「先按语言汇总、再看该语言有没有
被标成 `dubbed-auto`」。我们的归一化字段（`Functions.sniff_youtube`）此前也没保留
`format_note`，连这个唯一的标记都看不到，于是自动配音音轨：

1. 被当成普通独立音轨，进了默认视图的音频档（`_audio_tracks`）；
2. 参与 `_multi_languages()` 的语言归类 —— en-US 因此被算成「一种语言」，视频档按
   「分辨率 × 语言」拆出一整套 en-us 档（决策 58 的机制被误用）；
3. 还可能被 `_pick_compatible_audio()` 选去和视频流配对。

## 结论

1. `src/sookit/core/functions.py`：归一化格式字典新增 `'format_note'`（原样保留
   yt-dlp 的备注文本），给展示层一份可判定的事实。
2. `src/sookit/core/sniff_options.py` 新增两个判定：

   ```python
   def _is_auto_dubbed(fmt):        # format_note 含 dubbed-auto（大小写不敏感）
   def _auto_dubbed_languages(formats):  # 被自动配音占用的语言码集合（小写）
   ```

   第二个是**按语言汇总**的关键：只要某语言有一条音轨带 `dubbed-auto`，该语言的**所有**
   音轨都算自动配音（`233-0` 与 `139-0` / `140-0` … 是同一份音轨的不同容器）；人工配音
   （`- dubbed`，如日配）不会进这个集合。
3. `build_view_options()` **入口处一次性筛掉**：

   ```python
   auto_dubbed_langs = _auto_dubbed_languages(formats)
   formats = [f for f in formats
              if not (_is_auto_dubbed(f) or _lang_tag(f) in auto_dubbed_langs)]
   ```

   下游的 `_multi_languages()`（语言归类）、`_pick_compatible_audio()`（音轨配对）、
   `_pick_video_at_height()`（成品格式按语言）、`_audio_tracks()`（音频档归并）都只拿到
   筛过的列表，一处改动全部生效，也不会出现「视频档还绑着自动配音」这种半吊子状态。
4. **表格视图不动**：它走另一份 `_formats_data`（`sniff_youtube` 的原始归一化结果），
   自动配音行照旧列出，需要时可以手动勾选下载 —— 「默认视图替你过滤，表格视图给你全部」。

## 没采用的替代做法

- **只按单条音轨的 `format_note` 筛**（第一版实现）：只能筛掉 `233-0` / `234-0` 两条，
  同一份自动配音的 5 条 DASH 变体（`139-0` / `140-0` / `249-0` / `250-0` / `251-0`）仍留在
  默认视图，视频档也还拆着 en-us 一套 —— 与用户「其实全都是 dubbed-auto」的事实不符。
- **用 `info['language']`（原语言）判：非原声一律丢**：会把 ja 视频的**真人配音**一起丢掉，
  用户明确反对；同理也让决策 58 的多语言功能失去意义。
- **用 `language_preference < 0` 判配音**：机翻与人工配音的偏好值都是 -1（Shorts 的
  `140-0` 与 ja 视频的 `140-0` 完全相同），分不出两者。
- **在 UI 层过滤（只删卡片）**：语言归类那一步已经污染了（en-US 会拆出整套视频档），
  只删卡片等于让视频档继续绑着自动配音。
- **在 `sniff_youtube` 里直接丢弃**：表格视图也要能手动选这些音轨，源头丢会丢信息。

## 改动文件

| 文件 | 改动 |
|---|---|
| `src/sookit/core/functions.py` | 归一化格式字典新增 `format_note`（+3 行含注释） |
| `src/sookit/core/sniff_options.py` | 新增 `_is_auto_dubbed()` / `_auto_dubbed_languages()`；`build_view_options()` 入口过滤（+35 行） |
| `docs/decisions/60-ignore-auto-dubbed-audio.md` | 本文 |
| `docs/DEVELOPMENT.md` | 决策索引追加第 60 行 |

## 实测效果

该 Shorts（50 个原始格式；`233-0` / `234-0` 带 `dubbed-auto`，en-US 音轨共 7 条）：

| | 改前 | 改后 |
|---|---|---|
| 音频档 | 24 条（en-US 与 zh-Hant 混排） | **7 条，全部 zh-Hant** |
| 视频档 | 10 条（5 高度 × {zh-hant, en-us}，档位名带 `・xx` 后缀） | **5 条**，不再拆语言、无后缀 |
| 表格视图 | 50 行 | 50 行（不变，含 `233-0` / `234-0` / `140-0`） |

**人工配音必须保留**（ja 视频 `Yb9H5vhWyHg`，原声 ko、日配为真人配音）：视频档仍
10 条（`1080p・ko` / `1080p・ja` …）、音频档仍 12 条，`140-0`（日语 DASH）与
`233-0`（`日本語 - dubbed`）都在 —— 判定只认 `dubbed-auto`，不误伤人工配音。

其它样本回归（档数不变）：NCT 127（`Nr7R3mDeJsk`）5 视频 / 7 音频；X（`icccow/…`）
3 视频 / 3 音频（无自动配音，筛选是空操作）。

## 验证

`verify60b.py`（纯文字断言，19/19）：

- 该 Shorts 的 `dubbed-auto` 恰好是 `233-0` / `234-0`，自动配音语言集合 = `{en-us}`，
  en-US 音轨共 7 条（其中 5 条不带标记）；
- 默认视图视频档 5 条、音频档 7 条，**所有**档位（含合并选择器）都不含
  `140-0` / `251-0` / `139-0` / `249-0` / `250-0` / `233-0` / `234-0`，档位名无 `・` 后缀；
- ja 视频：无 `dubbed-auto`，档数 10/12 不变，`140-0` 与 `233-0` 仍在；
- NCT / X 档数不变；表格视图仍 50 行、`233-0` / `234-0` / `140-0` 仍在；
- 默认视图渲染出的卡片名里没有 `en-us`、没有 `dubbed-auto`。

## 风险

1. **标记只在 233/234 上，识别依赖它**：如果某语言只有 DASH 音轨、没有对应的
   `233-*` / `234-*` m3u8 音轨，该语言的自动配音就识别不出来、会留在默认视图（退化为
   改前行为，不会误删）。反过来「233/234 标 `dubbed` 但 DASH 是机翻」的情况不会发生 ——
   标记跟着音轨走。
2. **按语言整族丢弃**：某语言的自动配音被识别后，该语言下所有音轨（包括将来可能出现的
   该语言人工配音）都会一起消失；当前数据结构下两者无法进一步区分。
3. **靠英文子串匹配**（`dubbed-auto`）：yt-dlp 若改文案，判定失效 —— 表现是自动配音又
   出现在默认视图，不会误删。
4. **两处视图口径不同**：默认视图不列、表格视图列，用户可能困惑；文档与代码注释都写明了
   口径，且表格视图本来就是「看全部格式」的地方。

## 相关决策

- **决策 58**（默认视图按「分辨率 × 语言」拆分）：本条是它的补充 —— 自动配音不算一种
  可供选择的语言，先按语言筛掉再算语言列表；人工配音仍参与拆分。
- **决策 57**（`acodec` 缺失 = 未知而非无音频）：同属「yt-dlp 字段口径」类问题；
  `format_note` 这次也是「有事实但没被保留下来」。
- **决策 59**（表格视图加 ID 列）：表格视图因此成为查这些自动配音行的入口。
