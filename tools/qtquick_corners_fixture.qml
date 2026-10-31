// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    width: 370; height: 210
    Repeater {
        model: [4,6,8,10,12,14]
        Item {
            required property real modelData
            required property int index
            x: 10+index*60; width: 44; height: 200
            SmoothRectangle { objectName: "rectangle_"+parent.index; y: 10; width: 44; height: 44; radius: parent.modelData; color: "white" }
            LiveGlass { objectName: "glass_"+parent.index; y: 75; width: 44; height: 44; cornerRadius: parent.modelData; flatColor: "white"; edgeColor: "transparent"; effectsEnabled: false }
            RakButton { objectName: "button_"+parent.index; y: 140; width: 44; height: 44; cornerRadius: parent.modelData; fillColor: "white"; hoverFillColor: "white"; edgeColor: "transparent" }
        }
    }
}
