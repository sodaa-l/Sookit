# 决策 55. 嗅探页表格补一圈细边框（`setBorderVisible`），四边闭合（2026-10-20）

> ⚠️ **本决策已被决策 56 部分取代（2026-10-21）**：用户要求「表格视图取消下闭合、右边线落在列尾」，
> 现在 `setBorderVisible(False)`、`setBorderRadius(8)` 已删除，改成右下开口 + `_TableEdgeRule`
> 自绘右竖线（空表时同一条线还负责补左边线）。本文的**根因分析（线从哪来）与实测数据仍然有效**，
> 结论段仅作历史记录，见
> [56-table-view-open-bottom-right.md](56-table-view-open-bottom-right.md)。

## 背景

表格视图一直是「左、上」有线、「右、下」没线：表头像是被画了一个开口的框，
数据区右下角直接融进白色背景。用像素扫描确认过这是**稳定复现的既有行为**，
不是决策 49~54 改出来的回归 —— 决策 54 只动了表格右侧宽度，四边的情况改前改后一致。

用户看图后问「为什么左和上有闭合，右和下没有」，确认要补齐。

## 根因

qfw 的表格样式表（`FluentStyleSheet.TABLE_VIEW`）里有三条规则叠在一起：

```css
QTableView { background: transparent; outline: none; border: none; }      /* ① 表格自身无外框 */
QTableView[isBorderVisible=true] { border: 1px solid rgba(0,0,0,15); }    /* ② 主动开启才有外框 */
QHeaderView::section { border: 1px solid rgba(0,0,0,15); }                /* ③ 表头每格四边都有线 */
QHeaderView::section:horizontal:last { border-right: none; }              /* ④ 最后一个表头格去掉右边线 */
QTableView::item { background: transparent; border: 0px; }                /* ⑤ 数据行无边框 */
```

`_FormatTable` 从未调用 `setBorderVisible()`，属性 `isBorderVisible` 是 `None`，
规则②不生效，**所以表格根本没有外框**。看到的线全部来自规则③「表头每个格子自带
1px 边框」：

| 边 | 有没有线 | 来源 |
|---|---|---|
| 上 | 有 | 规则③：表头各格子的 **top border** 横向连成一线（实测 `y=y0` 起连续 568px） |
| 左 | 有 | 规则③：行号列各格子的 **left border** 纵向连成一线（实测 `x=x0` 起连续 378px） |
| 右 | **无** | 规则④ 显式关掉最后一列表头的右边线；数据行按规则⑤无边框；表格按规则①无外框 —— 三处都不画 |
| 下 | **无** | 规则⑤ 数据行无边框 + 规则① 表格无外框 —— 最后一行下面什么都不画 |

换句话说：**用户看到的框其实是表头格子的边框拼出来的半成品**，右和下那两段本来
该由「表格外框」补上，而外框默认是关的。

## 结论

**开启 qfw 自带的 `setBorderVisible(True)`，让规则②生效补齐整圈，并给表格加 8px 圆角
与默认视图的档位卡片保持一致。**

```python
# src/sookit/pages/youtube_page.py（表格构造处）
self.format_table.setBorderVisible(True)   # 命中 qfw 自带 QTableView[isBorderVisible=true] 规则
self.format_table.setBorderRadius(8)       # 与 OptionRow.RADIUS 一致
```

`setBorderRadius` 内部走 `setCustomStyleSheet(self, "QTableView{border-radius: 8px}")`，
追加在 qfw 样式之后，圆角只影响外框、不影响表头格子。

暗色主题无需额外处理：qfw 的样式模板会按主题把颜色换成 `rgba(255, 255, 255, 21)`
（实测 `FluentStyleSheet.TABLE_VIEW.content()` 在 `Theme.DARK` 下三处 `border` 全部变成白色 21），
`isBorderVisible` 是 dynamic property，主题切换时 qfw 的 `updateDynamicStyle` 会重算。

### 没采用的替代做法

| 做法 | 为什么不用 |
|---|---|
| 自己 `setStyleSheet` 加 `QTableView{border: 1px solid #e5e5e5}` | 硬编码颜色，暗色主题要再写一套；而且会和 qfw 自己的样式表打架（setStyleSheet 覆盖优先级难控） |
| 给数据行的 item delegate 画右边/下边线 | 只能补数据区那一截，和表头的 1px 线粗细、颜色对不齐，右下角接缝还是看得出来 |
| 只加 `border` 不加圆角 | 圆角是现代 UI 的默认语感，页面上档位卡片、滚动区都是圆角，直角框显得粗糙 |

## 改动文件

| 文件 | 改动 |
|---|---|
| `src/sookit/pages/youtube_page.py` | 表格构造处新增 `setBorderVisible(True)` + `setBorderRadius(8)`，附注释说明「默认只有表头格子的边线拼出上/左，右/下靠外框补」 |

## 实测效果（真实 FluentWindow，1131×731）

| | 上边 | 下边 | 左边 | 右边 |
|---|---|---|---|---|
| 改前 | 568px 连续 | **0px** | 378px 连续 | **0px** |
| 改后 | 589~591px 连续 | 586~588px 连续 | 404~407px 连续 | 402~406px 连续 |

- 四条边全部连续闭合，四角放大 5 倍肉眼确认无缺口，圆角处平滑。
- `viewport` 从 `565×375` 变成 `563×374`（宽 −2、高 −1，外框占位），
  列宽由 `_distribute_columns()` 按 viewport 重算自动吸收，**内容不溢出**。
- 表格宽 594、右缘仍贴窗口右缘 1131，表格滚动条距窗右 1（决策 54 的效果未受影响）。
- 两个下载按钮仍是 `240×32`；默认视图边距仍是 `(0, 24)`、左留白 15、与分段控件左对齐。
- 966/1005/1131/1280/1440 五档窗口全部成立。
- 左栏在 966 宽下仍是 412，`minimumSizeHint().width()` 未上升。

## 风险

1. **`viewport` 少 2px 宽、1px 高**：`_distribute_columns()` 用 `viewport().width()`
   算列宽，所以 5 列各窄零点几 px。实测 `w=966` 时 `COL_MIN=50` 兜底会把窄列抬高，
   列宽总和可能比 `viewport - RIGHT_RESERVE` 多 1~2px —— 只要 `sum(cols) <= viewport`
   就不影响，冒烟里按这个口径断言。
2. **`setBorderRadius` 的 QSS 是追加的**：如果以后调用别的 `setStyleSheet`/`setCustomStyleSheet`
   覆盖表格，圆角可能丢。改表格样式时留意别把这条冲掉。
3. **边框颜色不可单独调**：用的是 qfw 模板里的 `rgba(0,0,0,15)`（亮）/`rgba(255,255,255,21)`（暗）。
   想换颜色要自己追加一条带 `QTableView[isBorderVisible=true]` 选择器的 QSS，别直接改 `border`。

## 验证

1. `compileall` 通过。
2. 冒烟 31/31：`isBorderVisible == True`、QSS 含该规则与 `border-radius: 8px`、
   列宽不溢出 viewport、表格右缘仍贴窗、滚动条距窗右 ≤4、两按钮 240×32、
   默认视图边距与左对齐未变、清空/重新嗅探后边框仍在、966~1440 五档窗口全部成立、左栏未挤。
3. 像素扫描四边（见上表）+ 四角 5 倍放大目视，亮色/暗色主题各渲染一次。
4. 同进程内 `setBorderVisible(False)` 抓一张对照图，除四边外无其它像素差异。

## 相关决策

- 决策 47：默认视图档位卡片的圆角来自 `OptionRow.RADIUS = 8`，本文表格圆角与之对齐
- 决策 54：表格铺满到窗口右缘（本文只改边框，不改宽度/列宽口径）
