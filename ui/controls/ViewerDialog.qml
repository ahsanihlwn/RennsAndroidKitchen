// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window

Window {
    id: dialog
    required property var colors
    required property var ownerWindow
    default property alias body: stage.data
    readonly property bool animationsEnabled: typeof rakPreferences !== "undefined" && rakPreferences && rakPreferences.settings ? !!rakPreferences.settings.animations : true
    flags: Qt.Dialog; modality: Qt.WindowModal; transientParent: ownerWindow
    color: colors.BG
    function open() {
        x=ownerWindow.x+Math.round((ownerWindow.width-width)/2)
        y=ownerWindow.y+Math.round((ownerWindow.height-height)/2)
        show();requestActivate()
    }
    onVisibleChanged: {
        if (visible && animationsEnabled) {
            openScale.restart()
        } else {
            openScale.stop()
            stage.scale = 1; stage.opacity = 1
        }
    }
    Item {
        id: stage
        anchors.fill: parent
        transformOrigin: Item.Center
        ParallelAnimation {
            id: openScale
            NumberAnimation { target: stage; property: "scale"; from: 0.95; to: 1.0; duration: 160; easing.type: Easing.OutCubic }
            NumberAnimation { target: stage; property: "opacity"; from: 0.0; to: 1.0; duration: 150; easing.type: Easing.OutCubic }
        }
    }
    Shortcut { sequence: "Escape"; context: Qt.WindowShortcut; onActivated: dialog.close() }
}
