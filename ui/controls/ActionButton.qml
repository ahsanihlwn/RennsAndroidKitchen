// SPDX-License-Identifier: MPL-2.0
import QtQuick

RakButton {
    id: control
    required property var colors
    property bool primary: false
    // Explicit visibility survives a hidden parent page, like QWidget.isHidden.
    // PageFooter still needs those outgoing actions for its transition.
    property bool actionVisible: true
    visible: actionVisible
    fillColor: primary ? (colors.BTN_PRIMARY_BG || colors.a20) : (colors.BTN_SEC_BG || colors.b05)
    hoverFillColor: primary ? (colors.BTN_PRIMARY_HOVER || colors.a32) : (colors.BTN_SEC_HOVER || colors.b09)
    downFillColor: primary ? (colors.BTN_PRIMARY_DOWN || colors.a44) : (colors.BTN_SEC_DOWN || colors.b13)
    edgeColor: primary ? (colors.BTN_PRIMARY_EDGE || colors.a45) : (colors.BTN_SEC_EDGE || colors.b10)
    hoverEdgeColor: primary ? (colors.BTN_PRIMARY_EDGE_HOVER || colors.a65) : (colors.BTN_SEC_EDGE_HOVER || colors.b18)
    labelColor: primary ? (colors.BTN_PRIMARY_FG || colors.FG_BRIGHT) : (colors.BTN_SEC_FG || colors.FG)
    hoverLabelColor: primary ? (colors.BTN_PRIMARY_FG_HOVER || colors.FG_BRIGHT) : (colors.BTN_SEC_FG_HOVER || colors.FG_BRIGHT)
    disabledFillColor: primary ? (colors.BTN_PRIMARY_DISABLED_BG || colors.a07) : (colors.BTN_SEC_DISABLED_BG || colors.b02)
    disabledEdgeColor: primary ? (colors.BTN_PRIMARY_DISABLED_EDGE || colors.a12) : (colors.BTN_SEC_DISABLED_EDGE || colors.b04)
    disabledLabelColor: primary ? (colors.BTN_PRIMARY_DISABLED_FG || colors.a30) : (colors.BTN_SEC_DISABLED_FG || colors.FG_DISABLED2)
    loadingAccentColor: primary ? (colors.accent_hex || colors.a65) : (colors.accent_hex || colors.b20)
    font.weight: primary ? Font.DemiBold : Font.Normal
}
