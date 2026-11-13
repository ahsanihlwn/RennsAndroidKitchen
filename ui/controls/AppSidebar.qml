// SPDX-License-Identifier: MPL-2.0
// AppSidebar.qml — Collapsible left sidebar for RAK launcher
// Layout-only component: no button styles changed, only structure & positioning.
import QtQuick
import QtQuick.Controls as C

Item {
    id: sidebar

    // ── Public API ────────────────────────────────────────────────────────────
    required property var model          // uiModel (colors, strings, profile, drives, etc.)
    property bool collapsed: false       // collapse to icon-only rail (64px)
    signal settingsRequested()
    signal trashClicked()

    // ── Sizing ────────────────────────────────────────────────────────────────
    readonly property real expandedWidth: 220
    readonly property real collapsedWidth: 64
    implicitWidth: collapsed ? collapsedWidth : expandedWidth

    readonly property var c: model.colors
    readonly property var s: model.strings

    Behavior on implicitWidth {
        enabled: model.settings.animations
        NumberAnimation { duration: 240; easing.type: Easing.OutCubic }
    }

    // ── Background panel ──────────────────────────────────────────────────────
    SmoothRectangle {
        id: panel
        anchors.top: parent.top; anchors.topMargin: 12
        anchors.bottom: parent.bottom; anchors.bottomMargin: 12
        anchors.left: parent.left; anchors.leftMargin: 10
        width: sidebar.implicitWidth - 10
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
                        font.pixelSize: 10
                        color: sidebar.c.FG_MUTED
                    }
                }
            }

            // Collapse / expand chevron (only visible when expanded)
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
                    color: collapseHover.hovered ? sidebar.c.b08 : "transparent"
                }
                Image {
                    anchors.centerIn: parent; width: 11; height: 11
                    source: sidebar.model.icon("arrow_right", sidebar.c.FG_MUTED)
                    rotation: 180
                    sourceSize: Qt.size(11, 11)
                }
                HoverHandler { id: collapseHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: sidebar.collapsed = true }
            }

            // Tap anywhere on collapsed rail to expand
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

            // --- Projects nav item ---
            Item {
                width: parent.width; height: 40

                SmoothRectangle {
                    anchors.fill: parent; radius: 10
                    color: sidebar.model.page === "projects"
                        ? sidebar.c.a12 : navProjectsHov.hovered ? sidebar.c.b05 : "transparent"
                    border.color: sidebar.model.page === "projects" ? sidebar.c.a35 : "transparent"
                }
                Row {
                    x: sidebar.collapsed ? 14 : 12
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 11
                    Behavior on x {
                        enabled: sidebar.model.settings.animations
                        NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
                    }

                    Image {
                        width: 18; height: 18
                        source: sidebar.model.icon("project_list",
                            sidebar.model.page === "projects" ? sidebar.c.accent_hex : sidebar.c.FG2)
                        sourceSize: Qt.size(18, 18)
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    RakLabel {
                        text: sidebar.s.hdr_my_projects
                        font.pixelSize: 12; font.weight: Font.DemiBold
                        color: sidebar.model.page === "projects" ? sidebar.c.FG_BRIGHT : sidebar.c.FG
                        opacity: sidebar.collapsed ? 0 : 1
                        visible: opacity > 0
                        anchors.verticalCenter: parent.verticalCenter
                        Behavior on opacity {
                            enabled: sidebar.model.settings.animations
                            NumberAnimation { duration: 160 }
                        }
                    }
                }

                HoverHandler { id: navProjectsHov; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: if (sidebar.model.page !== "projects") sidebar.model.navigate("projects")
                }
            }

            // --- Settings nav item ---
            Item {
                width: parent.width; height: 40

                SmoothRectangle {
                    anchors.fill: parent; radius: 10
                    color: sidebar.model.page === "settings"
                        ? sidebar.c.a12 : navSettingsHov.hovered ? sidebar.c.b05 : "transparent"
                    border.color: sidebar.model.page === "settings" ? sidebar.c.a35 : "transparent"
                }
                Row {
                    x: sidebar.collapsed ? 14 : 12
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 11
                    Behavior on x {
                        enabled: sidebar.model.settings.animations
                        NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
                    }

                    Image {
                        width: 18; height: 18
                        source: sidebar.model.icon("settings_octagon",
                            sidebar.model.page === "settings"
                            ? sidebar.c.accent_hex
                            : navSettingsHov.hovered ? sidebar.c.FG2 : sidebar.c.FG_MUTED)
                        sourceSize: Qt.size(18, 18)
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    RakLabel {
                        text: sidebar.s.btn_settings
                        font.pixelSize: 12; font.weight: Font.Medium
                        color: sidebar.model.page === "settings" ? sidebar.c.FG_BRIGHT : sidebar.c.FG
                        opacity: sidebar.collapsed ? 0 : 1
                        visible: opacity > 0
                        anchors.verticalCenter: parent.verticalCenter
                        Behavior on opacity {
                            enabled: sidebar.model.settings.animations
                            NumberAnimation { duration: 160 }
                        }
                    }
                }

                HoverHandler { id: navSettingsHov; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: if (sidebar.model.page !== "settings") sidebar.model.navigate("settings")
                }
            }
        }

        // Divider before drives section
        SmoothRectangle {
            id: drivesDivider
            x: 12
            y: navColumn.y + navColumn.height + 10
            width: parent.width - 24; height: 1
            color: sidebar.c.b06
        }

        // "STORAGE" section label
        RakLabel {
            id: storageLabel
            x: 20
            y: drivesDivider.y + 10
            text: sidebar.s.lbl_storage || "STORAGE"
            font.pixelSize: 9; font.weight: Font.Bold
            color: sidebar.c.FG_MUTED
            opacity: sidebar.collapsed ? 0 : 1
            visible: opacity > 0
            Behavior on opacity {
                enabled: sidebar.model.settings.animations
                NumberAnimation { duration: 160 }
            }
        }

        // Drive list
        Column {
            id: driveColumn
            x: 10
            y: storageLabel.y + (sidebar.collapsed ? 0 : storageLabel.height + 6)
            width: parent.width - 20
            spacing: 4

            Behavior on y {
                enabled: sidebar.model.settings.animations
                NumberAnimation { duration: 160; easing.type: Easing.OutCubic }
            }

            // Trash shortcut in sidebar
            Item {
                width: parent.width; height: 44

                SmoothRectangle {
                    anchors.fill: parent; radius: 8
                    color: trashSideHov.hovered ? sidebar.c.b05 : "transparent"
                }
                Row {
                    x: sidebar.collapsed ? 14 : 10
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 10
                    Behavior on x {
                        enabled: sidebar.model.settings.animations
                        NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
                    }

                    Image {
                        width: 18; height: 18
                        source: sidebar.model.icon("project_trash", sidebar.c.FG_MUTED)
                        sourceSize: Qt.size(18, 18)
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Column {
                        opacity: sidebar.collapsed ? 0 : 1
                        visible: opacity > 0
                        spacing: 1
                        anchors.verticalCenter: parent.verticalCenter
                        Behavior on opacity {
                            enabled: sidebar.model.settings.animations
                            NumberAnimation { duration: 160 }
                        }
                        RakLabel {
                            text: sidebar.s.hdr_trash || "Trash"
                            font.pixelSize: 10; font.weight: Font.DemiBold
                            color: sidebar.c.FG_BRIGHT
                        }
                        RakLabel {
                            text: sidebar.s.trash_count
                                ? sidebar.s.trash_count.replace("{n}", sidebar.model.trashCount.toString())
                                : (sidebar.model.trashCount + " item(s)")
                            font.pixelSize: 9; color: sidebar.c.FG_MUTED
                        }
                    }
                }
                HoverHandler { id: trashSideHov; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: sidebar.trashClicked() }
            }

            // Real drives from model
            Repeater {
                model: sidebar.model.driveRows
                delegate: Item {
                    required property var modelData
                    required property int index
                    width: parent.width; height: sidebar.collapsed ? 42 : 48

                    Behavior on height {
                        enabled: sidebar.model.settings.animations
                        NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
                    }

                    SmoothRectangle {
                        anchors.fill: parent; radius: 8
                        color: sidebar.c.b04
                    }
                    Row {
                        x: sidebar.collapsed ? 14 : 10
                        y: sidebar.collapsed ? 12 : 8
                        spacing: 10
                        Behavior on x {
                            enabled: sidebar.model.settings.animations
                            NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
                        }

                        Image {
                            width: 18; height: 18
                            source: sidebar.model.icon("drive", sidebar.c.FG_MUTED)
                            sourceSize: Qt.size(18, 18)
                        }
                        Column {
                            spacing: 5
                            opacity: sidebar.collapsed ? 0 : 1
                            visible: opacity > 0
                            Behavior on opacity {
                                enabled: sidebar.model.settings.animations
                                NumberAnimation { duration: 160 }
                            }
                            RakLabel {
                                text: modelData.label || modelData.key
                                font.pixelSize: 10; font.weight: Font.DemiBold
                                color: sidebar.c.FG
                            }
                            SmoothRectangle {
                                width: 136; height: 3; radius: 1.5
                                color: sidebar.c.b08
                                SmoothRectangle {
                                    width: parent.width * Math.min(1, modelData.usedBytes / Math.max(1, modelData.totalBytes))
                                    height: parent.height; radius: parent.radius
                                    color: sidebar.c.accent_hex
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
