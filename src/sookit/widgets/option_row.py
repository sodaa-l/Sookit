# coding:utf-8
"""
widgets/option_row.py
单选档位行：圆角卡片 + 左侧标题/副标题 + 右侧说明与单选圈，整行可点。

供嗅探页「默认视图」按分辨率/音质平铺选项使用（设计决策 47）。与表格视图的
多选表格刻意区分：无表头、无网格线、无复选框，语义是「只选一个、点一次就下载」。
自绘而非拼 qfw 控件：需要单选圈 + 两行文本 + 右侧弱化说明的紧凑排版。
"""
from PyQt6.QtCore import Qt, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

import qfluentwidgets as qfw


class OptionRow(QWidget):
    """可选档位行（单选语义由容器负责互斥）"""

    clicked = pyqtSignal()

    ROW_HEIGHT = 68
    RADIUS = 8
    PADDING = 14
    CIRCLE_D = 18

    def __init__(self, title, subtitle='', right_text='', parent=None):
        super().__init__(parent)
        self._title = title
        self._subtitle = subtitle
        self._right = right_text
        self._selected = False
        self._hover = False
        self.setFixedHeight(self.ROW_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # 需要 enter/leaveEvent 才能在未按下时收到 hover 通知（自绘状态）
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        # 主题切换重绘（配色读 qfw.isDarkTheme()/themeColor()）
        qfw.qconfig.themeChangedFinished.connect(self.update)

    # ---- 状态 ----
    def is_selected(self):
        return self._selected

    def set_selected(self, selected):
        selected = bool(selected)
        if self._selected == selected:
            return
        self._selected = selected
        self.update()

    # ---- 交互 ----
    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    # ---- 绘制 ----
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        dark = qfw.isDarkTheme()
        accent = QColor(qfw.themeColor())
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        if self._selected:
            bg = QColor(accent)
            bg.setAlpha(38 if dark else 28)
            painter.setBrush(bg)
            painter.setPen(QPen(accent, 1.4))
        else:
            painter.setBrush(QColor(255, 255, 255, 46 if self._hover else 18) if dark
                             else QColor(255, 255, 255, 235 if self._hover else 190))
            painter.setPen(QPen(QColor(255, 255, 255, 24) if dark else QColor(0, 0, 0, 16), 1))
        painter.drawRoundedRect(rect, self.RADIUS, self.RADIUS)

        pad = self.PADDING
        right_font = QFont("Microsoft YaHei", 9)
        right_fm = QFontMetrics(right_font)
        right_w = 0
        if self._right:
            right_w = right_fm.horizontalAdvance(self._right)
            painter.setFont(right_font)
            painter.setPen(QColor(140, 140, 140) if dark else QColor(120, 120, 120))
            painter.drawText(
                QRectF(rect.right() - pad - right_w, rect.top(), right_w, rect.height()),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._right)

        # 单选圈（选中=实心+对勾，未选=空心）
        circle_d = self.CIRCLE_D
        cx = rect.right() - pad - right_w - (10 if right_w else 0) - circle_d
        cy = rect.center().y() - circle_d / 2
        if self._selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(QRectF(cx, cy, circle_d, circle_d))
            painter.setPen(QPen(QColor("white"), 1.8))
            painter.drawLine(int(cx + 4), int(cy + 9), int(cx + 7), int(cy + 13))
            painter.drawLine(int(cx + 7), int(cy + 13), int(cx + 14), int(cy + 5))
        else:
            painter.setPen(QPen(QColor(150, 150, 150) if dark else QColor(160, 160, 160), 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(cx, cy, circle_d, circle_d))

        # 文本区：标题 + 可选副标题（超宽省略）
        text_left = rect.left() + pad
        text_right = cx - 12
        text_w = max(int(text_right - text_left), 20)

        title_font = QFont("Microsoft YaHei", 11)
        title_font.setWeight(QFont.Weight.DemiBold)
        title_fm = QFontMetrics(title_font)
        sub_font = QFont("Microsoft YaHei", 9)
        sub_fm = QFontMetrics(sub_font)

        painter.setFont(title_font)
        painter.setPen(accent if self._selected else (QColor(240, 240, 240) if dark else QColor(24, 24, 24)))
        title_text = title_fm.elidedText(self._title, Qt.TextElideMode.ElideRight, text_w)
        if self._subtitle:
            total_h = title_fm.height() + 2 + sub_fm.height()
            top = rect.center().y() - total_h / 2
            painter.drawText(QRectF(text_left, top, text_w, title_fm.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title_text)
            painter.setFont(sub_font)
            painter.setPen(QColor(150, 150, 150) if dark else QColor(110, 110, 110))
            sub_text = sub_fm.elidedText(self._subtitle, Qt.TextElideMode.ElideRight, text_w)
            painter.drawText(QRectF(text_left, top + title_fm.height() + 2, text_w, sub_fm.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, sub_text)
        else:
            painter.drawText(QRectF(text_left, rect.top(), text_w, rect.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title_text)
