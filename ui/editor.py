# SPDX-License-Identifier: MPL-2.0
"""Editor session powered exclusively by Quill Native (Rust Floem-core)."""
import os
import logging

from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl

from core.editor_document import EditorBridge


class EditorSession(QObject):
    activeChanged = Signal()
    reloadRequested = Signal()
    saved = Signal(str)
    warningRaised = Signal(str, str)
    openFileRequested = Signal(str)
    closeFileRequested = Signal(str)
    saveRequested = Signal()
    currentPathChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bridge = EditorBridge(self)
        self._active = False
        self._current_path = ""
        self._backend = "quill"
        self._bridge._ready = True
        logging.info("Editor session initialized with backend: %s", self._backend)
        self._bridge.viewRequested.connect(self.prepare)
        self._bridge.fileSaved.connect(self.saved)
        self._bridge.warningRaised.connect(self.warningRaised)

    @Property(str, constant=True)
    def backend(self):
        return self._backend

    @Property(str, notify=currentPathChanged)
    def currentPath(self):
        return self._current_path

    @Property(QObject, constant=True)
    def bridge(self):
        return self._bridge

    @Property(bool, notify=activeChanged)
    def active(self):
        return self._active

    @Property(str, notify=reloadRequested)
    def html(self):
        return ""

    @Property(QUrl, constant=True)
    def baseUrl(self):
        return QUrl()

    @Slot()
    def prepare(self):
        if not self._active:
            self._active = True
            self._bridge._ready = True
            self.activeChanged.emit()

    @Slot()
    def suspend(self):
        if self._active:
            self._active = False
            self.activeChanged.emit()

    @Slot(str, result=bool)
    @Slot(str, bool, result=bool)
    def openFile(self, path, read_only=False):
        self.prepare()
        key, tab = self._bridge._find_tab(path)
        if tab and tab.get('virtual'):
            self._current_path = path
            if read_only and not tab.get('read_only'):
                self._bridge.set_read_only(key, True)
            self._bridge._active = key
            self.currentPathChanged.emit()
            self.openFileRequested.emit(key)
            return True
        result = self._bridge.open_file(path, read_only=bool(read_only))
        if not result:
            return False
        self._current_path = path
        self.currentPathChanged.emit()
        self.openFileRequested.emit(path)
        return result

    @Slot(str)
    def closeFile(self, path):
        if self._current_path == path:
            self._current_path = ""
            self.currentPathChanged.emit()
        self.closeFileRequested.emit(path)
        self._bridge.close_file(path)

    @Slot()
    def saveCurrent(self):
        self.saveRequested.emit()

    @Slot(str, bool)
    def setDirty(self, path, dirty):
        key, tab = self._bridge._find_tab(path)
        if tab is not None and tab.get("unsaved") != dirty:
            tab["unsaved"] = dirty
            if dirty:
                self._bridge.fileUnsaved.emit(key)
            else:
                self._bridge.fileSaved.emit(key)

    @Slot(str, "QVariantMap")
    def setSelection(self, path, selection):
        self._bridge.update_selection(path, selection)

    @Slot(str, str)
    def setContent(self, path, content):
        key, tab = self._bridge._find_tab(path)
        if tab is not None and tab.get("content") != content:
            if not tab.get("unsaved") and tab.get("content", "").replace("\r\n", "\n") == content:
                return
            self._bridge.on_content_changed(key, content)

    @Slot(str)
    def notifySaved(self, path):
        tab = self._bridge._tabs.get(path)
        if tab is not None:
            tab["unsaved"] = False
            self._bridge.fileSaved.emit(path)
        self.saved.emit(path)

    @Slot()
    def reloadTheme(self):
        # Native editor styling is reactive via QML bindings.
        if self._active:
            self.reloadRequested.emit()
