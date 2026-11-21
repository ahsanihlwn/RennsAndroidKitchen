// SPDX-License-Identifier: MPL-2.0
import QtQuick
import "../motion.js" as Motion

Item {
    id: shelf
    required property var model
    property real headingWidth: 0
    property bool trash: false
    property real usageElapsed: 0
    property bool sidebar: false
    property bool ready: false
    property bool layoutActive: false
    property bool diskActive: false
    property var sources: ({})
    property var diskRows: []
    property real sourceListX: 36
    property real retainedContentHeight: 0
    property var diskOwner: null
    readonly property var c: model.colors
    readonly property bool wantedSidebar: width-40 < headingWidth+(model.drives.length+1)*128+model.drives.length*8+228
    readonly property real listX: layoutActive ? Math.round(Motion.mix(sourceListX,sidebar?180:36,Motion.outCubic(layoutClock.elapsed/layoutClock.duration))) : sidebar?180:36
    signal trashClicked()
    objectName: "driveShelf"

    function destination(index) { return sidebar ? Qt.point(32,94+index*52-scroll.contentY) : Qt.point(32+headingWidth+14+index*136,28) }
    function position(index,key) {
        const end=destination(index), source=sources[key]
        if (!layoutActive || !source) return end
        const p=Motion.travel(source,end,Motion.outCubic((layoutClock.elapsed-index*22)/440))
        return Qt.point(Math.round(p.x),Math.round(p.y))
    }
    function changeLayout() {
        if (!ready || !visible || trash || diskActive || sidebar===wantedSidebar) return
        let before={trash:position(0,"trash")}
        const rows=model.driveRows.snapshot()
        for (let i=0;i<rows.length;++i) before[rows[i].key]=position(i+1,rows[i].key)
        sourceListX=listX
        sources=before
        sidebar=wantedSidebar
        scroll.contentY=0
        layoutActive=model.settings.animations && parent.visible && !model.startupActive
        if (layoutActive) layoutClock.start(440+rows.length*22)
    }
    function finishDisks() {
        if (!diskActive) return
        diskClock.seek(diskClock.duration)
        diskActive=false; diskRows=[]
        const previous=diskOwner; diskOwner=null
        if (previous) previous.setAnimating(false)
        changeLayout()
    }
    function beginDisks(before,after,owner) {
        if (!ready || !visible || !model.settings.animations || !parent.visible || model.startupActive) return
        layoutClock.seek(layoutClock.duration); layoutActive=false
        const bounds=sidebar?Qt.rect(viewport.x,viewport.y,viewport.width,viewport.height):Qt.rect(20,20,width-40,60)
        const plan=Motion.diskScene(before,after,(i,row)=>destination(i+1),bounds)
        diskRows=plan.rows; diskOwner=owner; diskActive=true
        if (owner) owner.setAnimating(true)
        diskClock.start(plan.duration)
    }
    onTrashChanged: {
        if (trash) retainedContentHeight=scroll.contentHeight
        finishDisks()
        const rows=model.driveRows.snapshot()
        beginDisks(trash?rows:[],trash?[]:rows,null)
        if (!trash && !diskActive) changeLayout()
    }
    onWantedSidebarChanged: Qt.callLater(changeLayout)
    onWidthChanged: { finishDisks(); changeLayout() }
    Component.onCompleted: { sidebar=wantedSidebar; ready=true }
    Connections {
        target: shelf.model.driveRows
        function onAboutToReplace(before,after,animate) {
            if (animate && !shelf.trash) shelf.beginDisks(before,after,shelf.model.driveRows)
        }
        function onReplaced() { if (!shelf.diskActive) shelf.changeLayout() }
    }
    Connections {
        target: shelf.model
        function onSettingsChanged() {
            if (!shelf.model.settings.animations) { shelf.finishDisks(); shelf.layoutActive=false }
        }
        function onPageChanged() { shelf.finishDisks(); shelf.layoutActive=false }
    }
    MotionClock { id: layoutClock; objectName: "driveLayoutClock"; onFinished: { shelf.layoutActive=false; shelf.sources=({}) } }
    MotionClock { id: diskClock; objectName: "diskCardsClock"; onFinished: shelf.finishDisks() }
    Item {
        id: viewport
        x: shelf.sidebar?32:32+shelf.headingWidth+14
        y: shelf.sidebar?86:28
        width: shelf.sidebar?132:(shelf.model.drives.length+1)*136-8
        height: shelf.sidebar?Math.max(0,shelf.height-164):44
        clip: !shelf.layoutActive
        Flickable {
            id: scroll
            anchors.fill: parent
            interactive: shelf.sidebar && !shelf.layoutActive && !shelf.diskActive
            contentWidth: width
            contentHeight: shelf.sidebar?(shelf.trash?shelf.retainedContentHeight:Math.max(height,(shelf.model.drives.length+1)*52)):44
            boundsBehavior: Flickable.StopAtBounds
            RakCard {
                id: trashCard
                readonly property point point: shelf.position(0,"trash")
                objectName: "trashDrive"
                x: point.x-viewport.x; y: point.y-viewport.y+scroll.contentY
                width: 128; height: 44; radius: 10
                scale: trashFeedback.value
                PressFeedback { id: trashFeedback; enabledMotion: shelf.model.settings.animations }
                activeFocusOnTab: true
                Keys.onPressed: event => {
                    if (event.isAutoRepeat || [Qt.Key_Space,Qt.Key_Return,Qt.Key_Enter].indexOf(event.key)<0) return
                    trashFeedback.press(); event.accepted=true
                }
                Keys.onReleased: event => {
                    if (event.isAutoRepeat || [Qt.Key_Space,Qt.Key_Return,Qt.Key_Enter].indexOf(event.key)<0) return
                    trashFeedback.release(); shelf.trashClicked(); event.accepted=true
                }
                HoverHandler { id: trashHover; cursorShape: Qt.PointingHandCursor }
                HoverTip { label: shelf.model.strings.trash_retention; hovering: trashHover.hovered }
                color: shelf.trash?shelf.c.a12:shelf.c.BG2
                border.color: shelf.trash?shelf.c.a45:Qt.tint(shelf.c.BG2,shelf.c.b10)
                Image { x: 8; y: 12; width: 20; height: 20; sourceSize.width: width; sourceSize.height: height; source: shelf.model.icon("project_trash",shelf.c.FG2) }
                RakLabel { x: 35; y: 4; height: 18; text: shelf.model.strings.hdr_trash; color: shelf.c.FG_BRIGHT; font.pixelSize: 10; font.weight: Font.DemiBold; transformed: Math.abs(trashFeedback.value-1)>.0001 }
                RakLabel { x: 35; y: 23; height: 17; text: shelf.model.strings.trash_count.replace("{n}",shelf.model.trashCount.toString()); color: shelf.c.FG3; font.pixelSize: 9; transformed: Math.abs(trashFeedback.value-1)>.0001 }
                TapHandler {
                    onPressedChanged: if (pressed) { trashCard.forceActiveFocus(Qt.MouseFocusReason); trashFeedback.press() } else trashFeedback.release()
                    onTapped: shelf.trashClicked()
                }
            }
            Repeater {
                model: shelf.model.driveRows
                delegate: DriveCard {
                    required property int index
                    readonly property point point: shelf.position(index+1,modelData.key)
                    x: point.x-viewport.x; y: point.y-viewport.y+scroll.contentY
                    model: shelf.model; trash: shelf.trash
                    visible: !shelf.trash && !shelf.diskActive
                    usageProgress: shelf.model.settings.animations?Motion.driveProgress(shelf.usageElapsed,index):1
                }
            }
        }
    }
    Item {
        x: shelf.sidebar?viewport.x:20; y: shelf.sidebar?viewport.y:20
        width: shelf.sidebar?viewport.width:shelf.width-40; height: shelf.sidebar?viewport.height:60; clip: true
        visible: shelf.diskActive
        Repeater {
            model: shelf.diskRows
            delegate: Item {
                id: disk
                required property var modelData
                readonly property real phase: modelData.kind==="remove"?Motion.inOutSine(diskClock.elapsed/380):modelData.kind==="add"?Motion.inOutSine(diskClock.elapsed/310):Motion.outCubic((diskClock.elapsed-modelData.delay)/310)
                readonly property real inset: modelData.kind==="remove"?phase:0
                objectName: "diskMotionRow_" + modelData.row.key
                x: (modelData.kind==="keep"?Motion.mix(modelData.source.x,modelData.target.x,phase):modelData.source.x)-parent.x+3*inset
                y: (modelData.kind==="keep"?Motion.mix(modelData.source.y,modelData.target.y,phase):modelData.source.y)-parent.y+inset
                width: 128; height: 44
                opacity: modelData.kind==="remove"?1-phase:modelData.kind==="add"?phase:1
                transform: Scale { xScale: (128-6*disk.inset)/128; yScale: (44-2*disk.inset)/44 }
                layer.enabled: true
                DriveCard {
                    model: shelf.model; modelData: disk.modelData.row; interactive: false
                    usageProgress: shelf.model.settings.animations?Motion.driveProgress(shelf.usageElapsed,disk.modelData.index):1
                }
            }
        }
    }
}
