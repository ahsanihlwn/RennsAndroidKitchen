// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    width: 640; height: 380; visible: true; color: "#14191f"
    Rectangle {
        id: scene; objectName: "refractionScene"
        anchors.fill: parent; color: "#1c2834"
        property int phase: 0
        Repeater {
            model: 36
            Rectangle {
                required property int index
                x: index*18; width: 7; height: scene.height
                color: (index+scene.phase)%3===0?"#e9be64":(index+scene.phase)%3===1?"#6cb7d8":"#536b7d"
            }
        }
        Repeater {
            model: 20
            Rectangle { required property int index; y: index*20; width: scene.width; height: 2; color: "#d9e6ef" }
        }
        Text { x: 75; y: 135; text: "Live backdrop · curved refraction"; color: "white"; font.pixelSize: 22 }
    }
    LiveGlass {
        objectName: "refractionProbe"
        x: 80; y: 80; width: 480; height: 200
        backdropSource: scene; origin: Qt.point(x,y)
        blurRadius: 0; cornerRadius: 20
        flatColor: "#202020"; tintColor: "transparent"; edgeColor: "transparent"
    }
}
