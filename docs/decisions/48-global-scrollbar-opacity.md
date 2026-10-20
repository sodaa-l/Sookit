# 决策 48. 全局滚动条改为常显极淡、悬停加深（2026-10-21）

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 48」一一对应）

## 背景

qfw 的滚动条手柄默认常显（`ScrollBarHandleDisplayMode.ALWAYS`），亮色主题为
`rgba(0,0,0,114)`、暗色 `rgba(255,255,255,139)`，3px 宽。嗅探页默认视图改成两列卡片后，
右栏内容常常略高于视口，这条深灰竖线紧贴卡片右边，打断卡片的圆角轮廓，像一条多余的边框。

用户要求「把全局的滚动条的透明度提高」——**全局**，不是只改嗅探页。

## 备选方案与取舍

| 方案 | 做法 | 否决原因 |
|---|---|---|
| A 只调淡 | 补丁里把 alpha 调低，悬停时也保持淡 | 常显时够淡了，但滚动条彻底失去存在感，鼠标想抓的时候找不到；用户看完对比图后未选 |
| B 改为悬停才显示 | `setHandleDisplayMode(ON_HOVER)` | **实测只认右侧 12px 窄条**：鼠标进入内容区（`enterEvent`）不会触发，必须精确移到最右边那条 12px 区域才淡入。用户日常根本触发不了，等于滚动条彻底消失（见下方实测） |
| **C 常显极淡 + 悬停加深（采用）** | 常态 alpha 压低，`expand()` 时恢复原生浓度 | 常显不抢视线、又始终有位置提示；鼠标靠近滚动条才变深，符合「静默界面」的一般做法 |

### 方案 B 的实测记录（为什么不能选它）

`ScrollBar.enterEvent` 绑定在**滚动条自身**这个 12px 宽的 widget 上，而不是它所覆盖的
滚动区。实测三态截图：①平常完全不显示 ②鼠标在卡片区、内容区移动 → 仍然完全不显示
③鼠标精确移到窗口最右侧 12px 才淡入（同时 3px→6px 加粗、轨道浮出、上下箭头出现）。
对一个纵向列表来说，用户要把鼠标推到屏幕最右边才能看见滚动条，比现状更差。

## 结论

### 1. 只补丁三处，改的是「手柄颜色」

qfw 的滚动条是纯 `QPainter` 自绘（`.venv/Lib/site-packages/qfluentwidgets/components/widgets/scroll_bar.py`），
**不是 QSS**，包内也没有任何 `.qss` 文件、`common/config.py` 里没有滚动条配置项，
所以没有「改一个全局样式表」的选项。可用的公开 API 只有逐控件
`ScrollBar.setHandleColor()` / `setGrooveColor()` / `setArrowColor()`——那要改 6+ 处调用点，
且改不到 qfw 内部自建的（菜单、`LineEdit`、导航栏、表格）。因此选择**在类上打补丁**，
一处覆盖全应用。

实现在 `core/utils.py`：

```python
SCROLLBAR_HANDLE_FAINT = ((0, 0, 0, 55), (255, 255, 255, 70))     # 常显：极淡
SCROLLBAR_HANDLE_HOVER = ((0, 0, 0, 114), (255, 255, 255, 139))   # 悬停：qfw 原生浓度

def apply_global_scrollbar_style() -> None: ...
```

三处补丁：

| 原方法 | 补丁做的事 |
|---|---|
| `ScrollBarHandle.__init__` | 调原 `__init__` 后把手柄初始色改成极淡 |
| `ScrollBar.expand` | 调原方法后，**若 `self._isExpanded` 为真**才切到悬停色 |
| `ScrollBar.collapse` | 调原方法后，**若 `self._isExpanded` 为假**才切回极淡色 |

**必须看 `_isExpanded` 而不是无条件切色**：原 `expand()` 开头是
`if self._isExpanded or not self._isEnter: return`，原 `collapse()` 是
`if not self._isExpanded or self._isEnter: return`——两者都会在条件不满足时**早退**。
早退时颜色不该被改，所以补丁按原方法执行后的真实状态决定，而不是假设「调了就等于展开成功」。

### 2. 悬停浓度的取值：正好等于 qfw 原生值

`SCROLLBAR_HANDLE_HOVER` 就是 qfw 原本写死的常量（亮 `114`、暗 `139`）。这样悬停时
视觉与改动前完全一致，用户不会觉得「滚动条被改丑了」，只是平时变淡了。

### 3. 只改手柄，不动轨道与箭头

`ScrollBarGroove` 默认 `opacity=0`（只在 `expand()` 时 150ms 淡入到 1），
`ArrowButton` 也是随轨道一起出现——它们本来就只在悬停时可见，不属于「常显」，
不需要动。轨道颜色（亮 `252,252,252,217` / 暗 `44,44,44,245`）保持原样，
否则悬停时滚动条会变成另一副样子。

### 4. 幂等 + 调用时机

`apply_global_scrollbar_style()` 用类属性 `_sookit_scrollbar_patched` 做幂等守卫，
重复调用直接返回。

调用点在 `__main__.py` 里 `app.setFont(...)` 之后、`MainWindow()` 之前：

```python
from sookit.core.utils import apply_global_scrollbar_style
apply_global_scrollbar_style()
```

**必须在 `MainWindow` 之前**：手柄颜色写在 `__init__` 里，已经建好的手柄不会回溯改色。
（渲染/测试脚本若不走 `__main__.main()`，需要自己先调一次。）

## 为什么打补丁是可接受的

- 项目已有先例：决策 44 里 `pages/gif_page.py` 同样 monkeypatch 了 `qfw.MessageBox`。
- 补丁面很小：只碰三个方法，且**都先调原实现、再按原状态改颜色**，不复制、不替换原逻辑。
  即使 qfw 升级改了这三个方法的内部实现，补丁也只是「拿不到预期效果」，不会让滚动条坏掉。
- 覆盖面是真正「全局」的：实测应用内 20 个 `ScrollBarHandle`（含表格、菜单、`LineEdit`、
  导航栏）全部生效，无遗漏。

## 改动文件

| 文件 | 改动 |
|---|---|
| `core/utils.py` | 新增 `SCROLLBAR_HANDLE_FAINT` / `SCROLLBAR_HANDLE_HOVER` / `apply_global_scrollbar_style()` |
| `__main__.py` | `main()` 里 `app.setFont()` 之后调用 `apply_global_scrollbar_style()` |

## 风险

1. **qfw 升级**：若上游把 `expand`/`collapse` 改名或改语义，补丁的 `_isExpanded` 判断
   可能失效 → 表现是悬停不再加深（滚动条仍然可用，不是崩溃）。升级 qfw 后需重跑验证。
2. **暗色主题对比度**：暗色常态 `alpha=70`（原 139 的一半），在纯黑背景上更接近消失。
   已渲染确认可辨；若实机觉得太淡，只需调 `SCROLLBAR_HANDLE_FAINT` 常量。
3. **补丁对第三方弹窗/菜单的连带影响**：这些是 qfw 内部创建的滚动条，一并变淡。
   属于「全局」的本意，但若某个弹窗里滚动条很重要，需要单独 `setHandleColor()` 覆盖回来。

## 验证

1. **初始色**：真实 `MainWindow` + 一个 400 字符 `LineEdit` + 40 行 `TableWidget`，
   遍历 `app.allWidgets()` 得到 **20 个手柄，亮色主题初始色分布只有
   `{(0, 0, 0, 55)}`，未覆盖项 0 个**；暗色主题 18 个手柄全为 `(255, 255, 255, 70)`。
2. **悬停切换**：`sb._isEnter = True` + `sb.expand()` 后，手柄色 → `(0, 0, 0, 114)`、
   宽度 3→6px、轨道 `opacity` 0→1.0；`collapse()` 后回到 `(0, 0, 0, 55)`、宽度 3px、
   轨道 `opacity` 0.0。暗色同理（`139`）。
3. **幂等**：连续调用 `apply_global_scrollbar_style()` 两次，`_sookit_scrollbar_patched`
   仍为 `True`，颜色不被重复包装。
4. **覆盖范围**：切到表格视图后再统计，20 个手柄仍全部为极淡色。
5. **视觉**：渲染 966×644 实机窗口的平常态与悬停态，6 倍放大对比——
   平常态细、淡，卡片圆角轮廓连续；悬停态恢复改动前的浓度与 6px 宽度。

## 相关决策

- 决策 44：GIF 页弹窗 monkeypatch `qfw.MessageBox`，是本项目打补丁的既有先例。
- 决策 47：嗅探页默认视图两列卡片。本决策是为了让常显的滚动条不打断卡片布局，
  但作用范围是所有页面。
