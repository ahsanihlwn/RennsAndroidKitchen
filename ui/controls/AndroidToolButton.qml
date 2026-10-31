// SPDX-License-Identifier: MPL-2.0
import QtQuick

RakButton {
    id: control
    required property var colors
    property bool selected: false
    property bool danger: false
    height: 24; implicitHeight: 24
    implicitWidth: Math.ceil(textMetrics.advanceWidth)+20
    font.pixelSize: 10; font.weight: selected?Font.DemiBold:Font.Normal
    leftPadding: 10; rightPadding: 10; cornerRadius: 5
    fillColor: selected?colors.a12:"transparent"
    hoverFillColor: danger?"#20e95761":colors.b06
    downFillColor: danger?"#30e95761":colors.a18
    edgeColor: selected?colors.a30:colors.b10
    hoverEdgeColor: danger?"#80e95761":colors.a35
    labelColor: danger?colors.RED_STATUS:selected?colors.accent_hex:colors.FG2
    disabledLabelColor: colors.FG3
    TextMetrics { id: textMetrics; text: control.text; font: control.font }
}
