// SPDX-License-Identifier: MPL-2.0
import QtQuick

Item {
    id: feedback
    property bool enabledMotion: true
    property real value: 1
    property double started: 0
    function animate(target) {
        animation.stop()
        if (!enabledMotion) { value=1; return }
        animation.to=target
        animation.duration=target<1?110:170
        animation.start()
    }
    function press() { releaseDelay.stop(); started=Date.now(); animate(.96) }
    function release() {
        const remaining=Math.max(0,120-(Date.now()-started))
        if (remaining && enabledMotion) { releaseDelay.interval=remaining; releaseDelay.start() }
        else animate(1)
    }
    function reset() { releaseDelay.stop(); animation.stop(); value=1 }
    onEnabledMotionChanged: if (!enabledMotion) reset()
    NumberAnimation { id: animation; target: feedback; property: "value"; easing.type: Easing.OutCubic }
    Timer { id: releaseDelay; onTriggered: feedback.animate(1) }
}
