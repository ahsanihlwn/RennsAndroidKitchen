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

    // Semantic Base Color per iconKey:
    // Soft-tone semantic hues harmonized with the active theme
    readonly property color semanticBaseColor: {
        const key = button.iconKey;
        if (!key) return accentColor;
        switch (key) {
            case "back":
            case "back_to_proj":
                // Soft Sky Blue
                return Qt.color("#38bdf8");
            case "settings":
            case "set_project":
                // Soft Mechanical Gear Steel / Slate Gray
                return Qt.color("#94a3b8");
            case "layout":
            case "tree_mode":
                // Soft Ice Cyan / Teal
                return Qt.color("#2dd4bf");
            case "terminal":
                // Soft Mint / Emerald Green
                return Qt.color("#34d399");
            case "android":
            case "android_tools":
                // Soft Android Lime / Pistachio
                return Qt.color("#a3e635");
            case "import":
            case "import_project":
                // Soft Periwinkle / Indigo
                return Qt.color("#818cf8");
            case "agent":
            case "agentInbox":
            case "sparkle":
            case "send":
                // Soft Purple / Lilac
                return Qt.color("#c084fc");
            case "automation":
                // Soft Electric Yellow / Gold
                return Qt.color("#facc15");
            case "compare":
                // Soft Coral / Rose
                return Qt.color("#fb7185");
            case "drive":
            case "project_disk":
            case "storage":
                return Qt.color("#38bdf8");
            case "folder":
            case "project_folder":
            case "folder_project":
                return Qt.color("#fbbf24");
            default:
                return accentColor;
        }
    }

    // Refined theme tone:
    // Blends semantic base with 30% soft luminous tint (dark) or crisp deep tint (light)
    // and 15% theme accent, so it is softly pastel, never clashes, and naturally fits the active theme.
    readonly property color refinedThemeColor: {
        if (tint.a > 0) return tint;
        const base = semanticBaseColor;
        if (isLight) {
            return Qt.rgba(0.55 * base.r + 0.30 * 0.12 + 0.15 * accentColor.r,
                           0.55 * base.g + 0.30 * 0.14 + 0.15 * accentColor.g,
                           0.55 * base.b + 0.30 * 0.17 + 0.15 * accentColor.b, 1.0);
        } else {
            return Qt.rgba(0.55 * base.r + 0.30 * 0.94 + 0.15 * accentColor.r,
                           0.55 * base.g + 0.30 * 0.95 + 0.15 * accentColor.g,
                           0.55 * base.b + 0.30 * 0.97 + 0.15 * accentColor.b, 1.0);
        }
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
        }
    }
}
