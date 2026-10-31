// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: dialog; objectName: "workspaceRenxecPicker"
    required property var workspace
    readonly property var backend: workspace.privateFeatures.filePicker
    implicitWidth: 720; implicitHeight: 480; panelAlpha: 128/255
    function showPicker() {filter.text="";open()}
    RakLabel { x: 28; y: 26; height: 26; text: "Select Renxec File"; font.pixelSize: 13; font.weight: Font.DemiBold; color: dialog.c.FG }
    RakField { id: filter; anchors.right: parent.right; anchors.rightMargin: 28; y: 26; width: 160; height: 26; placeholderText: "Filter…"; font.pixelSize: 11; topPadding: 1; bottomPadding: 0; fillColor: dialog.c.b04; edgeColor: dialog.c.b10; labelColor: dialog.c.FG; onTextEdited: dialog.backend.filter(text) }
    SmoothRectangle { x: 28; y: 62; width: parent.width-56; height: 1; color: dialog.c.b06 }
    RakButton { x: 28; y: 73; width: 70; height: 24; text: "‹  Back"; font.pixelSize: 12; edgeWidth: 0; labelColor: dialog.c.FG_DESC; hoverLabelColor: dialog.c.FG_BLUE_MUTED; onClicked: {filter.text="";dialog.backend.up()} }
    RakLabel { x: 102; y: 73; width: parent.width-130; height: 24; text: dialog.backend.directory; elide: Text.ElideMiddle; font.pixelSize: 11; color: dialog.c.FG_BLUE_MUTED }
    ListView {
        id: sidebar; x: 28; y: 107; width: 152; height: 271; clip: true; spacing: 1; model: dialog.backend.locations
        delegate: Item {
            required property var modelData; width: sidebar.width; height: modelData.header?24:30
            RakButton { anchors.fill: parent; visible: !modelData.header; text: "📁  "+modelData.name; leftAligned: true; font.pixelSize: 11; cornerRadius: 6; edgeColor: dialog.backend.directory===modelData.path?dialog.c.a45:"transparent"; fillColor: dialog.backend.directory===modelData.path?dialog.c.a15:"transparent"; hoverFillColor: dialog.c.BG_MODAL; labelColor: dialog.c.FG_BLUE_MUTED; onClicked: {filter.text="";dialog.backend.load(modelData.path)} }
            RakLabel { x: 8; y: 4; visible: !!modelData.header; text: modelData.header; font.pixelSize: 9; font.weight: Font.Bold; color: dialog.c.FG_DISABLED }
        }
    }
    SmoothRectangle { x: 188; y: 107; width: 1; height: 271; color: dialog.c.b10 }
    ListView {
        id: files; objectName: "renxecFiles"; x: 197; y: 109; width: parent.width-x-28; height: 269; clip: true; spacing: 3; model: dialog.backend.rows
        C.ScrollBar.vertical: RakScrollBar { handleColor: dialog.c.BG_SCROLL }
        delegate: RakButton {
            required property var modelData; required property int index
            objectName: "renxecFile"+index; width: files.width; height: 36; cornerRadius: 6
            edgeColor: dialog.backend.selected===modelData.path?dialog.c.a45:"transparent"; fillColor: dialog.backend.selected===modelData.path?dialog.c.a15:"transparent"; hoverFillColor: dialog.c.b05
            contentItem: Item {
                Image { x: 10; y: 9; width: 18; height: 18; source: "image://icons/"+(modelData.folder?"folder":"archive") }
                RakLabel { x: 36; width: parent.width-112; height: parent.height; text: modelData.name; elide: Text.ElideRight; font.pixelSize: 12; color: dialog.c.FG }
                RakLabel { anchors.right: parent.right; anchors.rightMargin: 10; height: parent.height; text: modelData.size; font.pixelSize: 10; color: dialog.c.FG_DESC }
            }
            onClicked: dialog.backend.select(modelData.path)
            onDoubleClicked: {filter.text="";dialog.backend.activate(modelData.path)}
        }
        RakLabel { visible: files.count===0; width: parent.width; y: 12; horizontalAlignment: Text.AlignHCenter; text: dialog.backend.error || "No .renxec or .zip files found"; font.pixelSize: 11; color: dialog.c.FG_BLUE_MUTED }
    }
    SmoothRectangle { x: 28; y: 388; width: parent.width-56; height: 1; color: dialog.c.b06 }
    RakLabel { x: 28; y: 399; width: parent.width-56; height: 17; text: dialog.backend.selected || "No file selected"; elide: Text.ElideMiddle; font.pixelSize: 11; color: dialog.c.FG_BLUE_MUTED }
    Row {
        anchors.right: parent.right; anchors.rightMargin: 28; y: 426; spacing: 8
        DialogButton { colors: dialog.c; text: "Cancel"; onClicked: dialog.dismiss() }
        DialogButton { objectName: "renxecOpen"; colors: dialog.c; primary: true; text: "Open"; enabled: !!dialog.backend.selected; onClicked: dialog.backend.activate(dialog.backend.selected) }
    }
}
