// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
C.Slider {
    id: control
    AgentControlBinding { target: control; kind: "slider"; activate: value=>{if(typeof value!=="number" || !isFinite(value) || value<control.from || value>control.to)throw new Error("Value is outside this slider's range");control.value=value;control.moved()} }
    required property var colors
    property var preferences: typeof rakPreferences!=="undefined" ? rakPreferences : null
    property bool animationsEnabled: preferences ? preferences.settings.animations : true
    property bool capsuleTrack: false
    property int stepCount: 0
    property real knobDiameter: capsuleTrack ? 22 : 12
    property color trackColor: capsuleTrack ? (colors.b08 || colors.BG3 || "#191a24") : colors.b12
    property color trackFillColor: capsuleTrack ? (colors.accent_hex || colors.a55) : colors.a55
    property color dotColor: colors.b35 || colors.FG_MUTED || "#70748a"
    implicitHeight: capsuleTrack ? 28 : 26
    leftPadding: capsuleTrack ? 14 : 6
    rightPadding: capsuleTrack ? 14 : 6
    topPadding: 0; bottomPadding: 0
    snapMode: (capsuleTrack && stepCount > 1) ? C.Slider.SnapAlways : C.Slider.NoSnap
    stepSize: (capsuleTrack && stepCount > 1) ? 1 : 0

    readonly property real knobPosition: capsuleTrack ? positionMotion.value : control.visualPosition
    property real lastSwipeTime: 0
    property real lastSwipePos: 0
    property real swipeVelocity: 0

    SpringValue {
        id: positionMotion
        target: control.visualPosition
        stiffness: 450
        dampingRatio: 0.88
        animated: control.animationsEnabled
    }

    onVisualPositionChanged: {
        if (!control.capsuleTrack) return
        const now = Date.now()
        const dt = now - lastSwipeTime
        if (dt > 0 && dt < 200) {
            const currentSpeed = (control.visualPosition - lastSwipePos) / (dt / 1000)
            swipeVelocity = currentSpeed
            positionMotion.velocity = Math.max(-25, Math.min(25, currentSpeed * 0.75))
        }
        lastSwipePos = control.visualPosition
        lastSwipeTime = now
    }
    background: Item {
        id: track
        width: control.width; height: control.height
        SmoothRectangle {
            visible: !control.capsuleTrack
            x: control.leftPadding; y: Math.round((control.height-4)/2)
            width: control.availableWidth; height: 4; radius: 2; color: control.trackColor
            SmoothRectangle { x: control.mirrored?parent.width-width:0; height: 4; width: parent.width*control.position; radius: 2; color: control.trackFillColor }
        }
        SmoothRectangle {
            visible: control.capsuleTrack
            anchors.fill: parent
            radius: height / 2
            color: control.trackColor
            border.width: 1
            border.color: control.colors.b12 || "#282a3a"
            clip: true
            SmoothRectangle {
                visible: control.capsuleTrack
                x: 0; y: 0
                width: Math.max(height, Math.min(parent.width, control.leftPadding + control.knobPosition * control.availableWidth + height / 2))
                height: parent.height
                radius: height / 2
                color: control.trackFillColor
            }
            Repeater {
                model: (control.capsuleTrack && control.stepCount > 1) ? control.stepCount : 0
                delegate: Rectangle {
                    required property int index
                    readonly property real dotX: control.leftPadding + (control.availableWidth * index / Math.max(1, control.stepCount - 1))
                    readonly property bool filled: dotX <= (control.leftPadding + control.knobPosition * control.availableWidth - control.knobDiameter * 0.1)
                    x: Math.round(dotX - width / 2)
                    y: Math.round((parent.height - height) / 2)
                    width: 5; height: 5; radius: 2.5
                    color: filled ? (control.colors._is_light ? Qt.rgba(0, 0, 0, 0.35) : Qt.rgba(1, 1, 1, 0.5)) : control.dotColor
                }
            }
        }
    }
    handle: Item {
        id: handleItem
        x: control.leftPadding + control.knobPosition * control.availableWidth - width / 2
        y: Math.round((control.height - height) / 2)
        implicitWidth: Math.max(24, control.knobDiameter)
        implicitHeight: Math.max(24, control.knobDiameter)
        LiquidKnob {
            anchors.centerIn: parent
            width: control.knobDiameter
            height: control.knobDiameter
            objectName: "sliderKnob_" + control.objectName
            trackSource: track
            preferences: control.preferences
            glassEnabled: control.capsuleTrack
            knobColor: control.capsuleTrack ? (control.colors.BG_DARK || control.colors.BG2 || "#12131c") : control.colors.accent_hex
            surfaceColor: control.colors.BG_DARK || control.colors.BG2 || "#12131c"
            enabled: control.enabled
            pressed: control.pressed
            position: control.knobPosition
            animationsEnabled: control.animationsEnabled
            pressedScaleX: control.capsuleTrack ? 1.85 : 1.25
            pressedScaleY: control.capsuleTrack ? 1.5 : 1.25
        }
    }
}
