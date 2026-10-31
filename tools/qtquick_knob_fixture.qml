// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    id: window
    required property var preferences
    property int sliderMoves: 0
    property int switchToggles: 0
    width: 560; height: 290; visible: true; color: preferences.colors.BG
    SmoothRectangle { x: 24; y: 24; width: parent.width-48; height: parent.height-48; radius: 10; color: window.preferences.colors.BG2 }
    RakLabel { x: 48; y: 40; text: "Slider & toggle"; color: window.preferences.colors.FG; font.pixelSize: 14 }
    ViewerSlider {
        objectName: "motionSlider"
        x: 60; y: 95; width: 440; height: 26
        colors: window.preferences.colors; preferences: window.preferences
        from: 0; to: 100; stepSize: 1; value: 50
        onMoved: window.sliderMoves++
    }
    RakSwitch {
        objectName: "motionSwitch"
        x: 64; y: 190; width: 42; height: 24
        preferences: window.preferences
        accentColor: window.preferences.colors.accent_hex; offColor: window.preferences.colors.offToggle
        onToggled: window.switchToggles++
    }
}
