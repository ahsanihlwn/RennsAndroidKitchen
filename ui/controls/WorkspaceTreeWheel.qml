// SPDX-License-Identifier: MPL-2.0
import QtQuick

Item {
    id: scroll
    required property var view
    // Ordinary wheel input stays entirely on Qt's original scrolling path.
    // Ctrl selects a fixed, bounded fast step without automatic acceleration.
    readonly property real rowStep: 24*Qt.styleHints.wheelScrollLines
    readonly property real fastMultiplier: 2.25
    readonly property real maximumSpeed: 2400
    readonly property real maximumPending: 224
    property real targetY: 0
    property real lastFrameTime: 0
    property int direction: 0
    property bool applying: false
    readonly property bool running: motion.running

    function bounded(position) {
        const minimum=-view.topMargin
        return Math.max(minimum,Math.min(Math.max(minimum,view.contentHeight-view.height+view.bottomMargin),position))
    }
    function reset() {
        motion.stop();direction=0
        targetY=view.contentY
    }
    function handle(event) {
        if(!(event.modifiers&Qt.ControlModifier)
            || event.modifiers&(Qt.ShiftModifier|Qt.AltModifier|Qt.MetaModifier)
            || Math.abs(event.angleDelta.x)>Math.abs(event.angleDelta.y)
            || Math.abs(event.pixelDelta.x)>Math.abs(event.pixelDelta.y)) {
            reset();event.accepted=false;return
        }
        if(event.pixelDelta.y) {
            // Precision input stays direct, with the same explicit Ctrl boost.
            reset();view.cancelFlick()
            view.contentY=bounded(view.contentY-event.pixelDelta.y*fastMultiplier)
            event.accepted=true;return
        }
        const notches=-event.angleDelta.y/120
        if(!notches){event.accepted=false;return}
        const now=Date.now(),nextDirection=Math.sign(notches)
        if(nextDirection!==direction || !motion.running)targetY=view.contentY
        direction=nextDirection
        view.cancelFlick()
        const pending=Math.max(-maximumPending,Math.min(maximumPending,targetY-view.contentY+notches*rowStep*fastMultiplier))
        targetY=bounded(view.contentY+pending)
        if(!motion.running && Math.abs(targetY-view.contentY)>.1) {
            lastFrameTime=now;motion.start()
        }
        event.accepted=true
    }
    Connections {
        target: scroll.view
        function onContentYChanged() {if(!scroll.applying && scroll.running)scroll.reset()}
    }
    Timer {
        id: motion
        interval: 16; repeat: true
        onTriggered: {
            const now=Date.now(),elapsed=Math.max(1,Math.min(32,now-scroll.lastFrameTime))
            scroll.lastFrameTime=now
            scroll.targetY=scroll.bounded(scroll.targetY)
            const remaining=scroll.targetY-scroll.view.contentY
            const distance=Math.min(Math.abs(remaining),scroll.maximumSpeed*elapsed/1000,
                                   Math.abs(remaining)*(1-Math.exp(-elapsed/35)))
            scroll.applying=true
            scroll.view.contentY=Math.abs(remaining)<.5?scroll.targetY:scroll.view.contentY+Math.sign(remaining)*distance
            scroll.applying=false
            if(Math.abs(scroll.targetY-scroll.view.contentY)<.1)stop()
        }
    }
}
