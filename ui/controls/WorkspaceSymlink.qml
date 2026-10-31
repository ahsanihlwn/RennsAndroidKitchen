// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: dialog; objectName: "workspaceSymlink"
    required property var workspace
    property string directory: ""
    property int tab: 0
    property var selected: ({android:[],toybox:[]})
    readonly property string kind: tab===1?"android":"toybox"
    readonly property var presets: workspace.symlinkPresets
    implicitWidth: 480; implicitHeight: 420
    function showSymlink(data) {directory=data.path;tab=0;selected={android:[],toybox:[]};linkName.text="";linkTarget.text="";open()}
    function select(name,value) {
        let state=Object.assign({},selected),names=state[kind].filter(item=>item!==name)
        if(value)names.push(name)
        state[kind]=names;selected=state
    }
    function all(value) {let state=Object.assign({},selected);state[kind]=value?presets[kind].map(row=>row.name):[];selected=state}
    function create() {workspace.createSymlinks(tab===0?[{name:linkName.text,target:linkTarget.text}]:presets[kind].filter(row=>selected[kind].indexOf(row.name)>=0))}
    Column {
        x: 28; y: 24; width: parent.width-56; spacing: 10
        RakLabel { height: 22; text: dialog.s.sym_title; font.pixelSize: 14; font.weight: Font.DemiBold; color: dialog.c.FG_BRIGHT }
        Row { width: parent.width; spacing: 6; RakLabel { text: dialog.s.sym_in; font.pixelSize: 11; color: dialog.c.FG_MUTED } RakLabel { width: parent.width-32; text: dialog.directory; elide: Text.ElideRight; font.pixelSize: 11; color: dialog.c.FG_BLUE_MUTED } }
        Row {
            spacing: 2
            Repeater {
                model: [dialog.s.sym_tab_custom,dialog.s.sym_tab_android,dialog.s.sym_tab_toybox]
                delegate: RakButton {
                    required property int index; required property string modelData
                    objectName: "symlinkTab"+index; text: modelData; height: 28; cornerRadius: 6; edgeWidth: 0
                    fillColor: dialog.tab===index?dialog.c.a14:"transparent"; hoverFillColor: dialog.c.a10
                    labelColor: dialog.tab===index?dialog.c.FG_BLUE:dialog.c.FG_MUTED; font.pixelSize: 12; font.weight: dialog.tab===index?Font.DemiBold:Font.Medium
                    onClicked: dialog.tab=index
                }
            }
        }
        Item {
            width: parent.width; height: 236
            Column {
                y: 12; width: parent.width; spacing: 8; visible: dialog.tab===0
                RakLabel { text: dialog.s.sym_name_label; font.pixelSize: 11; font.weight: Font.DemiBold; color: dialog.c.FG }
                RakField { id: linkName; objectName: "symlinkName"; width: parent.width; height: 30; font.pixelSize: 12; placeholderText: dialog.s.sym_name_ph; leftPadding: 10; rightPadding: 10; topPadding: 1; bottomPadding: 0; fillColor: dialog.c.b06; focusFillColor: dialog.c.b10; edgeColor: dialog.c.b12; focusEdgeColor: dialog.c.a55; labelColor: dialog.c.FG; hintColor: dialog.c.FG_MUTED; cornerRadius: 6 }
                Item { width: 1; height: 4 }
                RakLabel { text: dialog.s.sym_target_label; font.pixelSize: 11; font.weight: Font.DemiBold; color: dialog.c.FG }
                RakField { id: linkTarget; objectName: "symlinkTarget"; width: parent.width; height: 30; font.pixelSize: 12; placeholderText: dialog.s.sym_target_ph; leftPadding: 10; rightPadding: 10; topPadding: 1; bottomPadding: 0; fillColor: dialog.c.b06; focusFillColor: dialog.c.b10; edgeColor: dialog.c.b12; focusEdgeColor: dialog.c.a55; labelColor: dialog.c.FG; hintColor: dialog.c.FG_MUTED; cornerRadius: 6 }
            }
            Column {
                y: 8; width: parent.width; spacing: 6; visible: dialog.tab!==0
                Row {
                    spacing: 6
                    DialogButton { objectName: "symlinkSelectAll"; colors: dialog.c; height: 24; text: dialog.s.sym_select_all; onClicked: dialog.all(true) }
                    DialogButton { objectName: "symlinkClearAll"; colors: dialog.c; height: 24; text: dialog.s.sym_clear_all; onClicked: dialog.all(false) }
                }
                SmoothRectangle {
                    width: parent.width; height: 194; radius: 6; color: "transparent"; border.width: 1; border.color: dialog.c.b10
                    ListView {
                        id: list; anchors.fill: parent; anchors.margins: 4; clip: true; model: dialog.presets[dialog.kind]
                        C.ScrollBar.vertical: RakScrollBar { width: 8; handleColor: dialog.c.b18; hoverColor: dialog.c.b30; downColor: dialog.c.a55 }
                        delegate: Item {
                            required property var modelData; width: list.width; height: 30
                            RoundCheck { x: 12; y: 7; width: 16; height: 16; cornerRadius: 8; colors: dialog.c; checked: dialog.selected[dialog.kind].indexOf(modelData.name)>=0; onToggled: dialog.select(modelData.name,checked) }
                            RakLabel { x: 36; width: parent.width/2-36; height: parent.height; text: modelData.name; font.pixelSize: 12; color: dialog.c.FG }
                            RakLabel { anchors.right: parent.right; anchors.rightMargin: 12; width: parent.width/2; height: parent.height; horizontalAlignment: Text.AlignRight; text: "→  "+modelData.target; elide: Text.ElideRight; font.pixelSize: 11; color: dialog.c.FG_BLUE_MUTED }
                        }
                    }
                }
            }
        }
    }
    RakLabel { x: 28; anchors.bottom: parent.bottom; anchors.bottomMargin: 28; height: 30; text: dialog.tab!==0 && dialog.selected[dialog.kind].length?dialog.s.sym_count_selected.replace("{n}",dialog.selected[dialog.kind].length):""; font.pixelSize: 11; color: dialog.c.FG_MUTED }
    Row {
        anchors.right: parent.right; anchors.rightMargin: 28; anchors.bottom: parent.bottom; anchors.bottomMargin: 28; spacing: 8
        DialogButton { colors: dialog.c; text: dialog.s.btn_cancel; onClicked: dialog.dismiss() }
        DialogButton { objectName: "symlinkCreate"; colors: dialog.c; primary: true; text: dialog.s.sym_btn_create; onClicked: dialog.create() }
    }
}
