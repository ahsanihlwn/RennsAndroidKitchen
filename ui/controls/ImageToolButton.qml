// SPDX-License-Identifier: MPL-2.0
import QtQuick
RakButton {
    required property var colors
    property bool selected: false
    height: 26; implicitWidth: iconSource.toString().length && !text.length?30:Math.max(26,Math.ceil(contentItem.implicitWidth)+22)
    cornerRadius: 6; font.pixelSize: 12; font.weight: selected?Font.DemiBold:Font.Normal
    leftPadding: 10; rightPadding: 10; iconWidth: 16; iconHeight: 16
    fillColor: selected?colors.a18:"transparent"; hoverFillColor: selected?colors.a18:colors.b07; downFillColor: colors.a20
    edgeColor: selected?colors.a40:colors.b08; hoverEdgeColor: selected?colors.a40:colors.b14
    labelColor: selected?colors.FG_BLUE:colors.FG_MUTED; hoverLabelColor: colors.FG
}
