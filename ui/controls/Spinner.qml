// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Shapes
import QtQuick.Window

Item {
    id: spinner
    property color accent: "#4d8ef0"
    property real thickness: 7
    property real inset: thickness
    property real phase: 0
    property color trackColor: Qt.rgba(accent.r,accent.g,accent.b,46/255)
    property real sweep: -105
    property int rotationDuration: 1008
    property bool spinning: visible && (Window.window?Window.window.visible:true)
    readonly property bool curveRenderer: shape.rendererType===Shape.CurveRenderer
    width: 38; height: 38
    Shape {
        id: shape
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        antialiasing: true
        rotation: spinner.phase
        ShapePath {
            strokeWidth: spinner.thickness
            strokeColor: spinner.trackColor
            fillColor: "transparent"; capStyle: ShapePath.RoundCap
            PathAngleArc { centerX: spinner.width/2; centerY: spinner.height/2; radiusX: spinner.width/2-spinner.inset; radiusY: spinner.height/2-spinner.inset; startAngle: 0; sweepAngle: 360 }
        }
        ShapePath {
            strokeWidth: spinner.thickness; strokeColor: spinner.accent
            fillColor: "transparent"; capStyle: ShapePath.RoundCap
            PathAngleArc { centerX: spinner.width/2; centerY: spinner.height/2; radiusX: spinner.width/2-spinner.inset; radiusY: spinner.height/2-spinner.inset; startAngle: 0; sweepAngle: spinner.sweep }
        }
    }
    NumberAnimation on phase { from: 0; to: 360; duration: spinner.rotationDuration; loops: Animation.Infinite; running: spinner.spinning }
}
