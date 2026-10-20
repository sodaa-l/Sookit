# 决策 50. 嗅探页档位卡片的左右留白对称（2026-10-20）

> **⚠️ 本文的「动态让位」部分已被 [决策 53](53-sniff-fixed-right-margin.md) 取代。**
> 右边距现已固定为 28px，`_sync_option_margins()` 与 `rangeChanged` 连接已删除。
> 本文保留作为「为什么考虑过动态方案、为什么最终放弃」的历史记录，
> **不要照着本文的代码描述去修改实现**。

> 决策索引：[DEVELOPMENT.md](../DEVELOPMENT.md#决策索引)（编号与代码注释里的「决策 50」一一对应）

## 背景

决策 49 为了让默认视图的滚动条贴住窗口右缘，把 `option_layout` 的右内距补成了
**30px**（左 0）。副作用是卡片块整体贴在滚动区左边缘、右侧空出 30px，看起来
像是排版没做完——用户原话：「现在右侧区域的卡片是靠左的」。

第一版尝试是让 `OptionFlowLayout` 在卡片被 `widgetMaximumWidth` 夹住后把剩余
空白均分（左 15 / 右 15）。实测布局数值确实对称，但用户看完直接否掉了：
「算了算了好丑，回到 7d30fcf」。**否决的是「把卡片在可用宽度里居中」这个思路**，
不是「留白对称」这个目标——宽窗口下卡片被夹在中间、左右大片空白，比贴左更难看。

## 根因

滚动条不是布局的一部分。`qfw.ScrollBar._adjustPos()` 里是
`move(size.width() - 13, 1)`——它是**浮在滚动区 viewport 右缘的独立子控件**，
占 13px 但不占布局空间。所以：

- 滚动条可见时：卡片右缘到 viewport 右缘的距离，其中 13px 被滚动条压着
- 滚动条不可见时：这 13px 是纯留白

只用一组固定边距，这两种状态里总有一个不对称。

## 结论

**左边距固定 15px；右边距 = 15 + （滚动条可见 ? 13 : 0），按滚动条显隐实时调整。**

| 状态 | 左内距 | 右内距 | 卡片右缘到滚动条 | 卡片右缘到 viewport |
|---|---|---|---|---|
| 滚动条可见 | 15 | 28 | 15 | 28 |
| 滚动条消失 | 15 | 15 | — | 15 |

实现（`youtube_page.py`）：

- 类属性 `OPTION_MARGIN_X = 15`、`SCROLLBAR_RESERVE = 13`
- `_sync_option_margins()`：读 `option_scroll.scrollDelagate.vScrollBar.maximum() > 0`
  （**不能用 `isVisible()`**——默认视图被切到表格视图时整块面板隐藏，滚动条也跟着
  「不可见」，但它一露出来照样占宽度），算出目标右内距，与当前值不同才
  `setContentsMargins`
- 触发点：`vScrollBar.rangeChanged` 信号（`ScrollBar.setRange` 里更新
  `setVisible` 之后紧跟着 `emit`）；`_populate_default_view` 末尾与
  `_apply_view('default')` 里的 `QTimer.singleShot(0, ...)`（填充当拍/刚从表格视图
  切回时滚动范围可能还没算完）；`_clear_default_view()` 里直接复位

**为什么不改 `option_scroll.setViewportMargins(0, 0, 13, 0)`**：实测可行
（viewport 472→459），但那是把 13px 从卡片可用宽度里永久扣掉，滚动条不出现的
时候会白白窄一截；动态边距只在实际需要时让位。

**为什么不给卡片做居中对齐**：用户已明确否掉那版（「算了好丑」）。最初的理由是
宽窗口下卡片会被 400px 上限夹住、居中后孤立在中间——**那个上限本身已在决策 51
取消**，现在卡片恒为可用宽度的一半，不存在「夹住再居中」的形态。

## 是否振荡

滚动条出现 → 右边距 +13 → 内容变窄 → 可能更矮 → 滚动条消失 → 右边距 −13 →
可能更高 → 滚动条又出现，是理论上的死循环。实测（966 宽 × 高度 620/644/700/760
反复切）`rangeChanged` 全程只触发 12 次且每次都收敛，没有抖动：原因是内容高度
由「每行卡片数固定为 2」决定，卡片变窄并不改变行数，所以右内距的变化不影响
内容总高度。若将来列数改成随宽度变化（例如回到 1 列/2 列自适应），这个前提
就不成立了，需要重新验证——已在 `DEVELOPMENT.md` 的决策索引里写明。

## 改动文件

| 文件 | 改动 |
|---|---|
| `src/sookit/pages/youtube_page.py` | 类属性 `OPTION_MARGIN_X=15` / `SCROLLBAR_RESERVE=13`；`option_layout` 初始边距改为 `(15,0,15,0)`；新增 `_sync_option_margins()`；`rangeChanged` 连接；`_populate_default_view` 末尾 `QTimer.singleShot`；`_clear_default_view` 复位右边距 |

## 风险

1. **依赖「列数固定为 2」这个前提**（见上节）。列数一旦随宽度变化，
   `rangeChanged` 与边距可能互相驱动，需要重新验证是否振荡。
2. `_sync_option_margins` 直接访问 `scrollDelagate.vScrollBar`，是 qfw 的
   实现细节（公开 API 只有 `verticalScrollBar()`，返回原生 `QScrollBar`，
   没有 `.handle`/显隐语义）。qfw 升级后若 `scrollDelagate` 改名会报错。
3. 滚动条宽度 13px 是 `ScrollBar` 内部的常量（`_adjustPos` 里的
   `width - 13`），不是配置项；qfw 改了它，`SCROLLBAR_RESERVE` 要跟着改。

## 验证

1. `uv run python -m compileall -q src/sookit/pages/youtube_page.py` 通过。
2. 真实 `FluentWindow` + 7 条伪造格式，30 项断言全绿：
   - 966×644（滚动条可见）：左留白 15、到滚动条 15（±1）、边距 28
   - 966×780（滚动条消失）：左右都是 15、边距 15
   - 高度在 644↔780 之间来回切，每次都能收敛到对称
   - 左栏恒 412、`minimumSizeHint().width()` 仍 709
   - 两个下载按钮 240×32、表格视图几何无回归
   - 视图偏好落盘、清空后边距回基准、空态回来
   - 默认视图↔表格视图来回切、期间改窗口高度，切回默认视图后边距仍正确
     （表格视图期间默认视图隐藏，`isVisible()` 为假但 `maximum()` 仍为 19）
3. 渲染 966×644 与 1131×731 整窗截图，肉眼确认卡片左右等距、滚动条贴窗口右缘。

## 相关决策

- 决策 47：嗅探页双视图（默认视图卡片是这里的对象）
- 决策 49：滚动条贴窗口右缘（本决策修正了它留下的「卡片贴左」副作用）
