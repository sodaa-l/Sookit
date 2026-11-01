# 决策 67. 浏览器扩展工具栏面板（2026-11-01）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)

## 背景

决策 62/64 之后，扩展的两个站点入口只能通过重新加载扩展或翻设置页间接控制，用户看不到「扩展到底有没有接上 Sookit」。需要给扩展加一个工具栏弹窗（点扩展图标弹出的面板），把三件事放在一处：链路状态、两个站点按钮开关、桌面端接收总闸的只读状态。

关键是「已连接」的语义：用户要求它指**扩展与这套装好的 Sookit 绑定通了**（扩展本身是生效的），而不是「Sookit 进程此刻跑着没」。因此状态按桥接是否可用判定：host 只要能应答，就说明扩展与桌面端的绑定成立——即使 Sookit 没启动，面板也显示「已连接」。总闸不靠「桌面端启动后才知道」，而是由 host 直接读配置文件得出，所以桌面端没开时也照常显示；主按钮则恒为「启动 Sookit」。只有桥接没注册/被拒，才显示「未连接」。

## 决策

- **状态按桥接可用性判定**：`popup.js` 通过 `chrome.runtime.sendMessage({type: 'sookit.panel', op: 'status'})` 经后台转发到 host，host 再经本机通道问 GUI。三种结果：GUI 回执 `{ok, enabled, theme_color, dark}` → 「已连接」（`enabled` 为假则「已暂停」）；host 回 `{'ok': False, 'code': 'offline'}`（桌面端没开，但仍带上它自己读到的 `enabled`/`theme_color`）→ 仍「已连接」，总闸照配置显示；host 找不到或拒绝 → 「未连接」并指路重装/运行注册脚本。
- **总闸与主题色由 host 读配置，不依赖 GUI**：`forward_control` 的 status 分支在 `send_request` 返回 `None` 时走 `_offline_status()`，直接调 `load_browser_extension_enabled()` / `load_theme_color()` 读配置文件，回 `{'ok': False, 'code': 'offline', 'error': 'Sookit 未运行', 'enabled': …, 'theme_color': …}`；读不出来时字段为 `None`，面板显示「未知」，不猜。用户要求的就是这一条：桌面端开没开，扩展都该知道总闸状态。
- **status 绝不冷启动**：host 的 `forward_control({'action': 'status'})` 在 `send_request` 返回 `None` 时直接回 `{'ok': False, 'code': 'offline', 'error': 'Sookit 未运行'}`，不 `Popen`。开面板只探测，不把桌面端拉起来。
- **launch 才启动，且带可见窗口**：`forward_control({'action': 'launch'})` 先探一次；已连则再发 `activate` 让 GUI 把窗口从后台/最小化带回前台，返回 `{'ok': True, 'started': False, 'activated': …, 'status': …}`（不会二次启动）；未连则 `app_command(silent=False)` + 分离进程启动，30 秒内轮询 status 直到就绪，超时抛 `Sookit 启动超时，请手动启动后重试`。launch 不检查接收总闸——这是用户显式点击，不是页面请求。
- **唤窗只走本机通道**：`activate` 由 GUI 的 `handle_browser_request` 处理（`_raise_window()`：清掉最小化位 → `show()` → `raise_()` → `activateWindow()`），返回 `{'ok': True, 'activated': True}`；它不带 URL、不受总闸约束，但扩展进程本身发不进来（host 只放行 `status`/`launch`）。
- **动作白名单分通道**：`browser_protocol.py` 新增 `CONTROL_ACTIONS = {status, launch, activate}`（都不带 URL）、`HOST_CONTROL_ACTIONS = {status, launch}` 与 `LOCAL_CONTROL_ACTIONS = {status, activate}`；`validate_request(message, *, allowed=…)` 由调用方指定通道范围。Chrome host 放行 `BROWSER_ACTIONS | HOST_CONTROL_ACTIONS`，本机通道（`browser_ipc` 的 `LOCAL_ACTIONS`）只放行 `BROWSER_ACTIONS | LOCAL_CONTROL_ACTIONS`——launch 只可能来自扩展 host，activate 只可能来自 host 进程，页面脚本两边都发不进来。
- **面板消息只认面板自己**：`background.js` 对 `message.type === 'sookit.panel'` 校验 `sender.id === runtime.id` 且（`sender.url === chrome.runtime.getURL('popup.html')` 或 `sender.origin === chrome-extension://<id>`），否则抛 `请求来源无效`；op 只能是 `status`/`launch`，其余抛 `不支持的面板操作`。不能像页面消息那样要求 `sender.tab && sender.frameId === 0`：工具栏 popup 不是标签页，这两项在真实环境里不成立（2026-11-01 实测面板因此一直显示"未连接"，回执是 `请求来源无效`）。改成按扩展自身来源判定后，内容脚本（url/origin 都是网页自己的）依然伪装不成面板。
- **站点开关存扩展本地**：`chrome.storage.local` 的 `sookitSites` = `{youtube: bool, x: bool}`，缺省视为开启。后台在转发前按站点拒绝被关闭的动作（`该站点的 Sookit 按钮已在扩展设置中关闭`）；`content.js` 按开关决定是否注入按钮，并订阅 `storage.onChanged` 即时增删。桌面端配置不参与这两个开关。
- **总闸只读**：面板显示「桌面端接收：已开启/已关闭/未知」，不提供开关——总闸仍由设置页管理，避免同一状态两个写入方。值由 host 直接读配置文件得出，桌面端没开也能显示。
- **主按钮恒为「启动 Sookit」**：不再随状态变身（已连接时也照旧是「启动 Sookit」），桥接未注册/版本过旧时禁用；面板底部不再有提示小字，状态信息只由状态胶囊和总闸行承载。
- **所有提示集中在标题栏**：状态胶囊就是唯一的提示区——`连接中…`/`正在启动…` 时圆点换成转圈（`data-state="busy"` 下 `.dot` 隐藏、`.spinner` 显示并旋转），启动失败显示「启动失败」、设置保存失败显示「设置未保存」（`error` 红点）；主按钮文字在拉起过程中保持不变，不在按钮或其它位置另起文案。
- **动作结果显示在页面右上角小窗**：指令一发出（还没等到回执）就在页面右上角弹出 `#sookit-feedback`，`data-state` 分三态——`loading`（转圈 + 动作等待文案，如「正在嗅探视频…」，常驻到出结果）、`success`（绿边，2 秒后自动收起）、`error`（红边，常驻，由用户点关闭按钮收走）；页面切换导致回执被丢弃时，`finally` 会把还停在 `loading` 的小窗收掉，不留残影。样式对齐 Sookit：右上角 `top/right: 16px`、浅色卡片 `#fffffffa`（正文 `#1a1a1a`、关闭按钮 `#888888`）、8px 圆角、强调色左侧 3px 色条、微软雅黑 13px、与面板标题栏同一套转圈参数；小窗可点（`pointer-events` 默认），只有关闭按钮一处交互。桌面端拉面板的方案（`chrome.action.openPopup()`，实测 156 可用）被放弃：面板会立刻抢焦点，用户在页面上动一下鼠标就把它关掉。
- **主题先读缓存再问回执**：回执里的 `theme_color`/`dark` 会记进 `chrome.storage.local` 的 `sookitTheme`；面板打开时先把缓存套到 `--accent`/`data-theme`，再发 `status` 覆盖。缓存读到之前 `body` 保持 `opacity: 0`（`data-ready` 后才显示），所以不会出现「先薄荷绿、回执到了再突然变色」。副作用：用户在 Sookit 里改了主题色后第一次打开面板仍会先显示旧色、回执到了才切到新色（缓存只可能落后一次）。
- **UI 对齐既有风格**：强调色与明暗取自回执（`theme_color`/`dark`），默认薄荷绿 `#3A9B8A`；圆角 8px、Microsoft YaHei 13px/11px、行高 48px、胶囊开关，与设置页、任务卡一致。桌面端没开时强调色仍取自配置（host 直接读），明暗退回「跟随系统」。
- **扩展代码对缺失 API 容错**：`content.js` 用 `chrome.storage?.` 可选链，老测试环境（无 storage 桩）不受影响。

## 必做前提

改完必须重新打包并重新注册 host：`uv run --no-sync python scripts/register_browser_bridge.py`；只重载扩展不够——旧 host 不认识 `status`/`launch`，面板会一直显示「未连接」。面板在 `outdated`（旧 host 回 `不支持的浏览器请求`）时会给出重装指路。

## 验证覆盖

`tests/test_browser_extension.cjs` 覆盖：清单含 `action.default_popup`/`storage`、面板只认 popup 来源（页面冒充 launch 被拒且不触发本机调用）、未知 op 拒绝、launch 原样转发、站点开关关闭时同站点请求被后台拒绝、内容脚本按开关注入/移除、面板状态与主题同步（含桌面端没开时由 host 读配置给出的总闸）、打开即用缓存主题且不等回执、回执后回写缓存、主按钮恒为「启动 Sookit」、面板不含底部提示小字、标题栏转圈、页面小窗在右上角/发出即弹/loading 与 success 与 error 三态、成功 2 秒收起、失败常驻到点关闭、站点开关读写 storage。
`tests/test_browser_bridge.py` 覆盖：控制动作不带 URL 且通道范围限制（本机通道拒 launch、host 拒 activate）、status 不 Popen 且总闸/主题色由 host 读配置（读不出来回 `None`）、status 原样转发 GUI 回执、launch 已连不重启而是叫 GUI 唤窗、launch 冷启动不带 `--silent` 并轮询到绑定、本机通道 status/activate 派发与 launch 拒绝。

针对性命令：`node --test tests/test_browser_extension.cjs`；`uv run --no-sync python -X utf8 -m unittest discover -s tests -p test_browser_bridge.py -v`。
