"""
widgets/infobar.py
InfoBar 统一入口：长文案自动换行限宽，短文案保持原生行为。

背景：qfluentwidgets InfoBar 内置换行（_adjustText）按"父窗口宽/9"的字符数
硬换行（上限 120 字符），按字符数而非显示宽度计算；中文字符显示宽度约为
ASCII 的两倍，长中文文案实际不会被换行，导致单行撑爆（实测比 1131px 窗口
还宽）。本模块在创建后用真实渲染宽度判断，超阈值时重建为竖排换行版
（QLabel 原生 wordWrap 按像素宽换行 + 限宽 + heightForWidth 补高度）；
未超阈值时与原生 InfoBar 行为完全一致。

**title 与 content 一起判定**（2026-10-07）：此前只判 content，长标题（如任务
标题 = 整条推文文本）会走原生单行版，实测条宽 1539px 超过页面 1082px，横跨
页面顶部遮住 segment；现在任一超阈值都走竖排版，两个 label 各自折行，且因竖排
layout 天然上下排列，title 与 content 不会再挤在同一行。

项目约定：新增 InfoBar 提示一律使用 show_infobar，不要直接调 qfw.InfoBar.*。
"""
import re

from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation
from PyQt6.QtWidgets import QLabel
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
    if not _needs_wrap(bar, title, content, wrap_max_width):
        return bar  # 短文案：原生行为即可

    # 长文案：同调用栈内 close + 重建（无中间绘制，不会闪烁）
    bar.close()
    bar.deleteLater()
    bar = factory(parent=parent, title=title, content=content,
                  orient=Qt.Orientation.Vertical, isClosable=closable,
                  duration=duration)
    _apply_wrap(bar, wrap_max_width)
    return bar


def _needs_wrap(bar: qfw.InfoBar, title: str, content: str,
                wrap_max_width: int) -> bool:
    """title 或 content 任一超出阈值（或含显式换行）→ 需要竖排换行版。

    三个必须在竖排下才能解决的问题：
    1. 库的 `_adjustText` 按「父宽/9（上限 120）」的**字符数**硬换行，中文字符
       显示宽度约 ASCII 的两倍，长中文实际不会被折行；
    2. **title 与 content 都受影响**——此前只判 content，导致长标题（如任务标题
       = 整条推文文本）被当成短文案走原生单行版，实测条宽 1539px 超过页面
       1082px，横跨页面顶部遮住 segment；
    3. Horizontal 下 title/content 挤在同一行，标题一长就把内容顶到屏幕外。
    竖排（Vertical）下两者上下排列，各自按像素宽折行。
    """
    title = title or ""
    content = content or ""
    if "\n" in title or "\n" in content:
        return True
    return (bar.titleLabel.fontMetrics().horizontalAdvance(title) > wrap_max_width
            or bar.contentLabel.fontMetrics().horizontalAdvance(content) > wrap_max_width)


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
    """对竖排 InfoBar 的 title / content 都应用标点优先换行 + 限宽。

    - 关闭 wordWrap（文本已含 \\n，避免 Qt 按任意字符二次断行拆词）；
    - 同步覆盖 bar.title / bar.content：窗口 resize 时库的 _adjustText 会用
      TextWrap 重排这两个字段（按字符数，中文失效），覆盖后重排基于换行文本；
    - label 宽度钉死为「实际换行后最长行」的精确宽度（而非换行上限），
      避免条右侧出现大段留白；高度按行数精确计算。
    """
    if bar.title:
        bar.title = _wrap_label(bar.titleLabel, bar.title, wrap_max_width)
    if bar.content:
        bar.content = _wrap_label(bar.contentLabel, bar.content, wrap_max_width)
    bar.adjustSize()
    # 库布局的 sizeHint 在 QSS 字体 polish/首帧布局完成前会偏大（实测 679 → 557），
    # _apply_wrap 内的 adjustSize 用的是过早的值——事件循环后（sizeHint 收缩）再
    # 收缩一次，消除条右侧的额外留白。
    QTimer.singleShot(0, lambda: _settle(bar))
    QTimer.singleShot(100, lambda: _settle(bar))


def _wrap_label(label: QLabel, text: str, wrap_max_width: int) -> str:
    """把 text 按标点优先换行写入 label（限宽 + 补高），返回换行后的文本。"""
    fm = label.fontMetrics()
    wrapped = _wrap_by_punctuation(text.replace("\n", ""), wrap_max_width, fm)
    label.setWordWrap(False)
    label.setText(wrapped)
    lines = wrapped.split("\n")
    w_max = max(fm.horizontalAdvance(line) for line in lines) + 4  # 余量防末字符被裁
    label.setFixedWidth(w_max)
    rect = fm.boundingRect(
        0, 0, w_max + 10, 10000, Qt.TextFlag.TextWordWrap, wrapped)
    label.setMinimumHeight(rect.height())
    return wrapped


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
