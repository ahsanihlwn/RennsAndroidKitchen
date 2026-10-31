// SPDX-License-Identifier: MPL-2.0
import QtQuick

DialogWindow {
    id: dialog
    objectName: "workspacePermissions"
    required property var workspace
    property string current: ""
    property var bits: [false,false,false,false,false,false,false,false,false]
    implicitWidth: 316; implicitHeight: 300
    function showPermissions(data) {
        current=data.current || "---------"
        let values=[]
        for(let n=0;n<9;++n)values.push(current.charAt(n)!=="-")
        bits=values;open()
    }
    function permission() {
        let result="0"
        for(let row=0;row<3;++row)result+=String((bits[row*3]?4:0)+(bits[row*3+1]?2:0)+(bits[row*3+2]?1:0))
        return result
    }
    RakLabel { x: 20; y: 18; text: "File Permissions"; color: dialog.c.FG; font.pixelSize: 14; font.weight: Font.DemiBold }
    RakLabel { x: 20; y: 46; text: "Current: "+dialog.current; color: dialog.c.FG_DESC; font.pixelSize: 11 }
    SmoothRectangle { x: 20; y: 76; width: parent.width-40; height: 1; color: dialog.c.b07 }
    Item {
        x: 20; y: 89; width: parent.width-40; height: 142
        Repeater {
            model: ["Read","Write","Exec"]
            delegate: RakLabel { required property int index; required property string modelData; x: 80+index*65; width: 60; height: 18; text: modelData; color: dialog.c.FG_DESC; font.pixelSize: 11; horizontalAlignment: Text.AlignHCenter }
        }
        Repeater {
            model: ["Owner","Group","Others"]
            delegate: RakLabel { required property int index; required property string modelData; x: 0; y: 30+index*34; height: 20; text: modelData; color: dialog.c.FG; font.pixelSize: 13; font.weight: Font.Medium }
        }
        Repeater {
            model: 9
            delegate: RakButton {
                required property int index
                objectName: "permissionBit_"+index
                x: 100+(index%3)*65; y: 30+Math.floor(index/3)*34
                width: 20; height: 20; implicitWidth: 20; implicitHeight: 20; leftPadding: 0; rightPadding: 0
                cornerRadius: 4; fillColor: checked?dialog.c.a30:dialog.c.b06
                edgeColor: checked?dialog.c.a60:dialog.c.b18; hoverEdgeColor: dialog.c.a60
                hoverFillColor: checked?dialog.c.a40:dialog.c.b10
                checkable: true; checked: !!dialog.bits[index]
                iconSource: checked?"image://rak/check_white":""; iconWidth: 14; iconHeight: 14
                onToggled: dialog.bits[index]=checked
            }
        }
    }
    SmoothRectangle { x: 20; y: parent.height-60; width: parent.width-40; height: 1; color: dialog.c.b07 }
    Row {
        anchors.right: parent.right; anchors.rightMargin: 20; y: parent.height-46; spacing: 8
        DialogButton { colors: dialog.c; text: "Cancel"; onClicked: dialog.dismiss() }
        DialogButton { objectName: "permissionApply"; colors: dialog.c; primary: true; text: "Apply"; onClicked: {workspace.dispatch("applyPermission",{value:dialog.permission()});dialog.dismiss()} }
    }
}
