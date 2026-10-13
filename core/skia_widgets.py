# SPDX-License-Identifier: MPL-2.0
"""Cached Skia vector surfaces underneath native Qt widget contents.

Qt still paints fonts, selection, caret, icons, and handles all input. A scoped
transparent QSS override suppresses only the original surface, retaining its
border widths, padding and subcontrol geometry. The original rules remain the
source of truth for Skia. No idle graphics effects or per-frame QSS mutations.
"""
import time
from PySide6.QtCore import QObject, QEvent, QPoint, QRectF, QTimer, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import (
    QApplication, QWidget, QFrame, QLabel, QLineEdit, QAbstractButton,
    QAbstractScrollArea, QComboBox, QSpinBox, QDoubleSpinBox, QProgressBar,
    QSlider, QScrollBar, QStyle, QStyleOption, QStyleOptionSlider,
    QStyleOptionProgressBar,
)
from shiboken6 import getCppPointer, isValid

from core.skia_gpu import gpu_available, render_gpu, skia
from core.skia_button import button_surface, _style_revision

_NATIVE_PAINTS = frozenset(cls.paintEvent for cls in (
    QWidget, QFrame, QLabel, QLineEdit, QAbstractScrollArea, QComboBox,
    QSpinBox, QDoubleSpinBox, QProgressBar, QSlider, QScrollBar,
))
_STYLE_EVENTS = frozenset((QEvent.Show, QEvent.StyleChange,
                          QEvent.DynamicPropertyChange, QEvent.ParentChange))
_STATE_EVENTS = frozenset((QEvent.FocusIn, QEvent.FocusOut, QEvent.Enter,
                          QEvent.Leave, QEvent.EnabledChange, QEvent.PaletteChange))


def _option(widget):
    if isinstance(widget, (QSlider, QScrollBar)):
        option = QStyleOptionSlider()
        widget.initStyleOption(option)
    elif isinstance(widget, QProgressBar):
        option = QStyleOptionProgressBar()
        widget.initStyleOption(option)
    else:
        option = QStyleOption()
        option.initFrom(widget)
    return option


def _layers(widget, option):
    surface = button_surface(widget, option)
    if surface is None:
        return None
    result = [(surface, QRectF(widget.rect()))]
    if isinstance(widget, QScrollBar):
        controls = (("sub-page", QStyle.SC_ScrollBarSubPage),
                    ("add-page", QStyle.SC_ScrollBarAddPage),
                    ("handle", QStyle.SC_ScrollBarSlider),
                    ("sub-line", QStyle.SC_ScrollBarSubLine),
                    ("add-line", QStyle.SC_ScrollBarAddLine))
        for name, control in controls:
            rect = widget.style().subControlRect(QStyle.CC_ScrollBar, option, control, widget)
            if rect.isEmpty():
                continue
            shape = button_surface(widget, option, name)
            if shape is None:
                return None
            result.append((shape, QRectF(rect)))
    elif isinstance(widget, QSlider):
        groove = widget.style().subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderGroove, widget)
        handle = widget.style().subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderHandle, widget)
        track = button_surface(widget, option, "groove")
        thumb = button_surface(widget, option, "handle")
        if track is None or thumb is None or widget.tickPosition() != QSlider.NoTicks:
            return None
        result.append((track, QRectF(groove)))
        fill = button_surface(widget, option, "sub-page")
        if fill is not None:
            rect = QRectF(groove)
            if widget.orientation() == Qt.Horizontal:
                if option.upsideDown:
                    rect.setLeft(handle.center().x())
                else:
                    rect.setRight(handle.center().x())
            elif option.upsideDown:
                rect.setTop(handle.center().y())
            else:
                rect.setBottom(handle.center().y())
            result.append((fill, rect))
        result.append((thumb, QRectF(handle)))
    elif isinstance(widget, QProgressBar):
        if option.minimum == option.maximum:
            return None  # Preserve native indeterminate animation.
        chunk = button_surface(widget, option, "chunk")
        if chunk is None:
            return None
        rect = QRectF(widget.style().subElementRect(QStyle.SE_ProgressBarContents, option, widget))
        fraction = max(0.0, min(1.0, (option.progress - option.minimum) /
                                      max(1, option.maximum - option.minimum)))
        if widget.orientation() == Qt.Horizontal:
            reverse = option.invertedAppearance ^ (widget.layoutDirection() == Qt.RightToLeft)
            width = rect.width() * fraction
            if reverse:
                rect.setLeft(rect.right() - width)
            else:
                rect.setWidth(width)
        else:
            height = rect.height() * fraction
            if option.invertedAppearance:
                rect.setHeight(height)
            else:
                rect.setTop(rect.bottom() - height)
        if not rect.isEmpty():
            result.append((chunk, rect))
    if not any(shape.background.alpha() or (shape.width and shape.border.alpha())
               for shape, _ in result):
        return None
    return result


def render_widget_surface(widget):
    option = _option(widget)
    ratio = widget.devicePixelRatioF()
    control = None
    if isinstance(widget, (QSlider, QScrollBar)):
        control = (option.minimum, option.maximum, option.sliderPosition,
                   option.pageStep, option.upsideDown, option.orientation.value,
                   option.activeSubControls.value, option.subControls.value)
    elif isinstance(widget, QProgressBar):
        control = (option.minimum, option.maximum, option.progress, option.text,
                   option.textVisible, option.textAlignment.value,
                   option.bottomToTop, option.invertedAppearance, widget.orientation().value)
    fast_key = (widget.width(), widget.height(), ratio, _style_revision(),
                option.state.value, option.direction.value, control)
    fast = getattr(widget, "_skia_widget_fast_frame", None)
    if fast is not None and fast[0] == fast_key:
        return fast[1]
    layers = _layers(widget, option)
    if layers is None:
        return None
    key = (widget.width(), widget.height(), ratio,
           tuple((shape, rect.getRect()) for shape, rect in layers),
           (option.state.value, option.text, option.textVisible,
            option.textAlignment.value, option.bottomToTop,
            option.palette.cacheKey(), widget.font().toString(),
            _style_revision()) if isinstance(widget, QProgressBar) else None)
    cached = getattr(widget, "_skia_widget_frame", None)
    if cached is not None and cached[0] == key:
        widget._skia_widget_fast_frame = (fast_key, cached[1])
        return cached[1]

    label = None
    if isinstance(widget, QProgressBar) and widget.isTextVisible():
        # Native Qt owns formatting, font shaping, alignment and text color.
        label = QImage(max(1, round(widget.width() * ratio)),
                       max(1, round(widget.height() * ratio)), QImage.Format_ARGB32_Premultiplied)
        label.setDevicePixelRatio(ratio)
        label.fill(Qt.transparent)
        painter = QPainter(label)
        painter.setFont(widget.font())
        widget.style().drawControl(QStyle.CE_ProgressBarLabel, option, painter, widget)
        painter.end()

    def draw(canvas, bridge):
        for shape, rect in layers:
            shape.draw(canvas, bridge, rect)
        if label is not None:
            bridge.draw_image(canvas, label, QRectF(widget.rect()))

    image = render_gpu(widget.size(), ratio, draw)
    if image is not None:
        image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        widget._skia_widget_frame = (key, image)
        widget._skia_widget_fast_frame = (fast_key, image)
    return image


class _WidgetGpuManager(QObject):
    def __init__(self, app):
        super().__init__(app)
        self._busy = False
        self._pending = set()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._flush)

    @staticmethod
    def _eligible(widget):
        if (not isinstance(widget, QWidget) or not isValid(widget) or isinstance(widget, QAbstractButton)
                or (widget.isWindow() and widget.testAttribute(Qt.WA_TranslucentBackground))
                or widget.property("skipWidgetGpuRendering")
                or getattr(widget, "_skia_widget_failed", False)
                or type(widget).paintEvent not in _NATIVE_PAINTS):
            return False
        parent = widget.parentWidget()
        # Native item/editor viewport painting owns its backing store.
        return not (isinstance(parent, QAbstractScrollArea) and parent.viewport() is widget)

    def _queue(self, widget):
        self._pending.add(widget)
        if not self._timer.isActive():
            self._timer.start(0)

    def _flush(self):
        if not gpu_available():
            self._pending.clear()
            return
        started = time.perf_counter()
        deferred = set()
        while self._pending:
            widget = self._pending.pop()
            startup = False
            if isValid(widget) and widget.isVisible():
                root = widget.window()
                startup = getattr(root, "_startup_splash", None) is not None
                if getattr(root, "_cached_page_transition_active", False):
                    deferred.add(widget)
                else:
                    self._prepare(widget)
            if startup and self._pending and time.perf_counter() - started >= .004:
                # Returning to Qt lets splash/input/animation events run
                # between batches of style preparation.
                self._timer.start(1)
                break
        self._pending.update(deferred)
        if deferred and not self._timer.isActive():
            self._timer.start(100)

    def _prepare(self, widget):
        if not self._eligible(widget) or self._busy:
            return
        sheet = widget.styleSheet()
        if sheet == getattr(widget, "_skia_surface_effective", None):
            widget._skia_surface_active = True
            return
        if _layers(widget, _option(widget)) is None:
            widget._skia_no_surface_revision = _style_revision()
            widget._skia_no_surface_valid = True
            return
        identity = str(getCppPointer(widget)[0])
        # The property makes this override local to the one widget. Including
        # an existing ID preserves specificity against its local ID rules.
        object_id = "#" + widget.objectName() if widget.objectName() else ""
        selector = f'*{object_id}[_rakSkiaSurface="{identity}"]'
        selectors = ", ".join(selector + state for state in ("", ":hover", ":focus", ":disabled", ":read-only"))
        # Qt implicitly wraps declaration-only sheets in '* { ... }'. Mixing
        # raw declarations with a new selector makes that stylesheet invalid,
        # losing its fonts/padding. Normalize it before appending our rule.
        base = sheet if "{" in sheet or not sheet.strip() else "* {" + sheet + "}"
        effective = base + "\n" + selectors + " {background:transparent; border-color:transparent;}"
        self._busy = True
        try:
            widget._skia_surface_original = sheet
            widget._skia_surface_effective = effective
            widget.setProperty("_rakSkiaSurface", identity)
            widget.setStyleSheet(effective)
            widget._skia_surface_active = True
            widget.update()
        finally:
            self._busy = False

    def eventFilter(self, widget, event):
        kind = event.type()
        # Don't retain wrappers or query children while Qt is constructing a
        # QWidget base for a scrollbar/editor. Doing so can pin the wrong
        # PySide wrapper type before the most-derived constructor completes.
        if (kind not in _STYLE_EVENTS and kind not in _STATE_EVENTS and kind != QEvent.Paint
                or self._busy):
            return False
        if kind == QEvent.DynamicPropertyChange and bytes(event.propertyName()).startswith(b"_PySide"):
            return False
        # A stylesheet on an unrelated animation replica does not change this
        # widget's empty surface. Qt sends actual style/state changes locally;
        # keep negative results through Hide/Show instead of reparsing hundreds
        # of native captions whenever a page becomes visible again.
        if kind != QEvent.Show and kind != QEvent.Paint and hasattr(widget, "_skia_no_surface_revision"):
            widget._skia_no_surface_valid = False
        if (kind in _STYLE_EVENTS and kind != QEvent.Show
                and not hasattr(widget, "_skia_surface_original")):
            return False
        if not self._eligible(widget):
            return False
        if kind in _STYLE_EVENTS:
            if kind == QEvent.Show and getattr(widget, "_skia_no_surface_valid", False):
                return False
            if kind == QEvent.StyleChange:
                widget._skia_surface_active = False
            self._queue(widget)
        elif kind in _STATE_EVENTS:
            widget.update()
        elif kind == QEvent.Paint:
            if not getattr(widget, "_skia_surface_active", False):
                if not getattr(widget, "_skia_no_surface_valid", False):
                    self._queue(widget)
                return False
            image = render_widget_surface(widget)
            if image is None:
                # Restore native decorations if allocation/context support is
                # lost. Defer mutation until the paint callback unwinds.
                self._restore_later(widget)
                return False
            painter = QPainter(widget)
            painter.drawImage(QPoint(), image)
            painter.end()
            return isinstance(widget, (QScrollBar, QSlider, QProgressBar))
        return False

    def _restore_later(self, widget):
        def restore():
            if not isValid(widget):
                return
            self._busy = True
            try:
                original = widget._skia_surface_original
                widget._skia_widget_failed = True
                widget._skia_surface_effective = None
                widget._skia_surface_active = False
                widget.setStyleSheet(original)
                widget.update()
            finally:
                self._busy = False
        QTimer.singleShot(0, restore)


def install_widget_gpu_rendering(app):
    if app is None or getattr(app, "_widget_gpu_manager", None) is not None:
        return
    if not gpu_available():
        return
    # Match an existing Qt Quick API; do not force WebEngine off its platform
    # backend merely to add an OpenGL child to the same top-level window.
    from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
    app._skia_direct_composition_allowed = (
        QQuickWindow.graphicsApi() == QSGRendererInterface.GraphicsApi.OpenGL)
    manager = _WidgetGpuManager(app)
    app._widget_gpu_manager = manager
    app.installEventFilter(manager)
    for widget in app.allWidgets():
        if manager._eligible(widget):
            manager._queue(widget)
