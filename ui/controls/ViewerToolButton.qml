// SPDX-License-Identifier: MPL-2.0
import QtQuick

RakButton {
    required property var colors
    property bool primary: false
    implicitWidth: Math.ceil(contentItem.implicitWidth)+(primary?22:16)
    implicitHeight: primary?28:24
    font.pixelSize: 11; font.weight: primary?Font.Medium:Font.Normal
    leftPadding: primary?10:8; rightPadding: leftPadding; cornerRadius: 4
    edgeWidth: primary?1:0; edgeColor: colors.a38; hoverEdgeColor: colors.a55
    fillColor: primary?colors.a18:"transparent"
    hoverFillColor: primary?colors.a28:colors.b08
    downFillColor: primary?colors.a40:colors.a20
    labelColor: primary?colors.FG_BLUE:colors.FG2
    hoverLabelColor: primary?colors.FG_BLUE_LIGHT:colors.FG_BRIGHT
    disabledLabelColor: colors.FG_MUTED
}
