// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: dialog
    objectName: "workspaceBranchPicker"
    required property var workspace
    property var details: ({root:"",active:"",branches:[]})
    implicitWidth: 480; implicitHeight: 420
    function showPicker(data) {details=data;open()}
    function pick(choice) {dismiss();workspace.branches.choose(choice)}
    WorkspaceBranchHeader { x: 18; y: 16; height: 20; colors: dialog.c; root: dialog.details.root }
    SmoothRectangle { x: 18; y: 48; width: parent.width-36; height: 1; color: dialog.c.b07 }
    SmoothRectangle {
        x: 18; y: 61; width: parent.width-36; height: parent.height-132; color: dialog.c.BG4
        Flickable {
            anchors.fill: parent; contentWidth: width; contentHeight: card.height; clip: true; boundsBehavior: Flickable.StopAtBounds
            C.ScrollBar.vertical: RakScrollBar { width: 4; handleColor: dialog.c.b12 }
            SmoothRectangle {
                id: card; width: parent.width; height: list.height+12; radius: 10; color: dialog.c.b03; border.width: 1; border.color: dialog.c.b07
                Column {
                    id: list; y: 6; width: parent.width
                    Repeater {
                        model: [{name:"",color:""}].concat(dialog.details.branches || [])
                        delegate: Item {
                            id: branch
                            required property var modelData
                            required property int index
                            readonly property bool isActive: modelData.name===dialog.details.active
                            readonly property bool isSource: !modelData.name
                            readonly property color branchColor: {
                                const colors=dialog.workspace.branches.colors
                                for(let n=0;n<colors.length;++n)if(colors[n].name===modelData.color)return colors[n].color
                                return dialog.c.FG_DISABLED
                            }
                            width: list.width; height: 53
                            RakLabel { x: 13; y: 0; width: 14; height: 52; text: "●"; color: branch.branchColor; font.pixelSize: 12; verticalAlignment: Text.AlignVCenter }
                            RakLabel { x: 37; y: 8; width: Math.max(30,buttons.x-47); text: branch.isSource?"Source":branch.modelData.name; color: branch.isSource?dialog.c.FG_DESC:dialog.c.FG; font.pixelSize: 13; font.weight: branch.isActive?Font.Bold:Font.Medium; elide: Text.ElideRight }
                            RakLabel { x: 37; y: 29; text: branch.isActive?"Active now":branch.isSource?"Original filesystem, no branch":""; color: branch.isActive?dialog.c.accent_hex:dialog.c.FG_DISABLED; font.pixelSize: 11 }
                            Row {
                                id: buttons; anchors.right: parent.right; anchors.rightMargin: 12; y: Math.floor((branch.height-height)/2); spacing: 10
                                RakLabel { visible: branch.isActive; text: "Active ✓"; font.pixelSize: 11; color: dialog.c.accent_hex; height: 26; verticalAlignment: Text.AlignVCenter }
                                RakButton {
                                    objectName: "workspaceBranchSwitch_"+(branch.modelData.name || "source")
                                    visible: !branch.isActive; height: 26; implicitWidth: Math.max(80,contentItem.implicitWidth+26)
                                    text: branch.isSource?"Use Source":"Switch"; cornerRadius: 13; font.pixelSize: 11; font.weight: Font.DemiBold
                                    fillColor: dialog.c.a12; hoverFillColor: dialog.c.a24; downFillColor: dialog.c.a36
                                    edgeColor: dialog.c.a28; hoverEdgeColor: dialog.c.a50
                                    labelColor: dialog.c.FG_BLUE; hoverLabelColor: dialog.c.FG_BLUE_LIGHT
                                    onClicked: dialog.pick(branch.isSource?"NONE":branch.modelData.name)
                                }
                                RakButton {
                                    objectName: "workspaceBranchDelete_"+branch.modelData.name
                                    visible: !branch.isActive&&!branch.isSource; height: 26; implicitWidth: contentItem.implicitWidth+22
                                    text: "Delete"; cornerRadius: 13; font.pixelSize: 11; leftPadding: 10; rightPadding: 10
                                    fillColor: "transparent"; hoverFillColor: dialog.c.red14; edgeColor: Qt.rgba(220/255,70/255,70/255,.2); hoverEdgeColor: Qt.rgba(220/255,70/255,70/255,.45); labelColor: dialog.c.RED_BTN
                                    onClicked: dialog.pick("__DEL__"+branch.modelData.name)
                                }
                            }
                            SmoothRectangle { visible: branch.index<(dialog.details.branches || []).length; y: parent.height-1; width: parent.width; height: 1; color: dialog.c.b05 }
                        }
                    }
                }
            }
        }
    }
    SmoothRectangle { x: 18; y: parent.height-59; width: parent.width-36; height: 1; color: dialog.c.b07 }
    DialogButton { objectName: "workspaceBranchPickerNew"; x: 18; y: parent.height-46; colors: dialog.c; primary: true; text: "+ Create New Branch"; onClicked: dialog.pick("__NEW__") }
    DialogButton { objectName: "workspaceBranchPickerClose"; anchors.right: parent.right; anchors.rightMargin: 18; y: parent.height-46; colors: dialog.c; text: "Close"; onClicked: dialog.dismiss() }
}
