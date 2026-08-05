"""
widgets/cover_image.py
自绘封面控件
"""

from PyQt6.QtWidgets import QWidget, QSizePolicy
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPixmap, QPainter, QColor, QPainterPath
import qfluentwidgets as qfw


class CoverImageWidget(QWidget):
    """自绘封面控件，每次重绘时按当前尺寸稳定绘制，无黑边"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._placeholder = "嗅探后将显示封面"
        self.setMinimumSize(400, 225)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        qfw.qconfig.themeChangedFinished.connect(self.update)

    def resizeEvent(self, event):
        """窗口缩放时强制全量重绘，确保圆角不丢失"""
        super().resizeEvent(event)
        self.update()

    def setPixmap(self, pixmap):
        self._pixmap = pixmap
        self.update()

    def clearPixmap(self, placeholder="嗅探后将显示封面"):
        self._pixmap = QPixmap()
        self._placeholder = placeholder
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = self.rect()

        # 1. 画圆角背景（无裁剪，直接用 drawRoundedRect 保证外框四角圆润）
        painter.setPen(Qt.PenStyle.NoPen)
        bg = QColor("#272727") if qfw.isDarkTheme() else QColor("#F9F9F9")
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 16, 16)

        if self._pixmap.isNull():
            pen_color = QColor("#666") if qfw.isDarkTheme() else QColor("#aaa")
            painter.setPen(pen_color)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._placeholder)
            return

        # 2. 按当前容器尺寸缩放，保持宽高比
        scaled = self._pixmap.scaled(
            rect.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )
        x = (rect.width() - scaled.width()) // 2
        y = (rect.height() - scaled.height()) // 2

        # 3. 只对封面图自己的绘制区域做圆角裁剪
        painter.save()
        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(x, y, scaled.width(), scaled.height()), 16, 16)
        painter.setClipPath(clip_path)
        painter.drawPixmap(x, y, scaled)
        painter.restore()
