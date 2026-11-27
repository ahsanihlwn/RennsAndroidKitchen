// SPDX-License-Identifier: MPL-2.0
import QtQuick
import "../controls"
import "../motion.js" as Motion

Item {
    id: page
    required property var model
    readonly property var c: model.colors
    readonly property var s: model.strings
    readonly property bool glassChrome: headerGlass.rendering
    property var presentationMotion: null
    readonly property real headingOpacity: presentationMotion ? presentationMotion.headerOpacity(page) : 1
    readonly property real headingOffsetY: presentationMotion ? presentationMotion.headerOffsetY(page) : 0
    property alias footer: footer
    property alias createButton: newProject
    property alias importButton: importProject
    readonly property var identityItem: footer.identityItem
    function identityEndpoint(host) { return footer.identityEndpoint(host) }
    readonly property var activeList: page.trash ? trashProjects : projects
    function menuAnchor(path) {
        const list = activeList
        for (let i=0;i<list.count;++i) {
            const card=list.itemAtIndex(i)
            if (card && card.modelData.path===path) return card.menuAnchor
        }
        return null
    }
    property bool trash: false
    property real trashProgress: 0
    onTrashChanged: {
        trashProgressAnim.stop()
        const target = trash ? 1 : 0
        const dur = page.model.settings.animations ? Motion.remainingDuration(200, trashProgress, trash ? 0 : 1, target) : 0
        if (dur <= 0) {
            trashProgress = target
        } else {
            trashProgressAnim.to = target
            trashProgressAnim.duration = dur
            trashProgressAnim.start()
        }
    }
    NumberAnimation { id: trashProgressAnim; target: page; property: "trashProgress"; easing.type: Easing.OutCubic }
    Connections {
        target: page.model
        function onSettingsChanged() {
            if (!page.model.settings.animations) {
                trashProgressAnim.stop()
                page.trashProgress = page.trash ? 1 : 0
            }
        }
    }

    function setFilterMode(mode) {
        if (model.busy) return
        const currentMode = trash ? "trash" : (model.favoriteFilter ? "favorites" : "all")
        if (mode === currentMode) return

        if (projectMotion.active) projectMotion.cancel()

        if (mode === "trash") {
            trash = true
        } else {
            const wantFav = (mode === "favorites")
            if (model.favoriteFilter !== wantFav) {
                model.setFavoriteFilter(wantFav)
            }
            trash = false
        }
    }

    function toggleTrash() { setFilterMode(trash ? "all" : "trash") }
    function toggleFavorites() { setFilterMode((!trash && model.favoriteFilter) ? "all" : "favorites") }
    function clearFilter() { setFilterMode("all") }
    Connections {
        target: page.trash ? page.model.trashRows : page.model.projectRows
        function onAboutToReplace(before,after,animate) {
            const visBefore = (!page.trash && page.model.favoriteFilter) ? before.filter(row => !!row.pinned) : before
            const visAfter = (!page.trash && page.model.favoriteFilter) ? after.filter(row => !!row.pinned) : after
            projectMotion.begin(visBefore, visAfter, animate, page.activeList.model)
        }
        function onReplaced() { page.activeList.forceLayout(); projectMotion.layoutApplied() }
        function onCancelMotion() { projectMotion.cancel() }
    }
    property string driveKeys: model.drives.map(row=>row.key).join(",")
    function restartDriveCounter() { driveClock.start(100+840+Math.max(0,model.drives.length-1)*42) }
    onDriveKeysChanged: restartDriveCounter()
    MotionClock { id: driveClock; objectName: "driveUsageClock" }
    readonly property bool sidebar: driveShelf.sidebar
    RakLabel {
        id: heading
        opacity: page.headingOpacity
        transform: Translate { y: page.headingOffsetY }
        z: 2
        x: 16; y: 16; height: 32
        text: page.s.hdr_my_projects
        font.pixelSize: 22; font.weight: Font.DemiBold; color: page.c.FG
    }
    // DriveShelf is hidden when sidebar layout is active.
    // It is kept as a data source for listX / layout calculations.
    DriveShelf {
        id: driveShelf
        opacity: page.headingOpacity
        enabled: opacity>.99
        z: 2
        anchors.fill: parent
        visible: false  // drives shown in AppSidebar instead
        model: page.model; headingWidth: heading.implicitWidth; trash: page.trash
        usageElapsed: driveClock.elapsed-100
        onTrashClicked: page.toggleTrash()
    }
    RakField {
        id: search
        opacity: page.headingOpacity
        transform: Translate { y: page.headingOffsetY }
        z: 2
        enabled: !page.trash && !page.model.busy && opacity>.99
        objectName: "projectSearch"
        anchors.right: parent.right
        anchors.rightMargin: 16 + marginOffset
        anchors.verticalCenter: filterBar.verticalCenter

        property bool expanded: false
        property real targetWidth: Math.min(260, Math.max(160, page.width / 3.4))
        property real animWidth: 32
        property real marginOffset: 0

        width: Math.max(32, animWidth)
        height: 32
        cornerRadius: 16
        readOnly: !expanded
        cursorVisible: expanded && activeFocus

        placeholderText: page.s.placeholder_search
        font.pixelSize: 11
        leftPadding: expanded ? 34 : 16
        rightPadding: (expanded && text.length > 0) ? 30 : 10

        fillColor: activeFocus ? page.c.BG_INPUT : (searchHover.hovered ? page.c.b06 : page.c.BG2)
        focusFillColor: page.c.BG_INPUT
        edgeColor: activeFocus ? page.c.a45 : (searchHover.hovered ? page.c.b12 : page.c.b08)
        focusEdgeColor: page.c.a45
        hintColor: expanded ? (page.c.placeholder || "#666c80") : "transparent"
        labelColor: expanded ? page.c.FG : "transparent"
        selectionColor: page.c.a35

        Behavior on leftPadding {
            NumberAnimation {
                duration: page.model.settings.animations ? 240 : 0
                easing.type: Easing.OutCubic
            }
        }

        // --- ANIMASI BUKA (EXPAND) ---
        // Kurva Bezier elastis: akselerasi halus, meluncur deras, dan akhiran overshoots super mulus!
        NumberAnimation {
            id: widthExpandAnim
            target: search
            property: "animWidth"
            from: 32
            to: search.targetWidth
            duration: page.model.settings.animations ? 380 : 0
            easing.type: Easing.Bezier
            easing.bezierCurve: [0.3, 1.6, 0.4, 1.0, 1.0, 1.0]
        }

        SequentialAnimation {
            id: expandMarginAnim
            NumberAnimation {
                target: search
                property: "marginOffset"
                to: 12
                duration: 140
                easing.type: Easing.OutQuad
            }
            NumberAnimation {
                target: search
                property: "marginOffset"
                to: 0
                duration: 240
                easing.type: Easing.Bezier
                easing.bezierCurve: [0.3, 1.3, 0.4, 1.0, 1.0, 1.0]
            }
        }

        NumberAnimation {
            id: iconExpandWidthAnim
            target: searchIcon
            property: "width"
            to: 14
            duration: page.model.settings.animations ? 350 : 0
            easing.type: Easing.Bezier
            easing.bezierCurve: [0.3, 1.5, 0.4, 1.0, 1.0, 1.0]
        }

        NumberAnimation {
            id: iconExpandXAnim
            target: searchIcon
            property: "x"
            to: 11
            duration: page.model.settings.animations ? 350 : 0
            easing.type: Easing.Bezier
            easing.bezierCurve: [0.3, 1.5, 0.4, 1.0, 1.0, 1.0]
        }

        // --- ANIMASI TUTUP (COLLAPSE / OUT) ---
        // Kurva Bezier decelerasi sutra: meluncur kembali dan mendarat sangat lembut di 32px tanpa lonjong!
        NumberAnimation {
            id: widthCollapseAnim
            target: search
            property: "animWidth"
            from: search.width
            to: 32
            duration: page.model.settings.animations ? 280 : 0
            easing.type: Easing.Bezier
            easing.bezierCurve: [0.25, 0.1, 0.1, 1.0, 1.0, 1.0]
        }

        NumberAnimation {
            id: iconCollapseWidthAnim
            target: searchIcon
            property: "width"
            to: 18
            duration: page.model.settings.animations ? 260 : 0
            easing.type: Easing.Bezier
            easing.bezierCurve: [0.25, 0.1, 0.1, 1.0, 1.0, 1.0]
        }

        NumberAnimation {
            id: iconCollapseXAnim
            target: searchIcon
            property: "x"
            to: 7
            duration: page.model.settings.animations ? 260 : 0
            easing.type: Easing.Bezier
            easing.bezierCurve: [0.25, 0.1, 0.1, 1.0, 1.0, 1.0]
        }

        SequentialAnimation {
            id: collapseMarginAnim
            NumberAnimation {
                target: search
                property: "marginOffset"
                to: 8
                duration: 120
                easing.type: Easing.OutQuad
            }
            NumberAnimation {
                target: search
                property: "marginOffset"
                to: 0
                duration: 160
                easing.type: Easing.Bezier
                easing.bezierCurve: [0.25, 0.1, 0.1, 1.0, 1.0, 1.0]
            }
        }

        onExpandedChanged: {
            if (page.model.settings.animations) {
                if (expanded) {
                    widthCollapseAnim.stop()
                    iconCollapseWidthAnim.stop()
                    iconCollapseXAnim.stop()
                    collapseMarginAnim.stop()

                    widthExpandAnim.restart()
                    iconExpandWidthAnim.restart()
                    iconExpandXAnim.restart()
                    expandMarginAnim.restart()
                } else {
                    widthExpandAnim.stop()
                    iconExpandWidthAnim.stop()
                    iconExpandXAnim.stop()
                    expandMarginAnim.stop()

                    widthCollapseAnim.restart()
                    iconCollapseWidthAnim.restart()
                    iconCollapseXAnim.restart()
                    collapseMarginAnim.restart()
                }
            } else {
                animWidth = expanded ? targetWidth : 32
                searchIcon.width = expanded ? 14 : 18
                searchIcon.x = expanded ? 11 : 7
                marginOffset = 0
            }
        }

        HoverHandler {
            id: searchHover
            enabled: search.enabled
            cursorShape: search.expanded ? Qt.IBeamCursor : Qt.PointingHandCursor
        }

        MouseArea {
            id: expandClickArea
            anchors.fill: parent
            enabled: search.enabled && !search.expanded
            cursorShape: Qt.PointingHandCursor
            hoverEnabled: true
            onClicked: {
                search.expanded = true
                Qt.callLater(search.forceActiveFocus)
            }
        }

        Image {
            id: searchIcon
            anchors.verticalCenter: parent.verticalCenter
            sourceSize.width: Math.round(width)
            sourceSize.height: Math.round(height)
            width: search.expanded ? 14 : 18
            height: width
            x: search.expanded ? 11 : 7
            source: page.model.icon("project_search", (search.expanded || searchHover.hovered) ? (page.c.accent_hex || page.c.FG) : page.c.FG_MUTED)
        }

        RakButton {
            id: clearBtn
            anchors.right: parent.right
            anchors.rightMargin: 6
            anchors.verticalCenter: parent.verticalCenter
            width: 20; height: 20
            cornerRadius: 10
            visible: search.expanded && search.text.length > 0
            opacity: visible ? 1 : 0
            text: "\u2715"
            font.pixelSize: 10
            labelColor: page.c.FG_MUTED
            hoverLabelColor: page.c.FG
            hoverFillColor: page.c.b10
            onClicked: {
                search.text = ""
                page.model.search("")
                search.focus = false
                search.expanded = false
            }
        }

        onActiveFocusChanged: {
            if (!activeFocus && text.trim().length === 0) {
                expanded = false
            }
        }

        Keys.onEscapePressed: {
            text = ""
            page.model.search("")
            focus = false
            expanded = false
        }

        onTextEdited: page.model.search(text)
    }

    TapHandler {
        target: null
        enabled: search.expanded && search.text.trim().length === 0
        onTapped: (eventPoint) => {
            const p = eventPoint.position
            const sp = search.mapFromItem(page, p.x, p.y)
            if (sp.x < 0 || sp.x > search.width || sp.y < 0 || sp.y > search.height) {
                search.focus = false
                search.expanded = false
            }
        }
    }
    // Filter Pills Bar (Semua, Favorit, Sampah)
    Row {
        id: filterBar
        z: 2
        opacity: page.headingOpacity
        transform: Translate { y: page.headingOffsetY }
        x: 16; y: 60
        spacing: 8

        // Pill: Semua
        RakButton {
            objectName: "allProjectsDrive"
            height: 32
            cornerRadius: 16
            fillColor: (!page.trash && !page.model.favoriteFilter) ? page.c.a14 : page.c.b04
            hoverFillColor: (!page.trash && !page.model.favoriteFilter) ? page.c.a20 : page.c.b08
            downFillColor: page.c.a30
            edgeColor: (!page.trash && !page.model.favoriteFilter) ? page.c.a35 : page.c.b08
            edgeWidth: 1
            animatePress: page.model.settings.animations
            onClicked: page.setFilterMode("all")

            contentItem: Row {
                anchors.centerIn: parent
                spacing: 6
                leftPadding: 10; rightPadding: 10
                Image {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 13; height: 13
                    source: page.model.icon("project_all", (!page.trash && !page.model.favoriteFilter) ? page.c.accent_hex : page.c.FG_MUTED)
                    sourceSize: Qt.size(13, 13)
                }
                RakLabel {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Semua"
                    font.pixelSize: 11; font.weight: (!page.trash && !page.model.favoriteFilter) ? Font.DemiBold : Font.Normal
                    color: (!page.trash && !page.model.favoriteFilter) ? page.c.accent_hex : page.c.FG
                }
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.max(18, allCountLbl.implicitWidth + 8); height: 18; radius: 9
                    color: (!page.trash && !page.model.favoriteFilter) ? page.c.a25 : page.c.b08
                    RakLabel {
                        id: allCountLbl
                        anchors.centerIn: parent
                        anchors.verticalCenterOffset: 1
                        text: (page.model.allProjectsCount !== undefined ? page.model.allProjectsCount : (page.model.projects ? page.model.projects.length : 0)).toString()
                        font.pixelSize: 9; font.weight: Font.Bold
                        color: (!page.trash && !page.model.favoriteFilter) ? page.c.accent_hex : page.c.FG_MUTED
                    }
                }
            }
        }

        // Pill: Favorit (Star / Pinned)
        RakButton {
            objectName: "favoriteDrive"
            height: 32
            cornerRadius: 16
            fillColor: (!page.trash && page.model.favoriteFilter) ? (page.c.is_light ? "#fef3c7" : "#2b2515") : page.c.b04
            hoverFillColor: (!page.trash && page.model.favoriteFilter) ? (page.c.is_light ? "#fde68a" : "#3b331c") : page.c.b08
            downFillColor: page.c.b10
            edgeColor: (!page.trash && page.model.favoriteFilter) ? (page.c.is_light ? "#f59e0b" : "#5a4520") : page.c.b08
            edgeWidth: 1
            animatePress: page.model.settings.animations
            onClicked: page.setFilterMode("favorites")

            contentItem: Row {
                anchors.centerIn: parent
                spacing: 6
                leftPadding: 10; rightPadding: 10
                Image {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 13; height: 13
                    source: page.model.icon("project_favorite", (!page.trash && page.model.favoriteFilter) ? "#f59e0b" : page.c.FG_MUTED)
                    sourceSize: Qt.size(13, 13)
                }
                RakLabel {
                    anchors.verticalCenter: parent.verticalCenter
                    text: page.s.hdr_favorites || "Favorit"
                    font.pixelSize: 11; font.weight: (!page.trash && page.model.favoriteFilter) ? Font.DemiBold : Font.Normal
                    color: (!page.trash && page.model.favoriteFilter) ? "#f59e0b" : page.c.FG
                }
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.max(18, favCountLbl.implicitWidth + 8); height: 18; radius: 9
                    color: (!page.trash && page.model.favoriteFilter) ? (page.c.is_light ? "#fde68a" : "#44341c") : page.c.b08
                    RakLabel {
                        id: favCountLbl
                        anchors.centerIn: parent
                        anchors.verticalCenterOffset: 1
                        text: (page.model.favoriteCount || 0).toString()
                        font.pixelSize: 9; font.weight: Font.Bold
                        color: (!page.trash && page.model.favoriteFilter) ? "#f59e0b" : page.c.FG_MUTED
                    }
                }
            }
        }

        // Pill: Sampah (Trash)
        RakButton {
            objectName: "trashDrive"
            height: 32
            cornerRadius: 16
            fillColor: page.trash ? (page.c.is_light ? "#fee2e2" : "#301a1a") : page.c.b04
            hoverFillColor: page.trash ? (page.c.is_light ? "#fecaca" : "#422323") : page.c.b08
            downFillColor: page.c.b10
            edgeColor: page.trash ? (page.c.is_light ? "#fca5a5" : "#552424") : page.c.b08
            edgeWidth: 1
            animatePress: page.model.settings.animations
            onClicked: page.setFilterMode("trash")

            contentItem: Row {
                anchors.centerIn: parent
                spacing: 6
                leftPadding: 10; rightPadding: 10
                Image {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 13; height: 13
                    source: page.model.icon("project_trash", page.trash ? (page.c.red_alt || "#ef4444") : page.c.FG_MUTED)
                    sourceSize: Qt.size(13, 13)
                }
                RakLabel {
                    anchors.verticalCenter: parent.verticalCenter
                    text: page.s.hdr_trash || "Sampah"
                    font.pixelSize: 11; font.weight: page.trash ? Font.DemiBold : Font.Normal
                    color: page.trash ? (page.c.red_alt || "#ef4444") : page.c.FG
                }
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.max(18, trCountLbl.implicitWidth + 8); height: 18; radius: 9
                    color: page.trash ? (page.c.is_light ? "#fca5a5" : "#4d2020") : page.c.b08
                    RakLabel {
                        id: trCountLbl
                        anchors.centerIn: parent
                        anchors.verticalCenterOffset: 1
                        text: (page.model.trashCount || 0).toString()
                        font.pixelSize: 9; font.weight: Font.Bold
                        color: page.trash ? (page.c.red_alt || "#ef4444") : page.c.FG_MUTED
                    }
                }
            }
        }
    }

    SmoothRectangle {
        id: projectSurface
        readonly property rect inputBounds: Qt.rect(0,headerGlass.height,width,Math.max(0,height-headerGlass.height-footer.height))
        anchors.fill: parent; color: page.c.BG; clip: true
    ListView {
        id: projects
        objectName: "projectList"
        opacity: 1 - page.trashProgress
        visible: opacity > 0.001
        enabled: !page.trash
        x: 16 - page.trashProgress * 14; y: 100; width: Math.max(0, parent.width - 32); height: Math.max(0, parent.height - 100 - footer.height)
        clip: !page.glassChrome; spacing: 0; boundsBehavior: Flickable.StopAtBounds
        displayMarginBeginning: page.glassChrome?100:0
        displayMarginEnd: page.glassChrome?footer.height:0
        model: page.model.projectRows
        interactive: !projectMotion.active
        onContentHeightChanged: {
            if (!dragging && !flicking) {
                const maxScroll = Math.max(0, contentHeight - height)
                if (contentY > maxScroll) contentY = maxScroll
            }
        }
        delegate: Item {
            id: rowDelegate
            required property var modelData
            required property int index
            readonly property var menuAnchor: cardLoader.item ? cardLoader.item.menuAnchor : null
            readonly property real pathOffset: cardLoader.item ? cardLoader.item.pathOffset : 0
            readonly property bool hiddenByFavorite: page.model.favoriteFilter && !modelData.pinned
            readonly property int visIdx: Math.max(0, Math.min(7, Math.floor(Math.max(0, y - projects.contentY) / 74)))
            readonly property real pageItemOpacity: page.presentationMotion ? page.presentationMotion.itemOpacity(page, visIdx) : 1
            readonly property real pageItemOffsetY: page.presentationMotion ? page.presentationMotion.itemOffsetY(page, visIdx) : 0
            property bool everShown: false
            property real reveal: 1
            Component.onCompleted: {
                reveal = hiddenByFavorite ? 0 : 1
                if (!hiddenByFavorite) everShown = true
            }
            onHiddenByFavoriteChanged: {
                if (!hiddenByFavorite) everShown = true
                revealAnim.stop()
                const target = hiddenByFavorite ? 0 : 1
                const dur = (page.model.settings.animations && !page.trash)
                    ? Motion.remainingDuration(190, reveal, hiddenByFavorite ? 1 : 0, target)
                    : 0
                if (dur <= 0) {
                    reveal = target
                } else {
                    revealAnim.to = target
                    revealAnim.duration = dur
                    revealAnim.start()
                }
            }
            NumberAnimation { id: revealAnim; target: rowDelegate; property: "reveal"; easing.type: Easing.OutCubic }
            Connections {
                target: page.model
                function onSettingsChanged() {
                    if (!page.model.settings.animations) {
                        revealAnim.stop()
                        rowDelegate.reveal = rowDelegate.hiddenByFavorite ? 0 : 1
                    }
                }
            }
            visible: reveal > 0.001
            width: projects.width; height: Math.round(74 * reveal)
            clip: reveal < 0.999
            Loader {
                id: cardLoader
                anchors.left: parent.left; anchors.right: parent.right
                y: Math.round((1 - rowDelegate.reveal) * -6)
                height: 66
                active: rowDelegate.everShown
                visible: rowDelegate.reveal > 0.001
                opacity: rowDelegate.reveal * rowDelegate.pageItemOpacity
                transform: [
                    Scale {
                        origin.x: cardLoader.width / 2
                        origin.y: 33
                        xScale: 0.96 + 0.04 * rowDelegate.reveal
                        yScale: 0.96 + 0.04 * rowDelegate.reveal
                    },
                    Translate {
                        y: rowDelegate.pageItemOffsetY
                    }
                ]
                sourceComponent: ProjectCard {
                    model: page.model; trash: false
                    modelData: rowDelegate.modelData; index: rowDelegate.index
                    width: cardLoader.width
                    interactive: !rowDelegate.hiddenByFavorite && !projectMotion.active
                    opacity: projectMotion.active ? 0 : 1
                }
            }
        }
        RakLabel {
            anchors.centerIn: parent; width: parent.width - 32
            opacity: page.headingOpacity
            visible: page.model.favoriteFilter && page.model.visibleProjectsCount === 0 && !projectMotion.active; text: page.s.favorites_empty; color: page.c.FG3
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; font.pixelSize: 11
        }
    }
    ListView {
        id: trashProjects
        objectName: "trashList"
        opacity: page.trashProgress
        visible: opacity > 0.001
        enabled: page.trash
        x: 16 + (1 - page.trashProgress) * 14; y: 100; width: Math.max(0, parent.width - 32); height: Math.max(0, parent.height - 100 - footer.height)
        clip: !page.glassChrome; spacing: 0; boundsBehavior: Flickable.StopAtBounds
        displayMarginBeginning: page.glassChrome?100:0
        displayMarginEnd: page.glassChrome?footer.height:0
        model: page.model.trashRows
        interactive: !projectMotion.active
        delegate: Item {
            required property var modelData
            required property int index
            property alias menuAnchor: card.menuAnchor
            readonly property real pathOffset: card.pathOffset
            readonly property int visIdx: Math.max(0, Math.min(7, Math.floor(Math.max(0, y - trashProjects.contentY) / 74)))
            width: trashProjects.width; height: 74
            opacity: page.presentationMotion ? page.presentationMotion.itemOpacity(page, visIdx) : 1
            transform: Translate { y: page.presentationMotion ? page.presentationMotion.itemOffsetY(page, visIdx) : 0 }
            ProjectCard {
                id: card
                model: page.model; trash: true
                modelData: parent.modelData; index: parent.index
                width: trashProjects.width
                interactive: !projectMotion.active
                opacity: projectMotion.active ? 0 : 1
            }
        }
        RakLabel {
            anchors.centerIn: parent; width: parent.width - 32
            opacity: page.headingOpacity
            visible: trashProjects.count === 0 && !projectMotion.active; text: page.s.trash_empty; color: page.c.FG3
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; font.pixelSize: 11
        }
    }
    ProjectListMotion {
        id: projectMotion
        x: projects.x; y: projects.y; width: projects.width; height: projects.height
        model: page.model; view: page.activeList; trash: page.trash
        clip: !page.glassChrome
    }
    }
    GlassBand { id: headerGlass; objectName: "projectsHeaderGlass"; z: 1; width: parent.width; height: 100; model: page.model; backdropSource: projectSurface; origin: Qt.point(0,0) }
    GlassBand { objectName: "projectsFooterGlass"; z: 1; y: parent.height-footer.height; width: parent.width; height: footer.height; model: page.model; backdropSource: projectSurface; origin: Qt.point(0,y) }
    PageFooter {
        id: footer
        z: 2
        anchors.bottom: parent.bottom; width: parent.width; model: page.model
        transform: Translate { y: page.presentationMotion ? page.presentationMotion.footerOffsetY(page) : 0 }
        ActionButton {
            id: importProject
            objectName: "importButton"
            width: 95; height: 32; colors: page.c; primary: false
            text: page.s.btn_import_project || "Import"
            iconKey: "import"
            iconWidth: 15; iconHeight: 15
            animatePress: page.model.settings.animations
            onClicked: page.model.dispatch("importProject", {})
        }
        ActionButton {
            id: newProject
            objectName: "createButton"
            primary: true
            width: 121; height: 32; colors: page.c; text: page.s.btn_create_project
            iconKey: "plus"
            iconWidth: 15; iconHeight: 15
            animatePress: page.model.settings.animations
            onClicked: page.model.dispatch("newProject", {})
        }
    }
}
