// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"
import "../ui"

Window {
    id: window
    required property var preferences
    required property var commands
    required property var details
    readonly property Item backdropSurface: surface
    readonly property var managedDialogs: [message]
    width: 1080; height: 650; visible: true; color: preferences.colors.BG3
    SmoothRectangle { id: surface; anchors.fill: parent; color: window.preferences.colors.BG3 }
    WorkspaceHexViewer { anchors.fill: parent; workspace: window.commands; preferences: window.preferences; details: window.details }
    MessageWindow { id: message; model: window.preferences; commandTarget: window.commands; hostWindow: window }
    Connections { target: commands; function onMessage(title,text) {message.showMessage({title:title,text:text})} }
}
