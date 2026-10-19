# 决策 46. 恢复「并发分片数」设置，默认 8（2026-10-19）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 46」一一对应）

## 背景

2026-09-06 的 `81fd39c` 把「并发分片数」整条链路删掉了（UI 卡片 / 配置项 /
`download_youtube` 形参 / `--concurrent-fragments` 命令行，共 6 个文件 `+17/−44`），
当时的口径是「分片流走 yt-dlp 原生默认顺序下载」。

2026-10-19 用户要求恢复，并把默认值定为 **8**（该设置历史上先是 4（`cfcadbd`），
后被统一成 10（`46c19ad`））。

## 为什么这个参数确实有用（与 aria2c 无关）

排查「某流不显示剩余下载时间」（决策 45）时验证过一条关键事实：**HLS 流不可能交给
aria2c 下载**。

- `yt_dlp/downloader/external.py` 里 `class Aria2cFD(ExternalFD)` 的
  `SUPPORTED_PROTOCOLS = ('http', 'https', 'ftp', 'ftps')`，`ExternalFD.supports()`
  要求**所有** proto 都在其中；`m3u8_native` 不在 → `can_download()` 返回 False。
- 分发流程（`yt_dlp/downloader/__init__.py`）对 `m3u8`/`m3u8_native` 有独立分支，
  非直播时最终返回 `HlsFD`——即使显式写 `--downloader m3u8:aria2c` 也一样
  （实测 `-v` 显示 `[debug] Invoking hlsnative downloader`，产物 md5 与不加参数完全一致）。
- 因此 `--downloader-args aria2c:-x N -s N`（决策 20 引入的「连接数」）对 HLS **毫无作用**，
  它只影响单文件 HTTP 分段下载。

结论：**分片流（HLS/DASH）的下载并发只能靠 yt-dlp 的 `-N/--concurrent-fragments`**。
本地实测（250ms/请求延迟的模拟网络，6 个分片）：`-N 1` 耗时 6.185s，`-N 6` 耗时 4.693s，
产物 md5 相同。`-x16` 对分片流无效果。

## 结论

### 1. 恢复整条链路，形参位置与历史一致

| 文件 | 改动 |
|---|---|
| `core/config.py` `load_download_config()` | 新增 `'concurrent_fragments': download_config.get(..., 8)` |
| `core/functions.py` `download_youtube` | 形参 `concurrent_fragments=8`，**紧跟 `remote_components`**（与 `cfcadbd` 历史签名同序） |
| `core/functions.py` `download_and_convert_gif` | 同上新增形参，透传给内部的 `download_youtube` |
| `core/workers.py` `MonitorWorker.__init__` | 新增 `concurrent_fragments=8`（紧跟 `remote_components`），并在 `download_ready` 的 config dict 里带上 |
| `pages/settings_page.py` | 恢复卡片「**yt-dlp 并发分片数**」+ `ComboBox` 档位 **1/2/4/8/16**，默认 8；加载/保存/信号连接；`_normalize_fragments` 兜底旧值 |
| `pages/youtube_page.py` | args 追加该值 |
| `pages/monitor_page.py` | 三处：建 worker 两处、`_on_download_ready` 的 `config.get(...)` 兜底 8 |
| `pages/gif_page.py` | args 追加该值 |

**形参位置固定在 `remote_components` 之后**，是位置参数，所有调用点必须同步改。
`download_and_convert_gif` 里它在 `aria2c_connections` **之后**（该函数历史顺序如此），
`gif_page.py` 的 args 也相应追加在末尾。

### 2. `> 1` 才追加命令行，保持「1 = 关」的直观语义

```python
# HLS/DASH 分片流并发下载（决策 46）。aria2c 的 SUPPORTED_PROTOCOLS 不含
# m3u8_native，HLS 恒由 yt-dlp 内建 hlsnative 下载器负责，`--downloader`
# 对它无效，因此分片流提速只能靠 -N。默认 1（顺序下载）；> 1 才追加。
if concurrent_fragments and concurrent_fragments > 1:
    cmd.extend(['-N', str(concurrent_fragments)])
```

- `-N 1` 是 yt-dlp 的默认值，显式传等于没传，故跳过。
- `0`/`None` 一律当「不并发」（`concurrent_fragments and ...` 先短路），避免脏配置传
  `-N 0` 触发 yt-dlp 报错。
- 用短选项 `-N`（历史实现用的长选项 `--concurrent-fragments`，两者等价，短选项更省日志宽度）。

### 3. 设置卡片不参与 aria2c 灰化

`settings_page.py` 里 `aria2c_switch_card` / `connections_card` 在 aria2c 检测失败时
会被 `setEnabled(False)` 灰化（`81fd39c` 引入）。新卡片**不加入**这个灰化列表——
HLS 根本不走 aria2c，aria2c 缺失时这个设置依然有效。

卡片文案为「**yt-dlp 并发分片数**」（点明它管的是 yt-dlp 的 `-N`，与上一张卡
「aria2c 连接数」不是一回事）。下拉档位只给 **1/2/4/8/16** 五档，不再用 1~16 连续档：
并发数没有「7 比 6 好」这种精细需求，档位少更容易选。

### 3.1 旧值归一（`config.normalize_fragments`）

档位收窄后，`ComboBox.setCurrentText('10')` 对不存在的项会**静默保持原选中值**，
结果是「UI 显示 8，实际配置仍是 10」。

**归一放在配置层（`core/config.py`），不是设置页。** 这是关键：如果只在
`_load_download_config()` 里归一 UI 显示，而 `load_download_config()` 仍把 10 原样
返回给下载链路，就会出现「界面显示 8，下载实际用 10」的静默不一致。归一必须在
**读取配置的那一刻**发生，让所有消费方（UI、`youtube_page`、`monitor_page`、
`gif_page`）拿到同一个合法值。

| 旧值 | 归一结果 | 理由 |
|---|---|---|
| `≤ 1`（含 0/负数） | `1` | 1 = 不并发 |
| `3` | `2` | **向下取档**，不静默抬高带宽 |
| `5/6/7` | `4` | 同上 |
| `9~15` | `8` | 同上；历史默认 10 正好落到新默认 8 |
| `≥ 16` | `16` | 封顶 |
| 非整数/脏值 | `8` | 回默认 |

关键取舍：**向下取档而不是就近取整**。设过 15 近就会变 16——那等于替用户增加了
带宽占用。宁可少并发，不可默默多占。

`config.py` 以 `FRAGMENT_TIERS = (1, 2, 4, 8, 16)` / `FRAGMENT_DEFAULT = 8` 常量定义；
设置页下拉若将来增删档位，**必须同步这两个常量**（否则 UI 能选到归一函数不认的值）。

归一结果**会写回配置**：用户在设置页动一下下拉框，`_on_download_setting_changed`
就把归一后的档位存盘，旧值随之消失。

### 4. 动图页也带上

动图页（`gif_page.py`）走同一个 `download_and_convert_gif` → `download_youtube`。
X 的视频/动图常是 HLS 或 DASH 分片流，同样受益。该页面是删卡片之后才做的
（决策 30/44），当年没赶上这个参数。

## 旧配置兼容

用户 `config.json` 里可能残留旧键 `download.concurrent_fragments`，值可能是历史默认
**10** 或 **4**。`load_download_config()` 现在统一过 `normalize_fragments()`：

- 缺失 → `8`（新默认）
- `10` → `8`（向下取档）
- `4` → `4`（合法档位，原样保留）

所以旧值不再「原样覆盖新默认」，但也**不是被粗暴重置**——落在档位上的值会保留。
用户若想要别的档位，在设置页选一次即可。

> 注意：`save_download_config(download_config)` 是**整个 `download` 段替换**，
> 调用方必须传全量字典。`_on_download_setting_changed` 已补齐三项，勿只传改动的那一项。

## 风险

1. **带宽**：8 路并发分片会显著提高瞬时连接数与带宽占用。小文件上并发反而可能因为
   握手开销摊薄不掉而**略慢**；这是把选择权交回用户的代价，故保留 1~16 可调。
2. **位置参数错位**：所有调用点必须同步改。已逐点核对：`functions.py` 内部 GIF 转发、
   `youtube_page.py`、`monitor_page.py`（×3）、`gif_page.py`，无遗漏。
3. **`download_ready` 的 config dict 结构变化**：`_on_download_ready` 用
   `config.get('concurrent_fragments', 8)` 兜底，旧格式（无该键）不会炸。

## 验证

1. `py_compile` 全部 7 个改动文件通过。
2. 桩掉 `run_ytdlp` 抓真实命令行：
   - `concurrent_fragments=8` → `-N 8` 且索引在 URL **之前**；
   - `=1` / `=0` → **不追加** `-N`（符合预期）；
   - `=16` → `-N 16`；
   - 与 aria2c 分支共存：同时出现 `--downloader`、`--downloader-args`、`-N 8`，互不干扰。
3. `load_download_config()` 三键齐全。

## 相关决策

- 决策 33 / 38：HLS 不使用 `--hls-prefer-ffmpeg`，走 `HlsFD`。
- 决策 45：HLS 与 aria2c 两条下载路径的 ETA 解析（本次改动**不触及**解析逻辑）。
- 本决策只是恢复 `-N` 参数与设置项，不改变任何下载器选择逻辑。
