// SPDX-License-Identifier: MPL-2.0
import QtQuick

Item {
    id: clock
    property real elapsed: 0
    property int duration: 0
    readonly property bool running: animation.running
    signal finished()
    function start(milliseconds) {
        animation.stop()
        duration = Math.max(1, milliseconds)
        elapsed = 0
        animation.start()
    }
    function finish() { animation.stop(); elapsed = duration; finished() }
    function seek(milliseconds) { animation.stop(); elapsed = Math.max(0, Math.min(duration, milliseconds)) }
    NumberAnimation {
        id: animation
        target: clock; property: "elapsed"
        from: 0; to: clock.duration; duration: clock.duration
        easing.type: Easing.Linear
        onFinished: clock.finished()
    }
}
