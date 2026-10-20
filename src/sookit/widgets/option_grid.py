# coding:utf-8
"""
widgets/option_grid.py
档位卡片的双列布局（设计决策 47）。

默认视图的档位卡片按「一排放两个」平铺：单列时右面板右侧会留出大片空白。
窗口再窄也**不退回单列**——窗口本身有最小宽度，实际根本窄不到「一列才放得下」
的程度，设个单列回退反而永远不生效、白增分支。窄下去时由卡片自己丢信息
（`OptionRow` 按宽度把副标题里的编码段去掉），保住容器和大小。

不直接用 `qfw.AdaptiveFlowLayout`：它的列数只由固定的 `widgetMinimumWidth`
决定，无法表达「固定两列」——宽窗口下会排成三列、四列。这里按可用宽度反算出
恰好只允许两列的 minWidth 再交给父类算法。
"""
from PyQt6.QtCore import QRect

from qfluentwidgets.components.layout import AdaptiveFlowLayout


class OptionFlowLayout(AdaptiveFlowLayout):
    """固定两列的流式布局：卡片宽度由可用宽度对半分，窗口再窄也不变一列。"""

    MAX_COLUMNS = 2

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)

    def _doLayout(self, rect: QRect, move: bool):
        # 父类算法是 cardsPerRow = (available + space) // (minWidth + space)。
        # 把 minWidth 设成「两列刚好放得下」的值，列数就被钉死在 2 列：卡片
        # 跟着窗口一起变窄，而不是掉回单列（决策 47）。
        space = self.horizontalSpacing()
        margin = self.contentsMargins()
        available = max(rect.width() - margin.left() - margin.right(), 1)
        min_width = max(
            (available - (self.MAX_COLUMNS - 1) * space) // self.MAX_COLUMNS, 1)
        self.setWidgetMinimumWidth(min_width)
        return super()._doLayout(rect, move)
