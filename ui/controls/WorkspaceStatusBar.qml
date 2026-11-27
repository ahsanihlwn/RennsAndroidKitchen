// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Shapes

Item {
    id: bar
    required property var workspace
    required property var preferences
    readonly property var c: preferences.colors
    readonly property bool animationsEnabled: preferences.settings.animations
    readonly property bool hasStatus: workspace.statusState!=="idle" && workspace.status.length>0
    readonly property bool hasLog: workspace.historyLines.length>0 || workspace.pendingLabels.length>0 || workspace.statusState==="active"
    property alias historyButton: dots
    signal historyRequested(var target)
    property string currentText: ""
    property string previousText: ""
    property string currentState: "idle"
    property color previousColor: c.FG_DIM
    readonly property color textColor: currentState==="error"?c.RED_STATUS:currentState==="done"?c.GREEN_STATUS:currentState==="active"?c.FG_LABEL:c.FG_DIM
    property real presence: hasStatus?1:0
    property real logPresence: hasLog?1:0
    property real textPhase: 1
    property real statusWidth: Math.min(Math.max(0,width-50),21+Math.max(incoming.implicitWidth,textPhase<1?outgoing.implicitWidth:0))
    height: 20

    function sync() {
        // Keep the last caption alive while its group fades out on idle.
        if(!workspace.status.length)return
        if(workspace.status!==currentText) {
            previousText=currentText;previousColor=textColor
            currentText=workspace.status
            captionFade.stop()
            textPhase=animationsEnabled && previousText.length?0:1
            if(textPhase<1)captionFade.start()
        }
        currentState=workspace.statusState
    }
    Component.onCompleted: sync()
    Connections { target: bar.workspace; function onStatusChanged(){bar.sync()} }
    Behavior on presence { NumberAnimation { duration: bar.animationsEnabled?280:0; easing.type: Easing.OutCubic } }
    Behavior on logPresence { NumberAnimation { duration: bar.animationsEnabled?240:0; easing.type: Easing.OutCubic } }
    Behavior on statusWidth { NumberAnimation { duration: bar.animationsEnabled?220:0; easing.type: Easing.OutCubic } }
    NumberAnimation { id: captionFade; target: bar; property: "textPhase"; to: 1; duration: bar.animationsEnabled?180:0; easing.type: Easing.OutCubic }
    onAnimationsEnabledChanged: if(!animationsEnabled){captionFade.stop();textPhase=1}

    Item {
        id: status
        objectName: "workspaceStatusContent"
        x: 10; width: bar.statusWidth; height: 18
        opacity: bar.presence; scale: .86+.14*bar.presence
        transformOrigin: Item.Left
        visible: opacity>0
        Item {
            width: 14; height: 14; y: 2
            property real activeMix: bar.currentState==="active"?1:0
            Behavior on activeMix { NumberAnimation { duration: bar.animationsEnabled?180:0; easing.type: Easing.OutCubic } }
            Spinner {
                objectName: "workspaceStatusSpinner"
                anchors.fill: parent; accent: bar.c.accent_hex; thickness: 3; inset: 3
                opacity: parent.activeMix; scale: .8+.2*parent.activeMix
                visible: bar.workspace.statusProgress < 0
                spinning: status.visible && opacity>0 && visible
            }
            Shape {
                id: statusBarRing
                anchors.fill: parent
                visible: bar.currentState==="active" && bar.workspace.statusProgress >= 0
                opacity: parent.activeMix
                preferredRendererType: Shape.CurveRenderer
                antialiasing: true
                readonly property color accentCol: bar.c.accent_hex
                ShapePath {
                    strokeColor: Qt.rgba(statusBarRing.accentCol.r, statusBarRing.accentCol.g, statusBarRing.accentCol.b, 0.22)
                    strokeWidth: 2.4; capStyle: ShapePath.RoundCap; fillColor: "transparent"
                    PathAngleArc { centerX: 7; centerY: 7; radiusX: 4.8; radiusY: 4.8; startAngle: 0; sweepAngle: 360 }
                }
                ShapePath {
                    strokeColor: statusBarRing.accentCol; strokeWidth: 2.4; capStyle: ShapePath.RoundCap; fillColor: "transparent"
                    PathAngleArc {
                        centerX: 7; centerY: 7; radiusX: 4.8; radiusY: 4.8; startAngle: -90
                        sweepAngle: 360 * Math.max(0, Math.min(100, bar.workspace.statusProgress)) / 100
                        Behavior on sweepAngle { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
                    }
                }
            }
            Image {
                objectName: "workspaceStatusResult"
                anchors.fill: parent; source: "image://icons/check_white?color="+encodeURIComponent(bar.textColor); sourceSize: Qt.size(48,48); smooth: true
                visible: bar.currentState==="done"; opacity: 1-parent.activeMix; scale: .8+.2*opacity
            }
            SmoothRectangle {
                visible: bar.currentState==="error"; x: 3; y: 3; width: 8; height: 8; radius: 4
                color: bar.c.RED_STATUS; opacity: 1-parent.activeMix; scale: .8+.2*opacity
            }
        }
        Item {
            x: 21; width: Math.max(0,parent.width-x); height: 18; clip: true
            RakLabel {
                id: outgoing
                objectName: "workspaceStatusOutgoing"
                width: parent.width; height: 18; text: bar.previousText
                elide: Text.ElideRight; font.pixelSize: 11; color: bar.previousColor
                opacity: 1-bar.textPhase; scale: 1-.06*bar.textPhase; transformOrigin: Item.Left
                transformed: bar.presence<.9999 || bar.textPhase<.9999
                verticalAlignment: Text.AlignVCenter
            }
            RakLabel {
                id: incoming
                objectName: "workspaceStatusText"
                width: parent.width; height: 18; text: bar.currentText
                elide: Text.ElideRight; font.pixelSize: 11; color: bar.textColor
                opacity: bar.textPhase; scale: .96+.04*bar.textPhase; transformOrigin: Item.Left
                transformed: bar.presence<.9999 || bar.textPhase<.9999
                verticalAlignment: Text.AlignVCenter
            }
        }
    }
    Item {
        id: logSlot
        objectName: "workspaceHistorySlot"
        x: 10+(bar.statusWidth+7)*bar.presence
        width: 20; height: 18; opacity: bar.logPresence
        scale: .72+.28*bar.logPresence; transformOrigin: Item.Left
        visible: opacity>0
        RakButton {
            id: dots
            objectName: "workspaceHistoryButton"
            anchors.fill: parent
            text: "\u2022\u2022\u2022"; implicitWidth: 20; implicitHeight: 18
            leftPadding: 0; rightPadding: 0; labelColor: bar.c.FG_DIM; edgeWidth: 0
            enabled: bar.hasLog
            contentItem: RakLabel {
                text: dots.text; color: dots.labelColor; font: dots.font
                transformed: logSlot.scale!==1 || dots.transformed
                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            }
            property Component returnCaption: Component {
                RakLabel {
                    text: dots.text; color: dots.labelColor; font: dots.font
                    transformed: true
                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                }
            }
            onClicked: bar.historyRequested(dots)
        }
    }
}
