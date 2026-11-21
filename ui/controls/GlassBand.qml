// SPDX-License-Identifier: MPL-2.0
import QtQuick

LiveGlass {
    required property var model
    preferences: model
    animateChanges: model.settings.animations
    readonly property color panel: model.colors.BG
    effectsEnabled: model.settings.effects==="high"
    flatColor: panel
    tintColor: Qt.rgba(panel.r,panel.g,panel.b,.58)
    cornerRadius: 0
    edgeColor: "transparent"
    sampleInSourceCoordinates: true
    fadeEdge: origin.y>0 ? "top" : "bottom"
    fadeSize: 24
    HoverHandler { blocking: true; cursorShape: Qt.ArrowCursor }
    MouseArea { anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.AllButtons; onWheel: wheel=>wheel.accepted=true }
}
