// SPDX-License-Identifier: MPL-2.0
import QtQuick

Image {
    id: frame
    property bool animationsEnabled: true
    property int duration: 240
    opacity: 0
    visible: opacity>0
    asynchronous: false; cache: false
    function start() { fade.stop(); opacity=1; fade.start() }
    onAnimationsEnabledChanged: if (!animationsEnabled) { fade.stop(); opacity=0 }
    NumberAnimation { id: fade; target: frame; property: "opacity"; to: 0; duration: frame.animationsEnabled?frame.duration:0; easing.type: Easing.OutCubic }
}
