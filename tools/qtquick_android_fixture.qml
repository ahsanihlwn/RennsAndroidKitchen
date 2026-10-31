// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"
Window {
    id: window; width: 1200; height: 820; color: preferences.colors.BG; visible: true
    required property var preferences
    required property var workspace
    readonly property Item backdropSurface: background
    readonly property var managedDialogs: [tools]
    readonly property bool nativeDialogActive: tools.presented
    SmoothRectangle { id: background; anchors.fill: parent; color: preferences.colors.BG }
    WorkspaceAndroidTools { id: tools; model: window.preferences; workspace: window.workspace; hostWindow: window }
}
