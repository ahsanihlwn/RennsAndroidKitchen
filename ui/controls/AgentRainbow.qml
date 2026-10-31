// SPDX-License-Identifier: MPL-2.0
import QtQuick

Item {
    id: effect
    property bool active: false
    property bool animated: true
    property real phase: 0
    property real fillOpacity: .10
    opacity: active?fillOpacity:0
    visible: opacity>.001; clip: true
    Behavior on opacity {
        enabled: effect.animated
        NumberAnimation { duration: effect.active?220:420; easing.type: Easing.InOutQuad }
    }
    Item {
        x: -effect.phase*effect.width; width: effect.width*2; height: effect.height
        Repeater { model: 2
            delegate: Rectangle {
                required property int index
                x: index*effect.width; width: effect.width; height: effect.height
                gradient: Gradient { orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: "#9279ee" }
                    GradientStop { position: .2; color: "#e588ba" }
                    GradientStop { position: .4; color: "#ebbc77" }
                    GradientStop { position: .6; color: "#66cca4" }
                    GradientStop { position: .8; color: "#58b9ef" }
                    GradientStop { position: 1; color: "#9279ee" }
                }
            }
        }
    }
    NumberAnimation on phase { from: 0; to: 1; duration: 9000; loops: Animation.Infinite; running: effect.visible && effect.animated }
}
