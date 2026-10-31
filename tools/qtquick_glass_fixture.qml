// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    width: 420; height: 320; visible: true; color: "#101010"
    Rectangle {
        id: scene
        objectName: "glassScene"
        anchors.fill: parent; color: "#101010"
        property color stripeColor: "white"
        Repeater {
            model: 21
            Rectangle { required property int index; x: index*20; width: 10; height: 320; color: scene.stripeColor }
        }
    }
    LiveGlass {
        id: glass
        objectName: "glassProbe"
        x: 100; y: 80; width: 220; height: 180
        backdropSource: scene; origin: Qt.point(x,y); cornerRadius: 18
        flatColor: "#202020"; tintColor: "#80202020"; edgeColor: "transparent"
    }
}
