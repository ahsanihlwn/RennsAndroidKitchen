// SPDX-License-Identifier: MPL-2.0
import QtQuick
import Renns.Workspace

Item {
    id: viewer
    objectName: "workspacePartitionViewer"
    required property var workspace
    required property var preferences
    required property var details
    readonly property var c: preferences.colors
    readonly property var s: preferences.strings
    readonly property bool superMode: details.kind==="super"
    readonly property var documentModel: document
    PartitionDocument { id: document }
    Component.onCompleted: document.load(details.documentPath || details.path,details.kind)
    Component.onDestruction: document.close()
    Connections { target: document; function onMessageRequested(title,text){viewer.workspace.showMessage(title,text)} }
    function translated(key,values){let text=viewer.s[key];for(const name in values)text=text.replace("{"+name+"}",String(values[name]));return text}
    function extract(all){viewer.workspace.extractPartitions(details.path,details.kind,document.selected,all)}
    SmoothRectangle { anchors.fill: parent; color: viewer.superMode?viewer.c.BG3:viewer.c.BG }
    Item {
        visible: !viewer.superMode; anchors.fill: parent
        SmoothRectangle {
            width: parent.width; height: 38; color: viewer.c.BG
            SmoothRectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: viewer.c.b06 }
            RakLabel { x: 10; height: 38; width: Math.max(0,selectionButtons.x-20); font.pixelSize: 11; color: viewer.c.FG3; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight; text: document.selected.length?viewer.translated("pv_lbl_n_selected",{count:document.selected.length,total:document.total}):viewer.translated("pv_lbl_n_partitions",{n:document.total}) }
            Row {
                id: selectionButtons; anchors.right: parent.right; anchors.rightMargin: 10; y: 7; spacing: 6
                Repeater {
                    model: [{name:"partitionSelectAll",label:viewer.s.pv_btn_select_all,all:true},{name:"partitionDeselect",label:viewer.s.pv_btn_deselect,all:false}]
                    delegate: RakButton { required property var modelData; objectName: modelData.name; text: modelData.label; height: 24; implicitWidth: Math.ceil(contentItem.implicitWidth)+22; font.pixelSize: 11; leftPadding: 10; rightPadding: 10; cornerRadius: 5; fillColor: viewer.c.b05; hoverFillColor: viewer.c.b09; downFillColor: viewer.c.b13; edgeColor: viewer.c.b09; hoverEdgeColor: edgeColor; labelColor: viewer.c.FG2; hoverLabelColor: viewer.c.FG_HOVER; onClicked: document.selectAll(modelData.all) }
                }
            }
        }
        PartitionList { objectName: "payloadPartitions"; x: 0; y: 42; width: parent.width; height: Math.max(0,parent.height-98); colors: viewer.c; document: viewer.documentModel }
        SmoothRectangle {
            anchors.bottom: parent.bottom; width: parent.width; height: 52; color: viewer.c.BG2
            SmoothRectangle { width: parent.width; height: 1; color: viewer.c.b08 }
            RakLabel { x: 12; height: 52; width: Math.max(0,extractSelected.x-24); font.pixelSize: 11; color: viewer.c.FG3; verticalAlignment: Text.AlignVCenter; text: document.selected.length?viewer.translated("pv_hint_n_extract",{n:document.selected.length}):viewer.s.pv_hint_pick; elide: Text.ElideRight }
            RakButton { id: extractSelected; objectName: "payloadExtractSelected"; anchors.right: parent.right; anchors.rightMargin: 12; y: 10; height: 32; implicitWidth: Math.ceil(contentItem.implicitWidth)+38; font.pixelSize: 12; font.weight: Font.DemiBold; cornerRadius: 7; leftPadding: 18; rightPadding: 18; text: viewer.s.pv_btn_extract_selected; enabled: document.selected.length>0; fillColor: enabled?viewer.c.a18:viewer.c.b09; hoverFillColor: viewer.c.a30; downFillColor: viewer.c.a44; edgeColor: enabled?viewer.c.a38:viewer.c.b13; hoverEdgeColor: viewer.c.a55; labelColor: viewer.c.FG_BLUE; hoverLabelColor: viewer.c.FG_BLUE_LIGHT; disabledLabelColor: viewer.c.FG3; onClicked: viewer.extract(false) }
        }
    }
    Item {
        visible: viewer.superMode; anchors.fill: parent
        PartitionList { objectName: "superPartitionsA"; x: 9; y: 9; width: Math.floor((parent.width-24)/2); height: Math.max(0,parent.height-59); colors: viewer.c; document: viewer.documentModel; superMode: true }
        PartitionList { objectName: "superPartitionsB"; x: Math.floor((parent.width-24)/2)+15; y: 9; width: Math.floor((parent.width-24)/2); height: Math.max(0,parent.height-59); colors: viewer.c; document: viewer.documentModel; superMode: true; side: "b" }
        Row {
            x: 9; y: parent.height-44; width: parent.width-18; height: 35; spacing: 6
            Repeater {
                model: [{name:"superExtractSelected",label:"Extract Selected",all:false},{name:"superExtractAll",label:"Extract All",all:true}]
                delegate: RakButton { required property var modelData; objectName: modelData.name; text: modelData.label; width: Math.floor((parent.width-6)/2); height: 35; cornerRadius: 6; font.pixelSize: 12; font.weight: Font.DemiBold; fillColor: viewer.c.a18; hoverFillColor: viewer.c.a35; downFillColor: viewer.c.a55; edgeColor: viewer.c.a40; hoverEdgeColor: viewer.c.a80; labelColor: viewer.c.FG_BRIGHT; hoverLabelColor: labelColor; onClicked: viewer.extract(modelData.all) }
            }
        }
    }
}
