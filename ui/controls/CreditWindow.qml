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
    Label { y: 183; text: dialog.s.crd_open_source }
    Repeater {
        model: [{name:"sdat2img",description:"Convert dat to img",author:"xpirt",url:"https://github.com/xpirt/sdat2img"},
                {name:"img2sdat",description:"Convert img to sparse dat",author:"xpirt",url:"https://github.com/xpirt/img2sdat"},
                {name:"fspatch",description:"Patch fs_config before unpack",author:"Affggh",url:"https://github.com/affggh/fspatch"}]
        delegate: Item {
            required property var modelData
            required property int index
            x: 22; y: 226+index*39.5; width: 316; height: 36
            RakLabel { width: 12; height: parent.height; text: "·"; color: dialog.c.a50; font.pixelSize: 14; horizontalAlignment: Text.AlignHCenter }
            RakLabel {
                x: 18; width: 298; height: parent.height; font.pixelSize: 11; wrapMode: Text.WordWrap; textFormat: Text.RichText
                text: '<a href="'+modelData.url+'" style="color:'+dialog.c.FG_BLUE+';text-decoration:none;">'+modelData.name+'</a> <span style="color:'+dialog.c.FG_MUTED+';">— '+modelData.description+' · '+modelData.author+'</span>'
                onLinkActivated: link=>Qt.openUrlExternally(link)
            }
        }
    }
    SmoothRectangle { x: 22; y: 358; width: 316; height: 1; color: dialog.c.b07 }
    DialogButton { objectName: "creditClose"; x: 258; y: 373; width: 80; height: 30; colors: dialog.c; text: dialog.s.crd_btn_close; onClicked: dialog.dismiss() }
}
