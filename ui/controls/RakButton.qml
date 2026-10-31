// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Templates as T

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
    property real cornerRadius: 7
    property real edgeWidth: 1
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
    HoverHandler { cursorShape: control.enabled?Qt.PointingHandCursor:Qt.ArrowCursor }
    HoverTip { objectName: "buttonToolTip_"+control.objectName; label: control.toolTip; hovering: control.enabled && control.hovered }
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
        pressAnimation.to = animatePress && pressFeedback ? .96 : 1
        pressAnimation.duration = animatePress ? (pressFeedback ? 110 : 170) : 0
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
    Timer {
        id: releaseDelay
        interval: Math.max(0, 120 - (Date.now() - control.pressStarted))
        onTriggered: control.pressFeedback = false
    }
    NumberAnimation { id: pressAnimation; target: control; property: "pressScale"; easing.type: Easing.OutCubic }
    background: SmoothRectangle {
        color: !control.enabled ? control.disabledFillColor : control.down ? control.downFillColor : control.visualHovered ? control.hoverFillColor : control.fillColor
        radius: Math.min(control.cornerRadius, height / 2)
        border.width: control.edgeWidth
        border.pixelAligned: control.edgeWidth === Math.round(control.edgeWidth)
        border.color: !control.enabled ? control.disabledEdgeColor : control.visualHovered ? control.hoverEdgeColor : control.edgeColor
    }
    contentItem: ButtonCaption { button: control; moving: control.transformed }
}
