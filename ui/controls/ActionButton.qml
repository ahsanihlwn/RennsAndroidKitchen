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
    fillColor: primary ? colors.a18 : colors.b05
    hoverFillColor: primary ? colors.a30 : colors.b09
    downFillColor: primary ? colors.a44 : colors.b13
    edgeColor: primary ? colors.a38 : colors.b09
    hoverEdgeColor: primary ? colors.a55 : colors.b15
    labelColor: primary ? colors.FG_BLUE : colors.FG_BLUE_MUTED
    hoverLabelColor: primary ? colors.FG_BLUE_LIGHT : colors.FG_HOVER
    disabledFillColor: primary ? colors.a07 : colors.b02
    disabledEdgeColor: primary ? colors.a12 : colors.b04
    disabledLabelColor: primary ? colors.a30 : colors.FG_DISABLED2
    font.weight: primary ? Font.DemiBold : Font.Normal
}
