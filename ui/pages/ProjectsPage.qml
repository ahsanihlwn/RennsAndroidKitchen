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
        let cuts=[0,86], result=[]
        for (let row=0; row<projects.count; ++row) {
            let bottom=94+row*74+66-projects.contentY
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
        y: 33; width: Math.min(174, Math.max(115, page.width / 4)); height: 34
        placeholderText: page.s.placeholder_search
        font.pixelSize: 11; leftPadding: 37; rightPadding: 8
        cornerRadius: 10; fillColor: page.c.BG2; focusFillColor: page.c.BG_INPUT
        edgeColor: page.c.b09; focusEdgeColor: page.c.a45; hintColor: page.c.placeholder; labelColor: page.c.FG
        selectionColor: page.c.a35
        Image { sourceSize.width: width; sourceSize.height: height; x: 9; y: 9; width: 16; height: 16; source: page.model.icon("project_search", "") }
        onTextEdited: page.model.search(text)
    }
    SmoothRectangle {
        id: projectSurface
        readonly property rect inputBounds: Qt.rect(0,headerGlass.height,width,Math.max(0,height-headerGlass.height-footer.height))
        anchors.fill: parent; color: page.c.BG; clip: true
    ListView {
        id: projects
        objectName: "projectList"
        x: 36; y: 94; width: Math.max(0, parent.width - 72); height: Math.max(0, parent.height - 180)
        clip: !page.glassChrome; spacing: 0; boundsBehavior: Flickable.StopAtBounds
        displayMarginBeginning: page.glassChrome?94:0
        displayMarginEnd: page.glassChrome?86:0
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
    }
    ProjectListMotion {
        id: projectMotion
        x: projects.x; y: projects.y; width: projects.width; height: projects.height
        model: page.model; view: projects; trash: page.trash
        clip: !page.glassChrome
    }
    PageRevealMask { anchors.fill: parent; motion: page.presentationMotion; page: page; topInset: headerGlass.height; bottomInset: footer.height }
    }
    GlassBand { id: headerGlass; objectName: "projectsHeaderGlass"; z: 1; width: parent.width; height: 86; model: page.model; backdropSource: projectSurface; origin: Qt.point(0,0) }
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
