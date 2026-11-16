// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

MorphPopup {
    id: popup
    objectName: "workspaceHistory"
    required property var workspace
    required property var preferences
    required property var hostWindow
    readonly property var c: preferences.colors
    readonly property bool hasHistory: workspace.taskHistory.length>0
    readonly property bool hasActivity: workspace.taskActivity.length>0 || workspace.pendingLabels.length>0
    parent: hostWindow.contentItem
    width: 330; height: 354
    padding: 0; modal: true; dim: false; focus: true
    springMotion: false; morphOnClose: true
    animationsEnabled: preferences.settings.animations
    backdropSource: hostWindow.backdropSurface
    effectsEnabled: preferences.settings.effects!=="low"
    panelColor: c.BG_MODAL; tintBaseColor: c.BG2; tintAlpha: .82; panelBorder: preferences.panelBorder
    panelRadius: 11
    closePolicy: C.Popup.CloseOnEscape|C.Popup.CloseOnPressOutside
    function showAt(target) {
        sourceItem=target
        const point=target.mapToItem(parent,target.width,0)
        if(point.x < 120) {
            x = Math.max(8, Math.min(parent.width-width-8, point.x + 8))
            y = Math.max(8, Math.min(parent.height-height-8, point.y - height + target.height))
            upward = true
        } else {
            x=Math.max(8,Math.min(parent.width-width-8,point.x-width))
            y=Math.max(8,Math.min(parent.height-height-8,point.y-height-6))
            upward=y+height<=point.y
        }
        open()
    }
    Item {
        objectName: "workspaceHistoryContent"
        width: popup.width; height: popup.height
        RakLabel { x: 16; y: 13; text: "ACTIVITY LOG"; font.pixelSize: 12; font.weight: Font.Bold; font.letterSpacing: .4; color: popup.c.FG_BRIGHT }
        SmoothRectangle { x: 14; y: 43; width: parent.width-28; height: 1; color: popup.c.b08 }
        Flickable {
            id: scroll
            x: 14; y: 53; width: parent.width-28; height: popup.height-44-y
            clip: true; contentWidth: width; contentHeight: body.height
            boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.vertical: RakScrollBar { width: 7; handleColor: popup.c.BG_SCROLL }
            Column {
                id: body; width: parent.width; spacing: 7
                RakLabel { visible: popup.hasActivity; text: "IN PROGRESS"; font.pixelSize: 9; font.weight: Font.Bold; font.letterSpacing: .5; color: popup.c.FG2 }
                Repeater {
                    model: popup.workspace.taskActivity
                    delegate: Item {
                        required property var modelData
                        width: body.width; height: 52
                        SmoothRectangle { anchors.fill: parent; radius: 5; color: popup.c.b05; border.width: 1; border.color: popup.c.b08 }
                        RakLabel { x: 9; y: 5; width: parent.width-18; text: modelData.label; font.pixelSize: 11; font.weight: Font.DemiBold; color: popup.c.FG; elide: Text.ElideRight }
                        RakLabel { x: 9; y: 23; width: parent.width-18; text: (modelData.active?"Running":"Queued")+"  ·  "+(modelData.progress>=0?modelData.progress+"%":modelData.done+" / "+modelData.total+" completed")+"  ·  "+(modelData.stage || Math.floor(modelData.elapsed)+"s"); font.pixelSize: 10; color: popup.c.FG2; elide: Text.ElideRight }
                        SmoothRectangle { x: 9; y: 42; width: parent.width-18; height: 3; radius: 1; color: popup.c.b10 }
                        SmoothRectangle { x: 9; y: 42; width: (parent.width-18)*(modelData.progress>=0?modelData.progress/100:modelData.total?modelData.done/modelData.total:0); height: 3; radius: 1; color: popup.c.accent_hex }
                    }
                }
                Repeater {
                    model: popup.workspace.pendingLabels
                    delegate: RakLabel { required property string modelData; width: body.width; text: "Queued  ·  "+modelData; font.pixelSize: 10; color: popup.c.FG2; wrapMode: Text.Wrap }
                }
                RakLabel { visible: popup.hasHistory; topPadding: 4; text: "RECENT RESULTS"; font.pixelSize: 9; font.weight: Font.Bold; font.letterSpacing: .5; color: popup.c.FG2 }
                Repeater {
                    model: popup.workspace.taskHistory
                    delegate: Item {
                        required property var modelData
                        readonly property var row: modelData
                        readonly property bool failed: !row.ok
                        width: body.width; height: row.detail?54:38
                        Image { x: 1; y: 3; width: 13; height: 13; source: parent.failed?"":"image://icons/check_white?color="+encodeURIComponent(historyCaption.color); sourceSize: Qt.size(48,48); smooth: true; opacity: .9 }
                        SmoothRectangle { visible: parent.failed; x: 3; y: 5; width: 9; height: 9; radius: 4.5; color: popup.c.RED_STATUS }
                        RakLabel { id: historyCaption; x: 20; y: 0; width: parent.width-92; text: row.name; font.pixelSize: 11; color: parent.failed?popup.c.RED_STATUS:popup.c.FG_LABEL; elide: Text.ElideRight }
                        RakLabel { x: 20; y: 17; width: parent.width-22; text: row.label+"  ·  "+Number(row.elapsed).toFixed(1)+"s"; font.pixelSize: 10; color: popup.c.FG2; elide: Text.ElideRight }
                        RakLabel { visible: !!row.detail; x: 20; y: 32; width: parent.width-22; height: 18; text: row.detail; font.pixelSize: 10; color: popup.c.RED_STATUS; elide: Text.ElideRight; toolTip: row.detail }
                        RakLabel { x: parent.width-68; y: 0; width: 66; horizontalAlignment: Text.AlignRight; text: row.time; font.pixelSize: 10; color: popup.c.FG2 }
                    }
                }
            }
        }
        RakButton {
            objectName: "workspaceHistoryClear"
            x: 14; y: parent.height-36; width: parent.width-28; height: 26; visible: popup.hasHistory
            text: "Clear history"; font.pixelSize: 11; font.weight: Font.DemiBold; cornerRadius: 6
            fillColor: popup.c.b06; hoverFillColor: popup.c.b10; downFillColor: popup.c.b16
            edgeWidth: 1; edgeColor: popup.c.b14; labelColor: popup.c.FG
            onClicked: {popup.workspace.dispatch("clearHistory",{});popup.close()}
        }
    }
    Connections {
        target: workspace
        function onStatusChanged() {if(!popup.hasActivity&&!popup.hasHistory)popup.close()}
    }
    // The status button can still be sliding as an operation finishes.
    Timer {
        interval: 16; repeat: true; running: popup.visible
        onTriggered: popup.anchorInParent=popup.anchorRect()
    }
}
