// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Templates as T

T.Switch {
    id: control
    AgentControlBinding { target: control; kind: "switch"; activate: value=>{if(typeof value!=="boolean")throw new Error("A boolean value is required");control.checked=value;control.toggled()} }
    ContentHitRegion { id: hitRegion; item: control }
    containmentMask: hitRegion.viewport?hitRegion:null
    property var preferences: typeof rakPreferences!=="undefined" ? rakPreferences : null
    readonly property var palette: preferences ? preferences.colors : null
    property color accentColor: palette && palette.TOGGLE_ON ? palette.TOGGLE_ON : "#2ecc8e"
    property color offColor: palette && palette.TOGGLE_OFF ? palette.TOGGLE_OFF : "#454545"
    property bool animationsEnabled: preferences ? preferences.settings.animations : true
    readonly property real knobPosition: Math.max(0,Math.min(1,positionMotion.value))
    readonly property real colorPosition: mirrored?1-knobPosition:knobPosition
    // Follow the pointer with the reference's critically damped position
    // spring. Stay expanded until the thumb reaches its final detent.
    readonly property bool thumbActive: down || Math.abs(knobPosition-visualPosition)>.025
    SpringValue { id: positionMotion; target: control.pressed?control.visualPosition:control.mirrored?1-Number(control.checked):Number(control.checked); stiffness: 1000; dampingRatio: 1; animated: control.animationsEnabled }
    implicitWidth: 42
    implicitHeight: 24
    leftPadding: 0
    rightPadding: 0
    topPadding: 0
    bottomPadding: 0
    indicator: Item {
        width: control.width
        height: control.height
        opacity: control.enabled ? 1 : .35
        SmoothRectangle {
            id: track
            anchors.fill: parent; radius: height/2
            color: Qt.rgba(control.offColor.r + control.colorPosition * (control.accentColor.r - control.offColor.r),
                           control.offColor.g + control.colorPosition * (control.accentColor.g - control.offColor.g),
                           control.offColor.b + control.colorPosition * (control.accentColor.b - control.offColor.b),
                           control.offColor.a + control.colorPosition * (control.accentColor.a - control.offColor.a))
        }
        LiquidKnob {
            objectName: "switchKnob_"+control.objectName
            width: parent.height - 4
            height: width
            y: 2
            x: 2 + control.knobPosition * (parent.width - width - 4)
            trackSource: track; preferences: control.preferences
            knobColor: control.palette && control.palette.TOGGLE_KNOB ? control.palette.TOGGLE_KNOB : "white"; enabled: control.enabled; pressed: control.thumbActive
            pressedScaleX: 1.8; pressedScaleY: 1.35
            position: control.knobPosition; animationsEnabled: control.animationsEnabled
        }
    }
    contentItem: Item {}
}
