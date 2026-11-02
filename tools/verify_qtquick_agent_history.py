# SPDX-License-Identifier: MPL-2.0
"""Verification for Agent Chat history popup and new conversation creation."""
import os
from pathlib import Path
import sys
import time

def main():
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    sys.path.insert(0, str(repo))

    from PySide6.QtCore import QUrl, QObject, Signal, Property, Slot, Qt, QTimer, QEventLoop
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    from core.fonts import configure_text_rendering_env, apply_app_font
    from ui.runtime import initialize_quick_runtime, IconProvider
    from tools.quick_test_utils import visual

    configure_text_rendering_env()
    initialize_quick_runtime(webengine=False)
    app = QGuiApplication.instance() or QGuiApplication([])
    apply_app_font(app, polish_widgets=False)
    engine = QQmlApplicationEngine()
    engine.addImageProvider("rak", IconProvider())
    diagnostics = []
    engine.warnings.connect(lambda errors: diagnostics.extend(error.toString() for error in errors))

    qml = b'''import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    id: win
    visible: true; width: 600; height: 700; color: "#13141b"
    property bool newChatCalled: false
    property string selectedChatCalled: ""
    property var mockChat: QtObject {
        property bool running: false
        property bool busy: false
        property string state: "idle"
        property string draft: ""
        property var messages: []
        property var attachments: []
        property var commands: []
        property var providers: [{id: "agy", label: "Antigravity"}]
        property string provider: "agy"
        property var models: []
        property string model: ""
        property var modes: []
        property string mode: ""
        property var chats: [
            {id: "chat-1", label: "This is an extremely long conversation title that would previously stretch the popup way too wide across the screen", provider: "agy"},
            {id: "chat-2", label: "Another conversation", provider: "agy"}
        ]
        property string selectedChat: "chat-1"
        property var pendingRequest: ({})
        property string error: ""
        property string modelCatalogError: ""
        property bool opened: true
        function labels(lang) {
            return {
                assistant: "Agent", history: "Riwayat", new: "Chat baru", new_conversation: "Percakapan baru", no_history: "Belum ada percakapan",
                welcome: "Welcome", welcome_body: "Ask anything", example: "Help me",
                latest: "Latest", waiting: "Waiting", permission: "Permission",
                choose_answer: "Choose", your_answer: "Your answer", save: "Save",
                allow: "Allow", deny: "Deny", answer: "Answer", placeholder: "Ask anything...",
                attachments: "Attach", add_selection: "Add selection", add_files: "Add files",
                add_folder: "Add folder", clear: "Clear", choose_agent: "Choose agent",
                send: "Send", stop: "Stop", force_stop: "Force stop", stop_after: "Stop after",
                retry: "Retry", details: "Details", hide_details: "Hide details",
                catalog_log: "Log", drop_files: "Drop files", shortened: "Shortened",
                thinking_empty: "", thought_empty: "", copy: "Copy", model_settings: "Settings",
                commands: "Commands", refresh: "Refresh", log: "Log"
            }
        }
        function send() { return true }
        function setDraft(t) { draft = t }
        function newChat() { win.newChatCalled = true }
        function selectChat(id) { win.selectedChatCalled = id; selectedChat = id }
    }
    property var mockPrefs: QtObject {
        property var colors: ({
            BG: "#13141b", BG2: "#1a1b22", BG3: "#22232a", BG4: "#2a2b34",
            FG: "#ffffff", FG2: "#d6d9e2", FG3: "#9ea3b0", FG_BRIGHT: "#ffffff",
            FG_MUTED: "#777c8c", accent_hex: "#58b9ef",
            b03: "#08ffffff", b04: "#0affffff", b06: "#0fffffff", b07: "#12ffffff", b08: "#14ffffff",
            b09: "#17ffffff", b10: "#1affffff", b14: "#24ffffff", b18: "#2effffff", b20: "#33ffffff",
            b35: "#59ffffff", a12: "#1f58b9ef", a18: "#2e58b9ef", a25: "#4058b9ef",
            a30: "#4d58b9ef", a40: "#6658b9ef", a48: "#7a58b9ef",
            RED_STATUS: "#ff5555"
        })
        property var strings: ({ languageKey: "id" })
        property var settings: ({ animations: false, effects: "high" })
    }
    property var mockWorkspace: QtObject {
        property var agentChat: win.mockChat
    }

    WorkspaceAgentChatPanel {
        id: panel
        anchors.fill: parent
        workspace: win.mockWorkspace
        preferences: win.mockPrefs
    }
}'''

    engine.loadData(qml, QUrl.fromLocalFile(str(repo / 'tools/agent_history_fixture.qml')))
    assert engine.rootObjects(), diagnostics
    win = engine.rootObjects()[0]

    def pause(ms=20):
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    def wait(predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while not predicate():
            assert time.monotonic() < deadline, "Condition timed out"
            pause(15)

    history_btn = visual(win, "agentChatHistory")
    assert history_btn is not None, "agentChatHistory button must exist"
    assert history_btn.property("visible") and history_btn.property("enabled")

    # Click history button to open popup
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, history_btn.mapToScene(history_btn.boundingRect().center()).toPoint())
    pause(100)

    popup = visual(win, "agentChatHistoryPopup")
    assert popup is not None, "agentChatHistoryPopup must exist"
    wait(lambda: popup.property("opened"))

    # Verify popup width is constrained (should be <= 260, definitely not the 600 window width or long text width)
    popup_w = popup.property("width")
    print(f"Popup width: {popup_w}")
    assert popup_w <= 260, f"Popup width {popup_w} is too wide! Must be <= 260"
    assert popup_w >= 180, f"Popup width {popup_w} is suspiciously small"

    # Verify new conversation button
    new_btn = visual(win, "agentChatNew")
    assert new_btn is not None, "agentChatNew button must exist in popup"
    assert new_btn.property("visible")

    # Verify history items exist
    item1 = visual(win, "agentChatHistoryItem_chat-1")
    assert item1 is not None, "agentChatHistoryItem_chat-1 must exist"
    item2 = visual(win, "agentChatHistoryItem_chat-2")
    assert item2 is not None, "agentChatHistoryItem_chat-2 must exist"

    # Click selectChat on item 2
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, item2.mapToScene(item2.boundingRect().center()).toPoint())
    pause(50)
    wait(lambda: not popup.property("opened"))
    assert win.property("selectedChatCalled") == "chat-2"
    print("selectChat verified successfully")

    # Re-open popup and test newChat
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, history_btn.mapToScene(history_btn.boundingRect().center()).toPoint())
    pause(50)
    wait(lambda: popup.property("opened"))

    new_btn = visual(win, "agentChatNew")
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, new_btn.mapToScene(new_btn.boundingRect().center()).toPoint())
    pause(50)
    wait(lambda: not popup.property("opened"))
    assert win.property("newChatCalled") is True
    print("newChat verified successfully")

    print("ALL AGENT HISTORY PICKER CHECKS PASSED!")

if __name__ == "__main__":
    main()
