// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"
Window {
    id: window
    required property var workspace
    required property var preferences
    readonly property Item backdropSurface: page
    width: 1080; height: 700; visible: true; color: preferences.colors.BG3
    Rectangle { id: page; anchors.fill: parent; color: window.preferences.colors.BG3 }
    WorkspaceContextMenu { id: menu; workspace: window.workspace; preferences: window.preferences; hostWindow: window }
    Connections {
        target: window.workspace
        function onPresentationRequested(kind,data){if(kind==="contextMenu")menu.showMenu(data)}
    }
}
