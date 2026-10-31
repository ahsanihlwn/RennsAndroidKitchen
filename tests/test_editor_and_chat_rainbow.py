# SPDX-License-Identifier: MPL-2.0
"""Test rainbow effect glow on editor border, editor tab, and agent composer box."""
import os
from pathlib import Path
import sys
import time

def main():
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    sys.path.insert(0, str(repo))

    from PySide6.QtCore import QUrl, QObject, Signal, Property, Slot
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow
    from PySide6.QtTest import QTest
    from core.fonts import configure_text_rendering_env, apply_app_font
    from ui.runtime import initialize_quick_runtime

    configure_text_rendering_env()
    initialize_quick_runtime(webengine=False)
    app = QGuiApplication([])
    apply_app_font(app, polish_widgets=False)
    engine = QQmlApplicationEngine()
    diagnostics = []
    engine.warnings.connect(lambda errors: diagnostics.extend(error.toString() for error in errors))

    # Test 1: Agent Composer Rainbow Border / Shadow
    chat_qml = b'''import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    id: win
    visible: true; width: 420; height: 500; color: "#13141b"
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
        property var chats: []
        property string selectedChat: ""
        property var pendingRequest: ({})
        property string error: ""
        property string modelCatalogError: ""
        property bool opened: true
        function labels(lang) {
            return {
                assistant: "Agent", history: "History", new: "New", more: "More", close: "Close",
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
    }
    property var mockPrefs: QtObject {
        property var colors: ({
            BG: "#13141b", BG2: "#1a1b22", BG3: "#22232a", BG4: "#2a2b34",
            FG: "#ffffff", FG2: "#d6d9e2", FG3: "#9ea3b0", FG_BRIGHT: "#ffffff",
            FG_MUTED: "#777c8c", accent_hex: "#58b9ef",
            b03: "#08ffffff", b04: "#0affffff", b06: "#0fffffff", b08: "#14ffffff",
            b09: "#17ffffff", b14: "#24ffffff", b18: "#2effffff", b20: "#33ffffff",
            b35: "#59ffffff", a12: "#1f58b9ef", a18: "#2e58b9ef", a25: "#4058b9ef",
            a30: "#4d58b9ef", a40: "#6658b9ef", a48: "#7a58b9ef",
            RED_STATUS: "#ff5555"
        })
        property var strings: ({ languageKey: "en" })
        property var settings: ({ animations: true, effects: "high" })
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

    engine.loadData(chat_qml, QUrl.fromLocalFile(str(repo / 'tools/agent_chat_fixture.qml')))
    assert engine.rootObjects(), diagnostics
    chat_win = engine.rootObjects()[0]
    from tools.quick_test_utils import visual
    composer_rainbow = visual(chat_win, 'agentChatComposerRainbow')
    assert composer_rainbow is not None, "agentChatComposerRainbow item must exist in composerBox"

    def wait(predicate, seconds=4):
        deadline = time.monotonic() + seconds
        while not predicate():
            assert time.monotonic() < deadline, 'Verification timed out'
            QTest.qWait(10)

    # Initially inactive
    assert not composer_rainbow.property('active')
    assert composer_rainbow.opacity() < 0.001

    # Agent starts responding (mockChat.running = True)
    chat_win.property('mockChat').setProperty('running', True)
    wait(lambda: composer_rainbow.property('active'))
    wait(lambda: composer_rainbow.opacity() >= 0.99)
    assert composer_rainbow.isVisible()

    # Agent stops responding
    chat_win.property('mockChat').setProperty('running', False)
    wait(lambda: not composer_rainbow.property('active'))
    wait(lambda: not composer_rainbow.isVisible())
    assert composer_rainbow.opacity() < 0.001

    chat_win.close()
    engine.deleteLater()
    QTest.qWait(30)
    print("Agent chat composer rainbow test passed successfully!")

    # Test 2: Editor rainbow shadow & Editor tab rainbow test fixture
    engine2 = QQmlApplicationEngine()
    editor_qml = b'''import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    id: win2
    visible: true; width: 600; height: 400; color: "#13141b"
    property var mockActivity: QtObject {
        property var paths: ({})
        function key(p) { return p.toLowerCase().replace(/\\\\/g, "/") }
    }
    property var mockWorkspace: QtObject {
        property var agentActivity: win2.mockActivity
        property var projectSettings: ({ agent: { ui_triggers: false, highlight: true } })
        property bool hasTabs: true
        property string activeKind: "editor"
        property string activeTab: "system/build.prop"
    }
    property var mockPrefs: QtObject {
        property var colors: ({
            BG: "#13141b", BG2: "#1a1b22", BG3: "#22232a", BG_DARK: "#101116",
            FG: "#ffffff", FG2: "#d6d9e2", FG_BRIGHT: "#ffffff", FG_MUTED: "#777c8c",
            GRAY_MID: "#555555", GRAY_MID2: "#666666", RED_STRONG: "#ff6b6b",
            accent_hex: "#58b9ef", b04: "#0affffff", b18: "#2effffff",
            red18: "#2edc4646", red32: "#52dc4646"
        })
        property var settings: ({ animations: true, effects: "high" })
    }

    Item {
        id: motionPane
        anchors.fill: parent

        AgentRainbowShadow {
            id: editorRainbowShadow
            objectName: "workspaceEditorRainbow"
            anchors.fill: editorCard
            radius: editorCard.radius
            active: {
                if (!win2.mockWorkspace.agentActivity) return false
                const agentSettings = win2.mockWorkspace.projectSettings ? win2.mockWorkspace.projectSettings.agent : null
                if (!agentSettings || !agentSettings.ui_triggers) return false
                if (!win2.mockWorkspace.hasTabs || win2.mockWorkspace.activeKind !== "editor") return false
                const activeKey = win2.mockWorkspace.agentActivity.key(win2.mockWorkspace.activeTab || "")
                if (activeKey && win2.mockWorkspace.agentActivity.paths[activeKey]) return true
                const paths = win2.mockWorkspace.agentActivity.paths
                for (let k in paths) {
                    const row = paths[k]
                    if (row && (row.kind === "write" || row.state === "held")) return true
                }
                return false
            }
            animated: true
        }

        Rectangle {
            id: editorCard
            anchors.fill: parent
            radius: 8
            color: "#22232a"

            WorkspaceTabs {
                id: editorTabs
                objectName: "workspaceEditorTabs"
                anchors.top: parent.top
                width: parent.width
                preferences: win2.mockPrefs
                rows: [{key: "system/build.prop", title: "build.prop"}]
                activeKey: win2.mockWorkspace.activeTab
                workspace: win2.mockWorkspace
            }
        }
    }
}'''

    engine2.loadData(editor_qml, QUrl.fromLocalFile(str(repo / 'tools/editor_rainbow_fixture.qml')))
    assert engine2.rootObjects()
    win2 = engine2.rootObjects()[0]
    editor_rainbow = visual(win2, 'workspaceEditorRainbow')
    tab_rainbow = visual(win2, 'workspaceTabRainbow_system/build.prop')
    assert editor_rainbow is not None, "Editor rainbow shadow must exist"
    assert tab_rainbow is not None, "Editor tab rainbow highlight must exist"

    # When ui_triggers is False, both remain inactive even if file is written
    win2.property('mockActivity').setProperty('paths', {"system/build.prop": {"kind": "write", "state": "held"}})
    QTest.qWait(50)
    assert not editor_rainbow.property('active')
    assert not tab_rainbow.property('active')

    # Enable ui_triggers -> both should immediately become active!
    win2.property('mockWorkspace').setProperty('projectSettings', {"agent": {"ui_triggers": True, "highlight": True}})
    wait(lambda: editor_rainbow.property('active') and tab_rainbow.property('active'))
    wait(lambda: editor_rainbow.opacity() >= 0.99 and tab_rainbow.opacity() > 0.05)
    assert editor_rainbow.isVisible()
    assert tab_rainbow.isVisible()

    # When agent finishes editing (paths cleared) -> both deactivate
    win2.property('mockActivity').setProperty('paths', {})
    wait(lambda: not editor_rainbow.property('active') and not tab_rainbow.property('active'))
    wait(lambda: not editor_rainbow.isVisible() and not tab_rainbow.isVisible())

    win2.close()
    engine2.deleteLater()
    QTest.qWait(30)
    print("Editor rainbow shadow and tab rainbow test passed successfully!")

if __name__ == '__main__':
    main()
