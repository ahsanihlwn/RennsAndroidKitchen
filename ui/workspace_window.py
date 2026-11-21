# SPDX-License-Identifier: MPL-2.0
"""Qt Quick window owner and the launcher's preparation/return contract."""
from pathlib import Path
import time

from PySide6.QtCore import QObject, Signal, Slot, QUrl, QTimer, QPoint
from PySide6.QtGui import QIcon
from PySide6.QtQml import QQmlApplicationEngine, qmlRegisterType

from icons.handler import icon
from ui.runtime import IconProvider, DialogStack, WindowLifetime
from ui.workspace import WorkspaceSession
from ui.viewers import ViewerDocument
from ui.hex_viewer import HexDocument
from ui.so_viewer import SoDocument
from ui.partition_viewer import PartitionDocument
from ui.image_viewer import ImageDocument,DocumentImages
from ui.media_viewer import MediaDocument
from ui.archive_viewer import ArchiveDocument

_registered = False


class QuickWorkspace(QObject):
    ready = Signal()
    preparation_status = Signal(str)
    preparation_failed = Signal(str)
    return_to_project_signal = Signal()
    closed = Signal()

    def __init__(self, project, p1, p2=None, *, preferences, profile_provider=None,
                 settings_path=None, terminal_factory=None, enable_private_services=True,
                 qml_engine=None, qml_component=None, parent=None):
        super().__init__(parent)
        global _registered
        if not _registered:
            qmlRegisterType(ViewerDocument, "Renns.Workspace", 1, 0, "ViewerDocument")
            qmlRegisterType(HexDocument, "Renns.Workspace", 1, 0, "HexDocument")
            qmlRegisterType(SoDocument, "Renns.Workspace", 1, 0, "SoDocument")
            qmlRegisterType(PartitionDocument, "Renns.Workspace", 1, 0, "PartitionDocument")
            qmlRegisterType(ImageDocument, "Renns.Workspace", 1, 0, "ImageDocument")
            qmlRegisterType(MediaDocument, "Renns.Workspace", 1, 0, "MediaDocument")
            qmlRegisterType(ArchiveDocument, "Renns.Workspace", 1, 0, "ArchiveDocument")
            _registered = True
        self.project, self.p1, self.p2 = project, p1, p2
        self._closing = False
        self._entry_transition_active = False
        self._ready_emitted = False
        self._returning = False
        self._shutdown_and_quit = lambda: None
        options = {"terminal_factory": terminal_factory} if terminal_factory else {}
        self.session = WorkspaceSession(project, p1, p2, preferences=preferences,
                                        settings_path=settings_path, profile_provider=profile_provider,
                                        enable_private_services=enable_private_services,parent=self, **options)
        from core.error_reporting import register_error_presenter
        register_error_presenter(self.session)
        self.diagnostics = []
        self._qml_component = qml_component
        if qml_engine is not None and qml_component is not None:
            self.engine = qml_engine
            self.engine.setParent(self)
        else:
            self.engine = QQmlApplicationEngine(self)
        self.engine.warnings.connect(lambda errors: self.diagnostics.extend(error.toString() for error in errors))
        self.engine.rootContext().setContextProperty("rakPreferences", preferences)
        self.engine.rootContext().setContextProperty("rakAgentControls", self.session.agentControls)
        if self.engine.imageProvider("icons") is None:
            self.engine.addImageProvider("icons", IconProvider())
        if self.engine.imageProvider("rak") is None:
            self.engine.addImageProvider("rak", IconProvider())
        if self.engine.imageProvider("documents") is None:
            self.engine.addImageProvider("documents", DocumentImages())
        if qml_engine is not None and qml_component is not None:
            self.window = qml_component.createWithInitialProperties({"workspace": self.session, "preferences": preferences})
            if self.window is None or qml_component.isError():
                errs = [e.toString() for e in qml_component.errors()] + self.diagnostics
                raise RuntimeError("Cannot load native workspace: " + "\n".join(errs))
        else:
            self.engine.setInitialProperties({"workspace": self.session, "preferences": preferences})
            self.engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("Workspace.qml"))))
            if not self.engine.rootObjects():
                raise RuntimeError("Cannot load native workspace: " + "\n".join(self.diagnostics))
            self.window = self.engine.rootObjects()[0]
        self.window.setIcon(QIcon(icon("ic_launcher")))
        self.engine._dialog_stack = DialogStack(self.window)
        self.session.ready.connect(self._prepared)
        self.session.preparation_status.connect(self.preparation_status)
        self.session.preparation_failed.connect(self.preparation_failed)
        self.session.returnRequested.connect(self._return)
        self.session.quitRequested.connect(self._quit)
        self.window.setOpacity(0)
        screen = self.window.screen()
        if screen:
            self.window.setPosition(screen.availableGeometry().center()-QPoint(self.window.width()//2,self.window.height()//2))
        self.window.show()
        QTimer.singleShot(0, self.session.start)

    @Slot()
    def _prepared(self):
        self._ready_emitted = True
        self.ready.emit()

    def show(self):
        self.window.show()
        self.window.requestActivate()

    def isVisible(self):
        return self.window.isVisible() and self.window.opacity() > 0

    def _begin_entry_transition(self): self._entry_transition_active = True
    def _complete_entry_transition(self):
        self._entry_transition_active = False
        if not self._closing:
            # Warm the hidden editor after the file-tree window is visible;
            # Chromium/Monaco startup must not hold the project loader open.
            QTimer.singleShot(0,self.session.editor.prepare)

    def reveal(self, callback=None):
        self._begin_entry_transition()
        self.show()
        self._reveal_duration=.180 if self.session.preferences.settings['animations'] else 0
        print(f'[MAIN STAGE] reveal window duration={self._reveal_duration:.3f}s',flush=True)
        self._reveal_started=time.perf_counter()
        self._reveal_callback=callback
        self._reveal_timer=QTimer(self)
        self._reveal_timer.setInterval(16)
        self._reveal_timer.timeout.connect(self._advance_reveal)
        # Qt Quick suspends its unified animation driver when every exposed
        # window has zero opacity. Advance the original OutCubic window fade
        # on the GUI clock, which also runs while only the loader is visible.
        self._reveal_timer.start()
        self._advance_reveal()

    @Slot()
    def _advance_reveal(self):
        if self._closing:
            self._reveal_timer.stop();return
        progress=min(1,(time.perf_counter()-self._reveal_started)/self._reveal_duration) if self._reveal_duration else 1
        self.window.setOpacity(1-(1-progress)**3)
        if progress>=1:
            self._reveal_timer.stop()
            self._complete_entry_transition()
            callback,self._reveal_callback=self._reveal_callback,None
            if callback: callback()

    def return_to_project(self): self.session.requestReturn()

    def abort_project_preparation(self): self.dispose()
    def force_shutdown(self): self.dispose()
    def close(self): self.session.requestQuit()

    @Slot()
    def _return(self):
        self._returning = True
        self.dispose()

    @Slot()
    def _quit(self):
        self.dispose()

    def dispose(self):
        if self._closing: return
        self._closing = True
        from core.error_reporting import unregister_error_presenter
        unregister_error_presenter(self.session)
        if hasattr(self,'_reveal_timer'):self._reveal_timer.stop()
        self.session.stop()
        self.engine._dialog_stack.close()
        self._dispose_main_window()

    def _dispose_main_window(self):
        self.lifetime = WindowLifetime(self.window, self.session.web_sessions(), self)
        self.lifetime.finished.connect(self._dispose_engine)
        self.lifetime.close()

    def _dispose_engine(self):
        self.window.setProperty("managedClose", False)
        if self._qml_component is not None:
            self._qml_component.deleteLater()
            self._qml_component = None
        engine, self.engine = self.engine, None
        engine.destroyed.connect(self._disposed)
        engine.deleteLater()

    def _disposed(self):
        self.closed.emit()
        if self._returning: self.return_to_project_signal.emit()
        elif self._ready_emitted: self._shutdown_and_quit()
