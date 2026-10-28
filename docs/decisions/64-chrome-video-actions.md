# 决策 64. Chrome 视频菜单与直接后台下载

## 交互

修订决策 62 的「点击按钮立即嗅探」：点击 YouTube 视频操作栏的 Sookit 按钮只展开菜单，列出「嗅探视频」「下载最佳画质」「下载封面」。菜单采用 YouTube 颜色变量、12px 圆角、阴影、24px 线框图标和 48px 菜单项，适配亮暗主题。

菜单定位在按钮下方，底部空间不足时向上展开，并限制在视口内。再次点击按钮、点外部、Esc、焦点离开、站内导航和操作栏重建时关闭；支持上下键、Home/End 和 Tab。请求发送期间禁用按钮，旧页面的异步回执不显示。

## 分发与隔离

沿用 Native Messaging 与 Qt 本机通道，不增加端口或服务。扩展后台及 Python 协议层只允许 `sniff`、`download_best`、`download_cover`；仍只转发标准化的 YouTube 单视频链接，所有动作服从浏览器请求接收开关。

### 站内导航后的当前视频校验（2026-10-28 修复）

YouTube 站内导航复用同一份页面文档，内容脚本消息的 `sender.url` 可能仍是注入时的首页、搜索页或旧视频。把它当作当前视频提取 ID，会在视频正常播放时误报「没有找到有效的 YouTube 视频 ID」，或错误提示页面已切换。

后台保留扩展 ID、顶层 frame 和 HTTPS YouTube 来源检查，`sender.url` 只校验站点；通过 `chrome.tabs.get(sender.tab.id)` 读取发起请求的标签页当前地址，再规范化并核对请求视频。标签页已关闭、地址不可读、当前已不是视频页或可见的 `pendingUrl` 指向另一视频/站点时拒绝请求，不发送给本机应用。

为读取 `Tab.url` 和 `Tab.pendingUrl`，清单新增且仅新增 `https://www.youtube.com/*` 的 `host_permissions`，范围与内容脚本注入站点一致，不申请全局 `tabs` 权限。[Chrome 官方 Tabs 权限文档](https://github.com/GoogleChrome/developer.chrome.com/blob/main/site/en/docs/extensions/reference/tabs/index.md#permissions--perms-)说明匹配站点的 host permission 可读取这些字段。此处修订决策 62 原先直接比较消息来源视频 ID 的做法。

- `sniff`：保持决策 62 的后台嗅探、忙碌拒绝、导航切换和结果通知。
- `download_best`：直接以 `bestvideo*+bestaudio/best` 调用既有 `Functions.download_youtube`，由 yt-dlp 选择最佳视频及音轨并按需合并。复用下载并发、aria2c、远程组件选项和 FFmpeg 定位，显式使用 `--ignore-config --no-playlist`，避免外部配置改变一键下载的语义。
- `download_cover`：`core/browser_downloads.py` 使用 yt-dlp 的 `--skip-download --write-thumbnail --no-simulate` 下载最佳可用封面，最高档返回错误时由下载器尝试下一档，保留原图格式。`--ignore-config` 避免外部配置启用全部缩略图；不传实际下载器不支持的 `--no-write-all-thumbnails`。

两个下载动作独立加入既有 `TaskType.YTDLP` 队列，不读取或覆盖正在嗅探的视频、格式、标题和封面，不切换应用页面或抢占焦点。输出目录取嗅探页当前所选目录，未设置时使用现有系统下载目录。网页提示仅代表已入队，完成/失败沿用任务队列的通知与管理。

## 产物与生命周期

复用任务队列独占 workspace、进程创建回调、暂停/取消、最终交付与记录。封面输出使用 `<视频ID>_cover.<原扩展名>`，防止标题及嗅探状态互相串用。

`--skip-download` 不触发 `after_move`，封面调用完成后直接验收 workspace 中与当前视频 ID 对应的产物：必须恰有一个非空且扩展名属于 JPG/JPEG/PNG/WebP/AVIF 的文件。缺少封面或仅有临时文件时任务失败，不把空产物记为成功。音视频仍沿用原有最终路径处理。

## 验证与更新

- `node --test tests/test_browser_extension.cjs`：实际脚本的菜单、来源验证、动作转发、键盘操作、关闭路径、重复发送、SPA 导航及旧回执隔离。16 项通过，包含来源旧地址为首页/搜索页/旧视频、三种动作、页面脚本到后台的联动，以及标签页关闭/切换/URL 不可读时拒绝请求。
- `uv run --no-sync python -X utf8 -m unittest discover -s tests -p test_browser_bridge.py -v`：协议、现有实例/冷启动和嗅探行为。
- `uv run --no-sync python -X utf8 -m unittest discover -s tests -p test_browser_downloads.py -v`：下载入队与状态隔离；真实 yt-dlp 及 FFmpeg 配合本机 HTTP 验证最高分辨率视频和音轨合并、最佳封面原图、404 回退及无封面失败，不访问外部视频平台。

此次站内导航校验修复只涉及扩展：在 `chrome://extensions` 重新加载 Sookit，确认其 YouTube 站点访问权限，再刷新已打开的 YouTube 页面即可；无需重建 host 或重启桌面应用。

更新扩展后需在 Chrome 扩展页重新加载，并刷新 YouTube 视频页。Native Messaging host 包含协议白名单，也必须使用本次源码重新构建；源码环境重新运行 `uv run --no-sync python scripts/register_browser_bridge.py`，安装版随新包更新。已运行的桌面应用需重启。离线测试不代替真实 Chrome 的 YouTube 布局和完整本机桥接验收。
