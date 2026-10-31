// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
C.Slider {
    id: control
    AgentControlBinding { target: control; kind: "slider"; activate: value=>{if(typeof value!=="number" || !isFinite(value) || value<control.from || value>control.to)throw new Error("Value is outside this slider's range");control.value=value;control.moved()} }
    required property var colors
    property var preferences: typeof rakPreferences!=="undefined" ? rakPreferences : null
    property bool animationsEnabled: preferences ? preferences.settings.animations : true
    property real knobDiameter: 12
    property color trackColor: colors.b12
    property color trackFillColor: colors.a55
    implicitHeight: 26; leftPadding: 6; rightPadding: 6; topPadding: 0; bottomPadding: 0
    background: Item {
        id: track
        width: control.width; height: control.height
        SmoothRectangle {
            x: control.leftPadding; y: Math.round((control.height-4)/2)
            width: control.availableWidth; height: 4; radius: 2; color: control.trackColor
            SmoothRectangle { x: control.mirrored?parent.width-width:0; height: 4; width: parent.width*control.position; radius: 2; color: control.trackFillColor }
        }
    }
    handle: LiquidKnob {
        objectName: "sliderKnob_"+control.objectName
        x: control.leftPadding+control.visualPosition*control.availableWidth-width/2
        y: Math.round((control.height-height)/2)
        implicitWidth: control.knobDiameter; implicitHeight: control.knobDiameter
        trackSource: track; preferences: control.preferences
        knobColor: control.colors.accent_hex; surfaceColor: control.colors.BG2
        enabled: control.enabled; pressed: control.pressed; position: control.visualPosition
        animationsEnabled: control.animationsEnabled
    }
}
