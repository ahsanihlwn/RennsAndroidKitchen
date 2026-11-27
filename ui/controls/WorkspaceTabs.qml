// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Effects
import QtQuick.Window

SmoothRectangle {
    id: bar
    required property var preferences
    required property var rows
    required property string activeKey
    property var workspace: null
    property bool terminal: false
    readonly property real contentWidth: tabs.width
    signal selected(string key)
    signal closed(string key)
    readonly property var c: preferences.colors
    property real rightCornerRadius: 0
    implicitHeight: 34; color: c.BG_DARK; radius: 7
    topRightRadius: rightCornerRadius; bottomLeftRadius: 0; bottomRightRadius: 0
    SmoothRectangle {
        id: cornerMask
        objectName: "workspaceTabCornerMask_"+bar.objectName
        width: bar.width; height: bar.height; color: "white"; visible: false
        radius: bar.radius; topRightRadius: bar.rightCornerRadius; bottomLeftRadius: 0; bottomRightRadius: 0
    }
    ShaderEffectSource {
        id: cornerTexture; sourceItem: cornerMask; hideSource: true; visible: false; live: true; smooth: true
        textureSize: Qt.size(Math.ceil(bar.width*2*Screen.devicePixelRatio),Math.ceil(bar.height*2*Screen.devicePixelRatio))
    }
    Flickable {
        id: scroll
        objectName: "workspaceTabViewport_"+bar.objectName
        anchors.fill: parent
        clip: true; contentWidth: tabs.width; contentHeight: height; boundsBehavior: Flickable.StopAtBounds
        // Clip the viewport edge, not individual tabs. Whichever tab reaches
        // the card's left corner follows it, including during horizontal scroll.
        layer.enabled: true; layer.smooth: true
        layer.effect: MultiEffect { maskEnabled: true; maskSource: cornerTexture; maskThresholdMin: .5; maskSpreadAtMin: 1 }
        Row {
            id: tabs
            move: Transition {
                NumberAnimation { properties: "x"; duration: bar.preferences.settings.animations?160:0; easing.type: Easing.OutQuint }
            }
            Repeater {
                model: bar.rows
                delegate: Item {
                    id: tab
                    objectName: "workspaceTab_"+key
                    required property var modelData
                    readonly property string key: modelData.key
                    readonly property bool active: bar.activeKey===key
                    width: Math.min(240,Math.max(120,title.implicitWidth+72)); height: 34
                    transformOrigin: Item.TopLeft
                    Component.onCompleted: {
                        if(bar.preferences.settings.animations && bar.Window.window && bar.Window.window.visible)entry.start()
                    }
                    ParallelAnimation {
                        id: entry
                        NumberAnimation { target: tab; property: "opacity"; from: 0; to: 1; duration: bar.preferences.settings.animations?170:0; easing.type: Easing.OutQuint }
                        NumberAnimation { target: tab; property: "scale"; from: .96; to: 1; duration: bar.preferences.settings.animations?170:0; easing.type: Easing.OutQuint }
                    }
                    Connections {
                        target: bar.preferences
                        function onSettingsChanged() {
                            if(!bar.preferences.settings.animations){entry.stop();tab.opacity=1;tab.scale=1}
                        }
                    }
                    readonly property bool agentWriting: {
                        if (!bar.workspace || !bar.workspace.agentActivity) return false
                        const agentSettings = bar.workspace.projectSettings ? bar.workspace.projectSettings.agent : null
                        if (!agentSettings || !agentSettings.ui_triggers) return false
                        if (!tab.active || bar.terminal) return false
                        const currentKey = bar.workspace.agentActivity.key(tab.key || "")
                        if (currentKey && bar.workspace.agentActivity.paths[currentKey]) return true
                        const paths = bar.workspace.agentActivity.paths
                        for (let k in paths) {
                            const row = paths[k]
                            if (row && (row.kind === "write" || row.state === "held")) return true
                        }
                        return false
                    }
                    HoverHandler { id: hover }
                    SmoothRectangle {
                        objectName: "workspaceTabFace_"+tab.key
                        anchors.fill: parent; color: tab.active?c.BG3:hover.hovered?c.b04:"transparent"
                    }
                    AgentRainbow {
                        objectName: "workspaceTabRainbow_"+tab.key
                        anchors.fill: parent
                        active: tab.agentWriting
                        animated: bar.preferences.settings.animations
                        fillOpacity: 0.18
                    }
                    Item {
                        width: parent.width; height: 2
                        visible: tab.active
                        SmoothRectangle {
                            anchors.fill: parent
                            color: c.accent_hex
                            visible: !tab.agentWriting
                        }
                        AgentRainbow {
                            anchors.fill: parent
                            active: tab.agentWriting
                            animated: bar.preferences.settings.animations
                            fillOpacity: 1.0
                        }
                    }
                    SmoothRectangle { anchors.right: parent.right; height: parent.height; width: 1; color: c.b04 }
                    readonly property bool nativeTabIcon: tab.modelData.icon === "terminal" || tab.modelData.icon === "start"
                    property int lockRevision: 0
                    Connections {
                        target: bar.workspace || null
                        function onLocksChanged() { tab.lockRevision++ }
                    }
                    readonly property bool tabLocked: {
                        tab.lockRevision
                        if (bar.terminal) return false
                        if (!!tab.modelData.readOnly) return true
                        return !!(bar.workspace && typeof bar.workspace.isFileReadOnly === "function" && bar.workspace.isFileReadOnly(tab.modelData.path || ""))
                    }
                    Image {
                        visible: !tab.nativeTabIcon
                        x: 12; y: bar.terminal?11:10; width: bar.terminal?14:16; height: width
                        source: visible ? ("image://icons/"+(tab.modelData.icon || "file")) : ""
                        sourceSize: Qt.size(width,width); smooth: true
                    }
                    WorkspaceNativeIcon {
                        visible: tab.nativeTabIcon
                        x: 12; y: bar.terminal?11:10; width: bar.terminal?14:16; height: width; iconSize: width
                        iconKey: tab.nativeTabIcon ? tab.modelData.icon : ""
                        strokeColor: tab.active ? c.FG_BRIGHT : (hover.hovered ? c.FG2 : c.GRAY_MID2)
                        accentColor: c.accent_hex || "#38bdf8"
                        isLight: !!(c && c.is_light)
                    }
                    Item {
                        objectName: "tabEntryLockBadge"
                        visible: tab.tabLocked
                        x: 20; y: 18; width: 10; height: 10; z: 2
                        SmoothRectangle {
                            anchors.fill: parent
                            radius: 3
                            color: tab.active ? c.BG3 : c.BG_DARK
                            border.width: 1
                            border.color: c.b18
                        }
                        WorkspaceNativeIcon {
                            anchors.centerIn: parent
                            iconKey: "lock"
                            iconSize: 7
                            strokeColor: tab.active ? c.FG_BRIGHT : c.FG2
                            isLight: !!(c && c.is_light)
                        }
                    }
                    SmoothRectangle { visible: !!tab.modelData.unsaved; x: 12; y: 20; width: 8; height: 8; radius: 4; color: c.accent_hex }
                    RakLabel {
                        id: title; x: bar.terminal?32:34; height: parent.height; width: parent.width-x-28
                        text: tab.modelData.title; font.pixelSize: 12; color: tab.active?c.FG_BRIGHT:hover.hovered?c.FG2:c.GRAY_MID2
                        verticalAlignment: Text.AlignVCenter; elide: Text.ElideMiddle
                    }
                    MouseArea {
                        anchors.fill: parent; acceptedButtons: Qt.LeftButton|Qt.MiddleButton
                        onClicked: mouse => mouse.button===Qt.MiddleButton?bar.closed(tab.key):bar.selected(tab.key)
                    }
                    WorkspaceIconButton {
                        objectName: "closeWorkspaceTab_"+tab.key
                        anchors.right: parent.right; anchors.rightMargin: 6; anchors.verticalCenter: parent.verticalCenter
                        width: 18; height: 18; implicitWidth: 18; implicitHeight: 18
                        preferences: bar.preferences; iconKey: "close_rounded"; iconWidth: 13; iconHeight: 13
                        cornerRadius: 5; edgeWidth: 0; hoverEdgeColor: "transparent"
                        tint: c.GRAY_MID; hoverLabelColor: c.RED_STRONG
                        hoverFillColor: c.red18; downFillColor: c.red32
                        onClicked: bar.closed(tab.key)
                    }
                    onActiveChanged: if (active) ensureVisible.restart()
                    Timer {
                        id: ensureVisible; interval: 0
                        onTriggered: {
                            if(tab.x<scroll.contentX)scroll.contentX=tab.x
                            else if(tab.x+tab.width>scroll.contentX+scroll.width)scroll.contentX=tab.x+tab.width-scroll.width
                        }
                    }
                }
            }
        }
        WheelHandler { orientation: Qt.Horizontal; property: "contentX"; rotationScale: -24 }
    }
    SmoothRectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: c.b06 }
}
