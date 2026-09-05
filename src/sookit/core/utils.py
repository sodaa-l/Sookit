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
