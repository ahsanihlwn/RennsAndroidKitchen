// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Shapes

Item {
    id: pin
    property color color: "#f59e0b"
    property string toolTip: ""
    HoverHandler { id: hover }
    HoverTip { label: pin.toolTip; hovering: hover.hovered }
    width: 12; height: 12; clip: true
    Shape {
        anchors.fill: parent
        ShapePath {
            fillColor: pin.color; strokeColor: "transparent"
            PathSvg { path: "M 6 1 L 7.55 4.13 L 11 4.64 L 8.5 7.07 L 9.09 10.51 L 6 8.89 L 2.91 10.51 L 3.5 7.07 L 1 4.64 L 4.45 4.13 Z" }
        }
    }
}
