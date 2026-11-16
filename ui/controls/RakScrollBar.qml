// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Templates as T

T.ScrollBar {
    id: control
    property color handleColor: "#2a2c3a"
    property color hoverColor: handleColor
    property color downColor: hoverColor
    property bool autoHide: true
    property bool shown: false
    property int idleDelay: 1000
    opacity: autoHide && !shown ? 0 : 1
    function reveal() {
        shown=true
        if(active || hovered || pressed)hideDelay.stop()
        else hideDelay.restart()
    }
    onPositionChanged: reveal()
    onActiveChanged: reveal()
    onHoveredChanged: reveal()
    onPressedChanged: reveal()
    Timer { id: hideDelay; interval: control.idleDelay; onTriggered: if(!control.active && !control.hovered && !control.pressed)control.shown=false }
    implicitWidth: orientation === Qt.Vertical ? 4 : 0
    implicitHeight: orientation === Qt.Horizontal ? 4 : 0
    minimumSize: .02
    padding: 0
    hoverEnabled: true
    policy: T.ScrollBar.AsNeeded
    visible: policy===T.ScrollBar.AlwaysOn || (policy===T.ScrollBar.AsNeeded && size<.9999)
    contentItem: SmoothRectangle {
        implicitWidth: control.orientation === Qt.Vertical ? (control.width > 0 ? control.width : 4) : 20
        implicitHeight: control.orientation === Qt.Horizontal ? (control.height > 0 ? control.height : 4) : 20
        radius: control.orientation === Qt.Vertical ? Math.min(control.width > 0 ? control.width / 2 : 2, 4) : Math.min(control.height > 0 ? control.height / 2 : 2, 4)
        color: control.pressed ? control.downColor : control.hovered ? control.hoverColor : control.handleColor
    }
}
