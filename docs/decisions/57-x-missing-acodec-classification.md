# 决策 57. 格式分类改用 yt-dlp 的 codec 口径：字段缺失 = 未知 = 有（2026-10-21）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 57」一一对应）

## 背景

用户报：X（Twitter）视频嗅探成功，但默认视图的画质卡片写着「**无音频流**」，表格视图里 9 个
格式有 6 个是「其他」——而这个视频其实有音轨，下载出来是有声的。

实测样本（`yt-dlp 2026.08.19`，`https://x.com/icccow/status/2112548568364192083`，
83 秒，返回 9 个格式）：

| format_id | vcodec | acodec | 实际是什么 |
|---|---|---|---|
| `hls-audio-32000-Audio` | `'none'` | **字段缺失** | 独立音轨 32kbps |
| `hls-audio-64000-Audio` | `'none'` | **字段缺失** | 独立音轨 64kbps |
| `hls-audio-128000-Audio` | `'none'` | **字段缺失** | 独立音轨 128kbps（最佳） |
| `hls-110/269/740` | `avc1.64001F` | `'none'` | 纯视频 270/360/708p |
| `http-288/832/2176` | **字段缺失** | **字段缺失** | 渐进式成品 270/360/708p，**实测 h264 + aac 双流** |

## 根因

`Functions.sniff_youtube` 的分类写的是 `f.get('acodec', 'none')`——把「**字段缺失**」当成了
「**没有音频**」：

- `hls-audio-*`：`vcodec` 明确是 `'none'`、`acodec` 缺失 → `has_video=False, has_audio=False`
  → 判成「其他」，默认视图因此整组音轨消失，`_video_option()` 走到 `audio_fmt is None`
  分支贴出「无音频流」。
- `http-*`：两个字段都缺失 → 同样判成「其他」，连带表格里既没有画质也没有编码。

yt-dlp 自己的口径与此相反：**只有字面量字符串 `'none'` 表示「这条流不存在」，字段缺失 /
`None` 表示「未知」，选择器按「有」处理**。证据：

- `yt-dlp -f bestaudio` 选中 `hls-audio-128000-Audio`（若 `acodec` 为 None 即不认，它选不出来）；
- `yt-dlp -f best` 选中 `http-2176`（渐进式成品）；
- 实测下载 `hls-740+hls-audio-128000-Audio` 合并产物：`ffprobe` → `h264 1278x708` + `aac`，有声。

## 结论

`src/sookit/core/functions.py`：

1. **分类判定与 yt-dlp 同口径**（`sniff_youtube` 内）：

```python
vcodec = f.get('vcodec')
acodec = f.get('acodec')
has_video = vcodec != 'none'      # None / 缺失 → True（未知，按「有」处理）
has_audio = acodec != 'none'
if has_video and has_audio:
    fmt_type = FormatType.VIDEO_AUDIO
elif has_video:
    fmt_type = FormatType.VIDEO_ONLY
elif has_audio:
    fmt_type = FormatType.AUDIO_ONLY
else:
    fmt_type = FormatType.OTHER
```

（源码里这四个常量就是中文字面量 `'视频+音频'` / `'音频流'` / `'视频流'` / `'其他'`，
`sniff_options.py:140-145`。）

2. **展示用编码名新增 `_codec_name()`**：`'none'` 与缺失/None 一律返回 `''`，其余取 `.`
   前的短名（`'avc1.64001F' → 'avc1'`、`'mp4a.40.2' → 'mp4a'`）。改前 `vcodec.split('.')[0]`
   在未知时会把整个字段名留在字符串里，现在表格「编码」列对未知编码显示为空。
3. **「其他」行的质量文案防 `None`**：`str(f.get('format_note') or '')`，避免出现字面「None」。

附带结果（符合既有口径、无需改代码）：表格视图的预勾选「有『视频+音频』就只勾最佳那一行」
（`youtube_page.py:647-655`）现在对 X 这类站点生效——从「视频流 + 音频流各勾一条」变成
「最佳成品格式单勾」，与决策 30 的「一体化格式单勾即可下载」一致。

## 补记：装上看效果后补的三处标识（同一天）

主改动落地后用户指出：「YouTube 下 ID 233 和 234 这两条就完全没有标识了」——表格里它们
类型是「音频流」，但**质量、编码两列全空**，只剩一个「ko」。

这两条的真实字段：`vcodec='none'`、**`acodec` 为 null**、`abr`/`tbr` 都没有，只有
`format_note='Default, low'` / `'Default, high'`、`protocol='m3u8_native'`、`quality=-1`
（yt-dlp 表格里因此标成 "Untested"）。它们确实是音轨（YouTube 的 m3u8 抽流），只是 yt-dlp
没报码率和编码。

补三处（只影响「有流但没数据」的少数格式）：

1. **表格「质量」列加 `format_note` 兜底**（`functions.py`）：分辨率/码率都没有可展示值时退回
   yt-dlp 自己的备注 → 233/234 显示 `Default, low` / `Default, high`。原来「音频流没码率就空着」
   的分支并入同一个兜底。
2. **默认视图音轨名加同样的兜底**（`sniff_options.build_view_options`）：码率未知的音轨原名用
   `acodec or '音频'`，而这两条 `acodec` 也是 null → 两张卡会**同名「音频」**。改成用
   `quality`（即上一条的 `format_note`）兜底，卡片变成 `Default, low` / `Default, high`。
3. **表格「编码」列对「未知」显式化**（`youtube_page.py`）：有音/视频但 yt-dlp 没报编码时写
   **「未知」**而不是留空（留空看起来像填充失败）；「其他」行（storyboard 等本来就无编码）保持空。

结果（YouTube `c7REvlMKFro`）：233/234 行变成 `音频流 | Default, low | 未知 | ko`，默认视图音频档
为 `129/101/56/48/45kbps + Default, low + Default, high`（无重名）。离屏 UI 冒烟 8/8 通过；
X 那组回归仍 11/11（其 `hls-audio-*` 同样「无编码」，现在也显示「未知」）。

**副作用**：`质量` 列可用宽度约 75px，`Default, high` 会折成两行、该行变高。若要单行可把
`Default, ` 前缀去掉只留 `low`/`high`，或统一显示「未标注」——当前保留 yt-dlp 原话。

## 没采用的替代做法

- **只修音轨判定**（`has_audio = acodec != 'none' or vcodec == 'none'`）：音轨能回来、默认视图
  不再误报，但 `http-*`（实测带音轨的成品）仍显示「其他」、无画质无编码，表格里摆着三条可用
  格式却不可识别。用户在同一轮确认里选了「按 yt-dlp 口径全修」。
- **靠 `format_note`/`format_id` 里有没有 `audio` 字样来猜**：X 的音轨恰好叫
  `hls-audio-...`，但 YouTube 的 `140`、B 站的 `30280` 等都不带 audio 字样，猜法不可靠。
- **保留旧判定、只给「其他」行加一句提示**：治不了默认视图的「无音频流」误报（那是按
  `格式类型 == 音频流` 收集音轨的）。

## 改动文件

- `src/sookit/core/functions.py`：`sniff_youtube` 分类 12 行 + 新增 `_codec_name()` +
  「其他」行 `quality` 防 `None`；两处 `vcodec`/`acodec` 展示字段改用 `_codec_name()`；
  质量列的 `format_note` 兜底（见补记）。
- `src/sookit/core/sniff_options.py`：默认视图音轨名对「码率未知」格式用 `quality` 兜底（见补记）。
- `src/sookit/pages/youtube_page.py`：表格「编码」列对未知编码显示「未知」（见补记）。
- `docs/decisions/57-x-missing-acodec-classification.md`（本文件）。
- `docs/DEVELOPMENT.md`：决策索引新增第 57 行。

## 实测效果

同一 X 链接，改后（`uv run python` 直连）：

| | 改前 | 改后 |
|---|---|---|
| 表格「类型」 | 3 视频流 + 6 其他 | 3 视频+音频(708/360/270p) + 3 音频流(128/64/32kbps) + 3 视频流(708/360/270p avc1) |
| 「其他」行 | 6 行（含 3 条真音轨） | 0 行 |
| 默认视图画质副标题 | `avc1 · mp4 · 无音频流` | `avc1+音频 · mp4`（708/360/270p） |
| 默认视图音频组 | 空 | 128 / 64 / 32kbps 三档 |
| 默认视图预选选择器 | 视频流 + 音频流组合 | `hls-740+hls-audio-128000-Audio` |
| 表格视图预勾 | 视频流 708p + 音频流 128kbps | 视频+音频 708p（单勾，最佳成品） |

回归（YouTube 同款链接 `zzFt3QY6kC4`）：类型统计「音频流 7 / 视频流 20 / 其他 4」与改前一致，
「其他」4 条全是 storyboard（`sb0`~`sb3`，确实既无视频也无音频、只有缩略图），编码列仍是
`avc1`/`mp4a`，默认视图档位 `1080p 60fps → 299+140` 不变。

UI 冒烟（离屏 FluentWindow 1131×731、挂 `msyh.ttc` 对齐真机字体）：真实嗅探结果走页面真实回调
`_on_sniff_done()` → 默认视图与表格视图各断言一次，**11/11 通过**，含「默认视图不出现『无音频流』」
「音轨 3 档」「表格无『其他』行」「预勾只有视频+音频 708p」等。

## 风险

1. **「未知」被当成「有」**：某站点若确实给了视频流却没填 `acodec`、而且真的没有音轨，会显示成
   「视频+音频」，表格预选也可能勾它。这与 yt-dlp 自身判定一致（它同样会把它当 A/V 下载），但
   界面上不再提示「无音频流」。
2. **未知编码在「编码」列显示为空**、默认视图副标题沿用旧兜底（`视频`/`音频`），不影响功能。
3. **只有字面量 `'none'` 算「不存在」**：个别站点若用空字符串 `''` 表示无音轨，仍会被判成「有」
   （yt-dlp 的选择器同样如此，口径保持一致性优先）。
4. 表格视图对「有成品格式」的站点恒只预勾一条：想走「视频流 + 音频流」手动合并的用户要自己改勾，
   互斥勾选逻辑（同类型只能一条）不受影响。
5. `_codec_name()` 只做展示，不参与 `is_standard_pair()` / `_merged_ext()` 等 mkv 预测判断；那些
   函数本来就对未知编码「放行不判定」（决策 38）。

## 验证

- `uv run python -c "from sookit.core.functions import Functions; ..."`：真实 X / YouTube 链接
  分类与档位逐条打印比对（见「实测效果」）。
- `yt-dlp -f "hls-740+hls-audio-128000-Audio" --merge-output-format mp4` + `ffprobe`：产物
  `h264 708p + aac`，确认「视频流 + 音轨」组合确实可用。
- 离屏 UI 冒烟 11/11（默认视图 + 表格视图，走真实嗅探回调）。

## 相关决策

- 决策 30（「视频+音频」一体化格式单勾即可下载）——本次预勾选行为变化由它定义。
- 决策 38（mkv 合并预测）：未知编码「放行不判定」，与本次口径同源（宁可不动声色，也不误报）。
- 决策 43（音轨恢复）：默认视图「音频」分组的来源，本次让它对 X 类站点真正有内容。
- 决策 47（结构化档位字段）：本节涉及的 `vcodec`/`acodec` 就是档位副标题的输入。
