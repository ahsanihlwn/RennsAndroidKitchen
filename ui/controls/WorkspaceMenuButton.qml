// SPDX-License-Identifier: MPL-2.0
import QtQuick

RakButton {
    id: button
    required property var preferences
    required property var row
    property string kind: "single"
    readonly property var c: preferences.colors
    readonly property bool separator: !!row.separator
    readonly property bool header: !!row.header
    readonly property bool mounted: kind==="mounted"
    readonly property color foreground: row.danger?c.RED_LIGHT:c.FG
    height: separator?1:header?28:32; implicitHeight: height
    leftAligned: true; leftPadding: mounted?12:14; rightPadding: kind==="single"?28:leftPadding
    font.pixelSize: header?10:12
    text: row.label || ""
    enabled: row.enabled!==false && !separator && !header
    edgeWidth: 0; cornerRadius: mounted?12:6
    fillColor: separator?c.b07:"transparent"
    hoverFillColor: mounted?c.BG3:row.danger?c.red14:c.a14
    downFillColor: mounted?c.BG3:c.b08
    labelColor: header?c.FG_MUTED:foreground
    hoverLabelColor: row.danger?c.RED_LIGHTER:c.FG_BRIGHT
    disabledLabelColor: header?c.FG_MUTED:mounted?c.FG_DIM:Qt.rgba(foreground.r,foreground.g,foreground.b,128/255)
    animatePress: preferences.settings.animations && !separator && !header
    Text {
        anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
        text: button.row.shortcut || ""
        visible: !button.row.children && !!button.row.shortcut
        font.pixelSize: 11
        renderType: button.transformed ? Text.CurveRendering : Text.NativeRendering
        color: button.enabled ? button.c.FG_MUTED : Qt.rgba(button.foreground.r,button.foreground.g,button.foreground.b,90/255)
    }
    Image {
        anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
        width: 12; height: 12; source: "image://icons/arrow_right?color="+encodeURIComponent(button.c.treeArrow)
        visible: !!button.row.children
    }
}
