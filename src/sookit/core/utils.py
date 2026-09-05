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


def set_smooth_scroll_params(scroll_area, duration: int = 250, fps: int = 120) -> None:
    """设置 qfw.ScrollArea 平滑滚动的动画时长与插值帧率。

    duration：每格滚轮动画放完的总时长（毫秒），越小越跟手（库默认 400 偏绵长）。
    fps：插值定时器频率，越高动画越细腻（库默认 60）。
    需同时覆盖普通屏(fixedStep)与 HiDPI(adaptive) 两个引擎——
    运行时按 宽度×DPR>2560 自动二选一。非 qfw.ScrollArea 静默跳过。
    """
    delegate = getattr(scroll_area, "scrollDelagate", None)
    if delegate is None:
        return
    for smooth in (delegate.verticalSmoothScroll, delegate.horizonSmoothScroll):
        for engine in (smooth.fixedStepScrollEngine, smooth.adaptiveScrollEngine):
            engine.duration = duration
            engine.fps = fps
