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
    property int settleRetries: 0
    property bool restarting: false
    property double startedAtMs: 0

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
            if(settleRetries<4) {
                settleRetries+=1
                updating=false;updateTimer.restart();return
            }
        }
        settleRetries=0
        const wasRunning=transition.running
        const prevProgress=progress
        const prevDuration=activeDuration
        const elapsedMs=wasRunning ? Math.max(0, Date.now()-startedAtMs) : 0
        const reversed=wasRunning && logicalChange && signature===previousSignature
        if(logicalChange)previousSignature=lastSignature
        lastSignature=signature;settleRequested=false;initialized=true
        if(changed || (!animate && transition.running)) {
            restarting=true
            transition.stop()
            restarting=false
            activeTransition=animate
            for(const pane of panes)pane.prepare(animate)
            if(animate) {
                let geomRatio=0
                let opacityRatio=0
                const refW=Math.max(1, motion.width || (motion.parent ? motion.parent.width : 1000))
                const refH=Math.max(1, motion.height || (motion.parent ? motion.parent.height : 700))
                for(const pane of panes) {
                    const opDelta=Math.abs(pane.endOpacity-pane.startOpacity)
                    if(opDelta>0.0001)opacityRatio=Math.max(opacityRatio,opDelta)
                    const dist=Math.max(
                        Math.abs(pane.endRect.x-pane.startRect.x)/refW,
                        Math.abs(pane.endRect.y-pane.startRect.y)/refH,
                        Math.abs(pane.endRect.width-pane.startRect.width)/refW,
                        Math.abs(pane.endRect.height-pane.startRect.height)/refH
                    )
                    if(dist>0.0001)geomRatio=Math.max(geomRatio,Motion.clamp(dist))
                }
                const visualWork=Motion.clamp(Math.max(opacityRatio,geomRatio))
                let nextSpan=1
                let nextDuration=duration
                if(wasRunning) {
                    if(!logicalChange) {
                        // SplitView polish tick: keep current remaining duration seamlessly
                        const remMs=Math.max(16, Math.round(prevDuration-elapsedMs))
                        nextDuration=Math.min(duration, remMs)
                        nextSpan=Motion.clamp(nextDuration/Math.max(1,duration))
                    } else if(reversed) {
                        // Reversing back: travel for the distance/duration actually elapsed so far
                        const traveled=Motion.clamp(Motion.outCubic(prevProgress)*activeSpan)
                        const backRatio=Math.max(0.18, Math.min(0.95, Math.max(traveled, visualWork*0.5)))
                        nextSpan=traveled
                        nextDuration=Math.max(45, Math.min(duration-1, Math.round(duration*Math.pow(backRatio, 0.72))))
                    } else {
                        // Retargeting to a new layout mid-motion: blend remaining time with actual travel distance
                        const remRatio=Motion.clamp((prevDuration-elapsedMs)/Math.max(1,duration))
                        const needed=Math.max(remRatio, Math.pow(visualWork, 0.6))
                        nextSpan=Motion.clamp(Math.max(visualWork, needed))
                        nextDuration=Math.max(60, Math.min(duration, Math.round(duration*Motion.clamp(needed))))
                    }
                } else {
                    nextSpan=visualWork>0.0001 ? Motion.clamp(visualWork) : 1
                    nextDuration=duration
                }
                activeSpan=nextSpan
                activeDuration=nextDuration
                if(activeDuration<=0) {
                    progress=1
                    for(const pane of panes)pane.step(1)
                    activeTransition=false
                    activeSpan=1
                } else {
                    startedAtMs=Date.now()
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
    onSignatureChanged: { settleRetries=0; schedule() }
    onAnimationsEnabledChanged: {
        if(!animationsEnabled && running) {
            restarting=true
            transition.stop()
            restarting=false
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
        onStopped: {
            if(motion.restarting)return
            motion.activeTransition=false
            if(motion.progress>=.9999)motion.activeSpan=1
        }
    }
}
