# 决策 42. yt-dlp 来源优先级反转为「内置优先」（2026-10-17）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 42」一一对应）
>
> 注：编号 41 由清理分支 `chore/remove-dead-code` 占用（死代码清理），本分支从 `master` 切出，故顺延取 42。

## 背景

原设计是「PATH 中的全局 yt-dlp 优先，未检测到则回退内置 `tools/yt-dlp/yt-dlp.exe`」（见 `core/ytdlp_utils.py` 模块 docstring）。该设计假设用户会自行管理 yt-dlp（scoop / pipx 等），应用不该抢。

但它带来两个实际问题：

1. **内置版形同虚设**。安装包「完整安装」会把 `yt-dlp.exe` + `deno.exe` 装进 `{app}\tools\yt-dlp\`（约 110 MB），可只要用户 PATH 里另有一份 yt-dlp，内置那份就永远不被使用——占着体积却不起作用。
2. **更新能力被让渡**。PATH 来源的副本应用**不能也不应**代更新（决策 28：只弹一次 InfoBar 提示「请自行更新」）。于是「装完整版 → 发现还是旧版 → 得自己去 scoop update」这种迷惑路径真实存在。

## 决策

把来源解析反转为 **内置 `tools/yt-dlp` 优先，PATH 仅作为回退**：

```python
# src/sookit/core/ytdlp_utils.py:77-94
def get_ytdlp_cmd() -> list[str] | None:
    p = get_ytdlp_exe_path()
    if p.is_file():
        return [str(p)]        # 内置优先
    exe = _which("yt-dlp")
    if exe:
        return [exe]           # PATH 回退
    return None

def get_ytdlp_source() -> str | None:
    if get_ytdlp_exe_path().is_file():
        return "tools"
    if _which("yt-dlp"):
        return "path"
    return None
```

**只改这两处**。`build_ytdlp_cmd()` 里 `if get_ytdlp_source() == "tools"` 的 Deno 挂载、`check_path_ytdlp_update()` / `check_tools_ytdlp_update()` 的来源互斥、设置页三处 `get_ytdlp_source()` 分支，全部自动跟随。

## 四态对照（实测）

| 内置 tools | PATH | 改前选中 | 改后选中 |
| --- | --- | --- | --- |
| 有 | 有 | **path** | **tools**（本次变化点） |
| 有 | 无 | tools | tools（不变） |
| 无 | 有 | path | path（不变） |
| 无 | 无 | None | None（不变） |

即：**只有「两者都有」这一格变了**，其余三态行为完全一致。

## 连带行为变化（均为自动跟随）

| 位置 | 变化 |
| --- | --- |
| 设置页 `_render_yt_label` | 两者都有时显示「yt-dlp（内置）— X / Deno — Y」，而非「（PATH）」 |
| 设置页 `_check_versions` | 两者都有时顺带检测 Deno 版本（`source == "tools"` 分支） |
| 设置页 `_update_ytdlp` | 「下载/更新」按钮从「只检查、引导自行更新」变成**真的可代更新**（走 `updater.exe` 提权） |
| 主窗口周期检查 | 两者都有时走 `check_tools_ytdlp_update()` 那路（点亮 Badge），PATH 路返回 `skipped` |
| `build_ytdlp_cmd` | 内置来源自动挂 `--js-runtimes deno:<绝对路径>`；PATH 来源不干预（保持原样） |

## 明确不做

- **不加「强制用 PATH」的逃生口**（如 `VIDEOTOOLBOX_YTDLP_SOURCE=path` 环境变量）。用户明确表示不需要：想用 PATH 那份，把 `tools\yt-dlp\yt-dlp.exe` 删掉即可。
- **不改 PATH 来源的更新策略**（决策 28 仍有效）：PATH 副本依旧只提示「请自行更新」，不代下载、不亮 Badge。

## 验证

分两层：

**1. 打桩四态 + 参数挂载（7 项断言）**
- `tools+PATH` → src=tools、cmd=内置路径；`tools only` → tools；`PATH only` → path；`neither` → None；
- 内置 + 有 deno → `build_ytdlp_cmd` 含 `--js-runtimes deno:<绝对路径>`；
- PATH 来源 → 不含 `--js-runtimes`；
- 两者都可用 → `get_ytdlp_cmd()[0] == 内置路径`。

**2. 真实端到端（临时把真 yt-dlp.exe 放进 `tools/yt-dlp/`，测完删除）**
- `get_ytdlp_source()` = `tools`、`get_ytdlp_cmd()` = `[内置路径]`（PATH 里同时有 scoop 的 yt-dlp）；
- `build_ytdlp_cmd("--version")` 真跑 → `rc=0`、输出 `2026.08.19`；
- `get_ytdlp_current_version()` = `2026.08.19`；
- 来源互斥：`check_path_ytdlp_update()` = `('skipped','','')`、`check_tools_ytdlp_update()` = `('latest','2026.08.19','2026.08.19')`；
- 设置页 `_render_yt_label()` 渲染出「（内置）」字样。

**3. 回归 PATH-only**（本机真实状态，`tools/yt-dlp` 不存在）：来源仍为 `path`、三个页面无「未找到 yt-dlp」提示条、Badge 不亮。
