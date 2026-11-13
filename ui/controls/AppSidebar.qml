// SPDX-License-Identifier: MPL-2.0
// AppSidebar.qml — Collapsible left sidebar for the RAK launcher.
// Features:
// 1. Sequenced collapse/expand animation: text fades out first before shrinking,
//    preventing text-icon collision / "ngejedug".
// 2. Circular progress ring for drive storage when collapsed (PathAngleArc),
//    expanding to full detailed horizontal progress bar when expanded.
// 3. Symmetrical alignment across all icons in both modes.
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
            // 1. Text fades out first to prevent collision (100ms)
            // 2. Width glides to 64px while icons slide to center and rings appear (220ms)
            SequentialAnimation {
                NumberAnimation { target: sidebar; property: "textAlpha"; duration: sidebar.anims ? 100 : 0; easing.type: Easing.OutQuad }
                ParallelAnimation {
                    NumberAnimation { target: sidebar; property: "contentWidth"; duration: sidebar.anims ? 220 : 0; easing.type: Easing.OutCubic }
                    NumberAnimation { target: sidebar; property: "ringProgress"; duration: sidebar.anims ? 220 : 0; easing.type: Easing.OutCubic }
                }
            }
        },
        Transition {
            from: "collapsed"; to: "expanded"
            // EXPAND SEQUENCE:
            // 1. Width glides to 220px first while rings return to bars (220ms)
            // 2. Text gracefully fades in once space is ready (120ms)
            SequentialAnimation {
                ParallelAnimation {
                    NumberAnimation { target: sidebar; property: "contentWidth"; duration: sidebar.anims ? 220 : 0; easing.type: Easing.OutCubic }
                    NumberAnimation { target: sidebar; property: "ringProgress"; duration: sidebar.anims ? 220 : 0; easing.type: Easing.OutCubic }
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

        readonly property real itemWidth: width - 12
        readonly property real itemX: 6

        // ── Account section ───────────────────────────────────────────────────
        Item {
            id: accountRow
            x: panel.itemX; y: 12
            width: panel.itemWidth; height: 46

            // Avatar container: smoothly shifts from x=4 to center as sidebar collapses
            Item {
                id: avatarBox
                x: Math.round(4 + (parent.width - 34 - 4) * sidebar.ringProgress / 2)
                y: Math.round((parent.height - 34) / 2)
                width: 34; height: 34

                RoundedImage {
                    anchors.fill: parent
                    source: sidebar.model.profile.avatar || ""
                }
            }

            // Name + subtitle (fades out first during collapse)
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

                // Icon shifts dynamically into center
                Image {
                    id: projIcon
                    x: Math.round(10 + ((parent.width - 18) / 2 - 10) * sidebar.ringProgress)
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

                // Icon shifts dynamically into center
                Image {
                    id: setIcon
                    x: Math.round(10 + ((parent.width - 18) / 2 - 10) * sidebar.ringProgress)
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

            // Trash icon shifts into center
            Image {
                id: trashIcon
                x: Math.round(10 + ((parent.width - 18) / 2 - 10) * sidebar.ringProgress)
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

        // ── Drive storage section (With Circular Progress Ring on Collapse) ───
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
                    height: sidebar.collapsed ? 40 : 46

                    Behavior on height {
                        enabled: sidebar.anims
                        NumberAnimation { duration: 180; easing.type: Easing.OutCubic }
                    }

                    SmoothRectangle {
                        anchors.fill: parent; radius: 8
                        color: driveHover.hovered ? sidebar.c.b06 : (modelData.active ? sidebar.c.b03 : "transparent")
                        border.color: modelData.active ? (modelData.distro ? sidebar.c.a30 : sidebar.c.b08) : "transparent"
                    }

                    // ── COLLAPSED MODE: CIRCULAR RING SURROUNDING THE DRIVE ICON ───
                    Item {
                        id: ringContainer
                        anchors.centerIn: parent
                        width: 36; height: 36
                        opacity: sidebar.ringProgress
                        visible: opacity > 0.01

                        Shape {
                            anchors.fill: parent
                            layer.enabled: true
                            layer.samples: 4

                            // Background subtle circular track
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

                            // Colored progress arc for capacity fraction
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

                        // Drive disk icon placed in center of the ring
                        Image {
                            anchors.centerIn: parent
                            width: 14; height: 14
                            source: sidebar.model.icon("project_disk", modelData.distro ? sidebar.c.accent_hex : "")
                            sourceSize: Qt.size(14, 14)
                            opacity: modelData.active ? 1 : 0.4
                        }
                    }

                    // ── EXPANDED MODE: HORIZONTAL LAYOUT WITH PROGRESS BAR ─────────
                    Item {
                        id: expandedContainer
                        anchors.fill: parent
                        opacity: 1.0 - sidebar.ringProgress
                        visible: opacity > 0.01

                        Image {
                            id: expDiskIcon
                            x: 10
                            anchors.verticalCenter: parent.verticalCenter
                            width: 18; height: 18
                            source: sidebar.model.icon("project_disk", modelData.distro ? sidebar.c.accent_hex : "")
                            sourceSize: Qt.size(18, 18)
                            opacity: modelData.active ? 1 : 0.4
                        }

                        Column {
                            anchors.left: expDiskIcon.right; anchors.leftMargin: 10
                            anchors.right: parent.right; anchors.rightMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 4

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
