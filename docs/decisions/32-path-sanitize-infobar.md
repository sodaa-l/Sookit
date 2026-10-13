# 决策 32. 路径输入清洗 + InfoBar 定位守卫（2026-10-11）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 32」一一对应）

**路径输入清洗**：用户从资源管理器「复制为路径」粘出来的路径带引号（`"E:\Music\a.mp4"`），
而各页此前只做 `.strip()`（只去空白、不去引号）。引号是 Win32 路径非法字符，后果有两种：
- 直接当路径用 → `os.chdir` / `open` / ffmpeg / yt-dlp 报 `[WinError 123] 文件名、目录名或卷标语法不正确`；
- 更隐蔽的一种：`burn_subtitles` 为规避 ffmpeg 的盘符冒号问题，会用**字幕路径的目录**当工作目录
  （`os.chdir(os.path.dirname(subtitle))`），开头的引号会落进 dirname（末尾那个引号被分给 basename），
  任务在调 ffmpeg 之前就失败——2026-10-11 用户实测，日志里的路径是
  `"E:\Music\...\dazbee_weverse_261011`。

- 新增 `PageBase.read_path(entry)`：`.strip()` → `sanitize_path()`（剥首尾引号 + `normpath`）；
  **空输入返回 `""`**（不能直接调 sanitize_path——`normpath("")` 会变成 `"."`）。
  只用于路径输入框：**URL 输入框不要用**（含 `://`，normpath 会改写）。
- 换用点：`subtitle_page`（video/sub/out）、`replace_audio_page`（video/audio/out）、
  `extract_audio_page`（video/out）、`gif_page`（out_dir）、`youtube_page`（out_dir ×2）、
  `monitor_page`（out_dir）。「输出目录为空」的既有默认值处理不变（`or DEFAULT_OUTPUT_DIR` /
  与视频同名拼接）。
- 刻意不做：不在 `Functions.*` 里兜底清洗——保持「输入清洗在 UI 层」的单一职责，
  队列任务之间的路径参数（均由程序内部产生）不重复清洗。

**InfoBar 定位守卫**：`widgets/infobar._settle` 用 `qfw.InfoBarManager.make(pos)._pos(bar)` 重算位置，
而 `_pos` 内部是 `self.infoBars[p].index(infoBar)`，`infoBars` 是 `WeakKeyDictionary`：
条已关闭 / 父对象已被回收 → **KeyError**；C++ 对象已销毁 → `adjustSize()` 抛 RuntimeError。
原守卫只写了 `(ValueError, RuntimeError)`，注释本意（「已从 manager 移除或已销毁」）恰好漏掉了
真正会发生的 KeyError，于是冒到全局 excepthook 记成 CRITICAL——2026-10-11 实测 2 次
（同一条 bar 的 0ms / 100ms 两个定时器都炸）。
- 改为整段 try + `except (KeyError, ValueError, RuntimeError)`，`adjustSize()` 也纳入 try。
  这属于纯尽力而为的定位同步（补的是「宽度收缩后重新定位」那颗补丁，见上文 InfoBar 段），失败即放弃。

**验证（2026-10-11 离线冒烟，`QT_QPA_PLATFORM=offscreen`，全程 monkeypatch 不触网）**：
25 项断言全过——import 环路检查（`pages/base` 新增 `core.functions` 依赖后主窗口与全部页面可导入）；
`read_path` 口径（剥首尾引号 / 单引号 / 正斜杠归一 / 首尾空白 / 纯空白与空输入 → `""`）；
页面用「用户实测那种带引号路径」入队后的 `args` 与 metadata（字幕页 video+sub+out 三处、
音频覆盖页并单引号、音频提取页、动图页空目录回退 `DEFAULT_OUTPUT_DIR`）；
`_settle` 在正常条 / manager 抛 KeyError / 控件已销毁三种情况下均不冒泡。
另：用 `sip.delete` 故意在 manager 仍持有该条时销毁控件，会让**测试进程退出时 SIGSEGV**（3 次 2 中）
——属测试副作用（悬空引用），与本次改动无关，已从常规用例里移除。
UTF-8 3 次 + GBK（cp936）2 次退出码 0、断言零失败。
