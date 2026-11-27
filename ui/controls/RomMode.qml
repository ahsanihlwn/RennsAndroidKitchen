// SPDX-License-Identifier: MPL-2.0
import QtQuick

RakButton {
    id: control
    required property var colors
    property bool selected: false
    property int dots: 1
    implicitHeight: 40
    cornerRadius: 8
    edgeWidth: 1
    fillColor: selected ? (colors.a15 || colors.a18 || "transparent") : (colors.b04 || "transparent")
    hoverFillColor: selected ? (colors.a20 || colors.a24 || "transparent") : (colors.b08 || "transparent")
    edgeColor: selected ? (colors.a45 || colors.a40 || "transparent") : (colors.b08 || "transparent")
    labelColor: selected ? (colors.FG_BLUE || "#c9d1d9") : (colors.FG_MUTED || "#8b949e")
    hoverLabelColor: selected ? (colors.FG_BLUE || "#c9d1d9") : (colors.FG || "#ffffff")
    font.pixelSize: 13
    font.weight: selected ? Font.DemiBold : Font.Normal
    contentItem: Item {
        Text {
            objectName: "buttonCaption_"+control.objectName
            anchors.centerIn: parent
            anchors.verticalCenterOffset: 1
            text: (control.dots===2?"\u2b24\u2b24  ":"\u2b24  ")+control.text
            font: control.font
            color: control.hovered ? control.hoverLabelColor : control.labelColor
            renderType: control.transformed ? Text.CurveRendering : Text.NativeRendering
        }
    }
}
