// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import "../motion.js" as Motion

Item {
    id: toolbar
    objectName: "workspaceToolbar"
    required property var workspace
    required property var preferences
    required property var hostWindow
    readonly property var c: preferences.colors
    readonly property color tintBase: c.BG2
    readonly property color baseColor: c.BG
    readonly property color raisedColor: c.BG4
    readonly property color groupFill: Qt.rgba(baseColor.r*.45+raisedColor.r*.55,baseColor.g*.45+raisedColor.g*.55,baseColor.b*.45+raisedColor.b*.55,1)
    readonly property var profile: preferences.profile
    readonly property bool hasProfile: !!profile.name
    readonly property real profileWidth: hasProfile?Math.max(name.implicitWidth + (toolbar.profile.badge ? 18 : 0), subtitle.implicitWidth)+54:0
    readonly property real groupX: hasProfile?profileWidth+29:19
    property bool compact: false
    property real modeAmount: 0
    property bool modeReady: false
    readonly property bool modeAnimating: modeAnimation.running
    readonly property real inlineWidth: actions.length*28+4
    readonly property real inlineOpacity: 1-Motion.clamp(modeAmount/.50)
    readonly property real compactOpacity: Motion.clamp((modeAmount-.30)/.70)
    property var actions: [
        {key:"back",icon:"back_to_proj",tip:"Back to projects"},
        {key:"settings",icon:"settings",tip:"Project settings"},
        {key:"layout",icon:"tree_mode",tip:"Toggle Layout / Split View"},
        {key:"terminal",icon:"terminal",tip:"Terminal"},
        {key:"android",icon:"android_tools",tip:"Android Tools"},
        {key:"bootAnalyzer",icon:"boot_analyzer",tip:"Boot Analyzer"},
        {key:"import",icon:"import",tip:"Import files"}
    ].concat(workspace.agentChat && workspace.agentChat.available?[{key:"agentInbox",icon:"agent",tip:workspace.agentChat.opened?"Minimize agent":"Agent chat"}]:[]).concat(workspace.privateFeatures && workspace.privateFeatures.allowed?[
        {key:"automation",icon:"automation",tip:"Automation"},
        {key:"compare",icon:"compare",tip:"Compare FileSystem"}]:[])
    implicitHeight: 60
    clip: true
    function settleMode() {
        modeAnimation.stop()
        modeAmount=compact?1:0
    }
    function morphMode() {
        if(!modeReady || !preferences.settings.animations){settleMode();return}
        const target=compact?1:0
        modeAnimation.stop()
        if(Math.abs(modeAmount-target)<.00001){modeAmount=target;return}
        compactButton.resetPressFeedback()
        for(let i=0;i<inlineActions.count;++i){const button=inlineActions.itemAt(i);if(button)button.resetPressFeedback()}
        // Retarget from the displayed geometry when a splitter reverses. No
        // captured/scaled toolbar: the native icons retain their own 28px slots.
        modeAnimation.to=target
        modeAnimation.duration=Math.max(80,440*Math.abs(target-modeAmount))
        modeAnimation.start()
    }
    function checkOverflow() {
        if (!workspace.rightOpen) { compact=false; return }
        const gap=width-(groupX+inlineWidth)
        if (!compact && gap<56)compact=true
        else if(compact && gap>100)compact=false
    }
    onWidthChanged: { checkOverflow(); if(popup.visible)popup.dismissImmediate() }
    onCompactChanged: { if(popup.visible)popup.dismissImmediate(); morphMode() }
    onActionsChanged: checkOverflow()
    onGroupXChanged: checkOverflow()
    Component.onCompleted: {checkOverflow();settleMode();modeReady=true}
    NumberAnimation { id: modeAnimation; target: toolbar; property: "modeAmount"; easing.type: Easing.OutCubic }
    Connections { target: workspace; function onChanged() {toolbar.checkOverflow()} }
    Connections { target: toolbar.preferences; function onSettingsChanged(){if(!toolbar.preferences.settings.animations)toolbar.settleMode()} }
    Item {
        visible: toolbar.hasProfile; x: 8; y: 8; width: toolbar.profileWidth; height: 46
        RoundedImage { x: 10; y: 9; width: 28; height: 28; source: toolbar.profile.avatar || ""; radius: 14 }
        RakLabel { id: name; x: 44; y: 8; text: toolbar.profile.name || ""; font.pixelSize: 12; font.weight: Font.DemiBold; color: c.FG_BRIGHT }
        Image {
            id: profileBadge
            x: name.x + name.implicitWidth + 4
            y: 9.5
            width: 14; height: 14
            visible: !!toolbar.profile.badge
            source: visible ? toolbar.preferences.icon(toolbar.profile.badge, "") : ""
            sourceSize: Qt.size(14, 14)

            HoverHandler { id: badgeHover }
            HoverTip {
                label: "Verified (" + (toolbar.profile.verifiedLevel || "") + ")"
                hovering: badgeHover.hovered
            }
        }
        RakLabel { id: subtitle; x: 44; y: 24; text: toolbar.profile.subtitle || ""; font.pixelSize: 10; color: c.FG_DIM }
    }
    SmoothRectangle { visible: toolbar.hasProfile; x: toolbar.profileWidth+18; y: 17; width: 1; height: 28; color: c.b12 }
    SmoothRectangle {
        objectName: "workspaceToolbarModeSurface"
        visible: toolbar.modeAmount<1 || toolbar.modeAnimating
        x: toolbar.groupX; y: 15+2*toolbar.modeAmount
        width: Motion.mix(toolbar.inlineWidth,28,toolbar.modeAmount); height: 32-4*toolbar.modeAmount
        radius: 8; color: toolbar.groupFill; border.width: 1; border.color: c.b06
        Repeater {
            id: inlineActions
            model: toolbar.actions
            delegate: WorkspaceIconButton {
                required property var modelData
                required property int index
                objectName: "workspaceAction_"+modelData.key
                x: (2+index*28)*(1-toolbar.modeAmount); y: 2*(1-toolbar.modeAmount)
                opacity: toolbar.inlineOpacity
                visible: opacity>0
                preferences: toolbar.preferences; iconKey: modelData.icon
                toolTip: (modelData.key === "layout" && toolbar.workspace) ? toolbar.workspace.layoutLabel : modelData.tip
                layoutMode: (toolbar.workspace && toolbar.workspace.layoutMode !== undefined) ? toolbar.workspace.layoutMode : 2
                fillColor: modelData.key==="agentInbox" && toolbar.workspace.agentChat.opened ? toolbar.preferences.colors.b08 : "transparent"
                SmoothRectangle {visible: modelData.key==="agentInbox" && toolbar.workspace.agentInbox.notificationCount>0;x: parent.width-7;y: 3;width: 5;height: 5;radius: 2.5;color: toolbar.c.accent_hex}
                enabled: !toolbar.compact && !toolbar.modeAnimating && (modelData.key!=="layout" || toolbar.workspace.prepared)
                onClicked: toolbar.workspace.dispatch(modelData.key,{})
            }
        }
    }
    WorkspaceIconButton {
        id: compactButton
        objectName: "workspaceToolbarExpand"
        visible: toolbar.modeAmount>0; x: toolbar.groupX; y: 17
        opacity: morphHidden?0:toolbar.compactOpacity
        enabled: toolbar.compact && !toolbar.modeAnimating
        preferences: toolbar.preferences; iconKey: "expand"; cornerRadius: 8
        fillColor: toolbar.modeAnimating?"transparent":toolbar.groupFill
        edgeWidth: toolbar.modeAnimating?0:1; edgeColor: c.b06; hoverEdgeColor: c.b12
        property Component returnCaption: Component {
            Item {
                WorkspaceNativeIcon {
                    anchors.centerIn: parent
                    iconKey: compactButton.iconKey
                    iconSize: compactButton.iconWidth
                    isLight: compactButton.isLight
                    hovered: compactButton.visualHovered
                    down: compactButton.down
                    enabled: compactButton.enabled
                    paletteName: (toolbar.preferences && toolbar.preferences.paletteName) ? toolbar.preferences.paletteName : "default"
                    strokeColor: compactButton.labelColor
                    accentColor: compactButton.accentColor
                }
            }
        }
        onClicked: { resetPressFeedback(); popup.open() }
    }
    Connections {
        target: toolbar.hostWindow
        function onWidthChanged(){popup.dismissImmediate()}
        function onHeightChanged(){popup.dismissImmediate()}
        function onXChanged(){popup.dismissImmediate()}
        function onYChanged(){popup.dismissImmediate()}
    }
    MorphPopup {
        id: popup
        objectName: "workspaceToolbarPopup"
        parent: toolbar.hostWindow.contentItem
        z: 10000
        sourceItem: compactButton
        springMotion: false
        morphOnClose: true
        animationsEnabled: toolbar.preferences.settings.animations
        backdropSource: toolbar.hostWindow.backdropSurface
        effectsEnabled: toolbar.preferences.settings.effects!=="low"
        panelColor: c.BG_MODAL; tintBaseColor: toolbar.tintBase; panelBorder: toolbar.preferences.panelBorder
        sourceBackdropColor: c.BG; panelRadius: 12
        x: Math.max(8,Math.min(parent.width-width-8,toolbar.mapToItem(parent,compactButton.x+14,0).x-width/2))
        y: toolbar.mapToItem(parent,0,compactButton.y).y
        width: expandedRow.implicitWidth+12; height: 40; modal: true; dim: false; focus: true
        property string picked: ""
        function pick(key){if(opening){picked=key;close()}}
        closePolicy: C.Popup.CloseOnEscape|C.Popup.CloseOnPressOutside
        onAboutToShow: {
            picked=""
            if(toolbar.hostWindow.dialogStack)toolbar.hostWindow.dialogStack.registerPopup(popup)
        }
        onAboutToHide: {
            collapseButton.resetPressFeedback()
            for(let i=0;i<expandedActions.count;++i)expandedActions.itemAt(i).resetPressFeedback()
        }
        onClosed: {
            if(toolbar.hostWindow.dialogStack)toolbar.hostWindow.dialogStack.unregisterPopup(popup)
            if(picked.length){const key=picked;picked="";toolbar.workspace.dispatch(key,{})}
        }
        Row {
            id: expandedRow
            x: 6; y: 4; height: 32; spacing: 4
            WorkspaceIconButton {
                id: collapseButton
                objectName: "workspaceToolbarCollapse"
                anchors.verticalCenter: parent.verticalCenter
                preferences: toolbar.preferences; iconKey: "expand"; cornerRadius: 8
                fillColor: toolbar.groupFill; edgeWidth: 1; edgeColor: c.b06; hoverEdgeColor: c.b12
                onClicked: popup.close()
            }
            SmoothRectangle { width: 1; height: 18; color: c.b08; anchors.verticalCenter: parent.verticalCenter }
            SmoothRectangle {
                width: expandedActionRow.width+4; height: 32; radius: 8
                color: toolbar.groupFill; border.width: 1; border.color: c.b06
                Row {
                    id: expandedActionRow; x: 2; y: 2
                    Repeater {
                        id: expandedActions
                        model: toolbar.actions
                        delegate: WorkspaceIconButton {
                            required property var modelData
                            objectName: "workspaceExpandedAction_"+modelData.key
                            preferences: toolbar.preferences; iconKey: modelData.icon
                            toolTip: (modelData.key === "layout" && toolbar.workspace) ? toolbar.workspace.layoutLabel : modelData.tip
                            layoutMode: (toolbar.workspace && toolbar.workspace.layoutMode !== undefined) ? toolbar.workspace.layoutMode : 2
                            fillColor: modelData.key==="agentInbox" && toolbar.workspace.agentChat.opened ? toolbar.preferences.colors.b08 : "transparent"
                            SmoothRectangle {visible: modelData.key==="agentInbox" && toolbar.workspace.agentInbox.notificationCount>0;x: parent.width-7;y: 3;width: 5;height: 5;radius: 2.5;color: toolbar.c.accent_hex}
                            enabled: popup.opening && (modelData.key!=="layout" || toolbar.workspace.prepared)
                            onClicked: popup.pick(modelData.key)
                        }
                    }
                }
            }
        }
    }
}
