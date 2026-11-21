// SPDX-License-Identifier: MPL-2.0
import QtQuick

Text {
    id: rootLabel
    property string toolTip: ""
    property bool transformed: false
    color: "#c9d1d9"
    font.family: "Poppins"
    font.pixelSize: 13
    font.hintingPreference: Font.PreferVerticalHinting
    renderType: transformed ? Text.CurveRendering : Text.NativeRendering
    textFormat: Text.AutoText
    verticalAlignment: Text.AlignVCenter
    Loader {
        active: rootLabel.toolTip.length > 0
        anchors.fill: parent
        sourceComponent: Item {
            HoverHandler { id: hover }
            HoverTip { label: rootLabel.toolTip; hovering: hover.hovered }
        }
    }
}
