# SPDX-License-Identifier: MPL-2.0
"""Verification for AgentLimitMeter and WorkspaceAgentChatPanel."""
import os
from pathlib import Path
import sys
import time

repo = Path(__file__).resolve().parents[1]
os.chdir(repo)
sys.path.insert(0, str(repo))

from PySide6.QtCore import QUrl, QTimer, QEventLoop, Qt, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from core.fonts import configure_text_rendering_env, apply_app_font
from ui.runtime import initialize_quick_runtime, IconProvider

configure_text_rendering_env()
initialize_quick_runtime(webengine=False)
app = QGuiApplication.instance() or QGuiApplication([])
apply_app_font(app, polish_widgets=False)

engine = QQmlApplicationEngine()
engine.addImageProvider("rak", IconProvider())
diagnostics = []
engine.warnings.connect(lambda errors: diagnostics.extend(error.toString() for error in errors))

qml_content = """import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    id: win
    visible: true; width: 440; height: 720; color: "#13141b"

    property var mockPrefs: QtObject {
        property var colors: ({
            BG: "#13141b", BG2: "#181a24", BG3: "#1d202d", BG_MODAL: "#1d202d",
            FG: "#e0e3ed", FG2: "#c5c8d6", FG_BRIGHT: "#ffffff", FG_MUTED: "#7b8096",
            RED_STATUS: "#ef4444", GREEN_STATUS: "#22c55e", accent_hex: "#38bdf8",
            b03: "#08ffffff", b04: "#0affffff", b06: "#0fffffff", b07: "#12ffffff",
            b08: "#14ffffff", b09: "#17ffffff", b10: "#1affffff", b12: "#1fffffff",
            b14: "#24ffffff", b20: "#33ffffff", b35: "#59ffffff",
            a40: "#6638bdf8", a50: "#8038bdf8"
        })
        property var settings: ({ animations: true, effects: "high" })
        property var strings: ({ languageKey: "id" })
    }

    property var mockChat: QtObject {
        property bool running: false
        property bool busy: false
        property bool modelCatalogPending: false
        property string modelCatalogError: ""
        property string state: "idle"
        property string draft: ""
        property string error: ""
        property var messages: [
            {role: "assistant", text: "Halo! Koneksi OK, saya siap membantu.", key: "msg-1"},
            {role: "user", text: "Cek kuota limit akun saya dong", key: "msg-2"}
        ]
        property var attachments: [{path: "screenshot.png"}]
        function attachmentPreview(row) {
            return {
                name: "screenshot.png",
                kind: "image",
                icon: "image://rak/image",
                sizeLabel: "12 KB",
                previewUrl: "image://rak/chat_arrow_up"
            }
        }
        function removeAttachment(p) {}
        property var commands: []
        property var providers: [{id: "agy", label: "Antigravity"}]
        property string provider: "agy"
        property var models: [
            {id: "gemini-3.8-flash", label: "Gemini 3.8 Flash"},
            {id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6"}
        ]
        property string model: "gemini-3.8-flash"
        property var modes: [{id: "build", label: "Build"}, {id: "plan", label: "Plan"}]
        property string mode: "build"
        property var pendingRequest: ({})
        property string selectedChat: "chat-1"
        property var chats: [{id: "chat-1", label: "Tes Limit"}]

        property bool limitsLoading: false
        property string limitsError: ""
        property var limits: ({
            "groups": [
                {
                    "label": "Gemini Models",
                    "scope": "Models within this group: Gemini 3.8 Flash",
                    "buckets": [
                        {"label": "5h", "name": "Five Hour Limit Remaining", "window": "5h", "remaining": 99.0, "reset": "2026-11-19T10:43:04Z"},
                        {"label": "weekly", "name": "Weekly Limit Remaining", "window": "weekly", "remaining": 90.1, "reset": "2026-11-25T06:12:31Z"}
                    ]
                },
                {
                    "label": "Claude and GPT models",
                    "scope": "Models within this group: Claude Sonnet 4.6, Claude Opus 4.6, GPT-OSS 120B",
                    "buckets": [
                        {"label": "5h", "name": "Five Hour Limit Remaining", "window": "5h", "remaining": 99.9, "reset": "2026-11-19T07:22:51Z"},
                        {"label": "weekly", "name": "Weekly Limit Remaining", "window": "weekly", "remaining": 100.0, "reset": "2026-11-26T02:22:51Z"}
                    ]
                }
            ],
            "quotaAvailable": true,
            "report": "",
            "source": "AGY /usage"
        })

        function labels(lang) {
            return {
                "assistant": "Agent", "account_limits": "Batas penggunaan akun",
                "refresh_usage": "Perbarui", "limits_loading": "Memeriksa penggunaan…",
                "limits_unavailable": "Backend belum melaporkan kuota.", "reset": "Reset",
                "quota_days": "{n} hari", "quota_hours": "{n} jam", "quota_minutes": "{n} menit",
                "choose_agent": "Pilih agent", "send": "Kirim", "stop": "Berhenti", "placeholder": "Tanya apa saja..."
            }
        }
        function refreshUsage() { console.log("[MOCK] refreshUsage called") }
        function selectProvider(key) { provider = key }
        function selectMode(m) { mode = m }
        function selectModel(m) { model = m }
        function send() { return true }
        function stop() {}
    }

    property var mockWorkspace: QtObject {
        property var agentChat: win.mockChat
    }

    WorkspaceAgentChatPanel {
        id: chatPanel
        anchors.fill: parent
        anchors.margins: 10
        workspace: win.mockWorkspace
        preferences: win.mockPrefs
    }
}
"""

def pause(ms=25):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()

def main():
    engine.loadData(qml_content.encode('utf-8'), QUrl.fromLocalFile(str(repo / "tools/limit_meter_fixture.qml")))
    if not engine.rootObjects():
        print("Failed to load QML!")
        for diag in diagnostics:
            print("DIAG:", diag)
        return

    win = engine.rootObjects()[0]
    out_dir = Path(r"C:\Users\ahsanihlwn\.gemini\antigravity\brain\41ae3517-0fca-4150-86c2-5f880c3f737f")

    for _ in range(20):
        pause(20)
        app.processEvents()

    # 1. Capture Header with Limit Meter Icon
    win.grabWindow().save(str(out_dir / "limit_meter_chat_header.png"))
    print("Saved limit_meter_chat_header.png")

    limit_meter = win.findChild(QObject, "agentChatLimitMeter")
    print("limit_meter item found:", limit_meter is not None)

    if limit_meter:
        # 2. Open Details Popup
        limit_meter.openDetails()
        for _ in range(25):
            pause(20)
            app.processEvents()
        win.grabWindow().save(str(out_dir / "limit_meter_popup_details.png"))
        print("Saved limit_meter_popup_details.png")

        # 3. Simulate Skeleton Loading State
        mock_chat = win.property("mockChat")
        mock_chat.setProperty("limitsLoading", True)
        for _ in range(15):
            pause(20)
            app.processEvents()
        win.grabWindow().save(str(out_dir / "limit_meter_loading_shimmer.png"))
        print("Saved limit_meter_loading_shimmer.png")

        # 4. Simulate Unknown Quota State
        mock_chat.setProperty("limitsLoading", False)
        mock_chat.setProperty("limits", {"groups": [], "quotaAvailable": False, "report": "No quota data"})
        for _ in range(15):
            pause(20)
            app.processEvents()
        win.grabWindow().save(str(out_dir / "limit_meter_unknown_question.png"))
        print("Saved limit_meter_unknown_question.png")

    win.close()
    print("Verification completed successfully!")

if __name__ == "__main__":
    main()
