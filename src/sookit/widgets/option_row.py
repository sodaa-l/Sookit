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
    PADDING = 12
    CIRCLE_D = 18
    # 档位行的最小宽度（sizeHint 用）。**不设上限**：卡片宽度由
    # OptionFlowLayout 对半分给两列，一旦在这里封顶，宽窗口下多出来的宽度就会
    # 全部堆到右侧、左右留白不再相等（决策 51）。
    MAX_WIDTH = 16777215
    MIN_WIDTH = 320
    # 副标题字号候选（从大到小）。两列窄卡片里先丢编码、再压紧写法，仍放不下
    # 才降字号，而不是直接省略号——省略号会把文件大小截掉。
    SUBTITLE_PT_SIZES = (9, 8)
    # 副标题带编码版本的宽度门槛（文字区宽度）：够宽才显示编码。数值是实测——
    # 9pt 宽松写法带编码最宽约 186px（`vp9+opus · webm · 约 1008.0MB`），
    # 去掉编码后最宽只要 118px（`webm · 约 1008.0MB`）。
    # 门槛取 190 而非实测的 186，是留 4px 余量：卡在 186 时同一屏里最长的行
    # 只要差 1px 就会单独掉到「去编码」档，看起来像各行没对齐。
    SUBTITLE_FULL_MIN_WIDTH = 190

    def __init__(self, title, subtitle='', short_subtitle='', right_text='', parent=None):
        super().__init__(parent)
        self._title = title
        self._subtitle = subtitle
        # 去掉编码段（`vp9+opus`）的短版副标题，只留容器和大小；窄卡片时优先用它
        self._short_subtitle = short_subtitle or subtitle
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

    def sizeHint(self):
        """按内容算首选宽度。

        自绘控件默认 sizeHint 是无效值 (-1,-1)，父布局在
        `addWidget(row, alignment=AlignLeft)` 时会把它收缩成 0 宽度而看不见。
        这里按「标题/副标题较长者 + 右侧说明 + 单选圈 + 两侧留白」给出建议宽度。
        """
        from PyQt6.QtCore import QSize

        title_font = QFont("Microsoft YaHei", 11)
        title_font.setWeight(QFont.Weight.DemiBold)
        sub_font = QFont("Microsoft YaHei", 9)
        text_w = max(QFontMetrics(title_font).horizontalAdvance(self._title),
                     QFontMetrics(sub_font).horizontalAdvance(self._subtitle))
        right_w = 0
        if self._right:
            right_w = QFontMetrics(sub_font).horizontalAdvance(self._right) + 10
        width = self.PADDING * 2 + text_w + 24 + right_w + self.CIRCLE_D
        return QSize(max(min(width, self.MAX_WIDTH), self.MIN_WIDTH), self.ROW_HEIGHT)

    def _fit_subtitle(self, text_w):
        """按可用宽度选副标题的「写法 + 字号」，返回 (文字, 字体, 度量)。

        宽度不够时**第一步是丢掉编码段**（`vp9+opus`），只留容器和大小，
        分隔符照旧留空格——这是用户实测后的要求：窗口窄下去时宁可少显示一项
        信息，也不要把整行文字挤成一团。全部候选仍放不下才压紧分隔符、再降字号。

        起点写法只看 `text_w`、不看本行内容，保证同网格里各行写法一致；
        个别副标题特别长时再往更紧的候选走一步兜底。
        `约` 与数字之间的空格始终保留（更好读），只有极窄时才退到省「约」。

        **不要直接交给 elidedText**：省略号截掉的正好是文件大小，而那正是
        选档位时最想看的数字。
        """
        if not self._subtitle:
            font = QFont("Microsoft YaHei", self.SUBTITLE_PT_SIZES[0])
            return '', font, QFontMetrics(font)
        candidates = self._subtitle_candidates()
        for text, size in candidates[self._subtitle_level(text_w):]:
            font = QFont("Microsoft YaHei", size)
            fm = QFontMetrics(font)
            if fm.horizontalAdvance(text) <= text_w:
                return text, font, fm
        text, size = candidates[-1]
        font = QFont("Microsoft YaHei", size)
        return text, font, QFontMetrics(font)

    def _subtitle_level(self, text_w):
        """按 text_w 落在哪一档，返回起点写法下标

        门槛是「该写法最宽用例能放下」的实测宽度：带编码的 9pt 宽松写法最宽
        186px（门槛取 190，留 4px 余量防同屏掉档）；放不下就跳到「去编码版」
        起点，而不是先压紧分隔符。
        """
        return 0 if text_w >= self.SUBTITLE_FULL_MIN_WIDTH else 1

    def _subtitle_candidates(self):
        """副标题候选写法，由宽到紧：[(文字, 字号), ...]

        第 1 项带编码，后 4 项都把编码段去掉（只留容器 + 大小）；第 2 项是
        起点档的正常写法，后 3 项只在某一行的文字仍放不下时才用到。
        """
        full = self._subtitle
        short = self._short_subtitle
        compact = short.replace(' · ', '·')
        tight = compact.replace('约 ', '').replace('约', '')
        big, small = self.SUBTITLE_PT_SIZES[0], self.SUBTITLE_PT_SIZES[-1]
        return [(full, big), (short, big), (compact, big), (compact, small), (tight, small)]

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
        sub_text, sub_font, sub_fm = self._fit_subtitle(text_w)

        painter.setFont(title_font)
        painter.setPen(accent if self._selected else (QColor(240, 240, 240) if dark else QColor(24, 24, 24)))
        title_text = title_fm.elidedText(self._title, Qt.TextElideMode.ElideRight, text_w)
        if sub_text:
            total_h = title_fm.height() + 2 + sub_fm.height()
            top = rect.center().y() - total_h / 2
            painter.drawText(QRectF(text_left, top, text_w, title_fm.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title_text)
            painter.setFont(sub_font)
            painter.setPen(QColor(150, 150, 150) if dark else QColor(110, 110, 110))
            # _fit_subtitle 已按宽度挑过写法，正常情况下到这里不该再被截
            painter.drawText(QRectF(text_left, top + title_fm.height() + 2, text_w, sub_fm.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                             sub_fm.elidedText(sub_text, Qt.TextElideMode.ElideRight, text_w))
        else:
            painter.drawText(QRectF(text_left, rect.top(), text_w, rect.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title_text)
