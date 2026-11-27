// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

ListView {
    id: list
    required property var colors
    required property var document
    property bool superMode: false
    property string side: "a"
    readonly property real rowHeight: superMode?26:32
    clip: true; boundsBehavior: Flickable.StopAtBounds
    model: side==="a"?document.partsA:document.partsB
    C.ScrollBar.vertical: RakScrollBar { width: 4; handleColor: list.superMode?list.colors.b28:list.colors.b35; hoverColor: list.superMode?list.colors.b45:list.colors.b55 }
    delegate: Item {
        id: row; required property string modelData; required property int index
        width: list.width; height: list.rowHeight
        readonly property bool selected: list.document.selected.indexOf(modelData)>=0
        readonly property bool hovered: selection.containsMouse && Math.floor((selection.mouseY+list.contentY)/list.rowHeight)===index
        SmoothRectangle { anchors.fill: parent; color: row.selected?(list.superMode?list.colors.a35:row.hovered?list.colors.a25:list.colors.a18):row.hovered?(list.superMode?list.colors.b06:list.colors.b045):"transparent" }
        WorkspaceNativeIcon {
            x: list.superMode ? 8 : 11; y: Math.round((parent.height - 16) / 2); width: 16; height: 16; iconSize: 16
            iconKey: "drive"
            strokeColor: row.selected ? list.colors.FG_BRIGHT : (list.colors.FG_LABEL || list.colors.FG)
            accentColor: list.colors.accent_hex || "#38bdf8"
            isLight: !!(list.colors && list.colors.is_light)
        }
        RakLabel { x: list.superMode?31:33; y: 1; width: parent.width-x-8; height: parent.height; text: row.modelData; font.pixelSize: 13; color: row.selected?list.colors.FG_BRIGHT:list.colors.FG_LABEL; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
    }
    MouseArea {
        id: selection; anchors.fill: parent; hoverEnabled: true
        property int lastRow: -1
        function rowAt(y){return Math.floor((y+list.contentY)/list.rowHeight)}
        onPressed: mouse=>{lastRow=rowAt(mouse.y);list.document.select(list.side,lastRow,mouse.modifiers)}
        onPositionChanged: mouse=>{const index=rowAt(mouse.y);if(pressed && index!==lastRow){lastRow=index;list.document.select(list.side,index,mouse.modifiers | Qt.ShiftModifier)}}
        onWheel: wheel=>wheel.accepted=false
        Timer {
            interval: 40; repeat: true
            running: selection.pressed && (selection.mouseY<20 || selection.mouseY>selection.height-20)
            onTriggered: {
                list.contentY=Math.max(0,Math.min(Math.max(0,list.contentHeight-list.height),list.contentY+(selection.mouseY<20?-12:12)))
                const index=Math.max(0,Math.min(list.count-1,selection.rowAt(selection.mouseY)))
                list.document.select(list.side,index,Qt.ShiftModifier)
            }
        }
    }
}
