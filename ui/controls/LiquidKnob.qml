// SPDX-License-Identifier: Apache-2.0
// Copyright 2025 Kyant
// Press/drag motion adapted from AndroidLiquidGlass-kmp's LiquidSlider,
// LiquidToggle and DampedDragAnimation. Ported to Qt Quick while retaining
// this application's thumb geometry and colors. See licenses/LICENSE-APACHE-2.0.
import QtQuick

Item {
    id: knob
    required property Item trackSource
    property var preferences: typeof rakPreferences!=="undefined" ? rakPreferences : null
    property color knobColor: "white"
    property color surfaceColor: preferences ? preferences.colors.BG2 : "#202020"
    property bool pressed: false
    property bool animationsEnabled: preferences ? preferences.settings.animations : true
    property bool glassEnabled: true
    property real pressedScaleX: 1.5
    property real pressedScaleY: 1.5
    property real position: 0
    readonly property real pressProgress: pressMotion.value
    readonly property real expansionX: widthMotion.value
    readonly property real expansionY: heightMotion.value
    property real dragVelocity: 0
    property real previousPosition: position
    property double previousTime: 0
    readonly property real progress: Math.max(0,Math.min(1,pressProgress))
    readonly property string effectLevel: preferences ? preferences.settings.effects : "low"
    readonly property real stretch: glassEnabled ? Math.max(-.2,Math.min(.2,dragVelocity)) : Math.max(-.08,Math.min(.08,dragVelocity))
    readonly property real visualWidth: width*expansionX/(1-stretch)
    readonly property real visualHeight: height*expansionY*(1-stretch/3)
    readonly property bool glassActive: glassLoader.active
    readonly property bool refracting: !!glassLoader.item && glassLoader.item.refracting
    readonly property var glass: glassLoader.item
    implicitWidth: 12; implicitHeight: 12
    SpringValue { id: pressMotion; target: knob.pressed && knob.enabled?1:0; stiffness: 1000; dampingRatio: 1; animated: knob.animationsEnabled }
    SpringValue { id: widthMotion; target: knob.pressed && knob.enabled?knob.pressedScaleX:1; stiffness: knob.glassEnabled ? 250 : 650; dampingRatio: knob.glassEnabled ? .6 : .86; animated: knob.animationsEnabled }
    SpringValue { id: heightMotion; target: knob.pressed && knob.enabled?knob.pressedScaleY:1; stiffness: knob.glassEnabled ? 250 : 650; dampingRatio: knob.glassEnabled ? .7 : .86; animated: knob.animationsEnabled }
    Behavior on dragVelocity { enabled: knob.animationsEnabled; NumberAnimation { duration: 70; easing.type: Easing.OutCubic } }
    onPositionChanged: {
        const now=Date.now(),elapsed=now-previousTime
        if(pressed && animationsEnabled && elapsed>0 && elapsed<180) {
            dragVelocity=Math.max(-.2,Math.min(.2,(position-previousPosition)*1000/elapsed*.075))
            velocityReset.restart()
        }
        previousPosition=position;previousTime=now
    }
    onPressedChanged: {
        previousPosition=position;previousTime=Date.now()
        if(!pressed){dragVelocity=0;velocityReset.stop()}
    }
    onEnabledChanged: if(!enabled){dragVelocity=0;velocityReset.stop()}
    Timer { id: velocityReset; interval: 90; onTriggered: knob.dragVelocity=0 }
    Item {
        id: visual
        x: (knob.width-width)/2; y: (knob.height-height)/2
        width: knob.visualWidth; height: knob.visualHeight
        SmoothRectangle {
            anchors.fill: parent; radius: height/2
            visible: !glassLoader.active; color: knob.knobColor
        }
        Loader {
            id: glassLoader
            anchors.fill: parent
            active: knob.glassEnabled && knob.enabled && knob.visible && knob.effectLevel!=="low" && knob.progress>.001
            sourceComponent: LiveGlass {
                objectName: "controlKnobGlass"
                preferences: knob.preferences
                // Capture only the track, which is a sibling of the thumb.
                // Sampling the containing page would capture the thumb itself.
                backdropSource: knob.trackSource
                sampleInSourceCoordinates: true
                origin: {
                    knob.x;knob.y;visual.x;visual.y
                    return visual.mapToItem(knob.trackSource,0,0)
                }
                flatColor: knob.surfaceColor
                tintColor: Qt.rgba(knob.knobColor.r,knob.knobColor.g,knob.knobColor.b,1-.78*knob.progress)
                cornerRadius: height/2
                blurRadius: knob.effectLevel==="medium" ? 18 : (knob.preferences ? knob.preferences.effectiveBlurRadius : 8)*(1-.85*knob.progress)
                refractionEnabled: knob.effectLevel==="high"
                refractionHeight: Math.min(10,height*.4)*knob.progress
                refractionAmount: Math.min(14,height*.45)*knob.progress
                edgeColor: Qt.rgba(1,1,1,.28*knob.progress)
            }
        }
    }
}
