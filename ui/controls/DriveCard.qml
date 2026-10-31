// SPDX-License-Identifier: MPL-2.0
import QtQuick

RakCard {
    id: drive
    required property var modelData
    required property var model
    property bool trash: false
    property bool interactive: true
    property real usageProgress: 1
    readonly property var c: model.colors
    readonly property bool active: modelData.active && !drive.trash
    readonly property bool transformed: Math.abs(feedback.value-1)>.0001
    objectName: "drive_" + modelData.key
    width: 128; height: 44; radius: 10
    scale: feedback.value
    activeFocusOnTab: interactive
    PressFeedback { id: feedback; enabledMotion: drive.interactive && drive.model.settings.animations }
    onVisibleChanged: if (!visible) feedback.reset()
    Keys.onPressed: event => {
        if (!interactive || event.isAutoRepeat || [Qt.Key_Space,Qt.Key_Return,Qt.Key_Enter].indexOf(event.key)<0) return
        feedback.press(); event.accepted=true
    }
    Keys.onReleased: event => {
        if (!interactive || event.isAutoRepeat || [Qt.Key_Space,Qt.Key_Return,Qt.Key_Enter].indexOf(event.key)<0) return
        feedback.release(); drive.model.dispatch("toggleDrive", {key: drive.modelData.key}); event.accepted=true
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    HoverTip { label: drive.modelData.name+"\n"+(drive.modelData.used/1073741824).toFixed(1)+" GB / "+(drive.modelData.total/1073741824).toFixed(1)+" GB"; hovering: hover.hovered && drive.interactive }
    color: active ? modelData.fill : drive.c.BG
    border.color: Qt.tint(active ? modelData.fill : drive.c.BG, modelData.distro ? (active ? drive.c.a38 : drive.c.a12) : (active ? drive.c.b10 : drive.c.b05))
    TapHandler {
        enabled: drive.interactive
        onPressedChanged: if (pressed) { drive.forceActiveFocus(Qt.MouseFocusReason); feedback.press() } else feedback.release()
        onTapped: drive.model.dispatch("toggleDrive", {key: drive.modelData.key})
    }
    Image { x: 8; y: 12; width: 20; height: 20; sourceSize.width: width; sourceSize.height: height; source: drive.model.icon("project_disk", drive.modelData.distro ? drive.c.accent_hex : ""); opacity: drive.active ? 1 : .38 }
    RakLabel { x: 35; y: 4; width: 85; height: 12; text: drive.modelData.name; font.pixelSize: 10; font.weight: Font.DemiBold; color: drive.active ? (drive.modelData.distro ? drive.c.accent_hex : drive.c.FG_BRIGHT) : drive.c.FG_MUTED; elide: Text.ElideRight; transformed: drive.transformed }
    RakLabel { x: 35; y: 17; width: 85; height: 11; text: (drive.modelData.used * drive.usageProgress / 1073741824).toFixed(0) + "/" + (drive.modelData.total / 1073741824).toFixed(0) + " GB"; font.pixelSize: 9; color: drive.active ? drive.c.FG3 : drive.c.FG_DISABLED; elide: Text.ElideRight; transformed: drive.transformed }
    SmoothRectangle {
        x: 35; y: 32; width: 57; height: 4; radius: 2; color: drive.c.b10
        SmoothRectangle { width: parent.width * drive.modelData.fraction * drive.usageProgress; height: 4; radius: 2; color: drive.active ? drive.modelData.barColor : drive.c.b18 }
    }
    RakLabel { x: 96; y: 29; width: 24; height: 11; text: Math.floor(drive.modelData.percent*drive.usageProgress) + "%"; font.pixelSize: 8; horizontalAlignment: Text.AlignRight; color: drive.active ? drive.c.FG3 : drive.c.FG_DISABLED; transformed: drive.transformed }
}
