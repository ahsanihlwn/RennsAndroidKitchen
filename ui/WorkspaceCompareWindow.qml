// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import Quill.Native 1.0
import "controls"

DialogWindow {
    id: window; objectName: "workspaceCompareWindow"
    required property var workspace
    readonly property var preferences: model
    readonly property var backend: workspace.privateFeatures.compare
    readonly property var managedDialogs: [confirmation,saveDialog,destinationDialog]
    property int tab: 0
    property real dragOffsetX: 0
    property real dragOffsetY: 0
    readonly property color glassSurface: window.c.b03
    implicitWidth: Math.max(540, Math.min(hostWindow.width * .74, 840))
    implicitHeight: Math.max(380, Math.min(hostWindow.height * .76, 540))
    panelAlpha: 180/255; panelRadius: 14; closePolicy: C.Popup.NoAutoClose
    x: Math.round(Math.max(8, Math.min(Math.max(8, hostWindow.width - width - 8), (hostWindow.width - width) / 2 + dragOffsetX)))
    y: Math.round(Math.max(8, Math.min(Math.max(8, hostWindow.height - height - 8), (hostWindow.height - height) / 2 + dragOffsetY)))

    function clampOffset(ox, oy) {
        var minX = 8 - (hostWindow.width - width) / 2
        var maxX = Math.max(minX, (hostWindow.width - width) / 2 - 8)
        var minY = 8 - (hostWindow.height - height) / 2
        var maxY = Math.max(minY, (hostWindow.height - height) / 2 - 8)
        dragOffsetX = Math.max(minX, Math.min(maxX, ox))
        dragOffsetY = Math.max(minY, Math.min(maxY, oy))
    }

    function moveByDelta(dx, dy) {
        clampOffset(dragOffsetX + dx, dragOffsetY + dy)
    }

    function dismiss() {
        window.backend.requestClose()
    }

    function syncVisibility() {
        if (window.backend.visible && !window.visible) {
            dragOffsetX = 0
            dragOffsetY = 0
            window.open()
        } else if (!window.backend.visible && window.visible) {
            window.close()
        }
    }

    Component.onCompleted: Qt.callLater(syncVisibility)

    function formatBytes(n) {
        if (n === undefined || n === null || n < 0) return "?"
        if (n < 1024) return n + " B"
        if (n < 1048576) return (n / 1024).toFixed(1) + " KB"
        return (n / 1048576).toFixed(1) + " MB"
    }

    Item {
        id: card; width: window.width; height: window.height

        // Title bar
        Item {
            id: titleBar; width: parent.width; height: 44
            Item {
                anchors.fill: parent; anchors.rightMargin: 44
                HoverHandler { cursorShape: Qt.SizeAllCursor }
                DragHandler {
                    id: titleDrag
                    target: null
                    property real startOffX: 0
                    property real startOffY: 0
                    property point startScene: Qt.point(0, 0)
                    onActiveChanged: {
                        if (active) {
                            startOffX = window.dragOffsetX
                            startOffY = window.dragOffsetY
                            startScene = centroid.scenePosition
                        }
                    }
                    onCentroidChanged: {
                        if (active) {
                            window.clampOffset(
                                startOffX + (centroid.scenePosition.x - startScene.x),
                                startOffY + (centroid.scenePosition.y - startScene.y)
                            )
                        }
                    }
                }
            }

            Row {
                x: 16; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                SmoothRectangle {
                    width: 22; height: 22; radius: 6
                    color: "#264d8ef0"; border.width: 1; border.color: "#594d8ef0"
                    anchors.verticalCenter: parent.verticalCenter
                    RakLabel { anchors.centerIn: parent; text: "A"; font.pixelSize: 10; font.weight: Font.Bold; color: "#7eb8f7" }
                }
                RakLabel {
                    anchors.verticalCenter: parent.verticalCenter
                    text: window.backend.nameA || "Side A"
                    font.pixelSize: 13; font.weight: Font.DemiBold; color: window.c.FG
                }
                RakLabel {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "↔"; font.pixelSize: 13; color: window.c.FG_MUTED
                }
                SmoothRectangle {
                    width: 22; height: 22; radius: 6
                    color: "#266dba8a"; border.width: 1; border.color: "#596dba8a"
                    anchors.verticalCenter: parent.verticalCenter
                    RakLabel { anchors.centerIn: parent; text: "B"; font.pixelSize: 10; font.weight: Font.Bold; color: "#6dba8a" }
                }
                RakLabel {
                    anchors.verticalCenter: parent.verticalCenter
                    text: window.backend.nameB || "Side B"
                    font.pixelSize: 13; font.weight: Font.DemiBold; color: window.c.FG
                }
            }

            RakButton {
                id: close; anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
                width: 28; height: 28; leftPadding: 0; rightPadding: 0; text: "✕"; cornerRadius: 7; edgeWidth: 0
                labelColor: window.c.FG_MUTED; hoverFillColor: window.c.red18; hoverLabelColor: window.c.RED_LIGHT
                onClicked: window.dismiss()
            }
        }

        // Single-line dynamic status + sleek progress bar
        SmoothRectangle {
            id: liveBar; x: 14; y: 46; width: parent.width - 28; height: 40; radius: 9
            color: window.glassSurface; border.width: 1; border.color: window.c.b08

            Item {
                x: 12; y: 5; width: parent.width - 24; height: 20
                RakLabel {
                    id: liveLine; objectName: "compareLiveStatus"
                    width: parent.width - phasePill.width - 10; height: parent.height
                    text: window.backend.liveStatus || window.backend.status || (window.backend.busy ? window.s.cmp_scanning : window.s.cmp_done)
                    font.family: "Consolas"; font.pixelSize: 11; elide: Text.ElideMiddle
                    color: window.backend.busy ? window.c.FG : window.c.FG2
                }
                RakLabel {
                    id: phasePill
                    anchors.right: parent.right; height: parent.height
                    text: window.backend.busy
                          ? (window.backend.progress >= 0 ? Math.round(window.backend.progress * 100) + "%" : window.backend.phase.toUpperCase())
                          : "READY"
                    font.pixelSize: 10; font.weight: Font.DemiBold
                    color: window.backend.busy ? window.c.accent_hex : window.c.FG_BLUE
                }
            }

            Item {
                id: track; x: 12; y: 28; width: parent.width - 24; height: 4; clip: true
                Rectangle { anchors.fill: parent; radius: 2; color: window.c.b08 }
                Rectangle {
                    id: indBar
                    visible: window.backend.busy && window.backend.progress < 0
                    width: Math.max(48, parent.width * 0.28); height: parent.height; radius: 2
                    color: window.c.accent_hex
                    SequentialAnimation on x {
                        running: indBar.visible && window.visible && (!window.model || window.model.settings.animations)
                        loops: Animation.Infinite
                        NumberAnimation { from: -indBar.width; to: track.width; duration: 1050; easing.type: Easing.InOutSine }
                    }
                }
                Rectangle {
                    visible: !indBar.visible
                    height: parent.height; radius: 2
                    color: window.c.accent_hex
                    width: Math.max(0, Math.min(parent.width, parent.width * Math.max(0, Math.min(1, window.backend.progress))))
                    Behavior on width { enabled: !window.model || window.model.settings.animations; NumberAnimation { duration: (!window.model || window.model.settings.animations) ? 110 : 0; easing.type: Easing.OutCubic } }
                }
            }
        }

        // Tabs + Ring 1/2/3 pills
        Item {
            id: toolStrip; x: 14; y: 92; width: parent.width - 28; height: 30
            Row {
                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                AndroidToolButton {
                    colors: window.c; height: 28; font.pixelSize: 11
                    text: window.s.cmp_tab_log
                    selected: window.tab === 0
                    onClicked: window.tab = 0
                }
                AndroidToolButton {
                    objectName: "compareDiffTab"; colors: window.c; height: 28; font.pixelSize: 11
                    text: window.backend.diffs.rowCount() > 0
                          ? (window.s.cmp_tab_diff + "  (" + window.backend.diffs.rowCount() + ")")
                          : window.s.cmp_tab_diff
                    selected: window.tab === 1
                    onClicked: window.tab = 1
                }
            }

            Row {
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                Repeater {
                    model: [
                        {num: 1, label: "Existence"},
                        {num: 2, label: "Config"},
                        {num: 3, label: "Content"}
                    ]
                    delegate: SmoothRectangle {
                        required property var modelData
                        readonly property string ringState: window.backend.rings[String(modelData.num)] || ""
                        readonly property bool isDone: ringState.length > 0 && ringState !== "skipped"
                        readonly property bool isSkip: ringState === "skipped"
                        readonly property string ringSuffix: isDone ? (ringState === "done" ? "" : ringState.replace(/\u2714/g, "").trim()) : (isSkip ? "skipped" : "")
                        height: 24; width: ringRow.implicitWidth + 14; radius: 6
                        color: isDone ? window.c.a12 : window.c.b04
                        border.width: 1; border.color: isDone ? window.c.a30 : window.c.b08
                        Row {
                            id: ringRow; anchors.centerIn: parent; spacing: 5
                            Image {
                                visible: isDone
                                width: 13; height: 13
                                sourceSize.width: 13; sourceSize.height: 13
                                anchors.verticalCenter: parent.verticalCenter
                                source: window.preferences.icon("check_white", window.c.FG_BLUE)
                            }
                            RakLabel {
                                anchors.verticalCenter: parent.verticalCenter
                                text: "R" + modelData.num + " · " + modelData.label + (ringSuffix ? "  " + ringSuffix : "")
                                font.pixelSize: 10
                                color: isDone ? window.c.FG : (isSkip ? window.c.FG_MUTED : window.c.FG2)
                            }
                            AndroidToolButton {
                                visible: modelData.num > 1 && !ringState && window.backend.busy
                                colors: window.c; height: 18; font.pixelSize: 10
                                text: window.s.cmp_btn_skip
                                onClicked: window.backend.skip(modelData.num)
                            }
                        }
                    }
                }
            }
        }

        // Tab 0: Compare Log
        SmoothRectangle {
            x: 14; y: 128; width: parent.width - 28; height: parent.height - y - 48; radius: 10
            color: window.glassSurface; border.width: 1; border.color: window.c.b08
            visible: window.tab === 0
            ListView {
                id: log; objectName: "compareLog"
                anchors.fill: parent; anchors.margins: 10; clip: true; model: window.backend.logs
                C.ScrollBar.vertical: RakScrollBar { handleColor: window.c.BG_SCROLL }
                onCountChanged: Qt.callLater(() => positionViewAtEnd())
                delegate: RakLabel {
                    required property var modelData
                    width: log.width; height: 18; text: modelData.text; font.family: "Consolas"; font.pixelSize: 12; textFormat: Text.PlainText
                    font.weight: modelData.hint === "section" ? Font.DemiBold : Font.Normal
                    color: modelData.hint === "a" ? "#7eb8f7"
                         : modelData.hint === "b" ? "#6dba8a"
                         : modelData.hint === "error" ? window.c.RED_LIGHT
                         : modelData.hint === "ok" ? window.c.GREEN_STATUS
                         : modelData.hint === "dim" ? window.c.FG_MUTED
                         : modelData.hint === "section" ? window.c.accent_hex
                         : window.c.FG
                }
            }
        }

        // Tab 1: Native Rust Side-by-Side Diff
        Item {
            x: 14; y: 128; width: parent.width - 28; height: parent.height - y - 48
            visible: window.tab === 1

            SmoothRectangle {
                id: diffListCard; width: 215; height: parent.height; radius: 10
                color: window.glassSurface; border.width: 1; border.color: window.c.b08
                ListView {
                    id: differences; objectName: "compareDiffList"
                    anchors.fill: parent; anchors.margins: 6; spacing: 3; clip: true; model: window.backend.diffs
                    C.ScrollBar.vertical: RakScrollBar {
                        width: 6; autoHide: false
                        handleColor: window.c.b30 || window.c.BG_SCROLL
                        hoverColor: window.c.b50
                        downColor: window.c.a55
                    }
                    delegate: RakButton {
                        required property var modelData; required property int index
                        width: differences.width; height: 42; cornerRadius: 7; edgeWidth: window.backend.selected === index ? 1 : 0
                        edgeColor: window.backend.selected === index ? window.c.a35 : "transparent"
                        fillColor: window.backend.selected === index ? window.c.a18 : "transparent"
                        hoverFillColor: window.c.b05
                        onClicked: window.backend.select(index)
                        contentItem: Item {
                            Image {
                                x: 8; anchors.verticalCenter: parent.verticalCenter
                                width: 18; height: 18
                                sourceSize: Qt.size(36, 36)
                                fillMode: Image.PreserveAspectFit; smooth: true
                                source: window.workspace && window.workspace.fileIconUrl
                                        ? window.workspace.fileIconUrl(modelData.icon || "file", window.preferences.paletteName)
                                        : ""
                            }
                            RakLabel {
                                x: 32; y: 5; width: parent.width - 38; height: 16
                                text: modelData.name || modelData.path
                                font.pixelSize: 11; font.weight: window.backend.selected === index ? Font.DemiBold : Font.Medium
                                elide: Text.ElideMiddle; color: window.c.FG
                            }
                            RakLabel {
                                x: 32; y: 22; width: parent.width - 38; height: 14
                                text: (modelData.dir ? modelData.dir + "  ·  " : "") + window.formatBytes(modelData.sizeA) + " → " + window.formatBytes(modelData.sizeB)
                                font.pixelSize: 10; elide: Text.ElideMiddle; color: window.c.FG_MUTED
                            }
                        }
                    }
                }
            }

            SmoothRectangle {
                x: diffListCard.width + 8; width: parent.width - x; height: parent.height; radius: 10
                color: window.glassSurface; border.width: 1; border.color: window.c.b08; clip: true

                Item {
                    id: diffHeader; width: parent.width
                    visible: window.backend.selectedName.length > 0
                    height: visible ? 30 : 0
                    RakLabel {
                        x: 12; width: parent.width - 24; height: parent.height
                        text: window.backend.selectedName
                        elide: Text.ElideMiddle; font.family: "Consolas"; font.pixelSize: 11; color: window.c.FG
                    }
                    SmoothRectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: window.c.b08 }
                }

                Item {
                    id: diffSideBar; y: diffHeader.height; width: parent.width; height: 22
                    Row {
                        x: 10; height: parent.height; width: Math.floor((parent.width - 1) / 2) - 10; spacing: 6
                        Rectangle { width: 6; height: 6; radius: 3; anchors.verticalCenter: parent.verticalCenter; color: "#7eb8f7" }
                        RakLabel { anchors.verticalCenter: parent.verticalCenter; text: "A · " + (window.backend.nameA || "Side A"); font.pixelSize: 10; font.weight: Font.Medium; color: window.c.FG2 }
                    }
                    SmoothRectangle { x: Math.floor((parent.width - 1) / 2); width: 1; height: parent.height; color: window.c.b08 }
                    Row {
                        x: Math.floor((parent.width - 1) / 2) + 11; height: parent.height; width: parent.width - x - 10; spacing: 6
                        Rectangle { width: 6; height: 6; radius: 3; anchors.verticalCenter: parent.verticalCenter; color: "#6dba8a" }
                        RakLabel { anchors.verticalCenter: parent.verticalCenter; text: "B · " + (window.backend.nameB || "Side B"); font.pixelSize: 10; font.weight: Font.Medium; color: window.c.FG2 }
                    }
                    SmoothRectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: window.c.b08 }
                }

                Item {
                    id: diffWeb; objectName: "compareDiffWeb"
                    y: diffHeader.height + diffSideBar.height; width: parent.width; height: parent.height - y
                    visible: window.backend.web.active
                    property bool syncingScroll: false
                    readonly property bool lightTheme: window.c && window.c.is_light !== undefined ? window.c.is_light : false
                    readonly property color diffFill: "transparent"
                    readonly property color diffSel: (window.c && window.c.accent_hex) ? window.c.accent_hex : (lightTheme ? "#4d8ef0" : "#3ecf8e")

                    NativeDiffEditor {
                        id: diffPaneA; objectName: "compareDiffPaneA"
                        x: 0; y: 0; width: Math.floor((parent.width - 1) / 2); height: parent.height
                        isLight: diffWeb.lightTheme
                        fillColor: diffWeb.diffFill
                        gutterColor: diffWeb.diffFill
                        textColor: window.c.FG
                        lineNumberColor: window.c.FG_MUTED
                        activeLineNumberColor: window.c.FG
                        cursorColor: window.c.accent_hex
                        selectionColor: Qt.rgba(diffWeb.diffSel.r, diffWeb.diffSel.g, diffWeb.diffSel.b, diffWeb.lightTheme ? 0.28 : 0.35)
                        gutterDividerColor: window.c.b08
                        diffData: window.backend.web.sideA
                        onScrollYChanged: {
                            if (diffWeb.syncingScroll) return
                            diffWeb.syncingScroll = true
                            diffPaneB.syncViewport(scrollX, scrollY)
                            diffWeb.syncingScroll = false
                        }
                        onScrollXChanged: {
                            if (diffWeb.syncingScroll) return
                            diffWeb.syncingScroll = true
                            diffPaneB.syncViewport(scrollX, scrollY)
                            diffWeb.syncingScroll = false
                        }
                    }
                    SmoothRectangle {
                        x: diffPaneA.width; y: 0; width: 1; height: parent.height
                        color: window.c.b08
                    }
                    NativeDiffEditor {
                        id: diffPaneB; objectName: "compareDiffPaneB"
                        x: diffPaneA.width + 1; y: 0; width: Math.max(1, parent.width - x); height: parent.height
                        isLight: diffWeb.lightTheme
                        fillColor: diffWeb.diffFill
                        gutterColor: diffWeb.diffFill
                        textColor: window.c.FG
                        lineNumberColor: window.c.FG_MUTED
                        activeLineNumberColor: window.c.FG
                        cursorColor: window.c.accent_hex
                        selectionColor: Qt.rgba(diffWeb.diffSel.r, diffWeb.diffSel.g, diffWeb.diffSel.b, diffWeb.lightTheme ? 0.28 : 0.35)
                        gutterDividerColor: window.c.b08
                        diffData: window.backend.web.sideB
                        onScrollYChanged: {
                            if (diffWeb.syncingScroll) return
                            diffWeb.syncingScroll = true
                            diffPaneA.syncViewport(scrollX, scrollY)
                            diffWeb.syncingScroll = false
                        }
                        onScrollXChanged: {
                            if (diffWeb.syncingScroll) return
                            diffWeb.syncingScroll = true
                            diffPaneA.syncViewport(scrollX, scrollY)
                            diffWeb.syncingScroll = false
                        }
                    }

                    // Diff markers + visible track behind portrait scroll hint
                    SmoothRectangle {
                        id: diffScrollTrack
                        anchors.fill: diffVScroll
                        visible: diffVScroll.visible
                        radius: 4; color: window.c.b05; border.width: 1; border.color: window.c.b08
                        z: 19
                        Repeater {
                            model: diffPaneA.diffMarkers
                            delegate: Rectangle {
                                required property var modelData
                                x: 2; width: Math.max(2, diffScrollTrack.width - 4)
                                y: Math.max(1, Math.min(diffScrollTrack.height - 4, (modelData.row * 20 / Math.max(1, diffPaneA.totalHeight)) * diffScrollTrack.height))
                                height: Math.max(3, Math.min(diffScrollTrack.height - y - 1, (modelData.count * 20 / Math.max(1, diffPaneA.totalHeight)) * diffScrollTrack.height))
                                radius: 1
                                color: diffWeb.lightTheme ? "#d92d20" : "#f85149"
                                opacity: 0.72
                            }
                        }
                    }

                    // Clickable & draggable portrait (vertical) scroll hint (syncs A & B)
                    RakScrollBar {
                        id: diffVScroll; objectName: "compareDiffVScroll"
                        anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.right: parent.right
                        anchors.topMargin: 3; anchors.bottomMargin: 3; anchors.rightMargin: 2
                        width: 9; z: 20; orientation: Qt.Vertical
                        autoHide: false
                        minimumSize: 0.10
                        handleColor: window.c.b35 || window.c.BG_SCROLL
                        hoverColor: window.c.b55 || Qt.rgba(1, 1, 1, 0.55)
                        downColor: window.c.a65 || window.c.accent_hex
                        Binding on position {
                            when: !diffVScroll.pressed
                            value: diffPaneA.totalHeight > diffPaneA.height
                                   ? (diffPaneA.scrollY / Math.max(1, diffPaneA.totalHeight - diffPaneA.height)) * Math.max(0.0001, 1.0 - diffVScroll.size)
                                   : 0
                        }
                        size: diffPaneA.totalHeight > diffPaneA.height
                              ? Math.max(0.10, diffPaneA.height / diffPaneA.totalHeight)
                              : 1
                        onPositionChanged: {
                            if (pressed && diffPaneA.totalHeight > diffPaneA.height) {
                                var ratio = Math.max(0, Math.min(1, position / Math.max(0.0001, 1.0 - size)))
                                var targetY = ratio * Math.max(0, diffPaneA.totalHeight - diffPaneA.height)
                                diffWeb.syncingScroll = true
                                diffPaneA.syncViewport(diffPaneA.scrollX, targetY)
                                diffPaneB.syncViewport(diffPaneB.scrollX, targetY)
                                diffWeb.syncingScroll = false
                            }
                        }
                    }

                    // Clickable & draggable horizontal scroll hints per pane
                    RakScrollBar {
                        id: diffHScrollA; objectName: "compareDiffHScrollA"
                        anchors.left: diffPaneA.left; anchors.right: diffPaneA.right; anchors.bottom: diffPaneA.bottom
                        anchors.leftMargin: 2; anchors.rightMargin: 4; anchors.bottomMargin: 2
                        height: 7; z: 20; orientation: Qt.Horizontal
                        autoHide: false
                        minimumSize: 0.10
                        handleColor: window.c.b30 || window.c.BG_SCROLL
                        hoverColor: window.c.b50 || Qt.rgba(1, 1, 1, 0.45)
                        downColor: window.c.a55 || window.c.accent_hex
                        Binding on position {
                            when: !diffHScrollA.pressed
                            value: diffPaneA.totalWidth > diffPaneA.width
                                   ? (diffPaneA.scrollX / Math.max(1, diffPaneA.totalWidth - diffPaneA.width)) * Math.max(0.0001, 1.0 - diffHScrollA.size)
                                   : 0
                        }
                        size: diffPaneA.totalWidth > diffPaneA.width
                              ? Math.max(0.10, diffPaneA.width / diffPaneA.totalWidth)
                              : 1
                        onPositionChanged: {
                            if (pressed && diffPaneA.totalWidth > diffPaneA.width) {
                                var ratio = Math.max(0, Math.min(1, position / Math.max(0.0001, 1.0 - size)))
                                var targetX = ratio * Math.max(0, diffPaneA.totalWidth - diffPaneA.width)
                                diffWeb.syncingScroll = true
                                diffPaneA.syncViewport(targetX, diffPaneA.scrollY)
                                diffPaneB.syncViewport(targetX, diffPaneA.scrollY)
                                diffWeb.syncingScroll = false
                            }
                        }
                    }
                    RakScrollBar {
                        id: diffHScrollB; objectName: "compareDiffHScrollB"
                        anchors.left: diffPaneB.left; anchors.right: diffPaneB.right; anchors.bottom: diffPaneB.bottom
                        anchors.leftMargin: 2; anchors.rightMargin: 13; anchors.bottomMargin: 2
                        height: 7; z: 20; orientation: Qt.Horizontal
                        autoHide: false
                        minimumSize: 0.10
                        handleColor: window.c.b30 || window.c.BG_SCROLL
                        hoverColor: window.c.b50 || Qt.rgba(1, 1, 1, 0.45)
                        downColor: window.c.a55 || window.c.accent_hex
                        Binding on position {
                            when: !diffHScrollB.pressed
                            value: diffPaneB.totalWidth > diffPaneB.width
                                   ? (diffPaneB.scrollX / Math.max(1, diffPaneB.totalWidth - diffPaneB.width)) * Math.max(0.0001, 1.0 - diffHScrollB.size)
                                   : 0
                        }
                        size: diffPaneB.totalWidth > diffPaneB.width
                              ? Math.max(0.10, diffPaneB.width / diffPaneB.totalWidth)
                              : 1
                        onPositionChanged: {
                            if (pressed && diffPaneB.totalWidth > diffPaneB.width) {
                                var ratio = Math.max(0, Math.min(1, position / Math.max(0.0001, 1.0 - size)))
                                var targetX = ratio * Math.max(0, diffPaneB.totalWidth - diffPaneB.width)
                                diffWeb.syncingScroll = true
                                diffPaneA.syncViewport(targetX, diffPaneB.scrollY)
                                diffPaneB.syncViewport(targetX, diffPaneB.scrollY)
                                diffWeb.syncingScroll = false
                            }
                        }
                    }
                }
            }
        }

        // Footer
        SmoothRectangle { anchors.bottom: footer.top; width: parent.width; height: 1; color: window.c.b08 }
        Item {
            id: footer; anchors.bottom: parent.bottom; width: parent.width; height: 44
            RakLabel {
                x: 16; width: parent.width - 210; height: parent.height
                text: window.backend.status
                font.pixelSize: 11; elide: Text.ElideRight; color: window.c.FG_MUTED
            }
            Row {
                anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 8
                DialogButton { objectName: "compareSave"; colors: window.c; text: window.s.cmp_btn_save; enabled: !window.backend.busy; onClicked: window.backend.requestSave() }
                DialogButton { colors: window.c; text: window.s.cmp_btn_hide; onClicked: window.backend.hide() }
            }
        }
    }

    MessageWindow { id: confirmation; model: window.preferences; hostWindow: window.hostWindow; commandTarget: window.backend }
    DialogWindow {
        id: saveDialog; objectName: "compareSaveDialog"; model: window.preferences; hostWindow: window.hostWindow
        property string mode: "log"; property string reference: "a"
        implicitWidth: 400; implicitHeight: 280
        Column {
            x: 20; y: 18; width: parent.width-40; spacing: 12
            RakLabel { text: window.s.cmp_save_what; font.pixelSize: 13; font.weight: Font.DemiBold; color: window.c.FG }
            Row { spacing: 6; AndroidToolButton { colors: window.c; selected: saveDialog.mode==="log"; text: window.s.cmp_mode_log; onClicked: saveDialog.mode="log" } AndroidToolButton { colors: window.c; selected: saveDialog.mode==="automation"; text: window.s.cmp_mode_auto; onClicked: saveDialog.mode="automation" } }
            RakLabel { width: parent.width; text: saveDialog.mode==="automation"?window.s.cmp_auto_hint:window.s.cmp_save_log_title; wrapMode: Text.WordWrap; font.pixelSize: 11; color: window.c.FG_MUTED }
            RakLabel { visible: saveDialog.mode==="automation"; text: window.s.cmp_which_new; font.pixelSize: 11; color: window.c.FG }
            Row { visible: saveDialog.mode==="automation"; spacing: 6; AndroidToolButton { colors: window.c; text: "A · "+window.backend.nameA; selected: saveDialog.reference==="a"; onClicked: saveDialog.reference="a" } AndroidToolButton { colors: window.c; text: "B · "+window.backend.nameB; selected: saveDialog.reference==="b"; onClicked: saveDialog.reference="b" } }
            Row { anchors.right: parent.right; spacing: 8; DialogButton { colors: window.c; text: window.s.cmp_btn_cancel; onClicked: saveDialog.dismiss() } DialogButton { objectName: "compareSaveConfirm"; colors: window.c; primary: true; text: window.s.cmp_btn_save; onClicked: {saveDialog.dismiss();if(window.backend.sideA.dual)destinationDialog.open();else window.backend.save(saveDialog.mode,saveDialog.reference,0)} } }
        }
    }
    DialogWindow {
        id: destinationDialog; model: window.preferences; hostWindow: window.hostWindow; implicitWidth: 320; implicitHeight: 172
        Column {
            x: 20; y: 18; width: parent.width-40; spacing: 12
            RakLabel { text: window.s.cmp_choose_save; font.pixelSize: 13; font.weight: Font.DemiBold; color: window.c.FG }
            DialogButton { colors: window.c; width: parent.width; text: workspace.androidTools.trees[0]; onClicked: {destinationDialog.dismiss();window.backend.save(saveDialog.mode,saveDialog.reference,0)} }
            DialogButton { colors: window.c; width: parent.width; text: workspace.androidTools.trees[1] || ""; onClicked: {destinationDialog.dismiss();window.backend.save(saveDialog.mode,saveDialog.reference,1)} }
            DialogButton { colors: window.c; text: window.s.cmp_btn_cancel; onClicked: destinationDialog.dismiss() }
        }
    }
    Connections {
        target: backend
        function onChanged() { window.syncVisibility() }
        function onSaveRequested() { saveDialog.open() }
        function onCloseRequested(data) { confirmation.showMessage(data) }
    }
}
