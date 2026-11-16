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
    implicitWidth: 28
    implicitHeight: 28
    leftPadding: 0
    rightPadding: 0

    readonly property bool isLight: (preferences && preferences.colors && preferences.colors.is_light !== undefined) ? preferences.colors.is_light : (preferences && preferences.paletteName ? preferences.paletteName.indexOf("light") >= 0 : false)
    readonly property color fgColor: preferences && preferences.colors && preferences.colors.FG ? preferences.colors.FG : (isLight ? "#24292f" : "#ededed")
    readonly property color fgHoverColor: preferences && preferences.colors && preferences.colors.FG_BRIGHT ? preferences.colors.FG_BRIGHT : (isLight ? "#090a0c" : "#ffffff")
    readonly property color accentColor: preferences && preferences.colors && (preferences.colors.accent_hex || preferences.colors.FG_BLUE) ? (preferences.colors.accent_hex || preferences.colors.FG_BLUE) : "#3ecf8e"

    // Refined theme tone:
    // - Dark BG: Cerah (luminous soft-tinted, tidak menusuk mata / tidak strong)
    // - Light BG: Lebih gelap (deep refined tone dengan undertone tema, kontras tinggi)
    readonly property color refinedThemeColor: {
        if (tint.a > 0) return tint;
        if (isLight) {
            return Qt.rgba(0.78 * 0.13 + 0.22 * accentColor.r,
                           0.78 * 0.15 + 0.22 * accentColor.g,
                           0.78 * 0.18 + 0.22 * accentColor.b, 1.0);
        } else {
            return Qt.rgba(0.72 * 0.92 + 0.28 * accentColor.r,
                           0.72 * 0.93 + 0.28 * accentColor.g,
                           0.72 * 0.95 + 0.28 * accentColor.b, 1.0);
        }
    }

    iconWidth: 16
    iconHeight: 16
    cornerRadius: circular ? width / 2 : 6
    edgeWidth: 1
    edgeColor: "transparent"
    hoverEdgeColor: Qt.rgba(accentColor.r, accentColor.g, accentColor.b, 0.24)
    fillColor: "transparent"
    hoverFillColor: isLight ? Qt.rgba(0, 0, 0, 0.05) : Qt.rgba(255, 255, 255, 0.08)
    downFillColor: isLight ? Qt.rgba(0, 0, 0, 0.10) : Qt.rgba(255, 255, 255, 0.14)
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
        }
    }
}
