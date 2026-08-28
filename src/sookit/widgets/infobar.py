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
from PyQt6.QtCore import Qt
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


def _apply_wrap(bar: qfw.InfoBar, wrap_max_width: int):
    """对已创建的 InfoBar 应用换行：wordWrap + 限宽 + 补足高度。

    注意：wordWrap 后 QLabel 的 sizeHint 仍按单行计算，直接 adjustSize
    会截断换行文本，需用 heightForWidth 求真实高度后补足最小高度。
    """
    label = bar.contentLabel
    label.setWordWrap(True)
    label.setMaximumWidth(wrap_max_width)
    h = label.heightForWidth(wrap_max_width)
    if h and h > label.height():
        bar.setMinimumHeight(bar.height() + h - label.height())
    bar.adjustSize()
