// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

C.Popup {
    id: menu
    objectName: "workspaceContextMenu"
    required property var workspace
    required property var preferences
    required property var hostWindow
    property var details: ({})
    property var metrics: ({width:8,height:8})
    readonly property var c: preferences.colors
    readonly property color tintBase: c.BG2
    readonly property string kind: details.menuKind || "single"
    parent: hostWindow.contentItem
    popupType: C.Popup.Item
    width: Math.min(metrics.width,hostWindow.width-1); height: Math.min(metrics.height,hostWindow.height-1)
    margins: 0; padding: 4; focus: true; modal: false; dim: false
    closePolicy: C.Popup.CloseOnEscape|C.Popup.CloseOnPressOutside
    onAboutToShow: workspace.setContextMenuActive(true)
    onClosed: {subMenu.close();workspace.setContextMenuActive(false)}
    function closeAll(){subMenu.close();close()}
    function pick(row) {
        const data=Object.assign({},details)
        if(row.paths)data.paths=row.paths
        if(row.tool){data.tool=row.tool;data.label=row.label}
        closeAll()
        if (typeof row.callback === "function") {
            row.callback()
        } else {
            workspace.dispatch(row.key,data)
        }
    }
    function showSubmenu(button,rows) {
        if(subMenu.visible && subMenu.anchorButton===button)return
        subMenu.close()
        let spaced=[]
        for(let i=0;i<rows.length;++i){if(i)spaced.push({separator:true});spaced.push(rows[i])}
        subMenu.rows=spaced;subMenu.metrics=workspace.contextMenuMetrics(spaced,"submenu")
        subContent.contentY=0
        subMenu.anchorButton=button
        const point=button.mapToItem(hostWindow.contentItem,button.width+4,-4)
        subMenu.x=Math.max(0,Math.min(point.x,hostWindow.width-1-subMenu.width))
        subMenu.y=Math.max(0,Math.min(point.y,hostWindow.height-1-subMenu.height))
        subMenu.open()
    }
    function showMenu(data) {
        subMenu.close();details=data
        metrics=workspace.contextMenuMetrics(data.actions || [],kind)
        x=Math.max(0,Math.min(data.x,hostWindow.width-1-width))
        y=Math.max(0,Math.min(data.y,hostWindow.height-1-height))
        content.contentY=0
        open()
    }
    Connections {
        target: menu.hostWindow
        function onXChanged(){menu.closeAll()}
        function onYChanged(){menu.closeAll()}
        function onWidthChanged(){menu.closeAll()}
        function onHeightChanged(){menu.closeAll()}
        function onVisibleChanged(){if(!menu.hostWindow.visible)menu.closeAll()}
        function onActiveChanged(){if(!menu.hostWindow.active)menu.closeAll()}
    }
    background: LiveGlass {
        objectName: "contextMenuGlass"
        preferences: menu.preferences
        backdropSource: menu.hostWindow.backdropSurface; origin: Qt.point(menu.x,menu.y)
        cornerRadius: 10; effectsEnabled: menu.preferences.settings.effects!=="low"
        tintColor: Qt.rgba(menu.tintBase.r,menu.tintBase.g,menu.tintBase.b,128/255)
        edgeColor: menu.c.menuBorder; flatColor: menu.c.BG_MODAL
    }
    contentItem: Flickable {
        id: content; contentHeight: menu.metrics.height-8; contentWidth: width
        clip: true; interactive: contentHeight>height; boundsBehavior: Flickable.StopAtBounds
        Column {
            width: parent.width; spacing: 1
            Repeater {
                model: menu.details.actions || []
                delegate: WorkspaceMenuButton {
                    required property var modelData
                    objectName: "workspaceContextAction_"+(modelData.key || "separator")
                    row: modelData; kind: menu.kind; preferences: menu.preferences; width: content.width
                    onHoveredChanged: if(hovered){if(row.children)menu.showSubmenu(this,row.children);else subMenu.close()}
                    onClicked: if(row.children)menu.showSubmenu(this,row.children);else menu.pick(row)
                }
            }
        }
        C.ScrollBar.vertical: RakScrollBar { handleColor: menu.c.BG_SCROLL }
    }
    C.Popup {
        id: subMenu; objectName: "workspaceContextSubmenu"
        property var rows: []; property var metrics: ({width:8,height:8}); property var anchorButton: null
        parent: menu.hostWindow.contentItem; popupType: C.Popup.Item
        width: Math.min(metrics.width,menu.hostWindow.width-1); height: Math.min(metrics.height,menu.hostWindow.height-1)
        margins: 0; padding: 4; focus: true; modal: false; dim: false
        closePolicy: C.Popup.CloseOnEscape
        onClosed: anchorButton=null
        background: LiveGlass {
            preferences: menu.preferences
            backdropSource: menu.hostWindow.backdropSurface; origin: Qt.point(subMenu.x,subMenu.y)
            cornerRadius: 10; effectsEnabled: menu.preferences.settings.effects!=="low"
            tintColor: Qt.rgba(menu.tintBase.r,menu.tintBase.g,menu.tintBase.b,128/255)
            edgeColor: menu.c.menuBorder; flatColor: menu.c.BG_MODAL
        }
        contentItem: Flickable {
            id: subContent; contentHeight: subMenu.metrics.height-8; contentWidth: width
            clip: true; interactive: contentHeight>height; boundsBehavior: Flickable.StopAtBounds
            Column {
                width: parent.width; spacing: 1
                Repeater {
                    model: subMenu.rows
                    delegate: WorkspaceMenuButton {
                        required property var modelData
                        objectName: "workspaceContextSubAction_"+(modelData.key || "separator")
                        row: modelData; kind: "submenu"; preferences: menu.preferences; width: subContent.width
                        onClicked: menu.pick(row)
                    }
                }
            }
            C.ScrollBar.vertical: RakScrollBar { handleColor: menu.c.BG_SCROLL }
        }
    }
}
