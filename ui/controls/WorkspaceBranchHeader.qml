// SPDX-License-Identifier: MPL-2.0
import QtQuick

Row {
    id: header
    required property var colors
    property string title: "NeoBranch"
    property string root: ""
    spacing: 8; height: 24
    RakLabel { width: 20; height: header.height; text: "⑂"; font.pixelSize: 15; color: header.colors.accent_hex; verticalAlignment: Text.AlignVCenter }
    RakLabel { height: header.height; text: header.title; color: header.colors.FG; font.pixelSize: 14; font.weight: Font.DemiBold; verticalAlignment: Text.AlignVCenter }
    Item {
        width: badgePanel.width+4; height: header.height
        SmoothRectangle {
        id: badgePanel; x: 4
        width: Math.ceil(badge.implicitWidth+24); height: header.height; radius: 5; color: header.colors.a14
        border.width: 1; border.color: header.colors.a28
        RakLabel { id: badge; x: 11; anchors.verticalCenter: parent.verticalCenter; text: header.root; color: header.colors.FG_BLUE; font.pixelSize: 11; font.weight: Font.DemiBold }
        }
    }
}
