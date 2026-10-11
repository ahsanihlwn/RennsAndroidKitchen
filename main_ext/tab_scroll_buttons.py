# SPDX-License-Identifier: MPL-2.0
from functools import lru_cache

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QTabBar, QToolButton

from icons.handler import icon_pixmap
from main_ext.class_ThemeUi import theme as _th


def tab_scroll_style() -> str:
    return "QTabBar::scroller { width: 44px; }"


def _scroll_button_style() -> str:
    base = QColor(_th.BG_DARK)
    accent = QColor(_th.accent_hex)

    def blend(amount):
        return QColor(
            round(base.red() * (1 - amount) + accent.red() * amount),
            round(base.green() * (1 - amount) + accent.green() * amount),
            round(base.blue() * (1 - amount) + accent.blue() * amount),
        ).name()

    return f"""
    QToolButton {{
        width: 18px;
        height: 18px;
        margin: 7px 2px;
        padding: 0;
        background: {_th.BG_DARK};
        border: none;
        border-radius: 4px;
    }}
    QToolButton:hover:enabled {{
        background: {blend(0.14)};
    }}
    QToolButton:pressed:enabled {{
        background: {blend(0.24)};
    }}
    QToolButton:disabled {{
        background: {_th.BG_DARK};
    }}
    """


@lru_cache(maxsize=32)
def _scroll_icon(left: bool, theme_name: str) -> QIcon:
    # Match FileTree's branch arrow: icon_pixmap("arrow_right") at 9 px.
    pixmap = icon_pixmap("arrow_right").scaled(
        9, 9, Qt.KeepAspectRatio, Qt.SmoothTransformation,
    )
    if left:
        pixmap = pixmap.transformed(QTransform().rotate(180), Qt.SmoothTransformation)

    icon = QIcon()
    icon.addPixmap(pixmap, QIcon.Normal)
    icon.addPixmap(pixmap, QIcon.Active)
    disabled = QPixmap(pixmap.size())
    disabled.fill(Qt.transparent)
    painter = QPainter(disabled)
    painter.setOpacity(0.38)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    icon.addPixmap(disabled, QIcon.Disabled)
    return icon


def configure_tab_scroll_buttons(tab_bar) -> None:

    style = _scroll_button_style()
    for button in tab_bar.findChildren(QToolButton):
        name = button.objectName()
        if name not in ("ScrollLeftButton", "ScrollRightButton"):
            continue
        left = name == "ScrollLeftButton"
        button.setArrowType(Qt.NoArrow)
        if button.styleSheet() != style:
            button.setStyleSheet(style)
        button.setIcon(_scroll_icon(left, _th.name))
        button.setIconSize(QSize(9, 9))
        button.setCursor(Qt.PointingHandCursor)
        button.setToolTip("Scroll tabs left" if left else "Scroll tabs right")


class ThemedScrollTabBar(QTabBar):


    def showEvent(self, event):
        super().showEvent(event)
        configure_tab_scroll_buttons(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        configure_tab_scroll_buttons(self)

    def tabLayoutChange(self):
        super().tabLayoutChange()
        configure_tab_scroll_buttons(self)
