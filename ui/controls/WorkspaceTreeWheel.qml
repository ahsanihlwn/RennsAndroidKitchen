// SPDX-License-Identifier: MPL-2.0
import QtQuick

Item {
    id: scroll
    required property var view
    property bool animationsEnabled: true
    property bool useFlickPhysics: false
    readonly property real rowStep: 28 * Math.max(2, Qt.styleHints.wheelScrollLines)
    readonly property real fastMultiplier: 2.25
    readonly property real maximumSpeed: 3200
    readonly property real maximumPending: 360
    property real targetY: 0
    property real lastOriginY: 0
    property int direction: 0
    property bool applying: false
    property double lastWheelAt: 0
    property real virtualVelocity: 0
    property int virtualDir: 0
    property int burstCount: 0
    readonly property bool running: useFlickPhysics ? !!(view && view.flicking) : motion.running

    function bounded(position) {
        const origin = view.originY || 0
        const minimum = origin - (view.topMargin || 0)
        const maximum = Math.max(minimum, origin + view.contentHeight - view.height + (view.bottomMargin || 0))
        return Math.max(minimum, Math.min(maximum, position))
    }
    function syncOrigin() {
        const cur = view ? (view.originY || 0) : 0
        const delta = cur - lastOriginY
        if (Math.abs(delta) > 0.01) {
            lastOriginY = cur
            if (motion.running) targetY += delta
            return true
        }
        return false
    }
    function reset() {
        motion.stop();direction=0
        virtualVelocity=0;virtualDir=0;burstCount=0;lastWheelAt=0
        if(view && view.cancelFlick) view.cancelFlick()
        lastOriginY = view ? (view.originY || 0) : 0
        targetY=view ? view.contentY : 0
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
        if(useFlickPhysics) {
            if(!animationsEnabled) {
                view.cancelFlick()
                view.contentY=bounded(view.contentY+notches*rowStep*boost)
                event.accepted=true;return
            }
            const now = Date.now()
            const dtMs = Math.max(0, now - lastWheelAt)
            const decel = Math.max(1000, view.flickDeceleration || 3800)
            const maxVel = Math.max(1200, view.maximumFlickVelocity || 9000)
            const dir = Math.sign(notches)
            const sameBurst = dir === virtualDir && dtMs <= 160
            burstCount = sameBurst ? Math.min(8, burstCount + 1) : 0
            const burstMult = sameBurst ? Math.min(2.2, 1.0 + burstCount * 0.20) : 1.0
            const step = Math.abs(notches) * rowStep * boost * burstMult

            // Qt Flickable verticalVelocity matches d/dt(contentY): > 0 when scrolling down, < 0 when scrolling up.
            const curVy = view.flicking ? view.verticalVelocity : 0
            const liveSameDir = (dir > 0 && curVy > 0) || (dir < 0 && curVy < 0)
            let estVy = 0
            if (dir === virtualDir && dtMs < 400) {
                estVy = Math.max(0, virtualVelocity - decel * (dtMs / 1000))
            }
            if (!liveSameDir && curVy !== 0) view.cancelFlick()
            const atBound = (dir > 0 && view.atYEnd) || (dir < 0 && view.atYBeginning)
            const baseVy = atBound ? 0 : Math.max(estVy, liveSameDir ? Math.abs(curVy) : 0)
            const nextVy = Math.min(maxVel, Math.sqrt(baseVy * baseVy + 2 * decel * step))
            virtualDir = dir
            virtualVelocity = nextVy
            lastWheelAt = now
            view.flick(0, -dir * nextVy)
            event.accepted=true;return
        }
        view.cancelFlick()
        if(!animationsEnabled) {
            reset()
            view.contentY=bounded(view.contentY+notches*rowStep*boost)
            targetY=view.contentY
            event.accepted=true;return
        }
        const nextDirection=Math.sign(notches)
        if(nextDirection!==direction || !motion.running) {
            targetY=view.contentY
            lastOriginY = view.originY || 0
        } else {
            syncOrigin()
        }
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
        function onOriginYChanged() { scroll.syncOrigin() }
        function onContentYChanged() {
            if (!scroll.applying && scroll.running) {
                if (!scroll.syncOrigin() && scroll.view && scroll.view.dragging) scroll.reset()
            }
        }
    }
    FrameAnimation {
        id: motion
        onTriggered: {
            const elapsed = Math.max(1, Math.min(32, frameTime * 1000))
            scroll.syncOrigin()
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
