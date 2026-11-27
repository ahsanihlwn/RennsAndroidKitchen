// SPDX-License-Identifier: MPL-2.0
import QtQuick

ActionButton {
    property bool danger: false
    implicitWidth: contentItem.implicitWidth+30
    implicitHeight: 30
    cornerRadius: 6
    fillColor: danger?(colors.BTN_DANGER_BG || colors.red18):primary?(colors.BTN_PRIMARY_BG || colors.a20):(colors.BTN_SEC_BG || colors.b06)
    hoverFillColor: danger?(colors.BTN_DANGER_HOVER || colors.red28):primary?(colors.BTN_PRIMARY_HOVER || colors.a32):(colors.BTN_SEC_HOVER || colors.b10)
    downFillColor: danger?(colors.BTN_DANGER_DOWN || colors.red40):primary?(colors.BTN_PRIMARY_DOWN || colors.a44):(colors.BTN_SEC_DOWN || colors.b14)
    edgeColor: danger?(colors.BTN_DANGER_EDGE || colors.red48):primary?(colors.BTN_PRIMARY_EDGE || colors.a45):(colors.BTN_SEC_EDGE || colors.b10)
    hoverEdgeColor: danger?(colors.BTN_DANGER_EDGE_HOVER || colors.red65):primary?(colors.BTN_PRIMARY_EDGE_HOVER || colors.a65):(colors.BTN_SEC_EDGE_HOVER || colors.b18)
    labelColor: danger?(colors.BTN_DANGER_FG || colors.RED_LIGHT):primary?(colors.BTN_PRIMARY_FG || colors.FG_BRIGHT):(colors.BTN_SEC_FG || colors.FG)
    hoverLabelColor: danger?(colors.BTN_DANGER_FG_HOVER || colors.RED_LIGHTER):primary?(colors.BTN_PRIMARY_FG_HOVER || colors.FG_BRIGHT):(colors.BTN_SEC_FG_HOVER || colors.FG_BRIGHT)
    font.weight: danger||primary?Font.DemiBold:Font.Normal
}
