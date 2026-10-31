// SPDX-License-Identifier: MPL-2.0
import QtQuick

Item {
    id: effect
    property bool active: false
    property bool animated: true
    property bool glowEnabled: true
    property real radius: 8
    property real shadowSpread: 10
    property real shadowOpacity: 0.65
    property real phase: 0

    enabled: false
    opacity: active ? 1 : 0
    visible: opacity > 0.001

    Behavior on opacity {
        enabled: effect.animated
        NumberAnimation { duration: effect.active ? 240 : 420; easing.type: Easing.InOutQuad }
    }

    ShaderEffect {
        id: shader
        anchors.fill: parent
        anchors.margins: -effect.shadowSpread
        visible: effect.glowEnabled

        property vector2d itemSize: Qt.vector2d(width, height)
        property real cornerRadius: effect.radius
        property real shadowSpread: effect.shadowSpread
        property real phase: effect.phase
        property real shadowOpacity: effect.shadowOpacity

        fragmentShader: Qt.resolvedUrl("../shaders/rainbow_shadow.frag.qsb")
    }

    NumberAnimation on phase {
        from: 0; to: 1; duration: 9000
        loops: Animation.Infinite
        running: effect.visible && effect.animated
    }
}
