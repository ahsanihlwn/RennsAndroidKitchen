// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import Renns.Workspace

Item {
    id: viewer
    objectName: "workspaceMarkdownViewer"
    required property var workspace
    required property var preferences
    required property var details
    readonly property var c: preferences.colors
    readonly property var documentModel: document
    readonly property bool anims: preferences && preferences.settings ? preferences.settings.animations : true
    property bool outlineOpen: false
    property int lockRevision: 0

    readonly property bool isLocked: {
        lockRevision
        const p = details.path || ""
        const dp = details.documentPath || p
        return !!(details.readOnly
            || (workspace && typeof workspace.isFileReadOnly === "function" && (workspace.isFileReadOnly(p) || workspace.isFileReadOnly(dp))))
    }

    function reloadDocument() {
        const targetPath = details.documentPath || details.path || ""
        if (!targetPath) return
        let liveText = ""
        if (workspace && typeof workspace.markdownSource === "function") {
            liveText = workspace.markdownSource(details.path || targetPath) || ""
        }
        document.load(targetPath, viewer.c, liveText)
    }

    function openHref(link) {
        const href = String(link || "").trim()
        if (!href) return
        if (href.startsWith("#")) {
            const target = href.slice(1).toLowerCase()
            const hs = document.headings || []
            for (let i = 0; i < hs.length; ++i) {
                if (String(hs[i].anchor || "").toLowerCase() === target) {
                    blockList.positionViewAtIndex(hs[i].blockIndex, ListView.Beginning)
                    return
                }
            }
            return
        }
        if (href.startsWith("http://") || href.startsWith("https://") || href.startsWith("mailto:")) {
            Qt.openUrlExternally(href)
        }
    }

    MarkdownDocument { id: document }

    Component.onCompleted: reloadDocument()
    Component.onDestruction: document.close()
    onDetailsChanged: reloadDocument()

    Connections {
        target: viewer.preferences
        function onColorsChanged() { document.refreshTheme(viewer.c) }
    }
    Connections {
        target: viewer.workspace
        function onLocksChanged() { viewer.lockRevision++ }
        function onTabsChanged() {
            if (viewer.visible && viewer.details && viewer.details.kind === "markdown") {
                viewer.reloadDocument()
            }
        }
    }

    SmoothRectangle { anchors.fill: parent; color: viewer.c.BG2 }

    // Top Header Bar
    Item {
        id: headerBar
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        height: 40
        SmoothRectangle { anchors.fill: parent; color: viewer.c.BG }
        SmoothRectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: viewer.c.b08 }

        Row {
            anchors.left: parent.left; anchors.leftMargin: 10; anchors.verticalCenter: parent.verticalCenter
            spacing: 8

            SmoothRectangle {
                height: 22; width: badgeLbl.implicitWidth + 14; radius: 6
                color: viewer.c.a14 || viewer.c.b06
                border.width: 1; border.color: viewer.c.a30 || viewer.c.b10
                anchors.verticalCenter: parent.verticalCenter
                RakLabel {
                    id: badgeLbl
                    anchors.centerIn: parent
                    text: "MD"
                    font.pixelSize: 10; font.weight: Font.Bold
                    color: viewer.c.accent_hex
                }
            }

            ImageToolButton {
                objectName: "markdownOutlineToggle"
                visible: (document.headings || []).length > 0
                colors: viewer.c; height: 26
                iconKey: "sidebar"; iconWidth: 14; iconHeight: 14
                text: "Outline"
                selected: viewer.outlineOpen
                onClicked: viewer.outlineOpen = !viewer.outlineOpen
            }

            RakLabel {
                anchors.verticalCenter: parent.verticalCenter
                text: document.ready
                    ? ((document.stats.words || 0) + " words  ·  " + (document.stats.readMinutes || 0) + " min read")
                    : "Rendering..."
                font.pixelSize: 11; color: viewer.c.FG_MUTED
            }
        }

        Row {
            anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
            spacing: 8

            Spinner {
                visible: document.busy; spinning: document.busy
                width: 16; height: 16; thickness: 2; inset: 2
                accent: viewer.c.accent_hex
                anchors.verticalCenter: parent.verticalCenter
            }

            SmoothRectangle {
                objectName: "markdownLockedPill"
                visible: viewer.isLocked
                height: 24; width: lockRow.implicitWidth + 14; radius: 6
                color: viewer.c.b05; border.width: 1; border.color: viewer.c.b12
                anchors.verticalCenter: parent.verticalCenter
                Row {
                    id: lockRow
                    anchors.centerIn: parent; spacing: 5
                    WorkspaceNativeIcon {
                        anchors.verticalCenter: parent.verticalCenter
                        iconKey: "lock"; iconSize: 12
                        strokeColor: viewer.c.FG2
                        isLight: !!(viewer.c && viewer.c.is_light)
                    }
                    RakLabel {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "Read-only"
                        font.pixelSize: 11; color: viewer.c.FG2
                    }
                }
            }

            ImageToolButton {
                objectName: "markdownCopySource"
                colors: viewer.c; height: 28
                text: "Copy"
                enabled: document.ready && document.source.length > 0
                onClicked: if (viewer.workspace && document.source) viewer.workspace.copyText(document.source)
            }

            ImageToolButton {
                objectName: "markdownEditToggle"
                colors: viewer.c; height: 28
                iconKey: "edit"; iconWidth: 15; iconHeight: 15
                text: "Edit"
                onClicked: {
                    if (viewer.workspace && typeof viewer.workspace.setMarkdownEditMode === "function") {
                        viewer.workspace.setMarkdownEditMode(viewer.details.path, true)
                    }
                }
            }
        }
    }

    // Body area: Optional Outline + Rendered Markdown Blocks
    Item {
        anchors.top: headerBar.bottom; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom

        // Collapsible Outline Sidebar
        SmoothRectangle {
            id: outlinePanel
            objectName: "markdownOutlinePanel"
            anchors.top: parent.top; anchors.bottom: parent.bottom; anchors.left: parent.left
            width: viewer.outlineOpen ? Math.min(220, parent.width * 0.32) : 0
            visible: width > 1
            clip: true
            color: viewer.c.BG
            Behavior on width { NumberAnimation { duration: viewer.anims ? 160 : 0; easing.type: Easing.OutCubic } }

            SmoothRectangle { anchors.right: parent.right; width: 1; height: parent.height; color: viewer.c.b08 }

            ListView {
                anchors.fill: parent; anchors.margins: 8; spacing: 2; clip: true
                model: document.headings || []
                C.ScrollBar.vertical: RakScrollBar { width: 4; handleColor: viewer.c.b28; hoverColor: viewer.c.b45 }
                delegate: RakButton {
                    required property var modelData
                    width: ListView.view.width; height: 28; cornerRadius: 5; edgeWidth: 0
                    leftAligned: true
                    leftPadding: 8 + Math.max(0, (modelData.level || 1) - 1) * 10
                    rightPadding: 8
                    fillColor: "transparent"; hoverFillColor: viewer.c.b06; downFillColor: viewer.c.b10
                    labelColor: modelData.level <= 2 ? viewer.c.FG_BRIGHT : viewer.c.FG2
                    hoverLabelColor: viewer.c.accent_hex
                    font.pixelSize: modelData.level === 1 ? 12 : 11
                    font.weight: modelData.level <= 2 ? Font.DemiBold : Font.Normal
                    text: modelData.text || ""
                    onClicked: blockList.positionViewAtIndex(modelData.blockIndex || 0, ListView.Beginning)
                }
            }
        }

        // Error state
        RakLabel {
            anchors.centerIn: parent
            visible: !document.busy && document.error.length > 0
            width: Math.min(parent.width - 48, 480)
            wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
            color: viewer.c.RED_LIGHT || "#f87171"; font.pixelSize: 12
            text: document.error
        }

        // Main rendered Markdown blocks
        ListView {
            id: blockList
            objectName: "markdownBlockList"
            anchors.top: parent.top; anchors.bottom: parent.bottom
            anchors.left: outlinePanel.right; anchors.right: parent.right
            clip: true
            topMargin: 20; bottomMargin: 28
            spacing: 10
            cacheBuffer: 900
            boundsBehavior: Flickable.StopAtBounds
            visible: !document.error.length
            model: document.blocks

            C.ScrollBar.vertical: RakScrollBar {
                width: 6
                handleColor: viewer.c.b28 || "#44ffffff"
                hoverColor: viewer.c.b45 || "#77ffffff"
                downColor: viewer.c.accent_hex
            }

            delegate: Item {
                id: blockRow
                required property var modelData
                width: blockList.width
                readonly property real proseWidth: Math.max(200, Math.min(width - 48, 860))
                readonly property real proseX: Math.round((width - proseWidth) / 2)
                readonly property string kind: modelData.kind || "paragraph"
                implicitHeight: blockLoader.item ? blockLoader.item.implicitHeight : 0
                height: implicitHeight

                Loader {
                    id: blockLoader
                    x: blockRow.proseX
                    width: blockRow.proseWidth
                    sourceComponent: {
                        switch (blockRow.kind) {
                        case "heading": return headingComp
                        case "code": return codeComp
                        case "blockquote": return quoteComp
                        case "list": return listComp
                        case "table": return tableComp
                        case "hr": return hrComp
                        case "image": return imageComp
                        default: return paraComp
                        }
                    }
                    onLoaded: if (item) item.block = Qt.binding(function() { return blockRow.modelData })
                }
            }
        }
    }

    // ── Block Components ─────────────────────────────────────────────────────

    Component {
        id: headingComp
        Column {
            property var block: ({})
            readonly property int lvl: block.level || 1
            width: parent ? parent.width : 0
            topPadding: lvl === 1 ? 8 : (lvl === 2 ? 6 : 3)
            spacing: 6

            TextEdit {
                width: parent.width
                readOnly: true; selectByMouse: true
                textFormat: TextEdit.RichText
                wrapMode: TextEdit.Wrap
                font.family: "Poppins"
                font.pixelSize: lvl === 1 ? 24 : lvl === 2 ? 19 : lvl === 3 ? 16 : lvl === 4 ? 14 : lvl === 5 ? 13 : 12
                font.weight: Font.DemiBold
                color: viewer.c.FG_BRIGHT
                selectionColor: viewer.c.a35 || "#4438bdf8"
                selectedTextColor: viewer.c.FG_BRIGHT
                text: block.html || ""
                onLinkActivated: link => viewer.openHref(link)
            }
            SmoothRectangle {
                visible: parent.lvl <= 2
                width: parent.width; height: 1
                color: viewer.c.b10
            }
        }
    }

    Component {
        id: paraComp
        TextEdit {
            property var block: ({})
            width: parent ? parent.width : 0
            readOnly: true; selectByMouse: true
            textFormat: TextEdit.RichText
            wrapMode: TextEdit.Wrap
            font.family: "Poppins"
            font.pixelSize: 13
            color: viewer.c.FG
            selectionColor: viewer.c.a35 || "#4438bdf8"
            selectedTextColor: viewer.c.FG_BRIGHT
            text: block.html || ""
            onLinkActivated: link => viewer.openHref(link)
        }
    }

    Component {
        id: codeComp
        SmoothRectangle {
            id: codeCard
            property var block: ({})
            width: parent ? parent.width : 0
            implicitHeight: 32 + codeFlick.height + 16
            height: implicitHeight
            radius: 8
            color: viewer.c.BG_DARK || viewer.c.BG
            border.width: 1; border.color: viewer.c.b08

            Item {
                id: codeHdr
                x: 0; y: 0; width: parent.width; height: 30
                SmoothRectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: viewer.c.b06 }
                RakLabel {
                    x: 12; anchors.verticalCenter: parent.verticalCenter
                    text: (codeCard.block.language || "code").toLowerCase()
                    font.family: "JetBrains Mono"
                    font.pixelSize: 10; font.weight: Font.DemiBold
                    color: viewer.c.FG_MUTED
                }
                RakButton {
                    anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter
                    height: 22; cornerRadius: 5; edgeWidth: 0
                    leftPadding: 8; rightPadding: 8
                    text: copied ? "Copied" : "Copy"
                    property bool copied: false
                    font.pixelSize: 10
                    fillColor: "transparent"; hoverFillColor: viewer.c.b08; downFillColor: viewer.c.b12
                    labelColor: copied ? viewer.c.accent_hex : viewer.c.FG_MUTED
                    hoverLabelColor: viewer.c.FG_BRIGHT
                    Timer { id: resetCopy; interval: 1200; onTriggered: parent.copied = false }
                    onClicked: {
                        if (viewer.workspace && codeCard.block.code !== undefined) {
                            viewer.workspace.copyText(codeCard.block.code)
                            copied = true
                            resetCopy.restart()
                        }
                    }
                }
            }

            Flickable {
                id: codeFlick
                x: 12; y: 38
                width: Math.max(10, parent.width - 24)
                height: Math.max(20, codeBody.implicitHeight)
                contentWidth: Math.max(width, codeBody.implicitWidth)
                contentHeight: codeBody.implicitHeight
                clip: true; boundsBehavior: Flickable.StopAtBounds
                C.ScrollBar.horizontal: RakScrollBar { height: 5; orientation: Qt.Horizontal; handleColor: viewer.c.b28; hoverColor: viewer.c.b45 }

                TextEdit {
                    id: codeBody
                    readOnly: true; selectByMouse: true
                    textFormat: TextEdit.RichText
                    wrapMode: TextEdit.NoWrap
                    font.family: "JetBrains Mono"
                    font.pixelSize: 12
                    color: viewer.c.FG_BRIGHT
                    selectionColor: viewer.c.a35 || "#4438bdf8"
                    selectedTextColor: viewer.c.FG_BRIGHT
                    text: "<pre style=\"margin:0; font-family:'JetBrains Mono','Cascadia Mono','Consolas';\">" + (codeCard.block.html || "") + "</pre>"
                }
            }
        }
    }

    Component {
        id: quoteComp
        SmoothRectangle {
            id: quoteCard
            property var block: ({})
            width: parent ? parent.width : 0
            implicitHeight: quoteText.implicitHeight + 16
            height: implicitHeight
            radius: 6
            color: viewer.c.b03 || "transparent"

            SmoothRectangle {
                x: 0; y: 2; width: 3; height: parent.height - 4; radius: 1.5
                color: viewer.c.accent_hex
            }
            TextEdit {
                id: quoteText
                x: 14; y: 8; width: Math.max(40, parent.width - 22)
                readOnly: true; selectByMouse: true
                textFormat: TextEdit.RichText
                wrapMode: TextEdit.Wrap
                font.family: "Poppins"
                font.pixelSize: 13
                color: viewer.c.FG2
                selectionColor: viewer.c.a35 || "#4438bdf8"
                selectedTextColor: viewer.c.FG_BRIGHT
                text: quoteCard.block.html || ""
                onLinkActivated: link => viewer.openHref(link)
            }
        }
    }

    Component {
        id: listComp
        Column {
            id: listCol
            property var block: ({})
            width: parent ? parent.width : 0
            spacing: 5
            Repeater {
                model: listCol.block.items || []
                delegate: Item {
                    id: liRow
                    required property var modelData
                    readonly property real indentPx: Math.min(4, modelData.depth || 0) * 20
                    width: listCol.width
                    implicitHeight: Math.max(20, liText.implicitHeight)
                    height: implicitHeight

                    // Task checkbox or bullet/number marker
                    Item {
                        x: liRow.indentPx + 4; y: 2; width: 18; height: 18
                        SmoothRectangle {
                            visible: liRow.modelData.checked !== null && liRow.modelData.checked !== undefined
                            anchors.centerIn: parent; width: 14; height: 14; radius: 4
                            color: liRow.modelData.checked ? viewer.c.accent_hex : "transparent"
                            border.width: 1.3
                            border.color: liRow.modelData.checked ? viewer.c.accent_hex : viewer.c.FG_MUTED
                            RakLabel {
                                anchors.centerIn: parent
                                visible: !!liRow.modelData.checked
                                text: "✓"; font.pixelSize: 10; font.weight: Font.Bold
                                color: "#ffffff"
                            }
                        }
                        RakLabel {
                            visible: liRow.modelData.checked === null || liRow.modelData.checked === undefined
                            anchors.centerIn: parent
                            text: liRow.modelData.marker || "•"
                            font.family: "Poppins"; font.pixelSize: liRow.modelData.ordered ? 12 : 14
                            font.weight: Font.DemiBold
                            color: viewer.c.accent_hex
                        }
                    }

                    TextEdit {
                        id: liText
                        x: liRow.indentPx + 26
                        width: Math.max(40, liRow.width - x)
                        readOnly: true; selectByMouse: true
                        textFormat: TextEdit.RichText
                        wrapMode: TextEdit.Wrap
                        font.family: "Poppins"
                        font.pixelSize: 13
                        color: viewer.c.FG
                        selectionColor: viewer.c.a35 || "#4438bdf8"
                        selectedTextColor: viewer.c.FG_BRIGHT
                        text: liRow.modelData.html || ""
                        onLinkActivated: link => viewer.openHref(link)
                    }
                }
            }
        }
    }

    Component {
        id: tableComp
        SmoothRectangle {
            id: tblCard
            property var block: ({})
            readonly property var headers: block.headersHtml || []
            readonly property var alignList: block.alignments || []
            readonly property var bodyRows: block.rows || []
            readonly property int colCount: Math.max(1, headers.length)
            readonly property real colWidth: Math.max(120, Math.floor((width - 2) / colCount))
            width: parent ? parent.width : 0
            implicitHeight: tblCol.implicitHeight + 2
            height: implicitHeight
            radius: 8; clip: true
            color: viewer.c.BG
            border.width: 1; border.color: viewer.c.b10

            Flickable {
                anchors.fill: parent; anchors.margins: 1
                contentWidth: Math.max(width, tblCol.implicitWidth)
                contentHeight: tblCol.implicitHeight
                clip: true; boundsBehavior: Flickable.StopAtBounds
                C.ScrollBar.horizontal: RakScrollBar { height: 5; orientation: Qt.Horizontal; handleColor: viewer.c.b28; hoverColor: viewer.c.b45 }

                Column {
                    id: tblCol
                    width: Math.max(tblCard.width - 2, tblCard.colCount * tblCard.colWidth)
                    spacing: 0

                    // Header row
                    SmoothRectangle {
                        width: parent.width; height: Math.max(32, hdrRow.implicitHeight)
                        color: viewer.c.BG4 || viewer.c.b06
                        Row {
                            id: hdrRow
                            width: parent.width
                            Repeater {
                                model: tblCard.headers
                                delegate: Item {
                                    required property string modelData
                                    required property int index
                                    width: Math.max(tblCard.colWidth, tblCol.width / tblCard.colCount)
                                    implicitHeight: Math.max(32, hdrTxt.implicitHeight + 14)
                                    height: implicitHeight
                                    TextEdit {
                                        id: hdrTxt
                                        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                        anchors.leftMargin: 10; anchors.rightMargin: 10
                                        readOnly: true; selectByMouse: true
                                        textFormat: TextEdit.RichText; wrapMode: TextEdit.Wrap
                                        font.family: "Poppins"; font.pixelSize: 12; font.weight: Font.DemiBold
                                        color: viewer.c.FG_BRIGHT
                                        horizontalAlignment: (tblCard.alignList[index] === "center") ? TextEdit.AlignHCenter : (tblCard.alignList[index] === "right") ? TextEdit.AlignRight : TextEdit.AlignLeft
                                        text: modelData || ""
                                    }
                                    SmoothRectangle { visible: index > 0; x: 0; width: 1; height: parent.height; color: viewer.c.b08 }
                                }
                            }
                        }
                        SmoothRectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: viewer.c.b10 }
                    }

                    // Body rows
                    Repeater {
                        model: tblCard.bodyRows
                        delegate: SmoothRectangle {
                            id: rowRect
                            required property var modelData
                            required property int index
                            width: tblCol.width
                            height: Math.max(30, cellsRow.implicitHeight)
                            color: (index % 2 === 1) ? (viewer.c.b02 || "transparent") : "transparent"

                            Row {
                                id: cellsRow
                                width: parent.width
                                Repeater {
                                    model: rowRect.modelData.cellsHtml || []
                                    delegate: Item {
                                        required property string modelData
                                        required property int index
                                        width: Math.max(tblCard.colWidth, tblCol.width / tblCard.colCount)
                                        implicitHeight: Math.max(30, cellTxt.implicitHeight + 12)
                                        height: implicitHeight
                                        TextEdit {
                                            id: cellTxt
                                            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                            anchors.leftMargin: 10; anchors.rightMargin: 10
                                            readOnly: true; selectByMouse: true
                                            textFormat: TextEdit.RichText; wrapMode: TextEdit.Wrap
                                            font.family: "Poppins"; font.pixelSize: 12
                                            color: viewer.c.FG
                                            horizontalAlignment: (tblCard.alignList[index] === "center") ? TextEdit.AlignHCenter : (tblCard.alignList[index] === "right") ? TextEdit.AlignRight : TextEdit.AlignLeft
                                            text: modelData || ""
                                            onLinkActivated: link => viewer.openHref(link)
                                        }
                                        SmoothRectangle { visible: index > 0; x: 0; width: 1; height: parent.height; color: viewer.c.b06 }
                                    }
                                }
                            }
                            SmoothRectangle { visible: index < tblCard.bodyRows.length - 1; anchors.bottom: parent.bottom; width: parent.width; height: 1; color: viewer.c.b06 }
                        }
                    }
                }
            }
        }
    }

    Component {
        id: hrComp
        Item {
            property var block: ({})
            width: parent ? parent.width : 0
            height: 14
            SmoothRectangle {
                anchors.verticalCenter: parent.verticalCenter
                width: parent.width; height: 1
                color: viewer.c.b12
            }
        }
    }

    Component {
        id: imageComp
        SmoothRectangle {
            id: imgWrap
            property var block: ({})
            readonly property string resolvedSrc: document.resolveImageUrl(block.url || "")
            width: parent ? parent.width : 0
            implicitHeight: resolvedSrc.length > 0 ? Math.min(360, Math.max(80, mdImg.implicitHeight + 28)) : 36
            height: implicitHeight
            radius: 8
            color: viewer.c.BG
            border.width: 1; border.color: viewer.c.b08

            Image {
                id: mdImg
                visible: imgWrap.resolvedSrc.length > 0
                anchors.centerIn: parent
                width: Math.min(parent.width - 20, implicitWidth > 0 ? implicitWidth : parent.width - 20)
                height: Math.min(332, implicitHeight > 0 ? implicitHeight : 200)
                fillMode: Image.PreserveAspectFit
                source: imgWrap.resolvedSrc
                smooth: true; asynchronous: true
            }
            RakLabel {
                visible: imgWrap.resolvedSrc.length === 0
                anchors.centerIn: parent
                width: parent.width - 24
                horizontalAlignment: Text.AlignHCenter; elide: Text.ElideMiddle
                font.pixelSize: 12; color: viewer.c.FG_MUTED
                text: "[Image: " + (imgWrap.block.alt || imgWrap.block.url || "") + "]"
            }
        }
    }
}
