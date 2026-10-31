// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: dialog; objectName: "workspacePartitionPicker"
    required property var workspace
    readonly property var backend: workspace.partitionPicker
    property bool accepted: false
    implicitWidth: 400; implicitHeight: backend.dual?454:414
    panelAlpha: 128/255
    function showPicker() {accepted=false;open()}
    onDismissed: backend.finish(accepted)
    Column {
        x: 28; y: 26; width: parent.width-56; spacing: 12
        RakLabel { height: 20; text: "Select Partition"; font.pixelSize: 13; font.weight: Font.DemiBold; color: dialog.c.FG }
        Row {
            visible: dialog.backend.dual; height: 28; spacing: 6
            Repeater {
                model: ["Tree 1","Tree 2"]
                delegate: RakButton { required property int index; required property string modelData; objectName: "partitionPickerTree"+index; text: modelData; height: 28; implicitWidth: 92; leftPadding: 14; rightPadding: 14; cornerRadius: 7; font.pixelSize: 12; font.weight: dialog.backend.tree===index?Font.DemiBold:Font.Normal; fillColor: dialog.backend.tree===index?dialog.c.a18:"transparent"; edgeColor: dialog.backend.tree===index?dialog.c.a40:"transparent"; labelColor: dialog.backend.tree===index?dialog.c.FG_BLUE:dialog.c.FG_DESC; hoverFillColor: dialog.c.b05; onClicked: dialog.backend.load(index) }
            }
        }
        SmoothRectangle { width: parent.width; height: 1; color: dialog.c.b06 }
        RakLabel { height: 17; text: "Project: "+dialog.backend.caption; font.pixelSize: 11; color: dialog.c.FG_BLUE_MUTED }
        ListView {
            id: list; objectName: "partitionPickerList"; width: parent.width; height: 220; clip: true; topMargin: 2; bottomMargin: 2; spacing: 4; model: dialog.backend.rows
            C.ScrollBar.vertical: RakScrollBar { width: 6; handleColor: dialog.c.b20; hoverColor: dialog.c.b35 }
            delegate: RakButton {
                required property var modelData; required property int index
                readonly property bool selected: dialog.backend.selected===modelData.name
                objectName: "partitionPickerRow"+index; width: list.width; height: 48; cornerRadius: 8
                fillColor: selected?dialog.c.a15:dialog.c.b03; hoverFillColor: selected?dialog.c.a15:dialog.c.b07
                edgeColor: selected?dialog.c.a40:dialog.c.b06; hoverEdgeColor: selected?dialog.c.a40:dialog.c.b10
                contentItem: Item {
                    Image { x: 12; y: 14; width: 20; height: 20; source: "image://icons/"+modelData.icon; smooth: true }
                    RakLabel { x: 42; width: parent.width-108; height: parent.height; text: modelData.name; elide: Text.ElideRight; font.pixelSize: 13; color: dialog.c.FG }
                    SmoothRectangle {
                        anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
                        visible: !!modelData.type; height: 18; width: badge.implicitWidth+12; radius: 4
                        color: modelData.type==="EROFS"?"#3a5f8a":modelData.type==="EXT4"?"#3a6b4a":modelData.type==="SUPER"?"#5a3a70":dialog.c.b10
                        RakLabel { id: badge; anchors.centerIn: parent; text: modelData.type; font.pixelSize: 10; font.weight: Font.DemiBold; color: modelData.type==="EROFS"?"#7eb8f7":modelData.type==="EXT4"?"#7fcf9a":modelData.type==="SUPER"?"#b07ae0":dialog.c.FG_BLUE_MUTED }
                    }
                }
                onClicked: dialog.backend.choose(modelData.name)
            }
            RakLabel { visible: list.count===0; width: parent.width; y: 12; horizontalAlignment: Text.AlignHCenter; text: dialog.backend.busy?"Loading…":"No filesystem partitions found."; font.pixelSize: 11; color: dialog.c.FG_BLUE_MUTED }
        }
        SmoothRectangle { width: parent.width; height: 1; color: dialog.c.b06 }
        Row {
            anchors.right: parent.right; spacing: 8
            DialogButton { colors: dialog.c; text: "Cancel"; onClicked: dialog.dismiss() }
            DialogButton { objectName: "partitionPickerSelect"; colors: dialog.c; primary: true; enabled: !!dialog.backend.selected; text: "Select"; onClicked: {dialog.accepted=true;dialog.dismiss()} }
        }
    }
}
