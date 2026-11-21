"""Embed the native terminal in a Python app. Works the same with PySide6 (swap the imports).

    python qtterm_demo.py [path/to/build/qml] [program args...]
The only requirement: the QtTerm QML plugin was built against the same Qt X.Y as your Python binding.
"""
import os, sys
from PyQt6.QtCore import QObject, QTimer, pyqtProperty, pyqtSignal, pyqtSlot   # PySide6: Signal / Slot / Property
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtQml import QQmlApplicationEngine

QML = b"""
import QtQuick
import QtQuick.Window
import QtTerm 1.0

Window {
    width: 900; height: 520; visible: true; color: "#1e1e2e"
    title: term.title !== "" ? term.title : "QtTerm from Python"
    Terminal {
        id: term
        anchors.fill: parent; focus: true; padding: 8
        program: bridge.program; arguments: bridge.args
        fontFamily: "DejaVu Sans Mono"; fontPixelSize: 15
        foreground: "#cdd6f4"; background: "#1e1e2e"
        onExited: (code) => bridge.onExited(code)
        onBell: bridge.onBell()
    }
    Connections {
        target: bridge
        function onSendText(t) { term.sendText(t) }
        function onAskScreen() { bridge.onScreen(term.screenText(), term.columns, term.rows, term.title) }
    }
}
"""

class Bridge(QObject):
    sendText = pyqtSignal(str)      # Python -> terminal
    askScreen = pyqtSignal()
    def __init__(self, program, args):
        super().__init__(); self._program, self._args = program, args; self.exit_code = None; self.screen = None
    @pyqtProperty(str, constant=True)
    def program(self): return self._program
    @pyqtProperty('QStringList', constant=True)
    def args(self): return self._args
    @pyqtSlot(int)
    def onExited(self, code): self.exit_code = code; print(f"[python] process exited with {code}")
    @pyqtSlot()
    def onBell(self): print("[python] bell")
    @pyqtSlot(str, int, int, str)
    def onScreen(self, text, cols, rows, title): self.screen = (text, cols, rows, title)

def main():
    plugin_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "build", "qml")
    program = sys.argv[2] if len(sys.argv) > 2 else "/bin/bash"
    app = QGuiApplication(sys.argv)
    bridge = Bridge(program, sys.argv[3:])
    engine = QQmlApplicationEngine()
    engine.addImportPath(os.path.abspath(plugin_dir))
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.loadData(QML)
    if not engine.rootObjects():
        sys.exit("QML failed to load (plugin Qt version mismatch?)")

    if os.environ.get("DEMO_SELFTEST"):          # scripted check used for verification
        QTimer.singleShot(1500, lambda: bridge.sendText.emit("echo PY-$((6*7)); printf '\\033[31mred\\033[0m\\n'; exit 3\r"))
        QTimer.singleShot(3500, bridge.askScreen.emit)
        def check():
            text, cols, rows, title = bridge.screen
            print(f"[python] grid {cols}x{rows}; screen text:\n" + "\n".join("  | " + l for l in text.splitlines()[:6]))
            ok = "PY-42" in text and bridge.exit_code == 3
            print("[python] SELFTEST", "PASS" if ok else "FAIL"); app.exit(0 if ok else 1)
        QTimer.singleShot(4200, check)
    sys.exit(app.exec())

main()
