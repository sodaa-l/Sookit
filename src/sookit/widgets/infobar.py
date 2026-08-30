"""
widgets/infobar.py
InfoBar 统一入口：长文案自动换行限宽，短文案保持原生行为。

背景：qfluentwidgets InfoBar 内置换行（_adjustText）按"父窗口宽/9"的字符数
硬换行（上限 120 字符），按字符数而非显示宽度计算；中文字符显示宽度约为
ASCII 的两倍，长中文文案实际不会被换行，导致单行撑爆（实测比 1131px 窗口
还宽）。本模块在创建后用真实渲染宽度判断，超阈值时重建为竖排换行版
（QLabel 原生 wordWrap 按像素宽换行 + 限宽 + heightForWidth 补高度）；
未超阈值时与原生 InfoBar 行为完全一致。

项目约定：新增 InfoBar 提示一律使用 show_infobar，不要直接调 qfw.InfoBar.*。
"""
import re

from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation
import qfluentwidgets as qfw

#: 内容渲染宽度超过该值（px）时自动换行；换行后内容区也限宽到该值
WRAP_THRESHOLD = 560


def show_infobar(parent, severity: str, title: str, content: str,
                 duration: int = -1, closable: bool = True,
                 wrap_max_width: int = WRAP_THRESHOLD) -> qfw.InfoBar:
    """创建 InfoBar 的统一入口，返回实例（可继续 addWidget 添加自定义控件）。

    content 实际渲染宽度超过 wrap_max_width 时自动转为竖排换行版
    （wordWrap 按像素宽换行 + 限宽 + 补足高度），否则与原生 InfoBar 一致。

    severity: 'error' / 'warning' / 'info' / 'success'
    """
    factory = getattr(qfw.InfoBar, severity)
    bar = factory(parent=parent, title=title, content=content,
                  orient=Qt.Orientation.Horizontal, isClosable=closable,
                  duration=duration)
    # 含 \n 的多行内容强制走换行分支：Horizontal 下多行长行 + addWidget 按钮
    # 会横向撑宽溢出窗口；竖排下按钮排在内容下方，长行按宽度折行。
    if "\n" not in content and bar.contentLabel.sizeHint().width() <= wrap_max_width:
        return bar  # 短文案：原生行为即可

    # 长文案：同调用栈内 close + 重建（无中间绘制，不会闪烁）
    bar.close()
    bar.deleteLater()
    bar = factory(parent=parent, title=title, content=content,
                  orient=Qt.Orientation.Vertical, isClosable=closable,
                  duration=duration)
    _apply_wrap(bar, wrap_max_width)
    return bar


_PUNCT_RE = re.compile(r"(?<=[，。；：！？、])")


def _wrap_by_punctuation(text: str, max_width: int, fm) -> str:
    """标点优先贪心换行：标点（，。；：！？、）后为首选断点，行宽不足才断；
    无标点长段（如 URL）二分硬断。返回含 \\n 的文本。"""
    lines, buf = [], ""
    for seg in _PUNCT_RE.split(text):
        if not seg:
            continue
        if fm.horizontalAdvance(buf + seg) <= max_width:
            buf += seg  # 装得下 → 继续累加（标点只是候选断点，非强制）
            continue
        if buf:
            lines.append(buf)
        while fm.horizontalAdvance(seg) > max_width:
            # 单段超宽（如长 URL）：二分找最大可容纳前缀硬断
            lo, hi = 1, len(seg)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if fm.horizontalAdvance(seg[:mid]) <= max_width:
                    lo = mid
                else:
                    hi = mid - 1
            lines.append(seg[:lo])
            seg = seg[lo:]
        buf = seg
    if buf:
        lines.append(buf)
    return "\n".join(lines)


def _apply_wrap(bar: qfw.InfoBar, wrap_max_width: int):
    """对已创建的 InfoBar 应用标点优先换行。

    - 关闭 wordWrap（文本已含 \\n，避免 Qt 按任意字符二次断行拆词）；
    - 同步覆盖 bar.content：窗口 resize 时库的 _adjustText 会用 TextWrap 重排
      self.content，TextWrap 对含 \\n 文本逐行处理且不破坏已有换行（行宽均
      < 120 显示宽），因此重排后标点断行保持稳定；
    - label 高度用字体度量按换行后文本精确计算。
    """
    label = bar.contentLabel
    fm = label.fontMetrics()
    wrapped = _wrap_by_punctuation(label.text().replace("\n", ""), wrap_max_width, fm)
    bar.content = wrapped  # 让 resize 时的 _adjustText 重排基于换行后文本
    label.setWordWrap(False)
    label.setText(wrapped)
    # label 宽度钉死为「实际换行后最长行」的精确宽度（而非换行上限），
    # 避免条右侧出现大段留白；高度按行数精确计算。
    lines = wrapped.split("\n")
    w_max = max(fm.horizontalAdvance(line) for line in lines) + 4  # 余量防末字符被裁
    label.setFixedWidth(w_max)
    rect = fm.boundingRect(
        0, 0, w_max + 10, 10000, Qt.TextFlag.TextWordWrap, wrapped)
    label.setMinimumHeight(rect.height())
    bar.adjustSize()
    # 库布局的 sizeHint 在 QSS 字体 polish/首帧布局完成前会偏大（实测 679 → 557），
    # _apply_wrap 内的 adjustSize 用的是过早的值——事件循环后（sizeHint 收缩）再
    # 收缩一次，消除条右侧的额外留白。
    QTimer.singleShot(0, lambda: _settle(bar))
    QTimer.singleShot(100, lambda: _settle(bar))


def _settle(bar: qfw.InfoBar):
    """延迟收缩宽度后同步重算位置。

    manager 在 show 瞬间按当时偏大的宽度算好 x（parentW - barW - margin），
    之后宽度收缩不会触发重定位（库只在父窗口 resize/其他条关闭时重算），
    导致右侧出现「宽度收缩量 + margin」的大段空隙，故收缩后需手动重算。
    """
    bar.adjustSize()
    if not bar.parent():
        return
    try:
        pos = qfw.InfoBarManager.make(bar.position)._pos(bar)
    except (ValueError, RuntimeError):
        return  # 已从 manager 移除或已销毁
    ani = bar.property('slideAni')
    if ani and ani.state() == QPropertyAnimation.State.Running:
        ani.setEndValue(pos)  # 滑入动画（200ms）未结束，直接 move 会被逐帧覆盖
    else:
        bar.move(pos)
