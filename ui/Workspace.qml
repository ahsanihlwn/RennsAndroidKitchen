// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import QtQuick.Controls as C
import QtQuick.Dialogs as D
import "controls"

Window {
    id: window
    required property var workspace
    required property var preferences
    readonly property var c: preferences.colors
    readonly property var s: preferences.strings
    readonly property Item backdropSurface: surface
    readonly property int cardInset: 3
    readonly property int headerInset: 1
    readonly property int editorGap: 12
    property var dialogStack: null
    readonly property var managedDialogs: [message,fatalMessage,errorLog,settingsDialog,actionDialog,agentDialog,inboxPanel,permissionDialog,openWithDialog,branchCreate,branchPicker,makeFilesystem,androidTools,flashable,fileConflicts,symlink,partitionPicker,renxecPicker,automationLog,comparePicker]
    readonly property bool nativeDialogActive: (dialogStack && dialogStack.modalActive) || importPicker.visible || mountExtractPicker.visible || (inboxPanel.presented && focusInside(inboxPanel.contentItem)) || managedDialogs.some(dialog => dialog.presented && dialog.modal)
    function focusInside(item) {
        if(!item)return false
        for(let focused=window.activeFocusItem;focused;focused=focused.parent) {
            if(focused===item)return true
        }
        return false
    }
    readonly property bool terminalFocused: focusInside(terminalCard)
    readonly property bool agentFocused: focusInside(agentPane)
    readonly property bool monacoFocused: focusInside(monaco)
    readonly property bool treeFocused: focusInside(firstTree) || focusInside(secondTree)
    readonly property string agentFocusArea: !active?"inactive":nativeDialogActive?"dialog":fileSearch.visible?"search":
        focusInside(agentPane)?"agent":focusInside(editorCard)?"editor":terminalFocused?"terminal":focusInside(firstTree)?"tree0":focusInside(secondTree)?"tree1":"workspace"
    onAgentFocusAreaChanged: workspace.setFocusArea(agentFocusArea)
    readonly property var contextMenu: contextMenu
    property bool managedClose: true
    property bool firstFrameReady: false
    width: 1080; height: 700; minimumWidth: 550; minimumHeight: 400
    color: c.BG; title: s.windowTitle; visible: false
    onWidthChanged: if(layoutMotion)layoutMotion.schedule(true)
    onHeightChanged: if(layoutMotion)layoutMotion.schedule(true)
    onClosing: event => { if(managedClose) {event.accepted=false;workspace.requestQuit()} }
    Shortcut { sequence: "Ctrl+S"; enabled: !window.nativeDialogActive && !fileSearch.visible; onActivated: workspace.saveCurrent() }
    Shortcut {
        sequence: "Ctrl+F"
        // Monaco and xterm handle their own keyboard input. This shortcut is
        // only for tree search and the native editor-tab header fallback.
        enabled: !window.nativeDialogActive && !fileSearch.visible && !contextMenu.visible
                 && !window.terminalFocused && !window.monacoFocused && !window.agentFocused
                 && (window.treeFocused || workspace.activeKind==="editor" || !workspace.hasTabs)
        onActivated: {
            if(window.treeFocused || !workspace.hasTabs) {
                workspace.dispatch("find", {})
            } else if(workspace.activeKind==="editor")monaco.find()
        }
    }
    Shortcut { sequence: "Ctrl+W"; enabled: !window.nativeDialogActive && !fileSearch.visible && !window.agentFocused; onActivated: if(workspace.activeTab.length)workspace.closeTab(workspace.activeTab) }
    SmoothRectangle {
        id: surface
        anchors.fill: parent; color: c.BG
        // SplitViews retain sizing/drag state. Persistent native panes below
        // interpolate their slots instead of fighting the SplitView geometry.
        WorkspaceLayoutMotion {
            id: layoutMotion
            objectName: "workspaceLayoutMotion"
            panes: [leftPane,firstTreePane,secondTreePane,editorPaneMotion,terminalPaneMotion,agentPane]
            splits: [mainSplit,trees,editorSplit]
            signature: workspace.layoutMode+":"+workspace.hasTabs+":"+workspace.terminalOpen+":"+workspace.agentChat.opened
            animationsEnabled: window.visible && window.preferences.settings.animations
        }
        WorkspaceSidePanel {
            id: sidePanel
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.margins: 11
            workspace: window.workspace
            preferences: window.preferences
            hostWindow: window
            z: 5
            onHistoryRequested: target => taskHistory.showAt(target)
        }
        C.SplitView {
            id: mainSplit
            objectName: "workspaceMainSplit"
            anchors.left: sidePanel.right
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.topMargin: 11
            anchors.bottomMargin: 11
            anchors.rightMargin: 11
            anchors.leftMargin: 8
            orientation: Qt.Horizontal
            enabled: !layoutMotion.running
            opacity: layoutMotion.running?0:1
            Behavior on opacity { NumberAnimation { duration: window.preferences.settings.animations?100:0 } }
            handle: WorkspaceSplitter { objectName: "workspaceMainDivider"; preferences: window.preferences; implicitWidth: 8; alignAt: trees.y+trees.height/2 }
            Item {
                id: left
                C.SplitView.preferredWidth: workspace.rightOpen || workspace.agentChat.opened?Math.min(280,Math.max(180,window.width*.28)):mainSplit.width
                C.SplitView.fillWidth: !workspace.rightOpen
                C.SplitView.minimumWidth: workspace.agentChat.opened?120:170
                C.SplitView {
                    id: trees
                    objectName: "workspaceTreeSplit"
                    y: 0; width: parent.width; height: parent.height
                    orientation: workspace.layoutMode===2?Qt.Horizontal:Qt.Vertical
                    handle: WorkspaceSplitter { objectName: "workspaceTreeDivider"; preferences: window.preferences; horizontal: trees.orientation===Qt.Vertical }
                    Item {
                        id: firstTreeSlot
                        C.SplitView.fillWidth: true; C.SplitView.fillHeight: true
                        C.SplitView.preferredWidth: trees.width/2-4; C.SplitView.preferredHeight: trees.height/2-4
                        C.SplitView.minimumWidth: 100; C.SplitView.minimumHeight: 100
                    }
                    Item {
                        id: secondTreeSlot
                        visible: workspace.layoutMode!==1
                        C.SplitView.preferredWidth: trees.width/2-4; C.SplitView.preferredHeight: trees.height/2-4
                        C.SplitView.minimumWidth: 100; C.SplitView.minimumHeight: 100
                    }
                }
            }
            Item {
                id: right
                visible: workspace.rightOpen
                C.SplitView.fillWidth: true
                C.SplitView.minimumWidth: workspace.agentChat.opened?120:200
                C.SplitView {
                    id: editorSplit
                    objectName: "workspaceEditorSplit"
                    anchors.fill: parent
                    orientation: Qt.Vertical
                    handle: WorkspaceSplitter { objectName: "workspaceEditorDivider"; preferences: window.preferences; horizontal: true; implicitHeight: window.editorGap }
                    Item {
                        id: editorSlot
                        visible: workspace.hasTabs
                        C.SplitView.fillHeight: true; C.SplitView.minimumHeight: 80
                    }
                    Item {
                        id: terminalSlot
                        visible: workspace.terminalOpen
                        C.SplitView.preferredHeight: workspace.hasTabs?editorSplit.height/2:editorSplit.height
                        C.SplitView.minimumHeight: 80
                    }
                }
            }
            Item {
                id: agentSlot
                visible: workspace.agentChat.opened
                C.SplitView.preferredWidth: Math.min(390,window.width*.43)
                C.SplitView.minimumWidth: 240
            }
        }
        WorkspaceMotionPane {
            id: agentPane; objectName: "workspaceAgentMotion"
            layoutTarget: agentSlot; motion: layoutMotion; requestedVisible: workspace.agentChat.opened; z: 4
            WorkspaceAgentChatPanel { anchors.fill: parent; workspace: window.workspace; preferences: window.preferences }
        }
        WorkspaceMotionPane {
            id: leftPane
            layoutTarget: left; motion: layoutMotion; z: 3
        }
        WorkspaceMotionPane {
            id: firstTreePane
            objectName: "workspaceFirstTreeMotion"
            layoutTarget: firstTreeSlot; motion: layoutMotion; z: 2
            WorkspaceTree {
                id: firstTree
                anchors.fill: parent
                treeId: 0; workspace: window.workspace; preferences: window.preferences; directory: workspace.treeModel
            }
        }
        WorkspaceMotionPane {
            id: secondTreePane
            objectName: "workspaceSecondTreeMotion"
            layoutTarget: secondTreeSlot; motion: layoutMotion; requestedVisible: workspace.layoutMode!==1; z: 2
            WorkspaceTree {
                id: secondTree
                anchors.fill: parent
                treeId: 1; workspace: window.workspace; preferences: window.preferences; directory: workspace.secondTreeModel
            }
        }
        WorkspaceMotionPane {
            id: editorPaneMotion
            objectName: "workspaceEditorMotion"
            layoutTarget: editorSlot; motion: layoutMotion; requestedVisible: workspace.hasTabs
            adjacentPane: terminalPaneMotion; adjacentGap: window.editorGap
            AgentRainbowShadow {
                id: editorRainbowShadow
                objectName: "workspaceEditorRainbow"
                anchors.fill: editorCard
                radius: editorCard.radius
                active: {
                    if (!workspace.agentActivity) return false
                    const agentSettings = workspace.projectSettings ? workspace.projectSettings.agent : null
                    if (!agentSettings || !agentSettings.ui_triggers) return false
                    if (!workspace.hasTabs || workspace.activeKind !== "editor") return false
                    const activeKey = workspace.agentActivity.key(workspace.activeTab || "")
                    if (activeKey && workspace.agentActivity.paths[activeKey]) return true
                    const paths = workspace.agentActivity.paths
                    for (let k in paths) {
                        const row = paths[k]
                        if (row && (row.kind === "write" || row.state === "held")) return true
                    }
                    return false
                }
                animated: window.preferences.settings.animations
            }
            SmoothRectangle {
                id: editorCard
                objectName: "workspaceEditorCard"
                anchors.fill: parent
                clip: true
                radius: 8; color: c.BG3; border.width: 1; border.color: c.b18
                SmoothRectangle {
                    x: window.headerInset; y: window.headerInset; width: parent.width-2*window.headerInset; height: 34
                    color: c.BG_DARK; radius: 7; bottomLeftRadius: 0; bottomRightRadius: 0
                }
                WorkspaceTabs {
                    id: editorTabs; objectName: "workspaceEditorTabs"
                    x: window.headerInset; y: window.headerInset; width: parent.width-2*window.headerInset-30-(workspace.activeRunnable?30:0); height: 34
                    rightCornerRadius: Math.abs(x+width-parent.width+window.headerInset)<.5?7:0
                    preferences: window.preferences; rows: workspace.tabs; activeKey: workspace.activeTab
                    workspace: window.workspace
                    onSelected: key => workspace.selectTab(key)
                    onClosed: key => workspace.closeTab(key)
                }
                WorkspaceIconButton {
                    objectName: "workspaceRunFile"; anchors.right: parent.right; anchors.rightMargin: window.cardInset+30; y: window.headerInset+2; width: 30; height: 30
                    visible: workspace.activeRunnable; preferences: window.preferences; iconKey: 'start'; toolTip: 'Run File'
                    onClicked: workspace.runActive()
                }
                WorkspaceIconButton {
                    objectName: "workspaceSendToAgent";anchors.right: parent.right;anchors.rightMargin: window.cardInset;y: window.headerInset+2;width: 30;height: 30
                    visible: workspace.agentChat.available
                    preferences: window.preferences;iconKey: "send";toolTip: "Send to agent"
                    SmoothRectangle {visible: workspace.agentInbox.notificationCount>0;x: parent.width-7;y: 3;width: 5;height: 5;radius: 2.5;color: window.c.accent_hex}
                    onClicked: {
                        var qEd = monaco.findQuillEditor()
                        if (qEd) {
                            monaco.session.setSelection(qEd.filePath || workspace.activeTab, qEd.selectionRange)
                            workspace.dispatch("agentSend", {origin: "editor", path: qEd.filePath || workspace.activeTab, selection: qEd.selectionRange})
                        } else {
                            workspace.dispatch("agentSend", {origin: "editor"})
                        }
                    }
                }
                // Keep a single Monaco alive while auxiliary viewers are selected.
                EditorPane {
                    id: monaco
                    x: window.cardInset; y: window.headerInset+34; width: parent.width-2*window.cardInset; height: Math.max(1,parent.height-y-window.cardInset)
                    session: workspace.editor; fillColor: c.BG3; colors: c; preferences: window.preferences; hostWindow: window; visible: workspace.activeKind==="editor"; nativeTabs: true; findOverlay: editorFind
                    contextMenu: contextMenu
                }
                Repeater {
                    model: workspace.tabs
                    delegate: Loader {
                        id: viewerSlot
                        required property var modelData
                        x: window.cardInset; y: window.headerInset+34; width: editorCard.width-2*window.cardInset; height: Math.max(1,editorCard.height-y-window.cardInset)
                        active: modelData.kind!=="editor"
                        visible: active && modelData.path===workspace.activeTab
                        enabled: visible
                        sourceComponent: WorkspaceViewer {
                            workspace: window.workspace; preferences: window.preferences; details: viewerSlot.modelData
                        }
                    }
                }
            }
        }
        WorkspaceMotionPane {
            id: terminalPaneMotion
            objectName: "workspaceTerminalMotion"
            layoutTarget: terminalSlot; motion: layoutMotion; requestedVisible: workspace.terminalOpen
            adjacentPane: editorPaneMotion; revealBelow: true; adjacentGap: window.editorGap
            SmoothRectangle {
                id: terminalCard
                objectName: "workspaceTerminalCard"
                anchors.fill: parent
                clip: true
                radius: 8; color: c.BG; border.width: 1; border.color: c.b18
                SmoothRectangle {
                    x: window.headerInset; y: window.headerInset; width: parent.width-2*window.headerInset; height: 34
                    color: c.BG_DARK; radius: 7; bottomLeftRadius: 0; bottomRightRadius: 0
                    WorkspaceTabs {
                        id: terminalTabs; objectName: "workspaceTerminalTabs"; width: Math.max(60,parent.width-rootSelector.width-spamWrap.width-66); height: 34
                        preferences: window.preferences; rows: window.workspace.terminalRows; activeKey: window.workspace.activeTerminal; workspace: window.workspace; terminal: true
                        onSelected: key => window.workspace.selectTerminal(key)
                        onClosed: key => window.workspace.closeTerminal(key)
                    }
                    WorkspaceIconButton {
                        objectName: "workspaceNewTerminal"; x: terminalTabs.width+6; y: 5; width: 24; height: 24
                        preferences: window.preferences; iconKey: "add_session"; iconWidth: 12; iconHeight: 12; circular: true
                        tint: c.FG_BLUE; fillColor: c.a07; hoverFillColor: c.a18; downFillColor: c.a30; edgeWidth: 1; edgeColor: c.a25; hoverEdgeColor: c.a48
                        toolTip: "New Session"; onClicked: workspace.addTerminal()
                    }
                    WorkspaceRootSelector {
                        id: rootSelector; objectName: "workspaceRootSelector"
                        workspace: window.workspace
                        x: parent.width-width-spamWrap.width-16; y: 5; colors: c
                    }
                    Row {
                        id: spamWrap; anchors.right: parent.right; anchors.rightMargin: 8; height: 34; spacing: 6
                        RakLabel { text: "Spam Enter"; font.pixelSize: 11; color: c.FG_HOVER; anchors.verticalCenter: parent.verticalCenter }
                        RakSwitch { objectName: "workspaceSpamEnter"; width: 34; height: 18; checked: workspace.spamEnter; accentColor: c.accent_hex; offColor: c.offToggle; animationsEnabled: window.preferences.settings.animations; anchors.verticalCenter: parent.verticalCenter; onToggled: workspace.setSpamEnter(checked) }
                    }
                }
                Repeater {
                    model: workspace.terminalSessions
                    delegate: TerminalPane {
                        required property var modelData
                        objectName: "workspaceTerminal_"+modelData.key
                        x: window.cardInset; y: window.headerInset+34; width: terminalCard.width-2*window.cardInset; height: Math.max(1,terminalCard.height-y-window.cardInset)
                        session: modelData.session; fillColor: c.BG
                        inputAllowed: !window.nativeDialogActive && !fileSearch.visible && !contextMenu.visible
                        layoutSettled: !layoutMotion.running && !layoutMotion.pending
                        visible: terminalCard.visible && modelData.key===workspace.activeTerminal
                    }
                }
            }
        }
    }
    WorkspaceEditorFind {
        id: editorFind
        objectName: "workspaceEditorFind"
        preferences: window.preferences
        hostWindow: window
        editorPane: monaco
        z: 20
    }
    SmoothRectangle {
        anchors.fill: parent; z: 30; color: "#96000000" 
        opacity: window.nativeDialogActive?1:0
        visible: opacity>0
        Behavior on opacity { NumberAnimation { duration: window.preferences.settings.animations?180:0; easing.type: Easing.OutCubic } }
    }
    MessageWindow { id: message; model: window.preferences; commandTarget: window.workspace; hostWindow: window }
    MessageWindow { id: fatalMessage; objectName: "workspaceFatalMessage"; model: window.preferences; commandTarget: window.workspace; hostWindow: window }
    ErrorLogWindow { id: errorLog; model: window.preferences; commandTarget: window.workspace; hostWindow: window }
    WorkspaceSettings { id: settingsDialog; model: window.preferences; hostWindow: window; workspace: window.workspace }
    WorkspaceActionDialog { id: actionDialog; model: window.preferences; hostWindow: window; workspace: window.workspace }
    WorkspaceAgentDialog { id: agentDialog; model: window.preferences; hostWindow: window; workspace: window.workspace }
    WorkspaceInboxPanel { id: inboxPanel; model: window.preferences; hostWindow: window; workspace: window.workspace }
    WorkspacePermissions { id: permissionDialog; model: window.preferences; hostWindow: window; workspace: window.workspace }
    WorkspaceOpenWith { id: openWithDialog; model: window.preferences; hostWindow: window; workspace: window.workspace }
    WorkspaceContextMenu { id: contextMenu; preferences: window.preferences; workspace: window.workspace; hostWindow: window }
    WorkspaceSearch { id: fileSearch; preferences: window.preferences; workspace: window.workspace; hostWindow: window }
    WorkspaceHistory { id: taskHistory; preferences: window.preferences; workspace: window.workspace; hostWindow: window }
    WorkspaceBranchCreate { id: branchCreate; workspace: window.workspace; model: window.preferences; hostWindow: window }
    WorkspaceBranchPicker { id: branchPicker; workspace: window.workspace; model: window.preferences; hostWindow: window }
    WorkspaceMakeFilesystem { id: makeFilesystem; workspace: window.workspace; model: window.preferences; hostWindow: window }
    WorkspaceAndroidTools { id: androidTools; workspace: window.workspace; model: window.preferences; hostWindow: window }
    WorkspaceFlashable { id: flashable; workspace: window.workspace; model: window.preferences; hostWindow: window }
    WorkspaceFileConflicts { id: fileConflicts; workspace: window.workspace; model: window.preferences; hostWindow: window }
    WorkspaceSymlink { id: symlink; workspace: window.workspace; model: window.preferences; hostWindow: window }
    WorkspacePartitionPicker { id: partitionPicker; workspace: window.workspace; model: window.preferences; hostWindow: window }
    WorkspaceRenxecPicker { id: renxecPicker; workspace: window.workspace; model: window.preferences; hostWindow: window }
    WorkspaceAutomationLog { id: automationLog; workspace: window.workspace; model: window.preferences; hostWindow: window }
    WorkspaceComparePicker { id: comparePicker; workspace: window.workspace; model: window.preferences; hostWindow: window }
    WorkspaceCompareWindow { workspace: window.workspace; preferences: window.preferences; hostWindow: window }
    D.FileDialog {
        id: importPicker
        objectName: "workspaceImportPicker"
        title: "Import files"
        fileMode: D.FileDialog.OpenFiles
        // The Windows shell picker can crash CPython/PySide while releasing
        // its COM dialog after Open. Keep this flow in the Quick scene.
        options: D.FileDialog.DontUseNativeDialog
        onAccepted: {
            const urls=selectedFiles.map(url=>url.toString())
            Qt.callLater(function(){workspace.dispatch("importPaths",{urls:urls})})
        }
    }
    D.FolderDialog { id: mountExtractPicker; property string path: ""; title: 'Extract from image'; onAccepted: workspace.mounts.extract(path,selectedFolder) }
    Connections {
        target: workspace
        function onPresentationRequested(kind,data) {
            if(kind==="message")message.showMessage(data)
            else if(kind==="fatal") {window.requestActivate();fatalMessage.showMessage(data)}
            else if(kind==="errorLog")errorLog.showLog(data.text)
            else if(kind==="settings")settingsDialog.showSettings()
            else if(kind==="agentSettingGuide")settingsDialog.showSettings(data.target)
            else if(kind==="android")androidTools.showTools()
            else if(kind==="flashable")flashable.showBuilder(data)
            else if(kind==="fileConflicts")fileConflicts.showConflicts(data)
            else if(kind==="symlink")symlink.showSymlink(data)
            else if(kind==="symlinksCreated")symlink.dismiss()
            else if(kind==="pickFilesystem")partitionPicker.showPicker()
            else if(kind==="renxecPicker")renxecPicker.showPicker()
            else if(kind==="renxecPicked")renxecPicker.dismiss()
            else if(kind==="automationLog")automationLog.showLog()
            else if(kind==="comparePicker")comparePicker.showPicker()
            else if(kind==="mountExtract") {mountExtractPicker.path=data.path;mountExtractPicker.open()}
            else if(kind==="actionDialog")actionDialog.showForm(data)
            else if(kind==="agentPrompt")agentDialog.showRequest(data)
            else if(kind==="agentInbox")inboxPanel.showInbox()
            else if(kind==="agentDismiss" && agentDialog.details.id===data.id)agentDialog.dismiss()
            else if(kind==="contextMenu")contextMenu.showMenu(data)
            else if(kind==="import")importPicker.open()
            else if(kind==="permissions")permissionDialog.showPermissions(data)
            else if(kind==="find")fileSearch.showAt(workspace.activeTreeId===0?firstTree.view:secondTree.view)
            else if(kind==="openWith")openWithDialog.showPicker(data)
            else if(kind==="history")taskHistory.showAt(sidePanel.historyButton)
            else if(kind==="neobranchCreate")branchCreate.showCreate(data)
            else if(kind==="neobranchPicker")branchPicker.showPicker(data)
            else if(kind==="makeFilesystem")makeFilesystem.showFolder(data)
            else if(kind==="filesystemFinished" && data.path===makeFilesystem.details.path)makeFilesystem.busy=false
            else if(kind==="filesystemDismiss" && data.path===makeFilesystem.details.path)makeFilesystem.dismiss()
        }
    }
}
