// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Shapes

Item {
    id: pin
    property color color: "#3ecf8e"
    property string toolTip: ""
    HoverHandler { id: hover }
    HoverTip { label: pin.toolTip; hovering: hover.hovered }
    width: 12; height: 12; clip: true
    SmoothRectangle { x: 2.64; y: 0; width: 6.72; height: 6.72; radius: 3.36; color: pin.color }
    Shape {
        anchors.fill: parent
        ShapePath {
            fillColor: pin.color; strokeColor: "transparent"
            PathSvg { path: "M 6 6.24 L 2.64 8.64 L 6 11.04 L 9.36 8.64 Z" }
        }
    }
    SmoothRectangle { x: 5.52; y: 10.08; width: .96; height: 2.16; radius: .48; color: pin.color }
}
