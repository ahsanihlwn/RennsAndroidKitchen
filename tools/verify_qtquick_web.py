# SPDX-License-Identifier: MPL-2.0
"""Verify the real Monaco/xterm assets in Qt Quick, optionally with a real PTY."""
import argparse
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-pty", action="store_true", help="Run an echo marker in a temporary terminal session")
    args = parser.parse_args()
    import faulthandler
    faulthandler.enable(all_threads=True)
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    os.chdir(repo)
    from core.fonts import configure_text_rendering_env
    configure_text_rendering_env()
    from PySide6.QtCore import Qt, QUrl, QMetaObject, QEventLoop, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow
    from ui.runtime import initialize_quick_runtime, WindowLifetime
    from ui.editor import EditorSession
    from ui.terminal import TerminalSession
    from core.terminal_pty import PtySignals

    # Initialize before the GUI application, as required by Qt WebEngine Quick.
    initialize_quick_runtime(webengine=True)
    app = QGuiApplication([])
    app.setQuitOnLastWindowClosed(False)

    class FakePty:
        def __init__(self):
            self.signals = PtySignals()
            self.cwd = str(repo)
            self.bash_mode = False
            self.writes = []
        def start(self, **kwargs):
            self.signals.data_ready.emit("QT_QUICK_TERMINAL_FIXTURE\r\n")
        def resize(self, *args):
            pass
        def write(self, value):
            self.writes.append(value)
        def shutdown(self):
            pass

    editor = EditorSession()
    terminal = TerminalSession(cwd=str(repo), manager=None if args.real_pty else FakePty())
    editor.bridge.open_virtual("qtquick-fixture.prop", "ro.product.device=qtquick\n", icon_b64="fixture")
    terminal.applyTheme({"BG": "#12131a", "FG": "#c9d1d9"})
    assert terminal.start()
    if args.real_pty:
        terminal.runCommand("echo QT_QUICK_PTY_OK")
    engine = QQmlApplicationEngine()
    diagnostics = []
    engine.warnings.connect(lambda errors: diagnostics.extend(error.toString() for error in errors))
    engine.setInitialProperties({"editorSession": editor, "terminalSession": terminal})
    engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("qtquick_web_fixture.qml"))))
    assert engine.rootObjects(), diagnostics
    window = engine.rootObjects()[0]
    assert isinstance(window, QQuickWindow)
    window.setFlags(window.flags() | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
    window.setPosition(app.primaryScreen().availableGeometry().topLeft())
    window.show()
    # PySide's QtWebEngineQuick bindings load QtWidgets transitively; there
    # must still be no QWebEngineView/QWidget frontend or widget instances.
    assert "PySide6.QtWebEngineWidgets" not in sys.modules
    assert "main_ext.class_WebEditorPanel" not in sys.modules
    from PySide6.QtWidgets import QApplication

    def wait(milliseconds):
        loop = QEventLoop()
        QTimer.singleShot(milliseconds, loop.quit)
        loop.exec()

    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and not (editor.bridge._ready and terminal.bridge.is_ready):
            wait(25)
        wait(150)
        QMetaObject.invokeMethod(window, "inspectContent")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not (window.property("editorResult") and window.property("terminalResult")):
            wait(25)
        editor_result = json.loads(window.property("editorResult"))
        terminal_result = json.loads(window.property("terminalResult"))
        assert editor.bridge._ready, "Monaco did not complete its WebChannel handshake"
        assert terminal.bridge.is_ready, ("xterm did not complete its WebChannel handshake", terminal_result)
        assert editor_result["content"] == "ro.product.device=qtquick\n", editor_result
        expected = "QT_QUICK_PTY_OK" if args.real_pty else "QT_QUICK_TERMINAL_FIXTURE"
        assert expected in terminal_result["content"], terminal_result
        assert terminal_result["cols"] >= 80, terminal_result
        QMetaObject.invokeMethod(window,'scrollEditorFixture');wait(250)
        def hints():
            window.setProperty('scrollResult','');QMetaObject.invokeMethod(window,'inspectScrollHints')
            deadline=time.monotonic()+5
            while not window.property('scrollResult'):
                assert time.monotonic()<deadline,'Editor scrollbar inspection timed out'
                wait(10)
            return json.loads(window.property('scrollResult'))
        current=hints();assert current=={'active':True,'opacity':'1'},current
        deadline=time.monotonic()+5
        while current['active'] and time.monotonic()<deadline:
            wait(100);current=hints()
        assert current=={'active':False,'opacity':'0'},current
        assert not diagnostics, diagnostics
        assert not QApplication.allWidgets()
        artifacts = repo / "build" / "qtquick_migration" / "web"
        artifacts.mkdir(parents=True, exist_ok=True)
        window.grabWindow().save(str(artifacts / "web.png"))
        report = {"result": "passed", "monaco": True, "xterm": True,"editor_scroll_hints_auto_hide":True,
                  "graphics_api": window.rendererInterface().graphicsApi().name,
                  "real_pty": args.real_pty, "qwidget_count": 0}
    finally:
        lifetime = WindowLifetime(window, (editor, terminal))
        cleanup = QEventLoop()
        released = []
        lifetime.finished.connect(lambda: released.append(True))
        lifetime.finished.connect(cleanup.quit)
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout.timeout.connect(cleanup.quit)
        timeout.start(3000)
        lifetime.close()
        cleanup.exec()
        timeout.stop()
        assert released, "Scene graph teardown did not complete"
        # Destroy the QML WebEngineViews before the GUI application/profile.
        engine.deleteLater()
        QTimer.singleShot(100, app.quit)
        app.exec()
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
