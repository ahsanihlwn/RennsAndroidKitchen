// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Effects
import QtQuick.Window

Item {
    id: image
    property alias source: bitmap.source
    property real radius: Math.min(width,height)/2
    property real samplingExtent: 0
    Image {
        id: bitmap; anchors.fill: parent
        sourceSize: Qt.size(Math.ceil((image.samplingExtent || image.width)*2*Screen.devicePixelRatio),Math.ceil((image.samplingExtent || image.height)*2*Screen.devicePixelRatio))
        fillMode: Image.PreserveAspectCrop
        smooth: true; mipmap: true; visible: false
    }
    SmoothRectangle { id: mask; anchors.fill: parent; color: "white"; radius: image.radius; antialiasing: true; visible: false }
    ShaderEffectSource {
        id: maskTexture; sourceItem: mask; hideSource: true; visible: false; smooth: true
        textureSize: Qt.size(Math.ceil((image.samplingExtent || image.width)*2*Screen.devicePixelRatio),Math.ceil((image.samplingExtent || image.height)*2*Screen.devicePixelRatio))
    }
    MultiEffect {
        anchors.fill: parent; source: bitmap; maskEnabled: true; maskSource: maskTexture
        maskThresholdMin: .5; maskSpreadAtMin: 1
    }
}
