// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Dialogs as D

DialogWindow {
    id: dialog
    objectName: "workspaceBootAnalyzer"
    required property var workspace
    readonly property var backend: workspace.bootAnalyzer
    // 0 = Partitions, 1 = Live Boot (VFS + Logcat), 2 = Logic Map, 3 = Auto-Fix
    property int page: 0
    property string pickerMode: "stage"
    property string pickerSlotKey: ""
    property int expandedLogIndex: -1
    property bool autoScrollLogcat: true
    property bool showLivePhone: false
    property string hoveredGateReason: ""

    implicitWidth: Math.max(760, Math.min(hostWindow.width * 0.80, 1080))
    implicitHeight: Math.max(500, Math.min(hostWindow.height * 0.82, 700))
    panelRadius: 12
    closePolicy: C.Popup.CloseOnEscape
    onClosed: dialog.backend.onDialogClosed()

    readonly property color colOk: dialog.c.GREEN_STATUS || "#22c55e"
    readonly property color colWarn: "#f59e0b"
    readonly property color colErr: dialog.c.RED_STATUS || "#ef4444"
    readonly property color colAccent: dialog.c.accent_hex || "#38bdf8"
    readonly property bool lightTheme: !!(dialog.c && dialog.c.is_light)
    readonly property string paletteName: (dialog.model && dialog.model.paletteName) ? dialog.model.paletteName : "midnight"

    function statusColor(status) {
        const s = String(status || "").toLowerCase()
        if (s === "error" || s === "e" || s === "f") return colErr
        if (s === "warn" || s === "w" || s === "missing") return colWarn
        if (s === "ok" || s === "i") return colOk
        return colAccent
    }

    function statusSymbol(status) {
        const s = String(status || "").toLowerCase()
        if (s === "error" || s === "e" || s === "f") return "status_error"
        if (s === "warn" || s === "w" || s === "missing") return "status_warn"
        return "status_check"
    }

    function logLevelColor(level) {
        if (level === "F" || level === "E") return colErr
        if (level === "W") return colWarn
        return colAccent
    }

    function extractDropPaths(event) {
        const out = []
        if (event.hasUrls && event.urls) {
            for (let i = 0; i < event.urls.length; i++)
                out.push(event.urls[i].toString())
        }
        const mime = event.source && event.source.dragMime ? event.source.dragMime : null
        function readFmt(fmt) {
            if (mime && mime[fmt]) return String(mime[fmt])
            if (event.formats && event.formats.indexOf(fmt) >= 0) return String(event.getDataAsString(fmt) || "")
            return ""
        }
        const treeRaw = readFmt("application/x-rak-tree")
        if (treeRaw) {
            try {
                const parsed = JSON.parse(treeRaw)
                const arr = parsed && parsed.paths ? parsed.paths : []
                for (let j = 0; j < arr.length; j++) out.push(String(arr[j]))
            } catch (e) {}
        }
        const imgRaw = readFmt("application/x-rak-image")
        if (imgRaw) {
            const parts = imgRaw.split("\n")
            if (parts.length > 1 && parts[1]) out.push(parts.slice(1).join("\n"))
        }
        if (out.length === 0) {
            const uriRaw = readFmt("text/uri-list")
            if (uriRaw) {
                const lines = uriRaw.split(/\r?\n/)
                for (let k = 0; k < lines.length; k++) {
                    const ln = lines[k].trim()
                    if (ln && ln[0] !== "#") out.push(ln)
                }
            }
        }
        return out
    }

    function canAcceptDrop(event) {
        if (event.hasUrls) return true
        const mime = event.source && event.source.dragMime ? event.source.dragMime : null
        if (mime && (mime["application/x-rak-tree"] || mime["application/x-rak-image"] || mime["text/uri-list"]))
            return true
        if (event.formats) {
            return event.formats.indexOf("application/x-rak-tree") >= 0
                || event.formats.indexOf("application/x-rak-image") >= 0
                || event.formats.indexOf("text/uri-list") >= 0
        }
        return false
    }

    function showAnalyzer() {
        page = 0
        expandedLogIndex = -1
        backend.open()
        open()
    }

    function openTreePicker(mode, slotKey) {
        pickerMode = mode || "stage"
        pickerSlotKey = slotKey || ""
        treePicker.multiSelect = (pickerMode === "stage" || pickerMode === "super")
        if (pickerMode === "slot" && pickerSlotKey)
            treePicker.pickerTitle = "Import " + pickerSlotKey + ".img"
        else if (pickerMode === "super")
            treePicker.pickerTitle = "Import super.img or Logical Partitions"
        else
            treePicker.pickerTitle = "Import Firmware & Partition Images"
        treePicker.currentFolder = dialog.backend.activeTreeUrl
        treePicker.showPicker()
    }

    Connections {
        target: dialog.backend
        function onBootStarted() {
            dialog.page = 1
            dialog.expandedLogIndex = -1
            dialog.autoScrollLogcat = true
            dialog.showLivePhone = false
        }
        function onLogcatChanged() {
            if (dialog.autoScrollLogcat && dialog.page === 1 && logcatList.count > 0)
                Qt.callLater(function() { logcatList.positionViewAtEnd() })
        }
        function onVfsChanged() {
            const r = dialog.backend.vfsRevealRow
            if (r >= 0 && r < vfsList.count)
                Qt.callLater(function() { vfsList.positionViewAtIndex(r, ListView.Contain) })
        }
        function onJumpToLiveBootRequested() {
            dialog.page = 1
        }
        function onQemuStarted() {
            if (!dialog.backend.qemuFeatureEnabled)
                return
            dialog.page = 1
            dialog.showLivePhone = true
        }
    }

    // Reused RAK tree-view file/folder picker (for importing outside current tree)
    WorkspaceFilePickerDialog {
        id: treePicker
        objectName: "bootAnalyzerTreePicker"
        workspace: dialog.workspace
        model: dialog.model
        hostWindow: dialog.hostWindow
        backdropSource: dialog.backdropSurface
        backend: dialog.backend.picker
        pickerTitle: "Import Firmware & Partition Images"
        openText: "Import"
        emptyText: "No .img, .zip, or partition folders found"
        multiSelect: true
        onAcceptedPaths: urls => {
            if (dialog.pickerMode === "slot") {
                Qt.callLater(function() { dialog.backend.stagePaths(urls, "firmware", dialog.pickerSlotKey) })
            } else if (dialog.pickerMode === "super") {
                Qt.callLater(function() { dialog.backend.stagePaths(urls, "super", "") })
            } else {
                Qt.callLater(function() { dialog.backend.stagePaths(urls, "auto", "") })
            }
        }
    }

    D.FileDialog {
        id: exportJsonPicker
        title: "Export Boot Analyzer Report (JSON)"
        fileMode: D.FileDialog.SaveFile
        defaultSuffix: "json"
        nameFilters: ["JSON Report (*.json)", "All files (*)"]
        onAccepted: dialog.backend.exportJson(selectedFile)
    }

    Item {
        id: body
        anchors.fill: parent

        // ── Titlebar (48px) ──
        Item {
            id: titlebar
            width: parent.width
            height: 48

            Row {
                x: 18
                height: parent.height
                spacing: 10
                WorkspaceNativeIcon {
                    width: 17; height: 17; iconSize: 17
                    iconKey: "boot_analyzer"
                    strokeColor: dialog.colAccent
                    accentColor: dialog.colAccent
                    isLight: dialog.lightTheme
                    anchors.verticalCenter: parent.verticalCenter
                }
                RakLabel {
                    text: "Boot Analyzer"
                    font.pixelSize: 13; font.weight: Font.Bold; font.letterSpacing: 0.3
                    height: parent.height
                    color: dialog.c.FG
                }

                // Pure QML status symbol counters (shown once report exists)
                Row {
                    visible: dialog.backend.hasReport
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 8
                    leftPadding: 4

                    Row {
                        spacing: 4
                        WorkspaceNativeIcon {
                            width: 14; height: 14; iconSize: 14
                            iconKey: "status_check"
                            strokeColor: dialog.colOk
                            isLight: dialog.lightTheme
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        RakLabel {
                            text: String(dialog.backend.passCount)
                            font.pixelSize: 11; font.weight: Font.DemiBold
                            color: dialog.colOk
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        spacing: 4
                        WorkspaceNativeIcon {
                            width: 14; height: 14; iconSize: 14
                            iconKey: "status_warn"
                            strokeColor: dialog.colWarn
                            isLight: dialog.lightTheme
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        RakLabel {
                            text: String(dialog.backend.warnCount)
                            font.pixelSize: 11; font.weight: Font.DemiBold
                            color: dialog.colWarn
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Row {
                        spacing: 4
                        WorkspaceNativeIcon {
                            width: 14; height: 14; iconSize: 14
                            iconKey: "status_error"
                            strokeColor: dialog.colErr
                            isLight: dialog.lightTheme
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        RakLabel {
                            text: String(dialog.backend.blockerCount)
                            font.pixelSize: 11; font.weight: Font.DemiBold
                            color: dialog.colErr
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                }
            }

            Row {
                anchors.right: parent.right
                anchors.rightMargin: 16
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8

                AndroidToolButton {
                    height: 28
                    colors: dialog.c
                    text: "Import"
                    enabled: !dialog.backend.running && !dialog.backend.stagingBusy
                    onClicked: dialog.openTreePicker("stage", "")
                }

                AndroidToolButton {
                    height: 28
                    colors: dialog.c
                    text: "Export JSON"
                    visible: dialog.backend.hasReport
                    enabled: dialog.backend.hasReport && !dialog.backend.running
                    onClicked: exportJsonPicker.open()
                }

                AndroidToolButton {
                    height: 28
                    colors: dialog.c
                    selected: true
                    text: dialog.backend.running ? "Booting…" : "Boot"
                    enabled: dialog.backend.canBoot && !dialog.backend.running && !dialog.backend.stagingBusy
                    onClicked: dialog.backend.startBoot()
                }
            }

            SmoothRectangle { y: parent.height - 1; width: parent.width; height: 1; color: dialog.c.b09 }
        }

        // ── Navigation Bar (38px) ──
        Item {
            id: navbar
            y: 48
            width: parent.width
            height: 38

            Row {
                x: 16
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6

                Repeater {
                    model: [
                        { idx: 0, label: "Partitions" },
                        { idx: 1, label: "Live Boot" },
                        { idx: 2, label: "Logic Map" + (dialog.backend.logicMapCount > 0 ? " (" + dialog.backend.logicMapCount + ")" : "") },
                        { idx: 3, label: "Auto-Fix" + (dialog.backend.autofixRecipes.length > 0 ? " (" + dialog.backend.autofixRecipes.length + ")" : "") }
                    ]
                    delegate: AndroidToolButton {
                        required property var modelData
                        colors: dialog.c
                        height: 24
                        text: modelData.label
                        selected: dialog.page === modelData.idx
                        onClicked: dialog.page = modelData.idx
                    }
                }
            }

            RakLabel {
                anchors.right: parent.right
                anchors.rightMargin: 16
                anchors.verticalCenter: parent.verticalCenter
                width: Math.min(360, parent.width * 0.44)
                horizontalAlignment: Text.AlignRight
                elide: Text.ElideMiddle
                font.pixelSize: 10
                color: dialog.c.FG3
                text: dialog.backend.profileSummary || dialog.backend.stagedSummary
            }

            SmoothRectangle { y: parent.height - 1; width: parent.width; height: 1; color: dialog.c.b09 }
        }

        // ── Main Content Area ──
        Item {
            id: contentArea
            y: 86
            width: parent.width
            height: parent.height - 86 - 44

            // =================================================================
            // PAGE 0: LEFT DRAGGABLE WORKSPACE TREE + RIGHT COMPACT CONTAINERS
            // =================================================================
            Item {
                anchors.fill: parent
                visible: dialog.page === 0

                // ── LEFT SIDEBAR: INTERACTIVE WORKSPACE TREE (Drag & Drop Source) ──
                Item {
                    id: treeSidebar
                    width: Math.max(220, Math.min(270, Math.floor(parent.width * 0.27)))
                    height: parent.height

                    // Tree switcher (if project has 2 trees) + path bar
                    Item {
                        id: treeTopBar
                        width: parent.width
                        height: dialog.backend.trees.length > 1 ? 62 : 34

                        Row {
                            visible: dialog.backend.trees.length > 1
                            x: 10; y: 6
                            spacing: 4
                            Repeater {
                                model: dialog.backend.trees
                                delegate: AndroidToolButton {
                                    required property int index
                                    required property string modelData
                                    colors: dialog.c
                                    height: 22
                                    text: modelData
                                    selected: dialog.backend.treeIndex === index
                                    onClicked: dialog.backend.selectTree(index)
                                }
                            }
                        }

                        Item {
                            y: dialog.backend.trees.length > 1 ? 32 : 4
                            width: parent.width
                            height: 26

                            SmoothRectangle {
                                x: 10
                                width: 24; height: 22; radius: 4
                                anchors.verticalCenter: parent.verticalCenter
                                color: treeUpMa.containsMouse && dialog.backend.treeCanUp ? dialog.c.b08 : dialog.c.b04
                                border.width: 1; border.color: dialog.c.b08
                                opacity: dialog.backend.treeCanUp ? 1.0 : 0.35
                                RakLabel {
                                    anchors.centerIn: parent
                                    text: ".."
                                    font.pixelSize: 10; font.family: "Consolas"; font.weight: Font.Bold
                                    color: dialog.c.FG
                                }
                                MouseArea {
                                    id: treeUpMa
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: dialog.backend.treeCanUp ? Qt.PointingHandCursor : Qt.ArrowCursor
                                    onClicked: dialog.backend.treeUp()
                                }
                            }

                            RakLabel {
                                x: 40
                                width: parent.width - 74
                                anchors.verticalCenter: parent.verticalCenter
                                text: dialog.backend.treeRelPath || "Project Tree"
                                font.pixelSize: 10; font.family: "Consolas"
                                color: dialog.c.FG2
                                elide: Text.ElideLeft
                            }

                            SmoothRectangle {
                                anchors.right: parent.right; anchors.rightMargin: 8
                                width: 22; height: 22; radius: 4
                                anchors.verticalCenter: parent.verticalCenter
                                color: refreshTreeMa.containsMouse ? dialog.c.b08 : "transparent"
                                RakLabel {
                                    anchors.centerIn: parent
                                    text: "↻"
                                    font.pixelSize: 11
                                    color: dialog.c.FG3
                                }
                                MouseArea {
                                    id: refreshTreeMa
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: dialog.backend.refreshTree()
                                }
                            }
                        }

                        SmoothRectangle { y: parent.height - 1; width: parent.width; height: 1; color: dialog.c.b06 }
                    }

                    // Draggable file/folder list
                    ListView {
                        id: treeList
                        y: treeTopBar.height
                        width: parent.width
                        height: parent.height - y
                        clip: true
                        topMargin: 6; bottomMargin: 6
                        spacing: 1
                        boundsBehavior: Flickable.StopAtBounds
                        model: dialog.backend.treeItems
                        C.ScrollBar.vertical: RakScrollBar { handleColor: dialog.c.BG_SCROLL }

                        delegate: SmoothRectangle {
                            id: treeRow
                            required property var modelData
                            x: 4
                            width: treeList.width - 8
                            height: 28
                            radius: 4
                            color: treeRowMa.containsMouse ? dialog.c.b06 : "transparent"

                            Item {
                                id: dragProxy
                                Drag.active: treeRowMa.drag.active
                                Drag.dragType: Drag.Automatic
                                Drag.supportedActions: Qt.CopyAction
                                Drag.mimeData: ({
                                    "application/x-rak-image": modelData.name + "\n" + modelData.path,
                                    "application/x-rak-tree": JSON.stringify({ paths: [modelData.path] }),
                                    "text/plain": modelData.name
                                })
                            }

                            Row {
                                x: 8
                                width: parent.width - 16
                                height: parent.height
                                spacing: 7

                                Image {
                                    width: 18; height: 18
                                    source: dialog.backend.fileIconUrl(modelData.iconKey || "file", dialog.paletteName)
                                    sourceSize: Qt.size(28, 28)
                                    fillMode: Image.PreserveAspectFit
                                    smooth: true; asynchronous: true; cache: true
                                    anchors.verticalCenter: parent.verticalCenter
                                }

                                RakLabel {
                                    width: parent.width - treeSizeLbl.implicitWidth - 32
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: modelData.name
                                    font.pixelSize: 12
                                    color: modelData.kind === "dir" ? dialog.c.FG : dialog.c.FG2
                                    elide: Text.ElideRight
                                }

                                RakLabel {
                                    id: treeSizeLbl
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: modelData.size
                                    font.pixelSize: 9
                                    color: dialog.c.FG3
                                }
                            }

                            MouseArea {
                                id: treeRowMa
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.OpenHandCursor
                                drag.target: dragProxy
                                drag.threshold: 6
                                onReleased: { dragProxy.x = 0; dragProxy.y = 0 }
                                onClicked: {
                                    if (modelData.kind === "dir")
                                        dialog.backend.treeOpenDir(modelData.path)
                                }
                                onDoubleClicked: {
                                    dialog.backend.stagePaths([modelData.path], "auto", "")
                                }
                            }
                        }

                        RakLabel {
                            visible: treeList.count === 0 && !dialog.backend.treeLoading
                            anchors.centerIn: parent
                            width: parent.width - 28
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                            font.pixelSize: 10
                            color: dialog.c.FG3
                            text: "No .img, .zip, or folders here.\nUse Import to browse other drives."
                        }
                    }
                }

                // Vertical divider between Tree and Staging Containers
                SmoothRectangle {
                    x: treeSidebar.width
                    width: 1
                    height: parent.height
                    color: dialog.c.b09
                }

                // ── RIGHT PANEL: COMPACT UNIFIED STAGING (Firmware Slots + SUPER) ──
                Item {
                    id: stagePanel
                    x: treeSidebar.width + 1
                    width: parent.width - x
                    height: parent.height

                    // Catch-all drop area for dragging .zip or multiple .img files anywhere on right panel
                    DropArea {
                        id: panelDrop
                        anchors.fill: parent
                        onEntered: event => { event.accepted = dialog.canAcceptDrop(event) }
                        onDropped: event => {
                            const paths = dialog.extractDropPaths(event)
                            if (paths.length > 0) {
                                dialog.backend.stagePaths(paths, "auto", "")
                                event.acceptProposedAction()
                            }
                        }
                    }

                    Flickable {
                        id: stageFlick
                        anchors.fill: parent
                        anchors.margins: 14
                        contentWidth: width
                        contentHeight: stageCol.implicitHeight + 8
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds
                        C.ScrollBar.vertical: RakScrollBar { handleColor: dialog.c.BG_SCROLL }

                        Column {
                            id: stageCol
                            width: stageFlick.width
                            spacing: 12

                            // Notice banner if probe warning occurred
                            SmoothRectangle {
                                visible: dialog.backend.stagingNotice.length > 0
                                width: parent.width
                                height: 28
                                radius: 6
                                color: "#1cf59e0b"
                                border.width: 1; border.color: "#45f59e0b"
                                Row {
                                    anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; spacing: 8
                                    WorkspaceNativeIcon {
                                        width: 13; height: 13; iconSize: 13
                                        iconKey: "status_warn"
                                        strokeColor: dialog.colWarn
                                        isLight: dialog.lightTheme
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                    RakLabel {
                                        text: dialog.backend.stagingNotice
                                        font.pixelSize: 10; color: dialog.colWarn
                                        elide: Text.ElideRight
                                        width: parent.width - 26
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                }
                            }

                            // ── SECTION 1: FIRMWARE PARTITIONS (Compact 2-column grid) ──
                            SmoothRectangle {
                                width: parent.width
                                height: fwSectionCol.implicitHeight + 18
                                radius: 8
                                color: dialog.c.b02
                                border.width: 1; border.color: dialog.c.b08

                                Column {
                                    id: fwSectionCol
                                    x: 12; y: 10
                                    width: parent.width - 24
                                    spacing: 8

                                    Item {
                                        width: parent.width; height: 18
                                        RakLabel {
                                            text: "FIRMWARE PARTITIONS"
                                            font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 0.7
                                            color: dialog.c.FG2
                                            anchors.verticalCenter: parent.verticalCenter
                                        }
                                        RakLabel {
                                            anchors.right: parent.right
                                            anchors.verticalCenter: parent.verticalCenter
                                            text: "Drag from tree or click slot"
                                            font.pixelSize: 9
                                            color: dialog.c.FG3
                                        }
                                    }

                                    Grid {
                                        id: fwGrid
                                        width: parent.width
                                        columns: 2
                                        columnSpacing: 8
                                        rowSpacing: 6

                                        Repeater {
                                            model: dialog.backend.firmwareSlots
                                            delegate: SmoothRectangle {
                                                required property var modelData
                                                width: Math.floor((fwGrid.width - 8) / 2)
                                                height: 40
                                                radius: 6
                                                color: slotDrop.containsDrag
                                                    ? dialog.c.a15
                                                    : (modelData.filled ? dialog.c.b04 : (slotMa.containsMouse ? dialog.c.b04 : dialog.c.b015))
                                                border.width: 1
                                                border.color: slotDrop.containsDrag
                                                    ? dialog.colAccent
                                                    : (modelData.filled
                                                        ? "#4522c55e"
                                                        : (modelData.required ? "#40f59e0b" : dialog.c.b06))

                                                DropArea {
                                                    id: slotDrop
                                                    anchors.fill: parent
                                                    onEntered: event => { event.accepted = dialog.canAcceptDrop(event) }
                                                    onDropped: event => {
                                                        const paths = dialog.extractDropPaths(event)
                                                        if (paths.length > 0) {
                                                            dialog.backend.stagePaths(paths, "firmware", modelData.key)
                                                            event.acceptProposedAction()
                                                        }
                                                    }
                                                }

                                                MouseArea {
                                                    id: slotMa
                                                    anchors.fill: parent
                                                    hoverEnabled: true
                                                    cursorShape: Qt.PointingHandCursor
                                                    onClicked: dialog.openTreePicker("slot", modelData.key)
                                                }

                                                WorkspaceNativeIcon {
                                                    x: 9
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    width: 14; height: 14; iconSize: 14
                                                    iconKey: modelData.filled ? "status_check" : (modelData.required ? "status_warn" : "status_check")
                                                    strokeColor: modelData.filled
                                                        ? dialog.colOk
                                                        : (modelData.required ? dialog.colWarn : dialog.c.FG3)
                                                    opacity: modelData.filled ? 1.0 : (modelData.required ? 0.85 : 0.30)
                                                    isLight: dialog.lightTheme
                                                }

                                                Column {
                                                    x: 30
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    width: parent.width - (modelData.filled ? 56 : 38)
                                                    spacing: 1
                                                    RakLabel {
                                                        width: parent.width
                                                        text: modelData.label
                                                        font.pixelSize: 11; font.weight: Font.DemiBold
                                                        color: modelData.filled ? dialog.c.FG : dialog.c.FG2
                                                        elide: Text.ElideRight
                                                    }
                                                    RakLabel {
                                                        width: parent.width
                                                        text: modelData.filled
                                                            ? (modelData.fileName + (modelData.sizeLabel ? " · " + modelData.sizeLabel : ""))
                                                            : modelData.role
                                                        font.pixelSize: 9
                                                        color: modelData.filled ? dialog.colOk : dialog.c.FG3
                                                        elide: Text.ElideMiddle
                                                    }
                                                }

                                                Item {
                                                    visible: modelData.filled
                                                    width: 22; height: 22
                                                    anchors.right: parent.right; anchors.rightMargin: 4
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    WorkspaceNativeIcon {
                                                        anchors.centerIn: parent
                                                        width: 10; height: 10; iconSize: 10
                                                        iconKey: "close"
                                                        strokeColor: clearSlotMa.containsMouse ? dialog.colErr : dialog.c.FG3
                                                        isLight: dialog.lightTheme
                                                    }
                                                    MouseArea {
                                                        id: clearSlotMa
                                                        anchors.fill: parent
                                                        hoverEnabled: true
                                                        cursorShape: Qt.PointingHandCursor
                                                        onClicked: mouse => {
                                                            mouse.accepted = true
                                                            dialog.backend.clearFirmwareSlot(modelData.key)
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            // ── SECTION 2: COMPACT SUPER CONTAINER ──
                            SmoothRectangle {
                                id: superBox
                                width: parent.width
                                height: Math.max(126, superInnerCol.implicitHeight + 22)
                                radius: 8
                                color: superDrop.containsDrag ? dialog.c.a10 : dialog.c.b02
                                border.width: superDrop.containsDrag ? 2 : 1
                                border.color: superDrop.containsDrag
                                    ? dialog.colAccent
                                    : (dialog.backend.canBoot ? "#4522c55e" : dialog.c.b08)

                                DropArea {
                                    id: superDrop
                                    anchors.fill: parent
                                    onEntered: event => { event.accepted = dialog.canAcceptDrop(event) }
                                    onDropped: event => {
                                        const paths = dialog.extractDropPaths(event)
                                        if (paths.length > 0) {
                                            dialog.backend.stagePaths(paths, "super", "")
                                            event.acceptProposedAction()
                                        }
                                    }
                                }

                                Column {
                                    id: superInnerCol
                                    x: 12; y: 10
                                    width: parent.width - 24
                                    spacing: 10

                                    // Compact SUPER header
                                    Item {
                                        width: parent.width
                                        height: 24

                                        Row {
                                            height: parent.height
                                            spacing: 7
                                            WorkspaceNativeIcon {
                                                width: 14; height: 14; iconSize: 14
                                                iconKey: dialog.backend.canBoot ? "status_check" : "status_warn"
                                                strokeColor: dialog.backend.canBoot ? dialog.colOk : dialog.colWarn
                                                isLight: dialog.lightTheme
                                                anchors.verticalCenter: parent.verticalCenter
                                            }
                                            RakLabel {
                                                text: dialog.backend.superLabel
                                                    ? ("SUPER · " + dialog.backend.superLabel)
                                                    : (dialog.backend.zipLabel
                                                        ? ("SUPER · " + dialog.backend.zipLabel)
                                                        : "SUPER")
                                                font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 0.7
                                                color: dialog.c.FG
                                                anchors.verticalCenter: parent.verticalCenter
                                            }
                                            RakLabel {
                                                visible: dialog.backend.superPartitionCount > 0
                                                text: "(" + dialog.backend.superPartitionCount + ")"
                                                font.pixelSize: 10
                                                color: dialog.colOk
                                                anchors.verticalCenter: parent.verticalCenter
                                            }
                                        }

                                        Row {
                                            anchors.right: parent.right
                                            anchors.verticalCenter: parent.verticalCenter
                                            spacing: 6
                                            AndroidToolButton {
                                                height: 22
                                                colors: dialog.c
                                                text: "+ Add"
                                                enabled: !dialog.backend.stagingBusy
                                                onClicked: dialog.openTreePicker("super", "")
                                            }
                                            AndroidToolButton {
                                                height: 22
                                                colors: dialog.c
                                                text: "Clear"
                                                visible: dialog.backend.canBoot || dialog.backend.firmwareStagedCount > 0
                                                enabled: !dialog.backend.stagingBusy
                                                onClicked: dialog.backend.clearAllStaged()
                                            }
                                        }
                                    }

                                    // Compact empty drop hint (standard AOSP — no tr_* clutter)
                                    SmoothRectangle {
                                        visible: dialog.backend.superPartitionCount === 0
                                        width: parent.width
                                        height: 76
                                        radius: 6
                                        color: dialog.c.b015
                                        border.width: 1; border.color: dialog.c.b06

                                        Column {
                                            anchors.centerIn: parent
                                            width: parent.width - 28
                                            spacing: 4
                                            RakLabel {
                                                width: parent.width
                                                horizontalAlignment: Text.AlignHCenter
                                                text: "Drop super.img, logical partitions, or firmware .zip here"
                                                font.pixelSize: 11; font.weight: Font.Medium
                                                color: dialog.c.FG2
                                            }
                                            RakLabel {
                                                width: parent.width
                                                horizontalAlignment: Text.AlignHCenter
                                                text: "system · vendor · product · system_ext · odm · dlkm"
                                                font.pixelSize: 10; font.family: "Consolas"
                                                color: dialog.c.FG3
                                            }
                                        }
                                    }

                                    // Compact staged logical partition chips inside SUPER
                                    Flow {
                                        visible: dialog.backend.superPartitionCount > 0
                                        width: parent.width
                                        spacing: 6

                                        Repeater {
                                            model: dialog.backend.superPartitions
                                            delegate: SmoothRectangle {
                                                required property var modelData
                                                width: chipRow.implicitWidth + 16
                                                height: 28
                                                radius: 6
                                                color: dialog.c.b04
                                                border.width: 1
                                                border.color: modelData.core ? "#3522c55e" : dialog.c.b08

                                                Row {
                                                    id: chipRow
                                                    anchors.centerIn: parent
                                                    spacing: 6

                                                    WorkspaceNativeIcon {
                                                        width: 12; height: 12; iconSize: 12
                                                        iconKey: "status_check"
                                                        strokeColor: dialog.colOk
                                                        isLight: dialog.lightTheme
                                                        anchors.verticalCenter: parent.verticalCenter
                                                    }
                                                    RakLabel {
                                                        text: modelData.name
                                                        font.pixelSize: 11; font.weight: Font.Medium
                                                        color: dialog.c.FG
                                                        anchors.verticalCenter: parent.verticalCenter
                                                    }
                                                    RakLabel {
                                                        visible: modelData.sizeLabel.length > 0
                                                        text: modelData.sizeLabel
                                                        font.pixelSize: 9
                                                        color: dialog.c.FG3
                                                        anchors.verticalCenter: parent.verticalCenter
                                                    }
                                                    Item {
                                                        visible: modelData.removable
                                                        width: 14; height: 14
                                                        anchors.verticalCenter: parent.verticalCenter
                                                        WorkspaceNativeIcon {
                                                            anchors.centerIn: parent
                                                            width: 9; height: 9; iconSize: 9
                                                            iconKey: "close"
                                                            strokeColor: rmChipMa.containsMouse ? dialog.colErr : dialog.c.FG3
                                                            isLight: dialog.lightTheme
                                                        }
                                                        MouseArea {
                                                            id: rmChipMa
                                                            anchors.fill: parent
                                                            hoverEnabled: true
                                                            cursorShape: Qt.PointingHandCursor
                                                            onClicked: dialog.backend.removeSuperPartition(modelData.name)
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // =================================================================
            // PAGE 1: LIVE BOOT (Top Boot Chain + Left Virtual FS + Right Logcat)
            // =================================================================
            Item {
                anchors.fill: parent
                visible: dialog.page === 1

                // Compact 6-Phase Boot Chain Strip (Pure QML Symbols)
                SmoothRectangle {
                    id: phaseStrip
                    x: 12; y: 8
                    width: parent.width - 24
                    height: dialog.backend.phases.length > 0 ? 32 : 0
                    visible: dialog.backend.phases.length > 0
                    radius: 7
                    color: dialog.c.b02
                    border.width: 1; border.color: dialog.c.b08

                    Row {
                        anchors.fill: parent
                        anchors.leftMargin: 8; anchors.rightMargin: 8
                        spacing: 6
                        Repeater {
                            model: dialog.backend.phases
                            delegate: Item {
                                required property var modelData
                                width: Math.floor((phaseStrip.width - 16 - 5 * 6) / Math.max(1, dialog.backend.phases.length))
                                height: parent.height
                                Row {
                                    anchors.fill: parent
                                    spacing: 5
                                    WorkspaceNativeIcon {
                                        width: 13; height: 13; iconSize: 13
                                        iconKey: modelData.symbol
                                        strokeColor: dialog.statusColor(modelData.status)
                                        isLight: dialog.lightTheme
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                    RakLabel {
                                        width: parent.width - 20
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: modelData.name.replace(/^\d+\.\s*/, "")
                                        font.pixelSize: 10; font.weight: Font.Medium
                                        color: dialog.c.FG2
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }
                }

                // Unified Kernel-Contract Hardware Gate Bar + Post-Boot ▶ Play Button (hidden while QEMU is disabled)
                SmoothRectangle {
                    id: hwGateBar
                    x: 12
                    y: phaseStrip.visible ? 44 : 8
                    width: parent.width - 24
                    height: visible ? 34 : 0
                    visible: dialog.backend.qemuFeatureEnabled && (dialog.backend.hasReport || dialog.backend.qemuRunning || dialog.backend.qemuPreparing)
                    radius: 7
                    color: dialog.c.b02
                    border.width: 1
                    border.color: dialog.backend.qemuHardwareGateFaults > 0 ? dialog.colErr : dialog.c.b08

                    Row {
                        x: 10
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 6

                        RakLabel {
                            text: "HW CONTRACT"
                            font.pixelSize: 9; font.weight: Font.Bold; font.letterSpacing: 0.6
                            color: dialog.c.FG3
                            anchors.verticalCenter: parent.verticalCenter
                        }

                        Repeater {
                            model: dialog.backend.qemuHardwareGates
                            delegate: SmoothRectangle {
                                required property var modelData
                                readonly property string st: String(modelData.status || "generic").toLowerCase()
                                readonly property color pillCol: st === "fault"
                                    ? dialog.colErr
                                    : (st === "warn" ? dialog.colWarn : (st === "pass" ? dialog.colOk : dialog.colAccent))
                                width: gateRow.implicitWidth + 12
                                height: 20
                                radius: 4
                                color: st === "fault" ? dialog.c.b06 : dialog.c.b03
                                border.width: 1
                                border.color: pillCol

                                Row {
                                    id: gateRow
                                    anchors.centerIn: parent
                                    spacing: 4
                                    SmoothRectangle {
                                        width: 6; height: 6; radius: 3
                                        color: pillCol
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                    RakLabel {
                                        text: modelData.shortLabel || modelData.label || ""
                                        font.pixelSize: 9; font.family: "Consolas"; font.weight: Font.Bold
                                        color: pillCol
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                }
                                MouseArea {
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    onEntered: dialog.hoveredGateReason = (modelData.label || "") + ": " + (modelData.reason || "")
                                    onExited: dialog.hoveredGateReason = ""
                                }
                            }
                        }
                    }

                    Row {
                        anchors.right: parent.right
                        anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 5

                        Repeater {
                            model: [
                                { key: "auto", label: "Auto" },
                                { key: "arm64", label: "64" },
                                { key: "arm64_32", label: "64_32" },
                                { key: "arm32", label: "32" }
                            ]
                            delegate: AndroidToolButton {
                                required property var modelData
                                height: 22; colors: dialog.c
                                text: modelData.label
                                selected: dialog.backend.qemuAbi === modelData.key
                                enabled: !dialog.backend.qemuRunning && !dialog.backend.qemuPreparing
                                onClicked: dialog.backend.setQemuAbi(modelData.key)
                            }
                        }

                        AndroidToolButton {
                            height: 22; colors: dialog.c
                            text: dialog.showLivePhone ? "Hide Screen" : "Screen"
                            visible: dialog.backend.qemuHasLog || dialog.backend.qemuRunning || dialog.backend.qemuPreparing
                            onClicked: dialog.showLivePhone = !dialog.showLivePhone
                        }

                        SmoothRectangle {
                            width: expLbl.implicitWidth + 10
                            height: 20
                            radius: 4
                            anchors.verticalCenter: parent.verticalCenter
                            color: dialog.c.b04
                            border.width: 1
                            border.color: dialog.colWarn

                            RakLabel {
                                id: expLbl
                                anchors.centerIn: parent
                                text: "EXPERIMENTAL"
                                font.pixelSize: 8; font.family: "Consolas"; font.weight: Font.Bold; font.letterSpacing: 0.4
                                color: dialog.colWarn
                            }
                            MouseArea {
                                anchors.fill: parent
                                hoverEnabled: true
                                onEntered: dialog.hoveredGateReason = "Experimental QEMU Live Boot: executes real kernel/init/SELinux/SurfaceFlinger under software TCG (slow on Windows; OEM SELinux/TEE/HAL differences may prevent full GUI rendering). Static Boot Analyzer remains instant for all ROMs."
                                onExited: dialog.hoveredGateReason = ""
                            }
                        }

                        AndroidToolButton {
                            height: 24; colors: dialog.c
                            selected: true
                            text: dialog.backend.qemuRunning
                                ? "■ Stop"
                                : (dialog.backend.qemuPreparing ? "Prep…" : "▶ Play")
                            enabled: dialog.backend.qemuRunning || (dialog.backend.hasReport && !dialog.backend.running && !dialog.backend.qemuPreparing)
                            onClicked: {
                                if (dialog.backend.qemuRunning) {
                                    dialog.backend.stopQemuBoot()
                                } else {
                                    dialog.showLivePhone = true
                                    dialog.backend.startQemuBoot()
                                }
                            }
                        }
                    }
                }

                // Split Area: Left Virtual Mounted FS | Right Live Logcat
                Item {
                    id: liveBootSplit
                    readonly property bool phoneOpen: dialog.backend.qemuFeatureEnabled && (dialog.showLivePhone || dialog.backend.qemuRunning || dialog.backend.qemuPreparing)
                    readonly property int phoneW: phoneOpen ? Math.min(280, Math.max(230, Math.floor(width * 0.28))) : 0
                    x: 12
                    y: hwGateBar.visible ? (hwGateBar.y + hwGateBar.height + 6) : (phaseStrip.visible ? 46 : 8)
                    width: parent.width - 24
                    height: parent.height - y - 8

                    // ── LEFT PANE: VIRTUAL MOUNTED FILESYSTEM (Native WorkspaceTree Style & Icons) ──
                    SmoothRectangle {
                        id: vfsPane
                        width: liveBootSplit.phoneOpen
                            ? Math.max(200, Math.min(250, Math.floor(parent.width * 0.24)))
                            : Math.max(255, Math.min(370, Math.floor(parent.width * 0.35)))
                        height: parent.height
                        radius: 8
                        color: dialog.c.BG3
                        border.width: 1; border.color: dialog.c.b08
                        clip: true

                        // Native WorkspaceTree-style expandable tree view
                        ListView {
                            id: vfsList
                            y: 4
                            width: parent.width
                            height: parent.height - y - (vfsPreviewBox.visible ? vfsPreviewBox.height : 0)
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds
                            model: dialog.backend.vfsEntries
                            C.ScrollBar.vertical: RakScrollBar { handleColor: dialog.c.BG_SCROLL }

                            delegate: Item {
                                id: vfsRow
                                required property var modelData
                                readonly property int rowDepth: modelData.depth || 0
                                readonly property bool isSel: dialog.backend.vfsSelectedPath === modelData.vpath
                                width: vfsList.width
                                height: 28

                                // Selection / hover highlight matching WorkspaceTree
                                SmoothRectangle {
                                    x: 2; y: 0; width: parent.width - 4; height: 28; radius: 2
                                    color: vfsRow.isSel
                                        ? dialog.c.a15
                                        : (vfsItemMa.containsMouse ? dialog.c.b06 : "transparent")
                                }

                                // Exact WorkspaceEntry branch connector lines (20px per depth)
                                Item {
                                    width: (vfsRow.rowDepth + 1) * 20
                                    height: 28
                                    Repeater {
                                        model: modelData.branchLines || []
                                        delegate: SmoothRectangle {
                                            required property int index
                                            required property bool modelData
                                            visible: modelData
                                            x: index * 20 + 10; y: 0; width: 1; height: 28
                                            color: dialog.c.b18
                                        }
                                    }
                                    SmoothRectangle {
                                        visible: vfsRow.rowDepth > 0
                                        x: (vfsRow.rowDepth - 1) * 20 + 10; y: 0; width: 1
                                        height: modelData.lastSibling ? 14 : 28
                                        color: dialog.c.b18
                                    }
                                    SmoothRectangle {
                                        visible: vfsRow.rowDepth > 0
                                        x: (vfsRow.rowDepth - 1) * 20 + 10; y: 14; width: 10; height: 1
                                        color: dialog.c.b18
                                    }
                                }

                                // Exact WorkspaceTree branch arrow chevron
                                Image {
                                    visible: modelData.kind === "dir"
                                    x: 4 + vfsRow.rowDepth * 20; y: 8; width: 12; height: 12
                                    source: "image://icons/arrow_right?color=" + encodeURIComponent(dialog.c.treeArrow)
                                    sourceSize: Qt.size(24, 24); smooth: true; cache: true
                                    opacity: 0.8
                                    rotation: modelData.expanded ? 90 : 0
                                }

                                // Exact WorkspaceEntry native file/folder/mount/apk/so/xml icon
                                Image {
                                    x: 18 + vfsRow.rowDepth * 20; y: 4; width: 20; height: 20
                                    source: dialog.backend.fileIconUrl(modelData.iconKey || "file", dialog.paletteName)
                                    sourceSize: Qt.size(28, 28)
                                    fillMode: Image.PreserveAspectFit
                                    smooth: true; asynchronous: true; cache: true
                                }

                                // Label + symlink target
                                RakLabel {
                                    x: 42 + vfsRow.rowDepth * 20
                                    width: Math.max(40, parent.width - x - vfsMetaRow.width - 10)
                                    height: 28
                                    text: modelData.name + (modelData.isLink && modelData.linkTarget ? (" -> " + modelData.linkTarget) : "")
                                    font.pixelSize: 12
                                    color: modelData.isMount
                                        ? dialog.c.FG
                                        : (modelData.kind === "dir" ? dialog.c.FG : dialog.c.FG2)
                                    elide: Text.ElideRight
                                }

                                // Right-aligned mount badge or size label
                                Row {
                                    id: vfsMetaRow
                                    anchors.right: parent.right; anchors.rightMargin: 8
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 4

                                    SmoothRectangle {
                                        visible: !!(modelData.badge && modelData.badge.length > 0)
                                        width: badgeTxt.implicitWidth + 10; height: 16; radius: 3
                                        anchors.verticalCenter: parent.verticalCenter
                                        color: dialog.c.a10
                                        border.width: 1; border.color: dialog.c.a25
                                        RakLabel {
                                            id: badgeTxt
                                            anchors.centerIn: parent
                                            text: modelData.badge || ""
                                            font.pixelSize: 9; font.weight: Font.Bold
                                            color: dialog.colAccent
                                        }
                                    }

                                    RakLabel {
                                        visible: !!(modelData.sizeLabel && modelData.sizeLabel.length > 0)
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: modelData.sizeLabel || ""
                                        font.pixelSize: 10
                                        color: dialog.c.FG3
                                    }
                                }

                                MouseArea {
                                    id: vfsItemMa
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        if (modelData.kind === "dir")
                                            dialog.backend.vfsToggle(modelData.vpath)
                                        else
                                            dialog.backend.vfsOpen(modelData.vpath)
                                    }
                                }
                            }
                        }

                        RakLabel {
                            visible: !dialog.backend.vfsReady
                            anchors.centerIn: parent
                            width: parent.width - 28
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                            font.pixelSize: 11
                            color: dialog.c.FG3
                            text: dialog.backend.running
                                ? "Mounting virtual partitions…"
                                : "Stage partitions and click Boot to mount virtual filesystem."
                        }

                        // Inline file preview drawer
                        SmoothRectangle {
                            id: vfsPreviewBox
                            visible: dialog.backend.vfsPreviewPath.length > 0
                            anchors.bottom: parent.bottom
                            width: parent.width
                            height: Math.min(190, parent.height * 0.45)
                            color: dialog.c.BG2
                            border.width: 1; border.color: dialog.c.b10

                            Item {
                                width: parent.width; height: 24
                                RakLabel {
                                    x: 8; width: parent.width - 36; height: 24
                                    text: dialog.backend.vfsPreviewPath
                                    font.pixelSize: 10; font.family: "Consolas"; font.weight: Font.DemiBold
                                    color: dialog.colAccent
                                    elide: Text.ElideMiddle
                                }
                                Item {
                                    width: 22; height: 22
                                    anchors.right: parent.right; anchors.rightMargin: 2; anchors.verticalCenter: parent.verticalCenter
                                    WorkspaceNativeIcon {
                                        anchors.centerIn: parent; width: 10; height: 10; iconSize: 10
                                        iconKey: "close"; strokeColor: dialog.c.FG3; isLight: dialog.lightTheme
                                    }
                                    MouseArea {
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: dialog.backend.vfsClosePreview()
                                    }
                                }
                                SmoothRectangle { y: 23; width: parent.width; height: 1; color: dialog.c.b08 }
                            }
                            Flickable {
                                x: 8; y: 26
                                width: parent.width - 16
                                height: parent.height - 30
                                contentWidth: width
                                contentHeight: previewEdit.implicitHeight + 8
                                clip: true
                                TextEdit {
                                    id: previewEdit
                                    width: parent.width
                                    readOnly: true
                                    selectByMouse: true
                                    wrapMode: TextEdit.WrapAnywhere
                                    font.pixelSize: 10; font.family: "Consolas"
                                    color: dialog.c.FG2
                                    text: dialog.backend.vfsPreviewText
                                }
                            }
                        }
                    }

                    // ── CENTER PANE: LIVE ANDROID LOGCAT (+ UNIFIED QEMU STREAM) ──
                    SmoothRectangle {
                        id: logcatPane
                        x: vfsPane.width + 10
                        width: Math.max(220, parent.width - vfsPane.width - 10 - (liveBootSplit.phoneOpen ? (liveBootSplit.phoneW + 10) : 0))
                        height: parent.height
                        radius: 8
                        color: dialog.c.b02
                        border.width: 1; border.color: dialog.c.b08
                        clip: true

                        Item {
                            id: logcatBar
                            width: parent.width
                            height: 34

                            Row {
                                x: 10
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 5

                                RakLabel {
                                    text: "LOGCAT"
                                    font.pixelSize: 10; font.weight: Font.Bold; font.letterSpacing: 0.7
                                    color: dialog.c.FG2
                                    anchors.verticalCenter: parent.verticalCenter
                                    rightPadding: 4
                                }

                                Repeater {
                                    model: [
                                        { key: "all", sym: "", label: "All", col: dialog.colAccent },
                                        { key: "I", sym: "status_check", label: "I", col: dialog.colOk },
                                        { key: "W", sym: "status_warn", label: "W", col: dialog.colWarn },
                                        { key: "E", sym: "status_error", label: "E", col: dialog.colErr }
                                    ]
                                    delegate: SmoothRectangle {
                                        required property var modelData
                                        readonly property bool active: dialog.backend.logcatLevelFilter === modelData.key
                                        width: pillRow.implicitWidth + 14
                                        height: 22
                                        radius: 5
                                        color: active ? dialog.c.a15 : (fMa.containsMouse ? dialog.c.b06 : dialog.c.b02)
                                        border.width: 1
                                        border.color: active ? dialog.c.a25 : dialog.c.b08

                                        Row {
                                            id: pillRow
                                            anchors.centerIn: parent
                                            spacing: 4
                                            WorkspaceNativeIcon {
                                                visible: modelData.sym.length > 0
                                                width: 11; height: 11; iconSize: 11
                                                iconKey: modelData.sym || "status_check"
                                                strokeColor: modelData.col
                                                isLight: dialog.lightTheme
                                                anchors.verticalCenter: parent.verticalCenter
                                            }
                                            RakLabel {
                                                text: modelData.label
                                                font.pixelSize: 10; font.weight: Font.DemiBold
                                                color: active ? dialog.c.FG : dialog.c.FG2
                                                anchors.verticalCenter: parent.verticalCenter
                                            }
                                        }
                                        MouseArea {
                                            id: fMa
                                            anchors.fill: parent
                                            hoverEnabled: true
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: dialog.backend.setLogcatLevelFilter(modelData.key)
                                        }
                                    }
                                }

                                SmoothRectangle {
                                    id: verbosePill
                                    readonly property bool active: dialog.backend.logcatVerbose
                                    width: verboseLbl.implicitWidth + 14
                                    height: 22
                                    radius: 5
                                    color: active ? dialog.c.a15 : (verboseMa.containsMouse ? dialog.c.b06 : dialog.c.b02)
                                    border.width: 1
                                    border.color: active ? dialog.c.a25 : dialog.c.b08
                                    RakLabel {
                                        id: verboseLbl
                                        anchors.centerIn: parent
                                        text: "Verbose"
                                        font.pixelSize: 10; font.weight: Font.DemiBold
                                        color: verbosePill.active ? dialog.colAccent : dialog.c.FG2
                                    }
                                    MouseArea {
                                        id: verboseMa
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: dialog.backend.setLogcatVerbose(!dialog.backend.logcatVerbose)
                                    }
                                }
                            }

                            Row {
                                anchors.right: parent.right
                                anchors.rightMargin: 8
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: 6

                                SmoothRectangle {
                                    width: Math.max(110, Math.min(185, logcatPane.width * 0.26))
                                    height: 24
                                    radius: 5
                                    color: dialog.c.BG_INPUT
                                    border.width: 1; border.color: logSearch.activeFocus ? dialog.colAccent : dialog.c.b08
                                    TextInput {
                                        id: logSearch
                                        anchors.fill: parent
                                        anchors.leftMargin: 8; anchors.rightMargin: 8
                                        verticalAlignment: TextInput.AlignVCenter
                                        font.pixelSize: 10
                                        color: dialog.c.FG
                                        selectionColor: dialog.colAccent
                                        clip: true
                                        onTextChanged: dialog.backend.setLogcatQuery(text)
                                        RakLabel {
                                            anchors.fill: parent
                                            text: "Filter logcat…"
                                            font.pixelSize: 10
                                            color: dialog.c.FG3
                                            visible: !logSearch.text
                                        }
                                    }
                                }

                                AndroidToolButton {
                                    height: 24
                                    colors: dialog.c
                                    text: "Copy"
                                    enabled: dialog.backend.logcatCount > 0
                                    onClicked: dialog.backend.copyLogcat()
                                }
                            }

                            SmoothRectangle { y: parent.height - 1; width: parent.width; height: 1; color: dialog.c.b06 }
                        }

                        ListView {
                            id: logcatList
                            y: 34
                            width: parent.width
                            height: parent.height - 34
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds
                            model: dialog.backend.logcatLines
                            onMovementStarted: dialog.autoScrollLogcat = false
                            C.ScrollBar.vertical: RakScrollBar { handleColor: dialog.c.BG_SCROLL }

                            delegate: Item {
                                required property int index
                                required property var modelData
                                readonly property bool expanded: dialog.expandedLogIndex === index
                                readonly property bool hasExtra: (modelData.detail && modelData.detail.length > 0) || (modelData.path && modelData.path.length > 0)
                                width: logcatList.width
                                height: rowCol.implicitHeight + 8

                                SmoothRectangle {
                                    anchors.fill: parent
                                    color: (modelData.level === "E" || modelData.level === "F")
                                        ? "#12ef4444"
                                        : (modelData.level === "W" ? "#10f59e0b" : (logRowMa.containsMouse ? dialog.c.b04 : "transparent"))
                                }

                                Column {
                                    id: rowCol
                                    x: 10; y: 4
                                    width: parent.width - 20
                                    spacing: 3

                                    Row {
                                        width: parent.width
                                        spacing: 6

                                        WorkspaceNativeIcon {
                                            width: 12; height: 12; iconSize: 12
                                            iconKey: dialog.statusSymbol(modelData.level)
                                            strokeColor: dialog.logLevelColor(modelData.level)
                                            isLight: dialog.lightTheme
                                            y: 2
                                        }

                                        RakLabel {
                                            text: modelData.ts
                                            font.pixelSize: 10; font.family: "Consolas"
                                            color: dialog.c.FG3
                                        }

                                        SmoothRectangle {
                                            width: 16; height: 15; radius: 3
                                            color: (modelData.level === "E" || modelData.level === "F")
                                                ? "#28ef4444"
                                                : (modelData.level === "W" ? "#28f59e0b" : dialog.c.a10)
                                            RakLabel {
                                                anchors.centerIn: parent
                                                text: modelData.level
                                                font.pixelSize: 10; font.family: "Consolas"; font.weight: Font.Bold
                                                color: dialog.logLevelColor(modelData.level)
                                            }
                                        }

                                        RakLabel {
                                            text: modelData.tag + ":"
                                            font.pixelSize: 10; font.family: "Consolas"; font.weight: Font.DemiBold
                                            color: dialog.logLevelColor(modelData.level)
                                        }

                                        RakLabel {
                                            width: Math.max(80, parent.width - 135 - Math.min(130, (modelData.tag.length + 2) * 7))
                                            text: modelData.msg
                                            font.pixelSize: 10; font.family: "Consolas"
                                            color: (modelData.level === "E" || modelData.level === "F") ? dialog.colErr : dialog.c.FG
                                            wrapMode: expanded ? Text.WrapAnywhere : Text.NoWrap
                                            elide: expanded ? Text.ElideNone : Text.ElideRight
                                        }
                                    }

                                    SmoothRectangle {
                                        visible: expanded && hasExtra
                                        width: parent.width - 18
                                        x: 18
                                        height: detailCol.implicitHeight + 12
                                        radius: 6
                                        color: dialog.c.b04
                                        border.width: 1; border.color: dialog.c.b08

                                        Column {
                                            id: detailCol
                                            x: 10; y: 6
                                            width: parent.width - 20
                                            spacing: 4
                                            RakLabel {
                                                visible: modelData.path && modelData.path.length > 0
                                                width: parent.width
                                                text: "Open in Virtual FS: " + modelData.path
                                                font.pixelSize: 10; font.family: "Consolas"; font.underline: true
                                                color: dialog.colAccent
                                                elide: Text.ElideMiddle
                                                MouseArea {
                                                    anchors.fill: parent
                                                    cursorShape: Qt.PointingHandCursor
                                                    onClicked: dialog.backend.vfsOpen(modelData.path)
                                                }
                                            }
                                            RakLabel {
                                                visible: modelData.detail && modelData.detail.length > 0
                                                width: parent.width
                                                text: modelData.detail
                                                font.pixelSize: 10; font.family: "Consolas"
                                                color: dialog.c.FG2
                                                wrapMode: Text.WrapAnywhere
                                            }
                                        }
                                    }
                                }

                                MouseArea {
                                    id: logRowMa
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: hasExtra ? Qt.PointingHandCursor : Qt.ArrowCursor
                                    onClicked: {
                                        if (hasExtra)
                                            dialog.expandedLogIndex = expanded ? -1 : index
                                    }
                                }
                            }
                        }

                        RakLabel {
                            visible: dialog.backend.logcatCount === 0
                            anchors.centerIn: parent
                            font.pixelSize: 11; font.family: "Consolas"
                            color: dialog.c.FG3
                            text: "Waiting for virtual boot logcat…"
                        }
                    }

                    // ── RIGHT PANE: UNIFIED EMBEDDED VIRTUAL PHONE SCREEN (opens on ▶ Play) ──
                    SmoothRectangle {
                        id: livePhonePane
                        visible: liveBootSplit.phoneOpen
                        x: logcatPane.x + logcatPane.width + 10
                        width: liveBootSplit.phoneW
                        height: parent.height
                        radius: 10
                        color: dialog.c.b02
                        border.width: 1
                        border.color: dialog.backend.qemuRunning ? dialog.c.a25 : dialog.c.b08
                        clip: true

                        Item {
                            id: livePhoneHdr
                            width: parent.width; height: 28
                            Row {
                                x: 8; anchors.verticalCenter: parent.verticalCenter; spacing: 5
                                SmoothRectangle {
                                    width: 7; height: 7; radius: 4
                                    anchors.verticalCenter: parent.verticalCenter
                                    color: dialog.backend.qemuRunning ? dialog.colOk : (dialog.backend.qemuPreparing ? dialog.colWarn : dialog.c.FG3)
                                }
                                RakLabel {
                                    text: dialog.backend.qemuHasLiveGpu ? "GPU LIVE" : (dialog.backend.qemuRunning ? "BOOTING" : "VIRTUAL DEVICE")
                                    font.pixelSize: 9; font.weight: Font.Bold; font.letterSpacing: 0.5
                                    color: dialog.c.FG
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                                SmoothRectangle {
                                    width: phoneExpLbl.implicitWidth + 8
                                    height: 15
                                    radius: 3
                                    anchors.verticalCenter: parent.verticalCenter
                                    color: dialog.c.b04
                                    border.width: 1
                                    border.color: dialog.colWarn
                                    RakLabel {
                                        id: phoneExpLbl
                                        anchors.centerIn: parent
                                        text: "EXP"
                                        font.pixelSize: 7; font.family: "Consolas"; font.weight: Font.Bold
                                        color: dialog.colWarn
                                    }
                                }
                            }
                            RakLabel {
                                anchors.right: parent.right; anchors.rightMargin: 8
                                anchors.verticalCenter: parent.verticalCenter
                                text: dialog.backend.qemuAbi.toUpperCase()
                                font.pixelSize: 9; font.family: "Consolas"; font.weight: Font.Bold
                                color: dialog.colAccent
                            }
                            SmoothRectangle { y: parent.height - 1; width: parent.width; height: 1; color: dialog.c.b06 }
                        }

                        // Embedded Interactive Screen (virtio-gpu + virtio-tablet touch)
                        SmoothRectangle {
                            id: liveScreenFrame
                            x: 8; y: 34
                            width: parent.width - 16
                            height: Math.max(120, parent.height - 96)
                            radius: 8
                            color: "#090D14"
                            border.width: 1; border.color: dialog.c.b08
                            clip: true

                            Image {
                                id: liveScreenImg
                                anchors.fill: parent
                                anchors.margins: 2
                                fillMode: Image.PreserveAspectFit
                                smooth: true
                                cache: false
                                asynchronous: false
                                source: "image://qemu_screen/frame?" + dialog.backend.qemuFrameSeq
                            }

                            MouseArea {
                                anchors.fill: liveScreenImg
                                cursorShape: dialog.backend.qemuRunning ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onPressed: function(ev) {
                                    if (liveScreenImg.width > 0 && liveScreenImg.height > 0)
                                        dialog.backend.qemuTouch(ev.x / liveScreenImg.width, ev.y / liveScreenImg.height, true)
                                }
                                onPositionChanged: function(ev) {
                                    if (pressed && liveScreenImg.width > 0 && liveScreenImg.height > 0)
                                        dialog.backend.qemuTouch(ev.x / liveScreenImg.width, ev.y / liveScreenImg.height, true)
                                }
                                onReleased: function(ev) {
                                    if (liveScreenImg.width > 0 && liveScreenImg.height > 0)
                                        dialog.backend.qemuTouch(ev.x / liveScreenImg.width, ev.y / liveScreenImg.height, false)
                                }
                            }
                        }

                        // Compact status line under screen
                        RakLabel {
                            x: 8; y: liveScreenFrame.y + liveScreenFrame.height + 4
                            width: parent.width - 16; height: 18
                            verticalAlignment: Text.AlignVCenter
                            horizontalAlignment: Text.AlignHCenter
                            text: dialog.backend.qemuStatus || "Ready"
                            font.pixelSize: 9; font.family: "Consolas"
                            color: dialog.c.FG2
                            elide: Text.ElideRight
                        }

                        // Android Hardware Nav + Snap + Log→Editor
                        Row {
                            anchors.horizontalCenter: parent.horizontalCenter
                            anchors.bottom: parent.bottom; anchors.bottomMargin: 6
                            spacing: 4

                            AndroidToolButton {
                                height: 22; colors: dialog.c; text: "◀"
                                enabled: dialog.backend.qemuRunning
                                onClicked: dialog.backend.qemuKey("back")
                            }
                            AndroidToolButton {
                                height: 22; colors: dialog.c; text: "●"
                                enabled: dialog.backend.qemuRunning
                                onClicked: dialog.backend.qemuKey("home")
                            }
                            AndroidToolButton {
                                height: 22; colors: dialog.c; text: "▢"
                                enabled: dialog.backend.qemuRunning
                                onClicked: dialog.backend.qemuKey("recents")
                            }
                            AndroidToolButton {
                                height: 22; colors: dialog.c; text: "⏻"
                                enabled: dialog.backend.qemuRunning
                                onClicked: dialog.backend.qemuKey("power")
                            }
                            AndroidToolButton {
                                height: 22; colors: dialog.c; text: "Snap"
                                onClicked: dialog.backend.qemuSaveScreenshot()
                            }
                            AndroidToolButton {
                                height: 22; colors: dialog.c; text: "Log"
                                visible: dialog.backend.qemuHasLog
                                onClicked: {
                                    dialog.backend.openQemuLogInEditor()
                                    dialog.close()
                                }
                            }
                        }
                    }
                }
            }

            // =================================================================
            // PAGE 2: INTERACTIVE LOGIC MAP EXPLORER (DEX + NATIVE DAEMONS)
            // =================================================================
            Item {
                anchors.fill: parent
                visible: dialog.page === 2

                Item {
                    id: lmHeader
                    x: 14; y: 10
                    width: parent.width - 28
                    height: 32

                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 5
                        Repeater {
                            model: [
                                { key: "all", label: "All" },
                                { key: "services", label: "SystemServer" },
                                { key: "jni", label: "JNI" },
                                { key: "binder", label: "Binder/HAL" },
                                { key: "reflection", label: "Reflection" },
                                { key: "daemons", label: "Daemons" },
                                { key: "hardware", label: "Hardware" },
                                { key: "kernel", label: "Kernel" }
                            ]
                            delegate: AndroidToolButton {
                                required property var modelData
                                height: 24
                                colors: dialog.c
                                text: modelData.label
                                selected: dialog.backend.logicMapCategory === modelData.key
                                onClicked: dialog.backend.setLogicMapCategory(modelData.key)
                            }
                        }
                    }

                    SmoothRectangle {
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.max(150, Math.min(260, parent.width * 0.28))
                        height: 24; radius: 5
                        color: dialog.c.BG_INPUT
                        border.width: 1; border.color: lmSearchInput.activeFocus ? dialog.colAccent : dialog.c.b08
                        TextInput {
                            id: lmSearchInput
                            anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 8
                            verticalAlignment: TextInput.AlignVCenter
                            font.pixelSize: 11; color: dialog.c.FG
                            selectByMouse: true; clip: true
                            onTextChanged: dialog.backend.setLogicMapQuery(text)
                            RakLabel {
                                visible: !lmSearchInput.text
                                anchors.verticalCenter: parent.verticalCenter
                                text: "Filter class, daemon, HAL, JNI…"
                                font.pixelSize: 10; color: dialog.c.FG3
                            }
                        }
                    }
                }

                RakLabel {
                    id: lmSummaryLbl
                    visible: dialog.backend.logicMapSummary.length > 0
                    x: 14; y: 46
                    width: parent.width - 28
                    text: dialog.backend.logicMapSummary
                    font.pixelSize: 10; font.family: "Consolas"
                    color: dialog.c.FG3
                    elide: Text.ElideRight
                }

                ListView {
                    x: 14; y: lmSummaryLbl.visible ? 66 : 48
                    width: parent.width - 28
                    height: parent.height - y - 10
                    spacing: 6
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: dialog.backend.logicMapItems
                    C.ScrollBar.vertical: RakScrollBar { handleColor: dialog.c.BG_SCROLL }

                    delegate: SmoothRectangle {
                        required property var modelData
                        width: ListView.view.width
                        height: lmRowCol.implicitHeight + 14
                        radius: 6
                        color: lmRowMa.containsMouse ? dialog.c.b04 : dialog.c.b02
                        border.width: 1
                        border.color: modelData.status === "error"
                            ? dialog.colErr
                            : (modelData.status === "warn" ? dialog.colWarn : dialog.c.b06)

                        Column {
                            id: lmRowCol
                            x: 10; y: 7
                            width: parent.width - 96
                            spacing: 2

                            Row {
                                spacing: 7
                                SmoothRectangle {
                                    width: lmBadgeTxt.implicitWidth + 10; height: 16; radius: 3
                                    anchors.verticalCenter: parent.verticalCenter
                                    color: dialog.c.a10; border.width: 1; border.color: dialog.c.a25
                                    RakLabel {
                                        id: lmBadgeTxt
                                        anchors.centerIn: parent
                                        text: modelData.badge || ""
                                        font.pixelSize: 9; font.weight: Font.Bold
                                        color: dialog.statusColor(modelData.status)
                                    }
                                }
                                RakLabel {
                                    width: Math.max(80, lmRowCol.width - lmBadgeTxt.implicitWidth - 20)
                                    text: modelData.title || ""
                                    font.pixelSize: 11; font.family: "Consolas"; font.weight: Font.DemiBold
                                    color: dialog.c.FG
                                    elide: Text.ElideMiddle
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                            }
                            RakLabel {
                                width: parent.width
                                text: modelData.detail || ""
                                font.pixelSize: 10
                                color: dialog.c.FG2
                                elide: Text.ElideRight
                            }
                        }

                        AndroidToolButton {
                            visible: !!(modelData.vfsPath && modelData.vfsPath.length > 0 && dialog.backend.vfsReady)
                            anchors.right: parent.right; anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            height: 22
                            colors: dialog.c
                            text: "Inspect"
                            onClicked: dialog.backend.jumpToVfs(modelData.vfsPath)
                        }

                        MouseArea {
                            id: lmRowMa
                            anchors.fill: parent
                            anchors.rightMargin: 76
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: dialog.backend.copyText(modelData.title + " — " + modelData.detail)
                        }
                    }
                }

                RakLabel {
                    visible: dialog.backend.logicMapItems.length === 0
                    anchors.centerIn: parent
                    font.pixelSize: 11
                    color: dialog.c.FG3
                    text: dialog.backend.hasReport
                        ? "No logic map entries match this filter."
                        : "Run Boot first to populate DEX Logic Map & Native Daemon symbol audit."
                }
            }

            // =================================================================
            // PAGE 3: AUTO-FIX RECIPES (WITH PREVIEW DIFF, 1-CLICK APPLY & UNDO)
            // =================================================================
            Item {
                anchors.fill: parent
                visible: dialog.page === 3

                SmoothRectangle {
                    id: afStatusBar
                    visible: dialog.backend.autofixStatus.length > 0 || dialog.backend.autofixCanUndo
                    x: 14; y: 10
                    width: parent.width - 28
                    height: visible ? 32 : 0
                    radius: 6
                    color: dialog.backend.autofixStatusOk ? dialog.c.a10 : dialog.c.b04
                    border.width: 1
                    border.color: dialog.backend.autofixStatusOk ? dialog.c.a25 : dialog.colWarn

                    RakLabel {
                        x: 10; anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - (afUndoBtn.visible ? 120 : 20)
                        text: dialog.backend.autofixStatus || "Auto-Fix backup available."
                        font.pixelSize: 10; color: dialog.backend.autofixStatusOk ? dialog.c.FG : dialog.colWarn
                        elide: Text.ElideRight
                    }
                    AndroidToolButton {
                        id: afUndoBtn
                        visible: dialog.backend.autofixCanUndo
                        anchors.right: parent.right; anchors.rightMargin: 6
                        anchors.verticalCenter: parent.verticalCenter
                        height: 22; colors: dialog.c; text: "Undo Last Fix"
                        onClicked: dialog.backend.undoLastAutofix()
                    }
                }

                ListView {
                    x: 14; y: afStatusBar.visible ? 50 : 12
                    width: parent.width - 28
                    height: parent.height - y - 12
                    spacing: 10
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    model: dialog.backend.autofixRecipes
                    C.ScrollBar.vertical: RakScrollBar { handleColor: dialog.c.BG_SCROLL }

                    delegate: SmoothRectangle {
                        required property var modelData
                        readonly property bool showingDiff: dialog.backend.autofixDiffRecipeId === modelData.id && dialog.backend.autofixDiffText.length > 0
                        width: ListView.view.width
                        height: afCol.implicitHeight + 22
                        radius: 8
                        color: dialog.c.b02
                        border.width: 1; border.color: dialog.c.b08

                        Column {
                            id: afCol
                            x: 14; y: 11
                            width: parent.width - 28
                            spacing: 7

                            Item {
                                width: parent.width; height: 24
                                Row {
                                    spacing: 8
                                    anchors.verticalCenter: parent.verticalCenter
                                    WorkspaceNativeIcon {
                                        width: 14; height: 14; iconSize: 14
                                        iconKey: "status_warn"
                                        strokeColor: dialog.colWarn
                                        isLight: dialog.lightTheme
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                    RakLabel {
                                        width: Math.max(120, afCol.width - afBtnsRow.width - 32)
                                        text: modelData.title
                                        font.pixelSize: 11; font.weight: Font.DemiBold
                                        color: dialog.c.FG
                                        elide: Text.ElideRight
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                }
                                Row {
                                    id: afBtnsRow
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 6

                                    AndroidToolButton {
                                        visible: !!modelData.canApply
                                        height: 24; colors: dialog.c
                                        text: showingDiff ? "Hide Diff" : "Preview Diff"
                                        onClicked: {
                                            if (showingDiff)
                                                dialog.backend.closeAutofixDiff()
                                            else
                                                dialog.backend.previewAutofix(modelData.id)
                                        }
                                    }
                                    AndroidToolButton {
                                        visible: !!modelData.canApply
                                        height: 24; colors: dialog.c; selected: true
                                        text: "Apply Fix"
                                        onClicked: dialog.backend.applyAutofix(modelData.id)
                                    }
                                    AndroidToolButton {
                                        height: 24
                                        colors: dialog.c
                                        text: "Copy"
                                        onClicked: dialog.backend.copyText(modelData.copyText)
                                    }
                                }
                            }

                            SmoothRectangle {
                                visible: showingDiff
                                width: parent.width
                                height: Math.min(200, afDiffLbl.implicitHeight + 14)
                                radius: 6
                                color: dialog.c.BG2
                                border.width: 1; border.color: dialog.c.a25
                                clip: true
                                Flickable {
                                    anchors.fill: parent; anchors.margins: 7
                                    contentWidth: width; contentHeight: afDiffLbl.implicitHeight
                                    clip: true
                                    RakLabel {
                                        id: afDiffLbl
                                        width: parent.width
                                        text: dialog.backend.autofixDiffText
                                        font.pixelSize: 10; font.family: "Consolas"
                                        color: dialog.colOk
                                        wrapMode: Text.WrapAnywhere
                                    }
                                }
                            }

                            SmoothRectangle {
                                width: parent.width
                                height: Math.min(180, afPreview.implicitHeight + 14)
                                radius: 6
                                color: dialog.c.b015
                                border.width: 1; border.color: dialog.c.b06
                                clip: true
                                Flickable {
                                    anchors.fill: parent; anchors.margins: 7
                                    contentWidth: width; contentHeight: afPreview.implicitHeight
                                    clip: true
                                    RakLabel {
                                        id: afPreview
                                        width: parent.width
                                        text: modelData.preview
                                        font.pixelSize: 10; font.family: "Consolas"
                                        color: dialog.c.FG2
                                        wrapMode: Text.WrapAnywhere
                                    }
                                }
                            }
                        }
                    }
                }

                Column {
                    visible: dialog.backend.autofixRecipes.length === 0
                    anchors.centerIn: parent
                    spacing: 8
                    WorkspaceNativeIcon {
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: 24; height: 24; iconSize: 24
                        iconKey: "status_check"
                        strokeColor: dialog.colOk
                        isLight: dialog.lightTheme
                    }
                    RakLabel {
                        text: dialog.backend.hasReport ? "No auto-fix patches needed." : "Run Boot first to generate auto-fix recipes."
                        font.pixelSize: 11
                        color: dialog.c.FG3
                    }
                }
            }

        }

        // ── Footer (44px) ──
        Item {
            id: footer
            y: parent.height - 44
            width: parent.width
            height: 44

            SmoothRectangle { width: parent.width; height: 1; color: dialog.c.b09 }

            SmoothRectangle {
                visible: dialog.backend.running || dialog.backend.stagingBusy || dialog.backend.qemuPreparing
                y: 0; width: parent.width; height: 3
                color: dialog.c.a10
                clip: true
                SmoothRectangle {
                    visible: dialog.backend.progressPercent >= 0
                    width: parent.width * Math.max(0, Math.min(100, dialog.backend.progressPercent)) / 100
                    height: 3; radius: 1
                    color: dialog.colAccent
                    Behavior on width { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
                }
                SmoothRectangle {
                    id: marquee
                    visible: dialog.backend.progressPercent < 0
                    width: parent.width * 0.28; height: 3; radius: 1
                    color: dialog.colAccent
                    SequentialAnimation on x {
                        running: (dialog.backend.running || dialog.backend.stagingBusy || dialog.backend.qemuPreparing) && dialog.backend.progressPercent < 0
                        loops: Animation.Infinite
                        NumberAnimation { from: -marquee.width; to: footer.width; duration: 1200; easing.type: Easing.InOutQuad }
                    }
                }
            }

            Row {
                x: 16
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                width: Math.max(180, parent.width - footerRight.width - 42)

                WorkspaceNativeIcon {
                    visible: dialog.backend.hasReport && !dialog.backend.running
                    width: 14; height: 14; iconSize: 14
                    iconKey: dialog.statusSymbol(dialog.backend.verdictStatus)
                    strokeColor: dialog.statusColor(dialog.backend.verdictStatus)
                    isLight: dialog.lightTheme
                    anchors.verticalCenter: parent.verticalCenter
                }

                RakLabel {
                    width: parent.width - 22
                    anchors.verticalCenter: parent.verticalCenter
                    text: dialog.hoveredGateReason
                        ? dialog.hoveredGateReason
                        : (dialog.backend.running
                            ? dialog.backend.progressText
                            : (dialog.backend.qemuPreparing
                                ? dialog.backend.qemuStatus
                                : (dialog.backend.stagingBusy
                                    ? "Probing images…"
                                    : (dialog.backend.errorText
                                        ? dialog.backend.errorText
                                        : (dialog.backend.hasReport ? dialog.backend.verdictSummary : "")))))
                    font.pixelSize: 10
                    color: dialog.backend.errorText ? dialog.colErr : dialog.c.FG2
                    elide: Text.ElideRight
                }
            }

            Row {
                id: footerRight
                anchors.right: parent.right
                anchors.rightMargin: 16
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8

                AndroidToolButton {
                    height: 26
                    colors: dialog.c
                    text: dialog.backend.avbEnabled ? "AVB: ON" : "AVB: OFF"
                    selected: dialog.backend.avbEnabled
                    onClicked: dialog.backend.setAvbEnabled(!dialog.backend.avbEnabled)
                }

                AndroidToolButton {
                    height: 26
                    colors: dialog.c
                    text: "Permissive"
                    selected: dialog.backend.permissive
                    onClicked: dialog.backend.setPermissive(!dialog.backend.permissive)
                }

                AndroidToolButton {
                    height: 26
                    colors: dialog.c
                    text: "Close"
                    onClicked: dialog.close()
                }
            }
        }
    }
}
