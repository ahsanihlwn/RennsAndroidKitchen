// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window

Item {
    id: path
    property string text: ""
    property color color: "#888888"
    property bool moving: true
    property bool transformed: false
    property real frozenOffset: 0
    property real offset: 0
    property int direction: 1
    property int pauseTicks: 16
    readonly property real implicitTextWidth: caption.implicitWidth
    readonly property int maximum: Math.max(0,Math.round(caption.implicitWidth)-width+2)
    function reset() { offset=0; direction=1; pauseTicks=16 }
    clip: true
    onMovingChanged: if (!moving) reset()
    onMaximumChanged: if (!maximum) reset()
    onTextChanged: reset()
    RakLabel { id: caption; x: -(path.moving?path.offset:path.frozenOffset); height: path.height; text: path.text; color: path.color; font.pixelSize: 10; transformed: path.transformed }
    Timer {
        interval: 50; repeat: true
        running: path.moving && path.maximum>0 && path.visible && (path.Window.window?path.Window.window.visible:true)
        onTriggered: {
            if (path.pauseTicks) { --path.pauseTicks; return }
            path.offset+=path.direction*2
            if (path.offset>=path.maximum) { path.offset=path.maximum; path.direction=-1; path.pauseTicks=16 }
            else if (path.offset<=0) { path.offset=0; path.direction=1; path.pauseTicks=16 }
        }
    }
    HoverHandler { id: hover }
    HoverTip { label: path.text; hovering: hover.hovered }
}
