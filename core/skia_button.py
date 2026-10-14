# SPDX-License-Identifier: MPL-2.0
"""Draw QSS button surfaces as Skia vectors; ask Qt to draw only the label.

The resolver supports the solid-color, uniform rounded borders used by this
application. Unsupported decorations stay on the native Qt paint path rather
than being silently approximated. QSS still owns font/layout/input semantics.
"""
from dataclasses import dataclass
from functools import lru_cache
from math import ceil
import re
from shiboken6 import getCppPointer

from PySide6.QtCore import QObject, QEvent, QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QPushButton, QLabel, QWidget, QStyle, QStyleOptionButton

from core.skia_gpu import gpu_available, render_gpu, skia

_STATE_BITS = {name: flag.value for name, flag in (
    ("enabled", QStyle.State_Enabled), ("disabled", QStyle.State_Enabled),
    ("hover", QStyle.State_MouseOver), ("pressed", QStyle.State_Sunken),
    ("checked", QStyle.State_On), ("unchecked", QStyle.State_On),
    ("on", QStyle.State_On), ("off", QStyle.State_On),
    ("focus", QStyle.State_HasFocus), ("selected", QStyle.State_Selected),
)}


class _QssInvalidator(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.revision = 0

    def watch_name(self, widget):
        if not getattr(widget, "_skia_qss_name_watched", False):
            widget.objectNameChanged.connect(self._name_changed)
            widget._skia_qss_name_watched = True

    def _name_changed(self, name):
        self.revision += 1

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.DynamicPropertyChange
                and bytes(event.propertyName()) == b"_PySideInvalidatePtr"):
            return False  # PySide wrapper bookkeeping cannot change a QSS surface.
        if event.type() in (QEvent.StyleChange, QEvent.DynamicPropertyChange,
                            QEvent.ParentChange, QEvent.FontChange, QEvent.PaletteChange):
            self.revision += 1
        return False


def _style_revision():
    app = QApplication.instance()
    invalidator = getattr(app, "_skia_qss_invalidator", None)
    if invalidator is None:
        invalidator = _QssInvalidator(app)
        app._skia_qss_invalidator = invalidator
        app.installEventFilter(invalidator)
    return invalidator.revision


@lru_cache(maxsize=256)
def _rules(sheet):
    sheet = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
    if "{" not in sheet:
        sheet = "* {" + sheet + "}"
    result = []
    for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", sheet):
        declarations = []
        for item in body.split(";"):
            if ":" in item:
                name, value = item.split(":", 1)
                declarations.append((name.strip().lower(), value.strip()))
        for selector in selectors.split(","):
            result.append((selector.strip(), tuple(declarations)))
    return tuple(result)


@lru_cache(maxsize=1024)
def _atom(selector):
    attributes = tuple(re.findall(r'\[([\w-]+)(?:\s*=\s*["\']?([^"\']*?)["\']?)?\]', selector))
    selector = re.sub(r"\[[^\]]+\]", "", selector)
    pseudos = tuple(re.findall(r":(!?[\w-]+)", selector))
    selector = re.sub(r":!?[\w-]+", "", selector)
    names = tuple(re.findall(r"#([\w-]+)", selector))
    selector = re.sub(r"#[\w-]+", "", selector)
    return attributes, pseudos, names, selector.strip()


def _atom_matches(widget, selector, option=None):
    # Subcontrols, siblings, positional selectors and unknown pseudo states
    # aren't part of this renderer's supported QSS subset.
    if "::" in selector:
        return False
    attributes, pseudos, names, kind = _atom(selector)
    if kind and kind != "*" and not widget.inherits(kind):
        return False
    if names and widget.objectName() not in names:
        return False
    for name, value in attributes:
        actual = widget.property(name)
        actual = str(actual).lower() if isinstance(actual, bool) else str(actual)
        if actual != value:
            return False
    if not pseudos:
        return True
    state = option.state.value if option is not None else 0
    for pseudo in pseudos:
        negate = pseudo.startswith("!")
        name = pseudo.lstrip("!")
        if name in _STATE_BITS:
            matched = bool(state & _STATE_BITS[name])
            if option is None and name in ("enabled", "disabled"):
                matched = widget.isEnabled()
            if name in ("disabled", "unchecked", "off"):
                matched = not matched
        elif name in ("default", "flat"):
            features = getattr(getattr(option, "features", None), "value", 0)
            flag = QStyleOptionButton.DefaultButton if name == "default" else QStyleOptionButton.Flat
            matched = bool(features & flag.value)
        elif name in ("vertical", "horizontal"):
            matched = hasattr(widget, "orientation") and widget.orientation() == (
                Qt.Vertical if name == "vertical" else Qt.Horizontal)
        elif name == "read-only":
            matched = hasattr(widget, "isReadOnly") and widget.isReadOnly()
        else:
            return False
        if matched == negate:
            return False
    return True


@lru_cache(maxsize=1024)
def _selector_parts(selector):
    return tuple(re.findall(r'(?:\[[^\]]*\]|[^\s>])+|>', selector))


def _matches(widget, selector, option):
    # Split outside attribute selectors, preserving quoted property values.
    parts = _selector_parts(selector)
    if not parts or not _atom_matches(widget, parts[-1], option):
        return False
    current = widget
    index = len(parts) - 2
    while index >= 0:
        direct = parts[index] == ">"
        if direct:
            index -= 1
        if index < 0:
            return False
        current = current.parentWidget()
        while current is not None and not _atom_matches(current, parts[index]):
            if direct:
                return False
            current = current.parentWidget()
        if current is None:
            return False
        index -= 1
    return True


@lru_cache(maxsize=1024)
def _specificity(selector):
    # Qt's cascade: IDs, attributes/pseudo states, then type selectors.
    clean = re.sub(r"\[[^\]]*\]", "", selector)
    types = sum(bool(re.match(r"^[A-Za-z_]", part)) for part in clean.split())
    return (selector.count("#"), selector.count("[") + len(re.findall(r":(?!:)", clean)), types)


@lru_cache(maxsize=256)
def _dependencies(sheet):
    return tuple(sorted(set(re.findall(r"\[([\w-]+)", sheet))))


def _source_sheet(widget):
    sheet = widget.styleSheet()
    if sheet == getattr(widget, "_skia_surface_effective", None):
        return widget._skia_surface_original
    return sheet


def _declarations(widget, option, subcontrol=None):
    fast_key = (_style_revision(), option.state.value,
                getattr(getattr(option, "features", None), "value", 0),
                widget.orientation().value if hasattr(widget, "orientation") else None,
                widget.isReadOnly() if hasattr(widget, "isReadOnly") else None)
    fast_cache = getattr(widget, "_skia_qss_fast", {})
    fast = fast_cache.get(subcontrol)
    if fast is not None and fast[0] == fast_key:
        return fast[1]
    scopes = []
    ancestry = []
    current = widget
    while current is not None:
        QApplication.instance()._skia_qss_invalidator.watch_name(current)
        scopes.append(_source_sheet(current))
        ancestry.append(current)
        current = current.parentWidget()
    scopes.append(QApplication.instance().styleSheet())
    props = sorted({name for sheet in scopes for name in _dependencies(sheet)})
    signature = (tuple(scopes), option.state.value,
                 getattr(getattr(option, "features", None), "value", 0), subcontrol,
                 widget.orientation().value if hasattr(widget, "orientation") else None,
                 widget.isReadOnly() if hasattr(widget, "isReadOnly") else None,
                 tuple((getCppPointer(item)[0], item.objectName(), item.isEnabled(),
                        tuple(item.property(name) for name in props)) for item in ancestry))
    cache = getattr(widget, "_skia_qss_cache", {})
    cached = cache.get(subcontrol)
    if cached is not None and cached[0] == signature:
        fast_cache[subcontrol] = (fast_key, cached[1])
        widget._skia_qss_fast = fast_cache
        return cached[1]
    result = []
    for sheet in reversed(scopes):
        rules = []
        for selector, declarations in _rules(sheet):
            if subcontrol is not None:
                if "::" + subcontrol not in selector:
                    continue
                selector = selector.replace("::" + subcontrol, "", 1)
            elif "::" in selector:
                continue
            rules.append((selector, declarations))
        matched = [( _specificity(selector), order, declarations)
                   for order, (selector, declarations) in enumerate(rules)
                   if _matches(widget, selector, option)]
        for _, _, declarations in sorted(matched):
            result.extend(declarations)
    result = tuple(result)
    cache[subcontrol] = (signature, result)
    widget._skia_qss_cache = cache
    fast_cache[subcontrol] = (fast_key, result)
    widget._skia_qss_fast = fast_cache
    return result


def _color(value):
    value = value.strip()
    match = re.fullmatch(r"rgba?\(([^)]+)\)", value, re.I)
    if match:
        channels = [part.strip() for part in match[1].split(",")]
        if len(channels) not in (3, 4):
            raise ValueError(value)
        rgb = [round(float(part.rstrip("%")) * (2.55 if "%" in part else 1))
               for part in channels[:3]]
        alpha = 255
        if len(channels) == 4:
            part = channels[3]
            alpha = round(float(part.rstrip("%")) *
                          (2.55 if "%" in part else 255 if "." in part else 1))
        return QColor(*rgb, max(0, min(255, alpha)))
    color = QColor(value)
    if not color.isValid():
        raise ValueError(value)
    return color


def _length(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)(?:px)?", value.strip())
    if match is None:
        raise ValueError(value)
    return float(match[1])


@dataclass
class ButtonSurface:
    background: QColor
    border: QColor
    width: float
    radius: float

    def draw(self, canvas, bridge, rect, opacity=1.0, radius=None):
        rect = QRectF(rect)
        radius = self.radius if radius is None else radius
        radius = min(radius, rect.width() / 2, rect.height() / 2)
        outer = skia.RRect.MakeRectXY(
            skia.Rect.MakeXYWH(rect.x(), rect.y(), rect.width(), rect.height()), radius, radius)
        if self.background.alpha():
            paint = skia.Paint(Color4f=bridge.color(self.background), AntiAlias=True)
            paint.setAlphaf(self.background.alphaF() * opacity)
            canvas.drawRRect(outer, paint)
        width = min(self.width, rect.width() / 2, rect.height() / 2)
        if width > 0 and self.border.alpha():
            # A difference of rounded rects preserves fractional CSS widths.
            # Qt QSS rounded borders can round a 1.5px width to integer pixels.
            inner_radius = max(0.0, radius - width)
            inner = skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(
                rect.x() + width, rect.y() + width,
                rect.width() - 2 * width, rect.height() - 2 * width),
                inner_radius, inner_radius)
            paint = skia.Paint(Color4f=bridge.color(self.border), AntiAlias=True)
            paint.setAlphaf(self.border.alphaF() * opacity)
            canvas.drawDRRect(outer, inner, paint)


def button_surface(widget, option=None, subcontrol=None):
    """Resolve solid rounded QSS surfaces. None means use the native style."""
    if option is None:
        option = QStyleOptionButton()
        option.initFrom(widget)
        if hasattr(widget, "initStyleOption"):
            widget.initStyleOption(option)
    background, border = QColor(Qt.transparent), QColor(Qt.transparent)
    width, radius, decorated = 0.0, 0.0, False
    unsupported = {}
    try:
        declarations = _declarations(widget, option, subcontrol)
        cache = getattr(widget, "_skia_surface_cache", {})
        cached = cache.get(subcontrol)
        if cached is not None and cached[0] == declarations:
            return cached[1]
        for name, value in declarations:
            if name in ("background", "background-color"):
                background = _color(value)
                decorated = True
            elif name == "border":
                decorated = True
                # A shorthand resets per-side declarations from earlier rules.
                unsupported = {key: item for key, item in unsupported.items()
                               if not key.startswith("border-")}
                if value in ("none", "0", "0px"):
                    width, border = 0.0, QColor(Qt.transparent)
                else:
                    match = re.fullmatch(r"([\d.]+(?:px)?)\s+solid\s+(.+)", value)
                    if not match:
                        return None
                    width, border = _length(match[1]), _color(match[2])
            elif name == "border-width":
                width = _length(value)
            elif name == "border-color":
                border = _color(value)
            elif name == "border-radius":
                radius = _length(value)
            elif name == "border-style":
                if value == "none":
                    width = 0.0
                elif value != "solid":
                    return None
            elif name.startswith(("border-", "background-")) or name == "image":
                unsupported[name] = value
        if unsupported or not decorated:
            return None
        surface = ButtonSurface(background, border, width, radius)
        cache[subcontrol] = (declarations, surface)
        widget._skia_surface_cache = cache
        return surface
    except (ValueError, TypeError):
        return None


def _button_children(widget, ratio):
    children = [child for child in widget.findChildren(QWidget, options=Qt.FindDirectChildrenOnly)
                if not child.isHidden() and not child.isWindow()]
    if not children:
        return None
    # Dropdown text and arrows are native leaf QLabels. Their pixels remain
    # stable throughout a press; don't render them again for each scale tick.
    # Other/custom/live children still get a fresh native capture.
    key = None
    if all(type(child).paintEvent is QLabel.paintEvent and child.movie() is None
           and not child.findChildren(QWidget) for child in children):
        key = (widget.width(), widget.height(), ratio, _style_revision(),
               tuple((getCppPointer(child)[0], child.geometry().getRect(), child.text(),
                      child.pixmap().cacheKey(), child.font().toString(),
                      child.palette().cacheKey(), child.alignment().value,
                      child.margin(), child.indent(), child.wordWrap(),
                      child.hasScaledContents(), child.textFormat().value,
                      child.layoutDirection().value, child.isEnabled()) for child in children))
        cached = getattr(widget, "_skia_qt_children_key", None)
        if cached is not None and cached[0] == key:
            return cached[1]
    image = QImage(max(1, ceil(widget.width() * ratio)),
                   max(1, ceil(widget.height() * ratio)), QImage.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    for child in children:
        child.render(painter, child.pos(), QRegion(), QWidget.DrawWindowBackground | QWidget.DrawChildren)
    painter.end()
    cached = getattr(widget, "_skia_qt_children", None)
    if cached is not None and cached.devicePixelRatio() == ratio and cached == image:
        image = cached
    widget._skia_qt_children = image
    if key is not None:
        widget._skia_qt_children_key = (key, image)
    return image


def button_label(widget, option, ratio, include_children=False):
    """Native Qt label (font shaping, palette, icons and contents geometry)."""
    children = _button_children(widget, ratio) if include_children else None
    key = (widget.width(), widget.height(), ratio, option.state.value, option.features.value,
           option.text, option.icon.cacheKey(), option.iconSize.width(), option.iconSize.height(),
           option.palette.cacheKey(), widget.font().toString(),
           tuple(_declarations(widget, option)), widget.layoutDirection(),
           children.cacheKey() if children is not None else None)
    cached = getattr(widget, "_skia_qt_label", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    image = QImage(max(1, ceil(widget.width() * ratio)),
                   max(1, ceil(widget.height() * ratio)), QImage.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.transparent)
    label = QStyleOptionButton(option)
    label.rect = widget.style().subElementRect(QStyle.SE_PushButtonContents, option, widget)
    painter = QPainter(image)
    painter.setFont(widget.font())
    painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing |
                           QPainter.SmoothPixmapTransform)
    caption = getattr(widget, "_qt_button_caption", None)
    if caption is not None:
        caption(painter, option, QRectF(label.rect))
    else:
        widget.style().drawControl(QStyle.CE_PushButtonLabel, label, painter, widget)
    if children is not None:
        painter.drawImage(QPoint(), children)
    painter.end()
    widget._skia_qt_label = (key, image)
    return image


def render_button(widget, scale=1.0, include_children=False):
    if not gpu_available():
        return None
    # Preserve subclasses that draw extra content, e.g. theme previews and
    # image toggles. Their existing source painting remains GPU-composited.
    if (type(widget).paintEvent is not QPushButton.paintEvent
            and not getattr(widget, "_paints_button_press_feedback", False)):
        return None
    option = QStyleOptionButton()
    widget.initStyleOption(option)
    if option.features & QStyleOptionButton.HasMenu:
        return None
    surface = button_surface(widget, option)
    if surface is None:
        return None
    ratio = widget.devicePixelRatioF()
    label = button_label(widget, option, ratio, include_children)
    rect = QRectF(widget.rect())
    key = (widget.width(), widget.height(), ratio, scale, surface, label.cacheKey())
    cached = getattr(widget, "_skia_vector_frame", None)
    if cached is not None and cached[0] == key:
        return cached[1]

    def draw(canvas, bridge):
        center = rect.center()
        canvas.translate(center.x(), center.y())
        canvas.scale(scale, scale)
        canvas.translate(-center.x(), -center.y())
        surface.draw(canvas, bridge, rect)
        bridge.draw_image(canvas, label, rect)

    image = render_gpu(widget.size(), ratio, draw)
    if image is not None:
        image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        widget._skia_vector_frame = (key, image)
    return image
