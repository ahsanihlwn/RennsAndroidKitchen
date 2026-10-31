# SPDX-License-Identifier: MPL-2.0
"""Native Quick loading window with the launcher's cancellation contract."""
import os
from pathlib import Path
from PySide6.QtCore import QObject, QUrl, QPoint
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine


class QuickLoadingWindow(QObject):
    def __init__(self, engine, model, project_path, parent=None):
        super().__init__(parent)
        component = QQmlComponent(engine, QUrl.fromLocalFile(str(Path(__file__).with_name("LoadingWindow.qml"))))
        self.window = component.createWithInitialProperties({"uiModel": model, "projectName": os.path.basename(project_path)})
        if self.window is None:
            raise RuntimeError("\n".join(error.toString() for error in component.errors()))
        QQmlEngine.setObjectOwnership(self.window, QQmlEngine.CppOwnership)
        self._component = component
        from ui.runtime import FirstFramePresenter
        self._presenter=FirstFramePresenter(self.window)
        self._block_close = False
        self._on_hide_requested = None
        self._handoff_fade = None
        self.window.closeRequested.connect(self._close_requested)
        self.destroyed.connect(self.window.deleteLater)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.window.setPosition(screen.availableGeometry().center()-QPoint(160,98))

    def _close_requested(self):
        self.hide()
        if self._block_close and callable(self._on_hide_requested):
            self._on_hide_requested()
        elif not self._block_close:
            self.close()

    def show(self):
        self.window.show()

    def hide(self):
        self.window.hide()

    def isVisible(self):
        return self.window.isVisible()

    def set_status(self, text):
        self.window.setProperty("statusText", text)

    def close(self):
        if self._block_close:
            self._close_requested()
        else:
            self.window.setProperty("allowClose", True)
            self.window.close()

    def windowOpacity(self):
        return self.window.opacity()
