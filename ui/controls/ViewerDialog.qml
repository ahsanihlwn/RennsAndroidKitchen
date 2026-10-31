// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window

Window {
    id: dialog
    required property var colors
    required property var ownerWindow
    flags: Qt.Dialog; modality: Qt.WindowModal; transientParent: ownerWindow
    color: colors.BG
    function open() {
        x=ownerWindow.x+Math.round((ownerWindow.width-width)/2)
        y=ownerWindow.y+Math.round((ownerWindow.height-height)/2)
        show();requestActivate()
    }
    Shortcut { sequence: "Escape"; context: Qt.WindowShortcut; onActivated: dialog.close() }
}
