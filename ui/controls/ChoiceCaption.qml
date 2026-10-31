// SPDX-License-Identifier: MPL-2.0
import QtQuick

Item {
    id: caption
    required property var colors
    property string text: ""
    property font captionFont
    property color labelColor: colors.FG_BLUE_MUTED
    property bool moving: false
    property string arrowName: ""
    readonly property bool arrowReady: arrow.status===Image.Ready
    implicitWidth: label.implicitWidth
    Text {
        id: label
        objectName: "choiceCaption_"+caption.arrowName
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        text: caption.text; font: caption.captionFont; color: caption.labelColor
        renderType: caption.moving ? Text.CurveRendering : Text.NativeRendering
    }
    Image {
        id: arrow
        objectName: caption.arrowName
        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
        width: 9; height: 9; sourceSize: caption.moving ? Qt.size(18,18) : Qt.size(9,9)
        source: "image://rak/arrow_right?color="+encodeURIComponent(caption.colors.FG2)
        rotation: 90
    }
}
