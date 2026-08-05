"""
core/utils.py
共享工具函数
"""


def get_scrollbar_style(is_dark: bool) -> str:
    """获取滚动条样式"""
    if is_dark:
        handle_color = "rgba(255, 255, 255, 0.3)"
        handle_hover = "rgba(255, 255, 255, 0.5)"
    else:
        handle_color = "rgba(0, 0, 0, 0.2)"
        handle_hover = "rgba(0, 0, 0, 0.35)"
    return f"""
        QScrollArea {{ background: transparent; }}
        QScrollBar:vertical {{
            background: transparent;
            width: 6px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: {handle_color};
            min-height: 30px;
            border-radius: 3px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {handle_hover};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
    """
