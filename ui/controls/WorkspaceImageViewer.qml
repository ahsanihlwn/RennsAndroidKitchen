// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Dialogs as D
import Renns.Workspace
Item {
    id: viewer; objectName: "workspaceImageViewer"
    required property var workspace
    required property var preferences
    required property var details
    readonly property var c: preferences.colors
    readonly property var s: preferences.strings
    readonly property var documentModel: document
    property bool editorOpen: false
    property string tool: "pan"
    property string mode: "scale"
    property bool aspectLocked: true
    property bool fitted: true
    property real zoom: 1
    property int brush: 24
    property int threshold: 10
    property int smoothing: 50
    function fit() {
        fitted=true
        // A pane can begin at zero height while opening above a terminal.
        // Keep fitting through its resize instead of caching a negative zoom.
        if(viewport.width<=4 || viewport.height<=4 || document.imageWidth<=0 || document.imageHeight<=0)return
        zoom=Math.min((viewport.width-4)/document.imageWidth,(viewport.height-4)/document.imageHeight)
        viewport.contentX=0;viewport.contentY=0
    }
    function zoomAt(factor,x,y) {
        const next=zoom*factor;if(next<.05 || next>40)return
        const px=(viewport.contentX+x-picture.x)/zoom,py=(viewport.contentY+y-picture.y)/zoom
        fitted=false;zoom=next;viewport.contentX=Math.max(0,px*zoom+picture.x-x);viewport.contentY=Math.max(0,py*zoom+picture.y-y)
    }
    function dimensions(which) {
        if(!aspectLocked || mode!=="scale" || document.imageWidth<=0)return
        if(which==="w")heightInput.text=String(Math.max(1,Math.round(Number(widthInput.text)*document.imageHeight/document.imageWidth)))
        else widthInput.text=String(Math.max(1,Math.round(Number(heightInput.text)*document.imageWidth/document.imageHeight)))
    }
    ImageDocument { id: document }
    Component.onCompleted: document.load(details.documentPath || details.path)
    Component.onDestruction: document.close()
    Connections {
        target: document
        function onRefitRequested(){widthInput.text=String(document.imageWidth);heightInput.text=String(document.imageHeight);Qt.callLater(viewer.fit)}
        function onDirtyChanged(dirty){viewer.workspace.setViewerUnsaved(viewer.details.path,dirty)}
        function onMessageRequested(title,text){viewer.workspace.showMessage(title,text)}
    }
    Connections { target: workspace; function onPresentationRequested(kind,data){if(kind==="saveViewer" && data.path===viewer.details.path)document.save("")} }
    Shortcut { sequence: "Ctrl+Z"; enabled: viewer.visible; onActivated: document.undo() }
    SmoothRectangle { anchors.fill: parent; color: viewer.c.BG2 }
    Row {
        x: 8; y: 6; spacing: 6; height: 26
        ImageToolButton { objectName: "imageZoomOut"; colors: viewer.c; text: "−"; onClicked: viewer.zoomAt(1/1.25,viewport.width/2,viewport.height/2) }
        ImageToolButton { objectName: "imageZoomIn"; colors: viewer.c; text: "+"; onClicked: viewer.zoomAt(1.25,viewport.width/2,viewport.height/2) }
        ImageToolButton { objectName: "imageFit"; colors: viewer.c; text: viewer.s.iv_fit; onClicked: viewer.fit() }
        ImageToolButton { colors: viewer.c; text: "1:1"; onClicked: {viewer.fitted=false;viewer.zoom=1;viewport.contentX=0;viewport.contentY=0} }
    }
    Row {
        anchors.right: parent.right; anchors.rightMargin: 8; y: 6; height: 26; spacing: 6
        RakLabel { height: 26; text: Math.round(viewer.zoom*100)+"%"; color: viewer.c.FG_MUTED; font.pixelSize: 11 }
        SmoothRectangle { width: 1; height: 26; color: viewer.c.b10; visible: !document.svg }
        ImageToolButton { objectName: "imageEditorToggle"; visible: !document.svg; colors: viewer.c; text: viewer.s.iv_editor; iconSource: "image://icons/edit"; selected: viewer.editorOpen; onClicked: viewer.editorOpen=!viewer.editorOpen }
    }
    SmoothRectangle { y: 37; width: parent.width; height: 1; color: viewer.c.b08 }
    Column {
        id: editor; visible: viewer.editorOpen; x: 0; y: 38; width: parent.width; spacing: 0; enabled: !document.busy
        Flickable {
            objectName: "imageToolsScroll"
            width: parent.width; height: 36; contentWidth: tools.width+16; contentHeight: height; clip: true; boundsBehavior: Flickable.StopAtBounds
            Row {
                id: tools; x: 8; y: 5; height: 26; spacing: 6
                Repeater { model: [{key:"pan",icon:"pan",tip:viewer.s.iv_pan},{key:"erase",icon:"eraser",tip:viewer.s.iv_eraser},{key:"magic",icon:"magic_erase",tip:viewer.s.iv_magic}]
                    delegate: ImageToolButton { required property var modelData; objectName: "imageTool_"+modelData.key; colors: viewer.c; iconSource: "image://icons/"+modelData.icon; toolTip: modelData.tip; selected: viewer.tool===modelData.key; onClicked: viewer.tool=viewer.tool===modelData.key?"pan":modelData.key }
                }
                RakLabel { height: 26; text: viewer.s.iv_brush; color: viewer.c.FG_MUTED; font.pixelSize: 11; font.weight: Font.DemiBold }
                ViewerSlider { objectName: "imageBrush"; width: 90; colors: viewer.c; from: 2; to: 200; stepSize: 1; value: viewer.brush; onMoved: viewer.brush=value }
                RakLabel { width: 26; height: 26; text: viewer.brush; color: viewer.c.FG_MUTED; font.pixelSize: 11 }
                RakLabel { height: 26; text: viewer.s.iv_threshold; color: viewer.c.FG_MUTED; font.pixelSize: 11; font.weight: Font.DemiBold }
                ViewerSlider { width: 90; colors: viewer.c; from: 0; to: 100; stepSize: 1; value: viewer.threshold; onMoved: viewer.threshold=value }
                RakLabel { width: 34; height: 26; text: viewer.threshold+"%"; color: viewer.c.FG_MUTED; font.pixelSize: 11 }
                RakLabel { height: 26; text: viewer.s.iv_smooth_edge; color: viewer.c.FG_MUTED; font.pixelSize: 11; font.weight: Font.DemiBold }
                ViewerSlider { width: 80; colors: viewer.c; from: 1; to: 100; stepSize: 1; value: viewer.smoothing; onMoved: viewer.smoothing=value }
                RakLabel { width: 34; height: 26; text: viewer.smoothing+"%"; color: viewer.c.FG_MUTED; font.pixelSize: 11 }
                ImageToolButton { objectName: "imageSmooth"; colors: viewer.c; text: viewer.s.iv_smooth_edge; toolTip: viewer.s.iv_smooth_hint; onClicked: document.smoothEdges(viewer.smoothing) }
                ImageToolButton { objectName: "imageUndo"; colors: viewer.c; iconSource: "image://icons/undo"; toolTip: viewer.s.iv_undo; onClicked: document.undo() }
            }
        }
        Flickable {
            width: parent.width; height: 36; contentWidth: Math.max(width,resizeTools.width+saveTools.width+24); contentHeight: height; clip: true
            Row {
                id: resizeTools; x: 8; y: 5; spacing: 6
                ImageToolButton { objectName: "imageScale"; colors: viewer.c; text: viewer.s.iv_scale; selected: viewer.mode==="scale"; onClicked: viewer.mode="scale" }
                ImageToolButton { objectName: "imageCrop"; colors: viewer.c; text: viewer.s.iv_crop; selected: viewer.mode==="crop"; onClicked: viewer.mode="crop" }
                RakLabel { height: 26; text: "W"; color: viewer.c.FG_MUTED; font.pixelSize: 11; font.weight: Font.DemiBold }
                ViewerField { id: widthInput; objectName: "imageWidth"; colors: viewer.c; width: 58; height: 26; horizontalAlignment: TextInput.AlignHCenter; validator: IntValidator { bottom: 1; top: 30000 } onTextEdited: viewer.dimensions("w") }
                ImageToolButton { colors: viewer.c; width: 28; iconSource: "image://icons/lock"; selected: viewer.aspectLocked; enabled: viewer.mode==="scale"; onClicked: {viewer.aspectLocked=!viewer.aspectLocked;if(viewer.aspectLocked)viewer.dimensions("w")} }
                RakLabel { height: 26; text: "H"; color: viewer.c.FG_MUTED; font.pixelSize: 11; font.weight: Font.DemiBold }
                ViewerField { id: heightInput; objectName: "imageHeight"; colors: viewer.c; width: 58; height: 26; horizontalAlignment: TextInput.AlignHCenter; validator: IntValidator { bottom: 1; top: 30000 } onTextEdited: viewer.dimensions("h") }
                ImageToolButton { objectName: "imageApply"; colors: viewer.c; text: viewer.s.iv_apply; onClicked: document.resizeImage(Number(widthInput.text),Number(heightInput.text),viewer.mode) }
            }
            Row { id: saveTools; x: parent.contentWidth-width-8; y: 5; spacing: 6
                ImageToolButton { objectName: "imageSave"; colors: viewer.c; text: viewer.s.iv_save; onClicked: document.save("") }
                ImageToolButton { objectName: "imageSaveAs"; colors: viewer.c; text: viewer.s.iv_save_as; onClicked: savePicker.open() }
            }
        }
    }
    Item {
        id: viewportBox; x: 0; y: viewer.editorOpen?110:38; width: parent.width; height: Math.max(0,parent.height-y-26); clip: true
        Canvas { anchors.fill: parent; onPaint: {const ctx=getContext("2d");for(let y=0;y<height;y+=12)for(let x=0;x<width;x+=12){ctx.fillStyle=(Math.floor(x/12)+Math.floor(y/12))%2===0?"#3c4048":"#2c3038";ctx.fillRect(x,y,12,12)}} onWidthChanged: requestPaint(); onHeightChanged: requestPaint() }
        Flickable {
            id: viewport; anchors.fill: parent; contentWidth: Math.max(width,picture.width); contentHeight: Math.max(height,picture.height); clip: true
            onWidthChanged: if(viewer.fitted)Qt.callLater(viewer.fit)
            onHeightChanged: if(viewer.fitted)Qt.callLater(viewer.fit)
            interactive: viewer.tool==="pan"; boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.horizontal: RakScrollBar { height: 6; orientation: Qt.Horizontal; handleColor: viewer.c.b28; hoverColor: viewer.c.b45 }
            C.ScrollBar.vertical: RakScrollBar { width: 6; handleColor: viewer.c.b28; hoverColor: viewer.c.b45 }
            Image { id: picture; objectName: "imageCanvas"; x: Math.max(0,(viewport.width-width)/2); y: Math.max(0,(viewport.height-height)/2); width: document.imageWidth*viewer.zoom; height: document.imageHeight*viewer.zoom; source: document.source; cache: false; smooth: true; mipmap: true
                MouseArea { anchors.fill: parent; enabled: viewer.tool!=="pan" && !document.busy; cursorShape: Qt.CrossCursor
                    function point(mouse,phase){document.toolPoint(mouse.x/viewer.zoom,mouse.y/viewer.zoom,phase,viewer.tool,viewer.brush,viewer.threshold)}
                    onPressed: mouse=>point(mouse,"press"); onPositionChanged: mouse=>{if(pressed)point(mouse,"move")}; onReleased: mouse=>point(mouse,"release")
                }
            }
            WheelHandler { onWheel: event=>{viewer.zoomAt(event.angleDelta.y>0?1.25:1/1.25,event.x,event.y);event.accepted=true} }
        }
    }
    RakLabel { x: 12; y: parent.height-26; height: 26; width: parent.width-24; text: document.info; color: viewer.c.FG_MUTED; font.pixelSize: 11; elide: Text.ElideRight }
    D.FileDialog { id: savePicker; title: viewer.s.iv_save_as; fileMode: D.FileDialog.SaveFile; nameFilters: ["Images (*.png *.jpg *.jpeg *.bmp *.webp)","All files (*)"]; currentFile: document.fileUrl; onAccepted: document.save(selectedFile.toString()) }
}
