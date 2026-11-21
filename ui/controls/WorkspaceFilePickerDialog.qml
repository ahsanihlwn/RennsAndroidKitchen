// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: dialog
    required property var workspace
    required property var backend
    property string pickerTitle: "Select File"
    property string openText: "Open"
    property string emptyText: "No files found"
    property bool multiSelect: false
    property url currentFolder: ""
    signal acceptedPaths(var urls)

    readonly property bool isLight: !!(dialog.c && dialog.c.is_light)
    readonly property string selectionText: dialog.backend.selectedLabel || "No file selected"
    readonly property bool canConfirm: dialog.multiSelect ? dialog.backend.selectedPaths.length > 0 : !!dialog.backend.selected

    implicitWidth: 720; implicitHeight: 480; panelAlpha: 170/255

    function showPicker() {
        filter.text = ""
        dialog.backend.refreshLocations()
        const target = dialog.currentFolder.toString().length ? dialog.backend.localPath(dialog.currentFolder) : ""
        dialog.backend.load(target || dialog.backend.directory)
        open()
    }

    Connections {
        target: dialog.backend
        function onChosenUrls(urls) {
            dialog.acceptedPaths(urls)
            dialog.dismiss()
        }
    }

    RakLabel { x: 28; y: 24; height: 28; verticalAlignment: Text.AlignVCenter; text: dialog.pickerTitle; font.pixelSize: 13; font.weight: Font.DemiBold; color: dialog.c.FG }
    RakField { id: filter; anchors.right: parent.right; anchors.rightMargin: 28; y: 24; width: 176; height: 28; cornerRadius: 7; placeholderText: "Filter…"; font.pixelSize: 11; topPadding: 1; bottomPadding: 0; fillColor: dialog.c.BG_INPUT; edgeColor: dialog.c.b10; focusEdgeColor: dialog.c.a55; labelColor: dialog.c.FG; onTextEdited: dialog.backend.filter(text) }
    SmoothRectangle { x: 28; y: 62; width: parent.width-56; height: 1; color: dialog.c.b06 }
    RakButton {
        id: backBtn
        x: 28; y: 72; width: 66; height: 26; cornerRadius: 6
        leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
        fillColor: dialog.c.b04; hoverFillColor: dialog.c.b08
        edgeColor: dialog.c.b08; hoverEdgeColor: dialog.c.b12
        onClicked: {filter.text="";dialog.backend.up()}
        contentItem: Item {
            Row {
                anchors.centerIn: parent; spacing: 5
                Image {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 11; height: 11; rotation: 180
                    source: "image://icons/arrow_right?color="+encodeURIComponent(backBtn.hovered ? dialog.c.FG_BRIGHT : (dialog.c.treeArrow || dialog.c.FG2))
                    sourceSize: Qt.size(22,22); smooth: true; cache: true
                }
                RakLabel {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Back"; font.pixelSize: 11; font.weight: Font.Medium
                    color: backBtn.hovered ? dialog.c.FG_BRIGHT : dialog.c.FG
                    transformed: backBtn.transformed
                }
            }
        }
    }
    RakLabel { x: 104; y: 72; width: parent.width-132; height: 26; verticalAlignment: Text.AlignVCenter; text: dialog.backend.directory; elide: Text.ElideMiddle; font.pixelSize: 11; color: dialog.c.FG2 }
    ListView {
        id: sidebar; x: 28; y: 108; width: 152; height: 270; clip: true; spacing: 2; model: dialog.backend.locations
        C.ScrollBar.vertical: RakScrollBar { handleColor: dialog.c.BG_SCROLL }
        delegate: Item {
            required property var modelData
            readonly property bool active: !modelData.header && dialog.backend.directory===modelData.path
            width: sidebar.width; height: modelData.header ? 24 : 30
            RakButton {
                id: locBtn
                anchors.fill: parent; visible: !modelData.header
                leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
                cornerRadius: 6
                fillColor: active ? dialog.c.a15 : "transparent"
                hoverFillColor: active ? dialog.c.a15 : dialog.c.b06
                edgeColor: active ? dialog.c.a40 : "transparent"
                hoverEdgeColor: active ? dialog.c.a40 : "transparent"
                onClicked: {filter.text="";dialog.backend.load(modelData.path)}
                contentItem: Item {
                    WorkspaceNativeIcon {
                        x: 8; anchors.verticalCenter: parent.verticalCenter
                        iconKey: modelData.kind==="drive" ? "project_disk" : "project_folder"
                        iconSize: 15; isLight: dialog.isLight
                        strokeColor: active ? dialog.c.accent_hex : (locBtn.hovered ? dialog.c.FG : dialog.c.FG_MUTED)
                        accentColor: dialog.c.accent_hex
                    }
                    RakLabel {
                        x: 30; width: parent.width-38; height: parent.height
                        text: modelData.name; elide: Text.ElideRight
                        font.pixelSize: 11; font.weight: active ? Font.DemiBold : Font.Normal
                        verticalAlignment: Text.AlignVCenter
                        color: active ? dialog.c.FG_BRIGHT : (locBtn.hovered ? dialog.c.FG : dialog.c.FG2)
                        transformed: locBtn.transformed
                    }
                }
            }
            RakLabel { x: 8; y: 6; visible: !!modelData.header; text: modelData.header; font.pixelSize: 9; font.weight: Font.Bold; color: dialog.c.FG_MUTED }
        }
    }
    SmoothRectangle { x: 188; y: 108; width: 1; height: 270; color: dialog.c.b08 }
    ListView {
        id: files; objectName: "renxecFiles"; x: 197; y: 108; width: parent.width-x-28; height: 270; clip: true; spacing: 2; model: dialog.backend.rows
        C.ScrollBar.vertical: RakScrollBar { handleColor: dialog.c.BG_SCROLL }
        delegate: RakButton {
            id: fileBtn
            required property var modelData; required property int index
            readonly property string fileName: modelData.name
            readonly property bool chosen: dialog.multiSelect
                ? dialog.backend.selectedPaths.indexOf(modelData.path) >= 0
                : dialog.backend.selected===modelData.path
            objectName: "renxecFile"+index; width: files.width; height: 30; cornerRadius: 6
            leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
            fillColor: chosen ? dialog.c.a15 : "transparent"
            hoverFillColor: chosen ? dialog.c.a15 : dialog.c.b05
            edgeColor: chosen ? dialog.c.a40 : "transparent"
            hoverEdgeColor: chosen ? dialog.c.a40 : "transparent"
            contentItem: Item {
                Image { x: 10; y: 6; width: 18; height: 18; source: "image://icons/"+(modelData.icon || (modelData.folder?"folder":"archive")); sourceSize: Qt.size(18,18); smooth: true }
                RakLabel { x: 36; width: parent.width-114; height: parent.height; verticalAlignment: Text.AlignVCenter; text: modelData.name; elide: Text.ElideRight; font.pixelSize: 12; color: dialog.c.FG; transformed: fileBtn.transformed }
                RakLabel { anchors.right: parent.right; anchors.rightMargin: 10; height: parent.height; verticalAlignment: Text.AlignVCenter; text: modelData.size; font.pixelSize: 10; color: dialog.c.FG_MUTED; transformed: fileBtn.transformed }
            }
            onClicked: dialog.backend.select(modelData.path)
            onDoubleClicked: {filter.text="";dialog.backend.activate(modelData.path)}
        }
        RakLabel { visible: files.count===0; width: parent.width; y: 12; horizontalAlignment: Text.AlignHCenter; text: dialog.backend.error || dialog.emptyText; font.pixelSize: 11; color: dialog.c.FG_MUTED }
    }
    SmoothRectangle { x: 28; y: 388; width: parent.width-56; height: 1; color: dialog.c.b06 }
    RakLabel { x: 28; y: 398; width: parent.width-56; height: 18; verticalAlignment: Text.AlignVCenter; text: dialog.selectionText; elide: Text.ElideMiddle; font.pixelSize: 11; color: dialog.canConfirm ? dialog.c.FG2 : dialog.c.FG_MUTED }
    Row {
        anchors.right: parent.right; anchors.rightMargin: 28; y: 426; spacing: 8
        DialogButton { colors: dialog.c; text: "Cancel"; onClicked: dialog.dismiss() }
        DialogButton { objectName: "renxecOpen"; colors: dialog.c; primary: true; text: dialog.openText; enabled: dialog.canConfirm; onClicked: dialog.backend.openSelected() }
    }
}
