# coding:utf-8
"""
widgets/combo_box.py
静态化下拉框：仅 1 项时为纯静态显示（点击不弹出菜单、不绘制下拉箭头）。
"""
from PyQt6.QtWidgets import QPushButton

from qfluentwidgets import ComboBox


class StaticComboBox(ComboBox):
    """仅 1 项时为静态显示的 ComboBox。

    - count <= 1：点击不弹出下拉菜单（吞掉 mouseReleaseEvent），paintEvent
      跳过父类末尾的下拉箭头绘制 → 外观与行为均为纯静态显示，暗示"无需选择"
    - count >= 2：与原生 qfluentwidgets.ComboBox 行为完全一致（弹菜单、选择、
      箭头、动画）

    说明：
    - count() 每次事件时动态判断，档位数变化（如嗅探不同站点 1↔5 项）无需手动刷新
    - 按压/hover 透明度状态由 ComboBoxBase.eventFilter 在事件进入
      mouseReleaseEvent 之前维护，此处吞事件不会卡住按压视觉状态
    """

    def mouseReleaseEvent(self, e):
        if self.count() <= 1:
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        if self.count() <= 1:
            # 只画按钮本体，跳过 ComboBox.paintEvent 末尾的 FIF.ARROW_DOWN 箭头
            QPushButton.paintEvent(self, e)
            return
        super().paintEvent(e)
