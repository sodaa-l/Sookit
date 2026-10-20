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


# 滚动条手柄颜色（亮色主题, 暗色主题），决策 48
SCROLLBAR_HANDLE_FAINT = ((0, 0, 0, 55), (255, 255, 255, 70))     # 常显：极淡
SCROLLBAR_HANDLE_HOVER = ((0, 0, 0, 114), (255, 255, 255, 139))   # 悬停：恢复 qfw 原生浓度


def apply_global_scrollbar_style() -> None:
    """全局下调滚动条手柄的常显浓度，鼠标悬停到滚动条时恢复原生浓度（决策 48）。

    qfw 的 ScrollBarHandle 把颜色写死在 __init__ 里（亮 0,0,0,114 / 暗 255,255,255,139），
    也没有任何全局配置项，因此只能在类上打补丁：

    - ``__init__``：新建手柄的初始色改为极淡，让常显的滚动条不再抢视线；
    - ``expand`` / ``collapse``：鼠标移进右侧 12px 滚动条区域时切回原生浓度，
      移出再变淡。qfw 自身在这两个时机已有「手柄 3px→6px 加粗 + 轨道淡入」的动画，
      颜色随之切换不会显得突兀。

    与其他滚动条定制保持一致：只改手柄颜色，不动轨道（ScrollBarGroove）与
    箭头（ArrowButton）——轨道本来就只在悬停时才淡入，不算「常显」。
    必须在创建 QApplication 之后、任何控件之前调用，否则已经建好的手柄不会被改；
    重复调用会直接返回。
    """
    from PyQt6.QtGui import QColor
    from qfluentwidgets.components.widgets.scroll_bar import (
        ScrollBar, ScrollBarHandle,
    )

    if getattr(ScrollBarHandle, '_sookit_scrollbar_patched', False):
        return

    faint_light, faint_dark = (QColor(*c) for c in SCROLLBAR_HANDLE_FAINT)
    hover_light, hover_dark = (QColor(*c) for c in SCROLLBAR_HANDLE_HOVER)

    def _set_color(handle, light, dark) -> None:
        handle.lightColor = QColor(light)
        handle.darkColor = QColor(dark)
        handle.update()

    _original_init = ScrollBarHandle.__init__

    def _patched_init(self, orient, parent=None):
        _original_init(self, orient, parent)
        _set_color(self, faint_light, faint_dark)

    _original_expand = ScrollBar.expand

    def _patched_expand(self):
        result = _original_expand(self)
        # 原方法在「已展开」或「鼠标没进来」时会早退，此时 _isExpanded 才是真实结果
        if self._isExpanded:
            _set_color(self.handle, hover_light, hover_dark)
        return result

    _original_collapse = ScrollBar.collapse

    def _patched_collapse(self):
        result = _original_collapse(self)
        if not self._isExpanded:
            _set_color(self.handle, faint_light, faint_dark)
        return result

    ScrollBarHandle.__init__ = _patched_init
    ScrollBar.expand = _patched_expand
    ScrollBar.collapse = _patched_collapse
    ScrollBarHandle._sookit_scrollbar_patched = True


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
