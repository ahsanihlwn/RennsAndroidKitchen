// SPDX-License-Identifier: MPL-2.0
import QtQuick

DialogWindow {
    id: dialog
    objectName: "creditWindow"
    implicitWidth: 360; implicitHeight: 421; title: s.crd_title
    component Label: RakLabel {
        x: 22; width: 316; height: 35
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
        textFormat: Text.RichText; font.pixelSize: 11; color: dialog.c.FG_MUTED
    }
    RoundedImage { x: 156; y: 20; width: 48; height: 48; source: dialog.model.icon("renns_project","") }
    Label { y: 80; text: "Renn's Project"; font.pixelSize: 15; font.weight: Font.DemiBold; color: dialog.c.FG }
    Label { y: 117; text: "@ahsanihlwn"; font.pixelSize: 12; color: dialog.c.FG_BLUE }
    SmoothRectangle { x: 22; y: 168; width: 316; height: 1; color: dialog.c.b07 }
    SmoothRectangle {
        x: (parent.width-67)/2; y: 184; width: 67; height: 32; radius: 4
        color: dialog.c.a12; border.color: dialog.c.a25
        RakLabel {
            anchors.fill: parent; text: "MPL-2.0"; color: dialog.c.FG_BLUE
            font.pixelSize: 11; font.weight: Font.DemiBold; horizontalAlignment: Text.AlignHCenter
        }
    }
    Label { y: 226; height: 24; text: "Mozilla Public License 2.0"; font.pixelSize: 12; font.weight: Font.DemiBold; color: dialog.c.FG }
    Label { y: 256; height: 52; text: dialog.s.lic_notice }
    Label {
        y: 314; height: 32
        text: '<a href="https://www.mozilla.org/en-US/MPL/2.0/" style="color:'+dialog.c.FG_BLUE+';text-decoration:none;">mozilla.org/MPL/2.0</a>'
        onLinkActivated: link=>Qt.openUrlExternally(link)
    }
    SmoothRectangle { x: 22; y: 358; width: 316; height: 1; color: dialog.c.b07 }
    DialogButton { objectName: "creditClose"; x: 258; y: 373; width: 80; height: 30; colors: dialog.c; text: dialog.s.crd_btn_close; onClicked: dialog.dismiss() }
}
