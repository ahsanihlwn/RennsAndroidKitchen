// SPDX-License-Identifier: MPL-2.0
import QtQuick
DialogWindow {
    id: dialog; objectName: "workspaceComparePicker"
    required property var workspace
    readonly property var backend: workspace.privateFeatures.compare
    implicitWidth: 580; implicitHeight: 440; panelAlpha: 170/255
    function showPicker() {open()}
    RakLabel { x: 28; y: 26; text: dialog.s.cmp_title; font.pixelSize: 13; font.weight: Font.DemiBold; color: dialog.c.FG }
    SmoothRectangle { x: 28; y: 58; width: parent.width-56; height: 1; color: dialog.c.b06 }
    ComparePartitionColumn { x: 28; y: 71; width: (parent.width-89)/2; height: 280; caption: "A"; backend: dialog.backend.sideA; preferences: dialog.model }
    SmoothRectangle { x: parent.width/2; y: 71; width: 1; height: 280; color: dialog.c.b08 }
    ComparePartitionColumn { x: parent.width/2+17; y: 71; width: (parent.width-89)/2; height: 280; caption: "B"; backend: dialog.backend.sideB; preferences: dialog.model }
    SmoothRectangle { x: 28; y: 363; width: parent.width-56; height: 1; color: dialog.c.b06 }
    Row { anchors.right: parent.right; anchors.rightMargin: 28; y: 378; spacing: 8; DialogButton { colors: dialog.c; text: dialog.s.cmp_btn_cancel; onClicked: dialog.dismiss() } DialogButton { objectName: "compareStart"; colors: dialog.c; primary: true; enabled: !!(dialog.backend.sideA.selected && dialog.backend.sideB.selected); text: dialog.s.cmp_btn_compare; onClicked: if(dialog.backend.start())dialog.dismiss() } }
}
