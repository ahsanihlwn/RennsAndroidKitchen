// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: dialog
    objectName: "workspaceOpenWith"
    required property var workspace
    property var details: ({})
    property string choice: ""
    property bool always: false
    readonly property real chromeHeight: header.height+164
    implicitWidth: 380
    implicitHeight: Math.min(chromeHeight+464,hostWindow.height-24)
    panelRadius: 18
    readonly property color tintBase: c.BG2
    background: Item {
        LiveGlass {
            preferences: dialog.model
            x: 8; y: 8; width: parent.width-16; height: parent.height-16
            backdropSource: dialog.backdropSource; origin: Qt.point(dialog.x+8,dialog.y+8)
            effectsEnabled: dialog.model.settings.effects!=="low"; cornerRadius: 18
            tintColor: Qt.rgba(dialog.tintBase.r,dialog.tintBase.g,dialog.tintBase.b,170/255)
            flatColor: dialog.c.BG_MODAL; edgeColor: dialog.model.panelBorder
        }
    }
    function showPicker(data) {details=data;choice="";always=false;open()}
    function choose() {if(choice.length){workspace.chooseViewer(details.path,choice,always);dismiss()}}
    Column {
        id: header; x: 28; y: 26; width: parent.width-56; spacing: 12
        RakLabel { text: dialog.s.ow_title; color: dialog.c.FG; font.pixelSize: 13; font.weight: Font.DemiBold }
        RakLabel { width: parent.width; text: dialog.details.name || ""; color: dialog.c.FG_BLUE_MUTED; font.pixelSize: 11; wrapMode: Text.WordWrap; maximumLineCount: 3; elide: Text.ElideRight; toolTip: dialog.details.name || "" }
    }
    SmoothRectangle { x: 28; y: header.y+header.height+12; width: parent.width-56; height: 1; color: dialog.c.b06 }
    Flickable {
        id: viewport
        objectName: "workspaceOpenWithOptions"
        x: 28; y: header.y+header.height+25; width: parent.width-56; height: parent.height-dialog.chromeHeight
        contentWidth: width; contentHeight: cards.height; clip: true; boundsBehavior: Flickable.StopAtBounds
        Column {
            id: cards; width: viewport.width; spacing: 12
            Repeater {
                model: [{key:"editor",icon:"edit",label:dialog.s.ow_editor},{key:"so",icon:"icon_so",label:dialog.s.ow_so},
                        {key:"bin",icon:"bin",label:dialog.s.ow_bin},{key:"image",icon:"image",label:dialog.s.ow_image},
                        {key:"archive",icon:"archive",label:dialog.s.ow_archive},{key:"payload",icon:"icon_bin",label:dialog.s.ow_payload},
                        {key:"super",icon:"drive",label:dialog.s.ow_super}]
                delegate: RakButton {
                    id: card
                    required property var modelData
                    objectName: "workspaceOpenWithOption_"+modelData.key
                    width: cards.width; height: 56; implicitHeight: 56; cornerRadius: 8; edgeWidth: 1
                    leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
                    readonly property bool chosen: dialog.choice===modelData.key
                    checked: chosen
                    fillColor: chosen?dialog.c.a15:dialog.c.b04
                    hoverFillColor: chosen?dialog.c.a18:dialog.c.b07
                    downFillColor: dialog.c.a22
                    edgeColor: chosen?dialog.c.a40:dialog.c.b08
                    hoverEdgeColor: chosen?dialog.c.a40:dialog.c.b12
                    onPressed: dialog.choice=card.modelData.key
                    onDoubleClicked: dialog.choose()
                    contentItem: Item {
                        Image { x: 12; y: 14; width: 28; height: 28; source: "image://icons/"+card.modelData.icon; sourceSize: Qt.size(28,28) }
                        RakLabel { x: 52; width: parent.width-64; height: parent.height; text: card.modelData.label; color: dialog.c.FG; font.pixelSize: 13; font.weight: Font.DemiBold; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; transformed: card.transformed }
                    }
                }
            }
        }
    }
    SmoothRectangle { x: 28; y: viewport.y+viewport.height+12; width: parent.width-56; height: 1; color: dialog.c.b06 }
    Row {
        x: 28; y: viewport.y+viewport.height+25; width: parent.width-56; spacing: 10
        RakButton {
            objectName: "workspaceOpenWithRemember"; width: 20; height: 20; implicitWidth: 20; implicitHeight: 20; leftPadding: 0; rightPadding: 0
            checked: dialog.always; checkable: true; cornerRadius: 4; fillColor: checked?dialog.c.a30:dialog.c.b06
            edgeColor: checked?dialog.c.a60:dialog.c.b18; hoverFillColor: dialog.c.a15
            iconSource: checked?"image://icons/check_white":""; iconWidth: 14; iconHeight: 14
            onToggled: dialog.always=checked
        }
        RakLabel { width: parent.width-30; height: 20; text: dialog.s.ow_always; color: dialog.c.FG_MUTED; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter; wrapMode: Text.WordWrap; MouseArea {anchors.fill: parent;cursorShape: Qt.PointingHandCursor;onClicked: dialog.always=!dialog.always} }
    }
    Row {
        anchors.right: parent.right; anchors.rightMargin: 28; y: viewport.y+viewport.height+57; spacing: 8
        DialogButton { colors: dialog.c; text: dialog.s.btn_cancel || dialog.s.main_btn_cancel; onClicked: dialog.dismiss() }
        DialogButton { objectName: "workspaceOpenWithConfirm"; colors: dialog.c; text: dialog.s.ow_open; primary: true; enabled: dialog.choice.length>0; onClicked: dialog.choose() }
    }
}
