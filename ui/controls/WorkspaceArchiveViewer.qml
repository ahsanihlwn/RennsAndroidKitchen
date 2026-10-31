// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import Renns.Workspace
Item {
    id: viewer; objectName: "workspaceArchiveViewer"
    required property var workspace
    required property var preferences
    required property var details
    readonly property var c: preferences.colors
    readonly property var documentModel: document
    ArchiveDocument { id: document }
    Component.onCompleted: document.load(details.documentPath || details.path)
    Component.onDestruction: document.close()
    SmoothRectangle { anchors.fill: parent; color: viewer.c.BG3 }
    ListView {
        id: tree; objectName: "archiveTree"; width: parent.width; height: parent.height-24; model: document.rows; clip: true
        C.ScrollBar.vertical: RakScrollBar { width: 4; handleColor: viewer.c.b35; hoverColor: viewer.c.b55 }
        add: Transition { NumberAnimation { property: "height"; from: 0; to: 28; duration: viewer.preferences.settings.animations?200:0; easing.type: Easing.OutCubic } }
        remove: Transition { NumberAnimation { property: "height"; to: 0; duration: viewer.preferences.settings.animations?200:0; easing.type: Easing.OutCubic } }
        delegate: Item {
            id: row; required property var modelData; required property int index; width: tree.width; height: 28
            SmoothRectangle { x: 14*(row.modelData.depth+1); width: parent.width-x; height: parent.height; color: row.modelData.selected?(mouse.containsMouse?viewer.c.a42:viewer.c.a35):mouse.containsMouse?viewer.c.b06:"transparent" }
            Image { x: row.modelData.depth*14+2; y: 9; width: 9; height: 9; visible: row.modelData.folder; rotation: row.modelData.expanded?90:0; opacity: .8; source: "image://icons/arrow_right"; sourceSize: Qt.size(9,9) }
            Image { x: row.modelData.depth*14+18; y: 6; width: 16; height: 16; source: "image://icons/"+row.modelData.icon; sourceSize: Qt.size(16,16) }
            RakLabel { x: row.modelData.depth*14+40; y: 1; height: 28; width: parent.width-x-8; font.pixelSize: 13; text: row.modelData.name; color: row.modelData.selected?viewer.c.FG_BRIGHT:viewer.c.FG; elide: Text.ElideRight }
            MouseArea {
                id: mouse; anchors.fill: parent; hoverEnabled: true; drag.target: dragProxy
                onClicked: event=>{if(event.x<row.modelData.depth*14+14)document.toggle(row.index)}
                onPressed: event=>{document.select(row.index,event.modifiers)}
                onDoubleClicked: document.toggle(row.index)
                onReleased: if(dragProxy.Drag.active)dragProxy.Drag.drop()
            }
            Item { id: dragProxy; width: 1; height: 1; Drag.active: mouse.drag.active; Drag.dragType: Drag.Automatic; Drag.supportedActions: Qt.CopyAction; Drag.mimeData: ({"application/x-rdt-archive":document.selected.join("\n"),"application/x-rak-archive-source":viewer.details.documentPath || viewer.details.path}) }
        }
    }
    SmoothRectangle { y: parent.height-24; width: parent.width; height: 24; color: viewer.c.BG2; SmoothRectangle { width: parent.width; height: 1; color: viewer.c.b06 } }
    RakLabel { x: 10; y: parent.height-24; height: 24; width: parent.width-20; text: document.status; color: viewer.c.FG3; font.pixelSize: 10; elide: Text.ElideRight }
}
