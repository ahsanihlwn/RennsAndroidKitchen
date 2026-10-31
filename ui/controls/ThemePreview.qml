// SPDX-License-Identifier: MPL-2.0
import QtQuick

RakButton {
    id: tile
    required property var colors
    property bool selected: false
    signal chosen()
    width: 96; height: 68
    SmoothRectangle {
        anchors.fill: parent; anchors.margins: 1
        radius: 5; color: tile.colors.BG_DARK || tile.colors.BG
        SmoothRectangle { x: 0; y: 12; width: 22; height: parent.height - 12; color: tile.colors.BG2; radius: 5 }
        SmoothRectangle { x: 8; y: 12; width: 14; height: parent.height - 12; color: tile.colors.BG2 }
        SmoothRectangle { x: 22; y: 12; width: parent.width - 22; height: parent.height - 12; color: tile.colors.BG; radius: 5 }
        SmoothRectangle { x: 22; y: 12; width: parent.width - 22; height: parent.height - 18; color: tile.colors.BG }
        Row { x: 3; y: 3; spacing: 3; Repeater { model: ["#ff5f57", "#ffbc2e", "#28c840"]; SmoothRectangle { required property string modelData; width: 5; height: 5; radius: 2.5; color: modelData } } }
        Repeater {
            model: 3
            SmoothRectangle { required property int index; x: 2; y: 16 + index * 9; width: 16; height: 5; radius: 1; color: index === 0 ? tile.colors.accent_hex : tile.colors.FG2; opacity: index === 0 ? 150/255 : 50/255 }
        }
        SmoothRectangle { x: 26; y: 17; width: 44; height: 3; radius: 1; color: tile.colors.FG; opacity: 65/255 }
        SmoothRectangle { x: 26; y: 24; width: 57; height: 2; radius: 1; color: tile.colors.FG; opacity: 35/255 }
        SmoothRectangle { x: 26; y: 30; width: 38; height: 2; radius: 1; color: tile.colors.FG; opacity: 35/255 }
        SmoothRectangle { x: 26; y: 37; width: 28; height: 6; radius: 2; color: tile.colors.accent_hex; opacity: 210/255 }
    }
    SmoothRectangle { anchors.fill: parent; radius: 6; color: "transparent"; border.width: tile.selected ? 2.5 : 1; border.pixelAligned: !tile.selected; border.color: tile.selected ? tile.colors.accent_hex : tile.colors._is_light ? "#50000000" : "#1effffff" }
    contentItem: Item {}
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    onClicked: tile.chosen()
}
