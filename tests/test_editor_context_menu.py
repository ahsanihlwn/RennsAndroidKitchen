# SPDX-License-Identifier: MPL-2.0
"""Test Native Editor right-click context menu integration."""
import os
from pathlib import Path
import sys
import tempfile
import pytest

os.environ["QSG_RENDER_LOOP"] = "basic"

from PySide6.QtCore import QPoint, QPointF, Qt, QObject, Signal, Property, Slot, QUrl
from PySide6.QtWidgets import QApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from core.fonts import configure_text_rendering_env, apply_app_font
from ui.runtime import initialize_quick_runtime, IconProvider
from ui.presentation import LauncherModel
from tools.quick_test_utils import visual


class MockEditorSession(QObject):
    activeChanged = Signal()
    currentPathChanged = Signal()
    openFileRequested = Signal(str)
    closeFileRequested = Signal(str)
    saveRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._active = True
        self._backend = "quill"
        self._current_path = ""
        self._dirty_paths = {}

    @Property(bool, notify=activeChanged)
    def active(self):
        return self._active

    @Property(str, constant=True)
    def backend(self):
        return self._backend

    @Property(str, notify=currentPathChanged)
    def currentPath(self):
        return self._current_path

    @Slot(str, bool)
    def setDirty(self, path, dirty):
        self._dirty_paths[path] = dirty

    @Slot(str)
    def notifySaved(self, path):
        self._dirty_paths[path] = False

    @Slot(str, "QVariantMap")
    def setSelection(self, path, selection):
        pass


class MockAgentChat(QObject):
    availableChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._available = True

    @Property(bool, notify=availableChanged)
    def available(self):
        return self._available


class MockWorkspace(QObject):
    changed = Signal()
    presentationRequested = Signal(str, 'QVariantMap')

    def __init__(self, parent=None):
        super().__init__(parent)
        self.dispatched = []
        self._agent_chat = MockAgentChat(self)
        self._editor = MockEditorSession(self)

    @Property(QObject, constant=True)
    def agentChat(self):
        return self._agent_chat

    @Property(QObject, constant=True)
    def editor(self):
        return self._editor

    @Slot(bool)
    def setContextMenuActive(self, active):
        pass

    @Slot('QVariantList', str, result='QVariantMap')
    def contextMenuMetrics(self, rows, kind):
        from ui.menu_metrics import menu_metrics
        return menu_metrics(rows, kind)

    @Slot(str, 'QVariantMap')
    def dispatch(self, action, data):
        self.dispatched.append((action, data))


FIXTURE_QML = """// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    id: window
    required property var workspace
    required property var preferences
    readonly property Item backdropSurface: page
    width: 800; height: 600; visible: true; color: preferences.colors.BG3
    Rectangle { id: page; anchors.fill: parent; color: window.preferences.colors.BG3 }
    WorkspaceContextMenu {
        id: menu
        objectName: "workspaceContextMenu"
        workspace: window.workspace
        preferences: window.preferences
        hostWindow: window
    }
    EditorPane {
        id: editorPane
        objectName: "editorPane"
        anchors.fill: parent
        session: window.workspace.editor
        fillColor: window.preferences.colors.BG3
        colors: window.preferences.colors
        preferences: window.preferences
        hostWindow: window
        contextMenu: menu
    }
}
"""


def test_editor_context_menu():
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    configure_text_rendering_env()
    initialize_quick_runtime(webengine=False)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app, polish_widgets=False)

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
        tf.write("print('Hello Native Editor!')\nsecond_line = 123\n")
        temp_file = tf.name

    try:
        engine = QQmlApplicationEngine()
        engine.addImageProvider('icons', IconProvider())
        diagnostics = []
        engine.warnings.connect(lambda errors: diagnostics.extend(error.toString() for error in errors))

        workspace = MockWorkspace()
        workspace.editor._current_path = temp_file
        prefs = LauncherModel()
        engine.setInitialProperties({'workspace': workspace, 'preferences': prefs})

        engine.loadData(FIXTURE_QML.encode("utf-8"), QUrl.fromLocalFile(str(repo / "tools" / "fixture.qml")))
        assert len(engine.rootObjects()) > 0, f"QML load failed: {diagnostics}"

        root = engine.rootObjects()[0]
        root.show()
        QTest.qWait(60)

        editor_pane = root.findChild(QObject, "editorPane")
        ctx_menu = root.findChild(QObject, "workspaceContextMenu")
        quill_editor = editor_pane.findQuillEditor()
        assert quill_editor is not None, "Quill editor must be found inside EditorPane"

        quill_editor.loadPath(temp_file)
        QTest.qWait(50)

        # Trigger showContextMenu directly or simulate event
        editor_pane.showContextMenu(100, 50, quill_editor)
        QTest.qWait(60)

        assert ctx_menu.property("visible") is True, "Context menu must be opened"
        details = ctx_menu.property("details")
        if hasattr(details, "toVariant"):
            details = details.toVariant()
        actions = details.get("actions", [])
        assert len(actions) == 5, f"Expected 5 actions (Copy, Cut, Paste, Separator, Send to agent), got {len(actions)}"
        keys = [a.get("key", "separator") for a in actions]
        assert keys == ["copy", "cut", "paste", "separator", "agentSend"]

        # Shortcuts
        assert actions[0]["shortcut"] == "Ctrl+C"
        assert actions[1]["shortcut"] == "Ctrl+X"
        assert actions[2]["shortcut"] == "Ctrl+V"

        # Initially no selection: Copy & Cut should be disabled
        assert actions[0]["enabled"] is False
        assert actions[1]["enabled"] is False
        assert actions[2]["enabled"] is True
        assert actions[4]["enabled"] is True

        # Test selecting all
        quill_editor.selectAll()
        assert quill_editor.property("hasSelection") is True
        assert "print('Hello Native Editor!')" in quill_editor.property("selectedText")

        # Open menu again and test Copy via QML button
        editor_pane.showContextMenu(100, 50, quill_editor)
        QTest.qWait(60)
        copy_btn = visual(root, "workspaceContextAction_copy")
        assert copy_btn is not None
        assert copy_btn.property("enabled") is True
        copy_btn.clicked.emit()
        QTest.qWait(50)
        assert "print('Hello Native Editor!')" in app.clipboard().text()

        # Open menu again and test Send to agent via QML button
        editor_pane.showContextMenu(100, 50, quill_editor)
        QTest.qWait(60)
        agent_btn = visual(root, "workspaceContextAction_agentSend")
        assert agent_btn is not None
        agent_btn.clicked.emit()
        QTest.qWait(50)
        assert len(workspace.dispatched) >= 1
        last_dispatch = workspace.dispatched[-1]
        assert last_dispatch[0] == "agentSend"
        assert last_dispatch[1]["origin"] == "editor"
        assert os.path.realpath(last_dispatch[1]["path"]) == os.path.realpath(temp_file)

        # Open menu again and test Cut via QML button
        editor_pane.showContextMenu(100, 50, quill_editor)
        QTest.qWait(60)
        cut_btn = visual(root, "workspaceContextAction_cut")
        assert cut_btn is not None
        assert cut_btn.property("enabled") is True
        cut_btn.clicked.emit()
        QTest.qWait(50)
        assert quill_editor._doc.line(0) == ""

        # Open menu again and test Paste via QML button
        editor_pane.showContextMenu(100, 50, quill_editor)
        QTest.qWait(60)
        paste_btn = visual(root, "workspaceContextAction_paste")
        assert paste_btn is not None
        assert paste_btn.property("enabled") is True
        paste_btn.clicked.emit()
        QTest.qWait(50)
        assert "print('Hello Native Editor!')" in quill_editor._doc.line(0)

        ctx_menu.closeAll()
        QTest.qWait(50)
        assert ctx_menu.property("visible") is False

    finally:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass


if __name__ == "__main__":
    test_editor_context_menu()
    print("ALL TESTS PASSED!")
