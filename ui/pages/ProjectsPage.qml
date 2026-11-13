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
    readonly property bool motionInBackdrop: true
    readonly property real headingOpacity: presentationMotion ? presentationMotion.opacityAt(page,40) : 1
    property alias footer: footer
    property alias createButton: newProject
    readonly property var identityItem: footer.identityItem
    function identityEndpoint(host) { return footer.identityEndpoint(host) }
    function menuAnchor(path) {
        for (let i=0;i<projects.count;++i) {
            const card=projects.itemAtIndex(i)
            if (card && card.modelData.path===path) return card.menuAnchor
        }
        return null
    }
    function revealBands() {
        let cuts=[0,headerGlass.height], result=[]
        for (let row=0; row<projects.count; ++row) {
            let bottom=projects.y+row*74+66-projects.contentY
            if (bottom>=projects.y && bottom<=projects.y+projects.height) cuts.push(bottom)
        }
        cuts.push(height)
        cuts.sort((a,b)=>a-b)
        for (let i=0;i<cuts.length-1;++i) if(cuts[i+1]>cuts[i]) result.push({top:cuts[i],bottom:cuts[i+1]})
        return result
    }
    property bool trash: false
    function toggleTrash() {
        if (model.busy || projectMotion.active) return
        const before=projects.model.snapshot()
        const after=trash ? model.projectRows.snapshot() : model.trashRows.snapshot()
        projectMotion.begin(before,after,true,null)
        trash=!trash
        projects.forceLayout()
        projectMotion.layoutApplied()
    }

    function toggleFavorites() {
        if (model.busy || projectMotion.active) return
        if (trash) {
            if (!model.favoriteFilter) model.toggleFavoriteFilter()
            toggleTrash()
        } else {
            model.toggleFavoriteFilter()
        }
    }

    function clearFilter() {
        if (model.busy || projectMotion.active) return
        if (trash) toggleTrash()
        if (model.favoriteFilter) model.toggleFavoriteFilter()
    }
    Connections {
        target: page.trash ? page.model.trashRows : page.model.projectRows
        function onAboutToReplace(before,after,animate) { projectMotion.begin(before,after,animate,projects.model) }
        function onReplaced() { projects.forceLayout(); projectMotion.layoutApplied() }
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
        z: 2
        x: 32; y: 28; height: 44
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
        z: 2
        enabled: !page.model.busy && opacity>.99
        objectName: "projectSearch"
        anchors.right: parent.right; anchors.rightMargin: 32
        anchors.verticalCenter: filterBar.verticalCenter
        width: Math.min(220, Math.max(140, page.width / 4)); height: 32
        placeholderText: page.s.placeholder_search
        font.pixelSize: 11; leftPadding: 34; rightPadding: 10
        cornerRadius: 16; fillColor: page.c.BG2; focusFillColor: page.c.BG_INPUT
        edgeColor: page.c.b08; focusEdgeColor: page.c.a45; hintColor: page.c.placeholder; labelColor: page.c.FG
        selectionColor: page.c.a35
        Image {
            sourceSize.width: width; sourceSize.height: height
            x: 11; anchors.verticalCenter: parent.verticalCenter
            width: 14; height: 14
            source: page.model.icon("project_search", page.c.FG_MUTED)
        }
        onTextEdited: page.model.search(text)
    }
    // Filter Pills Bar (Semua, Favorit, Sampah)
    Row {
        id: filterBar
        z: 2
        opacity: page.headingOpacity
        x: 32; y: 74
        spacing: 8

        // Pill: Semua
        RakButton {
            height: 32
            cornerRadius: 16
            fillColor: (!page.trash && !page.model.favoriteFilter) ? page.c.a14 : page.c.BG2
            hoverFillColor: (!page.trash && !page.model.favoriteFilter) ? page.c.a20 : page.c.b06
            downFillColor: page.c.a30
            edgeColor: (!page.trash && !page.model.favoriteFilter) ? page.c.a35 : page.c.b08
            edgeWidth: 1
            animatePress: page.model.settings.animations
            onClicked: page.clearFilter()

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
            fillColor: (!page.trash && page.model.favoriteFilter) ? (page.c.is_light ? "#fef3c7" : "#2b2515") : page.c.BG2
            hoverFillColor: (!page.trash && page.model.favoriteFilter) ? (page.c.is_light ? "#fde68a" : "#3b331c") : page.c.b06
            downFillColor: page.c.b10
            edgeColor: (!page.trash && page.model.favoriteFilter) ? (page.c.is_light ? "#f59e0b" : "#5a4520") : page.c.b08
            edgeWidth: 1
            animatePress: page.model.settings.animations
            onClicked: page.toggleFavorites()

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
            fillColor: page.trash ? (page.c.is_light ? "#fee2e2" : "#301a1a") : page.c.BG2
            hoverFillColor: page.trash ? (page.c.is_light ? "#fecaca" : "#422323") : page.c.b06
            downFillColor: page.c.b10
            edgeColor: page.trash ? (page.c.is_light ? "#fca5a5" : "#552424") : page.c.b08
            edgeWidth: 1
            animatePress: page.model.settings.animations
            onClicked: page.toggleTrash()

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
        x: 32; y: 120; width: Math.max(0, parent.width - 64); height: Math.max(0, parent.height - 120 - 80)
        clip: !page.glassChrome; spacing: 0; boundsBehavior: Flickable.StopAtBounds
        displayMarginBeginning: page.glassChrome?120:0
        displayMarginEnd: page.glassChrome?80:0
        model: page.trash ? page.model.trashRows : page.model.projectRows
        interactive: !projectMotion.active
        delegate: Item {
            required property var modelData
            required property int index
            property alias menuAnchor: card.menuAnchor
            readonly property real pathOffset: card.pathOffset
            width: projects.width; height: 74
            ProjectCard {
                id: card
                model: page.model; trash: !!parent.modelData._trash_id
                modelData: parent.modelData; index: parent.index
                width: projects.width
                interactive: !projectMotion.active
                opacity: projectMotion.active ? 0 : 1
            }
        }
        RakLabel {
            anchors.centerIn: parent; width: parent.width - 32
            visible: page.trash && projects.count === 0 && !projectMotion.active; text: page.s.trash_empty; color: page.c.FG3
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; font.pixelSize: 11
        }
        RakLabel {
            anchors.centerIn: parent; width: parent.width - 32
            visible: !page.trash && page.model.favoriteFilter && projects.count === 0 && !projectMotion.active; text: page.s.favorites_empty; color: page.c.FG3
            horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; font.pixelSize: 11
        }
    }
    ProjectListMotion {
        id: projectMotion
        x: projects.x; y: projects.y; width: projects.width; height: projects.height
        model: page.model; view: projects; trash: page.trash
        clip: !page.glassChrome
    }
    PageRevealMask { anchors.fill: parent; motion: page.presentationMotion; page: page; topInset: headerGlass.height; bottomInset: footer.height }
    }
    GlassBand { id: headerGlass; objectName: "projectsHeaderGlass"; z: 1; width: parent.width; height: 120; model: page.model; backdropSource: projectSurface; origin: Qt.point(0,0) }
    GlassBand { objectName: "projectsFooterGlass"; z: 1; y: parent.height-80; width: parent.width; height: 80; model: page.model; backdropSource: projectSurface; origin: Qt.point(0,y) }
    PageFooter {
        id: footer
        z: 2
        anchors.bottom: parent.bottom; width: parent.width; model: page.model
        ActionButton {
            id: newProject
            objectName: "createButton"
            width: 121; height: 32; colors: page.c; text: page.s.btn_create_project
            iconSource: page.model.icon("plus", page.c.FG2); iconWidth: 15; iconHeight: 15
            animatePress: page.model.settings.animations
            onClicked: page.model.dispatch("newProject", {})
        }
    }
}
