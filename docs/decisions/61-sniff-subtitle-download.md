# 决策 61. 视频嗅探的字幕与实时评论下载（2026-10-24）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)。修订决策 47 的视图入口；字幕成功判定不沿用决策 6 的媒体容错规则。

## 交互

右侧顶部改为「音视频 / 字幕」，发现任意字幕（包括默认隐藏的自动字幕）或评论时才显示，默认进入音视频。音视频保留原有两种视图及 `general.sniff_view` 偏好，改用右上白色按钮在「表格视图 / 默认视图」间切换；字幕页隐藏该按钮。标题栏右距与卡片共用 `OPTION_MARGIN_RIGHT=24`。

字幕逐轨多选，显示语言名、原始语言码及来源，支持搜索。「全选当前筛选」只作用可见轨道，搜索保留已选项；批量操作阻断逐项信号，结束后仅汇总一次。SRT / TXT / VTT 三种格式可多选，默认 SRT；按钮显示轨道数乘格式数，任一未选则禁用。内容切换不清除音视频档位、表格勾选和字幕选择。

自动字幕（含自动翻译）默认隐藏，使用字幕搜索框右侧的开关控制，开关只显示拨杆、不显示 ON/OFF 状态文字；关闭时清除其选择。仅有自动字幕时仍显示字幕页及可用开关。新嗅探会清空轨道、搜索和自动字幕显示状态。明确识别的自动生成字幕置顶；明确识别的自动翻译轨道按 zh-Hans、zh-Hant、en、其他语言排序；类别无法确认的自动轨道随后显示，平台字幕最后。实时评论或弹幕使用独立对话框，逐次下载一条原始 JSON / XML，不参与三种字幕格式的选择；下载按钮位于“输出格式”行最右侧，与字幕卡片右边缘对齐。

## 元数据与归属

- `Functions.sniff_youtube` 的 JSON 嗅探增加 `--write-auto-subs`，以展开自动字幕元数据；`-J` 模式下不下载字幕文件。
- `build_subtitle_tracks(info)` 返回 `subtitle_tracks` / `comment_tracks`，轨道保留 `key`、`language`、`name`、`source`、`formats`、`bucket`、`title`；自动来源另带 `automatic_kind`（`generated`、`translated` 或 `unknown`）。同一语言若同时出现在 `subtitles` 与 `automatic_captions`，保留两条来源不同的轨道；每个桶内同语言的格式合并。下载使用原始 bucket 和语言码，不能仅凭展示来源反推 bucket。
- `subtitles` 桶不等于人工：展示为「平台字幕」。`automatic_captions`、`ai-` 语言前缀及明确 AI / 自动生成名称归为自动字幕；YouTube yt-dlp 的 `-orig` 后缀或 `(Original)` / `auto-generated` 名称标记原语种自动生成，URL 的 `tlang` 或名称中的翻译标记用于识别自动翻译。自动字幕列表中出现 `(Original)` 时，该 Original 轨道为自动生成，其余自动字幕归为自动翻译。已知视频源语言时，仅保留元数据能确认由该源语言翻译的自动翻译轨道；来源不符或无法确认的自动翻译轨道丢弃。源语言优先读取 `language` / `original_language`，缺失时从 `-orig` 轨道回退；若源语言也无法确认则不按来源过滤。旧版或其他提取器没有这些信息时标为 `unknown`，不保证能识别所有 AI 字幕。`live_chat` / `danmaku` 分流到评论。
- 字幕任务绑定成功嗅探时的 URL，不能读取后来被编辑的输入框。开始、停止、失败、超时或清空链接会使字幕结果失效；旧代际回调不得复位新嗅探的按钮与计时器。
- 嗅探 worker 创建时即保留引用并连接 `finished`，在 GUI 线程排队回收；字幕任务只复用当前嗅探代际的封面字节。

## 下载、导出与成功判定

复用 `TaskType.YTDLP`、kwargs 通道及每任务 workspace（决策 8、31），不新增任务类型或依赖。页面直接从 `core/subtitle_utils.py` 导入 `download_subtitles` / `download_comments`。

每条轨道单独运行一次 yt-dlp，显式 `--ignore-config --no-playlist --skip-download --no-simulate`；根据原始 bucket 启用 `--write-subs` 或 `--write-auto-subs`，显式关闭另一种。语言参数用 `re.escape` 加首尾锚点精确匹配，避免同语言的两个来源互相覆盖。每条源轨下载一次，多个目标格式在本地导出。

`--skip-download` 不触发 `after_move`，不能把预备视频路径当成字幕产物。因此 `run_ytdlp(..., subtitle_manifest=...)` 改用 `after_video:%(requested_subtitles)j` 写结构化清单，严格要求零退出码，且不使用媒体日志路径兜底。调用方核对单视频、准确语言、实际格式、扩展名、workspace 内非空文件；缺字幕即使工具退出 0 也失败。默认媒体调用仍使用 `after_move:filepath`，保持决策 6 的非零退出码容错。

SRT / VTT 源直接解析，其他格式先通过 FFmpeg 归一到 VTT。SRT / VTT 导出保留时间轴和纯文本正文，转义实体；TXT 去时间轴、序号和标签，保留段落与重复文本，不修错字、不智能去重。导出会损失样式、定位及逐词高亮，不承诺与原文件逐字节相同。评论只复制原始 JSON / XML。

源文件、清单、转换中间文件置于 workspace 下的临时子目录，全部轨道导出成功后才发布并返回完整产物列表。队列仅搬运这些登记文件，清理临时目录；目标同名按序加后缀，避免覆盖。非空 workspace 已被取消删除时不得重建；`None` / 空串按既有队列降级契约使用输出目录。

日志回调兼作取消检查点，入口、逐轨、转换、解析、逐格式及保存前均检查。取消后新创建的进程立即结束；worker 退出时再次清理 workspace，防止本地导出与删除并发留下文件。`run_ytdlp` 保留回调的原始取消异常，退出路径关闭 stdout、等待并清理本任务进程树。队列必须在调用 `worker.cancel()` 前保留引用并连接 `finished`，并在取消后检查 `isRunning()`，详见决策 9。

## 验证与边界

2026-10-24，在 Windows、Python 3.13.16、yt-dlp 2026.10.21、FFmpeg 8.1.1 下执行：

```bash
uv run --no-sync python -X utf8 -m unittest discover -s tests -p 'test_*.py' -v
```

76 条通过（50.571 秒）：后端 35、UI 行为 25、集成 12、取消顺序 3、原音视频链路 1。集成测试使用隔离配置与临时目录、本地 HTTP 服务、真实 yt-dlp / FFmpeg 和 Qt 队列，覆盖三轨九文件、每源一次请求且不下载媒体、JSON 原字节、ASS 转换、后轨失败整单不发布、坏 ASS 清理、下载及转换阶段取消、线程和进程退出、registry 清理、下一任务启动和同名防覆盖。进程测试确认已跟踪的进程退出，未逐个枚举所有后代 PID。UI 用文字断言验证选择、过滤、代际和几何；未完成人工窗口外观验收。

真实 YouTube 样本：`Yb9H5vhWyHg` 的平台 en 及自动 en 各导出三格式成功（55 段）；`jNQXAC9IVRw` 自动 en 三格式成功（6 段）。脚本比较 SRT / VTT 的时间轴正文及 TXT 正文，下载结束自动清理：

```bash
uv run --no-sync python -X utf8 tests/probe_subtitle_network.py 'https://www.youtube.com/watch?v=Yb9H5vhWyHg' --source platform --language en
uv run --no-sync python -X utf8 tests/probe_subtitle_network.py 'https://www.youtube.com/watch?v=jNQXAC9IVRw' --source automatic --language first
```

B站 `BV1J94y177Nt`、`BV1xx411c7Xg` 在嗅探阶段均报 `Unable to extract initial state`；原嗅探参数对照也失败，尚未进入字幕下载，不能认定 B站已通过端到端验证。本轮按用户要求暂不修复。

站点登录状态、限流、失效字幕或上游提取器变化仍可能导致失败；没有字幕的媒体不生成新字幕。测试依赖已安装的 PyQt 和外部 CLI，不会为测试安装工具。联网平台探针独立于默认离线测试，结果受当时站点状态影响。
