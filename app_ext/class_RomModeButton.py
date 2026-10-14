# SPDX-License-Identifier: MPL-2.0
"""ROM mode dots are icons; captions keep the bundled native Qt font."""
from functools import lru_cache
import math
from PySide6.QtCore import QObject, QEvent, QSize, QRectF, Qt, QPointF
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPalette, QFontMetricsF
from PySide6.QtWidgets import QStyleOptionButton, QStyle

from core.language_binding import bind_text
from main_ext.class_ThemeUi import theme
from app_ext.class_ButtonPressAnimation import PressAnimatedButton, button_press_scale
from core.skia_button import render_button


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
        button._qt_button_caption = self.draw_caption
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

    def draw_caption(self, painter, option, rect):
        """Center the dots and shaped Qt caption together with a fixed 6px gap."""
        metrics = QFontMetricsF(self.button.font())
        icon_width = self.button.iconSize().width()
        text = metrics.elidedText(self.button.text(), Qt.ElideRight,
                                  max(0, rect.width() - icon_width - 12))
        width = icon_width + 6 + metrics.horizontalAdvance(text)
        left = rect.center().x() - width / 2
        mode = (QIcon.Disabled if not option.state & QStyle.State_Enabled else
                QIcon.Active if option.state & QStyle.State_MouseOver else QIcon.Normal)
        icon = self.button.icon().pixmap(self.button.iconSize(),
                                        self.button.devicePixelRatioF(), mode)
        painter.drawPixmap(QPointF(left, rect.center().y() - 5.5), icon)
        painter.setFont(self.button.font())
        painter.setPen(option.palette.color(QPalette.ButtonText))
        painter.drawText(QRectF(left + icon_width + 6, rect.y(),
                               metrics.horizontalAdvance(text) + 1, rect.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, text)


class RomModeButton(PressAnimatedButton):
    def paintEvent(self, event):
        scale = button_press_scale(self)
        image = render_button(self, scale)
        painter = QPainter(self)
        if image is not None:
            painter.drawImage(0, 0, image)
        else:
            option = QStyleOptionButton()
            self.initStyleOption(option)
            rect = QRectF(self.rect())
            painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
            painter.translate(rect.center())
            painter.scale(scale, scale)
            painter.translate(-rect.center())
            self.style().drawControl(QStyle.CE_PushButtonBevel, option, painter, self)
            contents = self.style().subElementRect(QStyle.SE_PushButtonContents, option, self)
            self._qt_button_caption(painter, option, QRectF(contents))
        painter.end()


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
