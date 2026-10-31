// SPDX-License-Identifier: MPL-2.0
import QtQuick

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
    property real progress: 1
    readonly property bool running: transition.running
    readonly property bool pending: updateTimer.running
    readonly property int duration: 280

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
        lastSignature=signature;settleRequested=false;initialized=true
        if(changed || (!animate && transition.running)) {
            transition.stop()
            for(const pane of panes)pane.prepare(animate)
            if(animate) {
                progress=0
                // Retarget from the displayed positions, including reversals.
                for(const pane of panes)pane.step(0)
                transition.start()
            } else {
                progress=1
                for(const pane of panes)pane.step(1)
            }
        }
        updating=false
    }
    onProgressChanged: { if(panes)for(const pane of panes)pane.step(progress) }
    onSignatureChanged: schedule()
    onAnimationsEnabledChanged: schedule(!animationsEnabled)
    Component.onCompleted: schedule(true)
    Timer { id: updateTimer; interval: 16; onTriggered: motion.sync() }
    NumberAnimation {
        id: transition; target: motion; property: "progress"; from: 0; to: 1
        duration: motion.duration; easing.type: Easing.OutQuint
    }
}
