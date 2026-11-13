// SPDX-License-Identifier: MPL-2.0
// AppSidebar.qml — Collapsible left sidebar for the RAK launcher.
// Features:
// 1. PINNED ICONS: Icons are 100% stationary (fixed X position at all times).
//    During collapse & expand, icons do NOT move even a single pixel.
// 2. Sequenced animation: text fades out first, width collapses from the right.
// 3. Circular progress ring around drive disk icon when collapsed, fading to horizontal bar when expanded.
import QtQuick
import QtQuick.Shapes

Item {
    id: sidebar

    // ── Public API ────────────────────────────────────────────────────────────
    required property var model          // LauncherModel (uiModel)
    property bool collapsed: false       // true = icon-only 64px rail
    signal trashClicked()

    // ── Sizing constants ──────────────────────────────────────────────────────
    readonly property real expandedWidth: 220
    readonly property real collapsedWidth: 64

    // ── Animated properties driven by states ──────────────────────────────────
    property real contentWidth: expandedWidth
    property real textAlpha: 1.0
    property real ringProgress: 0.0      // 0.0 = fully expanded, 1.0 = fully collapsed

    implicitWidth: contentWidth
    width: contentWidth

    readonly property var c: model.colors
    readonly property var s: model.strings
    readonly property bool anims: model.settings.animations

    // ── Sequenced States & Transitions ────────────────────────────────────────
    states: [
        State {
            name: "expanded"
            when: !sidebar.collapsed
            PropertyChanges { target: sidebar; contentWidth: sidebar.expandedWidth; textAlpha: 1.0; ringProgress: 0.0 }
        },
        State {
            name: "collapsed"
            when: sidebar.collapsed
            PropertyChanges { target: sidebar; contentWidth: sidebar.collapsedWidth; textAlpha: 0.0; ringProgress: 1.0 }
        }
    ]

    transitions: [
        Transition {
            from: "expanded"; to: "collapsed"
            // COLLAPSE SEQUENCE:
            // 1. Text fades out first (100ms)
            // 2. Width shrinks to 64px from the right (200ms) — icons STAY PINNED!
            SequentialAnimation {
                NumberAnimation { target: sidebar; property: "textAlpha"; duration: sidebar.anims ? 100 : 0; easing.type: Easing.OutQuad }
                ParallelAnimation {
                    NumberAnimation { target: sidebar; property: "contentWidth"; duration: sidebar.anims ? 200 : 0; easing.type: Easing.OutCubic }
                    NumberAnimation { target: sidebar; property: "ringProgress"; duration: sidebar.anims ? 200 : 0; easing.type: Easing.OutCubic }
                }
            }
        },
        Transition {
            from: "collapsed"; to: "expanded"
            // EXPAND SEQUENCE:
            // 1. Width expands to 220px to the right (200ms) — icons STAY PINNED!
            // 2. Text smoothly fades in (120ms)
            SequentialAnimation {
                ParallelAnimation {
                    NumberAnimation { target: sidebar; property: "contentWidth"; duration: sidebar.anims ? 200 : 0; easing.type: Easing.OutCubic }
                    NumberAnimation { target: sidebar; property: "ringProgress"; duration: sidebar.anims ? 200 : 0; easing.type: Easing.OutCubic }
                }
                NumberAnimation { target: sidebar; property: "textAlpha"; duration: sidebar.anims ? 120 : 0; easing.type: Easing.OutQuad }
            }
        }
    ]

    // ── Background panel ──────────────────────────────────────────────────────
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

        // Item geometry inside panel:
        // When collapsed (panel width = 48), button item width = 36, x = 6.
        // Center of button item is at x = 18 inside item (which is x = 24 inside panel, x = 32 in sidebar).
        // Standard icon width = 18 -> fixed X position inside button = 18 - 9 = 9px ALWAYS!
        readonly property real itemX: 6
        readonly property real itemWidth: width - 12
        readonly property real fixedIconX: 9

        // ── Account section ───────────────────────────────────────────────────
        Item {
            id: accountRow
            x: panel.itemX; y: 12
            width: panel.itemWidth; height: 46

            // Avatar container: FIXED X at 1px inside accountRow (center aligned with all icons at x=32 in sidebar)
            Item {
                id: avatarBox
                x: 1; y: Math.round((parent.height - 34) / 2)
                width: 34; height: 34

                RoundedImage {
                    anchors.fill: parent
                    source: sidebar.model.profile.avatar || ""
                }
            }

            // Name + subtitle (expanded only, fades in/out without moving avatar)
            Column {
                anchors.left: avatarBox.right; anchors.leftMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                opacity: sidebar.textAlpha
                visible: opacity > 0.01
                spacing: 1

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
                opacity: sidebar.textAlpha
                visible: opacity > 0.01

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

        // Divider under account
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

                // ICON IS TOTALLY PINNED AT fixedIconX (NO MOVEMENT)
                Image {
                    id: projIcon
                    x: panel.fixedIconX
                    anchors.verticalCenter: parent.verticalCenter
                    width: 18; height: 18
                    source: sidebar.model.icon("project_list",
                        sidebar.model.page === "projects" ? sidebar.c.accent_hex : sidebar.c.FG2)
                    sourceSize: Qt.size(18, 18)
                }

                RakLabel {
                    anchors.left: projIcon.right; anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: sidebar.s.hdr_my_projects
                    font.pixelSize: 12; font.weight: Font.DemiBold
                    color: sidebar.model.page === "projects" ? sidebar.c.FG_BRIGHT : sidebar.c.FG
                    opacity: sidebar.textAlpha
                    visible: opacity > 0.01
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

                // ICON IS TOTALLY PINNED AT fixedIconX (NO MOVEMENT)
                Image {
                    id: setIcon
                    x: panel.fixedIconX
                    anchors.verticalCenter: parent.verticalCenter
                    width: 18; height: 18
                    source: sidebar.model.icon("settings_octagon",
                        sidebar.model.page === "settings"
                        ? sidebar.c.accent_hex
                        : navSetHov.hovered ? sidebar.c.FG2 : sidebar.c.FG_MUTED)
                    sourceSize: Qt.size(18, 18)
                }

                RakLabel {
                    anchors.left: setIcon.right; anchors.leftMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    text: sidebar.s.btn_settings
                    font.pixelSize: 12; font.weight: Font.Medium
                    color: sidebar.model.page === "settings" ? sidebar.c.FG_BRIGHT : sidebar.c.FG
                    opacity: sidebar.textAlpha
                    visible: opacity > 0.01
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

        // Divider before drives section
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

            // ICON IS TOTALLY PINNED AT fixedIconX (NO MOVEMENT)
            Image {
                id: trashIcon
                x: panel.fixedIconX
                anchors.verticalCenter: parent.verticalCenter
                width: 18; height: 18
                source: sidebar.model.icon("project_trash", sidebar.c.FG_MUTED)
                sourceSize: Qt.size(18, 18)
            }

            Column {
                anchors.left: trashIcon.right; anchors.leftMargin: 10
                anchors.verticalCenter: parent.verticalCenter
                opacity: sidebar.textAlpha
                visible: opacity > 0.01
                spacing: 1

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
            y: trashRow.y + trashRow.height + 6
            width: panel.itemWidth
            spacing: 5

            Repeater {
                model: sidebar.model.driveRows
                delegate: Item {
                    id: driveDelegate
                    required property var modelData
                    required property int index
                    width: driveColumn.width
                    height: 42

                    SmoothRectangle {
                        anchors.fill: parent; radius: 8
                        color: driveHover.hovered ? sidebar.c.b06 : (modelData.active ? sidebar.c.b03 : "transparent")
                        border.color: modelData.active ? (modelData.distro ? sidebar.c.a30 : sidebar.c.b08) : "transparent"
                    }

                    // ── CIRCULAR PROGRESS RING (Fades in/out around pinned icon) ────
                    Item {
                        id: ringArea
                        // Center of the ring is strictly aligned to the fixed disk icon (center at x = fixedIconX + 9 = 18)
                        x: panel.fixedIconX - 9
                        anchors.verticalCenter: parent.verticalCenter
                        width: 36; height: 36
                        opacity: sidebar.ringProgress
                        visible: opacity > 0.01

                        Shape {
                            anchors.fill: parent
                            layer.enabled: true
                            layer.samples: 4

                            // Background circular track
                            ShapePath {
                                strokeColor: sidebar.c.b10
                                strokeWidth: 2.2
                                fillColor: "transparent"
                                capStyle: ShapePath.RoundCap
                                PathAngleArc {
                                    centerX: 18; centerY: 18
                                    radiusX: 13.5; radiusY: 13.5
                                    startAngle: -90
                                    sweepAngle: 360
                                }
                            }

                            // Colored progress arc
                            ShapePath {
                                strokeColor: modelData.barColor || sidebar.c.accent_hex
                                strokeWidth: 2.2
                                fillColor: "transparent"
                                capStyle: ShapePath.RoundCap
                                PathAngleArc {
                                    centerX: 18; centerY: 18
                                    radiusX: 13.5; radiusY: 13.5
                                    startAngle: -90
                                    sweepAngle: 360 * Math.max(0.01, Math.min(1.0, modelData.fraction))
                                }
                            }
                        }
                    }

                    // ── DRIVE DISK ICON IS TOTALLY PINNED AT fixedIconX (NO MOVEMENT) ──
                    Image {
                        id: diskIcon
                        x: panel.fixedIconX
                        anchors.verticalCenter: parent.verticalCenter
                        width: 18; height: 18
                        source: sidebar.model.icon("project_disk", modelData.distro ? sidebar.c.accent_hex : "")
                        sourceSize: Qt.size(18, 18)
                        opacity: modelData.active ? 1 : 0.4
                    }

                    // ── EXPANDED DRIVE DETAILS (Name, GB usage, horizontal bar) ───────
                    Column {
                        anchors.left: diskIcon.right; anchors.leftMargin: 10
                        anchors.right: parent.right; anchors.rightMargin: 10
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 4
                        opacity: sidebar.textAlpha
                        visible: opacity > 0.01

                        Item {
                            width: parent.width; height: 14
                            RakLabel {
                                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                                text: modelData.name || modelData.key
                                font.pixelSize: 10; font.weight: Font.DemiBold
                                color: modelData.active ? sidebar.c.FG_BRIGHT : sidebar.c.FG_MUTED
                            }
                            RakLabel {
                                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                text: (modelData.used / 1073741824).toFixed(0) + " / " + (modelData.total / 1073741824).toFixed(0) + " GB"
                                font.pixelSize: 9; color: sidebar.c.FG_MUTED
                            }
                        }

                        // Horizontal capacity bar
                        SmoothRectangle {
                            width: parent.width
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
                        onTapped: {
                            if (sidebar.collapsed) sidebar.collapsed = false
                            else sidebar.model.dispatch("toggleDrive", {key: modelData.key})
                        }
                    }
                }
            }
        }
    }
}
