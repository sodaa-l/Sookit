# 决策 67. 浏览器扩展工具栏面板（2026-11-01）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)

## 背景

决策 62/64 之后，扩展的两个站点入口只能通过重新加载扩展或翻设置页间接控制，用户看不到「扩展到底有没有接上 Sookit」。需要给扩展加一个工具栏弹窗（点扩展图标弹出的面板），把三件事放在一处：链路状态、两个站点按钮开关、桌面端接收总闸的只读状态。

关键是「已连接」的语义：用户要求它指**扩展与这套装好的 Sookit 绑定通了**（扩展本身是生效的），而不是「Sookit 进程此刻跑着没」。因此状态按桥接是否可用判定：host 只要能应答，就说明扩展与桌面端的绑定成立——即使 Sookit 没启动，面板也显示「已连接」，另用「桌面端接收：未知」、主按钮「启动 Sookit」和一行提示说明现状。只有桥接没注册/被拒，才显示「未连接」。

## 决策

- **状态按桥接可用性判定**：`popup.js` 通过 `chrome.runtime.sendMessage({type: 'sookit.panel', op: 'status'})` 经后台转发到 host，host 再经本机通道问 GUI。三种结果：GUI 回执 `{ok, enabled, theme_color, dark}` → 「已连接」（`enabled` 为假则「已暂停」）；host 回 `{'ok': False, 'code': 'offline'}`（桌面端没开）→ 仍「已连接」，只是总闸显示「未知」、主按钮变「启动 Sookit」并提示「Sookit 未运行」；host 找不到或拒绝 → 「未连接」并指路重装/运行注册脚本。
- **status 绝不冷启动**：host 的 `forward_control({'action': 'status'})` 在 `send_request` 返回 `None` 时直接回 `{'ok': False, 'code': 'offline', 'error': 'Sookit 未运行'}`，不 `Popen`。开面板只探测，不把桌面端拉起来。
- **launch 才启动，且带可见窗口**：`forward_control({'action': 'launch'})` 先探一次；已连直接返回 `{'ok': True, 'started': False, 'status': …}`（不会二次启动）；未连则 `app_command(silent=False)` + 分离进程启动，30 秒内轮询 status 直到就绪，超时抛 `Sookit 启动超时，请手动启动后重试`。launch 不检查接收总闸——这是用户显式点击，不是页面请求。
- **动作白名单分通道**：`browser_protocol.py` 新增 `CONTROL_ACTIONS = {status, launch}` 与 `LOCAL_CONTROL_ACTIONS = {status}`；控制动作不带 URL，`validate_request(message, *, allowed=…)` 由调用方指定通道范围。Chrome host 放行 `BROWSER_ACTIONS | CONTROL_ACTIONS`，本机通道（`browser_ipc` 的 `LOCAL_ACTIONS`）只放行 `BROWSER_ACTIONS | LOCAL_CONTROL_ACTIONS`——launch 只可能来自扩展 host，页面脚本与本机通道都发不进来。
- **面板消息只认面板自己**：`background.js` 对 `message.type === 'sookit.panel'` 校验 `sender.id === runtime.id` 且（`sender.url === chrome.runtime.getURL('popup.html')` 或 `sender.origin === chrome-extension://<id>`），否则抛 `请求来源无效`；op 只能是 `status`/`launch`，其余抛 `不支持的面板操作`。不能像页面消息那样要求 `sender.tab && sender.frameId === 0`：工具栏 popup 不是标签页，这两项在真实环境里不成立（2026-11-01 实测面板因此一直显示"未连接"，回执是 `请求来源无效`）。改成按扩展自身来源判定后，内容脚本（url/origin 都是网页自己的）依然伪装不成面板。
- **站点开关存扩展本地**：`chrome.storage.local` 的 `sookitSites` = `{youtube: bool, x: bool}`，缺省视为开启。后台在转发前按站点拒绝被关闭的动作（`该站点的 Sookit 按钮已在扩展设置中关闭`）；`content.js` 按开关决定是否注入按钮，并订阅 `storage.onChanged` 即时增删。桌面端配置不参与这两个开关。
- **总闸只读**：面板显示「桌面端接收：已开启/已关闭/未知」，不提供开关——总闸仍由设置页管理，避免同一状态两个写入方。
- **主按钮两态**：桌面端在跑且已连接时，主按钮灰置为不可点的「已连接」，不做「唤窗到前台」（GUI 侧零副作用）；桌面端没开时按钮是可点的「启动 Sookit」。桥接未注册/版本过旧时按钮禁用，只留指路文案（启动 Sookit 解决不了桥接问题）。
- **UI 对齐既有风格**：强调色与明暗取自 GUI 回执（`theme_color`/`dark`），默认薄荷绿 `#3A9B8A`；圆角 8px、Microsoft YaHei 13px/11px、行高 48px、胶囊开关，与设置页、任务卡一致。离线时退回「跟随系统」。
- **扩展代码对缺失 API 容错**：`content.js` 用 `chrome.storage?.` 可选链，老测试环境（无 storage 桩）不受影响。

## 必做前提

改完必须重新打包并重新注册 host：`uv run --no-sync python scripts/register_browser_bridge.py`；只重载扩展不够——旧 host 不认识 `status`/`launch`，面板会一直显示「未连接」。面板在 `outdated`（旧 host 回 `不支持的浏览器请求`）时会给出重装指路。

## 验证覆盖

`tests/test_browser_extension.cjs` 覆盖：清单含 `action.default_popup`/`storage`、面板只认 popup 来源（页面冒充 launch 被拒且不触发本机调用）、未知 op 拒绝、launch 原样转发、站点开关关闭时同站点请求被后台拒绝、内容脚本按开关注入/移除、面板三种状态与主题同步、站点开关读写 storage。
`tests/test_browser_bridge.py` 覆盖：控制动作不带 URL 且通道范围限制（本机通道拒 launch）、status 不 Popen、status 原样转发 GUI 回执、launch 已连不重启、launch 冷启动不带 `--silent` 并轮询到绑定、本机通道 status 派发与 launch 拒绝。

针对性命令：`node --test tests/test_browser_extension.cjs`；`uv run --no-sync python -X utf8 -m unittest discover -s tests -p test_browser_bridge.py -v`。
