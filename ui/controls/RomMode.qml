// SPDX-License-Identifier: MPL-2.0
import QtQuick

RakButton {
    id: control
    required property var colors
    property bool selected: false
    property int dots: 1
    implicitHeight: 40
    cornerRadius: 8
    edgeWidth: selected ? 1.5 : 1
    fillColor: selected ? colors.a18 : colors.b04
    hoverFillColor: selected ? colors.a18 : colors.b08
    edgeColor: selected ? colors.a50 : colors.b08
    labelColor: selected ? colors.FG_BLUE : colors.FG_MUTED
    hoverLabelColor: selected ? colors.FG_BLUE : colors.FG
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
