# SPDX-License-Identifier: MPL-2.0
import os

from PySide6.QtWidgets import QWidget, QVBoxLayout, QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineSettings
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtCore import Qt, QObject, Slot, Signal, QUrl, QTimer
from PySide6.QtGui import QKeySequence, QShortcut, QColor

from main_ext.class_ThemeUi import theme as _th
from core.monaco_assets import resolve_monaco_vs, monaco_vs_url

print("[MAIN STAGE] WEB_EDITOR_PANEL")


from core.editor_document import (
    EditorBridge, _detect_language, _detect_eol, _decode_bytes,
    read_text_meta, encode_text_meta, _EOL_LABEL,
)
from core.editor_html import _build_html


class WebEditorPanel(QWidget):


    fileSaved  = Signal(str)
    tabOpened  = Signal(str)
    allClosed  = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

        # Cegah background flash putih saat show/hide
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Bridge
        self.bridge = EditorBridge(self)
        self.bridge.warningRaised.connect(self._show_document_warning)
        self.bridge.fileSaved.connect(self._on_saved)
        self.bridge.fileLoaded.connect(lambda fp, *_: self.tabOpened.emit(fp))
        self.bridge.viewRequested.connect(self._ensure_view)
        self.bridge.editorReady.connect(lambda: print("[WebEditorPanel] Ready"))
        self._view = None
        self.channel = None
        self._view_layout = layout

    def _show_document_warning(self, title, message):
        from main_ext.class_AppDialog import AppDialog
        AppDialog.warning(self.window(), title, message)

    @property
    def view(self):

        return self._ensure_view()

    def view_initialized(self):
        return self._view is not None

    def is_ready(self) -> bool:
        return bool(getattr(self.bridge, "_ready", False))

    def prepare(self):

        self._ensure_view()

    def _ensure_view(self):
        if self._view is not None:
            return self._view

        # WebView
        self._view = QWebEngineView(self)

        # Cegah background putih / flicker saat load dan resize
        self.view.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.view.setStyleSheet(f"background: {_th.BG3};")
        # Set page background color sebelum load HTML — eliminasi flash putih
        self.view.page().setBackgroundColor(QColor(_th.BG3))

        s = self.view.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)

        # Channel
        self.channel = QWebChannel(self.view.page())
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        self.bridge._ready = False
        base_dir = str(resolve_monaco_vs().parent) + os.sep
        self.view.setHtml(_build_html(), QUrl.fromLocalFile(base_dir))
        self._view_layout.addWidget(self.view)
        return self._view

    # Public API

    def open_file(self, filepath: str) -> bool:

        return self.bridge.open_file(filepath)

    def close_file(self, filepath: str):

        self.bridge.close_file(filepath)
        if self.bridge.tab_count() == 0:
            self.allClosed.emit()

    def save_current(self):

        fp = self.bridge.get_active_file()
        if fp and fp in self.bridge._tabs:
            content = self.bridge._tabs[fp]["content"]
            self.bridge.save_file(fp, content)

    def tab_count(self) -> int:

        return self.bridge.tab_count()

    def has_unsaved(self) -> bool:
        return self.bridge.has_unsaved()

    def is_unsaved(self, filepath: str) -> bool:
        return self.bridge.is_unsaved(filepath)

    def get_open_files(self) -> list:
        return self.bridge.get_open_files()

    def get_active_file(self):
        return self.bridge.get_active_file()

    # Callbacks

    def _on_saved(self, filepath: str):
        self.fileSaved.emit(filepath)
        # Trigger JS flash via evaluate
        self.view.page().runJavaScript(
            "window.dispatchEvent(new Event('bridge-saved'));"
        )
