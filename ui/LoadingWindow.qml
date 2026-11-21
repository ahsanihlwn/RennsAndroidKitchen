// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "controls"

Window {
    id: window
    required property var uiModel
    property string projectName: ""
    property string statusText: uiModel.strings.loading_opening_project
    property bool allowClose: false
    signal closeRequested()
    readonly property var c: uiModel.colors
    width: 320; height: 196
    minimumWidth: 320; maximumWidth: 320; minimumHeight: 196; maximumHeight: 196
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    color: "transparent"
    title: "Renns Android Kitchen"
    onClosing: event => { if (!allowClose) { event.accepted=false; closeRequested() } }
    readonly property bool animationsEnabled: !!(uiModel && uiModel.settings && uiModel.settings.animations)
    onVisibleChanged: {
        if (visible && animationsEnabled) openScale.restart()
        else { openScale.stop(); cardStage.scale = 1 }
    }
    Item {
        id: cardStage
        anchors.fill: parent
        transformOrigin: Item.Center
        NumberAnimation { id: openScale; target: cardStage; property: "scale"; from: 0.94; to: 1.0; duration: 170; easing.type: Easing.OutCubic }
        Repeater {
            model: 5
            SmoothRectangle { required property int index; x: index+1; y: x; width: window.width-2*x; height: window.height-2*y; radius: 16; color: Qt.rgba(0,0,0,(12-index*2)/255) }
        }
        SmoothRectangle {
            x: 10; y: 10; width: 300; height: 176; radius: 14
            color: window.c.BG2; border.color: window.c.b13
            SmoothRectangle {
                width: parent.width; height: 44
                color: window.c.BG_DARK; topLeftRadius: 14; topRightRadius: 14
                SmoothRectangle { y: parent.height-1; width: parent.width; height: 1; color: window.c.b08 }
                Image { x: 16; y: 13; width: 18; height: 18; sourceSize.width: width; sourceSize.height: height; source: window.uiModel.icon("ic_launcher","") }
                RakLabel { x: 42; y: 0; height: 44; text: "Renns Android Kitchen"; color: window.c.FG; font.pixelSize: 12; font.weight: Font.DemiBold }
            }
        }
        Spinner { objectName: "loadingSpinner"; x: 141; y: window.projectName.length?75:86; accent: window.c.accent_hex; spinning: window.visible }
        RakLabel { visible: window.projectName.length>0; x: 34; y: 127; width: 252; height: 17; text: window.projectName; horizontalAlignment: Text.AlignHCenter; color: window.c.FG_BRIGHT; font.pixelSize: 12; font.weight: Font.DemiBold; elide: Text.ElideRight }
        RakLabel { x: 34; y: window.projectName.length?148:138; width: 252; height: 16; text: window.statusText; horizontalAlignment: Text.AlignHCenter; color: window.c.FG_MUTED; font.pixelSize: 11; elide: Text.ElideRight }
    }
}
