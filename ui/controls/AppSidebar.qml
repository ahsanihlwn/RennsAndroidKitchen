// SPDX-License-Identifier: MPL-2.0
// AppSidebar.qml — Collapsible left sidebar for the RAK launcher.
// Features:
// 1. PINNED ICONS: Icons are 100% stationary (fixed X/Y position at all times).
// 2. Parallel expand animation: text fades in concurrently with sidebar opening.
// 3. Sequential collapse animation: text fades out first before shrinking to prevent collision.
// 4. Fill-up animations (0 to target%):
//    - Ring sweep animation (0° to target angle) every time sidebar collapses.
//    - Bar fill animation (0px to target width) every time sidebar expands.
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
    property real ringFill: 0.0          // sweeps from 0.0 to 1.0 when collapsing
    property real barFill: 1.0           // fills from 0.0 to 1.0 when expanding

    implicitWidth: contentWidth
    width: contentWidth

    readonly property var c: model.colors
    readonly property var s: model.strings
    readonly property bool anims: model.settings.animations
    readonly property bool showProfile: !!model.privateEnabled && !!model.profile.name
    readonly property bool isLight: (c && c.is_light !== undefined) ? c.is_light : false
    readonly property color accentColor: Qt.color(c && c.accent_hex ? c.accent_hex : "#3ecf8e")
    readonly property color refinedTone: isLight
        ? Qt.rgba(0.78 * 0.13 + 0.22 * accentColor.r, 0.78 * 0.15 + 0.22 * accentColor.g, 0.78 * 0.18 + 0.22 * accentColor.b, 1.0)
        : Qt.rgba(0.72 * 0.92 + 0.28 * accentColor.r, 0.72 * 0.93 + 0.28 * accentColor.g, 0.72 * 0.95 + 0.28 * accentColor.b, 1.0)

    // ── Sequenced States & Transitions ────────────────────────────────────────
    states: [
        State {
            name: "expanded"
            when: !sidebar.collapsed
            PropertyChanges {
                target: sidebar
                contentWidth: sidebar.expandedWidth
                textAlpha: 1.0
                ringProgress: 0.0
                ringFill: 0.0
                barFill: 1.0
            }
        },
        State {
            name: "collapsed"
            when: sidebar.collapsed
            PropertyChanges {
                target: sidebar
                contentWidth: sidebar.collapsedWidth
                textAlpha: 0.0
                ringProgress: 1.0
                ringFill: 1.0
                barFill: 0.0
            }
        }
    ]

    transitions: [
        Transition {
            from: "expanded"; to: "collapsed"
            // COLLAPSE SEQUENCE:
            // 1. Text fades out fast (70ms) to clear the space
            // 2. Width glides to 64px while the circular ring sweeps from 0 to 100% (OutCubic)
            SequentialAnimation {
                NumberAnimation { target: sidebar; property: "textAlpha"; duration: sidebar.anims ? 70 : 0; easing.type: Easing.OutQuad }
                ParallelAnimation {
                    NumberAnimation { target: sidebar; property: "contentWidth"; duration: sidebar.anims ? 200 : 0; easing.type: Easing.OutCubic }
                    NumberAnimation { target: sidebar; property: "ringProgress"; duration: sidebar.anims ? 200 : 0; easing.type: Easing.OutCubic }
                    NumberAnimation { target: sidebar; property: "ringFill"; from: 0.0; to: 1.0; duration: sidebar.anims ? 320 : 0; easing.type: Easing.OutCubic }
                }
            }
        },
        Transition {
            from: "collapsed"; to: "expanded"
            // EXPAND SEQUENCE:
            // Text animates IN PARALLEL with width expansion (does NOT wait for expand to finish)
            // Bar sweeps from 0% to full width as it opens!
            ParallelAnimation {
                NumberAnimation { target: sidebar; property: "contentWidth"; duration: sidebar.anims ? 220 : 0; easing.type: Easing.OutCubic }
                NumberAnimation { target: sidebar; property: "ringProgress"; duration: sidebar.anims ? 180 : 0; easing.type: Easing.OutCubic }
                SequentialAnimation {
                    PauseAnimation { duration: sidebar.anims ? 25 : 0 }
                    NumberAnimation { target: sidebar; property: "textAlpha"; duration: sidebar.anims ? 160 : 0; easing.type: Easing.OutCubic }
                }
                SequentialAnimation {
                    PauseAnimation { duration: sidebar.anims ? 35 : 0 }
                    NumberAnimation { target: sidebar; property: "barFill"; from: 0.0; to: 1.0; duration: sidebar.anims ? 320 : 0; easing.type: Easing.OutCubic }
                }
            }
        }
    ]

    // ── Background panel ──────────────────────────────────────────────────────
    SmoothRectangle {
        id: panel
        anchors.fill: parent
        radius: 16
        color: sidebar.c.BG2
        border.color: sidebar.c.b08
        border.width: 1
        clip: true

        readonly property real itemX: 8
        readonly property real itemWidth: width - 16
        readonly property real fixedIconX: 15

        // ── Top / Account section ─────────────────────────────────────────────
        Item {
            id: topSection
            x: panel.itemX; y: 18
            width: panel.itemWidth
            height: sidebar.showProfile ? 46 : (sidebar.collapsed ? 0 : 32)
            visible: sidebar.showProfile || !sidebar.collapsed

            // Profile card (Visible only when connected to rennsproject / OFCL)
            Item {
                id: profileArea
                visible: sidebar.showProfile
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.rightMargin: 6
                anchors.top: parent.top
                anchors.bottom: parent.bottom

                // Hover feedback
                SmoothRectangle {
                    anchors.fill: parent
                    radius: 8
                    color: profileHov.hovered ? sidebar.c.b06 : "transparent"
                }

                // Avatar container: FIXED X at 7px inside account area (center aligned with all icons at x=32 in sidebar)
                Item {
                    id: avatarBox
                    x: 7; y: Math.round((parent.height - 34) / 2)
                    width: 34; height: 34

                    RoundedImage {
                        anchors.fill: parent
                        source: sidebar.model.profile.avatar || ""
                    }

                    // Verified badge pinned to corner of avatar when sidebar is collapsed
                    Item {
                        id: avatarCornerBadge
                        z: 10
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.rightMargin: -2
                        anchors.bottomMargin: -2
                        width: 14; height: 14
                        opacity: 1.0 - sidebar.textAlpha
                        visible: opacity > 0.01 && !!sidebar.model.profile.badge

                        SmoothRectangle {
                            anchors.fill: parent
                            anchors.margins: -1.5
                            radius: width / 2
                            color: sidebar.c.BG2
                        }

                        Image {
                            anchors.fill: parent
                            source: visible ? sidebar.model.icon(sidebar.model.profile.badge, "") : ""
                            sourceSize: Qt.size(14, 14)
                        }

                        HoverHandler { id: cornerBadgeHov }
                        HoverTip {
                            label: "Verified (" + (sidebar.model.profile.verifiedLevel || "") + ")"
                            hovering: cornerBadgeHov.hovered
                        }
                    }
                }

                // Name + subtitle + badge (expanded only, fades in concurrently with expand)
                Item {
                    id: profileTextArea
                    anchors.left: avatarBox.right; anchors.leftMargin: 10
                    anchors.right: parent.right; anchors.rightMargin: 2
                    anchors.verticalCenter: parent.verticalCenter
                    height: 32
                    opacity: sidebar.textAlpha
                    visible: opacity > 0.01
                    clip: true

                    RakLabel {
                        id: profileName
                        x: 0; y: 0; height: 17
                        text: sidebar.model.profile.name || ""
                        font.pixelSize: 12; font.weight: Font.DemiBold
                        color: sidebar.c.FG_BRIGHT
                        elide: Text.ElideRight
                        width: Math.min(implicitWidth, parent.width - (profileBadge.visible ? 18 : 0))
                    }

                    Image {
                        id: profileBadge
                        x: profileName.x + profileName.width + 4
                        y: 1.5
                        width: 14; height: 14
                        visible: !!sidebar.model.profile.badge
                        source: visible ? sidebar.model.icon(sidebar.model.profile.badge, "") : ""
                        sourceSize: Qt.size(14, 14)

                        HoverHandler { id: badgeHover }
                        HoverTip {
                            label: "Verified (" + (sidebar.model.profile.verifiedLevel || "") + ")"
                            hovering: badgeHover.hovered
                        }
                    }

                    RakLabel {
                        id: profileStatus
                        x: 0; y: 17; height: 15
                        width: parent.width
                        text: sidebar.model.profile.subtitle || ""
                        font.pixelSize: 10
                        color: sidebar.c.FG_MUTED
                        elide: Text.ElideRight
                    }
                }

                HoverHandler {
                    id: profileHov
                    cursorShape: (sidebar.model.profile.canEdit !== false) ? Qt.PointingHandCursor : Qt.ArrowCursor
                }
                TapHandler {
                    enabled: sidebar.model.profile.canEdit !== false
                    onTapped: sidebar.model.dispatch("profile", {})
                }
            }


        }



        // Divider under account (visible only when profile is shown)
        SmoothRectangle {
            id: divAccount
            visible: sidebar.showProfile
            anchors.horizontalCenter: parent.horizontalCenter
            y: topSection.y + topSection.height + 6
            width: parent.width - 16; height: visible ? 1 : 0
            color: sidebar.c.b06
        }

        // ── Navigation items (Projects & Settings) ─────────────────────────────
        Column {
            id: navColumn
            x: panel.itemX
            y: sidebar.showProfile ? (divAccount.y + 10) : (topSection.y + topSection.height + (sidebar.collapsed ? 0 : 8))
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
                WorkspaceNativeIcon {
                    id: projIcon
                    objectName: "projIcon"
                    x: panel.fixedIconX
                    anchors.verticalCenter: parent.verticalCenter
                    iconKey: "project_list"
                    iconSize: 18
                    isLight: sidebar.isLight
                    hovered: navProjHov.hovered
                    strokeColor: (sidebar.model.page === "projects" || navProjHov.hovered)
                        ? (sidebar.isLight ? "#090a0c" : "#ffffff")
                        : sidebar.refinedTone
                    accentColor: sidebar.accentColor
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

                Rectangle {
                    visible: opacity > 0.01
                    opacity: sidebar.textAlpha
                    anchors.right: parent.right; anchors.rightMargin: 10
                    anchors.verticalCenter: parent.verticalCenter
                    width: Math.max(18, projCountLbl.implicitWidth + 8); height: 18; radius: 9
                    color: sidebar.model.page === "projects" ? sidebar.c.a20 : sidebar.c.b08
                    RakLabel {
                        id: projCountLbl
                        anchors.centerIn: parent
                        anchors.verticalCenterOffset: 1
                        text: (sidebar.model.allProjectsCount !== undefined ? sidebar.model.allProjectsCount : (sidebar.model.projects ? sidebar.model.projects.length : 0)).toString()
                        font.pixelSize: 9; font.weight: Font.DemiBold
                        color: sidebar.model.page === "projects" ? sidebar.c.accent_hex : sidebar.c.FG_MUTED
                    }
                }

                HoverHandler { id: navProjHov; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
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
                WorkspaceNativeIcon {
                    id: setIcon
                    objectName: "setIcon"
                    x: panel.fixedIconX
                    anchors.verticalCenter: parent.verticalCenter
                    iconKey: "settings"
                    iconSize: 18
                    isLight: sidebar.isLight
                    hovered: navSetHov.hovered
                    strokeColor: (sidebar.model.page === "settings" || navSetHov.hovered)
                        ? (sidebar.isLight ? "#090a0c" : "#ffffff")
                        : sidebar.refinedTone
                    accentColor: sidebar.accentColor
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



        // ── Drive storage section ─────────────────────────────────────────────
        Column {
            id: driveColumn
            x: panel.itemX
            y: divDrives.y + 8
            width: panel.itemWidth
            spacing: 5

            Repeater {
                model: sidebar.model.driveRows
                delegate: Item {
                    id: driveDelegate
                    required property var modelData
                    required property int index
                    readonly property bool isWsl: Boolean(modelData.distro || (modelData.key && modelData.key.indexOf("WSL") === 0))
                    width: driveColumn.width
                    height: 42

                    SmoothRectangle {
                        anchors.fill: parent; radius: 8
                        color: driveHover.hovered ? sidebar.c.b06 : (modelData.active ? sidebar.c.b03 : "transparent")
                        border.color: modelData.distro ? (modelData.active ? sidebar.c.a30 : sidebar.c.a12) : "transparent"
                    }

                    // ── DRIVE DISK ICON IS TOTALLY PINNED AT fixedIconX (NO MOVEMENT) ──
                    WorkspaceNativeIcon {
                        id: diskIcon
                        x: panel.fixedIconX
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.verticalCenterOffset: sidebar.collapsed ? -4 : 0
                        iconKey: driveDelegate.isWsl ? "linux" : "project_disk"
                        iconSize: 18
                        isLight: sidebar.isLight
                        strokeColor: modelData.distro ? sidebar.c.accent_hex : ((modelData.active || driveHover.hovered) ? (sidebar.isLight ? "#090a0c" : "#ffffff") : sidebar.refinedTone)
                        accentColor: sidebar.accentColor
                        opacity: modelData.active ? 1 : 0.4

                        Behavior on anchors.verticalCenterOffset {
                            NumberAnimation { duration: 180; easing.type: Easing.InOutCubic }
                        }
                    }

                    // ── COLLAPSED DRIVE LETTER & VERTICAL GAUGE (Konsep 3) ──────────────────
                    RakLabel {
                        id: driveLetterCollapsed
                        x: diskIcon.x + Math.round((diskIcon.width - width) / 2)
                        y: diskIcon.y + diskIcon.height + 2
                        width: implicitWidth
                        height: implicitHeight
                        text: driveDelegate.isWsl ? "WSL" : (modelData.key ? modelData.key.replace(":", "") : ((modelData.name || "").charAt(0)))
                        font.pixelSize: 8; font.weight: Font.Bold
                        color: modelData.fraction > 0.90 ? "#ef4444" : (modelData.active ? sidebar.c.FG_BRIGHT : sidebar.refinedTone)
                        opacity: sidebar.ringProgress
                        visible: opacity > 0.01
                    }

                    // Vertical capacity gauge on right of disk icon
                    Item {
                        id: collapsedGaugeArea
                        anchors.right: parent.right; anchors.rightMargin: 6
                        anchors.verticalCenter: parent.verticalCenter
                        width: 3; height: 22
                        opacity: sidebar.ringProgress
                        visible: opacity > 0.01

                        // Background Track
                        SmoothRectangle {
                            anchors.fill: parent; radius: 1.5
                            color: sidebar.c.b10
                        }

                        // Vertical progress fill (anchored to bottom)
                        SmoothRectangle {
                            anchors.bottom: parent.bottom
                            anchors.horizontalCenter: parent.horizontalCenter
                            width: parent.width
                            height: Math.max(2, parent.height * Math.max(0.05, Math.min(1.0, modelData.fraction)) * sidebar.ringFill)
                            radius: 1.5
                            color: modelData.fraction > 0.90 ? "#ef4444" : (modelData.barColor || sidebar.c.accent_hex)
                        }
                    }

                    // ── EXPANDED DRIVE DETAILS (Animated fill 0% -> target% on expand) ───────
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
                                text: driveDelegate.isWsl ? (modelData.distro || (modelData.key ? modelData.key.replace(/^WSL:?/i, "") : "WSL")) : (modelData.name || modelData.key)
                                font.pixelSize: 11; font.weight: Font.DemiBold
                                color: modelData.active ? sidebar.c.FG_BRIGHT : sidebar.c.FG_MUTED
                            }
                            RakLabel {
                                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                text: (modelData.used / 1073741824).toFixed(0) + " / " + (modelData.total / 1073741824).toFixed(0) + " GB"
                                font.pixelSize: 10; color: sidebar.c.FG_MUTED
                            }
                        }

                        // Horizontal capacity bar with status-aware color & barFill dynamic sweep
                        SmoothRectangle {
                            width: parent.width
                            height: 3; radius: 1.5
                            color: sidebar.c.b08
                            SmoothRectangle {
                                width: parent.width * Math.max(0, Math.min(1, modelData.fraction)) * sidebar.barFill
                                height: parent.height; radius: parent.radius
                                color: modelData.fraction > 0.90 ? "#ef4444" : (modelData.barColor || sidebar.c.accent_hex)
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

        // ── Bottom custom sidebar toggle button (no arrows, no text) ─────────
        Item {
            id: toggleBtn
            objectName: "sidebarExpandToggle"
            x: 13
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 12
            width: 38
            height: 34

            SmoothRectangle {
                anchors.fill: parent
                radius: 8
                color: toggleHov.hovered ? sidebar.c.b06 : "transparent"
                border.color: toggleHov.hovered ? sidebar.c.b08 : "transparent"
            }

            WorkspaceNativeIcon {
                anchors.centerIn: parent
                iconKey: "sidebar"
                iconSize: 18
                collapsed: sidebar.collapsed
                isLight: sidebar.isLight
                hovered: toggleHov.hovered
                strokeColor: toggleHov.hovered ? (sidebar.isLight ? "#090a0c" : "#ffffff") : sidebar.refinedTone
                accentColor: sidebar.accentColor
            }

            HoverHandler { id: toggleHov; cursorShape: Qt.PointingHandCursor }
            HoverTip {
                label: sidebar.collapsed ? (sidebar.s.btn_expand || "Perluas sidebar") : (sidebar.s.btn_collapse || "Ciutkan sidebar")
                hovering: toggleHov.hovered
            }
            TapHandler {
                onTapped: sidebar.collapsed = !sidebar.collapsed
            }
        }
    }
}