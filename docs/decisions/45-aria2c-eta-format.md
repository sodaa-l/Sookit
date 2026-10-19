# 决策 45. aria2c 原生进度行的 ETA/速度格式纠正 + 缺失即清空（2026-10-19）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 45」一一对应）

## 背景

用户反馈：「上一次下载的时候有某一个流不显示剩余下载时间」。排查日志
（`%LOCALAPPDATA%\Sookit\log\sookit.log*`，可用环境变量 `VIDEOTOOLBOX_LOG_DIR` 覆盖）
确认为 `ProgressParser` 的一个真实缺陷：

一次双流下载（视频 + 音频分别建任务）里，**HLS 视频流的 ETA 一直正常，走 aria2c 的
音频流（`140-drc`，https）从头到尾没有 ETA**。两者唯一差别是下载器不同，问题锁定在
`ProgressParser.parse_ytdlp_output` 的 aria2c 回退分支。

### 根因一：两条正则都对着「想象出来的」格式写的

原实现（`core/task_queue.py`，本次改动前）：

```python
# 注释写着「aria2c 原生 ETA 格式: ETA:00:19」
eta_match = re.search(r'ETA:\s*(\d+:\d+)', line)          # 期望 ETA:00:19
# 注释写着「aria2c 原生速度格式: DL:1.2MiB/s」
speed_match = re.search(r'DL:\s*(\d+\.?\d*\w+/s)', line)  # 期望 DL:1.2MiB/s
```

aria2c 1.37.0 的真实输出不长这样。全量统计 4 个日志文件（`sookit.log` + `.1~.3`）：

| 项目 | 真实形态枚举 | 命中旧正则 |
|---|---|---|
| ETA token | `ETA:Ns` 269 次、`ETA:NmNs` 160 次、`ETA:Nm` 4 次 | **0 / 497 行** |
| 速度 token | `DL:NMiB` 270 次、`DL:NKiB` 185 次、`DL:NB` 22 次 | **0 / 497 行** |

- ETA 是**带单位后缀的时长**（`19s`、`1m42s`、`3m`），不是 `mm:ss` 冒号计时。
- `DL:` 后面**从不带 `/s`**——`DL:` 本身就表示「每秒」，与 yt-dlp 的 `at 1.2MiB/s` 不同。

所以每一条 aria2c 原生进度行都只解析出 `progress`（那条 `(XX%)` 正则是写对的），
`speed`/`eta` 永远是空。`progress_data` 里没有 `eta` 键 → 界面 `eta_label` 一直空着。

该分支由 commit `7593d0a`（2026-08-11，`build.260811.1` 修复直播监控下载元信息/进度/
日志/标题显示）引入，两处注释与实现是同一个错误猜测，一直没被真实输出证伪过。

### 根因二：`get("eta", task.eta)` 让缺失的 ETA 沿用旧值

`_on_progress`（`core/task_queue.py`）：

```python
task.eta = progress_data.get("eta", task.eta)   # 旧值兜底
```

即使修好正则一，只要某一行解析不出 ETA（yt-dlp 的 `ETA Unknown`、aria2c 卡在
`DL:0B` 的时刻），界面就会**僵在上一次的数值上**。日志里 `ETA Unknown` 出现 1914 次，
HLS 流的原始行一旦 `Unknown`，UI 会一直显示一个不再成立的假时间——这是与根因一
相互独立的第二个缺陷。

## 结论

### 1. 速度正则补上「无 `/s`」的真实形态

```python
# aria2c 原生速度格式: DL:1.2MiB（无 `/s` 后缀，与 yt-dlp 的 `at 1.2MiB/s` 不同）
speed_match = re.search(r'DL:\s*(\d+(?:\.\d+)?[KMG]?i?B)', line)
if speed_match:
    result["speed"] = f"{speed_match.group(1)}/s"
```

- 匹配后**统一补 `/s`**，让界面上三种下载器的速度显示口径一致（都是 `X MiB/s`）。
- `[KMG]?i?B` 同时覆盖 `MiB`/`KiB`/`B`；缺 `[KMGT]` 前缀时也能匹配裸 `B`。
- 保留旧正则里没有的大小写敏感行为即可：aria2c 只输出大写单位。

### 2. ETA 支持带单位的时长并归一成 `mm:ss`

```python
# aria2c 原生 ETA 格式: ETA:19s / ETA:1m42s / ETA:3m / ETA:1h2m3s（不是 `mm:ss`）
eta_match = re.search(r'ETA:\s*((?:\d+h)?(?:\d+m)?(?:\d+s)?)', line)
if eta_match:
    eta = ProgressParser._aria2c_eta_to_mmss(eta_match.group(1))
    if eta:
        result["eta"] = eta
```

新增静态方法 `_aria2c_eta_to_mmss(token)`：

| 输入 | 输出 |
|---|---|
| `19s` | `00:19` |
| `1m42s` | `01:42` |
| `3m` | `03:00` |
| `1h2m3s` | `1:02:03` |
| `1h` | `1:00:00` |
| `59m59s` | `59:59` |
| `0s` / 空 | `""`（视为未知，不写入 `eta` 键） |

- **归一成 `mm:ss`**：`task.eta` 是纯展示用途，全应用原本只有 `mm:ss` 一种形态
  （yt-dlp 原生行、ffmpeg 计算值都是）。混进 `19s` 会让同一张卡片的 ETA 宽度跳动。
- **小时级退回 `h:mm:ss`**：`mm:ss` 装不下（`1h2m3s` → `62:03` 与 `1:02:03` 语义都有
  歧义），而 aria2c 的 ETA 出现小时级说明下载量不小，显示 `h:mm:ss` 更可读。
- `0s`/空 token 返回空串：aria2c 在 `DL:0B` 卡顿时给的就是 `ETA:0s`（或干脆没有），
  此时「没有 ETA」才是真实情况，不能写 `00:00`。

### 3. 缺失即清空，不再沿用旧值

```python
# ETA 不做「缺字段沿用旧值」：新一行没解析出 ETA 就说明此刻确实未知
task.eta = progress_data.get("eta", "")
```

只对 ETA 做这个处理，`progress`/`speed` 仍是旧的沿用口径。理由：

- `progress` 是**只增**的量，上一行的百分比在下一行仍然成立（沿用是对的）；
- `speed` 短暂缺失时沿用旧值也不会误导（速度本来就是滑动值）；
- `eta` 是**当下时刻的推测**，时间越久越失真，僵住一个假时间比不显示更糟。

**闪烁风险评估**：清空会在「有 ETA ↔ 无 ETA」交替时让标签闪烁。实测日志里 HLS 分片行
19069 行中只有 40 次相邻 `K↔U` 交替（0.2%）；ffmpeg 风格行 8702 行里仅 15 行缺 ETA
（0.2%）。风险可以忽略，换来的是「不再展示假 ETA」。

### 4. 注释同步订正

`core/task_queue.py` 里三处错误样例（`:666` 的 docstring 示例、`:707`、`:711` 的两行注释）
全部改成真实形态，并在 docstring 末尾补一句「ETA 一律归一成 `mm:ss`」。

## 边界与影响范围

- **只动 aria2c 原生行分支**。yt-dlp 原生 `[download]` 行、HLS `(frag i/M)` 分片行
  （决策 39）、`[aria2c]` 包装行的解析逻辑一字未改——这些分支的正则本来是对的，
  离线回归确认输出不变。
- **不改变任何下载行为**：只修解析，不碰 yt-dlp/aria2c 命令行、不动并发、不动重试。
- **决策 39 不受影响**：HLS 仍走 `HlsFD`（`aria2c` 的 `SUPPORTED_PROTOCOLS` 不含
  `m3u8_native`，见 yt-dlp `downloader/external.py`），分片序号进度照旧。
- **`ETA Unknown` 行本来就不产生 `eta` 键**（旧行为），本次只是让它连同旧值一起清掉。

## 验证

离线跑真实日志（不联网、不改环境），验证脚本一次性、不入库：

1. **覆盖率**（4 个日志文件共 31036 行，其中 aria2c 原生行 497 条）：
   - 修复前：ETA 命中 **0**、速度命中 **0**；
   - 修复后：**ETA 命中 416 条（含 ETA token 的 433 行中 96%）**、速度命中 **495 条**；
   - 剩余 17 条未解析出的都是「aria2c 行后面拼了 `[download] 100% of …` 的完成行」，
     这类行走**主分支**（有 `[download]` 前缀），`progress=100` 后任务随即结束，不需要 ETA；
   - 64 条无 ETA token 的行**零误报**（不产生 `eta` 键）。
2. **换算正确性**：抽样 8 条真实行，`ETA:1s→00:01`、`ETA:2m9s→02:09`、
   `ETA:1m38s→01:38` 等全部正确；9 个边界值（`59s`/`60s`/`1h`/`0s`/`1h2m3s`/
   `59m59s` 等）输出符合上表。
3. **回归**：yt-dlp 原生行、HLS 分片行（`progress` 用分片序号 142/1004）、
   `ETA Unknown` 行（只有 `progress`+`speed`）、`[aria2c]` 包装行、ffmpeg 行
   （`time=00:00:04.00 speed=2.0x total=10` → `progress=40.0, eta=00:03`）输出均与改动前一致。
4. `python -m py_compile src/sookit/core/task_queue.py` 通过。

## 相关决策

- 决策 33 / 38：HLS 不用 `--hls-prefer-ffmpeg`，走 `HlsFD`（aria2c 不支持 `m3u8_native`）。
- 决策 39：HLS 进度用分片序号，与本次 ETA 修复互不影响。
