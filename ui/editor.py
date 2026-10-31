# SPDX-License-Identifier: MPL-2.0
"""Lazy Monaco session for the Qt Quick WebEngineView."""
import os

from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl

from core.editor_document import EditorBridge
from core.editor_html import _build_html
from core.monaco_assets import resolve_monaco_vs


class EditorSession(QObject):
    activeChanged = Signal()
    reloadRequested = Signal()
    saved = Signal(str)
    warningRaised = Signal(str, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bridge = EditorBridge(self)
        self._active = False
        self._bridge.viewRequested.connect(self.prepare)
        self._bridge.fileSaved.connect(self.saved)
        self._bridge.warningRaised.connect(self.warningRaised)

    @Property(QObject, constant=True)
    def bridge(self):
        return self._bridge

    @Property(bool, notify=activeChanged)
    def active(self):
        return self._active

    @Property(str, notify=reloadRequested)
    def html(self):
        return _build_html()

    @Property(QUrl, constant=True)
    def baseUrl(self):
        return QUrl.fromLocalFile(str(resolve_monaco_vs().parent) + os.sep)

    @Slot()
    def prepare(self):
        if not self._active:
            self._queue_buffers()
            self._active = True
            self.activeChanged.emit()

    @Slot()
    def suspend(self):
        if self._active:
            self._bridge._ready = False
            self._active = False
            self.activeChanged.emit()

    @Slot(str, result=bool)
    def openFile(self, path):
        tab=self._bridge._tabs.get(path)
        if tab and tab.get('virtual'):
            self._bridge._active=path
            self._bridge._emit_or_queue_file_loaded(path,tab['content'],tab['language'],self._bridge._resolve_icon_b64(path),bool(tab.get('read_only')))
            return True
        return self._bridge.open_file(path)

    @Slot(str)
    def closeFile(self, path):
        self._bridge.close_file(path)

    @Slot()
    def saveCurrent(self):
        path = self._bridge.get_active_file()
        tab = self._bridge._tabs.get(path)
        if tab is not None:
            self._bridge.save_file(path, tab["content"])

    @Slot()
    def reloadTheme(self):
        # Preserve unsaved buffers while the web presentation is recreated.
        self._queue_buffers()
        if self._active:
            self.reloadRequested.emit()

    def _queue_buffers(self):
        self._bridge._ready = False
        paths = list(self._bridge._tabs)
        active = self._bridge.get_active_file()
        if active in paths:
            paths.remove(active)
            paths.append(active)
        self._bridge._pending_emits = [
            (path, tab["content"], tab["language"],
             self._bridge._resolve_icon_b64(path), bool(tab.get("read_only")))
            for path in paths for tab in (self._bridge._tabs[path],)
        ]
