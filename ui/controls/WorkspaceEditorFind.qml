// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

Item {
    id: popup
    objectName: "workspaceEditorFind"
    property var preferences: typeof rakPreferences !== "undefined" ? rakPreferences : null
    required property var hostWindow
    required property var editorPane
    z: 20
    visible: false
    opacity: visible ? 1 : 0
    Behavior on opacity { NumberAnimation { duration: popup.effectivePreferences.settings.animations ? 120 : 0; easing.type: Easing.OutCubic } }
    readonly property var defaultColors: ({
        BG_MODAL: "#20212b",
        BG2: "#1a1a1a",
        BG_INPUT: "#161720",
        b02: "#05ffffff",
        b04: "#0affffff",
        b05: "#0dffffff",
        b06: "#10ffffff",
        b08: "#14ffffff",
        b09: "#16ffffff",
        b10: "#18ffffff",
        b12: "#1cffffff",
        b13: "#1affffff",
        b14: "#1fffffff",
        b15: "#1dffffff",
        b18: "#25ffffff",
        b20: "#28ffffff",
        b22: "#2dffffff",
        b25: "#33ffffff",
        a07: "#153ecf8e",
        a12: "#203ecf8e",
        a18: "#203ecf8e",
        a30: "#403ecf8e",
        a32: "#503ecf8e",
        a38: "#603ecf8e",
        a44: "#683ecf8e",
        a48: "#703ecf8e",
        a55: "#8c3ecf8e",
        a60: "#993ecf8e",
        red14: "#24f85149",
        red28: "#45f85149",
        red32: "#50f85149",
        red42: "#65f85149",
        red55: "#8cf85149",
        FG: "#ededed",
        FG3: "#999999",
        FG_BRIGHT: "#ffffff",
        FG_MUTED: "#8b949e",
        FG_HOVER: "#ffffff",
        FG_DISABLED2: "#555555",
        FG_BLUE: "#3ecf8e",
        FG_BLUE_MUTED: "#328c60",
        FG_BLUE_LIGHT: "#5eead4",
        RED_LIGHT: "#f85149",
        RED_LIGHTER: "#ff7b72",
        accent_hex: "#3ecf8e"
    })
    readonly property var effectivePreferences: preferences || ({
        colors: defaultColors,
        panelBorder: "#25ffffff",
        settings: {
            animations: true,
            effects: "high"
        }
    })
    readonly property var c: (effectivePreferences && effectivePreferences.colors) ? effectivePreferences.colors : defaultColors
    readonly property color tint: popup.c.BG2
    readonly property color modalBg: popup.c.BG_MODAL
    readonly property bool lightTheme: (popup.c && popup.c.is_light !== undefined) ? popup.c.is_light : false

    width: 404
    height: replaceExpanded ? 80 : 44
    Behavior on height { NumberAnimation { duration: popup.effectivePreferences.settings.animations ? 140 : 0; easing.type: Easing.OutCubic } }
    clip: true

    property bool replaceExpanded: false

    readonly property real minimapWidth: {
        if (editorPane && typeof editorPane.findQuillEditor === "function") {
            var qe = editorPane.findQuillEditor()
            if (qe) {
                var enabled = qe.minimapEnabled !== undefined ? qe.minimapEnabled : true
                var w = qe.minimapWidth || 76
                var edW = qe.width || editorPane.width
                if (enabled && edW >= 300) {
                    return w
                }
            }
        }
        return 0
    }

    readonly property real rightMargin: minimapWidth > 0 ? (minimapWidth + 24) : 24

    property bool customPositioned: false
    property real customX: 0
    property real customY: 0

    property real targetX: {
        if (!editorPane || !hostWindow || !hostWindow.contentItem) return 0
        var _dep = editorPane.x + editorPane.y + editorPane.width + hostWindow.width + rightMargin
        var relX = Math.max(12, editorPane.width - width - rightMargin)
        var pt = editorPane.mapToItem(hostWindow.contentItem, relX, 12)
        return Math.round(pt.x)
    }
    property real targetY: {
        if (!editorPane || !hostWindow || !hostWindow.contentItem) return 0
        var _dep = editorPane.x + editorPane.y + editorPane.height + hostWindow.height
        var pt = editorPane.mapToItem(hostWindow.contentItem, 0, 12)
        return Math.round(pt.y)
    }
    x: customPositioned ? customX : targetX
    y: customPositioned ? customY : targetY

    function reposition() {
        if (!editorPane || !editorPane.visible || !hostWindow || !hostWindow.contentItem) return
        var relX = Math.max(12, editorPane.width - popup.width - rightMargin)
        var pt = editorPane.mapToItem(hostWindow.contentItem, relX, 12)
        if (!customPositioned) {
            popup.x = Math.round(pt.x)
            popup.y = Math.round(pt.y)
        } else {
            var minPt = editorPane.mapToItem(hostWindow.contentItem, 12, 12)
            var maxPt = editorPane.mapToItem(hostWindow.contentItem, Math.max(12, editorPane.width - popup.width - rightMargin), Math.max(12, editorPane.height - popup.height - 12))
            customX = Math.max(Math.round(minPt.x), Math.min(Math.round(maxPt.x), customX))
            customY = Math.max(Math.round(minPt.y), Math.min(Math.round(maxPt.y), customY))
            popup.x = customX
            popup.y = customY
        }
    }
    Connections {
        target: editorPane
        function onWidthChanged() { if (popup.visible) popup.reposition() }
        function onHeightChanged() { if (popup.visible) popup.reposition() }
        function onXChanged() { if (popup.visible) popup.reposition() }
        function onYChanged() { if (popup.visible) popup.reposition() }
        function onVisibleChanged() { if (!editorPane.visible && popup.visible) popup.closeFind() }
    }
    Connections {
        target: hostWindow
        function onWidthChanged() { if (popup.visible) popup.reposition() }
        function onHeightChanged() { if (popup.visible) popup.reposition() }
        function onNativeDialogActiveChanged() {
            if (hostWindow.nativeDialogActive && popup.visible) {
                popup.closeFind()
            }
        }
    }

    function openFind(showReplace) {
        if (hostWindow && hostWindow.nativeDialogActive) return
        if (showReplace !== undefined) {
            replaceExpanded = showReplace
        }
        reposition()
        visible = true
        findInput.forceActiveFocus()
        findInput.selectAll()
        updateMatches()
    }

    function closeFind() {
        visible = false
        var qe = editorPane ? editorPane.findQuillEditor() : null
        if (qe) qe.forceActiveFocus()
    }
    function findNext() {
        var qe = editorPane ? editorPane.findQuillEditor() : null
        if (qe && findInput.text.length > 0) {
            qe.findNext(findInput.text, false)
            updateMatches()
        }
    }

    function findPrev() {
        var qe = editorPane ? editorPane.findQuillEditor() : null
        if (qe && findInput.text.length > 0) {
            qe.findNext(findInput.text, true)
            updateMatches()
        }
    }

    function replaceNext() {
        var qe = editorPane ? editorPane.findQuillEditor() : null
        if (qe && findInput.text.length > 0) {
            qe.replaceNext(findInput.text, replaceInput.text)
            updateMatches()
        }
    }

    function replaceAll() {
        var qe = editorPane ? editorPane.findQuillEditor() : null
        if (qe && findInput.text.length > 0) {
            qe.replaceAll(findInput.text, replaceInput.text)
            updateMatches()
        }
    }

    function updateMatches() {
        if (!findInput.text) {
            matchLabel.text = ""
            return
        }
        var qe = editorPane ? editorPane.findQuillEditor() : null
        if (!qe) return
        var count = qe.countMatches(findInput.text)
        matchLabel.text = count > 0 ? (count + " results") : "No results"
    }

    LiveGlass {
        id: surface
        objectName: "findGlass"
        anchors.fill: parent
        preferences: popup.effectivePreferences
        backdropSource: popup.hostWindow.backdropSurface
        origin: Qt.point(popup.x, popup.y)
        effectsEnabled: popup.effectivePreferences.settings.effects !== "low"
        flatColor: popup.modalBg
        tintColor: Qt.rgba(popup.tint.r, popup.tint.g, popup.tint.b, 180/255)
        cornerRadius: 12
        edgeColor: popup.effectivePreferences.panelBorder
    }

    Item {
        anchors.fill: parent
        Column {
            anchors.fill: parent
            anchors.margins: 8
            spacing: 8

            // Row 1: Find Row
            Row {
                width: parent.width
                height: 28
                spacing: 6

                // Drag handle (gripper)
                Item {
                    id: dragHandle
                    width: 10; height: 28

                    Column {
                        anchors.centerIn: parent
                        spacing: 2
                        Repeater {
                            model: 3
                            Row {
                                spacing: 2
                                Repeater {
                                    model: 2
                                    SmoothRectangle {
                                        width: 2; height: 2; radius: 1
                                        color: dragArea.containsMouse ? popup.c.FG_BRIGHT : popup.c.b20
                                    }
                                }
                            }
                        }
                    }

                    MouseArea {
                        id: dragArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.SizeAllCursor
                        property real startMouseX: 0
                        property real startMouseY: 0
                        onPressed: mouse => {
                            startMouseX = mouse.x
                            startMouseY = mouse.y
                        }
                        onPositionChanged: mouse => {
                            if (pressed) {
                                popup.customPositioned = true
                                popup.customX = Math.round(popup.x + (mouse.x - startMouseX))
                                popup.customY = Math.round(popup.y + (mouse.y - startMouseY))
                                popup.x = popup.customX
                                popup.y = popup.customY
                            }
                        }
                        onDoubleClicked: {
                            popup.customPositioned = false
                            popup.reposition()
                        }
                        HoverTip { label: "Drag to move (Double-click to reset)"; hovering: dragArea.containsMouse }
                    }
                }

                // Expand/collapse replace chevron (matching filetree folder arrow)
                Item {
                    id: toggleReplaceBtn
                    width: 24; height: 28

                    SmoothRectangle {
                        anchors.fill: parent
                        radius: 5
                        color: toggleArea.pressed ? popup.c.b13 : (toggleArea.containsMouse ? popup.c.b08 : "transparent")
                    }

                    Image {
                        anchors.centerIn: parent
                        width: 12; height: 12
                        source: "image://icons/arrow_right?color=" + encodeURIComponent(toggleArea.containsMouse ? (popup.lightTheme ? popup.c.FG : popup.c.FG_BRIGHT) : (popup.lightTheme ? popup.c.FG : popup.c.FG_MUTED))
                        sourceSize: Qt.size(24, 24)
                        smooth: true
                        opacity: toggleArea.containsMouse ? 1.0 : 0.8
                        rotation: popup.replaceExpanded ? 90 : 0
                        Behavior on rotation { NumberAnimation { duration: popup.effectivePreferences.settings.animations ? 140 : 0; easing.type: Easing.OutCubic } }
                    }

                    MouseArea {
                        id: toggleArea
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: popup.replaceExpanded = !popup.replaceExpanded
                        HoverTip { label: popup.replaceExpanded ? "Collapse Replace (Ctrl+H)" : "Expand Replace (Ctrl+H)"; hovering: toggleArea.containsMouse }
                    }
                }

                // Find input frame
                SmoothRectangle {
                    id: findFrame
                    width: 162; height: 28; radius: 6
                    color: popup.c.BG_INPUT
                    border.width: 1
                    border.color: findInput.activeFocus ? (popup.c.a55 || popup.c.accent_hex) : popup.c.b13
                    Behavior on border.color { ColorAnimation { duration: popup.effectivePreferences.settings.animations ? 120 : 0 } }

                    RakField {
                        id: findInput
                        objectName: "findInput"
                        anchors.fill: parent
                        anchors.leftMargin: 8; anchors.rightMargin: 8
                        placeholderText: "Find"
                        font.family: "JetBrains Mono"
                        font.pixelSize: 11
                        labelColor: popup.c.FG
                        hintColor: popup.c.FG_MUTED
                        selectionColor: popup.c.a38
                        selectedTextColor: popup.c.FG_BRIGHT
                        fillColor: "transparent"
                        focusFillColor: "transparent"
                        edgeColor: "transparent"
                        focusEdgeColor: "transparent"
                        background: null
                        verticalAlignment: TextInput.AlignVCenter
                        onTextChanged: popup.updateMatches()
                        Keys.onPressed: function(event) {
                            if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                                if (event.modifiers & Qt.ShiftModifier) {
                                    popup.findPrev()
                                } else {
                                    popup.findNext()
                                }
                                event.accepted = true
                            } else if (event.key === Qt.Key_Escape) {
                                popup.closeFind()
                                event.accepted = true
                            }
                        }
                    }
                }

                // Match count indicator
                RakLabel {
                    id: matchLabel
                    width: 64
                    height: parent.height
                    verticalAlignment: Text.AlignVCenter
                    horizontalAlignment: Text.AlignHCenter
                    font.family: "Poppins"
                    font.pixelSize: 10
                    color: text === "No results" ? popup.c.RED_LIGHT : popup.c.FG_MUTED
                    elide: Text.ElideRight
                }

                // Previous match button (filetree arrow pointing up)
                Item {
                    id: prevBtn
                    width: 24; height: 28

                    SmoothRectangle {
                        anchors.fill: parent
                        radius: 5
                        color: prevArea.pressed ? popup.c.b13 : (prevArea.containsMouse ? popup.c.b08 : "transparent")
                    }

                    Image {
                        anchors.centerIn: parent
                        width: 12; height: 12
                        source: "image://icons/arrow_right?color=" + encodeURIComponent(prevArea.containsMouse ? (popup.lightTheme ? popup.c.FG : popup.c.FG_BRIGHT) : (popup.lightTheme ? popup.c.FG : popup.c.FG_MUTED))
                        sourceSize: Qt.size(24, 24)
                        rotation: -90
                        smooth: true
                        opacity: prevArea.containsMouse ? 1.0 : 0.8
                    }

                    MouseArea {
                        id: prevArea
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: popup.findPrev()
                        HoverTip { label: "Previous Match (Shift+Enter)"; hovering: prevArea.containsMouse }
                    }
                }

                // Next match button (filetree arrow pointing down)
                Item {
                    id: nextBtn
                    width: 24; height: 28

                    SmoothRectangle {
                        anchors.fill: parent
                        radius: 5
                        color: nextArea.pressed ? popup.c.b13 : (nextArea.containsMouse ? popup.c.b08 : "transparent")
                    }

                    Image {
                        anchors.centerIn: parent
                        width: 12; height: 12
                        source: "image://icons/arrow_right?color=" + encodeURIComponent(nextArea.containsMouse ? (popup.lightTheme ? popup.c.FG : popup.c.FG_BRIGHT) : (popup.lightTheme ? popup.c.FG : popup.c.FG_MUTED))
                        sourceSize: Qt.size(24, 24)
                        rotation: 90
                        smooth: true
                        opacity: nextArea.containsMouse ? 1.0 : 0.8
                    }

                    MouseArea {
                        id: nextArea
                        anchors.fill: parent
                        hoverEnabled: true
                        onClicked: popup.findNext()
                        HoverTip { label: "Next Match (Enter)"; hovering: nextArea.containsMouse }
                    }
                }

                // Close button (X)
                Item {
                    id: closeBtn
                    width: 24; height: 28
                    readonly property color closeIconColor: closeArea.containsMouse
                        ? (popup.c.RED_LIGHT || "#f85149")
                        : (popup.lightTheme ? popup.c.FG : (popup.c.FG_MUTED || "#8b949e"))

                    SmoothRectangle {
                        anchors.fill: parent
                        radius: 5
                        color: closeArea.pressed
                            ? (popup.c.red28 || "#45f85149")
                            : (closeArea.containsMouse ? (popup.c.red14 || "#20f85149") : "transparent")
                    }

                    Image {
                        id: closeImg
                        anchors.centerIn: parent
                        width: 12; height: 12
                        source: "image://icons/close_rounded?color=" + encodeURIComponent(closeBtn.closeIconColor)
                        sourceSize: Qt.size(24, 24)
                        smooth: true
                        opacity: closeArea.containsMouse ? 1.0 : (popup.lightTheme ? 0.9 : 0.75)
                        visible: status === Image.Ready
                    }

                    RakLabel {
                        anchors.centerIn: parent
                        text: "✕"
                        font.pixelSize: 11
                        font.weight: Font.DemiBold
                        color: closeBtn.closeIconColor
                        opacity: closeArea.containsMouse ? 1.0 : (popup.lightTheme ? 0.9 : 0.75)
                        visible: closeImg.status !== Image.Ready
                    }

                    MouseArea {
                        id: closeArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: popup.closeFind()
                        HoverTip { label: "Close (Escape)"; hovering: closeArea.containsMouse }
                    }
                }
            }

            // Row 2: Replace Row (collapsible)
            Row {
                width: parent.width
                height: 28
                spacing: 6
                visible: popup.replaceExpanded

                // Spacer matching drag handle + chevron width
                Item { width: 40; height: 28 }

                // Replace input frame
                SmoothRectangle {
                    id: replaceFrame
                    width: 162; height: 28; radius: 6
                    color: popup.c.BG_INPUT
                    border.width: 1
                    border.color: replaceInput.activeFocus ? (popup.c.a55 || popup.c.accent_hex) : popup.c.b13
                    Behavior on border.color { ColorAnimation { duration: popup.effectivePreferences.settings.animations ? 120 : 0 } }

                    RakField {
                        id: replaceInput
                        objectName: "replaceInput"
                        anchors.fill: parent
                        anchors.leftMargin: 8; anchors.rightMargin: 8
                        placeholderText: "Replace"
                        font.family: "JetBrains Mono"
                        font.pixelSize: 11
                        labelColor: popup.c.FG
                        hintColor: popup.c.FG_MUTED
                        selectionColor: popup.c.a38
                        selectedTextColor: popup.c.FG_BRIGHT
                        fillColor: "transparent"
                        focusFillColor: "transparent"
                        edgeColor: "transparent"
                        focusEdgeColor: "transparent"
                        background: null
                        verticalAlignment: TextInput.AlignVCenter
                        Keys.onPressed: function(event) {
                            if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                                if (event.modifiers & (Qt.ControlModifier | Qt.AltModifier)) {
                                    popup.replaceAll()
                                } else {
                                    popup.replaceNext()
                                }
                                event.accepted = true
                            } else if (event.key === Qt.Key_Escape) {
                                popup.closeFind()
                                event.accepted = true
                            }
                        }
                    }
                }

                // Replace button
                DialogButton {
                    id: replaceBtn
                    objectName: "replaceBtn"
                    colors: popup.c
                    primary: false
                    text: "Replace"
                    implicitHeight: 28
                    implicitWidth: 72
                    cornerRadius: 6
                    leftPadding: 8; rightPadding: 8
                    font.pixelSize: 11
                    toolTip: "Replace match (Enter)"
                    onClicked: popup.replaceNext()
                }

                // Replace All button
                DialogButton {
                    id: replaceAllBtn
                    objectName: "replaceAllBtn"
                    colors: popup.c
                    primary: false
                    text: "Replace All"
                    implicitHeight: 28
                    implicitWidth: 86
                    cornerRadius: 6
                    leftPadding: 8; rightPadding: 8
                    font.pixelSize: 11
                    toolTip: "Replace all matches (Ctrl+Alt+Enter)"
                    onClicked: popup.replaceAll()
                }
            }
        }
    }
}
