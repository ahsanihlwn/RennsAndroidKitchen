// SPDX-License-Identifier: MPL-2.0
import QtQuick

DialogWindow {
    id: dialog
    objectName: "licenseWindow"
    readonly property real tailOffset: model.privateEnabled?0:-47
    implicitWidth: 400; implicitHeight: 534+tailOffset
    title: s.lic_title
    component Label: RakLabel {
        x: 22; width: 356; height: 40
        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
        textFormat: Text.RichText; font.pixelSize: 11; color: dialog.c.FG_MUTED
        onLinkActivated: link=>Qt.openUrlExternally(link)
    }
    component Divider: SmoothRectangle { x: 22; width: 356; height: 1; color: dialog.c.b07 }
    Label { y: 20; text: dialog.s.lic_title; font.pixelSize: 15; font.weight: Font.DemiBold; color: dialog.c.FG }
    Divider { y: 76 }
    SmoothRectangle { x: (parent.width-67)/2; y: 93; width: 67; height: 40; radius: 4; color: dialog.c.a12; border.color: dialog.c.a25
        RakLabel { anchors.fill: parent; text: "MPL-2.0"; color: dialog.c.FG_BLUE; font.pixelSize: 11; font.weight: Font.DemiBold; horizontalAlignment: Text.AlignHCenter }
    }
    Label { y: 149; text: dialog.s.lic_copyright; font.pixelSize: 12; color: dialog.c.FG_BLUE_MUTED }
    Label { y: 201; text: dialog.s.lic_notice }
    Label { y: 249; height: 39; visible: dialog.model.privateEnabled; text: dialog.s.lic_private_notice }
    Label { y: 296+dialog.tailOffset; text: '<a href="https://github.com/ahsanihlwn/RennsAndroidKitchen" style="color:'+dialog.c.FG_BLUE+';">'+dialog.s.lic_source+'</a>' }
    Divider { y: 352+dialog.tailOffset }
    Label { y: 367+dialog.tailOffset; text: dialog.s.lic_third_party }
    Label { y: 415+dialog.tailOffset; text: dialog.s.lic_pyside6 }
    Divider { y: 471+dialog.tailOffset }
    DialogButton { objectName: "licenseClose"; x: 298; y: 486+dialog.tailOffset; width: 80; height: 30; colors: dialog.c; text: dialog.s.lic_btn_close; onClicked: dialog.dismiss() }
}
