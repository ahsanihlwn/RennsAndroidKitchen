// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Shapes

Item {
    id: sidePanel
    objectName: "workspaceSidePanel"
    required property var workspace
    required property var preferences
    required property var hostWindow

    readonly property var c: preferences.colors
    readonly property var profile: preferences.profile
    readonly property bool hasProfile: !!(profile && profile.name)
    readonly property bool anims: preferences && preferences.settings ? preferences.settings.animations : true

    property alias historyButton: statusButton
    property string loadingActionKey: ""
    signal historyRequested(var target)

    // Keep model metadata static so toggling Agent or refreshing chat/features
    // never destroys and recreates sidebar button delegates mid-click.
    readonly property var actions: [
        {key: "settings", icon: "settings", tip: "Project settings", scope: "always"},
        {key: "layout", icon: "tree_mode", tip: "Toggle Layout / Split View", scope: "always"},
        {key: "terminal", icon: "terminal", tip: "Terminal", scope: "always"},
        {key: "android", icon: "android_tools", tip: "Android Tools", scope: "always"},
        {key: "bootAnalyzer", icon: "boot_analyzer", tip: "Boot Analyzer", scope: "always"},
        {key: "import", icon: "import", tip: "Import files", scope: "always"},
        {key: "agentInbox", icon: "agent", tip: "Agent chat", scope: "agent"},
        {key: "automation", icon: "automation", tip: "Automation", scope: "private"},
        {key: "compare", icon: "compare", tip: "Compare FileSystem", scope: "private"}
    ]
    readonly property int visibleActionCount: 6
        + ((workspace.agentChat && workspace.agentChat.available) ? 1 : 0)
        + ((workspace.privateFeatures && workspace.privateFeatures.allowed) ? 2 : 0)

    implicitWidth: 48
    width: 48

    // Floating card background with rounded corners and border
    SmoothRectangle {
        anchors.fill: parent
        radius: 12
        color: sidePanel.c.BG2
        border.color: sidePanel.c.b08
        border.width: 1
    }

    Column {
        anchors.fill: parent
        anchors.topMargin: 8
        anchors.bottomMargin: 10
        spacing: 6

        // TOP: Back to projects (Home)
        WorkspaceIconButton {
            id: backButton
            objectName: "workspaceSideAction_back"
            preferences: sidePanel.preferences
            iconKey: "back_to_proj"
            toolTip: "Back to projects"
            implicitWidth: 34
            implicitHeight: 34
            iconWidth: 18
            iconHeight: 18
            cornerRadius: 8
            anchors.horizontalCenter: parent.horizontalCenter
            fillColor: visualHovered ? sidePanel.c.b08 : "transparent"
            onClicked: { resetPressFeedback(); sidePanel.workspace.dispatch("back", {}) }
        }

        // Subtle divider separating home navigation from tool actions
        SmoothRectangle {
            width: 24
            height: 1
            color: sidePanel.c.b08
            anchors.horizontalCenter: parent.horizontalCenter
        }

        // TOOLBAR ACTIONS
        Repeater {
            id: actionRepeater
            model: sidePanel.actions
            delegate: WorkspaceIconButton {
                id: sideActionBtn
                required property var modelData
                readonly property bool isAgentAction: modelData.scope === "agent"
                readonly property bool isPrivateAction: modelData.scope === "private"
                readonly property bool agentActive: isAgentAction && !!(sidePanel.workspace.agentChat && (sidePanel.workspace.agentChat.running || sidePanel.workspace.agentChat.busy))
                objectName: "workspaceSideAction_" + modelData.key
                visible: isAgentAction
                    ? !!(sidePanel.workspace.agentChat && sidePanel.workspace.agentChat.available)
                    : (isPrivateAction ? !!(sidePanel.workspace.privateFeatures && sidePanel.workspace.privateFeatures.allowed) : true)
                preferences: sidePanel.preferences
                iconKey: modelData.icon
                toolTip: (modelData.key === "layout" && sidePanel.workspace)
                    ? sidePanel.workspace.layoutLabel
                    : (isAgentAction && sidePanel.workspace && sidePanel.workspace.agentChat && sidePanel.workspace.agentChat.opened ? "Minimize agent" : modelData.tip)
                implicitWidth: 34
                implicitHeight: 34
                iconWidth: 18
                iconHeight: 18
                cornerRadius: 8
                anchors.horizontalCenter: parent.horizontalCenter
                layoutMode: (sidePanel.workspace && sidePanel.workspace.layoutMode !== undefined) ? sidePanel.workspace.layoutMode : 2
                enabled: modelData.key !== "layout" || sidePanel.workspace.prepared
                loading: sidePanel.loadingActionKey === modelData.key
                aiActive: sideActionBtn.agentActive
                aiColored: sideActionBtn.agentActive
                aiGlow: sideActionBtn.agentActive
                fillColor: sideActionBtn.agentActive
                    ? Qt.rgba(0.573, 0.475, 0.933, sidePanel.c.is_light ? 0.12 : 0.16)
                    : ((isAgentAction && sidePanel.workspace.agentChat && sidePanel.workspace.agentChat.opened) ? sidePanel.c.b12 : "transparent")
                edgeColor: sideActionBtn.agentActive ? Qt.rgba(0.573, 0.475, 0.933, 0.34) : "transparent"

                // Subtle AI rainbow glow matching file-touch highlight when Agent is operating
                AgentRainbowShadow {
                    objectName: sideActionBtn.isAgentAction ? "workspaceAgentButtonRainbow" : ""
                    anchors.fill: parent
                    z: -1
                    radius: sideActionBtn.cornerRadius
                    shadowSpread: 7
                    shadowOpacity: 0.72
                    active: sideActionBtn.agentActive
                    animated: sidePanel.anims
                }

                // Unread notification pip for agent inbox
                SmoothRectangle {
                    visible: modelData.key === "agentInbox" && sidePanel.workspace.agentInbox && sidePanel.workspace.agentInbox.notificationCount > 0
                    x: parent.width - 8
                    y: 4
                    width: 6
                    height: 6
                    radius: 3
                    color: sidePanel.c.accent_hex
                }

                onClicked: {
                    resetPressFeedback()
                    sidePanel.workspace.dispatch(modelData.key, {})
                }
            }
        }

        // FLEXIBLE SPACER (pushes loading/status indicator and profile to bottom)
        Item {
            width: parent.width
            height: Math.max(8, sidePanel.height - (50 + sidePanel.visibleActionCount * 40 + (sidePanel.hasProfile ? 48 : 0) + 46))
        }

        // =====================================================================
        // LOADING / STATUS / TASK HISTORY INDICATOR (Right above profile)
        // =====================================================================
        RakButton {
            id: statusButton
            objectName: "workspaceSideStatusButton"
            focusPolicy: Qt.NoFocus
            width: 34
            height: 34
            implicitWidth: 34
            implicitHeight: 34
            leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
            cornerRadius: 8; edgeWidth: 1
            fillColor: isActive ? sidePanel.c.b06 : "transparent"
            hoverFillColor: sidePanel.c.b08
            downFillColor: sidePanel.c.b12
            edgeColor: "transparent"
            hoverEdgeColor: sidePanel.c.b10
            animatePress: sidePanel.anims
            anchors.horizontalCenter: parent.horizontalCenter
            toolTip: (sidePanel.workspace.statusProgress >= 0)
                ? (sidePanel.workspace.status + "\n" + sidePanel.workspace.statusProgress + "%" + (sidePanel.workspace.statusStage ? " · " + sidePanel.workspace.statusStage : ""))
                : (sidePanel.workspace.status && sidePanel.workspace.status.length > 0)
                ? sidePanel.workspace.status
                : (statusButton.isActive ? "Loading..." : "Task History / Activity")
            onClicked: { resetPressFeedback(); sidePanel.historyRequested(statusButton) }

            readonly property string statusState: sidePanel.workspace.statusState || "idle"
            readonly property bool isActive: statusState === "active"
            readonly property bool isDone: statusState === "done"
            readonly property bool isError: statusState === "error"
            readonly property bool isIdle: !isActive && !isDone && !isError

            // Orchestrated scale up/down & crossfade progresses with bounce/overshoot
            property real activeProgress: isActive ? 1.0 : 0.0
            property real doneProgress: isDone ? 1.0 : 0.0
            property real errorProgress: isError ? 1.0 : 0.0
            property real idleProgress: isIdle ? 1.0 : 0.0

            Behavior on activeProgress {
                NumberAnimation {
                    duration: sidePanel.anims ? 260 : 0
                    easing.type: statusButton.isActive ? Easing.OutBack : Easing.InQuad
                    easing.overshoot: 1.4
                }
            }
            Behavior on doneProgress {
                NumberAnimation {
                    duration: sidePanel.anims ? 280 : 0
                    easing.type: statusButton.isDone ? Easing.OutBack : Easing.InQuad
                    easing.overshoot: 1.6
                }
            }
            Behavior on errorProgress {
                NumberAnimation {
                    duration: sidePanel.anims ? 280 : 0
                    easing.type: statusButton.isError ? Easing.OutBack : Easing.InQuad
                    easing.overshoot: 1.5
                }
            }
            Behavior on idleProgress {
                NumberAnimation {
                    duration: sidePanel.anims ? 260 : 0
                    easing.type: statusButton.isIdle ? Easing.OutBack : Easing.InQuad
                    easing.overshoot: 1.3
                }
            }

            contentItem: Item {

            // 1. Loading Progress (Active Spinner): scale up 0.25 -> 1.05 -> 1.0, scale down to 0.25
            Item {
                anchors.centerIn: parent
                width: 18; height: 18
                opacity: Math.max(0, Math.min(1, statusButton.activeProgress))
                scale: 0.25 + 0.75 * statusButton.activeProgress
                visible: statusButton.activeProgress > 0.01
                Spinner {
                    anchors.fill: parent
                    accent: sidePanel.c.accent_hex
                    thickness: 2.2
                    inset: 2.2
                    spinning: statusButton.activeProgress > 0.05
                    visible: sidePanel.workspace.statusProgress < 0
                }
                Shape {
                    id: progressRing
                    anchors.centerIn: parent
                    width: 24; height: 24
                    visible: sidePanel.workspace.statusProgress >= 0
                    preferredRendererType: Shape.CurveRenderer
                    antialiasing: true
                    readonly property color accentCol: sidePanel.c.accent_hex
                    ShapePath {
                        strokeColor: Qt.rgba(progressRing.accentCol.r, progressRing.accentCol.g, progressRing.accentCol.b, 0.22)
                        strokeWidth: 2.3
                        capStyle: ShapePath.RoundCap
                        fillColor: "transparent"
                        PathAngleArc { centerX: 12; centerY: 12; radiusX: 9.8; radiusY: 9.8; startAngle: 0; sweepAngle: 360 }
                    }
                    ShapePath {
                        strokeColor: progressRing.accentCol
                        strokeWidth: 2.3
                        capStyle: ShapePath.RoundCap
                        fillColor: "transparent"
                        PathAngleArc {
                            centerX: 12; centerY: 12; radiusX: 9.8; radiusY: 9.8
                            startAngle: -90
                            sweepAngle: 360 * Math.max(0, Math.min(100, sidePanel.workspace.statusProgress)) / 100
                            Behavior on sweepAngle { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
                        }
                    }
                }
                RakLabel {
                    anchors.centerIn: parent
                    visible: sidePanel.workspace.statusProgress >= 0
                    transformed: true
                    text: sidePanel.workspace.statusProgress
                    font.pixelSize: 8
                    font.weight: Font.DemiBold
                    color: sidePanel.c.FG_BRIGHT
                    verticalAlignment: Text.AlignVCenter
                    horizontalAlignment: Text.AlignHCenter
                }
            }

            // 2. Success Result (Checkmark ✓): bouncy pop-in scale up 0.25 -> 1.15 -> 1.0
            Item {
                anchors.centerIn: parent
                width: 16; height: 16
                opacity: Math.max(0, Math.min(1, statusButton.doneProgress))
                scale: 0.25 + 0.75 * statusButton.doneProgress
                visible: statusButton.doneProgress > 0.01
                Shape {
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        strokeColor: sidePanel.c.GREEN_STATUS || "#22c55e"
                        strokeWidth: 2.2
                        fillColor: "transparent"
                        capStyle: ShapePath.RoundCap
                        joinStyle: ShapePath.RoundJoin
                        PathSvg { path: "M 3.2 8.5 L 6.5 11.8 L 12.8 4.8" }
                    }
                }
            }

            // 3. Error Result (Cross ×): bouncy pop-in scale up 0.25 -> 1.15 -> 1.0
            Item {
                anchors.centerIn: parent
                width: 16; height: 16
                opacity: Math.max(0, Math.min(1, statusButton.errorProgress))
                scale: 0.25 + 0.75 * statusButton.errorProgress
                visible: statusButton.errorProgress > 0.01
                Shape {
                    anchors.fill: parent
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath {
                        strokeColor: sidePanel.c.RED_STATUS || "#ef4444"
                        strokeWidth: 2.2
                        fillColor: "transparent"
                        capStyle: ShapePath.RoundCap
                        joinStyle: ShapePath.RoundJoin
                        PathSvg { path: "M 4.5 4.5 L 11.5 11.5 M 11.5 4.5 L 4.5 11.5" }
                    }
                }
            }

            // 4. Idle / Default (Three Dots •••): scale up 0.30 -> 1.0, scale down to 0.30
            Item {
                anchors.centerIn: parent
                width: 18; height: 18
                opacity: Math.max(0, Math.min(1, statusButton.idleProgress))
                scale: 0.30 + 0.70 * statusButton.idleProgress
                visible: statusButton.idleProgress > 0.01
                Row {
                    anchors.centerIn: parent
                    spacing: 2.8
                    Repeater {
                        model: 3
                        Rectangle {
                            width: 3.5
                            height: 3.5
                            radius: 1.75
                            color: statusButton.visualHovered ? sidePanel.c.FG_BRIGHT : sidePanel.c.FG_MUTED
                        }
                    }
                }
            }
            }

            // MorphPopup Return Caption
            property Component returnCaption: Component {
                Item {
                    width: 34; height: 34
                    Row {
                        anchors.centerIn: parent
                        spacing: 2.8
                        Repeater {
                            model: 3
                            Rectangle {
                                width: 3.5; height: 3.5; radius: 1.75
                                color: sidePanel.c.FG_DIM
                            }
                        }
                    }
                }
            }



            Item {
                id: statusLoadingTrack
                z: 5
                visible: sidePanel.loadingActionKey === "history"
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 2
                width: 20
                height: 3

                SmoothRectangle {
                    id: statusLoadingThumb
                    readonly property bool determinate: sidePanel.workspace.statusProgress >= 0
                    width: determinate ? Math.max(height, statusLoadingTrack.width * sidePanel.workspace.statusProgress / 100) : 9
                    height: parent.height
                    radius: height / 2
                    color: sidePanel.c.accent_hex
                    x: 0
                    Behavior on width { enabled: statusLoadingThumb.determinate; NumberAnimation { duration: 180 } }

                    SequentialAnimation on x {
                        loops: Animation.Infinite
                        running: statusLoadingTrack.visible && !statusLoadingThumb.determinate
                        NumberAnimation {
                            from: 0
                            to: Math.max(0, statusLoadingTrack.width - statusLoadingThumb.width)
                            duration: 460
                            easing.type: Easing.InOutCubic
                        }
                        NumberAnimation {
                            from: Math.max(0, statusLoadingTrack.width - statusLoadingThumb.width)
                            to: 0
                            duration: 460
                            easing.type: Easing.InOutCubic
                        }
                    }
                }
            }
        }

        // =====================================================================
        // BOTTOM: PROFILE AVATAR WITH TUCKED-IN CHECKMARK BADGE
        // =====================================================================
        RakButton {
            id: profileBox
            objectName: "workspaceSideProfile"
            focusPolicy: Qt.NoFocus
            visible: sidePanel.hasProfile
            width: 34
            height: 34
            implicitWidth: 34
            implicitHeight: 34
            leftPadding: 0; rightPadding: 0; topPadding: 0; bottomPadding: 0
            cornerRadius: 17; edgeWidth: 0
            fillColor: "transparent"
            hoverFillColor: sidePanel.c.b08
            downFillColor: sidePanel.c.b12
            animatePress: sidePanel.anims
            anchors.horizontalCenter: parent.horizontalCenter
            toolTip: ((sidePanel.profile && sidePanel.profile.name) || "Profile") + ((sidePanel.profile && sidePanel.profile.subtitle) ? ("\n" + sidePanel.profile.subtitle) : "")
            onClicked: { resetPressFeedback(); sidePanel.workspace.dispatch("settings", {}) }

            // Avatar Image / Fallback Circle
            contentItem: Item {
                id: avatarItem

                RoundedImage {
                    anchors.fill: parent
                    source: (sidePanel.profile && sidePanel.profile.avatar) || ""
                    radius: 17
                }

                // Fallback user icon if avatar image is empty
                WorkspaceNativeIcon {
                    visible: !(sidePanel.profile && sidePanel.profile.avatar)
                    anchors.centerIn: parent
                    iconKey: "user"
                    iconSize: 18
                    strokeColor: sidePanel.c.FG_MUTED
                }
            }

            // Verified badge pinned to bottom-right corner of avatar ("ceklis nyempil")
            Item {
                id: avatarCornerBadge
                z: 10
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.rightMargin: -2
                anchors.bottomMargin: -2
                width: 14
                height: 14
                visible: !!(sidePanel.profile && sidePanel.profile.badge)

                SmoothRectangle {
                    anchors.fill: parent
                    anchors.margins: -1.5
                    radius: width / 2
                    color: sidePanel.c.BG2
                }

                Image {
                    anchors.fill: parent
                    source: visible ? sidePanel.preferences.icon(sidePanel.profile.badge, "") : ""
                    sourceSize: Qt.size(14, 14)
                }

                HoverHandler { id: cornerBadgeHov }
                HoverTip {
                    label: "Verified (" + ((sidePanel.profile && sidePanel.profile.verifiedLevel) || "") + ")"
                    hovering: cornerBadgeHov.hovered
                }
            }

        }
    }
}
