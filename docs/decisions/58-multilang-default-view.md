# 决策 58. 多语言视频的默认视图：视频档按「分辨率 × 语言」、音频档按语言分档（2026-10-21）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 58」一一对应）

## 背景

用户报：YouTube 多语言视频（同一个视频有 ja/ko 两条音轨）在**表格视图**能看出语言（「语言」列
写着 ja / ko），但**默认视图完全没有语言信息**：

- 每个分辨率只有一张画质卡，且都悄悄绑死了**原声那条（ko）**——日语音轨在默认视图里选不到；
- 音频档按码率归并，**同码率的 ja / ko 并成一条**，另一种语言整档消失。

用户要求：

1. 多语言格式按「分辨率 帧率・语言」命名，例如 `2160p 60fps・JA`；
2. 语言码取 **yt-dlp 的 `language` 原值**，且**小写**（是 `ja` 不是 `JA`、更不是 `JP`）；
3. **只有单语言时不标语言**（普通视频与改前完全一样，不加噪音）。

## 实测样本

`yt-dlp 2026.08.19`，`https://www.youtube.com/watch?v=Yb9H5vhWyHg`（49 个格式，
1080p~144p、24fps），音轨的语言字段：

| format_id | type | language | language_preference | format_note |
|---|---|---|---|---|
| `137`（1080p avc1） | 视频流 | 空 | 0 | — |
| `140-1`（129kbps m4a） | 音频流 | `ko` | **10** | — |
| `140-0`（129kbps m4a） | 音频流 | `ja` | **-1** | — |
| `233-1` | 音频流 | `ko` | 10 | `한국어 - original (original)` |
| `233-0` | 音频流 | `ja` | -1 | `日本語 - dubbed` |

`language_preference` 是 yt-dlp 自己给「原声/默认音轨」打的偏好分（正数优先），
它决定了 yt-dlp 默认会挑哪条音轨，所以默认视图的「原声排第一」也跟着它走。

## 根因

`src/sookit/core/sniff_options.py` 的 `build_view_options()` 里：

- **视频档**只按 `height` 去重（`heights[:MAX_VIDEO_ROWS]`）→ 每个高度只出一张卡；
  `_video_option()` 再让 `_pick_compatible_audio()` 按「容器族 → 共同标准容器 → 码率」
  挑音轨，**语言根本没参与**，于是所有画质卡都挑到码率最高的那条（本例 ko）。
- **音频档** `_audio_tracks()` 的归并键是「码率取整」，同码率的 ja/ko 落进同一组，
  只留一条。

## 结论

`src/sookit/core/functions.py`：归一化字典补一个字段（`sniff_youtube` 内）：

```python
'language_preference': f.get('language_preference') or 0,
```

`src/sookit/core/sniff_options.py`：

1. 新增 `LANG_SEP = '・'`（全角中点，与副标题里的 `·` 区分）。
2. `_lang_tag(fmt)`：格式的语言码，小写；没有语言信息时 `''`。
3. `_multi_languages(formats)`：只在**独立音频流**里统计语言；**≥2 种才返回**非空列表
   （单语言/无语言站点返回 `[]` → 后续全部走原路径）；排序按
   `language_preference` 降序、再按语言码字典序 → 原声排第一、预选第一档即
   「最高画质 + 原声」。
4. `_pick_compatible_audio(formats, video_fmt, language=None)`：给定 `language` 时
   先把候选音轨过滤到该语言，**并在本语言内兜底**（宁可本语言低码率，也不能换成别的
   语言——那等于把音轨下错）。
5. `_pick_video_at_height(formats, height, language=None)`：独立视频流本身不带语言
   （所有语言共用同一个画面），只有**成品格式**（视频+音频一体）才按语言过滤，语言
   为空的成品视为通用。
6. `_video_option(formats, height, language=None)`：标题追加 `・ja`，`id` 同步加
   语言后缀（`h1080_ja`，保证唯一）。
7. `_audio_tracks(formats, languages=())`：`languages` 非空时归并键再带上语言
   （`(abr, lang)` / `('unk', lang, format_id)`），否则同码率 ja/ko 又会被并成一条。
8. `build_view_options()`：视频档改成双层循环 `for height in heights[:5]: for lang in
   (languages or (None,))`；音频档标题按语言加同一个后缀。

## 没采用的替代做法

- **语言码大写（`JA`）/ 用国家码（`JP`）**：用户先写「大写字母语言（例如 2160p 60fps・JP）」，
  随后更正为「不是 JP，是 JA」，最终口径是「yt-dlp 的 `language` 原值、小写」。语言码与
  国家码不是一回事（`ja` 是语言、`JP` 是国家），用国家码在 `pt-BR`/`zh-Hant` 这类值上会
  直接错位。
- **只改视频档、不动音频档**：同码率的 ja/ko 仍会被并成一条，日语音轨还是选不到。
- **视频卡只绑「该高度最好的音轨」而不分语言**：等于替用户默认，另一种语言永远看不到。
- **给所有视频都挂语言后缀（含单语言）**：普通视频每张卡都多一段 `・ko`，纯噪音；用户
  明确要求单语言不标。
- **在 `youtube_page.py` 里拼标题**：语言逻辑会和档位/选择器生成分叉（选择器在 core 里
  编译），表格视图与默认视图还要各写一遍；放 core 一处最省。

## 改动文件

| 文件 | 改动 |
|---|---|
| `src/sookit/core/functions.py` | `sniff_youtube` 归一化字典补 `language_preference`（+3 行） |
| `src/sookit/core/sniff_options.py` | 上述 8 处（+87 / −20） |
| `docs/decisions/58-multilang-default-view.md` | 本文 |
| `docs/DEVELOPMENT.md` | 决策索引追加第 58 行 |

## 实测效果

1131×731 默认视图（`Yb9H5vhWyHg`）：

- **视频画质 10 张**：`1080p・ko`、`1080p・ja`、`720p・ko`、`720p・ja` … `240p・ja`；
  同一分辨率的 ko / ja 两档**只差音轨**（`137+140-1` vs `137+140-0`）；预选第一张
  `1080p・ko`（最高画质 + 原声）。
- **音频 12 张**：6 种码率 × 2 种语言，`129kbps・ko` / `129kbps・ja` / `127kbps・ja` ……
  未知码率的两条也各自带语言：`日本語 - dubbed・ja`、`한국어 - original (original)・ko`。

回归（都不该出现后缀）：

- 单语言 YouTube（`c7REvlMKFro`）：视频 5 档 / 音频 7 档，标题与改前一致；
- X（`icccow/status/…`，无 `language` 字段）：视频 3 档 / 音频 3 档，与决策 57 之后一致。

## 验证

`verify58.py`（纯文字断言，不截图，23/23）：

- 多语言：`_multi_languages == ['ko', 'ja']`；视频档 10 档且标题顺序如上；`1080p・ko`
  的选择器以 `+140-1` 结尾、`1080p・ja` 以 `+140-0` 结尾；视频/音频档 `id`、`spec` 均唯一；
  音频档 ja/ko 各 6 档且标题都带语言；预选 `1080p・ko`。
- 单语言：`_multi_languages == []`、标题无 `・`、视频 5 档 / 音频 7 档。
- X：`_multi_languages == []`、视频 3 / 音频 3、无后缀。
- UI 真渲染（离屏 FluentWindow 1131×731）：卡片总数 22、同时出现 `1080p・ko` 与
  `1080p・ja`、默认选中 `1080p・ko`。

## 风险 / 已知边界

1. **只看独立音频流的语言**：若某站点只在成品格式上给 `language`，`_multi_languages()`
   判不出多语言，仍按单语言处理（与改前一致，不会更差）。
2. **卡片数量翻倍**：视频档上限仍是 5 个高度 → 多语言时最多 10 张视频卡 + 音频卡也翻倍，
   档位区要滚动。
3. 某个高度只有独立视频流、而该语言没有任何音轨时，会出两张只差后缀的「无音频流」卡
   （极少见，未特殊处理）。
4. **`language_preference` 缺失时按 0** → 排序退化为语言码字典序，「原声排第一」不再保证，
   预选档位也随之变化。
5. 表格视图未改：它本来就显示「语言」列，且每行是一份具体格式。

## 相关决策

- **决策 57**（X 的 `acodec` 缺失被误判为没有音频）：同为「按 yt-dlp 的口径走」，本决策
  复用了它归一化后的格式字典（`quality`/`language` 等字段）。
- 决策 47/50/51/53（档位卡片布局）：多语言只让卡片变多，不改变两列等宽与 24px 右边距。
- 决策 38（合并落点判定）：`_pick_compatible_audio` 的语言过滤发生在容器族判断之前，
  落点判定逻辑本身未动。
