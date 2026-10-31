// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    id: window
    required property var preferences
    required property var commands
    required property var details
    width: 1080; height: 650; visible: true; color: preferences.colors.BG3
    WorkspacePartitionViewer { anchors.fill: parent; workspace: window.commands; preferences: window.preferences; details: window.details }
}
