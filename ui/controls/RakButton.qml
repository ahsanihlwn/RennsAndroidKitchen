// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Templates as T
import "../motion.js" as Motion

T.Button {
    id: control
    AgentControlBinding { target: control; kind: "button"; label: control.toolTip || control.text; activate: ()=>control.clicked() }
    ContentHitRegion { id: hitRegion; item: control }
    containmentMask: hitRegion.viewport?hitRegion:null
    property color fillColor: "transparent"
    property color hoverFillColor: fillColor
    property color downFillColor: hoverFillColor
    property color edgeColor: "transparent"
    property color hoverEdgeColor: edgeColor
    property color labelColor: "#c9d1d9"
    property color hoverLabelColor: labelColor
    property color disabledFillColor: fillColor
    property color disabledEdgeColor: edgeColor
    property color disabledLabelColor: labelColor
    property bool loading: false
    property color loadingAccentColor: "transparent"
    property real cornerRadius: 7
    property real edgeWidth: 1
    property string iconKey: ""
    property url iconSource: ""
    property int iconWidth: 16
    property int iconHeight: 16
    property bool animatePress: typeof rakPreferences!=="undefined" ? rakPreferences.settings.animations : true
    property bool leftAligned: false
    property string toolTip: ""
    property bool pressFeedback: false
    property real pressScale: 1
    readonly property bool transformed: Math.abs(pressScale-1)>.0001
    property bool morphHidden: false
    // A GPU source image is rendered after the popup takes pointer focus.
    // Preserve the pre-popup hover styling until that hidden source is handed
    // back, matching the reference's synchronous full-button grab.
    property int morphHoverState: -1
    readonly property bool visualHovered: morphHoverState<0 ? hovered : morphHoverState===1
    property double pressStarted: 0
    property Component morphCaption: Component {
        Item {
            ButtonCaption {
                x: control.leftPadding; y: control.topPadding
                width: parent.width-control.leftPadding-control.rightPadding
                height: parent.height-control.topPadding-control.bottomPadding
                button: control; moving: true
            }
        }
    }
    implicitWidth: Math.max(80, contentItem.implicitWidth + leftPadding + rightPadding)
    implicitHeight: 30
    leftPadding: 14
    rightPadding: 14
    topPadding: 0
    bottomPadding: 0
    spacing: 6
    hoverEnabled: true
    HoverHandler { cursorShape: (control.enabled && !control.loading) ? Qt.PointingHandCursor : Qt.ArrowCursor }
    HoverTip { objectName: "buttonToolTip_"+control.objectName; label: control.toolTip; hovering: control.enabled && !control.loading && control.hovered }
    opacity: morphHidden ? 0 : 1
    font.family: "Poppins"
    font.pixelSize: 12
    font.hintingPreference: Font.PreferVerticalHinting
    scale: pressScale
    function resetPressFeedback() {
        releaseDelay.stop()
        pressFeedback = false
        // Clearing feedback triggers its change handler; stop that newly
        // scheduled release too before restoring the native button surface.
        pressAnimation.stop()
        pressScale = 1
    }
    onPressFeedbackChanged: {
        pressAnimation.stop()
        const target = animatePress && pressFeedback ? .96 : 1
        const base = pressFeedback ? 1 : .96
        pressAnimation.to = target
        pressAnimation.duration = animatePress ? Motion.remainingDuration(pressFeedback ? 110 : 170, pressScale, base, target) : 0
        pressAnimation.start()
    }
    onPressed: {
        releaseDelay.stop()
        pressStarted = Date.now()
        pressFeedback = true
    }
    onReleased: releaseDelay.start()
    onCanceled: releaseDelay.start()
    onAnimatePressChanged: if (!animatePress) resetPressFeedback()
    onVisibleChanged: if (!visible) resetPressFeedback()
    onEnabledChanged: if (!enabled) resetPressFeedback()
    onLoadingChanged: if (loading) resetPressFeedback()
    Timer {
        id: releaseDelay
        interval: Math.max(0, 120 - (Date.now() - control.pressStarted))
        onTriggered: control.pressFeedback = false
    }
    NumberAnimation { id: pressAnimation; target: control; property: "pressScale"; easing.type: Easing.OutCubic }
    background: SmoothRectangle {
        color: control.loading ? control.fillColor : (!control.enabled ? control.disabledFillColor : control.down ? control.downFillColor : control.visualHovered ? control.hoverFillColor : control.fillColor)
        radius: Math.min(control.cornerRadius, height / 2)
        border.width: control.edgeWidth
        border.pixelAligned: control.edgeWidth === Math.round(control.edgeWidth)
        border.color: control.loading ? control.edgeColor : (!control.enabled ? control.disabledEdgeColor : control.visualHovered ? control.hoverEdgeColor : control.edgeColor)
    }
    contentItem: ButtonCaption { button: control; moving: control.transformed; opacity: control.loading ? 0.75 : 1.0 }
    Loader {
        active: control.loading
        visible: control.loading
        anchors.fill: parent
        z: 2
        sourceComponent: Item {
            id: loadEffect
            anchors.fill: parent
            clip: true

            readonly property color accentColor: {
                if (control.loadingAccentColor.a > 0) return control.loadingAccentColor
                if (typeof control.colors !== "undefined" && control.colors && control.colors.accent_hex) return control.colors.accent_hex
                return "#ff7800"
            }
            readonly property color cTrans: Qt.rgba(accentColor.r, accentColor.g, accentColor.b, 0)
            readonly property color cGlowMid: Qt.rgba(accentColor.r, accentColor.g, accentColor.b, 0.10)
            readonly property color cGlowCenter: Qt.rgba(accentColor.r, accentColor.g, accentColor.b, 0.25)
            readonly property color cTraceMid: Qt.rgba(accentColor.r, accentColor.g, accentColor.b, 0.55)
            readonly property color cTraceCenter: Qt.rgba(accentColor.r, accentColor.g, accentColor.b, 0.95)

            // Subtle ambient accent tint on button body
            SmoothRectangle {
                anchors.fill: parent
                radius: Math.min(control.cornerRadius, height / 2)
                color: loadEffect.accentColor
                opacity: 0.08
            }

            readonly property real runnerW: Math.max(30, Math.min(65, control.width * 0.5))
            property real phase: 0
            readonly property real runnerX: -runnerW + phase * (control.width + runnerW * 2)

            NumberAnimation on phase {
                from: 0; to: 1
                duration: 1100
                loops: Animation.Infinite
                running: control.loading && control.visible
            }

            // Top border light runner + inward impact
            Item {
                anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
                anchors.leftMargin: Math.min(6, control.cornerRadius)
                anchors.rightMargin: Math.min(6, control.cornerRadius)
                height: 4
                clip: true

                // Inward impact: 3.0px soft glow
                Rectangle {
                    x: loadEffect.runnerX; y: 1.0; width: loadEffect.runnerW; height: 3.0
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.00; color: loadEffect.cTrans }
                        GradientStop { position: 0.25; color: loadEffect.cGlowMid }
                        GradientStop { position: 0.50; color: loadEffect.cGlowCenter }
                        GradientStop { position: 0.75; color: loadEffect.cGlowMid }
                        GradientStop { position: 1.00; color: loadEffect.cTrans }
                    }
                }
                // Crisp border trace: aksen tema
                Rectangle {
                    x: loadEffect.runnerX; y: 0; width: loadEffect.runnerW; height: 1.2
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.00; color: loadEffect.cTrans }
                        GradientStop { position: 0.25; color: loadEffect.cTraceMid }
                        GradientStop { position: 0.50; color: loadEffect.cTraceCenter }
                        GradientStop { position: 0.75; color: loadEffect.cTraceMid }
                        GradientStop { position: 1.00; color: loadEffect.cTrans }
                    }
                }
            }

            // Bottom border light runner + inward impact
            Item {
                anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right
                anchors.leftMargin: Math.min(6, control.cornerRadius)
                anchors.rightMargin: Math.min(6, control.cornerRadius)
                height: 4
                clip: true

                // Inward impact: 3.0px soft glow
                Rectangle {
                    x: loadEffect.runnerX; y: 0; width: loadEffect.runnerW; height: 3.0
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.00; color: loadEffect.cTrans }
                        GradientStop { position: 0.25; color: loadEffect.cGlowMid }
                        GradientStop { position: 0.50; color: loadEffect.cGlowCenter }
                        GradientStop { position: 0.75; color: loadEffect.cGlowMid }
                        GradientStop { position: 1.00; color: loadEffect.cTrans }
                    }
                }
                // Crisp border trace: aksen tema
                Rectangle {
                    x: loadEffect.runnerX; y: 2.8; width: loadEffect.runnerW; height: 1.2
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0.00; color: loadEffect.cTrans }
                        GradientStop { position: 0.25; color: loadEffect.cTraceMid }
                        GradientStop { position: 0.50; color: loadEffect.cTraceCenter }
                        GradientStop { position: 0.75; color: loadEffect.cTraceMid }
                        GradientStop { position: 1.00; color: loadEffect.cTrans }
                    }
                }
            }
        }
    }
}
