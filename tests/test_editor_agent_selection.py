# SPDX-License-Identifier: MPL-2.0
"""Test editor line selection synchronization with Agent Chat and attachments."""
import os
from pathlib import Path
import sys
import tempfile
import pytest

os.environ["QSG_RENDER_LOOP"] = "basic"

from PySide6.QtCore import QPoint, Qt, QObject, Signal, Property, Slot, QUrl
from PySide6.QtWidgets import QApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from core.fonts import configure_text_rendering_env, apply_app_font
from ui.runtime import initialize_quick_runtime, IconProvider
from ui.presentation import LauncherModel
from ui.editor import EditorSession
from ui.workspace import WorkspaceSession
from core.agent_attachments import preview as attachment_preview


def test_quill_selection_and_agent_context(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    configure_text_rendering_env()
    initialize_quick_runtime(webengine=False)
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    apply_app_font(app, polish_widgets=False)

    test_file = tmp_path / "sample.py"
    test_content = (
        "def first():\n"
        "    return 1\n"
        "\n"
        "def second():\n"
        "    return 2\n"
        "\n"
        "def third():\n"
        "    return 3\n"
    )
    test_file.write_text(test_content, encoding="utf-8")

    from quill_native.editor import NativeEditor
    from quill_native._engine import Document

    editor = NativeEditor()
    editor.loadPath(str(test_file))
    assert editor.lineCount >= 7

    # Select lines 4 to 5 ("def second():\n    return 2")
    # In Document 0-based: row 3 col 0 to row 4 col 12
    editor._doc.set_cursor(3, 0, False)
    editor._doc.set_cursor(4, 12, True)
    editor._changed()

    sel = editor.selectionRange
    assert sel["startLineNumber"] == 4
    assert sel["endLineNumber"] == 5
    assert "def second():" in sel["text"]
    assert "return 2" in sel["text"]

    # Now verify WorkspaceSession + EditorSession + Inbox + Agent Chat integration
    system_dir = tmp_path / "system"
    system_dir.mkdir(parents=True, exist_ok=True)
    prefs = LauncherModel()
    ws = WorkspaceSession(str(tmp_path), "system", preferences=prefs, enable_private_services=False)
    ws.agentChat._providers = [{"id": "gemini", "label": "Gemini"}]

    # Open tab in workspace
    ws.openAs(str(test_file), "editor")
    assert str(test_file) in ws.editor.bridge._tabs or ws.activeTab == str(test_file)

    # Simulate editor sync from QML / stateChanged
    ws.editor.setSelection(str(test_file), sel)

    bridge_sel = ws.editor.bridge.get_selection(str(test_file))
    assert bridge_sel["startLineNumber"] == 4
    assert bridge_sel["endLineNumber"] == 5
    assert "def second():" in bridge_sel["text"]

    # Dispatch agentSend
    ws.dispatch("agentSend", {
        "origin": "editor",
        "path": str(test_file),
        "selection": sel
    })

    # Verify agent chat has attachment with selected lines
    assert ws.agentChat.opened is True
    attachments = ws.agentChat.attachments
    assert len(attachments) >= 1

    chip = attachments[0]
    # Name should be sample.py
    assert "sample.py" in chip["name"] or "sample.py" in chip["path"]
    # sizeLabel / caption should show lines 4–5
    assert "lines 4–5" in chip["sizeLabel"] or "lines 4–5" in chip.get("label", "")

    # Capture context and verify documents has the selection
    captured = ws.agentInbox.capture("editor", [str(test_file)])
    assert len(captured["documents"]) >= 1
    doc = captured["documents"][0]
    assert doc["selection"]["startLineNumber"] == 4
    assert doc["selection"]["endLineNumber"] == 5
    assert "def second():" in doc["selection"]["text"]


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_quill_selection_and_agent_context(Path(td))
    print("ALL TESTS PASSED!")
