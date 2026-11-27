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
    readonly property var managedDialogs: [message,fatalMessage].concat([errorLogLoader.item,settingsLoader.item,actionDialogLoader.item,agentDialogLoader.item,inboxPanelLoader.item,permissionDialogLoader.item,openWithDialogLoader.item,branchCreateLoader.item,branchPickerLoader.item,makeFilesystemLoader.item,androidToolsLoader.item,bootAnalyzerLoader.item,apkDebloaterLoader.item,sepolicyPatchLoader.item,flashableLoader.item,packSuperLoader.item,fileConflictsLoader.item,symlinkLoader.item,partitionPickerLoader.item,renxecPickerLoader.item,automationLogLoader.item,comparePickerLoader.item,compareWindowLoader.item].filter(dialog => !!dialog))
    readonly property bool nativeDialogActive: (dialogStack && dialogStack.modalActive) || importPicker.visible || mountExtractPicker.visible || (inboxPanelLoader.item && inboxPanelLoader.item.presented && focusInside(inboxPanelLoader.item.contentItem)) || managedDialogs.some(dialog => dialog.presented && dialog.modal)
    readonly property real dialogScrimOpacity: {
        if (mountExtractPicker.visible) return 1
        let peak = importPicker.visible ? importPicker.opacity : 0
        const all = managedDialogs.concat(dialogStack ? dialogStack.activeDialogs : [])
        for (let i = 0; i < all.length; ++i) {
            const d = all[i]
            if (d && d.visible && d.modal) peak = Math.max(peak, d.opacity)
        }
        return peak
    }
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
    readonly property string agentFocusArea: !active?"inactive":nativeDialogActive?"dialog":(fileSearchLoader.item && fileSearchLoader.item.visible)?"search":
        focusInside(agentPane)?"agent":focusInside(editorCard)?"editor":terminalFocused?"terminal":focusInside(firstTree)?"tree0":focusInside(secondTree)?"tree1":"workspace"
    onAgentFocusAreaChanged: workspace.setFocusArea(agentFocusArea)
    readonly property var contextMenu: contextMenu
    property bool managedClose: true
    property bool firstFrameReady: false
    property string loadingDialogKey: ""
    function openDialog(loader, url, props, actionKey, fn) {
        loader.openAsync(url, props, actionKey, fn)
    }
    function openModelDialog(loader, url, actionKey, fn) {
        loader.openAsync(url, {workspace: window.workspace, model: window.preferences, hostWindow: window}, actionKey, fn)
    }
    function openPrefDialog(loader, url, actionKey, fn) {
        loader.openAsync(url, {preferences: window.preferences, workspace: window.workspace, hostWindow: window}, actionKey, fn)
    }
    component AsyncDialogLoader : Loader {
        property var pendingCallback: null
        property string pendingKey: ""
        asynchronous: true
        function openAsync(url, props, actionKey, fn) {
            if (status === Loader.Ready && item) {
                if (actionKey && window.loadingDialogKey === actionKey) window.loadingDialogKey = ""
                if (fn) fn(item)
                return
            }
            pendingCallback = fn
            pendingKey = actionKey || ""
            if (pendingKey) window.loadingDialogKey = pendingKey
            if (status === Loader.Loading) return
            setSource(url, props)
        }
        function cancelPending() {
            pendingCallback = null
            if (pendingKey && window.loadingDialogKey === pendingKey) window.loadingDialogKey = ""
            pendingKey = ""
        }
        onStatusChanged: {
            if (status === Loader.Ready && item) {
                const fn = pendingCallback
                const key = pendingKey
                pendingCallback = null
                pendingKey = ""
                if (key && window.loadingDialogKey === key) window.loadingDialogKey = ""
                if (fn) fn(item)
            } else if (status === Loader.Error) {
                cancelPending()
            }
        }
    }
    width: 1080; height: 700; minimumWidth: 550; minimumHeight: 400
    color: c.BG; title: s.windowTitle; visible: false
    onWidthChanged: if(layoutMotion)layoutMotion.schedule(true)
    onHeightChanged: if(layoutMotion)layoutMotion.schedule(true)
    onClosing: event => { if(managedClose) {event.accepted=false;workspace.requestQuit()} }
    Shortcut { sequence: "Ctrl+S"; enabled: !window.nativeDialogActive && !(fileSearchLoader.item && fileSearchLoader.item.visible); onActivated: workspace.saveCurrent() }
    Shortcut {
        sequence: "Ctrl+F"
        // Monaco and xterm handle their own keyboard input. This shortcut is
        // only for tree search and the native editor-tab header fallback.
        enabled: !window.nativeDialogActive && !(fileSearchLoader.item && fileSearchLoader.item.visible) && !contextMenu.visible
                 && !window.terminalFocused && !window.monacoFocused && !window.agentFocused
                 && (window.treeFocused || workspace.activeKind==="editor" || !workspace.hasTabs)
        onActivated: {
            if(window.treeFocused || !workspace.hasTabs) {
                workspace.dispatch("find", {})
            } else if(workspace.activeKind==="editor")monaco.find()
        }
    }
    Shortcut { sequence: "Ctrl+W"; enabled: !window.nativeDialogActive && !(fileSearchLoader.item && fileSearchLoader.item.visible) && !window.agentFocused; onActivated: if(workspace.activeTab.length)workspace.closeTab(workspace.activeTab) }
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
            signature: workspace.layoutMode+":"+workspace.hasTabs+":"+workspace.terminalOpen+":"+(workspace.agentChat.opened && agentChatLoader.status===Loader.Ready)
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
            loadingActionKey: window.loadingDialogKey
            z: 5
            onHistoryRequested: target => window.openPrefDialog(taskHistoryLoader, "controls/WorkspaceHistory.qml", "history", function(d){d.showAt(target)})
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
            handle: WorkspaceSplitter { objectName: "workspaceMainDivider"; preferences: window.preferences; implicitWidth: 8; alignAt: trees.y+trees.height/2 }
            Item {
                id: left
                visible: workspace.layoutMode!==0
                C.SplitView.preferredWidth: workspace.rightOpen || workspace.agentChat.opened?Math.min(280,Math.max(180,window.width*.28)):mainSplit.width
                C.SplitView.fillWidth: !workspace.rightOpen
                C.SplitView.minimumWidth: workspace.agentChat.opened?120:170
                C.SplitView {
                    id: trees
                    objectName: "workspaceTreeSplit"
                    y: 0; width: parent.width; height: parent.height
                    orientation: (workspace.layoutMode===2 || workspace.layoutMode===4)?Qt.Horizontal:Qt.Vertical
                    handle: WorkspaceSplitter { objectName: "workspaceTreeDivider"; preferences: window.preferences; horizontal: trees.orientation===Qt.Vertical }
                    Item {
                        id: firstTreeSlot
                        visible: workspace.layoutMode!==0
                        C.SplitView.fillWidth: true; C.SplitView.fillHeight: true
                        C.SplitView.preferredWidth: trees.width/2-4; C.SplitView.preferredHeight: trees.height/2-4
                        C.SplitView.minimumWidth: 100; C.SplitView.minimumHeight: 100
                    }
                    Item {
                        id: secondTreeSlot
                        visible: workspace.layoutMode!==0 && workspace.layoutMode!==1
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
                visible: workspace.agentChat.opened && agentChatLoader.status===Loader.Ready
                C.SplitView.fillWidth: workspace.layoutMode===0 && !workspace.rightOpen
                C.SplitView.preferredWidth: Math.min(390,window.width*.43)
                C.SplitView.minimumWidth: 240
            }
        }
        WorkspaceMotionPane {
            id: agentPane; objectName: "workspaceAgentMotion"
            layoutTarget: agentSlot; motion: layoutMotion; requestedVisible: workspace.agentChat.opened && agentChatLoader.status===Loader.Ready; z: 4
            Loader {
                id: agentChatLoader
                width: agentPane.settledWidth; height: agentPane.settledHeight
                asynchronous: true
                function ensureLoaded() {
                    if (status === Loader.Ready && item) {
                        if (window.loadingDialogKey === "agentInbox") window.loadingDialogKey = ""
                        return item
                    }
                    window.loadingDialogKey = "agentInbox"
                    if (status !== Loader.Loading) {
                        setSource("controls/WorkspaceAgentChatPanel.qml", {workspace: window.workspace, preferences: window.preferences})
                    }
                    return null
                }
                onStatusChanged: {
                    if ((status === Loader.Ready || status === Loader.Error) && window.loadingDialogKey === "agentInbox") {
                        window.loadingDialogKey = ""
                    }
                }
                Component.onCompleted: if (workspace.agentChat.opened) ensureLoaded()
            }
            Connections {
                target: workspace.agentChat
                function onOpenChanged() { if (workspace.agentChat.opened) agentChatLoader.ensureLoaded() }
            }
        }
        WorkspaceMotionPane {
            id: leftPane
            layoutTarget: left; motion: layoutMotion; requestedVisible: workspace.layoutMode!==0; z: 3
        }
        WorkspaceMotionPane {
            id: firstTreePane
            objectName: "workspaceFirstTreeMotion"
            layoutTarget: workspace.layoutMode===4?secondTreeSlot:firstTreeSlot; motion: layoutMotion; requestedVisible: workspace.layoutMode!==0; z: 2
            WorkspaceTree {
                id: firstTree
                anchors.fill: parent
                treeId: 0; workspace: window.workspace; preferences: window.preferences; directory: workspace.treeModel
                layoutSettled: !layoutMotion.running && !layoutMotion.pending
            }
        }
        WorkspaceMotionPane {
            id: secondTreePane
            objectName: "workspaceSecondTreeMotion"
            layoutTarget: workspace.layoutMode===4?firstTreeSlot:secondTreeSlot; motion: layoutMotion; requestedVisible: workspace.layoutMode!==0 && workspace.layoutMode!==1; z: 2
            WorkspaceTree {
                id: secondTree
                anchors.fill: parent
                treeId: 1; workspace: window.workspace; preferences: window.preferences; directory: workspace.secondTreeModel
                layoutSettled: !layoutMotion.running && !layoutMotion.pending
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
                    readonly property int trailingActionsWidth: (workspace.agentChat.available ? 30 : 0)
                        + (workspace.activeRunnable ? 30 : 0)
                        + (workspace.activeKind === "editor" && !!workspace.activeDetails.markdown ? 78 : 0)
                    x: window.headerInset; y: window.headerInset; width: Math.max(80, parent.width-2*window.headerInset-trailingActionsWidth); height: 34
                    rightCornerRadius: Math.abs(x+width-parent.width+window.headerInset)<.5?7:0
                    preferences: window.preferences; rows: workspace.tabs; activeKey: workspace.activeTab
                    workspace: window.workspace
                    onSelected: key => workspace.selectTab(key)
                    onClosed: key => workspace.closeTab(key)
                }
                ImageToolButton {
                    objectName: "markdownPreviewToggle"
                    anchors.right: parent.right
                    anchors.rightMargin: window.cardInset + (workspace.agentChat.available ? 34 : 4) + (workspace.activeRunnable ? 30 : 0)
                    y: window.headerInset + 4; height: 26
                    visible: workspace.activeKind === "editor" && !!workspace.activeDetails.markdown
                    colors: window.c; text: "Preview"
                    toolTip: "Switch to Markdown Preview"
                    onClicked: workspace.setMarkdownEditMode(workspace.activeTab, false)
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
                            var targetPath = workspace.activeTab || qEd.filePath
                            monaco.session.setSelection(targetPath, qEd.selectionRange)
                            workspace.dispatch("agentSend", {origin: "editor", path: targetPath, selection: qEd.selectionRange})
                        } else {
                            workspace.dispatch("agentSend", {origin: "editor"})
                        }
                    }
                }
                // Keep a single Monaco alive while auxiliary viewers are selected.
                EditorPane {
                    id: monaco
                    x: window.cardInset; y: window.headerInset+34; width: Math.max(1,editorPaneMotion.settledWidth-2*window.cardInset); height: Math.max(1,editorPaneMotion.settledHeight-y-window.cardInset)
                    session: workspace.editor; fillColor: window.c.BG3; colors: window.c; preferences: window.preferences; hostWindow: window; visible: workspace.activeKind==="editor"; nativeTabs: true; findOverlay: editorFind
                    contextMenu: contextMenu
                }
                Repeater {
                    model: workspace.tabs
                    delegate: Loader {
                        id: viewerSlot
                        required property var modelData
                        asynchronous: true
                        x: window.cardInset; y: window.headerInset+34; width: Math.max(1,editorPaneMotion.settledWidth-2*window.cardInset); height: Math.max(1,editorPaneMotion.settledHeight-y-window.cardInset)
                        active: modelData.kind!=="editor"
                        visible: active && modelData.path===workspace.activeTab
                        enabled: visible
                        function syncViewer() {
                            if (active && !item) setSource("controls/WorkspaceViewer.qml", {workspace: window.workspace, preferences: window.preferences, details: viewerSlot.modelData})
                        }
                        onActiveChanged: syncViewer()
                        onModelDataChanged: if (item) item.details = modelData; else syncViewer()
                        Component.onCompleted: syncViewer()
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
                        id: terminalTabs; objectName: "workspaceTerminalTabs"; width: Math.max(60,parent.width-rootSelector.width-newTerminalBtn.width-20); height: 34
                        preferences: window.preferences; rows: window.workspace.terminalRows; activeKey: window.workspace.activeTerminal; workspace: window.workspace; terminal: true
                        onSelected: key => window.workspace.selectTerminal(key)
                        onClosed: key => window.workspace.closeTerminal(key)
                    }
                    WorkspaceIconButton {
                        id: newTerminalBtn; objectName: "workspaceNewTerminal"
                        x: Math.min(terminalTabs.contentWidth, terminalTabs.width)+6; y: 5; width: 24; height: 24
                        preferences: window.preferences; iconKey: "add_session"; iconWidth: 13; iconHeight: 13
                        toolTip: "New Session"; onClicked: workspace.addTerminal()
                    }
                    WorkspaceRootSelector {
                        id: rootSelector; objectName: "workspaceRootSelector"
                        workspace: window.workspace
                        x: parent.width-width-6; y: 5; colors: c
                    }
                }
                Repeater {
                    model: workspace.terminalSessions
                    delegate: TerminalPane {
                        required property var modelData
                        objectName: "workspaceTerminal_"+modelData.key
                        x: window.cardInset; y: window.headerInset+34; width: Math.max(1,terminalPaneMotion.settledWidth-2*window.cardInset); height: Math.max(1,terminalPaneMotion.settledHeight-y-window.cardInset)
                        session: modelData.session; fillColor: c.BG
                        inputAllowed: !window.nativeDialogActive && !(fileSearchLoader.item && fileSearchLoader.item.visible) && !contextMenu.visible
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
        opacity: window.dialogScrimOpacity
        visible: opacity>0.001
    }
    MessageWindow { id: message; model: window.preferences; commandTarget: window.workspace; hostWindow: window }
    MessageWindow { id: fatalMessage; objectName: "workspaceFatalMessage"; model: window.preferences; commandTarget: window.workspace; hostWindow: window }
    WorkspaceContextMenu { id: contextMenu; preferences: window.preferences; workspace: window.workspace; hostWindow: window }
    AsyncDialogLoader { id: errorLogLoader }
    AsyncDialogLoader { id: settingsLoader }
    AsyncDialogLoader { id: actionDialogLoader }
    AsyncDialogLoader { id: agentDialogLoader }
    AsyncDialogLoader { id: inboxPanelLoader }
    AsyncDialogLoader { id: permissionDialogLoader }
    AsyncDialogLoader { id: openWithDialogLoader }
    AsyncDialogLoader { id: fileSearchLoader }
    AsyncDialogLoader { id: taskHistoryLoader }
    AsyncDialogLoader { id: branchCreateLoader }
    AsyncDialogLoader { id: branchPickerLoader }
    AsyncDialogLoader { id: makeFilesystemLoader }
    AsyncDialogLoader { id: androidToolsLoader }
    AsyncDialogLoader { id: bootAnalyzerLoader }
    AsyncDialogLoader { id: apkDebloaterLoader }
    AsyncDialogLoader { id: sepolicyPatchLoader }
    AsyncDialogLoader { id: flashableLoader }
    AsyncDialogLoader { id: packSuperLoader }
    AsyncDialogLoader { id: fileConflictsLoader }
    AsyncDialogLoader { id: symlinkLoader }
    AsyncDialogLoader { id: partitionPickerLoader }
    AsyncDialogLoader { id: renxecPickerLoader }
    AsyncDialogLoader { id: automationLogLoader }
    AsyncDialogLoader { id: comparePickerLoader }
    AsyncDialogLoader { id: compareWindowLoader }
    WorkspaceFilePickerDialog {
        id: importPicker
        objectName: "workspaceImportPicker"
        workspace: window.workspace
        model: window.preferences
        hostWindow: window
        backdropSource: window.backdropSurface
        backend: workspace.importPicker
        pickerTitle: "Import files"
        openText: "Import"
        emptyText: "No files found"
        multiSelect: true
        onAcceptedPaths: urls => Qt.callLater(function(){workspace.dispatch("importPaths",{urls:urls})})
    }
    D.FolderDialog { id: mountExtractPicker; property string path: ""; title: 'Extract from image'; onAccepted: workspace.mounts.extract(path,selectedFolder) }
    Connections {
        target: workspace
        function onPresentationRequested(kind,data) {
            if(kind==="message")message.showMessage(data)
            else if(kind==="fatal") {window.requestActivate();fatalMessage.showMessage(data)}
            else if(kind==="errorLog") { const log=data; openDialog(errorLogLoader,"ErrorLogWindow.qml",{model:window.preferences,commandTarget:window.workspace,hostWindow:window},"",function(d){d.showLog(log.text,log.fatal)}) }
            else if(kind==="settings")openModelDialog(settingsLoader,"controls/WorkspaceSettings.qml","settings",function(d){d.showSettings()})
            else if(kind==="agentSettingGuide") { const tgt=data.target; openModelDialog(settingsLoader,"controls/WorkspaceSettings.qml","settings",function(d){d.showSettings(tgt)}) }
            else if(kind==="android")openModelDialog(androidToolsLoader,"controls/WorkspaceAndroidTools.qml","android",function(d){d.showTools()})
            else if(kind==="bootAnalyzer")openModelDialog(bootAnalyzerLoader,"controls/WorkspaceBootAnalyzer.qml","bootAnalyzer",function(d){d.showAnalyzer()})
            else if(kind==="apkDebloater") { const payload=data||{}; openModelDialog(apkDebloaterLoader,"controls/WorkspaceApkDebloater.qml","apkDebloater",function(d){d.showDebloater(payload.path||"")}) }
            else if(kind==="sepolicyPatch") { const payload=data||{}; openModelDialog(sepolicyPatchLoader,"controls/WorkspaceSepolicyPatch.qml","sepolicyPatch",function(d){d.showSepolicyPatch(payload.path||"")}) }
            else if(kind==="flashable") { const payload=data; openModelDialog(flashableLoader,"controls/WorkspaceFlashable.qml","",function(d){d.showBuilder(payload)}) }
            else if(kind==="packSuper") { const payload=data; openModelDialog(packSuperLoader,"controls/WorkspacePackSuper.qml","",function(d){d.showPackSuper(payload)}) }
            else if(kind==="fileConflicts") { const payload=data; openModelDialog(fileConflictsLoader,"controls/WorkspaceFileConflicts.qml","",function(d){d.showConflicts(payload)}) }
            else if(kind==="symlink") { const payload=data; openModelDialog(symlinkLoader,"controls/WorkspaceSymlink.qml","",function(d){d.showSymlink(payload)}) }
            else if(kind==="symlinksCreated") { if(symlinkLoader.item)symlinkLoader.item.dismiss(); else symlinkLoader.cancelPending() }
            else if(kind==="pickFilesystem")openModelDialog(partitionPickerLoader,"controls/WorkspacePartitionPicker.qml","",function(d){d.showPicker()})
            else if(kind==="renxecPicker")openModelDialog(renxecPickerLoader,"controls/WorkspaceRenxecPicker.qml","automation",function(d){d.showPicker()})
            else if(kind==="renxecPicked") { if(renxecPickerLoader.item)renxecPickerLoader.item.dismiss(); else renxecPickerLoader.cancelPending() }
            else if(kind==="automationLog")openModelDialog(automationLogLoader,"controls/WorkspaceAutomationLog.qml","automation",function(d){d.showLog()})
            else if(kind==="comparePicker")openModelDialog(comparePickerLoader,"controls/WorkspaceComparePicker.qml","compare",function(d){d.showPicker()})
            else if(kind==="mountExtract") {mountExtractPicker.path=data.path;mountExtractPicker.open()}
            else if(kind==="actionDialog") { const payload=data; openModelDialog(actionDialogLoader,"controls/WorkspaceActionDialog.qml","",function(d){d.showForm(payload)}) }
            else if(kind==="agentPrompt") { const payload=data; openModelDialog(agentDialogLoader,"controls/WorkspaceAgentDialog.qml","",function(d){d.showRequest(payload)}) }
            else if(kind==="agentInbox")openModelDialog(inboxPanelLoader,"controls/WorkspaceInboxPanel.qml","agentInbox",function(d){d.showInbox()})
            else if(kind==="agentDismiss") { if(agentDialogLoader.item && agentDialogLoader.item.details.id===data.id)agentDialogLoader.item.dismiss(); else agentDialogLoader.cancelPending() }
            else if(kind==="contextMenu")contextMenu.showMenu(data)
            else if(kind==="import")importPicker.showPicker()
            else if(kind==="permissions") { const payload=data; openModelDialog(permissionDialogLoader,"controls/WorkspacePermissions.qml","",function(d){d.showPermissions(payload)}) }
            else if(kind==="find") { const targetView=workspace.activeTreeId===0?firstTree.view:secondTree.view; openPrefDialog(fileSearchLoader,"controls/WorkspaceSearch.qml","",function(d){d.showAt(targetView)}) }
            else if(kind==="openWith") { const payload=data; openModelDialog(openWithDialogLoader,"controls/WorkspaceOpenWith.qml","",function(d){d.showPicker(payload)}) }
            else if(kind==="history")openPrefDialog(taskHistoryLoader,"controls/WorkspaceHistory.qml","history",function(d){d.showAt(sidePanel.historyButton)})
            else if(kind==="neobranchCreate") { const payload=data; openModelDialog(branchCreateLoader,"controls/WorkspaceBranchCreate.qml","",function(d){d.showCreate(payload)}) }
            else if(kind==="neobranchPicker") { const payload=data; openModelDialog(branchPickerLoader,"controls/WorkspaceBranchPicker.qml","",function(d){d.showPicker(payload)}) }
            else if(kind==="makeFilesystem") { const payload=data; openModelDialog(makeFilesystemLoader,"controls/WorkspaceMakeFilesystem.qml","",function(d){d.showFolder(payload)}) }
            else if(kind==="filesystemFinished" && makeFilesystemLoader.item && data.path===makeFilesystemLoader.item.details.path)makeFilesystemLoader.item.busy=false
            else if(kind==="filesystemDismiss") { if(makeFilesystemLoader.item && data.path===makeFilesystemLoader.item.details.path)makeFilesystemLoader.item.dismiss(); else makeFilesystemLoader.cancelPending() }
        }
    }
    Connections {
        target: workspace.privateFeatures ? workspace.privateFeatures.compare : null
        function onChanged() {
            if (workspace.privateFeatures.compare.visible) {
                openModelDialog(compareWindowLoader, "WorkspaceCompareWindow.qml", "compare", function(d){d.syncVisibility()})
            } else if (compareWindowLoader.item) {
                compareWindowLoader.item.syncVisibility()
            }
        }
    }
}
