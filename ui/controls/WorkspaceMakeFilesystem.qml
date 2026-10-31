// SPDX-License-Identifier: MPL-2.0
import QtQuick

DialogWindow {
    id: dialog
    objectName: "workspaceMakeFilesystem"
    required property var workspace
    property var details: ({path:"",name:""})
    property string filesystem: "EROFS"
    property bool busy: false
    readonly property color tint: c.BG2
    implicitWidth: 420; implicitHeight: form.height+48
    function showFolder(data) {details=data;filesystem="EROFS";mount.text="/"+data.name;busy=false;open()}
    function submit() {if(!busy){busy=true;workspace.createFilesystem(details.path,filesystem,mount.text)}}
    background: Item {
        LiveGlass {
            preferences: dialog.model
            x: 8; y: 8; width: parent.width-16; height: parent.height-16
            backdropSource: dialog.backdropSource; origin: Qt.point(dialog.x+8,dialog.y+8)
            cornerRadius: 14; tintColor: Qt.rgba(dialog.tint.r,dialog.tint.g,dialog.tint.b,170/255)
            flatColor: dialog.c.BG_MODAL; edgeColor: dialog.model.panelBorder
            effectsEnabled: dialog.model.settings.effects!=="low"
        }
    }
    Column {
        id: form; x: 28; y: 24; width: parent.width-56; spacing: 10
        RakLabel { width: parent.width; height: 20; text: dialog.s.mf_title; font.pixelSize: 14; font.weight: Font.DemiBold; color: dialog.c.FG }
        RakLabel { width: parent.width; height: Math.max(18,implicitHeight); text: dialog.s.mf_hint.replace("{part}",dialog.details.name); font.pixelSize: 10; color: dialog.c.FG_DESC; wrapMode: Text.WordWrap }
        RakLabel { width: parent.width; height: 18; text: dialog.s.mf_fs_type; font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1; color: dialog.c.FG_BLUE_MUTED }
        Row {
            width: parent.width; height: 34; spacing: 8
            Repeater {
                model: ["EROFS","EXT4"]
                delegate: RakButton {
                    required property string modelData
                    readonly property bool selected: dialog.filesystem===modelData
                    objectName: "workspaceFilesystem_"+modelData
                    width: (form.width-8)/2; height: 34; text: modelData; cornerRadius: 6
                    font.pixelSize: 12; font.weight: selected?Font.DemiBold:Font.Normal
                    fillColor: selected?dialog.c.a28:dialog.c.b05
                    hoverFillColor: selected?dialog.c.a36:dialog.c.b09
                    downFillColor: hoverFillColor
                    edgeColor: selected?dialog.c.a55:dialog.c.b12
                    hoverEdgeColor: selected?dialog.c.a55:dialog.c.b20
                    labelColor: selected?dialog.c.FG_BLUE_LIGHT:dialog.c.FG_MUTED
                    onClicked: dialog.filesystem=modelData
                }
            }
        }
        RakLabel { width: parent.width; height: 17; text: dialog.s.mf_partition_name; font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1; color: dialog.c.FG_BLUE_MUTED }
        RakField {
            objectName: "workspaceFilesystemPartition"; width: parent.width; height: 30
            text: dialog.details.name; enabled: false; font.pixelSize: 12; cornerRadius: 6
            fillColor: "transparent"; edgeColor: dialog.c.b10; disabledLabelColor: dialog.c.FG
            leftPadding: 11; rightPadding: 11; topPadding: 7; bottomPadding: 5
        }
        RakLabel { width: parent.width; height: 18; text: dialog.s.mf_mount_point; font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1; color: dialog.c.FG_BLUE_MUTED }
        RakField {
            id: mount; objectName: "workspaceFilesystemMount"; width: parent.width; height: 30
            font.pixelSize: 12; cornerRadius: 6; leftPadding: 11; rightPadding: 11; topPadding: 7; bottomPadding: 5
            fillColor: "transparent"; edgeColor: dialog.c.b10; focusEdgeColor: dialog.c.a45; labelColor: dialog.c.FG
        }
        SmoothRectangle { width: parent.width; height: 1; color: dialog.c.b07 }
        Item {
            width: parent.width; height: 32
            Row {
                anchors.right: parent.right; spacing: 10
                DialogButton { objectName: "workspaceFilesystemCancel"; width: Math.ceil(implicitWidth); height: 32; colors: dialog.c; text: dialog.s.mf_cancel; onClicked: dialog.dismiss() }
                DialogButton { objectName: "workspaceFilesystemCreate"; width: Math.ceil(implicitWidth); height: 32; colors: dialog.c; text: dialog.s.mf_create; primary: true; enabled: !dialog.busy; onClicked: dialog.submit() }
            }
        }
    }
}
