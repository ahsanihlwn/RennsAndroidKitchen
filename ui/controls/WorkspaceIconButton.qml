// SPDX-License-Identifier: MPL-2.0
import QtQuick

RakButton {
    id: button
    required property var preferences
    property string iconKey: ""
    property bool circular: false
    property color tint: "transparent"
    property int layoutMode: 2
    property bool useAccentColor: true
    property bool loading: false
    implicitWidth: 28
    implicitHeight: 28
    leftPadding: 0
    rightPadding: 0

    readonly property bool isLight: (preferences && preferences.colors && preferences.colors.is_light !== undefined) ? preferences.colors.is_light : (preferences && preferences.paletteName ? preferences.paletteName.indexOf("light") >= 0 : false)
    readonly property color fgColor: preferences && preferences.colors && preferences.colors.FG ? preferences.colors.FG : (isLight ? "#24292f" : "#ededed")
    readonly property color fgHoverColor: preferences && preferences.colors && preferences.colors.FG_BRIGHT ? preferences.colors.FG_BRIGHT : (isLight ? "#090a0c" : "#ffffff")
    readonly property color accentColor: preferences && preferences.colors && (preferences.colors.accent_hex || preferences.colors.FG_BLUE) ? (preferences.colors.accent_hex || preferences.colors.FG_BLUE) : "#3ecf8e"
    readonly property bool anims: preferences && preferences.settings ? preferences.settings.animations : true

    // Neutral light/dark tone with subtle palette DNA (~10% accent tint), no multi-color hues.
    readonly property color refinedThemeColor: {
        if (tint.a > 0) return tint;
        if (isLight) {
            return Qt.rgba(0.90 * 0.10 + 0.10 * accentColor.r,
                           0.90 * 0.11 + 0.10 * accentColor.g,
                           0.90 * 0.13 + 0.10 * accentColor.b, 1.0);
        }
        return Qt.rgba(0.90 * 0.92 + 0.10 * accentColor.r,
                       0.90 * 0.93 + 0.10 * accentColor.g,
                       0.90 * 0.95 + 0.10 * accentColor.b, 1.0);
    }

    iconWidth: 16
    iconHeight: 16
    cornerRadius: circular ? width / 2 : 6
    edgeWidth: 1
    edgeColor: "transparent"
    hoverEdgeColor: Qt.rgba(refinedThemeColor.r, refinedThemeColor.g, refinedThemeColor.b, 0.28)
    fillColor: "transparent"
    hoverFillColor: isLight ? Qt.rgba(refinedThemeColor.r, refinedThemeColor.g, refinedThemeColor.b, 0.08) : Qt.rgba(refinedThemeColor.r, refinedThemeColor.g, refinedThemeColor.b, 0.12)
    downFillColor: isLight ? Qt.rgba(refinedThemeColor.r, refinedThemeColor.g, refinedThemeColor.b, 0.15) : Qt.rgba(refinedThemeColor.r, refinedThemeColor.g, refinedThemeColor.b, 0.20)
    labelColor: useAccentColor ? refinedThemeColor : fgColor
    hoverLabelColor: isLight ? "#090a0c" : "#ffffff"
    disabledLabelColor: preferences && preferences.colors && preferences.colors.FG_DISABLED2 ? preferences.colors.FG_DISABLED2 : "#555555"
    animatePress: preferences && preferences.settings ? preferences.settings.animations : true

    contentItem: Item {
        implicitWidth: nativeIcon.width
        implicitHeight: nativeIcon.height
        WorkspaceNativeIcon {
            id: nativeIcon
            anchors.centerIn: parent
            iconKey: button.iconKey
            iconSize: button.iconWidth
            isLight: button.isLight
            hovered: button.visualHovered
            down: button.down
            enabled: button.enabled
            paletteName: (button.preferences && button.preferences.paletteName) ? button.preferences.paletteName : "default"
            strokeColor: !button.enabled ? button.disabledLabelColor : button.visualHovered ? button.hoverLabelColor : button.labelColor
            accentColor: button.refinedThemeColor
            fillColor: button.fillColor
            layoutMode: button.layoutMode
            animationsEnabled: button.anims
        }
    }

    Item {
        id: loadingTrack
        z: 5
        visible: button.loading
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 2
        width: Math.min(20, Math.max(12, button.width - 12))
        height: 3

        SmoothRectangle {
            id: loadingThumb
            width: Math.max(8, parent.width * 0.45)
            height: parent.height
            radius: height / 2
            color: button.accentColor
            x: button.anims ? 0 : (parent.width - width) / 2

            SequentialAnimation on x {
                loops: Animation.Infinite
                running: loadingTrack.visible && button.anims
                NumberAnimation {
                    from: 0
                    to: Math.max(0, loadingTrack.width - loadingThumb.width)
                    duration: 460
                    easing.type: Easing.InOutCubic
                }
                NumberAnimation {
                    from: Math.max(0, loadingTrack.width - loadingThumb.width)
                    to: 0
                    duration: 460
                    easing.type: Easing.InOutCubic
                }
            }
        }
    }
}
