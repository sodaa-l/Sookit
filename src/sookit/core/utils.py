"""
core/utils.py
共享工具函数
"""

import ssl


def get_certifi_ssl_context():
    """返回加载了 certifi 证书包的 SSL context。

    解决 PyInstaller 打包态下 Python 默认证书路径
    （C:\\Program Files\\Common Files\\SSL\\...）不存在导致 HTTPS 验证失败的问题。
    所有 urllib 发起的 HTTPS 请求都应使用此 context。
    """
    ctx = ssl.create_default_context()
    try:
        import certifi
        ctx.load_verify_locations(certifi.where())
    except ImportError:
        pass
    return ctx


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
