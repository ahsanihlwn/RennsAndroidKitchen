// SPDX-License-Identifier: MPL-2.0
import QtQuick

Item {
    id: scroll
    required property var view
    property bool animationsEnabled: true
    readonly property real rowStep: 28 * Math.max(2, Qt.styleHints.wheelScrollLines)
    readonly property real fastMultiplier: 2.25
    readonly property real maximumSpeed: 3200
    readonly property real maximumPending: 360
    property real targetY: 0
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
        if(event.modifiers&(Qt.ShiftModifier|Qt.AltModifier|Qt.MetaModifier)
            || Math.abs(event.angleDelta.x)>Math.abs(event.angleDelta.y)
            || Math.abs(event.pixelDelta.x)>Math.abs(event.pixelDelta.y)) {
            reset();event.accepted=false;return
        }
        const ctrl = !!(event.modifiers & Qt.ControlModifier)
        const boost = ctrl ? fastMultiplier : 1.0
        if(event.pixelDelta.y) {
            reset();view.cancelFlick()
            view.contentY=bounded(view.contentY-event.pixelDelta.y*boost)
            event.accepted=true;return
        }
        const notches=-event.angleDelta.y/120
        if(!notches){event.accepted=false;return}
        view.cancelFlick()
        if(!animationsEnabled) {
            reset()
            view.contentY=bounded(view.contentY+notches*rowStep*boost)
            targetY=view.contentY
            event.accepted=true;return
        }
        const nextDirection=Math.sign(notches)
        if(nextDirection!==direction || !motion.running)targetY=view.contentY
        direction=nextDirection
        const cap = ctrl ? maximumPending * 1.4 : maximumPending
        const pending=Math.max(-cap,Math.min(cap,targetY-view.contentY+notches*rowStep*boost))
        targetY=bounded(view.contentY+pending)
        if(!motion.running && Math.abs(targetY-view.contentY)>.1) {
            motion.start()
        }
        event.accepted=true
    }
    onAnimationsEnabledChanged: {
        if(!animationsEnabled && motion.running) {
            applying=true
            view.contentY=bounded(targetY)
            applying=false
            reset()
        }
    }
    Connections {
        target: scroll.view
        function onContentYChanged() {if(!scroll.applying && scroll.running)scroll.reset()}
    }
    FrameAnimation {
        id: motion
        onTriggered: {
            const elapsed = Math.max(1, Math.min(32, frameTime * 1000))
            scroll.targetY=scroll.bounded(scroll.targetY)
            const remaining=scroll.targetY-scroll.view.contentY
            const distance=Math.min(Math.abs(remaining),scroll.maximumSpeed*elapsed/1000,
                                   Math.abs(remaining)*(1-Math.exp(-elapsed/42)))
            scroll.applying=true
            scroll.view.contentY=Math.abs(remaining)<.35?scroll.targetY:scroll.view.contentY+Math.sign(remaining)*distance
            scroll.applying=false
            if(Math.abs(scroll.targetY-scroll.view.contentY)<.1)stop()
        }
    }
}
