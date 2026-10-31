// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Templates as T

T.TextField {
    id: control
    AgentControlBinding { target: control; kind: "text"; label: control.placeholderText; permitted: !control.readOnly && control.echoMode===TextInput.Normal
        activate: value=>{if(typeof value!=="string" || value.length>8192)throw new Error("Text value must contain at most 8192 characters");control.text=value;control.textEdited()} }
    property color fillColor: "#1a1a1a"
    property color edgeColor: "#1affffff"
    property color focusEdgeColor: "#8c2ecc8e"
    property color focusFillColor: fillColor
    property color disabledFillColor: fillColor
    property color disabledLabelColor: labelColor
    property color labelColor: "#c9d1d9"
    property color hintColor: "#666c80"
    property real cornerRadius: 7
    property real edgeWidth: 1
    implicitHeight: 38
    implicitWidth: 180
    leftPadding: 13
    rightPadding: 13
    // QLineEdit's integer baseline sits one pixel below TextInput's center
    // with Poppins. Keep typed text and the placeholder on that same line.
    topPadding: 7
    bottomPadding: 5
    color: enabled ? labelColor : disabledLabelColor
    placeholderTextColor: hintColor
    selectionColor: focusEdgeColor
    selectedTextColor: "white"
    font.family: "Poppins"
    font.pixelSize: 13
    font.hintingPreference: Font.PreferVerticalHinting
    renderType: TextInput.NativeRendering
    verticalAlignment: TextInput.AlignVCenter
    selectByMouse: true
    Text {
        x: control.leftPadding; y: control.topPadding
        width: Math.max(0,control.width-control.leftPadding-control.rightPadding)
        height: Math.max(0,control.height-control.topPadding-control.bottomPadding)
        text: control.placeholderText
        font: control.font
        color: control.placeholderTextColor
        renderType: Text.NativeRendering
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        visible: !control.length && !control.preeditText.length
    }
    background: SmoothRectangle {
        color: !control.enabled ? control.disabledFillColor : control.activeFocus ? control.focusFillColor : control.fillColor
        radius: control.cornerRadius
        border.width: control.edgeWidth
        border.color: control.activeFocus ? control.focusEdgeColor : control.edgeColor
    }
}
