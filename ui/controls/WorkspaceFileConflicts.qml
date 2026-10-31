// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: dialog; objectName: "workspaceFileConflicts"
    required property var workspace
    property var rows: []
    property var decisions: ({})
    property int token: 0
    property bool accepted: false
    readonly property bool multiple: rows.length>1
    implicitWidth: multiple?460:430
    implicitHeight: body.height+34
    function showConflicts(data) {
        rows=data.rows;token=data.token;accepted=false
        let initial={};for(const row of rows)initial[row.key]="replace";decisions=initial
        open()
    }
    function choose(key,policy) {let values=Object.assign({},decisions);values[key]=policy;decisions=values}
    function finish(policy) {
        if(policy){let values={};for(const row of rows)values[row.key]=policy;decisions=values}
        accepted=true;dismiss()
    }
    onDismissed: workspace.transfers.resolve(token,accepted?decisions:({"__cancel__":true}))
    Column {
        id: body; x: 20; y: 18; width: parent.width-40; spacing: 12
        Row {
            width: parent.width; spacing: 8
            Image { visible: !dialog.multiple; width: 20; height: 20; source: "image://icons/file" }
            RakLabel { width: parent.width-(dialog.multiple?0:28); text: dialog.multiple?dialog.rows.length+" files already exist":"File already exists"; font.pixelSize: 13; font.weight: Font.DemiBold; color: dialog.c.FG_BRIGHT }
        }
        RakLabel { width: parent.width; text: dialog.multiple?"Choose what to do for each file:":(dialog.rows.length?dialog.rows[0].name:"")+" already exists in the destination."; wrapMode: Text.WordWrap; font.pixelSize: 12; color: dialog.multiple?dialog.c.FG_BLUE_MUTED:dialog.c.FG }
        SmoothRectangle { visible: dialog.multiple; width: parent.width; height: 1; color: dialog.c.b07 }
        RakScrollView {
            colors: dialog.c
            id: list; width: parent.width; height: Math.min(300,conflicts.height); clip: true; contentWidth: availableWidth
            Column {
                id: conflicts; width: list.availableWidth; spacing: 6
                Repeater {
                    model: dialog.rows
                    delegate: SmoothRectangle {
                        required property var modelData
                        width: parent.width; height: dialog.multiple?116:94; radius: 6; color: dialog.c.b03; border.width: 1; border.color: dialog.c.b07
                        RakLabel { x: 10; y: 8; width: parent.width-20; height: 18; visible: dialog.multiple; text: modelData.name; font.pixelSize: 12; font.weight: Font.DemiBold; color: dialog.c.FG }
                        Row {
                            x: 10; y: dialog.multiple?32:8; width: parent.width-20; spacing: 12
                            Repeater {
                                model: [{header:"↓ INCOMING",path:modelData.source,info:modelData.incoming},{header:"📁 EXISTING",path:modelData.destination,info:modelData.existing}]
                                delegate: Column {
                                    required property var modelData
                                    width: (parent.width-12)/2; spacing: 4
                                    RakLabel { text: modelData.header; font.pixelSize: 10; font.weight: Font.Bold; color: dialog.c.accent_hex }
                                    RakLabel { visible: !dialog.multiple; width: parent.width; text: modelData.path.split(/[\\/]/).pop(); elide: Text.ElideRight; font.pixelSize: 11; font.weight: Font.DemiBold; color: dialog.c.FG_BRIGHT }
                                    RakLabel { width: parent.width; text: modelData.info; wrapMode: Text.WordWrap; font.pixelSize: 10; color: dialog.c.FG_MUTED }
                                }
                            }
                        }
                        Row {
                            anchors.right: parent.right; anchors.rightMargin: 10; anchors.bottom: parent.bottom; anchors.bottomMargin: 8; spacing: 6; visible: dialog.multiple
                            AndroidToolButton { colors: dialog.c; width: 70; height: 30; text: "Skip"; selected: dialog.decisions[modelData.key]==="skip"; onClicked: dialog.choose(modelData.key,"skip") }
                            AndroidToolButton { colors: dialog.c; width: 80; height: 30; text: "Replace"; danger: true; selected: dialog.decisions[modelData.key]==="replace"; onClicked: dialog.choose(modelData.key,"replace") }
                        }
                    }
                }
            }
        }
        SmoothRectangle { width: parent.width; height: 1; color: dialog.c.b07 }
        Item {
            width: parent.width; height: 30
            DialogButton { colors: dialog.c; text: "Cancel"; onClicked: dialog.dismiss() }
            Row {
                anchors.right: parent.right; spacing: 8
                DialogButton { objectName: "conflictSkip"; colors: dialog.c; text: dialog.multiple?"Skip All":"Skip"; onClicked: dialog.finish("skip") }
                DialogButton { objectName: "conflictReplace"; colors: dialog.c; danger: true; text: dialog.multiple?"Replace All":"Replace"; onClicked: dialog.finish("replace") }
                DialogButton { objectName: "conflictApply"; colors: dialog.c; primary: true; visible: dialog.multiple; text: "Apply"; onClicked: dialog.finish("") }
            }
        }
    }
}
