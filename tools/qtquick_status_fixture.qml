// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    id: window
    required property var workspace
    required property var preferences
    readonly property Item backdropSurface: page
    width: 720; height: 560; visible: true; color: preferences.colors.BG
    Rectangle { id: page; anchors.fill: parent; color: window.preferences.colors.BG }
    WorkspaceStatusBar {
        id: status
        objectName: "workspaceStatusBar"
        x: 16; y: 460; width: 500
        workspace: window.workspace; preferences: window.preferences
        onHistoryRequested: target => history.showAt(target)
    }
    WorkspaceHistory { id: history; workspace: window.workspace; preferences: window.preferences; hostWindow: window }
}
