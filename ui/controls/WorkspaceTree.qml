// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Window
import QtQml.Models

SmoothRectangle {
    id: panel
    required property var directory
    required property var workspace
    required property var preferences
    required property int treeId
    readonly property var c: preferences.colors
    readonly property bool activePanel: workspace.activeTreeId===treeId
    readonly property real nameWidth: workspace.nameColumnWidth
    readonly property real permissionWidth: workspace.permissionColumnWidth
    readonly property real contextWidth: Math.max(70,width-6-nameWidth-permissionWidth)
    readonly property color accentColor: c.accent_hex
    readonly property color selectedColor: Qt.rgba(accentColor.r,accentColor.g,accentColor.b,41/255)
    property int hoverRow: -1
    property int selectionRevision: 0
    property int agentRowRevision: 0
    property var selectedPaths: []
    property var pendingEdit: null
    property string editingPath: ""
    property int editingColumn: 0
    property bool editingActive: false
    property var multiAnchorParent: null
    property bool dropActive: false
    property string dropDestination: directory.location
    property bool dropAccepted: true
    property var dropPayload: ({})
    property real dropX: 0
    property real dropY: 0
    property real scrollSpeed: 0
    property real scrollRemainder: 0
    property int dropFirstRow: -1
    property int dropLastRow: -1
    property bool dropGuideActive: false
    readonly property real dropGuideX: dropGuideDepth*20+10-tree.contentX
    readonly property real dropGuideY: dropGuideFirstRow*28-tree.contentY
    readonly property real dropGuideHeight: Math.max(0,(dropLastRow-dropGuideFirstRow)*28+14)
    property int dropGuideFirstRow: -1
    property int dropGuideDepth: -1
    readonly property var dropGuideBranches: {
        let rows=[]
        if(!dropActive || !dropGuideActive || !dropAccepted)return rows
        const first=Math.max(tree.topRow,dropGuideFirstRow),last=Math.min(tree.bottomRow,dropLastRow)
        for(let row=first;row<=last;++row) {
            if(tree.depth(row)===dropGuideDepth+1)rows.push(row)
        }
        return rows
    }
    property var draggingPaths: []
    property bool nativeDragging: false
    property bool dragging: false
    property point dragPointer: Qt.point(0,0)
    property bool dragPressActive: false
    property bool previewPending: false
    property int dragGeneration: 0
    property var dragMime: ({})
    property var previewRows: []
    readonly property int previewFirstRow: previewRows.length?previewRows[0]:0
    property bool layoutSettled: true
    property alias view: tree
    onNameWidthChanged: if(tree)tree.forceLayout()
    onPermissionWidthChanged: if(tree)tree.forceLayout()
    onContextWidthChanged: if(tree)tree.forceLayout()
    onLayoutSettledChanged: if(layoutSettled && tree)tree.forceLayout()
    radius: 10; color: c.BG3; border.width: 1; border.color: c.b13
    function clearDrop(){dropActive=false;hoverRow=-1;dropFirstRow=-1;dropLastRow=-1;dropGuideActive=false;dropGuideFirstRow=-1;dropGuideDepth=-1;scrollSpeed=0;scrollRemainder=0;dropScroll.stop()}
    function endDrag(drop) {
        ++dragGeneration;previewPending=false
        if(treeDrag.Drag.active){if(drop)treeDrag.Drag.drop();else treeDrag.Drag.cancel()}
        dragging=false;dragPressActive=false;previewRows=[];clearDrop()
        treeDrag.preview=null;treeDrag.x=0;treeDrag.y=0
    }
    function moveDrag(point) {
        dragPointer=point
        if(!dragging || nativeDragging)return
        treeDrag.x=point.x-treeDrag.hotSpot.x;treeDrag.y=point.y-treeDrag.hotSpot.y
        const scene=panel.mapToItem(null,point.x,point.y),host=panel.Window.window
        if(scene.x<0 || scene.y<0 || scene.x>host.width || scene.y>host.height) {
            Qt.callLater(function(){if(panel.dragging && !panel.nativeDragging && treeDrag.Drag.active)treeDrag.Drag.startDrag()})
        }
    }
    function beginDrag(path,columnX,pressX,pressY,row) {
        const parent=directory.parentPath(path)
        draggingPaths=selectedPaths.filter(candidate=>directory.parentPath(candidate)===parent)
        if(!draggingPaths.length)draggingPaths=[path]
        dragMime=workspace.treeDragMimeData(treeId,draggingPaths)
        if(!Object.keys(dragMime).length)return
        prepareDragPreview(draggingPaths)
        treeDrag.hotSpot=Qt.point(columnX+pressX-tree.contentX,(row-previewFirstRow)*28+pressY)
        const generation=++dragGeneration
        previewPending=true
        // Input is live immediately. A fast release must not depend on the
        // asynchronous GPU drag pixmap finishing first.
        treeDrag.preview=null;treeDrag.Drag.imageSource=""
        treeDrag.x=dragPointer.x-treeDrag.hotSpot.x;treeDrag.y=dragPointer.y-treeDrag.hotSpot.y
        dragging=true;treeDrag.Drag.active=true
        dragPreview.grabToImage(function(result){
            if(generation!==panel.dragGeneration)return
            panel.previewPending=false
            if(!panel.dragPressActive){panel.previewRows=[];return}
            treeDrag.preview=result;treeDrag.Drag.imageSource=result.url
        })
    }
    function prepareDragPreview(paths) {
        let rows=[]
        for(const path of paths){
            const row=tree.rowAtIndex(directory.indexForPath(path))
            if(row>=tree.topRow && row<=tree.bottomRow)rows.push(row)
        }
        previewRows=rows.sort((a,b)=>a-b)
    }
    function updateDrop(x,y,payload) {
        dropActive=true;dropX=x;dropY=y;dropPayload=payload
        const cell=tree.cellAtPosition(x+tree.contentX,y+tree.contentY,true)
        const row=cell.y
        let index=tree.index(row,0),targetPath=""
        const source=payload.paths && payload.paths.length?payload.paths[0]:""
        dropGuideActive=false
        if(row>=0 && directory.displayMode==="tree") {
            const depth=tree.depth(row),contentX=x+tree.contentX
            const branchColumn=Math.round((contentX-10)/20)
            const branchX=branchColumn*20+10
            // The existing connector strokes become drop targets for visible ancestors.
            if(branchColumn>=0 && branchColumn<depth && Math.abs(contentX-branchX)<=7) {
                targetPath=directory.data(index,Qt.UserRole+1003) || ""
                for(let step=0;step<depth-branchColumn && targetPath;++step)targetPath=directory.parentPath(targetPath)
                if(targetPath) {
                    index=directory.indexForPath(targetPath)
                    dropGuideActive=true
                }
            }
        }
        const targetRow=targetPath?tree.rowAtIndex(index):row
        // A connector always previews its directory's whole group. The usual
        // same-directory row suppression would otherwise collapse its stem to
        // zero height after a file has been dropped into that directory once.
        const info=directory.dragTarget(index,dropGuideActive?"":source,targetRow>=0 && tree.isExpanded(targetRow))
        dropAccepted=info.accepted!==false
        dropDestination=info.destination || directory.location
        dropFirstRow=info.first?tree.rowAtIndex(directory.indexForPath(info.first)):-1
        dropLastRow=info.last?tree.rowAtIndex(directory.indexForPath(info.last)):-1
        // Light the selected directory's connector stem and all of its child
        // junctions, rather than just the segment beneath the pointer.
        dropGuideFirstRow=targetRow+1
        dropGuideDepth=targetRow>=0?tree.depth(targetRow):-1
        // Start well before the edge, with a neutral strip for choosing rows.
        // Near the edge, accelerate enough to cross large directories quickly.
        const viewportHeight=tree.height-columns.height,pointerY=y-columns.height
        const zone=viewportHeight*.40
        let speed=0,t=0
        if(pointerY<zone){t=Math.max(0,Math.min(1,1-pointerY/zone));speed=-(.20+23.80*Math.pow(t,1.65))}
        else if(pointerY>viewportHeight-zone){t=Math.max(0,Math.min(1,(pointerY-viewportHeight+zone)/zone));speed=.20+23.80*Math.pow(t,1.65)}
        if((speed>0)!==(scrollSpeed>0) || (speed<0)!==(scrollSpeed<0))scrollRemainder=0
        scrollSpeed=speed
        if(speed){if(!dropScroll.running)dropScroll.start()}else dropScroll.stop()
    }
    Timer {
        id: dropScroll; interval: 16; repeat: true
        onTriggered: {
            panel.scrollRemainder+=panel.scrollSpeed
            const step=Math.trunc(panel.scrollRemainder)
            if(!step)return
            panel.scrollRemainder-=step
            const minimum=-tree.topMargin,maximum=Math.max(minimum,tree.contentHeight-tree.height),position=Math.max(minimum,Math.min(maximum,tree.contentY+step))
            tree.contentY=position
            panel.updateDrop(panel.dropX,panel.dropY,panel.dropPayload)
            if(position===minimum && panel.scrollSpeed<0 || position===maximum && panel.scrollSpeed>0)stop()
        }
    }
    function activatePanel() {
        if (!activePanel) { workspace.selectTree(treeId); selectedPaths=[] }
        tree.forceActiveFocus()
    }
    function publishSelection() {
        let paths=[]
        const indexes=selection.selectedRows(0)
        for (let i=0;i<indexes.length;++i) {
            const path=directory.data(indexes[i],Qt.UserRole+1003)
            if (path && path!=="..") paths.push(path)
        }
        selectedPaths=paths
        workspace.selectPaths(treeId,paths)
    }
    function selectRow(row, modifiers) {
        activatePanel()
        const index=tree.index(row,0)
        if(modifiers&(Qt.ControlModifier|Qt.ShiftModifier)) {
            const parentPath=directory.parentPath(directory.data(index,Qt.UserRole+1003) || "")
            if(multiAnchorParent===null)multiAnchorParent=parentPath
            else if(parentPath!==multiAnchorParent)return
        } else multiAnchorParent=null
        let flags=ItemSelectionModel.ClearAndSelect|ItemSelectionModel.Rows
        if (modifiers&Qt.ControlModifier) flags=ItemSelectionModel.Toggle|ItemSelectionModel.Rows
        if (modifiers&Qt.ShiftModifier && tree.currentRow>=0) {
            const begin=Math.min(tree.currentRow,row), end=Math.max(tree.currentRow,row)
            selection.clearSelection()
            for(let n=begin;n<=end;++n) selection.select(tree.index(n,0),ItemSelectionModel.Select|ItemSelectionModel.Rows)
        } else selection.select(index,flags)
        selection.setCurrentIndex(index,ItemSelectionModel.NoUpdate)
        publishSelection()
    }
    function openRow(row,column) {
        const idx=tree.index(row,column)
        const path=directory.data(idx,Qt.UserRole+1003)
        if (column===0) {
            if (directory.displayMode==="tree" && tree.isExpanded(row)) tree.collapse(row)
            else if (directory.displayMode==="tree" && directory.data(idx,Qt.UserRole+1005)) tree.expand(row)
            else directory.activate(path)
        } else workspace.dispatch(column===1?"editContext":"editPermission",{path:path,treeId:treeId})
    }
    Connections {
        target: workspace
        function onChanged() { if (!panel.activePanel) { selection.clear(); panel.selectedPaths=[];panel.multiAnchorParent=null } }
        function onEditCellRequested(number,path,column) {
            if (number!==panel.treeId)return
            panel.pendingEdit={path:path,column:column}
            editDelay.restart()
        }
        function onPresentationRequested(kind,data) {
            if(kind==='mountExpand' && data.treeId===panel.treeId) {
                const index=directory.indexForPath(data.path),row=tree.rowAtIndex(index)
                if(row>=0){if(tree.isExpanded(row))tree.collapse(row);else tree.expand(row)}
            }
        }
    }
    Timer {
        id: editDelay; interval: 0
        onTriggered: {
            if(!panel.pendingEdit)return
            const cellIndex=panel.directory.cellForPath(panel.pendingEdit.path,panel.pendingEdit.column)
            panel.editingPath=panel.pendingEdit.path
            panel.editingColumn=panel.pendingEdit.column
            panel.pendingEdit=null
            tree.expandToIndex(cellIndex);tree.forceLayout()
            tree.positionViewAtIndex(cellIndex,TableView.Contain)
            tree.edit(cellIndex)
        }
    }
    Connections {
        target: panel.directory
        function onPathRevealed(path) {
            wheelScroll.reset()
            const index=panel.directory.indexForPath(path)
            tree.expandToIndex(index);tree.forceLayout()
            const row=tree.rowAtIndex(index)
            if(row>=0) {panel.selectRow(row,Qt.NoModifier);tree.positionViewAtIndex(index,TableView.Contain)}
        }
    }
    Item {
        id: header
        x: 1; y: 1; width: parent.width-2; height: 36
        AgentRainbow { objectName: "agentTreeHighlight_"+panel.directory.basePath; anchors.fill: parent
            active: !!(panel.workspace.agentActivity && panel.workspace.agentActivity.paths[panel.workspace.agentActivity.key(panel.directory.basePath || "")])
            animated: panel.preferences.settings.animations
        }
        Image { x: 14; y: 6; width: 24; height: 24; source: "image://icons/project_ic"; sourceSize: Qt.size(24,24); smooth: true }
        RakLabel {
            x: 47; anchors.verticalCenter: parent.verticalCenter
            width: Math.max(0,countBadge.x-x-9)
            text: directory.title; color: c.FG_BRIGHT; font.pixelSize: 13; font.weight: Font.DemiBold
            elide: Text.ElideRight; toolTip: directory.title
        }
        SmoothRectangle {
            id: countBadge
            width: countText.implicitWidth+16; height: 24; radius: 10
            anchors.right: parent.right; anchors.rightMargin: 13; anchors.verticalCenter: parent.verticalCenter
            color: c.BG4
            RakLabel { id: countText; anchors.centerIn: parent; text: directory.count+" items"; font.pixelSize: 10; font.weight: Font.DemiBold; color: c.FG2 }
        }
        MouseArea {
            anchors.fill: parent; acceptedButtons: Qt.LeftButton|Qt.RightButton
            onClicked: mouse => {
                panel.activatePanel()
                if (mouse.button===Qt.RightButton) workspace.dispatch("contextMenu",{path:directory.location,treeId:treeId,x:mapToItem(null,mouse.x,mouse.y).x,y:mapToItem(null,mouse.x,mouse.y).y})
            }
        }
        SmoothRectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: c.b08 }
    }
    Item {
        id: columns
        objectName: "workspaceTreeColumns"+panel.treeId
        x: 1; y: 37; width: parent.width-6; height: 40; clip: true; z: 4
        LiveGlass {
            objectName: "workspaceTreeHeaderGlass"+panel.treeId
            preferences: panel.preferences
            animateChanges: panel.preferences.settings.animations
            readonly property color backgroundColor: panel.c.BG3
            anchors.fill: parent
            backdropSource: tree
            sampleInSourceCoordinates: true
            effectsEnabled: panel.preferences.settings.effects==="high"
            refractionEnabled: panel.preferences.settings.effects==="high"
            flatColor: backgroundColor
            tintColor: Qt.rgba(backgroundColor.r,backgroundColor.g,backgroundColor.b,.58)
            cornerRadius: 0; edgeColor: "transparent"
        }
        MouseArea { anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.AllButtons; onWheel: wheel=>wheel.accepted=true }
        Repeater {
            model: ["Name","Selinux","Permission"]
            delegate: Item {
                required property int index
                required property string modelData
                objectName: "workspaceTreeColumn"+panel.treeId+"_"+index
                x: (index===0?0:index===1?panel.nameWidth:panel.nameWidth+panel.contextWidth)-tree.contentX
                width: index===0?panel.nameWidth:index===1?panel.contextWidth:panel.permissionWidth
                height: 40; clip: true
                HoverHandler { id: headerHover }
                SmoothRectangle { anchors.fill: parent; color: headerHover.hovered?c.b06:"transparent" }
                RakLabel { x: 6; y: -1; width: parent.width-12; height: parent.height; text: modelData; font.pixelSize: 11; font.weight: Font.DemiBold; color: c.FG2; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                MouseArea {
                    visible: index!==1; width: 7; height: parent.height; anchors.right: parent.right
                    cursorShape: Qt.SplitHCursor
                    property real initial: 0
                    property real starting: 0
                    onPressed: mouse => { initial=mapToItem(panel,mouse.x,mouse.y).x; starting=index===0?panel.nameWidth:panel.permissionWidth }
                    onPositionChanged: mouse => {
                        if (!pressed)return
                        const size=Math.max(70,starting+mapToItem(panel,mouse.x,mouse.y).x-initial)
                        workspace.resizeColumn(index,size)
                        tree.forceLayout()
                    }
                }
            }
        }
        SmoothRectangle { y: height-1; width: parent.width; height: 1; color: c.b08 }
    }
    Timer { id: selectionPublish; interval: 0; onTriggered: if(panel.activePanel)panel.publishSelection() }
    WorkspaceTreeWheel { id: wheelScroll; objectName: "treeWheelScroll"+panel.treeId; view: tree; animationsEnabled: panel.preferences.settings.animations }
    Connections {
        target: panel.directory.viewModel
        function onModelReset(){++panel.agentRowRevision;wheelScroll.reset();panel.multiAnchorParent=null;selectionPublish.restart()}
        function onRowsInserted(){++panel.agentRowRevision}
        function onRowsRemoved(){++panel.agentRowRevision}
        function onLayoutChanged(){++panel.agentRowRevision}
        function onDataChanged(first,last,roles){if(!roles.length || roles.indexOf(Qt.UserRole+1020)>=0 || roles.indexOf(Qt.UserRole+5000)>=0)++panel.agentRowRevision}
    }
    ItemSelectionModel {
        id: selection; model: panel.directory.viewModel
        onSelectionChanged: {++panel.selectionRevision;if(panel.activePanel)selectionPublish.restart()}
    }
    TreeView {
        id: tree
        objectName: "workspaceTree"+panel.treeId
        // The top margin preserves the first row's position while the viewport
        // extends behind the fixed header for its live glass source.
        x: 1; y: columns.y; width: parent.width-6; height: parent.height-y-11
        topMargin: columns.height
        Component.onCompleted: contentY=-topMargin
        model: panel.directory.viewModel
        selectionModel: selection
        clip: true; boundsBehavior: Flickable.StopAtBounds
        columnSpacing: 0; rowSpacing: 0
        columnWidthProvider: column => column===0?panel.nameWidth:column===1?panel.contextWidth:panel.permissionWidth
        rowHeightProvider: row => 28
        onExpanded: (row,depth) => {++panel.selectionRevision;panel.directory.ensureLoaded(tree.index(row,0))}
        onCollapsed: ++panel.selectionRevision
        onRowsChanged: ++panel.selectionRevision
        onContentYChanged: if(treeMouse.containsMouse && !treeMouse.pressed) Qt.callLater(treeMouse.refreshHover)
        onContentXChanged: if(treeMouse.containsMouse && !treeMouse.pressed) Qt.callLater(treeMouse.refreshHover)
        reuseItems: true
        editTriggers: TableView.NoEditTriggers
        C.ScrollBar.vertical: WorkspaceTreeScrollBar { objectName: "treeVerticalScrollBar"+panel.treeId; colors: panel.c; z: 3; y: columns.height; height: Math.max(0,tree.height-y); onPressedChanged: if(pressed)wheelScroll.reset() }
        C.ScrollBar.horizontal: WorkspaceTreeScrollBar { objectName: "treeHorizontalScrollBar"+panel.treeId; orientation: Qt.Horizontal; colors: panel.c; z: 3 }
        Keys.onPressed: event => {
            if(panel.dragging && event.key===Qt.Key_Escape){panel.endDrag(false);event.accepted=true}
            else if (event.key===Qt.Key_Delete) { workspace.dispatch("delete",{});event.accepted=true }
            else if (event.key===Qt.Key_F2) {workspace.dispatch("rename",{});event.accepted=true}
            else if (event.key===Qt.Key_Return || event.key===Qt.Key_Enter) { if (currentRow>=0) panel.openRow(currentRow,0);event.accepted=true }
            else if (event.key===Qt.Key_Backspace) { directory.goUp();event.accepted=true }
            else if ((event.modifiers&Qt.ControlModifier) && event.key===Qt.Key_C) {workspace.dispatch("copy",{});event.accepted=true}
            else if ((event.modifiers&Qt.ControlModifier) && event.key===Qt.Key_X) {workspace.dispatch("cut",{});event.accepted=true}
            else if ((event.modifiers&Qt.ControlModifier) && event.key===Qt.Key_V) {workspace.dispatch("paste",{});event.accepted=true}
            else if (event.key===Qt.Key_F5) {directory.refresh();event.accepted=true}
        }
        // The press owner must survive source-row recycling during auto-scroll.
        MouseArea {
            id: treeMouse
            parent: tree; anchors.fill: parent; z: 1
            enabled: !panel.editingActive
            hoverEnabled: true; acceptedButtons: Qt.LeftButton|Qt.RightButton
            preventStealing: true
            property string pressPath: ""
            property int pressRow: -1
            property int pressColumn: 0
            property bool branchPress: false
            property bool deferSelection: false
            property bool pressWasDrag: false
            property point pressPoint: Qt.point(0,0)
            function cellAt(x,y) {return tree.cellAtPosition(x+tree.contentX,y+tree.contentY,true)}
            function refreshHover() {if(containsMouse && !pressed)panel.hoverRow=cellAt(mouseX,mouseY).y}
            function isBranch(x) {
                const depth=tree.depth(pressRow)
                return pressColumn===0 && directory.displayMode==="tree" && directory.data(tree.index(pressRow,0),Qt.UserRole+1005)
                    && x+tree.contentX>=depth*20 && x+tree.contentX<depth*20+20
            }
            onPressed: mouse => {
                wheelScroll.reset()
                pressWasDrag=false
                const cell=cellAt(mouse.x,mouse.y)
                pressRow=cell.y;pressColumn=cell.x
                pressPath=directory.data(tree.index(pressRow,0),Qt.UserRole+1003) || ""
                if(!pressPath && mouse.button===Qt.LeftButton){mouse.accepted=false;return}
                panel.dragPressActive=mouse.button===Qt.LeftButton
                panel.dragPointer=mapToItem(panel,mouse.x,mouse.y)
                pressPoint=Qt.point(mouse.x,mouse.y)
                branchPress=mouse.button===Qt.LeftButton && pressRow>=0 && isBranch(mouse.x)
                const selected=pressRow>=0 && selection.isSelected(tree.index(pressRow,0))
                deferSelection=mouse.button===Qt.LeftButton && selected && panel.selectedPaths.length>1 && !(mouse.modifiers&(Qt.ControlModifier|Qt.ShiftModifier))
                if(branchPress)return
                if(pressPath && !deferSelection && (mouse.button===Qt.LeftButton || !selected))panel.selectRow(pressRow,mouse.button===Qt.RightButton?Qt.NoModifier:mouse.modifiers)
            }
            onClicked: mouse => {
                if(pressWasDrag)return
                if(mouse.button===Qt.RightButton) {
                    panel.activatePanel()
                    const point=mapToItem(null,mouse.x,mouse.y)
                    workspace.dispatch("contextMenu",{path:pressPath || directory.location,treeId:panel.treeId,x:point.x,y:point.y,paths:panel.selectedPaths})
                } else if(branchPress) {
                    panel.activatePanel()
                    if(tree.isExpanded(pressRow))tree.collapse(pressRow);else tree.expand(pressRow)
                } else if(deferSelection)panel.selectRow(pressRow,mouse.modifiers)
                deferSelection=false
            }
            onDoubleClicked: mouse => {if(mouse.button===Qt.LeftButton && pressPath && !branchPress && !pressWasDrag)panel.openRow(pressRow,pressColumn)}
            onEntered: panel.hoverRow=cellAt(mouseX,mouseY).y
            onExited: panel.hoverRow=-1
            onPositionChanged: mouse => {
                if(pressed) {
                    const point=mapToItem(panel,mouse.x,mouse.y)
                    panel.dragPointer=point
                    if(!pressWasDrag && pressPath && pressPath!==".." && !branchPress && (pressedButtons&Qt.LeftButton)
                        && (Math.abs(mouse.x-pressPoint.x)>=Qt.styleHints.startDragDistance || Math.abs(mouse.y-pressPoint.y)>=Qt.styleHints.startDragDistance)) {
                        pressWasDrag=true
                        panel.beginDrag(pressPath,0,pressPoint.x+tree.contentX,pressPoint.y+tree.contentY-pressRow*28,pressRow)
                    }
                    panel.moveDrag(point)
                }
                else panel.hoverRow=cellAt(mouse.x,mouse.y).y
            }
            onReleased: {if(!panel.nativeDragging)panel.endDrag(panel.dragging);panel.dragPressActive=false}
            onCanceled: {if(!panel.nativeDragging)panel.endDrag(false)}
            onWheel: wheel => wheelScroll.handle(wheel)
        }
        delegate: Item {
            id: cell
            required property int row
            required property int column
            required property int depth
            required property bool expanded
            required property bool selected
            required property var entry
            property var displayEntry: null
            property bool pooled: false
            function refreshEntry() {displayEntry=!pooled && entry && entry.alive?entry:null}
            onEntryChanged: refreshEntry()
            Component.onCompleted: refreshEntry()
            TableView.onPooled: {pooled=true;displayEntry=null}
            TableView.onReused: {pooled=false;refreshEntry()}
            Connections { target: cell.displayEntry; function onRetiring(){cell.displayEntry=null} }
            implicitWidth: 100; implicitHeight: 28
            clip: column!==0
            TableView.editDelegate: RakField {
                property string editPath: ""
                property int editColumn: 0
                x: cell.column===0?cell.depth*20+39:4
                y: 1; width: cell.width-x-2; height: cell.height-2
                fillColor: c.BG_INPUT; edgeColor: c.a55; focusEdgeColor: c.a55
                labelColor: c.FG; font.pixelSize: 13; cornerRadius: 3
                leftPadding: 2; rightPadding: 2; topPadding: 1; bottomPadding: 1
                property bool committed: false
                property string originalText: ""
                Component.onCompleted: {
                    panel.editingActive=true
                    editPath=panel.editingPath;editColumn=panel.editingColumn
                    text=panel.directory.data(panel.directory.cellForPath(editPath,editColumn),Qt.EditRole) || ""
                    originalText=text
                    cursorPosition=text.length;deselect()
                }
                TableView.onCommit: {
                    committed=true
                    if(text!==originalText)panel.directory.setData(panel.directory.cellForPath(editPath,editColumn),text,Qt.EditRole)
                }
                Component.onDestruction: {panel.editingActive=false;if(!committed)panel.directory.cancelDraftForPath(editPath)}
            }
            Loader {
                anchors.fill: parent
                active: cell.column===0
                visible: active && !!cell.displayEntry
                sourceComponent: Component { WorkspaceEntry {
                width: parent.width; height: parent.height
                workspace: panel.workspace; preferences: panel.preferences
                details: cell.displayEntry || ({}); depth: cell.depth; treeMode: directory.displayMode==="tree"; settled: panel.layoutSettled
                Image {
                    objectName: "treeBranchArrow:"+(cell.displayEntry?cell.displayEntry.path:"")
                    visible: directory.displayMode==="tree" && !!(cell.displayEntry && cell.displayEntry.folder)
                    x: 4+cell.depth*20; y: 8; width: 12; height: 12
                    source: "image://icons/arrow_right?color="+encodeURIComponent(c.treeArrow)
                    sourceSize: Qt.size(24,24); smooth: true; cache: true
                    opacity: .8
                    rotation: cell.expanded?90:0
                }
                } }
            }
            RakLabel {
                visible: column>0; x: 7; y: 1; width: parent.width-12; height: parent.height
                text: (cell.displayEntry?(column===1?cell.displayEntry.context:cell.displayEntry.permissions):"") || ""
                color: column===1 && cell.displayEntry && cell.displayEntry.contextColor && cell.displayEntry.contextColor.a>0?cell.displayEntry.contextColor:c.FG
                font.pixelSize: 13; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
            }
        }
    }
    Item {
        objectName: "treeFeedbackViewport"+panel.treeId
        // Feedback belongs to the viewport, never to a pooled table cell.
        parent: tree; z: 2
        width: tree.width; height: tree.height; clip: true
        SmoothRectangle {
            objectName: "treeHoverHighlight"+panel.treeId
            readonly property bool rowSelected: {
                panel.selectionRevision
                return panel.hoverRow>=0 && selection.isSelected(tree.index(panel.hoverRow,0))
            }
            visible: panel.hoverRow>=0 && panel.hoverRow<tree.rows && !rowSelected && !panel.dropActive && !panel.workspace.contextMenuActive
            x: 2; y: panel.hoverRow*28-tree.contentY; width: parent.width-4; height: 28; radius: 1
            color: panel.c.b07
        }
        Repeater {
            model: panel.selectedPaths.length>0 ? Math.max(0,Math.ceil(tree.height/28)+2) : 0
            delegate: SmoothRectangle {
                required property int index
                readonly property int viewRow: tree.topRow+index
                readonly property bool rowSelected: {
                    panel.selectionRevision
                    return viewRow>=0 && viewRow<=tree.bottomRow && viewRow<tree.rows && selection.isSelected(tree.index(viewRow,0))
                }
                objectName: "treeSelectionHighlight"+panel.treeId+"_"+viewRow
                visible: rowSelected
                x: 2; y: viewRow*28-tree.contentY; width: tree.width-4; height: 28; radius: 1
                color: panel.selectedColor
            }
        }
        Repeater {
            readonly property bool hasAgentPaths: !!(panel.workspace.agentActivity && (panel.agentRowRevision>=0) && Object.keys(panel.workspace.agentActivity.paths || {}).length>0)
            model: hasAgentPaths ? Math.max(0,Math.ceil(tree.height/28)+2) : 0
            delegate: AgentRainbow {
                required property int index
                readonly property int viewRow: tree.topRow+index
                readonly property bool inViewport: viewRow>=0 && viewRow<=tree.bottomRow && viewRow<tree.rows
                readonly property string path: {
                    panel.agentRowRevision;panel.selectionRevision
                    return inViewport?(directory.data(tree.index(viewRow,0),Qt.UserRole+1003) || ""):""
                }
                objectName: "agentFileHighlight_"+path
                visible: inViewport && active
                x: 2; y: viewRow*28-tree.contentY; width: tree.width-4; height: 28
                active: inViewport && !!(panel.workspace.agentActivity && panel.workspace.agentActivity.paths[panel.workspace.agentActivity.key(path)])
                animated: panel.preferences.settings.animations
            }
        }
        SmoothRectangle {
            objectName: "treeDropHighlight"+panel.treeId
            visible: panel.dropActive && panel.dropFirstRow>=0 && panel.dropLastRow>=panel.dropFirstRow
            x: 2; y: panel.dropFirstRow*28-tree.contentY; width: Math.max(tree.width,tree.contentWidth)-4
            height: (panel.dropLastRow-panel.dropFirstRow+1)*28; radius: 1
            color: Qt.rgba(panel.accentColor.r,panel.accentColor.g,panel.accentColor.b,56/255)
        }
        Item {
            id: dropGuide
            objectName: "treeDropGuide"+panel.treeId
            visible: panel.dropActive && panel.dropGuideActive && panel.dropAccepted && height>0
            x: panel.dropGuideX; y: panel.dropGuideY; width: 12; height: panel.dropGuideHeight
            SmoothRectangle { width: 2; height: parent.height; radius: 1; color: panel.accentColor }
            Repeater {
                model: panel.dropGuideBranches
                delegate: SmoothRectangle {
                    required property int modelData
                    objectName: "treeDropBranch"+panel.treeId+"_"+modelData
                    x: 0; y: modelData*28-tree.contentY-dropGuide.y+13
                    width: 11; height: 2; radius: 1; color: panel.accentColor
                }
            }
        }
    }
    WorkspaceDropArea {
        objectName: "treeDropArea"+panel.treeId
        x: tree.x; y: tree.y+columns.height; width: tree.width; height: tree.height-columns.height
        workspace: panel.workspace; treeId: panel.treeId; destination: panel.directory.location; treePanel: panel
    }
    Item {
        id: treeDrag; objectName: "treeNativeDrag"+panel.treeId
        property var preview: null
        readonly property var dragMime: panel.dragMime
        property point hotSpot: Qt.point(0,0)
        readonly property bool mounted: !!panel.dragMime["application/x-rdt-mount"]
        Drag.source: treeDrag
        Drag.dragType: Drag.Internal
        Drag.supportedActions: mounted?Qt.CopyAction:Qt.CopyAction|Qt.MoveAction
        Drag.proposedAction: mounted?Qt.CopyAction:Qt.MoveAction
        Drag.hotSpot: hotSpot; Drag.mimeData: panel.dragMime
        Drag.onDragStarted: panel.nativeDragging=true
        Drag.onDragFinished: {
            Drag.active=false
            panel.nativeDragging=false;panel.endDrag(false)
        }
    }
    ShaderEffectSource {
        parent: panel.Window.window?panel.Window.window.contentItem:panel
        readonly property point position: panel.mapToItem(parent,treeDrag.x,treeDrag.y)
        x: position.x; y: position.y; z: 20000
        width: dragPreview.width; height: dragPreview.height
        visible: panel.dragging && !panel.nativeDragging
        // Keep the capture source hidden, including during a native drag.
        // Its panel-relative position can otherwise overlap the other header.
        sourceItem: dragPreview
        live: true; hideSource: true; smooth: true
    }
    Item {
        id: dragPreview; objectName: "treeDragPreview"+panel.treeId
        x: -2*width; width: tree.width
        height: panel.previewRows.length?(panel.previewRows[panel.previewRows.length-1]-panel.previewFirstRow+1)*28:28
        visible: panel.previewRows.length>0
        Repeater {
            model: panel.previewRows
            delegate: Item {
                required property int modelData
                x: 0; y: (modelData-panel.previewFirstRow)*28; width: tree.width; height: 28
                ShaderEffectSource {
                    anchors.fill: parent
                    sourceItem: tree.contentItem; sourceRect: Qt.rect(tree.contentX,parent.modelData*28,tree.width,28)
                    live: false; hideSource: false; smooth: true
                }
                SmoothRectangle { x: 2; y: 0; width: parent.width-4; height: 28; radius: 1; color: panel.selectedColor }
            }
        }
    }
}
