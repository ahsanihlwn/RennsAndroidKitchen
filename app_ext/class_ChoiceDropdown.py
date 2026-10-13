# SPDX-License-Identifier: MPL-2.0
"""Shared Settings dropdown button and morph popup lifecycle."""
from math import ceil
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QTransform, QPalette, QPainter, QImage
from PySide6.QtWidgets import QLabel, QHBoxLayout, QStyleOptionButton, QStyle
from shiboken6 import isValid

from app_ext.class_ChoiceMorphPopup import ChoiceMorphPopup
from app_ext.class_ButtonPressAnimation import PressAnimatedButton, button_press_scale
from core.skia_button import render_button
from core.theme_binding import bind_style, bind_pixmap
from icons.handler import icon_pixmap
from main_ext.class_ThemeUi import theme


def _button_style():
    return f"""
        QPushButton {{
            background: {theme.border_color(0.05)};
            border: 1px solid {theme.border_color(0.09)};
            border-radius: 6px;
            color: {theme.FG_BLUE_MUTED};
            font-size: 12px;
            padding: 0;
            text-align: left;
        }}
        QPushButton:hover {{ background: {theme.border_color(0.09)}; border-color: {theme.border_color(0.15)}; color: {theme.FG}; }}
        QPushButton:pressed {{ background: {theme.border_color(0.13)}; }}
    """


class ChoiceDropdown(PressAnimatedButton):
    def __init__(self, caption="", parent=None, minimum_width=80):
        super().__init__(parent)
        self._minimum_width = minimum_width
        self.setFixedHeight(28)
        self.setAutoDefault(False)
        self.setCursor(Qt.PointingHandCursor)
        bind_style(self, _button_style)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 9, 0)
        layout.setSpacing(5)
        self.caption = QLabel(self)
        self.caption.setAttribute(Qt.WA_TransparentForMouseEvents)
        bind_style(self.caption, lambda: f"color:{theme.FG_BLUE_MUTED}; background:transparent; border:none; font-size:12px;")
        self.arrow = QLabel(self)
        self.arrow.setFixedSize(9, 9)
        self.arrow.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.arrow.setStyleSheet("background:transparent; border:none;")
        bind_pixmap(self.arrow, lambda: icon_pixmap("arrow_right").scaled(
            9, 9, Qt.KeepAspectRatio, Qt.SmoothTransformation,
        ).transformed(QTransform().rotate(90), Qt.SmoothTransformation))
        layout.addWidget(self.caption, 1)
        layout.addWidget(self.arrow)
        # The labels supply native Qt font/layout/icon data; draw their content
        # in this button's paint event so resize/effect caches cannot clip them.
        for label in (self.caption, self.arrow):
            policy = label.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            label.setSizePolicy(policy)
            label.hide()
        self.caption.setText(caption)
        self.fit_width()

    def setCaption(self, caption):
        if self.caption.text() == caption:
            return
        self.caption.setText(caption)
        self.fit_width()

    def currentText(self):
        return self.caption.text()

    def paintEvent(self, event):
        scale = button_press_scale(self)
        caption = self._qt_caption_frame(scale)
        image = render_button(self, scale)
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing |
                               QPainter.SmoothPixmapTransform)
        if image is not None:
            painter.drawImage(0, 0, image)
        if image is None:
            painter.save()
            center = QRectF(self.rect()).center()
            painter.translate(center)
            painter.scale(scale, scale)
            painter.translate(-center)
            option = QStyleOptionButton()
            self.initStyleOption(option)
            self.style().drawControl(QStyle.CE_PushButton, option, painter, self)
            painter.restore()
        painter.drawImage(0, 0, caption)
        painter.end()

    def _qt_caption_frame(self, scale):
        ratio = self.devicePixelRatioF()
        arrow = self.arrow.pixmap()
        key = (self.currentText(), self.width(), self.height(), ratio, scale,
               self.layoutDirection(),
               self.caption.font().toString(), self.caption.palette().cacheKey(),
               self.caption.geometry(), self.arrow.geometry(),
               arrow.cacheKey() if arrow is not None else None)
        cached = getattr(self, "_caption_frame", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        image = QImage(ceil(self.width() * ratio), ceil(self.height() * ratio),
                       QImage.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing |
                               QPainter.SmoothPixmapTransform)
        painter.setLayoutDirection(self.layoutDirection())
        center = QRectF(self.rect()).center()
        painter.translate(center)
        painter.scale(scale, scale)
        painter.translate(-center)
        self.caption_drawer(self.currentText())(painter)
        painter.end()
        self._caption_frame = (key, image)
        return image

    def caption_drawer(self, text):
        font, color = self.caption.font(), self.caption.palette().color(QPalette.WindowText)
        rect, alignment = QRectF(self.caption.geometry()), self.caption.alignment()
        arrow, arrow_pos = self.arrow.pixmap(), self.arrow.pos()
        def draw(painter):
            painter.setFont(font)
            painter.setPen(color)
            painter.drawText(rect, alignment, text)
            if arrow is not None and not arrow.isNull():
                painter.drawPixmap(arrow_pos, arrow)
        return draw

    def fit_width(self):
        self.setAccessibleName(self.currentText())
        self.setFixedWidth(max(self._minimum_width, self.caption.sizeHint().width() + 35))
        self.layout().invalidate()
        self.layout().setGeometry(self.rect())
        self.layout().activate()

    def _refresh_theme(self):
        self.fit_width()


def show_choice_popup(anchor, choices, current, on_select, *, caption_for_choice=None):
    """Use the anchor's top-level window, including modal Settings dialogs."""
    choices = tuple(choices)
    if not choices:
        return
    root = anchor.window()
    existing = getattr(root, "_choice_popup", None)
    if existing is not None:
        existing.close()
        return
    popup = ChoiceMorphPopup(root, anchor, choices, current, caption_for_choice=caption_for_choice)
    root._choice_popup = popup
    try:
        chosen = popup.exec()
    finally:
        root._choice_popup = None
    if chosen is not None and isValid(root) and isValid(anchor) and root.isVisible():
        on_select(chosen)
