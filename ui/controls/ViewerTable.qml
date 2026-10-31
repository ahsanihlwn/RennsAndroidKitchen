// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

Item {
    id: table
    required property var colors
    required property var rows
    required property var columns
    property int gutter: 33
    property var resized: ({})
    property var selectedRows: []
    property alias currentIndex: list.currentIndex
    property alias listItem: list
    readonly property var widths: {
        let fixed=0,stretch=0
        for(let i=0;i<columns.length;i++) {if(columns[i].stretch)stretch++;else fixed+=(resized[i] || columns[i].width || 100)}
        let result=[]
        for(let i=0;i<columns.length;i++)result.push(columns[i].stretch?Math.max(60,Math.floor((width-gutter-2-fixed)/Math.max(1,stretch))):(resized[i] || columns[i].width || 100))
        return result
    }
    readonly property real bodyWidth: widths.reduce((a,b)=>a+b,0)
    signal rowActivated(int row)
    function columnX(index) {let x=0;for(let i=0;i<index;i++)x+=widths[i];return x}
    function selectRow(index,modifiers) {
        if(modifiers & Qt.ShiftModifier && currentIndex>=0) {
            let result=[];for(let i=Math.min(currentIndex,index);i<=Math.max(currentIndex,index);i++)result.push(i);selectedRows=result
        } else if(modifiers & Qt.ControlModifier) {
            let result=selectedRows.slice();const at=result.indexOf(index);if(at<0)result.push(index);else result.splice(at,1);selectedRows=result
        } else {selectedRows=[index];list.selectionAnchor=index}
        currentIndex=index;list.forceActiveFocus()
    }
    SmoothRectangle { anchors.fill: parent; color: table.colors.BG; radius: 4; border.width: 1; border.color: table.colors.b06 }
    Flickable {
        id: horizontal; anchors.fill: parent; anchors.margins: 1; clip: true
        contentWidth: table.bodyWidth+table.gutter; contentHeight: height; boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.HorizontalFlick
        C.ScrollBar.horizontal: RakScrollBar { height: 5; orientation: Qt.Horizontal; handleColor: table.colors.b30; hoverColor: table.colors.b50 }
        SmoothRectangle { x: 0; y: 37; width: table.gutter; height: parent.height-37; color: table.colors.BG2 }
        Row {
            x: table.gutter; height: 38
            Repeater {
                model: table.columns
                delegate: SmoothRectangle {
                    id: header; required property int index; required property var modelData
                    width: table.widths[index]; height: 38; color: table.colors.BG2
                    RakLabel { anchors.fill: parent; text: header.modelData.title; font.pixelSize: 11; font.bold: true; color: table.colors.FG_LABEL; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                    SmoothRectangle { anchors.right: parent.right; width: 1; height: parent.height; color: table.colors.b12 }
                    SmoothRectangle { anchors.bottom: parent.bottom; height: 1; width: parent.width; color: table.colors.b12 }
                    MouseArea {
                        width: 6; height: parent.height; x: parent.width-3; enabled: !header.modelData.stretch; cursorShape: Qt.SplitHCursor
                        property real startX; property real startWidth
                        onPressed: mouse=>{startX=mapToItem(table,mouse.x,mouse.y).x;startWidth=header.width}
                        onPositionChanged: mouse=>{if(pressed){let copy=Object.assign({},table.resized);copy[header.index]=Math.max(40,startWidth+mapToItem(table,mouse.x,mouse.y).x-startX);table.resized=copy}}
                    }
                }
            }
        }
        ListView {
            id: list; objectName: table.objectName+"Rows"; x: 0; y: 38
            width: horizontal.contentWidth; height: horizontal.height-38; model: table.rows
            currentIndex: -1; keyNavigationEnabled: true; clip: true; boundsBehavior: Flickable.StopAtBounds
            property int selectionAnchor: -1
            C.ScrollBar.vertical: RakScrollBar { x: horizontal.contentX+horizontal.width-width; width: 5; handleColor: table.colors.b30; hoverColor: table.colors.b50 }
            Connections { target: table.rows; function onReplaced(){table.selectedRows=[];list.currentIndex=-1} }
            delegate: Item {
                id: row; required property int index; required property var modelData
                width: list.width; height: 30
                readonly property bool selected: table.selectedRows.indexOf(index)>=0
                SmoothRectangle { x: table.gutter; width: table.bodyWidth; height: parent.height; color: row.selected?table.colors.a20:rowMouse.containsMouse?table.colors.b04:"transparent" }
                RakLabel { x: 8; height: 30; width: table.gutter-12; text: String(row.index+1); color: table.colors.FG_LABEL; font.pixelSize: 11; font.bold: true; verticalAlignment: Text.AlignVCenter }
                Repeater {
                    model: table.columns
                    delegate: Item {
                        required property int index
                        x: table.gutter+table.columnX(index); width: table.widths[index]; height: 30
                        RakLabel { x: 11; width: parent.width-20; height: 30; text: row.modelData.cells[parent.index] || ""; color: row.selected?table.colors.FG_BRIGHT:parent.index===0 && row.modelData.firstColor?table.colors[row.modelData.firstColor]:table.colors.FG; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
                        SmoothRectangle { anchors.right: parent.right; width: 1; height: parent.height; color: table.colors.b03 }
                    }
                }
                SmoothRectangle { anchors.bottom: parent.bottom; height: 1; width: parent.width; color: table.colors.b03 }
                MouseArea { id: rowMouse; anchors.fill: parent; hoverEnabled: true; onClicked: mouse=>table.selectRow(row.index,mouse.modifiers); onDoubleClicked: table.rowActivated(row.index) }
            }
            Keys.onReturnPressed: table.rowActivated(currentIndex)
            Keys.onEnterPressed: table.rowActivated(currentIndex)
            Keys.onPressed: event=>{
                if(event.key===Qt.Key_A && event.modifiers & Qt.ControlModifier){let selected=[];for(let i=0;i<count;i++)selected.push(i);table.selectedRows=selected;event.accepted=true;return}
                let next=currentIndex
                if(event.key===Qt.Key_Down)next=Math.min(count-1,currentIndex+1)
                else if(event.key===Qt.Key_Up)next=Math.max(0,currentIndex-1)
                else if(event.key===Qt.Key_Home)next=0
                else if(event.key===Qt.Key_End)next=count-1
                else if(event.key===Qt.Key_PageDown)next=Math.min(count-1,currentIndex+Math.max(1,Math.floor(height/30)))
                else if(event.key===Qt.Key_PageUp)next=Math.max(0,currentIndex-Math.max(1,Math.floor(height/30)))
                else return
                if(count===0)return
                if(event.modifiers & Qt.ShiftModifier) {
                    if(selectionAnchor<0)selectionAnchor=Math.max(0,currentIndex)
                    let selected=[];for(let i=Math.min(selectionAnchor,next);i<=Math.max(selectionAnchor,next);i++)selected.push(i);table.selectedRows=selected
                } else if(!(event.modifiers & Qt.ControlModifier)){table.selectedRows=[next];selectionAnchor=next}
                currentIndex=next;positionViewAtIndex(next,ListView.Contain);event.accepted=true
            }
        }
    }
}
