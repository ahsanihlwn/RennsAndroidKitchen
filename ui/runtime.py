# SPDX-License-Identifier: MPL-2.0
"""Load native QML windows and original icon assets."""
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit, parse_qs

from PySide6.QtCore import QUrl, Qt, QObject, Property, Signal, Slot, QTimer, QPointF, QMetaObject, QEvent, QByteArray
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtQuickControls2 import QQuickStyle
try:
    from shiboken6 import isValid as _is_valid
except ImportError:
    def _is_valid(obj):
        return obj is not None



_input_regions_registered = False


def initialize_quick_runtime(*, webengine=False):
    """Call before QGuiApplication when the window contains web panes.

    Keep Qt's platform graphics backend (and explicit QSG_RHI_BACKEND choices).
    Forcing desktop OpenGL crashed the AMD driver during validation teardown.
    """
    # Every control has a custom scene-graph surface. Windows' native Controls
    # style rejects custom backgrounds/content, even on otherwise hidden pages.
    QQuickStyle.setStyle("Basic")
    global _input_regions_registered
    if not _input_regions_registered:
        from ui.input_regions import ContentInputRegion
        qmlRegisterType(ContentInputRegion, "Renns.Input", 1, 0, "ContentInputRegion")
        _input_regions_registered = True
    if not globals().get("_quill_registered", False):
        try:
            from ui.quill_editor import NativeEditor, NativeDiffEditor
            qmlRegisterType(NativeEditor, "Quill.Native", 1, 0, "NativeEditor")
            qmlRegisterType(NativeDiffEditor, "Quill.Native", 1, 0, "NativeDiffEditor")
            globals()["_quill_registered"] = True
        except Exception:
            pass
    if not globals().get("_qtterm_registered", False):
        try:
            from core.qtterm import register_qml_types as register_qtterm_types
            register_qtterm_types()
            globals()["_qtterm_registered"] = True
        except Exception as e:
            print(f"[runtime] WARNING: Failed to register QtTerm: {e}")


class WindowLifetime(QObject):
    """Release shared scene graph images before closing a window's web sessions.

    Call close() while the GUI event loop is running, then dispose of the QML
    engine after finished. Sessions retain their documents until their owner
    decides to discard them.
    """
    finished = Signal()

    def __init__(self, window, sessions=(), parent=None):
        super().__init__(parent)
        self._window = window
        self._sessions = tuple(sessions)
        self._closing = False
        self._released = False
        window.sceneGraphInvalidated.connect(self._release_sessions, Qt.QueuedConnection)

    @Slot()
    def close(self):
        if self._closing:
            return
        self._closing = True
        initialized = self._window.isSceneGraphInitialized()
        self._window.setPersistentSceneGraph(False)
        self._window.setPersistentGraphics(False)
        self._window.hide()
        self._window.releaseResources()
        if not initialized:
            QTimer.singleShot(0, self._release_sessions)

    @Slot()
    def _release_sessions(self):
        if not self._closing or self._released:
            return
        self._released = True
        for session in self._sessions:
            if hasattr(session, "suspend"):
                session.suspend()
            else:
                session.stop()
        QTimer.singleShot(0, self._finish)

    @Slot()
    def _finish(self):
        self._window.close()
        self.finished.emit()


class FirstFramePresenter(QObject):
    """Expose the composed scene after its first swap, never a blank surface."""
    presented = Signal()

    def __init__(self,window):
        super().__init__(window)
        self.window=window
        self.ready=False
        window.setOpacity(0)
        window.frameSwapped.connect(self._frame_ready,Qt.QueuedConnection)

    @Slot()
    def _frame_ready(self):
        if self.ready or not self.window.isVisible():return
        self.ready=True
        self.window.frameSwapped.disconnect(self._frame_ready)
        self.window.setProperty('firstFrameReady',True)
        self.window.setOpacity(1)
        self.presented.emit()


class IconProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self._svg_cache = {}

    def requestImage(self, identifier, size, requested_size):
        from icons.handler import icon
        from PySide6.QtSvg import QSvgRenderer
        from main_ext.class_ThemeUi import theme
        parsed = urlsplit(identifier)
        path = icon(unquote(parsed.path))
        width = requested_size.width() if requested_size.width() > 0 else 24
        height = requested_size.height() if requested_size.height() > 0 else 24
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHints(QPainter.Antialiasing|QPainter.SmoothPixmapTransform|QPainter.TextAntialiasing)

        query = parse_qs(parsed.query)
        color_tint = query.get("color", [""])[0] or query.get("tint", [""])[0]

        if path.lower().endswith(".svg"):
            theme_mode = query.get("theme", [""])[0]
            is_light = (theme_mode == "light") if theme_mode else theme._palette.get("_is_light", False)
            fg = query.get("fg", [""])[0] or (getattr(theme, "FG", "#24292f") if is_light else getattr(theme, "FG", "#c9d1d9"))
            accent = query.get("accent", [""])[0] or (getattr(theme, "accent_hex", "") or getattr(theme, "FG_BLUE", "#3ecf8e"))

            cache_key = (path, is_light, fg, accent)
            svg_bytes = self._svg_cache.get(cache_key)
            if svg_bytes is None:
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        raw_svg = f.read()

                    has_ui_tokens = any(tok in raw_svg for tok in ("#D7E3FF", "#d7e3ff", "#8AB4F8", "#8ab4f8", ".main", ".accent"))
                    has_white_stroke = any(tok in raw_svg for tok in ('stroke="#FFFFFF"', 'stroke="#ffffff"', 'fill="#FFFFFF"', 'fill="#ffffff"', 'fill="#fff"'))

                    if has_ui_tokens or has_white_stroke:
                        if is_light:
                            raw_svg = re.sub(r'#D7E3FF|#d7e3ff', fg, raw_svg)
                            raw_svg = re.sub(r'(stroke|fill)="(#FFFFFF|#ffffff|#fff)"', rf'\1="{fg}"', raw_svg)
                            raw_svg = re.sub(r'#8AB4F8|#8ab4f8', accent, raw_svg)
                            raw_svg = raw_svg.replace('fill-opacity="0.10"', 'fill-opacity="0.08"')
                        else:
                            raw_svg = re.sub(r'#8AB4F8|#8ab4f8', accent, raw_svg)

                    svg_bytes = raw_svg.encode("utf-8")
                    if len(self._svg_cache) > 512:
                        self._svg_cache.clear()
                    self._svg_cache[cache_key] = svg_bytes
                except Exception:
                    svg_bytes = None

            if svg_bytes:
                QSvgRenderer(QByteArray(svg_bytes)).render(painter)
            else:
                QSvgRenderer(path).render(painter)
        else:
            raster = QImage(path).scaled(width, height, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            painter.drawImage(0, 0, raster)

        if color_tint and QColor(color_tint).isValid():
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(image.rect(), QColor(color_tint))

        painter.end()
        size.setWidth(width)
        size.setHeight(height)
        return image


class FrozenImageProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self.frames = {}

    def requestImage(self, identifier, size, requested_size):
        result = self.frames.get(identifier, QImage())
        size.setWidth(result.width()); size.setHeight(result.height())
        return result


class DialogStack(QObject):
    """Position and focus in-scene modals; backdrops never leave the GPU."""
    changed = Signal()

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        # Keep explicit owners rather than creating Python wrappers for every
        # Qt Quick internal object before the first visible launcher frame.
        dialogs = owner.property("managedDialogs")
        initial = dialogs.toVariant() if hasattr(dialogs, "toVariant") else list(dialogs)
        self.dialogs = []
        self.stack = []
        self.popups = []
        self.serial = 0
        self._closed = False
        owner.installEventFilter(self)
        owner.setProperty("dialogStack", self)
        for dialog in initial:
            self.registerDialog(dialog)
        owner.managedDialogsChanged.connect(self._register_managed_dialogs)

    @Slot()
    def _register_managed_dialogs(self):
        dialogs = self.owner.property("managedDialogs")
        dialogs = dialogs.toVariant() if hasattr(dialogs, "toVariant") else list(dialogs)
        for dialog in dialogs:
            self.registerDialog(dialog)

    @Property(bool, notify=changed)
    def modalActive(self):
        return any(_is_valid(dialog) and dialog.property("presented") and dialog.property("modal") for dialog in self.stack)

    @Property("QVariantList", notify=changed)
    def activeDialogs(self):
        return [dialog for dialog in self.stack if _is_valid(dialog)]

    @Slot(QObject)
    def registerDialog(self, dialog):
        if self._closed or not _is_valid(dialog) or dialog in self.dialogs:
            return
        self.dialogs.append(dialog)
        dialog.prepareRequested.connect(lambda dialog=dialog: self.prepare(dialog))
        dialog.dismissed.connect(lambda dialog=dialog: self.release(dialog))

    @Slot(QObject)
    def unregisterDialog(self, dialog):
        if self._closed:
            return
        if dialog in self.dialogs:
            self.dialogs.remove(dialog)
        self.release(dialog)

    def eventFilter(self, window, event):
        if self.stack and event.type() in (QEvent.ShortcutOverride,QEvent.KeyPress) and event.key()==Qt.Key_Escape:
            if any(_is_valid(popup) and popup.property('visible') for popup in self.popups):return False
            event.accept()
            if event.type()==QEvent.KeyPress and _is_valid(self.stack[-1]):
                QMetaObject.invokeMethod(self.stack[-1], "dismiss")
            return True
        return False

    @Slot(QObject)
    def registerPopup(self,popup):
        if not self._closed and _is_valid(popup) and popup not in self.popups:self.popups.append(popup)

    @Slot(QObject)
    def unregisterPopup(self,popup):
        if popup in self.popups:self.popups.remove(popup)

    def prepare(self, dialog):
        if self._closed or not _is_valid(dialog):
            return
        self.release(dialog)
        backdrop=self.stack[-1].property('backdropSurface') if (self.stack and _is_valid(self.stack[-1])) else None
        self.stack.append(dialog)
        self.serial += 1
        dialog.setProperty("z", 100+self.serial)
        dialog.setProperty("backdropSource", backdrop or self.owner.property("backdropSurface"))
        self._focus()
        if _is_valid(self.owner):
            self.owner.requestActivate()

    def _focus(self):
        alive = []
        self.stack = [d for d in self.stack if _is_valid(d)]
        top = self.stack[-1] if self.stack else None
        for dialog in self.dialogs:
            if _is_valid(dialog):
                dialog.setProperty("active", bool(top is not None and dialog is top))
                alive.append(dialog)
        self.dialogs = alive
        if _is_valid(self):
            self.changed.emit()

    def release(self, dialog):
        if dialog in self.stack:
            self.stack.remove(dialog)
        self._focus()

    def close(self):
        self._closed = True
        for popup in tuple(self.popups):
            if _is_valid(popup):
                QMetaObject.invokeMethod(popup,'close')
        self.popups.clear()
        for dialog in tuple(self.dialogs):
            if _is_valid(dialog):
                dialog.setProperty("finalClose", True)
                QMetaObject.invokeMethod(dialog, "close")
        self.stack.clear()
        self.changed.emit()


def create_launcher(model, *, visible=True, defer_content=False):
    initialize_quick_runtime()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("rakPreferences", model)
    diagnostics = []
    engine._diagnostics=diagnostics
    engine.warnings.connect(lambda errors: diagnostics.extend(error.toString() for error in errors))
    engine.addImageProvider("rak", IconProvider())
    engine.setInitialProperties({"uiModel": model, "visible": False,
                                 "contentReady": not defer_content,
                                 "asynchronousContent": defer_content})
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("Launcher.qml"))))
    if not engine.rootObjects():
        raise RuntimeError("Could not load the Qt Quick launcher:\n" + "\n".join(diagnostics))
    window = engine.rootObjects()[0]
    if not defer_content and not window.property("contentLoaded"):
        raise RuntimeError("Could not load the Qt Quick launcher content:\n" + "\n".join(diagnostics))
    engine._frame_presenter=FirstFramePresenter(window)
    engine._dialog_stack = DialogStack(window)
    from ui.appearance import AppearanceCaptures
    engine._appearance_captures = AppearanceCaptures(engine,window,model,())
    if visible:window.show()
    return engine, window
