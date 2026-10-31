// SPDX-License-Identifier: MPL-2.0
import QtQuick

Text {
    id: label
    property string toolTip: ""
    property bool transformed: false
    color: "#c9d1d9"
    font.family: "Poppins"
    font.pixelSize: 13
    font.hintingPreference: Font.PreferVerticalHinting
    renderType: transformed ? Text.CurveRendering : Text.NativeRendering
    textFormat: Text.AutoText
    verticalAlignment: Text.AlignVCenter
    HoverHandler { id: hover; enabled: label.toolTip.length>0 }
    HoverTip { label: label.toolTip; hovering: hover.hovered }
}
