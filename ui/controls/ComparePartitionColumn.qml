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
        spacing: 6; height: 28
        SmoothRectangle { width: 24; height: 24; radius: 6; color: column.caption==="A"?"#264d8ef0":"#266dba8a"; border.width: 1; border.color: column.caption==="A"?"#594d8ef0":"#596dba8a"; RakLabel { anchors.centerIn: parent; text: column.caption; font.pixelSize: 11; font.weight: Font.Bold; color: column.caption==="A"?"#7eb8f7":"#6dba8a" } }
        Repeater {
            model: column.backend.treeLabels
            delegate: AndroidToolButton {
                id: folderButton
                required property int index
                required property string modelData
                objectName: "compare"+column.caption+"Tree"+index
                width: Math.min(implicitWidth,(column.width-24-6*column.backend.treeLabels.length)/column.backend.treeLabels.length)
                colors: column.c; text: modelData; toolTip: modelData
                contentItem: RakLabel {
                    text: folderButton.text; font: folderButton.font; color: folderButton.labelColor
                    horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                    elide: Text.ElideRight
                }
                selected: column.backend.tree===index
                onClicked: column.backend.load(index)
            }
        }
    }
    RakLabel { y: 36; width: parent.width; height: 17; text: backend.caption; elide: Text.ElideRight; font.pixelSize: 11; color: c.FG_BLUE_MUTED }
    ListView {
        id: list; y: 65; width: parent.width; height: parent.height-y; spacing: 4; clip: true; model: column.backend.rows
        C.ScrollBar.vertical: RakScrollBar { handleColor: column.c.b20 }
        delegate: RakButton {
            required property var modelData; required property int index
            objectName: "compare"+column.caption+"_"+index
            width: list.width; height: 48; cornerRadius: 8
            fillColor: column.backend.selected===modelData.name?column.c.a15:column.c.b03; edgeColor: column.backend.selected===modelData.name?column.c.a40:column.c.b06; hoverFillColor: column.c.b07; hoverEdgeColor: column.c.b10
            contentItem: Item {
                Image { x: 12; y: 14; width: 20; height: 20; source: "image://icons/"+modelData.icon }
                RakLabel { x: 42; width: parent.width-50; height: parent.height; text: modelData.name; elide: Text.ElideRight; font.pixelSize: 13; color: column.c.FG }
            }
            onClicked: column.backend.choose(modelData.name)
        }
    }
}
