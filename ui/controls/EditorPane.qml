// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import Quill.Native 1.0

Item {
    id: pane
    required property var session
    required property color fillColor
    property var colors: null
    property var preferences: typeof rakPreferences !== "undefined" ? rakPreferences : null
    property var hostWindow: null
    property var contextMenu: null
    property var findOverlay: null
    property bool nativeTabs: false
    readonly property bool isQuill: true
    readonly property var activeItem: quillEditor
    readonly property alias editor: quillEditor

    onVisibleChanged: if (!visible && findOverlay && findOverlay.visible) findOverlay.closeFind()
    readonly property var defaultColors: ({
        BG_MODAL: "#20212b",
        BG2: "#1a1a1a",
        BG_INPUT: "#161720",
        b18: "#25ffffff",
        b13: "#1affffff",
        b08: "#14ffffff",
        b06: "#10ffffff",
        b10: "#18ffffff",
        a18: "#203ecf8e",
        a30: "#403ecf8e",
        a32: "#503ecf8e",
        a38: "#603ecf8e",
        a48: "#703ecf8e",
        a55: "#8c3ecf8e",
        a60: "#993ecf8e",
        FG: "#ededed",
        FG3: "#999999",
        FG_BRIGHT: "#ffffff",
        FG_MUTED: "#8b949e",
        FG_HOVER: "#ffffff",
        FG_DISABLED2: "#555555",
        FG_BLUE: "#3ecf8e",
        FG_BLUE_LIGHT: "#5eead4",
        RED_LIGHT: "#f85149",
        RED_LIGHTER: "#ff7b72",
        red14: "#24f85149",
        red28: "#45f85149",
        red32: "#50f85149",
        red42: "#65f85149",
        red55: "#8cf85149",
        accent_hex: "#3ecf8e"
    })
    readonly property var effectivePreferences: preferences || ({
        colors: pane.colors || defaultColors,
        panelBorder: (pane.colors && pane.colors.b18) ? pane.colors.b18 : "#25ffffff",
        settings: {
            animations: true,
            effects: "high"
        }
    })
    readonly property var c: pane.colors || (preferences ? preferences.colors : defaultColors)

    function find() {
        if (!visible) return
        if (findOverlay) findOverlay.openFind(false)
    }

    function showContextMenu(clickX, clickY, editor) {
        const menu = pane.contextMenu || (pane.hostWindow && pane.hostWindow.contextMenu)
        if (!menu || !pane.hostWindow) return
        const pt = editor.mapToItem(pane.hostWindow.contentItem, clickX, clickY)
        const agentAvail = !!(pane.hostWindow.workspace && pane.hostWindow.workspace.agentChat && pane.hostWindow.workspace.agentChat.available)
        const hasSel = !!editor.hasSelection
        const canEdit = !editor.readOnly
        const filePath = editor.filePath || (pane.session ? pane.session.currentPath : "")
        const actions = [
            { key: "copy", label: "Copy", shortcut: "Ctrl+C", enabled: hasSel, callback: function() { editor.copy() } },
            { key: "cut", label: "Cut", shortcut: "Ctrl+X", enabled: hasSel && canEdit, callback: function() { editor.cut() } },
            { key: "paste", label: "Paste", shortcut: "Ctrl+V", enabled: canEdit, callback: function() { editor.paste() } },
            { separator: true },
            {
                key: "agentSend",
                label: "Send to agent",
                enabled: agentAvail,
                callback: function() {
                    pane.session.setSelection(filePath, editor.selectionRange)
                    pane.hostWindow.workspace.dispatch("agentSend", {
                        origin: "editor",
                        path: filePath,
                        selection: editor.selectionRange
                    })
                }
            }
        ]
        menu.showMenu({
            x: pt.x,
            y: pt.y,
            actions: actions,
            menuKind: "single"
        })
    }

    SmoothRectangle { anchors.fill: parent; color: pane.fillColor }

    Item {
        id: quillContainer
        anchors.fill: parent
        property alias editor: quillEditor

        NativeEditor {
            id: quillEditor
            objectName: "quickQuillView"
            anchors.fill: parent
            focus: true
            activeFocusOnTab: true
            readonly property bool lightTheme: (pane.colors && pane.colors.is_light !== undefined) ? pane.colors.is_light : (pane.effectivePreferences.paletteName ? pane.effectivePreferences.paletteName.indexOf("light") >= 0 : false)
            readonly property color selectedBg: (pane.colors && pane.colors.BG_SELECTED) ? pane.colors.BG_SELECTED : (lightTheme ? "#f5e6d8" : "#264f78")
            isLight: lightTheme
            fillColor: pane.fillColor
            gutterColor: pane.fillColor
            textColor: (pane.colors && pane.colors.FG) ? pane.colors.FG : (lightTheme ? "#2c2420" : "#d4d4d4")
            lineNumberColor: (pane.colors && pane.colors.FG_MUTED) ? pane.colors.FG_MUTED : (lightTheme ? "#9a8878" : "#6e7681")
            activeLineNumberColor: (pane.colors && pane.colors.FG) ? pane.colors.FG : (lightTheme ? "#100c08" : "#ffffff")
            cursorColor: (pane.colors && pane.colors.accent_hex) ? pane.colors.accent_hex : "#3ecf8e"
            selectionColor: Qt.rgba(selectedBg.r, selectedBg.g, selectedBg.b, lightTheme ? 0.45 : 0.25)
            lineHighlightColor: Qt.rgba(selectedBg.r, selectedBg.g, selectedBg.b, lightTheme ? 0.35 : 0.08)
            gutterDividerColor: (pane.colors && pane.colors.b18) ? pane.colors.b18 : (lightTheme ? Qt.rgba(0, 0, 0, 0.08) : Qt.rgba(255, 255, 255, 0.08))

            Component.onCompleted: {
                if (pane.session && pane.session.currentPath) {
                    quillEditor.loadPath(pane.session.currentPath)
                }
            }

            Connections {
                target: pane.session
                function onOpenFileRequested(filePath) {
                    quillEditor.loadPath(filePath)
                    quillEditor.forceActiveFocus()
                }
                function onCurrentPathChanged() {
                    if (pane.session && pane.session.currentPath) {
                        quillEditor.loadPath(pane.session.currentPath)
                        quillEditor.forceActiveFocus()
                    }
                }
                function onCloseFileRequested(filePath) {
                    quillEditor.closeFile(filePath)
                }
                function onSaveRequested() {
                    quillEditor.saveCurrent()
                    pane.session.notifySaved(quillEditor.filePath)
                }
            }

            Connections {
                target: pane.session ? pane.session.bridge : null
                function onAgentEditRequested(key, path, before, after) {
                    if (quillEditor.filePath === path || (pane.session && pane.session.currentPath === path)) {
                        var ok = quillEditor.applyEdit(before, after)
                        if (ok) {
                            pane.session.setContent(path, after)
                            pane.session.setDirty(path, true)
                            pane.session.bridge.on_agent_edit_finished(key, path, true, "")
                        } else {
                            pane.session.bridge.on_agent_edit_finished(key, path, false, "Document content did not match expected revision.")
                        }
                    } else {
                        var tab = pane.session.bridge.get_open_files().indexOf(path) >= 0 ? pane.session.bridge._tabs[path] : null
                        if (tab && tab.content === before && !tab.read_only) {
                            pane.session.bridge.on_content_changed(path, after)
                            pane.session.bridge.on_agent_edit_finished(key, path, true, "")
                        } else {
                            pane.session.bridge.on_agent_edit_finished(key, path, false, "Document changed or is no longer editable.")
                        }
                    }
                }
                function onNavigateRequested(path, line, column, endLine, endColumn) {
                    if (quillEditor.filePath === path || (pane.session && pane.session.currentPath === path)) {
                        quillEditor.navigateTo(line, column, endLine, endColumn)
                    }
                }
            }

            Connections {
                target: quillEditor
                function onStateChanged() {
                    pane.session.setDirty(quillEditor.filePath, quillEditor.dirty)
                    pane.session.setSelection(quillEditor.filePath, quillEditor.selectionRange)
                    if (quillEditor.filePath) {
                        pane.session.setContent(quillEditor.filePath, quillEditor.getText())
                    }
                }
                function onSelectionChanged(range) {
                    pane.session.setSelection(quillEditor.filePath, range)
                }
                function onViewportChanged() {
                    vScrollBar.reveal()
                    hScrollBar.reveal()
                }
                function onFindRequested() {
                    if (pane.findOverlay) pane.findOverlay.openFind(false)
                }
                function onReplaceRequested() {
                    if (pane.findOverlay) pane.findOverlay.openFind(true)
                }
                function onContextMenuRequested(clickX, clickY) {
                    pane.showContextMenu(clickX, clickY, quillEditor)
                }
            }
        }

        // Right-side auto-hiding scrollbar hint matching RAK style
        RakScrollBar {
            id: vScrollBar
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            anchors.right: parent.right
            anchors.rightMargin: 1
            anchors.topMargin: 2
            anchors.bottomMargin: 2
            width: 8
            z: 20
            orientation: Qt.Vertical
            autoHide: true
            idleDelay: 1000
            handleColor: (pane.colors && pane.colors.BG_SCROLL) ? pane.colors.BG_SCROLL : Qt.rgba(1, 1, 1, 0.22)
            hoverColor: (pane.colors && pane.colors.b50) ? pane.colors.b50 : Qt.rgba(1, 1, 1, 0.45)
            downColor: (pane.colors && pane.colors.a55) ? pane.colors.a55 : Qt.rgba(1, 1, 1, 0.65)
            Behavior on opacity { NumberAnimation { duration: 160 } }

            Binding on position {
                when: !vScrollBar.pressed
                value: quillEditor.totalHeight > quillEditor.height
                       ? (quillEditor.scrollY / quillEditor.totalHeight)
                       : 0
            }
            size: quillEditor.totalHeight > quillEditor.height
                  ? Math.max(0.025, quillEditor.height / quillEditor.totalHeight)
                  : 1

            onPositionChanged: {
                if (pressed && quillEditor.totalHeight > quillEditor.height) {
                    quillEditor.scrollY = position * quillEditor.totalHeight
                }
            }
        }

        // Bottom auto-hiding horizontal scrollbar
        RakScrollBar {
            id: hScrollBar
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.leftMargin: 2
            anchors.rightMargin: 10
            anchors.bottomMargin: 1
            height: 8
            z: 20
            orientation: Qt.Horizontal
            autoHide: true
            idleDelay: 1000
            handleColor: (pane.colors && pane.colors.BG_SCROLL) ? pane.colors.BG_SCROLL : Qt.rgba(1, 1, 1, 0.22)
            hoverColor: (pane.colors && pane.colors.b50) ? pane.colors.b50 : Qt.rgba(1, 1, 1, 0.45)
            downColor: (pane.colors && pane.colors.a55) ? pane.colors.a55 : Qt.rgba(1, 1, 1, 0.65)
            Behavior on opacity { NumberAnimation { duration: 160 } }

            Binding on position {
                when: !hScrollBar.pressed
                value: quillEditor.totalWidth > quillEditor.width
                       ? (quillEditor.scrollX / quillEditor.totalWidth)
                       : 0
            }
            size: quillEditor.totalWidth > quillEditor.width
                  ? Math.max(0.025, quillEditor.width / quillEditor.totalWidth)
                  : 1

            onPositionChanged: {
                if (pressed && quillEditor.totalWidth > quillEditor.width) {
                    quillEditor.scrollX = position * quillEditor.totalWidth
                }
            }
        }
    }

    function findQuillEditor() {
        return quillEditor
    }
}
