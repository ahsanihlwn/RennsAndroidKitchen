// SPDX-License-Identifier: MPL-2.0
import QtQuick
RakButton {
    required property var colors
    property bool selected: false
    property bool segmented: false
    readonly property bool hasIcon: (iconKey && iconKey.length > 0) || iconSource.toString().length > 0
    height: 26
    implicitWidth: hasIcon && !text.length ? 30 : Math.max(26, Math.ceil(contentItem.implicitWidth) + 20)
    cornerRadius: 6; font.pixelSize: 12; font.weight: selected ? Font.DemiBold : Font.Normal
    leftPadding: hasIcon && !text.length ? 0 : 10
    rightPadding: hasIcon && !text.length ? 0 : 10
    iconWidth: 15; iconHeight: 15
    fillColor: selected ? colors.a16 : "transparent"
    hoverFillColor: selected ? colors.a20 : colors.b07
    downFillColor: colors.a24
    disabledFillColor: selected ? colors.a10 : "transparent"
    edgeColor: selected ? colors.a35 : (segmented ? "transparent" : colors.b08)
    hoverEdgeColor: selected ? colors.a45 : (segmented ? colors.b10 : colors.b14)
    disabledEdgeColor: segmented ? "transparent" : colors.b05
    labelColor: selected ? colors.FG_BLUE : colors.FG_MUTED
    hoverLabelColor: colors.FG
    disabledLabelColor: colors.FG_DISABLED2 || colors.b28
}
