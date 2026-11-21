// SPDX-License-Identifier: MPL-2.0
import QtQuick
import "../motion.js" as Motion

Item {
    id: motion
    required property var panes
    required property var splits
    required property string signature
    property bool animationsEnabled: true
    property bool initialized: false
    property bool updating: false
    property bool settleRequested: false
    property string lastSignature: ""
    property string previousSignature: ""
    property real progress: 1
    property real activeSpan: 1
    property bool activeTransition: false
    readonly property bool running: activeTransition || transition.running
    readonly property bool pending: updateTimer.running
    readonly property int duration: animationsEnabled ? 240 : 0
    property int activeDuration: duration

    function schedule(immediate=false) {
        if(updating)return
        if(immediate)settleRequested=true
        if(!updateTimer.running)updateTimer.start()
    }
    function sync() {
        updating=true
        // SplitView polishes its nested slots on the scene frame. Coalesce
        // changes for that frame and never reveal an unallocated 0px slot.
        if(panes.some(pane=>pane.requestedVisible && (pane.destination().width<=0 || pane.destination().height<=0))) {
            updating=false;updateTimer.restart();return
        }
        const logicalChange=signature!==lastSignature
        const changed=panes.some(pane=>pane.needsUpdate())
        const dragging=splits.some(split=>split.resizing)
        const animate=initialized && animationsEnabled && !settleRequested && !dragging
            && (logicalChange || transition.running)
        if(logicalChange && !changed && initialized) {
            updating=false;updateTimer.restart();return
        }
        const wasRunning=transition.running
        const prevProgress=progress
        const reversed=wasRunning && logicalChange && signature===previousSignature
        if(logicalChange)previousSignature=lastSignature
        lastSignature=signature;settleRequested=false;initialized=true
        if(changed || (!animate && transition.running)) {
            transition.stop()
            activeTransition=animate
            for(const pane of panes)pane.prepare(animate)
            if(animate) {
                let remainingRatio=0
                let hasFade=false
                for(const pane of panes) {
                    const delta=Math.abs(pane.endOpacity-pane.startOpacity)
                    if(delta>0.0001) {
                        hasFade=true
                        remainingRatio=Math.max(remainingRatio,delta)
                    }
                }
                const reverseRatio=reversed ? Motion.clamp(Motion.outCubic(prevProgress)*activeSpan+(1-activeSpan)) : 1
                if(!hasFade)remainingRatio=reverseRatio
                else if(reversed)remainingRatio=Math.max(remainingRatio,reverseRatio)
                activeSpan=remainingRatio
                activeDuration=Motion.remainingDuration(duration,1-remainingRatio,0,1)
                if(activeDuration<=0) {
                    progress=1
                    for(const pane of panes)pane.step(1)
                    activeTransition=false
                    activeSpan=1
                } else {
                    progress=0
                    // Retarget from the displayed positions, including reversals.
                    for(const pane of panes)pane.step(0)
                    transition.start()
                }
            } else {
                progress=1
                for(const pane of panes)pane.step(1)
                activeTransition=false
                activeSpan=1
            }
        }
        updating=false
    }
    onProgressChanged: { if(panes)for(const pane of panes)pane.step(progress) }
    onSignatureChanged: schedule()
    onAnimationsEnabledChanged: {
        if(!animationsEnabled && running) {
            transition.stop()
            progress=1
            for(const pane of panes)pane.step(1)
            activeTransition=false
            activeSpan=1
        }
        schedule(!animationsEnabled)
    }
    Component.onCompleted: schedule(true)
    Timer { id: updateTimer; interval: 0; onTriggered: motion.sync() }
    NumberAnimation {
        id: transition; target: motion; property: "progress"; from: 0; to: 1
        duration: motion.activeDuration; easing.type: Easing.OutCubic
        onStopped: { motion.activeTransition=false; if(motion.progress>=.9999)motion.activeSpan=1 }
    }
}
