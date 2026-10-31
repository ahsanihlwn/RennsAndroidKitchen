// SPDX-License-Identifier: MPL-2.0
import QtQuick

// An analytic damped spring: stiffness and dampingRatio use the same physical
// units as Compose springs, independently of the display's frame rate.
FrameAnimation {
    id: motion
    property real target: 0
    property real value: target
    property real velocity: 0
    property real stiffness: 250
    property real dampingRatio: .6
    property real epsilon: .001
    property bool animated: true
    property bool ready: false
    running: ready && animated && (Math.abs(value-target)>epsilon || Math.abs(velocity)>epsilon*Math.sqrt(stiffness))
    function snap() {value=target;velocity=0}
    onTargetChanged: if(!ready || !animated)snap()
    onAnimatedChanged: if(!animated)snap()
    Component.onCompleted: {snap();ready=true}
    onTriggered: {
        const dt=Math.min(.25,frameTime),omega=Math.sqrt(stiffness),offset=value-target
        if(dt<=0)return
        let nextOffset,nextVelocity
        if(dampingRatio>=1) {
            const coefficient=velocity+omega*offset,decay=Math.exp(-omega*dt)
            nextOffset=(offset+coefficient*dt)*decay
            nextVelocity=(velocity-omega*coefficient*dt)*decay
        } else {
            const decayRate=dampingRatio*omega,frequency=omega*Math.sqrt(1-dampingRatio*dampingRatio)
            const cosine=Math.cos(frequency*dt),sine=Math.sin(frequency*dt),decay=Math.exp(-decayRate*dt)
            nextOffset=(offset*cosine+(velocity+decayRate*offset)/frequency*sine)*decay
            nextVelocity=(velocity*cosine-(decayRate*velocity+omega*omega*offset)/frequency*sine)*decay
        }
        const settled=Math.abs(nextOffset)<epsilon && Math.abs(nextVelocity)<epsilon*omega
        value=settled?target:target+nextOffset
        velocity=settled?0:nextVelocity
    }
}
