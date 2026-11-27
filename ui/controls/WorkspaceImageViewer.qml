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
    readonly property bool anims: preferences && preferences.settings ? preferences.settings.animations : true
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
        zoom=Math.min((viewport.width-24)/document.imageWidth,(viewport.height-24)/document.imageHeight)
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

    // Top Header Bar
    Item {
        id: headerBar
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        height: 40
        SmoothRectangle { anchors.fill: parent; color: viewer.c.BG }
        SmoothRectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: viewer.c.b08 }

        // Left: Segmented Zoom & View Capsule
        SmoothRectangle {
            id: zoomCapsule
            anchors.left: parent.left; anchors.leftMargin: 10; anchors.verticalCenter: parent.verticalCenter
            height: 30; width: zoomRow.implicitWidth + 6; radius: 8
            color: viewer.c.b04; border.width: 1; border.color: viewer.c.b08
            Row {
                id: zoomRow
                anchors.centerIn: parent; spacing: 2
                ImageToolButton {
                    objectName: "imageZoomOut"
                    colors: viewer.c; segmented: true; height: 24; width: 28; implicitWidth: 28
                    iconKey: "zoom_out"; iconWidth: 15; iconHeight: 15
                    toolTip: "Zoom Out"
                    onClicked: viewer.zoomAt(1/1.25, viewport.width/2, viewport.height/2)
                }
                RakLabel {
                    width: 44; height: 24
                    text: Math.round(viewer.zoom * 100) + "%"
                    color: viewer.c.FG; font.pixelSize: 11; font.weight: Font.Medium
                    horizontalAlignment: Text.AlignHCenter
                }
                ImageToolButton {
                    objectName: "imageZoomIn"
                    colors: viewer.c; segmented: true; height: 24; width: 28; implicitWidth: 28
                    iconKey: "zoom_in"; iconWidth: 15; iconHeight: 15
                    toolTip: "Zoom In"
                    onClicked: viewer.zoomAt(1.25, viewport.width/2, viewport.height/2)
                }
                Item {
                    width: 6; height: 16; anchors.verticalCenter: parent.verticalCenter
                    SmoothRectangle { anchors.centerIn: parent; width: 1; height: 16; color: viewer.c.b10 }
                }
                ImageToolButton {
                    objectName: "imageFit"
                    colors: viewer.c; segmented: true; height: 24
                    iconKey: "fit"; iconWidth: 14; iconHeight: 14
                    text: viewer.s.iv_fit
                    selected: viewer.fitted
                    onClicked: viewer.fit()
                }
                ImageToolButton {
                    colors: viewer.c; segmented: true; height: 24
                    text: "1:1"
                    selected: !viewer.fitted && Math.abs(viewer.zoom - 1) < 0.005
                    onClicked: { viewer.fitted = false; viewer.zoom = 1; viewport.contentX = 0; viewport.contentY = 0 }
                }
            }
        }

        // Right: Busy indicator, Unsaved indicator, Editor Toggle
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
                visible: document.dirty && !document.svg
                width: 7; height: 7; radius: 3.5
                color: viewer.c.accent_hex
                anchors.verticalCenter: parent.verticalCenter
            }
            ImageToolButton {
                objectName: "imageEditorToggle"
                visible: !document.svg
                colors: viewer.c; height: 28
                iconKey: "edit"; iconWidth: 15; iconHeight: 15
                text: viewer.s.iv_editor
                selected: viewer.editorOpen
                onClicked: viewer.editorOpen = !viewer.editorOpen
            }
        }
    }

    // Collapsible Editor Dock
    Item {
        id: editor
        anchors.top: headerBar.bottom; anchors.left: parent.left; anchors.right: parent.right
        readonly property real targetHeight: (viewer.editorOpen && !document.svg) ? editorCol.implicitHeight : 0
        height: targetHeight
        visible: height > 0.5
        clip: true
        enabled: !document.busy
        Behavior on height {
            NumberAnimation { duration: viewer.anims ? 180 : 0; easing.type: Easing.OutCubic }
        }

        SmoothRectangle { anchors.fill: parent; color: viewer.c.BG2 }
        SmoothRectangle { anchors.fill: parent; color: viewer.c.b02 }
        SmoothRectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: viewer.c.b08 }

        Column {
            id: editorCol
            width: parent.width; spacing: 0

            // Row 1: Retouch Tools, Sliders, Edge Smoothing, Undo
            Flickable {
                id: row1Flick
                objectName: "imageToolsScroll"
                width: parent.width; height: contentWidth > width ? 46 : 42
                contentWidth: row1Container.width; contentHeight: height
                clip: true; boundsBehavior: Flickable.StopAtBounds
                interactive: contentWidth > width
                C.ScrollBar.horizontal: RakScrollBar {
                    height: 5; orientation: Qt.Horizontal; autoHide: false
                    handleColor: viewer.c.b28; hoverColor: viewer.c.b45; downColor: viewer.c.accent_hex
                }
                WheelHandler {
                    enabled: row1Flick.contentWidth > row1Flick.width
                    onWheel: event => {
                        const d = Math.abs(event.angleDelta.x) > Math.abs(event.angleDelta.y) ? event.angleDelta.x : event.angleDelta.y
                        row1Flick.contentX = Math.max(0, Math.min(row1Flick.contentWidth - row1Flick.width, row1Flick.contentX - d * 0.6))
                        event.accepted = true
                    }
                }
                Item {
                    id: row1Container
                    width: Math.max(row1Flick.width, tools.implicitWidth + row1Actions.implicitWidth + 28)
                    height: 40
                    Row {
                        id: tools
                        x: 10; anchors.verticalCenter: parent.verticalCenter; spacing: 8

                        // Tool Mode Segmented Pill
                        SmoothRectangle {
                            height: 30; width: toolGroupRow.implicitWidth + 6; radius: 8
                            color: viewer.c.b04; border.width: 1; border.color: viewer.c.b08
                            anchors.verticalCenter: parent.verticalCenter
                            Row {
                                id: toolGroupRow
                                anchors.centerIn: parent; spacing: 2
                                Repeater {
                                    model: [
                                        {key: "pan", icon: "pan", tip: viewer.s.iv_pan},
                                        {key: "erase", icon: "eraser", tip: viewer.s.iv_eraser},
                                        {key: "magic", icon: "magic_erase", tip: viewer.s.iv_magic}
                                    ]
                                    delegate: ImageToolButton {
                                        required property var modelData
                                        objectName: "imageTool_" + modelData.key
                                        colors: viewer.c; segmented: true; height: 24; width: 28; implicitWidth: 28
                                        iconKey: modelData.icon; iconWidth: 15; iconHeight: 15
                                        toolTip: modelData.tip
                                        selected: viewer.tool === modelData.key
                                        onClicked: viewer.tool = viewer.tool === modelData.key ? "pan" : modelData.key
                                    }
                                }
                            }
                        }

                        // Brush Size Control Pill
                        SmoothRectangle {
                            height: 30; width: brushGroup.implicitWidth + 14; radius: 8
                            color: viewer.tool === "erase" ? viewer.c.a08 : viewer.c.b03
                            border.width: 1; border.color: viewer.tool === "erase" ? viewer.c.a30 : viewer.c.b07
                            anchors.verticalCenter: parent.verticalCenter
                            Row {
                                id: brushGroup
                                anchors.centerIn: parent; spacing: 6
                                RakLabel {
                                    height: 24; text: viewer.s.iv_brush
                                    color: viewer.tool === "erase" ? viewer.c.FG : viewer.c.FG_MUTED
                                    font.pixelSize: 11; font.weight: Font.DemiBold
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                                ViewerSlider {
                                    objectName: "imageBrush"
                                    width: 86; colors: viewer.c; from: 2; to: 200; stepSize: 1; value: viewer.brush
                                    anchors.verticalCenter: parent.verticalCenter
                                    onMoved: viewer.brush = value
                                }
                                SmoothRectangle {
                                    width: 34; height: 20; radius: 5; color: viewer.c.b06
                                    anchors.verticalCenter: parent.verticalCenter
                                    RakLabel {
                                        anchors.centerIn: parent
                                        text: viewer.brush
                                        color: viewer.c.FG; font.pixelSize: 10; font.weight: Font.Medium
                                    }
                                }
                            }
                        }

                        // Magic Threshold Control Pill
                        SmoothRectangle {
                            height: 30; width: thresholdGroup.implicitWidth + 14; radius: 8
                            color: viewer.tool === "magic" ? viewer.c.a08 : viewer.c.b03
                            border.width: 1; border.color: viewer.tool === "magic" ? viewer.c.a30 : viewer.c.b07
                            anchors.verticalCenter: parent.verticalCenter
                            Row {
                                id: thresholdGroup
                                anchors.centerIn: parent; spacing: 6
                                RakLabel {
                                    height: 24; text: viewer.s.iv_threshold
                                    color: viewer.tool === "magic" ? viewer.c.FG : viewer.c.FG_MUTED
                                    font.pixelSize: 11; font.weight: Font.DemiBold
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                                ViewerSlider {
                                    width: 86; colors: viewer.c; from: 0; to: 100; stepSize: 1; value: viewer.threshold
                                    anchors.verticalCenter: parent.verticalCenter
                                    onMoved: viewer.threshold = value
                                }
                                SmoothRectangle {
                                    width: 36; height: 20; radius: 5; color: viewer.c.b06
                                    anchors.verticalCenter: parent.verticalCenter
                                    RakLabel {
                                        anchors.centerIn: parent
                                        text: viewer.threshold + "%"
                                        color: viewer.c.FG; font.pixelSize: 10; font.weight: Font.Medium
                                    }
                                }
                            }
                        }

                        // Smooth Edge Group (Action + Strength Slider)
                        SmoothRectangle {
                            height: 30; width: smoothGroup.implicitWidth + 8; radius: 8
                            color: viewer.c.b03; border.width: 1; border.color: viewer.c.b07
                            anchors.verticalCenter: parent.verticalCenter
                            Row {
                                id: smoothGroup
                                anchors.centerIn: parent; spacing: 6
                                ImageToolButton {
                                    objectName: "imageSmooth"
                                    colors: viewer.c; height: 24
                                    fillColor: viewer.c.b05
                                    iconKey: "smooth"; iconWidth: 14; iconHeight: 14
                                    text: viewer.s.iv_smooth_edge
                                    toolTip: viewer.s.iv_smooth_hint
                                    anchors.verticalCenter: parent.verticalCenter
                                    onClicked: document.smoothEdges(viewer.smoothing)
                                }
                                SmoothRectangle { width: 1; height: 16; color: viewer.c.b08; anchors.verticalCenter: parent.verticalCenter }
                                ViewerSlider {
                                    width: 72; colors: viewer.c; from: 1; to: 100; stepSize: 1; value: viewer.smoothing
                                    anchors.verticalCenter: parent.verticalCenter
                                    onMoved: viewer.smoothing = value
                                }
                                SmoothRectangle {
                                    width: 36; height: 20; radius: 5; color: viewer.c.b06
                                    anchors.verticalCenter: parent.verticalCenter
                                    RakLabel {
                                        anchors.centerIn: parent
                                        text: viewer.smoothing + "%"
                                        color: viewer.c.FG; font.pixelSize: 10; font.weight: Font.Medium
                                    }
                                }
                            }
                        }
                    }

                    // Right-aligned Undo action
                    Row {
                        id: row1Actions
                        anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                        ImageToolButton {
                            objectName: "imageUndo"
                            colors: viewer.c; height: 28
                            iconKey: "undo"; iconWidth: 15; iconHeight: 15
                            text: viewer.s.iv_undo
                            enabled: document.canUndo && !document.busy
                            toolTip: viewer.s.iv_undo + " (Ctrl+Z)"
                            onClicked: document.undo()
                        }
                    }
                }
            }

            SmoothRectangle { width: parent.width; height: 1; color: viewer.c.b05 }

            // Row 2: Scale / Crop Mode, Dimensions, Apply, Save / Save As
            Flickable {
                id: row2Flick
                width: parent.width; height: contentWidth > width ? 46 : 42
                contentWidth: row2Container.width; contentHeight: height
                clip: true; boundsBehavior: Flickable.StopAtBounds
                interactive: contentWidth > width
                C.ScrollBar.horizontal: RakScrollBar {
                    height: 5; orientation: Qt.Horizontal; autoHide: false
                    handleColor: viewer.c.b28; hoverColor: viewer.c.b45; downColor: viewer.c.accent_hex
                }
                WheelHandler {
                    enabled: row2Flick.contentWidth > row2Flick.width
                    onWheel: event => {
                        const d = Math.abs(event.angleDelta.x) > Math.abs(event.angleDelta.y) ? event.angleDelta.x : event.angleDelta.y
                        row2Flick.contentX = Math.max(0, Math.min(row2Flick.contentWidth - row2Flick.width, row2Flick.contentX - d * 0.6))
                        event.accepted = true
                    }
                }
                Item {
                    id: row2Container
                    width: Math.max(row2Flick.width, resizeTools.implicitWidth + saveTools.implicitWidth + 28)
                    height: 40
                    Row {
                        id: resizeTools
                        x: 10; anchors.verticalCenter: parent.verticalCenter; spacing: 8

                        // Scale / Crop Segmented Pill
                        SmoothRectangle {
                            height: 30; width: modeGroupRow.implicitWidth + 6; radius: 8
                            color: viewer.c.b04; border.width: 1; border.color: viewer.c.b08
                            anchors.verticalCenter: parent.verticalCenter
                            Row {
                                id: modeGroupRow
                                anchors.centerIn: parent; spacing: 2
                                ImageToolButton {
                                    objectName: "imageScale"
                                    colors: viewer.c; segmented: true; height: 24
                                    iconKey: "scale"; iconWidth: 14; iconHeight: 14
                                    text: viewer.s.iv_scale
                                    selected: viewer.mode === "scale"
                                    onClicked: viewer.mode = "scale"
                                }
                                ImageToolButton {
                                    objectName: "imageCrop"
                                    colors: viewer.c; segmented: true; height: 24
                                    iconKey: "crop"; iconWidth: 14; iconHeight: 14
                                    text: viewer.s.iv_crop
                                    selected: viewer.mode === "crop"
                                    onClicked: viewer.mode = "crop"
                                }
                            }
                        }

                        // Dimensions Input Group
                        Row {
                            anchors.verticalCenter: parent.verticalCenter; spacing: 5
                            RakLabel {
                                height: 26; text: "W"; color: viewer.c.FG_MUTED
                                font.pixelSize: 11; font.weight: Font.DemiBold
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            ViewerField {
                                id: widthInput; objectName: "imageWidth"
                                colors: viewer.c; width: 60; height: 26; cornerRadius: 6
                                topPadding: 3; bottomPadding: 3; leftPadding: 6; rightPadding: 6
                                horizontalAlignment: TextInput.AlignHCenter
                                anchors.verticalCenter: parent.verticalCenter
                                validator: IntValidator { bottom: 1; top: 30000 }
                                onTextEdited: viewer.dimensions("w")
                            }
                            ImageToolButton {
                                colors: viewer.c; width: 28; height: 26; implicitWidth: 28
                                iconKey: viewer.aspectLocked ? "lock" : "unlock"
                                iconWidth: 14; iconHeight: 14
                                toolTip: viewer.s.iv_lock_aspect
                                selected: viewer.aspectLocked
                                enabled: viewer.mode === "scale"
                                anchors.verticalCenter: parent.verticalCenter
                                onClicked: {
                                    viewer.aspectLocked = !viewer.aspectLocked
                                    if (viewer.aspectLocked) viewer.dimensions("w")
                                }
                            }
                            RakLabel {
                                height: 26; text: "H"; color: viewer.c.FG_MUTED
                                font.pixelSize: 11; font.weight: Font.DemiBold
                                anchors.verticalCenter: parent.verticalCenter
                            }
                            ViewerField {
                                id: heightInput; objectName: "imageHeight"
                                colors: viewer.c; width: 60; height: 26; cornerRadius: 6
                                topPadding: 3; bottomPadding: 3; leftPadding: 6; rightPadding: 6
                                horizontalAlignment: TextInput.AlignHCenter
                                anchors.verticalCenter: parent.verticalCenter
                                validator: IntValidator { bottom: 1; top: 30000 }
                                onTextEdited: viewer.dimensions("h")
                            }
                        }

                        ImageToolButton {
                            objectName: "imageApply"
                            colors: viewer.c; height: 26
                            text: viewer.s.iv_apply
                            anchors.verticalCenter: parent.verticalCenter
                            onClicked: document.resizeImage(Number(widthInput.text), Number(heightInput.text), viewer.mode)
                        }
                    }

                    // Right-aligned Save Actions (guaranteed never to overlap resizeTools)
                    Row {
                        id: saveTools
                        anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter; spacing: 6
                        ImageToolButton {
                            objectName: "imageSave"
                            colors: viewer.c; height: 26
                            iconKey: "save"; iconWidth: 14; iconHeight: 14
                            text: viewer.s.iv_save
                            selected: document.dirty
                            onClicked: document.save("")
                        }
                        ImageToolButton {
                            objectName: "imageSaveAs"
                            colors: viewer.c; height: 26
                            iconKey: "save_as"; iconWidth: 14; iconHeight: 14
                            text: viewer.s.iv_save_as
                            onClicked: savePicker.open()
                        }
                    }
                }
            }
        }
    }

    // Canvas Viewport
    Item {
        id: viewportBox
        anchors.top: editor.bottom; anchors.bottom: statusBar.top
        anchors.left: parent.left; anchors.right: parent.right
        clip: true
        Canvas {
            anchors.fill: parent
            onPaint: {
                const ctx = getContext("2d")
                for (let py = 0; py < height; py += 12)
                    for (let px = 0; px < width; px += 12) {
                        ctx.fillStyle = (Math.floor(px / 12) + Math.floor(py / 12)) % 2 === 0 ? "#383c44" : "#2a2e36"
                        ctx.fillRect(px, py, 12, 12)
                    }
            }
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
        }
        Flickable {
            id: viewport
            anchors.fill: parent
            contentWidth: Math.max(width, picture.width + 20)
            contentHeight: Math.max(height, picture.height + 20)
            clip: true
            onWidthChanged: if (viewer.fitted) Qt.callLater(viewer.fit)
            onHeightChanged: if (viewer.fitted) Qt.callLater(viewer.fit)
            interactive: viewer.tool === "pan"
            boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.horizontal: RakScrollBar { height: 7; orientation: Qt.Horizontal; autoHide: false; handleColor: viewer.c.b28; hoverColor: viewer.c.b45; downColor: viewer.c.accent_hex }
            C.ScrollBar.vertical: RakScrollBar { width: 7; autoHide: false; handleColor: viewer.c.b28; hoverColor: viewer.c.b45; downColor: viewer.c.accent_hex }
            Image {
                id: picture
                objectName: "imageCanvas"
                x: Math.max(10, (viewport.width - width) / 2)
                y: Math.max(10, (viewport.height - height) / 2)
                width: document.imageWidth * viewer.zoom
                height: document.imageHeight * viewer.zoom
                source: document.source; cache: false; smooth: true; mipmap: true

                Rectangle {
                    anchors.fill: parent
                    color: "transparent"
                    border.width: 1
                    border.color: viewer.c.b14
                    visible: picture.width > 0 && picture.height > 0
                }

                MouseArea {
                    id: canvasMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    enabled: viewer.tool !== "pan" && !document.busy
                    cursorShape: viewer.tool === "erase" ? Qt.BlankCursor : Qt.CrossCursor
                    function point(mouse, phase) {
                        document.toolPoint(mouse.x / viewer.zoom, mouse.y / viewer.zoom, phase, viewer.tool, viewer.brush, viewer.threshold)
                    }
                    onPressed: mouse => point(mouse, "press")
                    onPositionChanged: mouse => { if (pressed) point(mouse, "move") }
                    onReleased: mouse => point(mouse, "release")
                }

                // Live Eraser Brush Preview Ring
                Rectangle {
                    visible: viewer.editorOpen && viewer.tool === "erase" && canvasMouse.containsMouse && !document.busy
                    width: Math.max(4, viewer.brush * viewer.zoom)
                    height: width
                    radius: width / 2
                    x: canvasMouse.mouseX - width / 2
                    y: canvasMouse.mouseY - height / 2
                    color: Qt.rgba(viewer.c.accent_hex.r, viewer.c.accent_hex.g, viewer.c.accent_hex.b, 0.10)
                    border.width: 1.5
                    border.color: viewer.c.accent_hex
                }
            }
            WheelHandler {
                onWheel: event => {
                    viewer.zoomAt(event.angleDelta.y > 0 ? 1.25 : 1 / 1.25, event.x, event.y)
                    event.accepted = true
                }
            }
        }
    }

    // Bottom Status Bar
    Item {
        id: statusBar
        anchors.bottom: parent.bottom; anchors.left: parent.left; anchors.right: parent.right
        height: 28
        SmoothRectangle { anchors.fill: parent; color: viewer.c.BG }
        SmoothRectangle { anchors.top: parent.top; width: parent.width; height: 1; color: viewer.c.b08 }
        RakLabel {
            anchors.left: parent.left; anchors.leftMargin: 12
            anchors.right: activeToolHint.left; anchors.rightMargin: 12
            height: parent.height
            text: document.info
            color: viewer.c.FG_MUTED; font.pixelSize: 11; elide: Text.ElideRight
        }
        RakLabel {
            id: activeToolHint
            anchors.right: parent.right; anchors.rightMargin: 12
            height: parent.height
            visible: viewer.editorOpen && !document.svg
            text: viewer.tool === "erase"
                ? (viewer.s.iv_eraser + " · " + viewer.brush + "px")
                : viewer.tool === "magic"
                    ? (viewer.s.iv_magic + " · " + viewer.threshold + "%")
                    : viewer.s.iv_pan
            color: viewer.tool === "pan" ? viewer.c.FG_MUTED : viewer.c.FG_BLUE
            font.pixelSize: 11; font.weight: Font.Medium
        }
    }

    D.FileDialog {
        id: savePicker
        title: viewer.s.iv_save_as
        fileMode: D.FileDialog.SaveFile
        nameFilters: ["Images (*.png *.jpg *.jpeg *.bmp *.webp)", "All files (*)"]
        currentFile: document.fileUrl
        onAccepted: document.save(selectedFile.toString())
    }
}
