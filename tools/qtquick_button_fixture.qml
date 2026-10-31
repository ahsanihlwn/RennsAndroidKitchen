// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    id: window
    required property var uiModel
    readonly property Item backdropSurface: surface
    width: 700; height: 260; color: uiModel.colors.BG2
    Rectangle { id: surface; anchors.fill: parent; color: window.uiModel.colors.BG2 }
    ActionButton { objectName: "normal"; x: 30; y: 30; width: 160; height: 34; colors: window.uiModel.colors; text: "Settings gjpq" }
    ActionButton { objectName: "primary"; x: 220; y: 30; width: 160; height: 34; colors: window.uiModel.colors; primary: true; text: "Confirm Project" }
    RomMode { objectName: "rom"; x: 410; y: 30; width: 250; height: 38; colors: window.uiModel.colors; text: "ROM Tunggal"; selected: true }
    ChoiceButton { objectName: "choice"; x: 30; y: 110; colors: window.uiModel.colors; text: "Medium"; currentKey: "medium"; choices: [{key:"low",label:"Low"},{key:"medium",label:"Medium"},{key:"high",label:"High"}] }
    ActionButton { objectName: "small"; x: 220; y: 110; width: 110; height: 28; colors: window.uiModel.colors; text: "Change"; font.pixelSize: 11 }
    DriveCard {
        objectName: "drive_C:"
        x: 360; y: 110; model: window.uiModel
        modelData: ({key:"C:",name:"C:",active:true,used:50*1073741824,total:100*1073741824,percent:50,fraction:.5,barColor:window.uiModel.colors.accent_hex,fill:window.uiModel.colors.BG2})
    }
    ProjectCard {
        x: 350; y: 180; width: 320; index: 0; model: window.uiModel
        modelData: ({path:"C:/fixture",project_name:"Fixture project",port1:"system",sizeLabel:"Size: 100 GB"})
    }
    ActionButton {
        id: bitmap
        objectName: "legacyBitmap"; x: 30; y: 180; width: 160; height: 34
        colors: window.uiModel.colors; text: "Settings gjpq"
        contentItem: Item {
            Text { anchors.centerIn: parent; text: bitmap.text; font: bitmap.font; color: bitmap.labelColor; renderType: Text.NativeRendering }
        }
    }
}
