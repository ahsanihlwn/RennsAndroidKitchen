// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
Item {
    id: column
    required property var backend
    required property var preferences
    required property string caption
    readonly property var c: preferences.colors
    Row {
        spacing: 6; height: 26
        SmoothRectangle {
            width: 24; height: 24; radius: 6; anchors.verticalCenter: parent.verticalCenter
            color: column.caption==="A"?"#264d8ef0":"#266dba8a"
            border.width: 1; border.color: column.caption==="A"?"#594d8ef0":"#596dba8a"
            RakLabel { anchors.centerIn: parent; text: column.caption; font.pixelSize: 11; font.weight: Font.Bold; color: column.caption==="A"?"#7eb8f7":"#6dba8a" }
        }
        Repeater {
            model: column.backend.treeLabels
            delegate: RakButton {
                id: folderButton
                required property int index
                required property string modelData
                readonly property bool chosen: column.backend.tree===index
                objectName: "compare"+column.caption+"Tree"+index
                height: 24; implicitHeight: 24; anchors.verticalCenter: parent.verticalCenter
                leftPadding: 10; rightPadding: 10; topPadding: 0; bottomPadding: 0; cornerRadius: 5
                width: Math.min(Math.max(48, textMetrics.advanceWidth+20), (column.width-24-6*column.backend.treeLabels.length)/column.backend.treeLabels.length)
                font.pixelSize: 10; font.weight: chosen ? Font.DemiBold : Font.Normal
                fillColor: chosen ? column.c.a18 : "transparent"
                hoverFillColor: chosen ? column.c.a18 : column.c.b06
                edgeColor: chosen ? column.c.a40 : column.c.b10
                hoverEdgeColor: chosen ? column.c.a40 : column.c.b14
                labelColor: chosen ? column.c.FG_BRIGHT : column.c.FG_MUTED
                hoverLabelColor: column.c.FG_BRIGHT
                toolTip: modelData
                TextMetrics { id: textMetrics; text: folderButton.modelData; font: folderButton.font }
                contentItem: RakLabel {
                    text: folderButton.modelData; font: folderButton.font
                    color: folderButton.hovered ? folderButton.hoverLabelColor : folderButton.labelColor
                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight; transformed: folderButton.transformed
                }
                onClicked: column.backend.load(index)
            }
        }
    }
    ListView {
        id: list; y: 38; width: parent.width; height: parent.height-y; spacing: 2; clip: true; model: column.backend.rows
        C.ScrollBar.vertical: RakScrollBar { handleColor: column.c.BG_SCROLL }
        delegate: RakButton {
            id: partBtn
            required property var modelData; required property int index
            readonly property bool chosen: column.backend.selected===modelData.name
            objectName: "compare"+column.caption+"_"+index
            width: list.width; height: 34; cornerRadius: 6
            leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
            fillColor: chosen ? column.c.a15 : "transparent"
            hoverFillColor: chosen ? column.c.a15 : column.c.b05
            edgeColor: chosen ? column.c.a40 : "transparent"
            hoverEdgeColor: chosen ? column.c.a40 : "transparent"
            contentItem: Item {
                Image { x: 10; y: 8; width: 18; height: 18; source: "image://icons/"+modelData.icon; sourceSize: Qt.size(18,18); smooth: true }
                RakLabel { x: 36; width: parent.width-(modelData.type ? 96 : 44); height: parent.height; verticalAlignment: Text.AlignVCenter; text: modelData.name; elide: Text.ElideRight; font.pixelSize: 12; color: chosen ? column.c.FG_BRIGHT : column.c.FG; transformed: partBtn.transformed }
                SmoothRectangle {
                    anchors.right: parent.right; anchors.rightMargin: 10; anchors.verticalCenter: parent.verticalCenter
                    visible: !!modelData.type; height: 17; width: badge.implicitWidth+10; radius: 4
                    color: modelData.type==="EROFS"?"#3a5f8a":modelData.type==="EXT4"?"#3a6b4a":modelData.type==="SUPER"?"#5a3a70":column.c.b10
                    RakLabel { id: badge; anchors.centerIn: parent; text: modelData.type; font.pixelSize: 9; font.weight: Font.DemiBold; color: modelData.type==="EROFS"?"#7eb8f7":modelData.type==="EXT4"?"#7fcf9a":modelData.type==="SUPER"?"#b07ae0":column.c.FG_MUTED }
                }
            }
            onClicked: column.backend.choose(modelData.name)
        }
        RakLabel { visible: list.count===0; width: parent.width; y: 12; horizontalAlignment: Text.AlignHCenter; text: column.backend.busy ? "Loading…" : "No folders found"; font.pixelSize: 11; color: column.c.FG_MUTED }
    }
}
