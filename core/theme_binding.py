# SPDX-License-Identifier: MPL-2.0
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import QWidget


def with_alpha(color, alpha):
    value = QColor(color)
    value.setAlphaF(alpha)
    return value


def panel_background(alpha):
    from main_ext.class_ThemeUi import theme
    color = QColor(theme.BG2)
    return theme.panel_rgba(color.red(), color.green(), color.blue(), alpha)


def _bind(widget, setter, factory):
    bindings = getattr(widget, "_theme_bindings", None)
    if bindings is None:
        bindings = widget._theme_bindings = {}
    bindings[setter] = factory
    getattr(widget, setter)(factory())


def bind_style(widget, factory):

    _bind(widget, "setStyleSheet", factory)


def bind_icon(widget, factory):
    _bind(widget, "setIcon", factory)


def bind_pixmap(widget, factory):
    _bind(widget, "setPixmap", factory)


def refresh_theme(root, recursive=True):

    widgets = [root]
    if recursive:
        widgets.extend(root.findChildren(QWidget))
        widgets.extend(root.findChildren(QAction))
    for widget in widgets:
        for setter, factory in getattr(widget, "_theme_bindings", {}).items():
            value = factory()
            if setter == "setStyleSheet":
                from core.skia_button import _source_sheet
                if value == _source_sheet(widget):
                    continue
            getattr(widget, setter)(value)
        callback = getattr(widget, "_refresh_theme", None)
        if callback is not None:
            callback()
        if isinstance(widget, QWidget):
            widget.update()
