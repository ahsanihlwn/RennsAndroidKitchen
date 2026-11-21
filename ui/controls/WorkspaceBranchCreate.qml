// SPDX-License-Identifier: MPL-2.0
import QtQuick

DialogWindow {
    id: dialog
    objectName: "workspaceBranchCreate"
    required property var workspace
    property string root: ""
    property string chosenColor: "blue"
    implicitWidth: 440; implicitHeight: form.height+32
    function showCreate(data) {root=data.root;chosenColor="blue";nameInput.text="";open()}
    function submit() {
        if(!createButton.enabled)return
        const name=nameInput.text.trim(), color=chosenColor
        dismiss();workspace.branches.create(name,color)
    }
    Column {
        id: form; x: 18; y: 16; width: parent.width-36; spacing: 12
        WorkspaceBranchHeader { colors: dialog.c; root: dialog.root; title: "Create NeoBranch" }
        SmoothRectangle { width: parent.width; height: 1; color: dialog.c.b07 }
        RakLabel { width: parent.width; height: 33; text: "Source filesystem is never modified. All edits are stored in a separate branch layer."; font.pixelSize: 11; color: dialog.c.FG_DESC; wrapMode: Text.WordWrap }
        RakLabel { height: 23; text: "Branch Name"; font.pixelSize: 12; font.weight: Font.DemiBold; color: dialog.c.FG_BLUE_MUTED; verticalAlignment: Text.AlignVCenter }
        RakField {
            id: nameInput; objectName: "workspaceBranchName"
            width: parent.width; height: 34; placeholderText: "nfc_test  (letters, numbers, _ , -)"
            font.family: "Consolas"; font.pixelSize: 13; cornerRadius: 7
            fillColor: dialog.c.b04; focusFillColor: dialog.c.a05
            edgeColor: dialog.c.b10; focusEdgeColor: dialog.c.a50; labelColor: dialog.c.FG; hintColor: dialog.c.placeholder
            leftPadding: 13; rightPadding: 13; topPadding: 7; bottomPadding: 5
            Keys.onReturnPressed: dialog.submit()
        }
        RakLabel { height: 24; text: "Branch Color"; font.pixelSize: 12; font.weight: Font.DemiBold; color: dialog.c.FG_BLUE_MUTED; verticalAlignment: Text.AlignVCenter }
        Row {
            height: 20; spacing: 8
            Repeater {
                model: dialog.workspace.branches.colors
                delegate: RakButton {
                    required property var modelData
                    objectName: "workspaceBranchColor_"+modelData.name
                    width: 20; height: 20; implicitWidth: 20; implicitHeight: 20
                    leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
                    cornerRadius: 10; edgeWidth: 2
                    fillColor: modelData.color; hoverFillColor: modelData.color; downFillColor: modelData.color
                    edgeColor: dialog.chosenColor===modelData.name?dialog.c.b80:"transparent"
                    hoverEdgeColor: dialog.c.b55
                    toolTip: modelData.name.charAt(0).toUpperCase()+modelData.name.slice(1)
                    contentItem: Item {}
                    onClicked: dialog.chosenColor=modelData.name
                }
            }
        }
        SmoothRectangle { width: parent.width; height: 1; color: dialog.c.b07 }
        Item {
            width: parent.width; height: 30
            Row {
                anchors.right: parent.right; spacing: 12
                DialogButton { objectName: "workspaceBranchCreateCancel"; width: 73; colors: dialog.c; text: "Cancel"; onClicked: dialog.dismiss() }
                DialogButton { id: createButton; objectName: "workspaceBranchCreateConfirm"; width: 119; colors: dialog.c; text: "Create Branch"; primary: true; enabled: /^[a-zA-Z0-9_\-]+$/.test(nameInput.text.trim()); onClicked: dialog.submit() }
            }
        }
    }
}
