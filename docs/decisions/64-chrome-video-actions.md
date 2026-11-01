# 决策 64. Chrome 视频菜单与直接后台下载

## 交互

修订决策 62 的「点击按钮立即嗅探」：点击 YouTube 视频操作栏的 Sookit 按钮只展开菜单，列出「嗅探视频」「下载最佳画质」「下载最佳封面」。菜单采用 YouTube 颜色变量、12px 圆角、阴影、24px 线框图标和 48px 菜单项，适配亮暗主题。

菜单定位在按钮下方，底部空间不足时向上展开，并限制在视口内。再次点击按钮、点外部、Esc、焦点离开、站内导航和操作栏重建时关闭；支持上下键、Home/End 和 Tab。请求发送期间禁用按钮，旧页面的异步回执不显示。

### X 视频帖

X/Twitter 视频帖在 `article[data-testid="tweet"]` 内依据视频播放器、status permalink 与 Grok 操作按钮识别。只有帖子含视频、状态链接可规范化且找到 Grok 按钮时才注入入口；按钮复制 Grok 按钮样式并读取其尺寸，放在 Grok 左侧且右侧留 8px 间距。站点 DOM 选择器仅作识别线索，需容忍 X 改版。

X 菜单按 X 原生 More 菜单的实测值自绘，不打开、不克隆也不修改 X 自身的 DOM：宽度随内容（上限 384px）、圆角 12px、容器无内边距；菜单项高 44px（内边距 12px 16px），图标 1.25em（15px 字号下 18.75px）且与文字间距 12px；文字 15px/20px、字体继承页面；字重取常规 400（X 内层标签实测 700，但继承到页面字体后渲染偏重，按观感取常规）；悬停底色为 3% 覆盖（亮色 rgba(0,0,0,.03)、暗色 rgba(255,255,255,.03)）。投影沿用 X 的 0 0 15px rgba(101,119,134,.2) 与 0 0 3px 1px rgba(101,119,134,.15)，暗色改用白色。实测环境为亮色主题、10 个英文菜单项，此时容器 248.69×440。文字色取自当前帖子文本，背景色与亮暗主题从页面背景推导。列出「嗅探视频」「下载最佳画质」「解析动图」，不提供封面动作。支持键盘操作、点外、失焦、Escape 关闭与滚动时重新定位。菜单状态、帖子复用和异步回执按当前状态链接校验，避免 DOM 重用后显示旧结果。

## 分发与隔离

沿用 Native Messaging 与 Qt 本机通道，不增加端口或服务。扩展后台及 Python 协议层按站点限制动作：YouTube 允许 `sniff`、`download_best`、`download_cover`，X/Twitter 允许 `sniff`、`download_best`、`parse_gif`；所有动作服从浏览器请求接收开关。`download_best` 与 `download_cover` 的 YouTube 请求可附带从当前页面读取的标题、频道和时长；页面字段未就绪时最多等待 2 秒，并校验等待期间视频未切换。X feed 中的状态链接规范化为 HTTPS `x.com` status URL，支持 `/video/N`、`/photo/N` 后缀；X `download_best` 可附带从当前帖子读取的正文标题、作者、可用时长和视频海报；扩展后台与 Python 协议层仅接受 HTTPS 精确主机 `pbs.twimg.com` 的海报 URL，并限制其他字段类型和长度；元数据用于最佳画质任务展示、封面结果通知和封面文件命名，不改变下载参数。

### 站内导航后的当前视频校验（2026-10-28 修复）

YouTube 站内导航复用同一份页面文档，内容脚本消息的 `sender.url` 可能仍是注入时的首页、搜索页或旧视频。把它当作当前视频提取 ID，会在视频正常播放时误报「没有找到有效的 YouTube 视频 ID」，或错误提示页面已切换。

YouTube 请求保留扩展 ID、顶层 frame 和 HTTPS 来源检查，`sender.url` 只校验站点；通过 `chrome.tabs.get(sender.tab.id)` 读取发起请求的标签页当前地址，再规范化并核对请求视频。标签页已关闭、地址不可读、当前已不是视频页或可见的 `pendingUrl` 指向另一视频/站点时拒绝请求。X 请求同样核对扩展 ID、顶层 frame、sender/tab/pending 的站点；feed 帖子的状态链接可能与当前标签页不同，因此来源域有效即可，只有标签页本身是 status 页才核对状态 ID。拒绝的请求均不发送给本机应用。

为读取 `Tab.url` 和 `Tab.pendingUrl`，YouTube 清单新增 `https://www.youtube.com/*`；X 扩展新增 `https://x.com/*`、`https://www.x.com/*`、`https://twitter.com/*`、`https://www.twitter.com/*`。host permissions 与脚本注入域一致，不申请全局 `tabs` 权限。[Chrome 官方 Tabs 权限文档](https://github.com/GoogleChrome/developer.chrome.com/blob/main/site/en/docs/extensions/reference/tabs/index.md#permissions--perms-)说明匹配站点的 host permission 可读取这些字段。YouTube 仍比对当前视频 ID；X feed 的帖子链接可能不同于标签页 URL，因此先验站点来源，标签页本身为 status 页时才比对状态 ID。

- `sniff`：YouTube 保持决策 62 的后台嗅探、忙碌拒绝、导航切换和结果通知；X 复用同一嗅探流程。
- `download_best`：直接以 `bestvideo*+bestaudio/best` 调用既有 `Functions.download_youtube`，由 yt-dlp 选择最佳视频及音轨并按需合并。复用下载并发、aria2c、远程组件选项和 FFmpeg 定位，显式使用 `--ignore-config --no-playlist`，避免外部配置改变一键下载的语义。YouTube 任务标题优先采用扩展传来的真实视频标题，缺失时回退到视频 ID；X 任务标题使用帖子正文，作者、时长和海报按可用性进入任务卡与通知，缺标题时回退到状态 ID。任务卡与系统通知显示可用的标题、频道和时长。X poster 缺失时入队后由独立后台 worker 运行 yt-dlp `--dump-single-json --skip-download --ignore-config --no-playlist` 查询，超时上限 30 秒；按现有 Twitter medium 单档策略优先取图（无 medium 时沿用 `normalize_thumbnails` 的通用降级），URL 必须通过 HTTPS 精确主机 `pbs.twimg.com` 校验。查询失败或无可用海报不影响下载，任务卡保留 VIDEO 图标占位。迟到的 poster 通过 `TaskQueueManager.update_task_cover_url(task_id, cover_url)` 更新活跃或已完成任务；完成任务立即写回 `completed_tasks.json`。
- `parse_gif`：仅限 X/Twitter status URL，路由到 `GifPage` 既有异步解析。成功、无结果和失败沿用现有系统结果通知，并遵循决策 40 的通知开关与主窗口失焦规则。
- `download_cover`：`core/browser_downloads.py` 使用 yt-dlp 的 `--skip-download --write-thumbnail --no-simulate` 下载最佳可用封面，最高档返回错误时由下载器尝试下一档，保留原图格式。`--ignore-config` 避免外部配置启用全部缩略图；不传实际下载器不支持的 `--no-write-all-thumbnails`。

`download_best` 独立加入既有 `TaskType.YTDLP` 队列，不读取或覆盖正在嗅探的视频、格式、标题和封面，不切换应用页面或抢占焦点。输出目录取嗅探页当前所选目录，未设置时使用现有系统下载目录。网页提示仅代表已入队；任务在 Worker 实际启动时发送开始通知，完成/失败沿用队列通知。三类通知均遵循决策 40 的开关与主窗口失焦规则。封面当前由决策 66 的专用 worker 直接下载，不进入任务队列。

## 产物与生命周期

复用任务队列独占 workspace、进程创建回调、暂停/取消、最终交付与记录。封面输出使用 `<视频ID>_cover.<原扩展名>`，防止标题及嗅探状态互相串用。

`--skip-download` 不触发 `after_move`，封面调用完成后直接验收 workspace 中与当前视频 ID 对应的产物：必须恰有一个非空且扩展名属于 JPG/JPEG/PNG/WebP/AVIF 的文件。缺少封面或仅有临时文件时任务失败，不把空产物记为成功。音视频仍沿用原有最终路径处理。

## 验证与更新

- `node --test tests/test_browser_extension.cjs`：实际脚本的菜单、来源验证、动作转发、键盘操作、关闭路径、重复发送、SPA 导航及旧回执隔离。16 项通过，包含来源旧地址为首页/搜索页/旧视频、三种动作、页面脚本到后台的联动，以及标签页关闭/切换/URL 不可读时拒绝请求。
- `uv run --no-sync python -X utf8 -m unittest discover -s tests -p test_browser_bridge.py -v`：协议、现有实例/冷启动和嗅探行为。
- `uv run --no-sync python -X utf8 -m unittest discover -s tests -p test_browser_downloads.py -v`：最佳画质真实 yt-dlp 及 FFmpeg 配合本机 HTTP 验证最高分辨率视频和音轨合并；浏览器封面逐档回退测试已改为独立 worker mock，详见决策 66。
- `uv run --no-sync python -X utf8 -m unittest discover -s tests -p test_x_cover_metadata.py -v`：验证 X poster 的 medium 档选择、HTTPS 主机白名单、yt-dlp 命令参数/30 秒超时/失败回退，以及 active 任务更新与 completed 任务 JSON 持久化。

此次站内导航校验修复只涉及扩展：在 `chrome://extensions` 重新加载 Sookit，确认其 YouTube 站点访问权限，再刷新已打开的 YouTube 页面即可；无需重建 host 或重启桌面应用。

更新扩展后需在 Chrome 扩展页重新加载，并刷新 YouTube 视频页和 X 视频帖。Native Messaging host 包含协议白名单，也必须使用本次源码重新构建；源码环境重新运行 `uv run --no-sync python scripts/register_browser_bridge.py`，安装版随新包更新。已运行的桌面应用需重启。离线测试不代替真实 Chrome 的 YouTube/X 布局和完整本机桥接验收。

## 封面路径修订（2026-10-31，见决策 66）

本决策第 23、25、29、31 行记录的是原始实现：浏览器封面通过 yt-dlp 下载并进入任务队列。该封面方案现由决策 66 修订。扩展的 `download_cover` 现在复用 `YouTubePage.CoverDownloadWorker`，按 `build_thumbnails(video_id)` 的优先级逐档直接下载，失败时清除部分文件后继续尝试；文件名优先使用安全清理并截短后的视频标题，缺失标题时回退视频 ID，后缀为 `_cover.<扩展名>`，冲突追加序号；封面结果通知显示视频标题及可用频道、时长。封面不再依赖 yt-dlp、不进入任务队列；扩展回执仍只表示已开始，完成/失败由共享封面回调发送 InfoBar 与系统结果通知（通知遵循现有开关和主窗口失焦规则），手动封面下载也使用这条结果通知。`download_best` 仍使用 yt-dlp 任务队列；扩展任务在 Worker 实际启动时发送开始系统通知，完成/失败沿用既有任务队列通知。
