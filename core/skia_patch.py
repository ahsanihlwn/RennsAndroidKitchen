# SPDX-License-Identifier: MPL-2.0
"""Cache small Skia surface corners/edges; compose resized surfaces without readback."""
import math
from collections import OrderedDict
from PySide6.QtCore import QSizeF, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPen
from core.skia_gpu import render_gpu

_PATCHES = OrderedDict()


class SurfacePatch:
    def __init__(self, shape, ratio, *, end_phase=(0, 0), center_pixels=2):
        self.shape = shape
        self.cap = math.ceil((shape.radius + max(1, shape.width) + 1)*ratio) / ratio
        key = (shape.background.rgba(), shape.border.rgba(), shape.width, shape.radius,
               ratio, end_phase, center_pixels)
        cached = _PATCHES.pop(key, None)
        if cached is not None:
            self.image = cached
            _PATCHES[key] = cached
            return
        # Match the final edge's subpixel position at fractional display scales.
        # Its ceil-sized pixel buffer can then be copied on the pixel grid,
        # without resampling the already antialiased corners.
        width = self.cap*2 + (center_pixels + end_phase[0])/ratio
        height = self.cap*2 + (center_pixels + end_phase[1])/ratio
        rect = QRectF(0, 0, width, height)
        self.image = render_gpu(QSizeF(width, height), ratio, lambda canvas, bridge: shape.draw(canvas, bridge, rect))
        if self.image is None:
            self.image = QImage(math.ceil(width*ratio), math.ceil(height*ratio), QImage.Format_ARGB32_Premultiplied)
            self.image.setDevicePixelRatio(ratio)
            self.image.fill(Qt.transparent)
            painter = QPainter(self.image)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setBrush(shape.background)
            painter.setPen(QPen(shape.border, shape.width) if shape.width else Qt.NoPen)
            inset = shape.width/2
            painter.drawRoundedRect(rect.adjusted(inset, inset, -inset, -inset), shape.radius, shape.radius)
            painter.end()
        else:
            self.image = self.image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        _PATCHES[key] = self.image
        while len(_PATCHES) > 128 or sum(image.sizeInBytes() for image in _PATCHES.values()) > 2*1024*1024:
            _PATCHES.popitem(last=False)

    def draw(self, painter, rect, alpha=1):
        if alpha <= 0 or rect.isEmpty():
            return
        ratio = self.image.devicePixelRatioF()
        cap = min(self.cap, rect.width()/2, rect.height()/2)
        source_cap = self.cap*ratio
        sx = (0, source_cap, self.image.width()-source_cap, self.image.width())
        sy = (0, source_cap, self.image.height()-source_cap, self.image.height())
        dx = (rect.left(), rect.left()+cap, rect.right()-cap, rect.right())
        dy = (rect.top(), rect.top()+cap, rect.bottom()-cap, rect.bottom())
        painter.save()
        painter.setOpacity(alpha)
        for y in range(3):
            for x in range(3):
                destination = QRectF(dx[x], dy[y], dx[x+1]-dx[x], dy[y+1]-dy[y])
                source = QRectF(sx[x], sy[y], sx[x+1]-sx[x], sy[y+1]-sy[y])
                if not destination.isEmpty():
                    painter.drawImage(destination, self.image, source)
        painter.restore()
