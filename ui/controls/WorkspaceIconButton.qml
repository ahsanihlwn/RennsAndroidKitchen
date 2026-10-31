// SPDX-License-Identifier: MPL-2.0
import QtQuick

RakButton {
    id: button
    required property var preferences
    property string iconKey: ""
    property bool circular: false
    property color tint: "transparent"
    implicitWidth: 28; implicitHeight: 28
    leftPadding: 0; rightPadding: 0
    iconSource: iconKey.length ? "image://icons/"+iconKey+(tint.a>0?"?color="+encodeURIComponent(tint.toString()):"") : ""
    iconWidth: 16; iconHeight: 16
    cornerRadius: circular?width/2:5
    edgeWidth: 0
    fillColor: "transparent"
    hoverFillColor: preferences.colors.b08
    downFillColor: preferences.colors.b13
    labelColor: preferences.colors.FG
    hoverLabelColor: preferences.colors.FG_BRIGHT
    disabledLabelColor: preferences.colors.FG_DISABLED2
    animatePress: preferences.settings.animations
}
