# 72 · 浏览器统一直播 / Premiere 监控入口

## 行为与边界

YouTube 视频页 Sookit 菜单新增「直播 / Premiere 监控」，动作名 `monitor`。不识别或区分直播与首播，普通视频也可添加，已可下载的视频沿用监控页的入队规则。X/Twitter 菜单保持三项，扩展后台和 Python 协议均拒绝 X 的 monitor 请求。

请求经过既有站点开关、当前页视频校验、Native Messaging 和桌面接收总闸。可选标题、频道通过协议清洗后先行展示；网页提供的状态不参与结束判定。桌面成功接收或发现重复任务后切换内部导航到监控页，保持窗口显隐与焦点。

监控页使用当前保存目录、轮询间隔和增强下载设置，复用初次检测、预约等待和 MonitorWorker。浏览器入口不清空手动输入，不打断已有监控；按视频 ID 对当前列表去重（包括手动添加的短链），重复回执带原 task_id 与说明。保留在列表中的已完成任务仍算重复，删除条目后可重新添加。退出中、无效链接或缺少 yt-dlp 返回错误。

监控的语义仍为结束后下载最高画质，不在开播时录制。检测结果为 is_upcoming、is_live、unknown 时持续等待，was_live、post_live、not_live 才提交下载。HTTP 降级仅凭开始时间已过不能判断结束：显式正在直播优先，其次 endTimestamp 确认结束，预约/offline 或有活动详情但无结束证据时等待，只有 isLiveContent 的不完整活动信息为 unknown。保守判断可能延长等待，避免延迟开播时提前下载。

## 验证与使用

离线测试覆盖协议 YouTube 边界与元数据清洗、桌面路由/总闸/退出、不唤醒窗口、保存目录/保留输入、去重与删除后重加、频道回填、轮询等待至结束及停止、HTTP 状态解析。JavaScript 测试执行真实菜单点击和消息转发，覆盖 YouTube 四项与 X 三项边界。

本轮验证：test_browser_monitor.py 与 test_monitor_cards.py 合计 16 项，test_browser_bridge.py 31 项，test_youtube_metadata.py 6 项，test_browser_downloads.py 11 项，全部通过；node --test tests/test_browser_extension.cjs 50 项通过，git diff --check 通过。Qt 离屏测试有字体目录和 propagateSizeHints 提示，退出码为 0；未生成截图。

扩展改动后需在 Chrome 扩展页重新加载，并刷新 YouTube 页面。Python 协议白名单有变化，已有安装版要更新桌面程序与 Native host；只重新加载扩展无法让旧 host 识别新动作。源码开发可使用项目既有打包/注册流程。本轮不修改注册表，不自动安装，不承诺真实 Chrome 和在线直播/Premiere 已验收。

关联：决策 62、64、67、71。
