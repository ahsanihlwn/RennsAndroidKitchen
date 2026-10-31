// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"
Window {
    id: window
    required property var workspace
    required property var preferences
    property real treeWidth: width-22
    width: 1000; height: 680; visible: true; color: preferences.colors.BG
    WorkspaceTree { x: 11; y: 11; width: window.treeWidth; height: parent.height-22; workspace: window.workspace; preferences: window.preferences; directory: workspace.treeModel; treeId: 0 }
}
