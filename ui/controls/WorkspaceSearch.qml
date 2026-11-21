// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

C.Popup {
    id: popup
    objectName: "workspaceSearch"
    required property var workspace
    required property var preferences
    required property var hostWindow
    readonly property var c: preferences.colors
    readonly property color tint: c.BG2
    readonly property bool animationsEnabled: preferences.settings.animations
    parent: hostWindow.contentItem
    popupType: C.Popup.Item
    width: Math.min(400,parent.width-24); height: Math.min(340,parent.height-24)
    padding: 10; modal: true; dim: false; focus: true
    transformOrigin: Item.Center
    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: popup.animationsEnabled?170:0; easing.type: Easing.OutCubic }
            NumberAnimation { property: "scale"; from: .94; to: 1; duration: popup.animationsEnabled?170:0; easing.type: Easing.OutCubic }
        }
    }
    exit: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 1; to: 0; duration: popup.animationsEnabled?120:0; easing.type: Easing.InCubic }
            NumberAnimation { property: "scale"; from: 1; to: .94; duration: popup.animationsEnabled?120:0; easing.type: Easing.InCubic }
        }
    }
    closePolicy: C.Popup.CloseOnEscape|C.Popup.CloseOnPressOutside
    function showAt(target) {
        const center=target.mapToItem(parent,target.width/2,target.height/2)
        x=Math.max(12,Math.min(parent.width-width-12,Math.floor(center.x-width/2)))
        y=Math.max(12,Math.min(parent.height-height-12,Math.floor(center.y-height/2)))
        open();input.forceActiveFocus();input.selectAll()
        workspace.search.query(input.text)
    }
    onAboutToHide: workspace.search.cancel()
    background: LiveGlass { preferences: popup.preferences; backdropSource: popup.hostWindow.backdropSurface; origin: Qt.point(popup.x,popup.y); tintColor: Qt.rgba(popup.tint.r,popup.tint.g,popup.tint.b,180/255); cornerRadius: 10; effectsEnabled: popup.preferences.settings.effects!=="low"; flatColor: popup.c.BG_MODAL; edgeColor: popup.preferences.panelBorder }
    contentItem: Item {
        SmoothRectangle {
            id: inputFrame; objectName: "workspaceSearchField"
            width: parent.width; height: 38; radius: 6
            color: popup.c.BG_INPUT; border.width: 1
            border.color: input.activeFocus?popup.c.a55:popup.c.b13
            Behavior on border.color { ColorAnimation { duration: popup.animationsEnabled?140:0 } }
            Image { x: 10; y: 12; width: 14; height: 14; source: "image://icons/project_search?color="+encodeURIComponent(popup.c.FG_MUTED); sourceSize: Qt.size(14,14) }
            RakField {
                id: input; objectName: "workspaceSearchInput"
                x: 32; width: parent.width-66; height: parent.height
                placeholderText: "File name or path..."; font.pixelSize: 13
                labelColor: popup.c.FG_BRIGHT; hintColor: popup.c.FG_MUTED
                selectionColor: popup.c.a30; selectedTextColor: popup.c.FG_BRIGHT
                leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
                background: null
                onTextChanged: if(popup.visible)popup.workspace.search.query(text)
                Keys.onReturnPressed: popup.pickFirst()
                Keys.onDownPressed: popup.focusResults(1)
                Keys.onUpPressed: popup.focusResults(-1)
            }
            WorkspaceIconButton {
                objectName: "workspaceSearchClear"; preferences: popup.preferences
                x: parent.width-width-5; y: 6; width: 26; height: 26
                iconKey: "close_rounded"; tint: hovered?popup.c.FG:popup.c.FG_MUTED
                iconWidth: 14; iconHeight: 14; visible: input.length>0
                toolTip: "Clear search"; onClicked: {input.clear();input.forceActiveFocus()}
            }
        }
        Spinner { x: 0; y: 49; width: 12; height: 12; thickness: 1.5; inset: 2; accent: popup.c.accent_hex; visible: popup.workspace.search.busy; spinning: visible && popup.animationsEnabled }
        RakLabel {
            objectName: "workspaceSearchStatus"; x: popup.workspace.search.busy?19:0; y: 45; width: parent.width-x; height: 20
            font.pixelSize: 10; color: popup.c.FG_MUTED
            text: popup.workspace.search.busy ? (popup.workspace.search.resultCount?popup.workspace.search.resultCount+" found · searching…":"Searching folders…")
                  : popup.workspace.search.scopeLabel+(popup.workspace.search.queryText.length?" · "+popup.workspace.search.resultCount+" results":"")
        }
        ListView {
            id: results; objectName: "workspaceSearchResults"
            y: 72; width: parent.width; height: Math.max(0,parent.height-y); clip: true; spacing: 2
            model: popup.workspace.search.rows
            boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.vertical: RakScrollBar { handleColor: popup.c.BG_SCROLL }
            delegate: Item {
                id: result
                required property var modelData
                required property int index
                objectName: "workspaceSearchResult:"+(modelData.path || "separator")
                width: results.width; height: modelData.separator?8:44
                HoverHandler { id: hover }
                SmoothRectangle {
                    x: 0; y: modelData.separator?4:0; width: parent.width; height: modelData.separator?1:parent.height; radius: modelData.separator?0:4
                    color: modelData.separator?popup.c.b13:results.currentIndex===result.index?popup.c.a12:hover.hovered?popup.c.b05:"transparent"
                    Behavior on color { ColorAnimation { duration: popup.animationsEnabled?100:0 } }
                }
                Image { visible: !modelData.separator; x: 8; y: 13; width: 18; height: 18; source: modelData.icon?"image://icons/"+modelData.icon:""; sourceSize: Qt.size(18,18) }
                RakLabel { visible: !modelData.separator; x: 34; y: 3; width: parent.width-44; height: 21; text: modelData.name || ""; textFormat: Text.PlainText; color: popup.c.FG_BRIGHT; font.pixelSize: 12; elide: Text.ElideRight; toolTip: modelData.path || "" }
                RakLabel { visible: !modelData.separator; x: 34; y: 23; width: parent.width-44; height: 16; text: modelData.parent || ""; textFormat: Text.PlainText; color: popup.c.FG_MUTED; font.pixelSize: 10; elide: Text.ElideMiddle }
                MouseArea { anchors.fill: parent; enabled: !result.modelData.separator; onClicked: {popup.workspace.search.choose(result.modelData.path);popup.close()} }
            }
            Keys.onReturnPressed: popup.pickCurrent()
            Keys.onDownPressed: popup.moveResult(1)
            Keys.onUpPressed: popup.moveResult(-1)
        }
        Column {
            anchors.centerIn: results; width: results.width-24; spacing: 7
            visible: popup.workspace.search.resultCount===0
            RakLabel { width: parent.width; horizontalAlignment: Text.AlignHCenter; color: popup.c.FG; font.pixelSize: 12; wrapMode: Text.Wrap
                text: popup.workspace.search.error || (popup.workspace.search.busy?"Searching…":popup.workspace.search.queryText.length?"No matching files":"Type a file name or path") }
        }
    }
    function pickFirst() {
        const rows=workspace.search.rows.snapshot()
        for(let n=0;n<rows.length;++n)if(rows[n].path){workspace.search.choose(rows[n].path);close();return}
    }
    function focusResults(direction) {
        results.currentIndex=direction>0?-1:results.count
        moveResult(direction)
        if(results.currentIndex>=0 && results.currentIndex<results.count)results.forceActiveFocus()
    }
    function moveResult(direction) {
        const rows=workspace.search.rows.snapshot()
        for(let index=results.currentIndex+direction;index>=0 && index<rows.length;index+=direction) {
            if(rows[index].path){results.currentIndex=index;results.positionViewAtIndex(index,ListView.Contain);return}
        }
    }
    function pickCurrent() {
        const rows=workspace.search.rows.snapshot(),index=results.currentIndex
        if(index>=0 && index<rows.length && rows[index].path){workspace.search.choose(rows[index].path);close()}
    }
}
