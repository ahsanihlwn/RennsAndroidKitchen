// SPDX-License-Identifier: MPL-2.0
// AppSidebar.qml — Collapsible left sidebar for the RAK launcher.
// Pure layout & alignment fix: mathematically centered in collapsed mode,
// clean drive rows with working icons, without altering button theme styles.
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

    readonly property real expandedWidth: 220
    readonly property real collapsedWidth: 64
    implicitWidth: collapsed ? collapsedWidth : expandedWidth
    width: implicitWidth

    Behavior on implicitWidth {
        enabled: model.settings.animations
        NumberAnimation { duration: 240; easing.type: Easing.OutCubic }
    }
    Behavior on width {
        enabled: model.settings.animations
        NumberAnimation { duration: 240; easing.type: Easing.OutCubic }
    }

    // ── Background panel ──────────────────────────────────────────────────────
    // Symmetrical left & right margins (8px each side)
    SmoothRectangle {
        id: panel
        anchors.top: parent.top;          anchors.topMargin: 12
        anchors.bottom: parent.bottom;    anchors.bottomMargin: 12
        anchors.left: parent.left;        anchors.leftMargin: 8
        anchors.right: parent.right;      anchors.rightMargin: 8
        radius: 14
        color: sidebar.c.BG_DARK
        border.color: sidebar.c.b06
        clip: true

        // ── Helper: width of clickable items inside panel ──────────────────────
        readonly property real itemWidth: width - 12
        readonly property real itemX: 6

        // ── Account section ───────────────────────────────────────────────────
        Item {
            id: accountRow
            x: panel.itemX; y: 12
            width: panel.itemWidth; height: 46

            // Avatar container: centered when collapsed, left-aligned when expanded
            Item {
                id: avatarBox
                x: sidebar.collapsed ? Math.round((parent.width - width) / 2) : 4
                y: Math.round((parent.height - height) / 2)
                width: 34; height: 34

                Behavior on x {
                    enabled: sidebar.model.settings.animations
                    NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
                }

                RoundedImage {
                    anchors.fill: parent
                    source: sidebar.model.profile.avatar || ""
                }
            }

            // Name + subtitle (expanded only)
            Column {
                anchors.left: avatarBox.right; anchors.leftMargin: 10
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

            // Collapse toggle chevron (expanded only)
            Item {
                anchors.right: parent.right; anchors.rightMargin: 4
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

            // Tap anywhere on collapsed rail to expand
            TapHandler {
                enabled: sidebar.collapsed
                onTapped: sidebar.collapsed = false
            }
        }

        // Divider under account — perfectly centered
        SmoothRectangle {
            id: divAccount
            anchors.horizontalCenter: parent.horizontalCenter
            y: accountRow.y + accountRow.height + 6
            width: parent.width - 16; height: 1
            color: sidebar.c.b06
        }

        // ── Navigation items (Projects & Settings) ─────────────────────────────
        Column {
            id: navColumn
            x: panel.itemX
            y: divAccount.y + 10
            width: panel.itemWidth
            spacing: 5

            // --- Projects nav item ---
            Item {
                width: parent.width; height: 38

                SmoothRectangle {
                    anchors.fill: parent; radius: 8
                    color: sidebar.model.page === "projects"
                        ? sidebar.c.a12 : navProjHov.hovered ? sidebar.c.b05 : "transparent"
                    border.color: sidebar.model.page === "projects" ? sidebar.c.a35 : "transparent"
                }

                // Icon: centered when collapsed, left margin when expanded
                Image {
                    id: projIcon
                    x: sidebar.collapsed ? Math.round((parent.width - width) / 2) : 10
                    anchors.verticalCenter: parent.verticalCenter
                    width: 18; height: 18
                    source: sidebar.model.icon("project_list",
                        sidebar.model.page === "projects" ? sidebar.c.accent_hex : sidebar.c.FG2)
                    sourceSize: Qt.size(18, 18)

                    Behavior on x {
                        enabled: sidebar.model.settings.animations
                        NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
                    }
                }

                RakLabel {
                    anchors.left: projIcon.right; anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: sidebar.s.hdr_my_projects
                    font.pixelSize: 12; font.weight: Font.DemiBold
                    color: sidebar.model.page === "projects" ? sidebar.c.FG_BRIGHT : sidebar.c.FG
                    opacity: sidebar.collapsed ? 0 : 1
                    visible: opacity > 0
                    Behavior on opacity {
                        enabled: sidebar.model.settings.animations
                        NumberAnimation { duration: 160 }
                    }
                }

                HoverHandler { id: navProjHov; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        if (sidebar.collapsed) sidebar.collapsed = false
                        if (sidebar.model.page !== "projects") sidebar.model.navigate("projects")
                    }
                }
            }

            // --- Settings nav item ---
            Item {
                width: parent.width; height: 38

                SmoothRectangle {
                    anchors.fill: parent; radius: 8
                    color: sidebar.model.page === "settings"
                        ? sidebar.c.a12 : navSetHov.hovered ? sidebar.c.b05 : "transparent"
                    border.color: sidebar.model.page === "settings" ? sidebar.c.a35 : "transparent"
                }

                // Icon: centered when collapsed, left margin when expanded
                Image {
                    id: setIcon
                    x: sidebar.collapsed ? Math.round((parent.width - width) / 2) : 10
                    anchors.verticalCenter: parent.verticalCenter
                    width: 18; height: 18
                    source: sidebar.model.icon("settings_octagon",
                        sidebar.model.page === "settings"
                        ? sidebar.c.accent_hex
                        : navSetHov.hovered ? sidebar.c.FG2 : sidebar.c.FG_MUTED)
                    sourceSize: Qt.size(18, 18)

                    Behavior on x {
                        enabled: sidebar.model.settings.animations
                        NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
                    }
                }

                RakLabel {
                    anchors.left: setIcon.right; anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: sidebar.s.btn_settings
                    font.pixelSize: 12; font.weight: Font.Medium
                    color: sidebar.model.page === "settings" ? sidebar.c.FG_BRIGHT : sidebar.c.FG
                    opacity: sidebar.collapsed ? 0 : 1
                    visible: opacity > 0
                    Behavior on opacity {
                        enabled: sidebar.model.settings.animations
                        NumberAnimation { duration: 160 }
                    }
                }

                HoverHandler { id: navSetHov; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        if (sidebar.collapsed) sidebar.collapsed = false
                        if (sidebar.model.page !== "settings") sidebar.model.navigate("settings")
                    }
                }
            }
        }

        // Divider before drives section — perfectly centered
        SmoothRectangle {
            id: divDrives
            anchors.horizontalCenter: parent.horizontalCenter
            y: navColumn.y + navColumn.height + 8
            width: parent.width - 16; height: 1
            color: sidebar.c.b06
        }

        // Trash shortcut item
        Item {
            id: trashRow
            x: panel.itemX
            y: divDrives.y + 8
            width: panel.itemWidth; height: 38

            SmoothRectangle {
                anchors.fill: parent; radius: 8
                color: trashHov.hovered ? sidebar.c.b05 : "transparent"
            }

            // Trash icon: centered when collapsed, left-aligned when expanded
            Image {
                id: trashIcon
                x: sidebar.collapsed ? Math.round((parent.width - width) / 2) : 10
                anchors.verticalCenter: parent.verticalCenter
                width: 18; height: 18
                source: sidebar.model.icon("project_trash", sidebar.c.FG_MUTED)
                sourceSize: Qt.size(18, 18)

                Behavior on x {
                    enabled: sidebar.model.settings.animations
                    NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
                }
            }

            Column {
                anchors.left: trashIcon.right; anchors.leftMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                opacity: sidebar.collapsed ? 0 : 1
                visible: opacity > 0
                spacing: 1
                Behavior on opacity {
                    enabled: sidebar.model.settings.animations
                    NumberAnimation { duration: 160 }
                }
                RakLabel {
                    text: sidebar.s.hdr_trash
                    font.pixelSize: 11; font.weight: Font.DemiBold; color: sidebar.c.FG_BRIGHT
                }
                RakLabel {
                    text: sidebar.s.trash_count.replace("{n}", sidebar.model.trashCount.toString())
                    font.pixelSize: 9; color: sidebar.c.FG_MUTED
                }
            }

            HoverHandler { id: trashHov; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: sidebar.trashClicked() }
        }

        // ── Drive storage section ─────────────────────────────────────────────
        Column {
            id: driveColumn
            x: panel.itemX
            y: trashRow.y + trashRow.height + 4
            width: panel.itemWidth
            spacing: 5

            Repeater {
                model: sidebar.model.driveRows
                delegate: Item {
                    id: driveDelegate
                    required property var modelData
                    required property int index
                    width: driveColumn.width
                    height: sidebar.collapsed ? 38 : 46

                    Behavior on height {
                        enabled: sidebar.model.settings.animations
                        NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
                    }

                    SmoothRectangle {
                        anchors.fill: parent; radius: 8
                        color: driveHover.hovered ? sidebar.c.b06 : (modelData.active ? sidebar.c.b03 : "transparent")
                        border.color: modelData.active ? (modelData.distro ? sidebar.c.a30 : sidebar.c.b08) : "transparent"
                    }

                    // Disk icon: centered when collapsed, left-aligned when expanded
                    Image {
                        id: diskIcon
                        x: sidebar.collapsed ? Math.round((parent.width - width) / 2) : 10
                        anchors.verticalCenter: sidebar.collapsed ? parent.verticalCenter : undefined
                        y: sidebar.collapsed ? undefined : 10
                        width: 18; height: 18
                        source: sidebar.model.icon("project_disk", modelData.distro ? sidebar.c.accent_hex : "")
                        sourceSize: Qt.size(18, 18)
                        opacity: modelData.active ? 1 : 0.4

                        Behavior on x {
                            enabled: sidebar.model.settings.animations
                            NumberAnimation { duration: 200; easing.type: Easing.OutCubic }
                        }
                    }

                    // Expanded drive details (name, GB usage, capacity bar)
                    Column {
                        anchors.left: diskIcon.right; anchors.leftMargin: 10
                        y: 7
                        opacity: sidebar.collapsed ? 0 : 1
                        visible: opacity > 0
                        spacing: 4

                        Behavior on opacity {
                            enabled: sidebar.model.settings.animations
                            NumberAnimation { duration: 160 }
                        }

                        Row {
                            spacing: 6
                            RakLabel {
                                text: modelData.name || modelData.key
                                font.pixelSize: 10; font.weight: Font.DemiBold
                                color: modelData.active ? sidebar.c.FG_BRIGHT : sidebar.c.FG_MUTED
                            }
                            RakLabel {
                                text: (modelData.used / 1073741824).toFixed(0) + "/" + (modelData.total / 1073741824).toFixed(0) + " GB"
                                font.pixelSize: 9; color: sidebar.c.FG_MUTED
                            }
                        }

                        // Usage progress bar
                        SmoothRectangle {
                            width: Math.max(20, driveDelegate.width - 40)
                            height: 3; radius: 1.5
                            color: sidebar.c.b08
                            SmoothRectangle {
                                width: parent.width * Math.max(0, Math.min(1, modelData.fraction))
                                height: parent.height; radius: parent.radius
                                color: modelData.barColor || sidebar.c.accent_hex
                            }
                        }
                    }

                    HoverHandler { id: driveHover; cursorShape: Qt.PointingHandCursor }
                    HoverTip {
                        label: (modelData.name || modelData.key) + "\n" +
                               (modelData.used / 1073741824).toFixed(1) + " / " +
                               (modelData.total / 1073741824).toFixed(1) + " GB"
                        hovering: driveHover.hovered
                    }
                    TapHandler {
                        onTapped: sidebar.model.dispatch("toggleDrive", {key: modelData.key})
                    }
                }
            }
        }
    }
}
