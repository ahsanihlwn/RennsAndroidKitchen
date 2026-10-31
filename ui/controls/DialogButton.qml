// SPDX-License-Identifier: MPL-2.0
import QtQuick

ActionButton {
    property bool danger: false
    implicitWidth: contentItem.implicitWidth+30
    implicitHeight: 30
    cornerRadius: 6
    fillColor: danger?colors.red14:primary?colors.a18:colors.b06
    hoverFillColor: danger?colors.red28:primary?colors.a32:colors.b10
    downFillColor: danger?colors.red42:primary?colors.a48:colors.b14
    edgeColor: danger?colors.red32:primary?colors.a38:colors.b10
    hoverEdgeColor: danger?colors.red55:primary?colors.a60:colors.b18
    labelColor: danger?colors.RED_LIGHT:primary?colors.FG_BLUE:colors.FG3
    hoverLabelColor: danger?colors.RED_LIGHTER:primary?colors.FG_BLUE_LIGHT:colors.FG_HOVER
    font.weight: danger||primary?Font.DemiBold:Font.Normal
}
