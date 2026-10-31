// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

C.ToolTip {
    id: tip
    required property string label
    readonly property var colors: typeof rakPreferences!=="undefined" ? rakPreferences.colors : ({BG_MODAL:"#22242e",FG:"#c9d1d9",b10:"#1affffff"})
    property bool hovering: false
    text: label
    visible: hovering && label.length>0
    delay: 700
    timeout: 10000
    font.family: "Poppins"
    font.pixelSize: 12
    background: SmoothRectangle { color: tip.colors.BG_MODAL; radius: 6; border.color: tip.colors.b10 }
    contentItem: Text { text: tip.text; font: tip.font; color: tip.colors.FG; wrapMode: Text.Wrap; renderType: Text.NativeRendering }
}
