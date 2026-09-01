"""
widgets/elided_label.py
多行省略标签 —— YouTube 风格：最多显示 max_lines 行，超出部分截断并在末行末尾加 …

实现说明：
- Qt 的 QFontMetrics.elidedText 只支持单行省略，无现成多行省略方案；
- 本组件用 QTextLayout 按宽度做与 QLabel(wordWrap) 相同的贪心换行（WordWrap 模式），
  取前 max_lines 行；若文本未排完，对末行起始处的剩余文本用 fm.elidedText 生成 xxx…，
  与前面各行之间插入显式换行符，保证省略号固定落在末行行尾；
- 标签高度按实际换行行数自适应（单行标题只占一行，最多 max_lines 行），
  顶部对齐，使父布局高度完全确定，避免 wordWrap QLabel 高度可变导致的挤压/叠压问题；
- 偏移量换算：QTextLine.textStart() 是 UTF-16 偏移，Python str 是码点偏移，
  标题含 emoji（增补平面字符）时两者不一致，_u16_to_py 负责换算。
"""

from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QFontMetrics, QTextLayout, QTextOption
from PyQt6.QtWidgets import QLabel


def _u16_len(text: str) -> int:
    """Python str 的 UTF-16 单元长度"""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text)


def _u16_to_py(text: str, u16_pos: int) -> int:
    """将 QString(UTF-16) 偏移转换为 Python str 码点偏移"""
    if u16_pos <= 0:
        return 0
    count = 0
    for i, ch in enumerate(text):
        if count >= u16_pos:
            return i
        count += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def elide_multiline(text: str, font, width: int, max_lines: int = 2) -> str:
    """按宽度换行排版，超过 max_lines 行时截断并在末行末尾加 …

    换行模式与 QLabel.setWordWrap(True) 一致（WordWrap，词边界优先），
    保证此处计算的换行位置与 QLabel 实际渲染一致，省略号不会落到额外行上。
    """
    if not text or width <= 0:
        return text
    fm = QFontMetrics(font)
    # 快速路径：单行放得下且无显式换行
    if "\n" not in text and fm.horizontalAdvance(text) <= width:
        return text

    layout = QTextLayout(text, font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WordWrap)
    layout.setTextOption(opt)

    layout.beginLayout()
    lines = []
    for _ in range(max(max_lines, 1)):
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        lines.append(line)
    layout.endLayout()

    if not lines:
        return text
    last = lines[-1]
    if last.textStart() + last.textLength() >= _u16_len(text):
        return text  # max_lines 行内放得下，无需省略

    py_start = _u16_to_py(text, last.textStart())
    head = text[:py_start].rstrip()
    tail = text[py_start:]
    elided_tail = fm.elidedText(tail, Qt.TextElideMode.ElideRight, width)
    return head + "\n" + elided_tail if head else elided_tail


def count_lines(text: str, font, width: int, max_lines: int = 0) -> int:
    """文本在给定宽度下排版所需行数（max_lines>0 时封顶）"""
    if not text or width <= 0:
        return 1
    layout = QTextLayout(text, font)
    opt = QTextOption()
    opt.setWrapMode(QTextOption.WrapMode.WordWrap)
    layout.setTextOption(opt)
    layout.beginLayout()
    n = 0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        n += 1
        if max_lines and n >= max_lines:
            break
    layout.endLayout()
    return max(1, n)


class ElidedLabel(QLabel):
    """最多显示 max_lines 行、末行尾部 … 截断的标签

    - 高度按实际换行行数自适应：单行标题只占一行，最多 max_lines 行；
    - 宽度、文本、字体变化时自动重新计算省略与高度；
    - text() 返回完整原文（截断只影响显示，不丢数据）。
    """

    def __init__(self, text: str = "", max_lines: int = 2, parent=None,
                 font=None):
        super().__init__(parent)
        self._max_lines = max(1, int(max_lines))
        self._full_text = text or ""
        if font is not None:
            super().setFont(font)   # 注意：setFont 非虚函数，Python 重写不会被外部调用
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self._apply_elision()

    # ---- 公共 API ----

    def fixedHeight(self) -> int:
        """当前宽度/文本/字体下标签的固定高度（父布局高度预算使用此值）"""
        fm = QFontMetrics(self.font())
        return self._n * fm.height()

    def setText(self, text: str):
        self._full_text = text or ""
        self._apply_elision()

    def text(self) -> str:
        return self._full_text

    def setMaxLines(self, max_lines: int):
        self._max_lines = max(1, int(max_lines))
        self._apply_elision()

    # ---- 内部 ----

    def _apply_elision(self):
        m = self.contentsMargins()
        w = self.width() - m.left() - m.right()
        fm = QFontMetrics(self.font())
        self._n = count_lines(self._full_text, self.font(), w, self._max_lines)
        new_h = self._n * fm.height()
        if self.minimumHeight() != new_h or self.maximumHeight() != new_h:
            self.setFixedHeight(new_h)
        super().setText(elide_multiline(self._full_text, self.font(),
                                        w, self._max_lines))

    # ---- 事件 ----

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._apply_elision()

    def changeEvent(self, e):
        if e.type() == QEvent.Type.FontChange:
            self._apply_elision()
        super().changeEvent(e)
