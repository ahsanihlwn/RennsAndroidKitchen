# SPDX-License-Identifier: MPL-2.0
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QWidget


def bind_language(widget, setter, factory):
    bindings = getattr(widget, "_language_bindings", None)
    if bindings is None:
        bindings = widget._language_bindings = {}
    bindings[setter] = factory
    getattr(widget, setter)(factory())
    return widget


def bind_text(widget, factory):
    return bind_language(widget, "setText", factory)


def refresh_language(root):
    widgets = [root, *root.findChildren(QWidget), *root.findChildren(QAction)]
    for widget in widgets:
        for setter, factory in getattr(widget, "_language_bindings", {}).items():
            value = factory()
            getter = setter[3:4].lower() + setter[4:]
            if value != getattr(widget, getter)():
                getattr(widget, setter)(value)
