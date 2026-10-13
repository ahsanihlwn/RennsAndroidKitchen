# SPDX-License-Identifier: MPL-2.0
"""ROM mode dots are icons; captions keep the bundled native Qt font."""
from functools import lru_cache
import math
from PySide6.QtCore import QObject, QEvent, QSize, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

from core.language_binding import bind_text
from main_ext.class_ThemeUi import theme


@lru_cache(maxsize=64)
def _dots_icon(count, ratio, normal, hover, disabled):
    icon = QIcon()
    width = count * 12 + 2
    for mode, color in ((QIcon.Normal, normal), (QIcon.Active, hover), (QIcon.Disabled, disabled)):
        pixmap = QPixmap(math.ceil(width * ratio), math.ceil(11 * ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        for index in range(count):
            painter.drawEllipse(QRectF(index * 12 + .5, .5, 10, 10))
        painter.end()
        icon.addPixmap(pixmap, mode)
    return icon


class _Marker(QObject):
    def __init__(self, button, count, active):
        super().__init__(button)
        self.button, self.count, self.active = button, count, active
        button.installEventFilter(self)
        button._refresh_rom_marker = self.refresh
        self.refresh()

    def refresh(self):
        active = self.active()
        normal = theme.FG_BLUE if active else theme.FG_MUTED
        hover = normal if active else theme.FG
        icon = _dots_icon(self.count, self.button.devicePixelRatioF(), normal, hover, theme.FG_DISABLED)
        if self.button.icon().cacheKey() != icon.cacheKey():
            self.button.setIcon(icon)
        size = QSize(self.count * 12 + 2, 11)
        if self.button.iconSize() != size:
            self.button.setIconSize(size)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.StyleChange, QEvent.PaletteChange, QEvent.DevicePixelRatioChange):
            self.refresh()
        return False


def bind_rom_mode_button(button, count, active, translate=None):
    if translate is None:
        from core.ui_lang import t
        translate = t
    key = "btn_single_rom" if count == 1 else "btn_dual_rom"
    # U+2B24 is absent from Poppins. A text glyph makes Qt enumerate system
    # fallback fonts while measuring even a hidden Create Project page.
    bind_text(button, lambda: translate(key).lstrip("\u2b24 "))
    _Marker(button, count, active)
    return button
