// SPDX-License-Identifier: MPL-2.0
import QtQuick

RakField {
    required property var colors
    implicitHeight: 30; cornerRadius: 4; font.pixelSize: 11
    fillColor: colors.BG_INPUT; edgeColor: colors.b10; focusEdgeColor: colors.a55
    labelColor: colors.FG; hintColor: colors.FG_MUTED
    leftPadding: 11; rightPadding: 11; topPadding: 7; bottomPadding: 5
}
