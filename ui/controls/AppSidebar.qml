// SPDX-License-Identifier: MPL-2.0
// AppSidebar.qml — Collapsible left sidebar for the RAK launcher.
// Pure layout change: no button styles, colors, or themes are modified.
import QtQuick

Item {
    id: sidebar

    // ── Public API ────────────────────────────────────────────────────────────
    required property var model          // LauncherModel (uiModel)
    property bool collapsed: false       // true = icon-only 64px rail
    signal trashClicked()

    // ── Sizing ────────────────────────────────────────────────────────────────
    readonly property var c: model.colors
    readonly property var s: model.strings

    width: collapsed ? 64 : 220

    Behavior on width {
        enabled: model.settings.animations
        NumberAnimation { duration: 240; easing.type: Easing.OutCubic }
    }

    // ── Background panel ──────────────────────────────────────────────────────
    SmoothRectangle {
        id: panel
        anchors.top: parent.top;    anchors.topMargin: 12
        anchors.bottom: parent.bottom; anchors.bottomMargin: 12
        anchors.left: parent.left;  anchors.leftMargin: 10
        anchors.right: parent.right
        radius: 14
        color: sidebar.c.BG_DARK
        border.color: sidebar.c.b06
        clip: true

        // ── Account section ───────────────────────────────────────────────────
        Item {
            id: accountRow
            x: 0; y: 14
            width: parent.width; height: 48

            Row {
                x: 14
                anchors.verticalCenter: parent.verticalCenter
                spacing: 11

                RoundedImage {
                    width: 34; height: 34
                    source: sidebar.model.profile.avatar || ""
                }
                Column {
                    anchors.verticalCenter: parent.verticalCenter
                    opacity: sidebar.collapsed ? 0 : 1
                    visible: opacity > 0
                    spacing: 1
                    Behavior on opacity {
                        enabled: sidebar.model.settings.animations
                        NumberAnimation { duration: 160 }
                    }
                    RakLabel {
                        text: sidebar.model.profile.name || ""
                        font.pixelSize: 12; font.weight: Font.DemiBold
                        color: sidebar.c.FG_BRIGHT
                    }
                    RakLabel {
                        text: sidebar.model.profile.subtitle || ""
                        font.pixelSize: 10; color: sidebar.c.FG_MUTED
                    }
                }
            }

            // Collapse chevron
            Item {
                anchors.right: parent.right; anchors.rightMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                width: 22; height: 22
                opacity: sidebar.collapsed ? 0 : 1
                visible: opacity > 0
                Behavior on opacity {
                    enabled: sidebar.model.settings.animations
                    NumberAnimation { duration: 160 }
                }
                SmoothRectangle {
                    anchors.fill: parent; radius: 6
                    color: collapseHov.hovered ? sidebar.c.b08 : "transparent"
                }
                Image {
                    anchors.centerIn: parent; width: 11; height: 11
                    source: sidebar.model.icon("arrow_right", sidebar.c.FG_MUTED)
                    rotation: 180; sourceSize: Qt.size(11, 11)
                }
                HoverHandler { id: collapseHov; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: sidebar.collapsed = true }
            }

            // Tap to expand when collapsed
            TapHandler {
                enabled: sidebar.collapsed
                onTapped: sidebar.collapsed = false
            }
        }

        // Divider under account
        SmoothRectangle {
            x: 12; y: accountRow.y + accountRow.height + 4
            width: parent.width - 24; height: 1
            color: sidebar.c.b06
        }

        // ── Navigation items ──────────────────────────────────────────────────
        Column {
            id: navColumn
            x: 10
            y: accountRow.y + accountRow.height + 14
            width: parent.width - 20
            spacing: 4

            // Projects nav
            Item {
                width: parent.width; height: 40
                SmoothRectangle {
                    anchors.fill: parent; radius: 10
                    color: sidebar.model.page === "projects"
                        ? sidebar.c.a12 : navProjHov.hovered ? sidebar.c.b05 : "transparent"
                    border.color: sidebar.model.page === "projects" ? sidebar.c.a35 : "transparent"
                }
                Row {
                    x: sidebar.collapsed ? 14 : 12
                    anchors.verticalCenter: parent.verticalCenter; spacing: 11
                    Behavior on x {
                        enabled: sidebar.model.settings.animations
                        NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
                    }
                    Image {
                        width: 18; height: 18
                        source: sidebar.model.icon("project_list",
                            sidebar.model.page === "projects" ? sidebar.c.accent_hex : sidebar.c.FG2)
                        sourceSize: Qt.size(18, 18); anchors.verticalCenter: parent.verticalCenter
                    }
                    RakLabel {
                        text: sidebar.s.hdr_my_projects
                        font.pixelSize: 12; font.weight: Font.DemiBold
                        color: sidebar.model.page === "projects" ? sidebar.c.FG_BRIGHT : sidebar.c.FG
                        opacity: sidebar.collapsed ? 0 : 1; visible: opacity > 0
                        anchors.verticalCenter: parent.verticalCenter
                        Behavior on opacity {
                            enabled: sidebar.model.settings.animations
                            NumberAnimation { duration: 160 }
                        }
                    }
                }
                HoverHandler { id: navProjHov; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: if (sidebar.model.page !== "projects") sidebar.model.navigate("projects")
                }
            }

            // Settings nav
            Item {
                width: parent.width; height: 40
                SmoothRectangle {
                    anchors.fill: parent; radius: 10
                    color: sidebar.model.page === "settings"
                        ? sidebar.c.a12 : navSetHov.hovered ? sidebar.c.b05 : "transparent"
                    border.color: sidebar.model.page === "settings" ? sidebar.c.a35 : "transparent"
                }
                Row {
                    x: sidebar.collapsed ? 14 : 12
                    anchors.verticalCenter: parent.verticalCenter; spacing: 11
                    Behavior on x {
                        enabled: sidebar.model.settings.animations
                        NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
                    }
                    Image {
                        width: 18; height: 18
                        source: sidebar.model.icon("settings_octagon",
                            sidebar.model.page === "settings"
                            ? sidebar.c.accent_hex
                            : navSetHov.hovered ? sidebar.c.FG2 : sidebar.c.FG_MUTED)
                        sourceSize: Qt.size(18, 18); anchors.verticalCenter: parent.verticalCenter
                    }
                    RakLabel {
                        text: sidebar.s.btn_settings
                        font.pixelSize: 12; font.weight: Font.Medium
                        color: sidebar.model.page === "settings" ? sidebar.c.FG_BRIGHT : sidebar.c.FG
                        opacity: sidebar.collapsed ? 0 : 1; visible: opacity > 0
                        anchors.verticalCenter: parent.verticalCenter
                        Behavior on opacity {
                            enabled: sidebar.model.settings.animations
                            NumberAnimation { duration: 160 }
                        }
                    }
                }
                HoverHandler { id: navSetHov; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: if (sidebar.model.page !== "settings") sidebar.model.navigate("settings")
                }
            }
        }

        // Divider before drives
        SmoothRectangle {
            id: drivesDivider
            x: 12
            y: navColumn.y + navColumn.height + 10
            width: parent.width - 24; height: 1
            color: sidebar.c.b06
        }

        // Trash shortcut
        Item {
            id: trashRow
            x: 10
            y: drivesDivider.y + 10
            width: parent.width - 20; height: 44

            SmoothRectangle {
                anchors.fill: parent; radius: 8
                color: trashHov.hovered ? sidebar.c.b05 : "transparent"
            }
            Row {
                x: sidebar.collapsed ? 14 : 10
                anchors.verticalCenter: parent.verticalCenter; spacing: 10
                Behavior on x {
                    enabled: sidebar.model.settings.animations
                    NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
                }
                Image {
                    width: 18; height: 18
                    source: sidebar.model.icon("project_trash", sidebar.c.FG_MUTED)
                    sourceSize: Qt.size(18, 18); anchors.verticalCenter: parent.verticalCenter
                }
                Column {
                    spacing: 1; anchors.verticalCenter: parent.verticalCenter
                    opacity: sidebar.collapsed ? 0 : 1; visible: opacity > 0
                    Behavior on opacity {
                        enabled: sidebar.model.settings.animations
                        NumberAnimation { duration: 160 }
                    }
                    RakLabel {
                        text: sidebar.s.hdr_trash
                        font.pixelSize: 10; font.weight: Font.DemiBold; color: sidebar.c.FG_BRIGHT
                    }
                    RakLabel {
                        text: sidebar.s.trash_count.replace("{n}", sidebar.model.trashCount.toString())
                        font.pixelSize: 9; color: sidebar.c.FG_MUTED
                    }
                }
            }
            HoverHandler { id: trashHov; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: sidebar.trashClicked() }
        }

        // Drive list (using DriveCard — same component as DriveShelf)
        Column {
            id: driveColumn
            x: 10
            y: trashRow.y + trashRow.height + 6
            width: parent.width - 20
            spacing: 4

            Repeater {
                model: sidebar.model.driveRows
                delegate: DriveCard {
                    required property var modelData
                    required property int index
                    // DriveCard expects width 128 but we stretch to fill
                    width: driveColumn.width
                    model: sidebar.model
                    modelData: parent.modelData
                    // No animated progress here — sidebar is secondary
                    usageProgress: 1
                    // Hide when sidebar is collapsed: show only icon
                    opacity: sidebar.collapsed ? 0.6 : 1
                }
            }
        }
    }
}
