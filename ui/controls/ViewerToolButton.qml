// SPDX-License-Identifier: MPL-2.0
import QtQuick

RakButton {
    required property var colors
    property bool primary: false
    implicitWidth: Math.ceil(contentItem.implicitWidth)+(primary?22:16)
    implicitHeight: primary?28:24
    font.pixelSize: 11; font.weight: primary?Font.Medium:Font.Normal
    leftPadding: primary?10:8; rightPadding: leftPadding; cornerRadius: 4
    edgeWidth: primary?1:0; edgeColor: primary?(colors.BTN_PRIMARY_EDGE || colors.a38):"transparent"; hoverEdgeColor: primary?(colors.BTN_PRIMARY_EDGE_HOVER || colors.a55):colors.b12
    fillColor: primary?(colors.BTN_PRIMARY_BG || colors.a18):"transparent"
    hoverFillColor: primary?(colors.BTN_PRIMARY_HOVER || colors.a28):colors.b08
    downFillColor: primary?(colors.BTN_PRIMARY_DOWN || colors.a40):colors.b12
    labelColor: primary?(colors.BTN_PRIMARY_FG || colors.FG_BRIGHT):colors.FG2
    hoverLabelColor: primary?(colors.BTN_PRIMARY_FG_HOVER || colors.FG_BRIGHT):colors.FG_BRIGHT
    disabledFillColor: primary?(colors.BTN_PRIMARY_DISABLED_BG || colors.a07):"transparent"
    disabledEdgeColor: primary?(colors.BTN_PRIMARY_DISABLED_EDGE || colors.a12):"transparent"
    disabledLabelColor: primary?(colors.BTN_PRIMARY_DISABLED_FG || colors.FG_MUTED):colors.FG_MUTED
}
