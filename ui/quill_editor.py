# SPDX-License-Identifier: MPL-2.0
"""Qt Quick integration for Quill's Python-hosted Rust editor."""
import hashlib
import math
import os
import time
import weakref

from PySide6.QtCore import Property, QObject, QPointF, QRectF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFontMetricsF, QKeySequence, QPainter, QTextCharFormat, QTextLayout
from shiboken6 import isValid as _qt_is_valid
from quill_native._engine import Document
from quill_native.editor import NativeEditor as QuillEditor, utf16_length


class NativeEditor(QuillEditor):
    sessionChanged = Signal()
    smoothScrollingChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session = None
        self._session_connections = []
        self._read_only = False
        self._virtual_read_only = {}
        self._virtual_saved_hashes = {}
        self._virtual_dirty_cache = {}
        self._animations_enabled = True
        self._smooth_active = False
        self._scroll_dir = 0
        self._driven_by_frame = False
        self._last_wheel_at = 0.0
        self._wheel_streak = 0
        # PySide can lose bound-method connections made during QML type
        # construction. Route them through plain callables so timers and
        # geometry/focus notifications continue to reach the Python host.
        editor = weakref.ref(self)

        def reconnect(signal, method):
            signal.disconnect()

            def invoke():
                instance = editor()
                if instance is not None and _qt_is_valid(instance):
                    getattr(instance, method)()

            signal.connect(invoke)

        for timer, method in (
            (self._prepare_timer, "_prepare"),
            (self._lex_timer, "_prepare"),
            (self._smooth_timer, "_animate_smooth_scroll"),
            (self._blink, "_blink_caret"),
            (self._drag_timer, "_autoscroll"),
            (self._minimap_timer, "_rebuild_minimap"),
        ):
            reconnect(timer.timeout, method)
        reconnect(self.activeFocusChanged, "_focus_changed")
        reconnect(self.widthChanged, "_on_geometry_changed")
        reconnect(self.heightChanged, "_on_geometry_changed")

    @Property(QObject, notify=sessionChanged)
    def session(self):
        return self._session

    @session.setter
    def session(self, value):
        if value is self._session:
            return
        self._disconnect_session()
        self._session = value
        self._connect_session()
        self.sessionChanged.emit()
        if self._session is not None and getattr(self._session, "currentPath", ""):
            self.loadPath(self._session.currentPath)

    @Property(bool, notify=QuillEditor.stateChanged)
    def readOnly(self):
        return bool(self._read_only)

    @Property(bool, notify=smoothScrollingChanged)
    def smoothScrolling(self):
        return bool(self._smooth_active)

    @Property(bool, notify=sessionChanged)
    def animationsEnabled(self):
        return bool(self._animations_enabled)

    @animationsEnabled.setter
    def animationsEnabled(self, value):
        enabled = bool(value)
        if enabled == self._animations_enabled:
            return
        self._animations_enabled = enabled
        if not enabled and self._smooth_active:
            self._set_smooth_active(False)
            self._set_scroll_y_immediate(self._target_scroll_y)

    def _set_smooth_active(self, active):
        active = bool(active)
        if active == self._smooth_active:
            return
        self._smooth_active = active
        if not active:
            self._smooth_timer.stop()
            self._scroll_dir = 0
            self._wheel_streak = 0
        self.smoothScrollingChanged.emit()

    def _set_scroll_y_immediate(self, value):
        max_scroll = max(0.0, self.totalHeight - self.height())
        clamped = max(0.0, min(float(value), max_scroll))
        if abs(clamped - self._scroll_y) > 0.1:
            self._scroll_y = clamped
            self.viewportChanged.emit()
            self._prepare(trigger_update=True)

    def _step_smooth_scroll(self, elapsed):
        max_scroll = max(0.0, self.totalHeight - self.height())
        self._target_scroll_y = max(0.0, min(float(self._target_scroll_y), max_scroll))
        remaining = self._target_scroll_y - self._scroll_y
        if abs(remaining) < 0.35:
            self._set_scroll_y_immediate(self._target_scroll_y)
            self._set_smooth_active(False)
            return
        max_speed = 16000.0
        decay = 1.0 - math.exp(-elapsed / 0.038)
        distance = min(abs(remaining), max_speed * elapsed, max(abs(remaining) * decay, 1.8))
        self._set_scroll_y_immediate(self._scroll_y + math.copysign(distance, remaining))

    @Slot(float)
    def advanceScroll(self, frame_time):
        if not self._smooth_active:
            return
        self._driven_by_frame = True
        if self._smooth_timer.isActive():
            self._smooth_timer.stop()
        elapsed = max(0.001, min(0.032, float(frame_time)))
        self._step_smooth_scroll(elapsed)

    def _animate_smooth_scroll(self):
        if self._driven_by_frame or not self._smooth_active:
            self._smooth_timer.stop()
            return
        now = time.monotonic()
        elapsed = max(0.001, min(0.032, now - self._last_scroll_time))
        self._last_scroll_time = now
        self._step_smooth_scroll(elapsed)

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1 if event.angleDelta().y() > 0 else -1)
            event.accept()
            return
        pixel = event.pixelDelta()
        if not pixel.isNull():
            self._set_smooth_active(False)
            self._target_scroll_y = max(0.0, min(max(0.0, self.totalHeight - self.height()), self._scroll_y - pixel.y()))
            self._set_scroll_y_immediate(self._target_scroll_y)
            self.scrollX -= pixel.x()
            event.accept()
            return
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.scrollX -= event.angleDelta().y() / 120.0 * 96.0
            event.accept()
            return
        notches = -event.angleDelta().y() / 120.0
        if notches:
            row_step = max(self._line_height * 4.5, 90.0)
            max_scroll = max(0.0, self.totalHeight - self.height())
            if not self._animations_enabled:
                self._set_smooth_active(False)
                self._target_scroll_y = max(0.0, min(max_scroll, self._scroll_y + notches * row_step))
                self._set_scroll_y_immediate(self._target_scroll_y)
            else:
                now = time.monotonic()
                was_active = self._smooth_active
                next_dir = 1 if notches > 0 else -1
                dt = now - self._last_wheel_at
                same_burst = next_dir == self._scroll_dir and dt <= 0.16
                if next_dir != self._scroll_dir or not was_active:
                    self._target_scroll_y = self._scroll_y
                self._wheel_streak = min(8, self._wheel_streak + 1) if same_burst else 0
                burst_mult = min(2.2, 1.0 + self._wheel_streak * 0.18) if same_burst else 1.0
                self._scroll_dir = next_dir
                self._last_wheel_at = now
                max_pending = 3600.0
                pending = max(-max_pending, min(max_pending, self._target_scroll_y - self._scroll_y + notches * row_step * burst_mult))
                self._target_scroll_y = max(0.0, min(max_scroll, self._scroll_y + pending))
                self._set_smooth_active(abs(self._target_scroll_y - self._scroll_y) > 0.1)
                if self._smooth_active:
                    # Step immediately when starting or separated by >=10ms; bursts within one frame queue into FrameAnimation.
                    if not was_active or (now - self._last_scroll_time) >= 0.010:
                        self._last_scroll_time = now
                        self._step_smooth_scroll(0.012)
                    if self._smooth_active and not self._driven_by_frame and not self._smooth_timer.isActive():
                        self._smooth_timer.setInterval(8)
                        self._smooth_timer.start()
        if event.angleDelta().x():
            self.scrollX -= event.angleDelta().x() / 120.0 * 96.0
        event.accept()

    def mousePressEvent(self, event):
        self._set_smooth_active(False)
        super().mousePressEvent(event)

    def _reset(self, path="", scroll_state=None):
        self._set_smooth_active(False)
        super()._reset(path, scroll_state=scroll_state)

    @Property(bool, notify=QuillEditor.stateChanged)
    def dirty(self):
        if not self._doc:
            return False
        if self._path and self._is_virtual_path(self._path):
            cache_key = self._virtual_cache_key(self._path)
            saved_hash = self._virtual_saved_hashes.get(cache_key)
            if saved_hash is None:
                return self._doc.dirty()
            revision = self._doc.revision()
            cached = self._virtual_dirty_cache.get(cache_key)
            if cached is not None and cached[0] == revision:
                return cached[1]
            is_dirty = hashlib.md5(self.getText().encode("utf-8", errors="ignore")).hexdigest() != saved_hash
            self._virtual_dirty_cache[cache_key] = (revision, is_dirty)
            return is_dirty
        return self._doc.dirty()

    @staticmethod
    def _is_virtual_scheme(path):
        return isinstance(path, str) and path.startswith("mount://")

    @staticmethod
    def _virtual_cache_key(path):
        return "virtual:" + path

    @staticmethod
    def _doc_text(doc):
        return "\n".join(doc.line(i) for i in range(doc.line_count())) if doc else ""

    def _bridge(self):
        return getattr(self._session, "bridge", None) if self._session is not None else None

    def _virtual_tab(self, path):
        bridge = self._bridge()
        if not path or bridge is None:
            return None
        key, tab = bridge._find_tab(path)
        if tab and tab.get("virtual"):
            return key, tab
        return None

    def _is_virtual_path(self, path):
        if not path:
            return False
        if self._is_virtual_scheme(path) or self._virtual_tab(path) is not None:
            return True
        return self._virtual_cache_key(path) in self._docs

    def _save_current_scroll_state(self):
        if not self._path or not self._doc:
            return
        key = self._virtual_cache_key(self._path) if self._is_virtual_path(self._path) else os.path.normcase(self._path)
        self._scroll_states[key] = (self._scroll_y, self._scroll_x, *self._doc.caret())

    def _disconnect_session(self):
        for signal, slot in self._session_connections:
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self._session_connections.clear()

    def _connect_session(self):
        bridge = self._bridge()
        if bridge is None:
            return
        editor_ref = weakref.ref(self)

        def bind(signal, method_name):
            def handler(*args):
                instance = editor_ref()
                if instance is not None:
                    getattr(instance, method_name)(*args)

            signal.connect(handler)
            self._session_connections.append((signal, handler))

        bind(bridge.fileLoaded, "_on_bridge_file_loaded")
        bind(bridge.fileReloaded, "_on_bridge_file_reloaded")
        bind(bridge.readOnlyChanged, "_on_bridge_read_only_changed")
        bind(bridge.documentChanged, "_on_bridge_document_changed")
        bind(bridge.documentSaved, "_on_bridge_document_saved")
        bind(bridge.documentSaveFailed, "_on_bridge_document_save_failed")

    def _on_bridge_file_reloaded(self, filepath, content):
        norm = os.path.normpath(filepath) if not self._is_virtual_scheme(filepath) else filepath
        cache_key = self._virtual_cache_key(norm)
        self._docs.pop(cache_key, None)
        if self._path and os.path.normcase(os.path.normpath(self._path)) == os.path.normcase(norm):
            found = self._virtual_tab(filepath)
            if found is not None:
                self._load_virtual_tab(found[0], found[1], force_reload=True)
            else:
                bridge = self._bridge()
                _, tab = bridge._find_tab(norm) if bridge is not None else (None, None)
                self._read_only = bool(tab.get("read_only", False)) if tab else False
                super().loadPath(norm)
                self.stateChanged.emit()

    def _on_bridge_read_only_changed(self, filepath, read_only):
        found = self._virtual_tab(filepath)
        norm = found[0] if found else (os.path.normpath(filepath) if not self._is_virtual_scheme(filepath) else filepath)
        cache_key = self._virtual_cache_key(norm)
        self._virtual_read_only[cache_key] = bool(read_only)
        if self._path and os.path.normcase(os.path.normpath(self._path)) == os.path.normcase(norm):
            self._read_only = bool(read_only)
            self.stateChanged.emit()

    def _on_bridge_file_loaded(self, filepath, content, _lang, _icon, read_only):
        found = self._virtual_tab(filepath)
        if found is None:
            norm = os.path.normpath(filepath) if not self._is_virtual_scheme(filepath) else filepath
            if self._path and os.path.normcase(os.path.normpath(self._path)) == os.path.normcase(norm):
                locked = bool(read_only)
                if self._read_only != locked:
                    self._read_only = locked
                    self.stateChanged.emit()
            return
        norm_path, tab = found
        cache_key = self._virtual_cache_key(norm_path)
        self._virtual_read_only[cache_key] = bool(read_only)
        self._virtual_saved_hashes.pop(cache_key, None)
        self._virtual_dirty_cache.pop(cache_key, None)
        self._docs.pop(cache_key, None)
        if self._path == norm_path:
            self._load_virtual_tab(norm_path, tab, force_reload=True)

    def _on_bridge_document_changed(self, filepath):
        found = self._virtual_tab(filepath)
        if found is None:
            return
        norm_path, tab = found
        if norm_path == self._path:
            return
        cache_key = self._virtual_cache_key(norm_path)
        cached_doc = self._docs.get(cache_key)
        if cached_doc is not None and self._doc_text(cached_doc) != tab.get("content", ""):
            self._docs.pop(cache_key, None)
            self._virtual_dirty_cache.pop(cache_key, None)

    def _on_bridge_document_saved(self, filepath):
        if not self._is_virtual_path(filepath):
            return
        found = self._virtual_tab(filepath)
        norm_path = found[0] if found else filepath
        tab = found[1] if found else None
        cache_key = self._virtual_cache_key(norm_path)
        if tab is not None:
            saved_hash = tab.get("hash") or hashlib.md5(tab.get("content", "").encode("utf-8", errors="ignore")).hexdigest()
            self._virtual_saved_hashes[cache_key] = saved_hash
        elif cache_key in self._docs:
            self._virtual_saved_hashes[cache_key] = hashlib.md5(
                self._doc_text(self._docs[cache_key]).encode("utf-8", errors="ignore")
            ).hexdigest()
        self._virtual_dirty_cache.pop(cache_key, None)
        if self._path == norm_path:
            self.stateChanged.emit()
            self.notice.emit("Saved")

    def _on_bridge_document_save_failed(self, filepath, message):
        if self._is_virtual_path(filepath) and self._path == filepath:
            self.errorOccurred.emit(str(message))

    def _load_virtual_tab(self, norm_path, tab, *, force_reload=False):
        cache_key = self._virtual_cache_key(norm_path)
        if not force_reload and self._path == norm_path:
            return
        self._save_current_scroll_state()
        self._read_only = bool(tab.get("read_only", False))
        self._virtual_read_only[cache_key] = self._read_only
        if not force_reload and cache_key in self._docs:
            self._doc = self._docs[cache_key]
            prev_state = self._scroll_states.get(cache_key)
            self._reset(norm_path, scroll_state=prev_state)
            return
        doc = Document(tab.get("content", ""), norm_path)
        self._docs[cache_key] = doc
        self._doc = doc
        self._virtual_dirty_cache.pop(cache_key, None)
        self._reset(norm_path)

    @Slot(str)
    def loadPath(self, path):
        if not path:
            return
        found = self._virtual_tab(path)
        if found is not None or self._is_virtual_scheme(path):
            if found is None:
                return
            norm_path, tab = found
            self._load_virtual_tab(norm_path, tab)
            return
        if self._path and self._is_virtual_path(self._path):
            self._save_current_scroll_state()
        bridge = self._bridge()
        _, tab = bridge._find_tab(path) if bridge is not None else (None, None)
        self._read_only = bool(tab.get("read_only", False)) if tab else False
        super().loadPath(path)
        self.stateChanged.emit()

    @Slot(str)
    def closeFile(self, path):
        if not path:
            return
        if self._is_virtual_path(path):
            found = self._virtual_tab(path)
            norm_path = found[0] if found else path
            cache_key = self._virtual_cache_key(norm_path)
            self._docs.pop(cache_key, None)
            self._scroll_states.pop(cache_key, None)
            self._virtual_read_only.pop(cache_key, None)
            self._virtual_saved_hashes.pop(cache_key, None)
            self._virtual_dirty_cache.pop(cache_key, None)
            if self._path == norm_path:
                self._path = ""
                self._read_only = False
                self._doc = Document()
                self._reset("")
            return
        super().closeFile(path)

    @Slot(result=bool)
    def saveCurrent(self):
        if self._read_only:
            self.errorOccurred.emit("This document is read-only")
            return False
        if self._is_virtual_path(self._path):
            bridge = self._bridge()
            if bridge is not None:
                bridge.save_file(self._path, self.getText())
            return False
        super().saveCurrent()
        return not self.dirty

    def keyPressEvent(self, event):
        if not self._read_only:
            super().keyPressEvent(event)
            return
        if not self.hasActiveFocus():
            event.ignore()
            return
        key, modifiers = event.key(), event.modifiers()
        ctrl = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        if (
            event.matches(QKeySequence.StandardKey.Copy)
            or event.matches(QKeySequence.StandardKey.SelectAll)
            or (ctrl and key == Qt.Key.Key_F)
            or key in (
                Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
                Qt.Key.Key_Home, Qt.Key.Key_End, Qt.Key.Key_PageUp, Qt.Key.Key_PageDown,
                Qt.Key.Key_Escape,
            )
        ):
            super().keyPressEvent(event)
            return
        event.ignore()

    @Slot()
    def cut(self):
        if self._read_only:
            self.copy()
            return
        super().cut()

    @Slot()
    def paste(self):
        if self._read_only:
            return
        super().paste()

    @Slot()
    def undo(self):
        if self._read_only:
            return
        super().undo()

    @Slot()
    def redo(self):
        if self._read_only:
            return
        super().redo()

    @Slot(str)
    def setText(self, text):
        if self._read_only:
            return
        super().setText(text)

    @Slot(str, str, result=bool)
    def applyEdit(self, before, after):
        if self._read_only:
            return False
        return super().applyEdit(before, after)

    @Slot(str, str, result=bool)
    def replaceNext(self, needle, replacement):
        if self._read_only:
            return False
        return super().replaceNext(needle, replacement)

    @Slot(str, str, result=int)
    def replaceAll(self, needle, replacement):
        if self._read_only:
            return 0
        return super().replaceAll(needle, replacement)

    def inputMethodEvent(self, event):
        if self._read_only:
            event.accept()
            return
        super().inputMethodEvent(event)

    def paint(self, painter: QPainter):
        if not self._minimap_enabled or self.width() < 300:
            super().paint(painter)
            return
        self._minimap_enabled = False
        try:
            super().paint(painter)
        finally:
            self._minimap_enabled = True
        self._paint_minimap(painter)

    def _paint_minimap(self, painter: QPainter):
        minimap_w = self._minimap_width
        minimap_x = self.width() - minimap_w

        if self._minimap_image is None or self._minimap_dirty:
            self._rebuild_minimap()

        # Seamless 50% translucent overlay + slight dark tint without any divider border.
        bg_col = QColor(self._fill_color)
        bg_col.setAlpha(128)
        painter.fillRect(QRectF(minimap_x, 0, minimap_w, self.height()), bg_col)
        tint_col = QColor(0, 0, 0, 14 if self._is_light else 26)
        painter.fillRect(QRectF(minimap_x, 0, minimap_w, self.height()), tint_col)

        total_lines = len(self._visible_rows_map) if self._visible_rows_map else self.lineCount
        line_h = self._minimap_line_h
        total_minimap_h = max(1.0, total_lines * line_h)
        max_editor_scroll = max(0.0, self.totalHeight - self.height())
        scroll_ratio = (self._scroll_y / max_editor_scroll) if max_editor_scroll > 0 else 0.0

        top_vi = self._scroll_y / max(1.0, self._line_height)
        vis_in_view = self.height() / max(1.0, self._line_height)

        if total_minimap_h <= self.height():
            minimap_scroll_y = 0.0
            slider_y = top_vi * line_h
            slider_h = max(20.0, min(float(self.height()) - slider_y, vis_in_view * line_h))
        else:
            visible_ratio = min(1.0, self.height() / max(self.height(), self.totalHeight))
            slider_h = max(24.0, visible_ratio * self.height())
            slider_travel = self.height() - slider_h
            slider_y = scroll_ratio * slider_travel
            line_y = top_vi * line_h
            minimap_scroll_y = max(0.0, min(total_minimap_h - self.height(), line_y - slider_y))

        if self._minimap_image is not None and not self._minimap_image.isNull():
            src_y = minimap_scroll_y
            src_h = min(float(self.height()), float(self._minimap_image.height()) - src_y)
            if src_h > 0:
                dest_rect = QRectF(minimap_x, 0, minimap_w, src_h)
                src_rect = QRectF(0, src_y, minimap_w, src_h)
                painter.drawImage(dest_rect, self._minimap_image, src_rect)

        is_active_slider = self._hover_minimap or self._dragging_minimap
        if self._is_light:
            slider_bg = QColor(0, 0, 0, 32 if is_active_slider else 18)
        else:
            slider_bg = QColor(255, 255, 255, 36 if is_active_slider else 18)

        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(slider_bg)
        painter.drawRect(QRectF(minimap_x, slider_y, minimap_w, slider_h))
        painter.restore()


class NativeDiffEditor(NativeEditor):
    """Read-only side-by-side diff view powered by the Rust Quill document engine."""

    diffChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._minimap_enabled = False
        self._font.setPixelSize(12)
        self._metrics = QFontMetricsF(self._font)
        self._line_height = 20.0
        self._gutter = 48.0
        self._diff_marks = []
        self._diff_line_numbers = []
        self._diff_markers = []
        self._diff_binary = False
        self._diff_data = {}
        self._syncing_viewport = False

    @Property(bool, constant=True)
    def readOnly(self):
        return True

    @Property("QVariantList", notify=diffChanged)
    def diffMarkers(self):
        return self._diff_markers

    @Property("QVariantMap", notify=diffChanged)
    def diffData(self):
        return self._diff_data

    @diffData.setter
    def diffData(self, payload):
        data = dict(payload or {})
        self._diff_data = data
        text = str(data.get("text") or "")
        marks = list(data.get("marks") or [])
        line_numbers = list(data.get("lineNumbers") or [])
        path = str(data.get("path") or "")
        is_binary = bool(data.get("binary", False))
        self.loadDiff(text, marks, line_numbers, path, is_binary)
        self.diffChanged.emit()

    @Slot(str, "QVariantList", "QVariantList", str, bool)
    def loadDiff(self, text, marks, line_numbers=None, path="", is_binary=False):
        self._smooth_timer.stop()
        self._scroll_x = 0.0
        self._scroll_y = 0.0
        self._target_scroll_y = 0.0
        self._max_width = 0.0
        self._diff_marks = [str(m) for m in (marks or [])]
        self._diff_line_numbers = [str(n) for n in (line_numbers or [])]
        self._diff_binary = bool(is_binary)
        self._diff_markers = self._build_diff_markers(self._diff_marks)
        self._doc = Document(text, "" if is_binary else path)
        self._layouts.clear()
        self._folded.clear()
        self._hidden_rows.clear()
        self._fold_ranges.clear()
        self._visible_rows_map = list(range(self._doc.line_count()))
        self._row_to_visual = {r: r for r in self._visible_rows_map}
        self._prepare(trigger_update=True)
        self.viewportChanged.emit()
        self.stateChanged.emit()

    @Slot(float, float)
    def syncViewport(self, scroll_x, scroll_y):
        if self._syncing_viewport:
            return
        self._syncing_viewport = True
        try:
            self._smooth_timer.stop()
            max_y = max(0.0, self.totalHeight - self.height())
            max_x = max(0.0, self.totalWidth - self.width())
            nx = max(0.0, min(float(scroll_x), max_x))
            ny = max(0.0, min(float(scroll_y), max_y))
            changed = abs(nx - self._scroll_x) > 0.1 or abs(ny - self._scroll_y) > 0.1
            self._scroll_x = nx
            self._scroll_y = ny
            self._target_scroll_y = ny
            if changed:
                self._prepare(trigger_update=True)
                self.viewportChanged.emit()
        finally:
            self._syncing_viewport = False

    def _compute_folding(self):
        count = self._doc.line_count() if self._doc else 0
        if len(self._visible_rows_map) != count:
            self._visible_rows_map = list(range(count))
            self._row_to_visual = {r: r for r in self._visible_rows_map}
        self._fold_ranges = {}
        self._folded = set()
        self._hidden_rows = set()
        self._folds_dirty = False

    def _prepare(self, trigger_update=True):
        super()._prepare(trigger_update=trigger_update)
        if self.width() > 0 and self.height() > 0:
            max_label = max((len(n) for n in self._diff_line_numbers if n), default=len(str(self.lineCount)))
            sample = "9" * max(2, max_label)
            self._gutter = max(44.0, self._metrics.horizontalAdvance(sample) + 18.0)

    def keyPressEvent(self, event):
        if not self.hasActiveFocus():
            event.ignore()
            return
        key, modifiers = event.key(), event.modifiers()
        shift = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        ctrl = bool(modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier))
        if event.matches(QKeySequence.StandardKey.Copy):
            self.copy()
        elif event.matches(QKeySequence.StandardKey.SelectAll):
            self._doc.select_all()
            self._changed()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Home, Qt.Key.Key_End):
            direction = {
                Qt.Key.Key_Left: "left",
                Qt.Key.Key_Right: "right",
                Qt.Key.Key_Up: "up",
                Qt.Key.Key_Down: "down",
                Qt.Key.Key_Home: "home",
                Qt.Key.Key_End: "end",
            }[key]
            if ctrl and direction in ("left", "right"):
                direction = "word_" + direction
            elif ctrl and direction in ("home", "end"):
                direction = "doc_" + direction
            self._doc.move_cursor(direction, shift)
            self._changed()
        elif key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            row, column = self._doc.caret()
            step = max(1, int(self.height() // self._line_height) - 2)
            target_row = max(0, min(max(0, self.lineCount - 1), row + (-step if key == Qt.Key.Key_PageUp else step)))
            self._doc.set_cursor(target_row, column, shift)
            self._changed()
        else:
            event.ignore()
            return
        event.accept()

    def cut(self):
        self.copy()

    def paste(self):
        pass

    def undo(self):
        pass

    def redo(self):
        pass

    def inputMethodEvent(self, event):
        event.accept()

    def paint(self, painter: QPainter):
        self._paint_count += 1
        if not self._rows and self.width() > 0 and self.height() > 0:
            self._prepare(trigger_update=False)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.fillRect(self.boundingRect(), self._fill_color)
        painter.setFont(self._font)

        has_selection = self._doc.has_selection()
        ar, ac, br, bc = self._doc.selection()
        origin_x = self._gutter + 10.0 - self._scroll_x
        content_w = max(0.0, self.width() - self._gutter)

        changed_bg = QColor("#fde8e8") if self._is_light else QColor("#3d1010")
        changed_fg = QColor("#b42318") if self._is_light else QColor("#ff9090")
        changed_bar = QColor("#d92d20") if self._is_light else QColor("#f85149")
        ghost_bg = QColor("#f6ebed") if self._is_light else QColor("#241114")

        for row, vi, text, layout in self._rows:
            y = vi * self._line_height - self._scroll_y
            mark = self._diff_marks[row] if row < len(self._diff_marks) else "normal"
            if mark == "changed":
                painter.fillRect(QRectF(0, y, self.width(), self._line_height), changed_bg)
            elif mark == "ghost":
                painter.fillRect(QRectF(0, y, self.width(), self._line_height), ghost_bg)

            if mark == "ghost":
                continue

            painter.save()
            painter.setClipRect(QRectF(self._gutter, 0, content_w, self.height()))

            selections = []
            if has_selection and ar <= row <= br:
                start = ac if row == ar else 0
                end = bc if row == br else utf16_length(text) + 1
                region = QTextLayout.FormatRange()
                region.start = start
                region.length = max(0, end - start)
                region.format = QTextCharFormat()
                region.format.setBackground(self._selection_color)
                if not self._is_light:
                    region.format.setForeground(QColor("#ffffff"))
                selections.append(region)
                if row < br:
                    painter.fillRect(
                        QRectF(origin_x + self._cursor_x(layout, start), y, self.width(), self._line_height),
                        self._selection_color,
                    )

            is_plain_changed = mark == "changed" and (self._diff_binary or self._doc.language() == "Plain Text")
            painter.setPen(changed_fg if is_plain_changed else self._text_color)
            text_y = y + (self._line_height - self._metrics.height()) / 2.0
            layout.draw(painter, QPointF(origin_x, text_y), selections)
            painter.restore()

        gutter_bg = self._gutter_color if self._gutter_color != self._fill_color else self._fill_color
        painter.fillRect(QRectF(0, 0, self._gutter, self.height()), gutter_bg)

        for row, vi, _, _ in self._rows:
            y = vi * self._line_height - self._scroll_y
            mark = self._diff_marks[row] if row < len(self._diff_marks) else "normal"
            if mark == "changed":
                painter.fillRect(QRectF(0, y, self._gutter, self._line_height), changed_bg)
                painter.fillRect(QRectF(0, y, 3.0, self._line_height), changed_bar)
            elif mark == "ghost":
                painter.fillRect(QRectF(0, y, self._gutter, self._line_height), ghost_bg)
                continue

            label = self._diff_line_numbers[row] if row < len(self._diff_line_numbers) else str(row + 1)
            if label:
                painter.setPen(changed_fg if mark == "changed" else self._line_number_color)
                painter.drawText(
                    QRectF(4, y, self._gutter - 12, self._line_height),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                    label,
                )

        painter.setPen(self._gutter_divider_color)
        painter.drawLine(QPointF(self._gutter - 1, 0), QPointF(self._gutter - 1, self.height()))

    @staticmethod
    def _build_diff_markers(marks):
        markers = []
        start = None
        for idx, mark in enumerate(marks):
            if mark != "normal":
                if start is None:
                    start = idx
            elif start is not None:
                markers.append({"row": start, "count": idx - start})
                start = None
        if start is not None:
            markers.append({"row": start, "count": len(marks) - start})
        if len(markers) > 160:
            step = max(1, len(markers) // 160)
            markers = markers[::step]
        return markers
