# coding:utf-8
"""
widgets/option_grid.py
档位卡片的自适应列布局（设计决策 47 补充）。

默认视图的档位卡片按「一排放两个」平铺：单列时右面板右侧会留出大片空白，
两列能把这块空间用起来；窗口窄到放不下两列时自动退回单列。

不直接用 `qfw.AdaptiveFlowLayout`：它的列数只由固定的 `widgetMinimumWidth`
决定，无法表达「最多两列」——宽窗口下会排成三列、四列。这里按可用宽度直接
算列数并夹在 1~2，再反算出父类算法需要的 minWidth。
"""
from PyQt6.QtCore import QRect

from qfluentwidgets.components.layout import AdaptiveFlowLayout


class OptionFlowLayout(AdaptiveFlowLayout):
    """最多两列的流式布局：放得下就两列，窄了自动退回单列。"""

    MAX_COLUMNS = 2

    def __init__(self, min_card_width=215, parent=None):
        super().__init__(parent)
        # 单列时的最小卡片宽：再窄副标题就省略得没法看了
        self._minCardWidth = min_card_width
        self.setContentsMargins(0, 0, 0, 0)

    def columns_for_width(self, width):
        """按容器可用宽度算列数（1 或 2）"""
        margin = self.contentsMargins()
        available = width - margin.left() - margin.right()
        space = self.horizontalSpacing()
        if available < self._minCardWidth * self.MAX_COLUMNS + space:
            return 1
        return self.MAX_COLUMNS

    def _doLayout(self, rect: QRect, move: bool):
        # 把列数上限喂给父类算法：父类用
        #   cardsPerRow = (available + space) // (minWidth + space)
        # 反算出一个恰好只允许 columns 列的 minWidth。
        columns = self.columns_for_width(rect.width())
        space = self.horizontalSpacing()
        margin = self.contentsMargins()
        available = rect.width() - margin.left() - margin.right()
        if columns <= 1:
            min_width = max(self._minCardWidth, available)
        else:
            min_width = max(self._minCardWidth,
                            (available - (columns - 1) * space) // columns)
        self.setWidgetMinimumWidth(min_width)
        return super()._doLayout(rect, move)
