// SPDX-License-Identifier: MPL-2.0
import QtQuick
import "../motion.js" as Motion

Item {
    id: overlay
    required property var model
    required property var view
    property bool trash: false
    property bool active: false
    property var scene: []
    property real enterDelay: 0
    property var owner: null
    property real targetScroll: 0
    objectName: "projectListMotion"
    clip: true
    visible: active
    function begin(before, after, animate, rowsOwner) {
        if (!animate || !model.settings.animations || !parent.visible || model.startupActive) return
        // Unclipped rows remain visible behind the glass outside the list's
        // viewport. Keep them in the same scene instead of dropping them when
        // the native delegates hand over to their animated copies.
        const plan=Motion.projectScene(before,after,width,height,view.contentY,
                                       clip?0:Math.max(0,y),clip?0:Math.max(0,parent.height-y-height))
        if (plan.duration<=0) return
        let offsets={}
        for (let i=0;i<view.count;++i) {
            const item=view.itemAtIndex(i)
            if (item) offsets[item.modelData.path]=item.pathOffset
        }
        for (const record of plan.rows) {
            let data={}
            for (const key in record.row) data[key]=record.row[key]
            data._scrollOffset=offsets[record.row.path] || 0
            record.row=data
        }
        scene=plan.rows
        enterDelay=plan.enterDelay
        targetScroll=plan.targetScroll
        owner=rowsOwner
        active=true
        if (owner) owner.setAnimating(true)
        clock.start(plan.duration)
    }
    function cancel() {
        if (!active) return
        clock.seek(clock.duration)
        active=false
        scene=[]
        const previous=owner; owner=null
        if (previous) previous.setAnimating(false)
    }
    // The model applies the final layout under the scene in the same event turn.
    function layoutApplied() { if (active) view.contentY=targetScroll }
    onWidthChanged: cancel()
    onHeightChanged: cancel()
    MotionClock { id: clock; objectName: "projectCardsClock"; onFinished: overlay.cancel() }
    Connections {
        target: overlay.model
        function onSettingsChanged() { if (!overlay.model.settings.animations) overlay.cancel() }
        function onPageChanged() { overlay.cancel() }
    }
    WheelHandler { onWheel: event => { overlay.cancel(); event.accepted=false } }
    Repeater {
        model: overlay.scene
        delegate: Item {
            id: row
            required property var modelData
            readonly property real phase: modelData.kind==="remove" ? Motion.inOutSine(clock.elapsed/240) : modelData.kind==="add" ? Motion.outQuint((clock.elapsed-overlay.enterDelay)/180) : Motion.outCubic((clock.elapsed-modelData.delay)/310)
            readonly property real inset: modelData.kind==="remove" ? phase : modelData.kind==="add" ? 1-phase : 0
            readonly property real dx: Math.max(2,Motion.roundEven(width*.02))*inset
            readonly property real dy: Math.max(1,Motion.roundEven(height*.02))*inset
            objectName: "projectMotionRow_" + modelData.row.path
            x: dx
            y: (modelData.kind==="keep" ? Motion.mix(modelData.sourceY,modelData.targetY,phase) : modelData.kind==="add" ? modelData.targetY : modelData.sourceY)+dy
            width: overlay.width; height: 74
            opacity: modelData.kind==="remove" ? 1-phase : modelData.kind==="add" ? phase : 1
            transform: Scale { xScale: (row.width-2*row.dx)/Math.max(1,row.width); yScale: (row.height-2*row.dy)/row.height }
            layer.enabled: true
            ProjectCard {
                width: parent.width
                model: overlay.model; modelData: row.modelData.row; index: row.modelData.index
                trash: !!row.modelData.row._trash_id; interactive: false; presentationCopy: true
            }
        }
    }
}
