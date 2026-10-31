// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

Item {
    id: handle
    required property var preferences
    property bool horizontal: false
    property real alignAt: (horizontal?width:height)/2
    implicitWidth: 8; implicitHeight: 8
    HoverHandler { id: hover; cursorShape: handle.horizontal?Qt.SplitVCursor:Qt.SplitHCursor }
    Repeater {
        model: 3
        SmoothRectangle {
            required property int index
            width: 4; height: 4; radius: 2
            x: handle.horizontal?handle.alignAt-2+(index-1)*5:Math.floor((handle.width-4)/2)
            y: handle.horizontal?Math.floor((handle.height-4)/2):handle.alignAt-2+(index-1)*5
            color: hover.hovered?handle.preferences.colors.a90:Qt.rgba(140/255,140/255,150/255,90/255)
        }
    }
}
