// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Shapes

SmoothRectangle {
    id: splash
    required property var model
    property bool active: model.startupActive
    objectName: "startupSplash"
    color: model.colors.BG
    visible: active
    enabled: visible && opacity>.001
    opacity: 1
    signal revealed()
    function reveal() {
        if (!visible) return
        if (fade.running) return
        if (model.settings.animations) fade.start()
        else { opacity = 0; revealed() }
    }
    Image {
        id: logo
        objectName: "splashLogo"
        width: 68; height: 68
        x: Math.round((splash.width-width)/2)
        y: Math.round((splash.height-height)/2)-22
        source: splash.model.icon("splash", splash.model.colors.FG)
        sourceSize.width: width; sourceSize.height: height
    }
    Shape {
        id: glint
        preferredRendererType: Shape.CurveRenderer
        antialiasing: true
        objectName: "splashGlint"
        x: logo.x-6; y: logo.y-6; width: 80; height: 80
        property real phase: 0
        readonly property color accent: splash.model.colors.accent_hex
        ShapePath {
            strokeColor: Qt.rgba(glint.accent.r,glint.accent.g,glint.accent.b,42/255)
            strokeWidth: .7; fillColor: "transparent"
            PathSvg { path: splash.model.splashOutline }
        }
        ShapePath {
            strokeColor: Qt.rgba(glint.accent.r,glint.accent.g,glint.accent.b,28/255)
            strokeWidth: 4; fillColor: "transparent"; capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
            trim.start: 0; trim.end: .14; trim.offset: glint.phase
            PathSvg { path: splash.model.splashOutline }
        }
        ShapePath {
            strokeColor: Qt.rgba(glint.accent.r,glint.accent.g,glint.accent.b,235/255)
            strokeWidth: 1.6; fillColor: "transparent"; capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
            trim.start: 0; trim.end: .14; trim.offset: glint.phase
            PathSvg { path: splash.model.splashOutline }
        }
        NumberAnimation on phase { from: 0; to: 1; duration: 1600; loops: Animation.Infinite; running: splash.visible }
    }
    Image {
        objectName: "splashBrand"
        width: splash.model.splashBrand.width; height: 20
        x: Math.round((splash.width-width)/2)
        y: splash.height-Math.max(26,Math.round(splash.height*.045))-4-height/2
        source: splash.model.icon("rennsproject_logo", splash.model.colors.FG)
        sourceSize.width: splash.model.splashBrand.sourceWidth; sourceSize.height: splash.model.splashBrand.sourceHeight
    }
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: wheel => wheel.accepted = true }
    NumberAnimation { id: fade; target: splash; property: "opacity"; to: 0; duration: 200; easing.type: Easing.OutCubic; onFinished: splash.revealed() }
}
