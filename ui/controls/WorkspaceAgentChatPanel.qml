// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Dialogs as D
import QtQuick.Effects

Item {
    id: panel
    objectName: "workspaceAgentChatPanel"
    required property var workspace
    required property var preferences
    readonly property var chat: workspace.agentChat
    readonly property var c: preferences.colors
    readonly property var tr: chat.labels(preferences.strings.languageKey)
    readonly property var request: chat.pendingRequest
    readonly property var approvalChoices: ["save","yes","no"].map(kind=>((panel.request.options || []).find(o=>o.kind===kind) || {id:"",kind:kind}))
    readonly property string providerName: (chat.providers.find(p=>p.id===chat.provider)||{}).label || tr.assistant
    property var answers: ({})
    property bool followTail: true
    readonly property string conversationId: chat.selectedChat
    onConversationIdChanged: {followTail=true;tail()}
    readonly property var suggestions: composer.text.startsWith("/") && composer.text.indexOf(" ")<0?chat.commands.filter(command=>command.name.startsWith(composer.text.slice(1))).slice(0,6):[]
    onRequestChanged: answers=({})
    function answerQuestion(id,value) {const copy=Object.assign({},answers);copy[id]=value;answers=copy}
    function sendMessage() {followTail=true;if(chat.send()){tail();composer.forceActiveFocus()}}
    function scrollToEnd() {
        conversation.forceLayout()
        conversation.positionViewAtEnd()
        conversation.contentY=Math.max(conversation.originY-conversation.topMargin,conversation.originY+conversation.contentHeight-conversation.height+conversation.bottomMargin)
    }
    function tail() {if(followTail)Qt.callLater(()=>{if(followTail)scrollToEnd()})}
    function latest() {followTail=true;scrollToEnd()}
    function chooseModels() {
        if(chat.models.length<2 && !chat.busy)chat.connectBackend()
        Qt.callLater(()=>modelPicker.openChoices())
    }
    Connections {
        target: chat
        function onTranscriptChanged() {panel.tail()}
        function onModelsRequested() {panel.chooseModels()}
        function onOpenChanged() {if(chat.opened)composer.forceActiveFocus()}
    }
    SmoothRectangle { anchors.fill: parent; radius: 12; color: panel.c.BG3; border.width: 1; border.color: panel.c.b08 }
    Item {
        id: header; x: 18; y: 10; width: parent.width-36; height: 32
        Row {
            anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
            spacing: 6
            RakLabel {
                anchors.verticalCenter: parent.verticalCenter
                text: panel.tr.assistant
                font.pixelSize: 12
                font.weight: Font.Medium
                color: panel.c.FG2
            }
            AgentChatChoice {
                id: backendPicker
                objectName: "agentChatBackend"
                anchors.verticalCenter: parent.verticalCenter
                height: 24
                font.pixelSize: 11
                colors: panel.c
                popupBoundsItem: panel
                text: panel.providerName
                choices: chat.providers.map(p=>({key:p.id,label:p.label}))
                currentKey: chat.provider
                enabled: !chat.running
                onChosen: key=>chat.selectProvider(key)
                toolTip: panel.tr.choose_agent
            }
            AgentLimitMeter {
                id: limitMeter
                objectName: "agentChatLimitMeter"
                anchors.verticalCenter: parent.verticalCenter
                chat: panel.chat
                preferences: panel.preferences
                panelItem: panel
            }
        }
        Row {
            anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            spacing: 6

            // Mode toggle button (Plan / Build)
            RakButton {
                id: modeToggleBtn
                objectName: "agentChatModeToggle"
                visible: panel.chat.modes.length > 0
                width: 24; height: 26; cornerRadius: 7; edgeWidth: 0
                leftPadding: 4; rightPadding: 4
                fillColor: "transparent"; hoverFillColor: panel.c.b06; downFillColor: panel.c.b09
                disabledFillColor: "transparent"
                enabled: !panel.chat.busy
                readonly property bool animsEnabled: panel.preferences && panel.preferences.settings ? panel.preferences.settings.animations : true
                readonly property string currentMode: panel.chat.mode || (panel.chat.modes[0] ? panel.chat.modes[0].id : "build")
                readonly property bool isPlan: currentMode.toLowerCase().indexOf("plan") !== -1
                readonly property color iconColor: modeToggleBtn.visualHovered ? panel.c.FG : panel.c.FG_MUTED
                toolTip: isPlan ? "Mode: Plan (Klik untuk ganti ke Build)" : "Mode: Build (Klik untuk ganti ke Plan)"

                onClicked: {
                    if (panel.chat.modes.length < 2) return
                    var cur = modeToggleBtn.currentMode
                    var next = null
                    for (var i = 0; i < panel.chat.modes.length; i++) {
                        if (panel.chat.modes[i].id !== cur) {
                            next = panel.chat.modes[i]
                            break
                        }
                    }
                    if (next) panel.chat.selectMode(next.id)
                }

                contentItem: Item {
                    anchors.centerIn: parent
                    width: 14; height: 14

                    // Plan Icon (Document with checklist lines)
                    Item {
                        id: planIcon
                        anchors.centerIn: parent
                        width: 14; height: 14
                        opacity: modeToggleBtn.isPlan ? 1.0 : 0.0
                        scale: modeToggleBtn.isPlan ? 1.0 : 0.5
                        visible: opacity > 0.01

                        Behavior on opacity {
                            NumberAnimation { duration: modeToggleBtn.animsEnabled ? 180 : 0; easing.type: Easing.OutCubic }
                        }
                        Behavior on scale {
                            NumberAnimation { duration: modeToggleBtn.animsEnabled ? 200 : 0; easing.type: Easing.OutCubic }
                        }

                        Rectangle {
                            anchors.centerIn: parent
                            width: 11; height: 13; radius: 1.8
                            color: "transparent"; border.width: 1.3; border.color: modeToggleBtn.iconColor
                            Rectangle { anchors.horizontalCenter: parent.horizontalCenter; y: -1.3; width: 4.5; height: 2.2; radius: 0.8; color: modeToggleBtn.iconColor }
                            Rectangle { x: 2.2; y: 3.5; width: 6.5; height: 1.1; radius: 0.5; color: modeToggleBtn.iconColor }
                            Rectangle { x: 2.2; y: 6.2; width: 6.5; height: 1.1; radius: 0.5; color: modeToggleBtn.iconColor }
                            Rectangle { x: 2.2; y: 8.9; width: 4.0; height: 1.1; radius: 0.5; color: modeToggleBtn.iconColor }
                        }
                    }

                    // Build (Hammer / Palu) Icon
                    Item {
                        id: buildIcon
                        anchors.centerIn: parent
                        width: 14; height: 14
                        opacity: !modeToggleBtn.isPlan ? 1.0 : 0.0
                        scale: !modeToggleBtn.isPlan ? 1.0 : 0.5
                        visible: opacity > 0.01

                        Behavior on opacity {
                            NumberAnimation { duration: modeToggleBtn.animsEnabled ? 180 : 0; easing.type: Easing.OutCubic }
                        }
                        Behavior on scale {
                            NumberAnimation { duration: modeToggleBtn.animsEnabled ? 200 : 0; easing.type: Easing.OutCubic }
                        }

                        Item {
                            anchors.centerIn: parent
                            width: 14; height: 14
                            rotation: -42

                            // Head
                            Rectangle {
                                x: 2; y: 1.6; width: 10; height: 3.0; radius: 0.9
                                color: modeToggleBtn.iconColor
                            }
                            // Striking face
                            Rectangle {
                                x: 10.8; y: 1.0; width: 1.5; height: 4.2; radius: 0.6
                                color: modeToggleBtn.iconColor
                            }
                            // Claw notch / peen
                            Rectangle {
                                x: 0.8; y: 2.2; width: 1.8; height: 2.4; radius: 0.5
                                color: modeToggleBtn.iconColor
                            }
                            // Handle
                            Rectangle {
                                x: 6.1; y: 4.4; width: 1.8; height: 8.4; radius: 0.8
                                color: modeToggleBtn.iconColor
                            }
                        }
                    }
                }
            }

            AgentChatHistoryPicker {
                id: historyMenu
                objectName: "agentChatHistory"
                chat: panel.chat
                preferences: panel.preferences
                panelItem: panel
                onNewConversationRequested: composer.forceActiveFocus()
            }
        }
    }
    ListView {
        id: conversation; objectName: "agentChatMessages"
        x: 18; y: 50; width: parent.width-36; height: Math.max(0,requestCard.y-y-18)
        model: chat.messages; clip: true; spacing: 10; boundsBehavior: Flickable.StopAtBounds
        topMargin: 16; bottomMargin: 10
        onMovementStarted: panel.followTail=false
        onContentYChanged: if(moving || scrollBar.pressed)panel.followTail=atYEnd
        onMovementEnded: panel.followTail=atYEnd
        onCountChanged: panel.tail()
        onContentHeightChanged: panel.tail()
        onHeightChanged: panel.tail()
        C.ScrollBar.vertical: RakScrollBar { id: scrollBar; width: 3; handleColor: panel.c.b20; hoverColor: panel.c.b35; onPressedChanged: if(pressed)panel.followTail=false }
        delegate: Item {
            id: message; required property var modelData; width: conversation.width; height: body.implicitHeight+(user?22:6)
            property bool expanded: false
            readonly property bool user: modelData.role==="user"
            readonly property bool thought: modelData.role==="thought"
            readonly property bool activity: modelData.role==="tool" || thought
            readonly property bool status: modelData.role==="status"
            readonly property bool problem: modelData.role==="problem"
            readonly property bool isRunning: !!(panel.chat && panel.chat.running) && (
                (activity && (modelData.status==="running" || modelData.status==="inProgress" || modelData.status==="pending")
                 && !(modelData.title || "").startsWith(panel.tr.tool_unknown || "No result")
                 && !(modelData.title || "").startsWith("No result")
                 && !(modelData.title || "").startsWith("Belum ada hasil")
                 && !(modelData.title || "").startsWith(panel.tr.tool_stopped || "Interrupted")
                 && !(modelData.title || "").startsWith("Stopped")
                 && !(modelData.title || "").startsWith("Dihentikan")
                 && !(modelData.title || "").startsWith("Terputus"))
                || (thought && !!modelData.thinking)
            )
            readonly property bool isFailed: !!modelData.failed || modelData.status==="failed" || modelData.status==="error"
            readonly property string opIconName: {
                if (thought) return "op_thought"
                var tk = ((modelData.tool_kind) || "").toLowerCase()
                var title = ((modelData.title) || "").toLowerCase()
                if (tk.indexOf("edit") !== -1 || tk.indexOf("write") !== -1 || tk.indexOf("change") !== -1 || title.indexOf("edit") !== -1 || title.indexOf("diubah") !== -1) {
                    return "op_edit"
                }
                if (tk.indexOf("read") !== -1 || tk.indexOf("view") !== -1 || title.indexOf("analyz") !== -1 || title.indexOf("dianalisis") !== -1) {
                    return "op_read"
                }
                return "op_command"
            }
            TextMetrics {
                id: userTextMetrics
                font.family: "Poppins"
                font.pixelSize: 13
                text: message.user ? (message.modelData.text || "") : ""
            }
            readonly property real userContentWidth: {
                if (!user) return message.width
                var maxW = Math.max(60, message.width - 38)
                var w = userTextMetrics.width + 4
                if (modelData.attachments && modelData.attachments.length > 0) {
                    w = Math.max(w, Math.min(maxW, 174))
                }
                return Math.min(maxW, Math.max(20, Math.ceil(w)))
            }
            SmoothRectangle { x: body.x-12; y: 0; width: body.width+24; height: message.height; visible: message.user; radius: 13; color: panel.c.b04; border.width: 1; border.color: panel.c.b06 }
            Column {
                id: body; width: message.user ? message.userContentWidth : message.width; x: message.user ? (message.width - width - 12) : 0; y: message.user ? 11 : 3; spacing: 8
                RakLabel { visible: message.problem; width: parent.width; text: (message.modelData && message.modelData.title) || ""; font.pixelSize: 13; font.weight: Font.DemiBold; color: (message.modelData && message.modelData.is_model_error) ? (panel.c.RED_STATUS || "#f87171") : panel.c.FG_BRIGHT }
                C.TextArea {
                    objectName: "agentChatMessageText"; visible: !message.activity && !message.problem && !message.status && !!(message.modelData && message.modelData.text); width: parent.width; height: visible?implicitHeight:0; padding: 0
                    text: (message.modelData && message.modelData.text) || ""; textFormat: TextEdit.MarkdownText
                    readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; font.family: "Poppins"; font.pixelSize: 13; color: panel.c.FG
                    selectionColor: panel.c.a40; selectedTextColor: panel.c.FG_BRIGHT; background: null
                }
                SmoothRectangle {
                    visible: message.problem && !!(message.modelData && message.modelData.body)
                    width: parent.width
                    height: problemBodyText.implicitHeight + 16
                    radius: 7
                    color: panel.c.b04 || "#1a1b26"
                    border.width: 1
                    border.color: (message.modelData && message.modelData.is_model_error) ? (panel.c.red22 || "#451c1d") : panel.c.b10
                    C.TextArea {
                        id: problemBodyText
                        anchors.fill: parent
                        anchors.margins: 8
                        text: (message.modelData && message.modelData.body) || ""
                        readOnly: true
                        selectByMouse: true
                        wrapMode: TextEdit.Wrap
                        font.family: "Consolas"
                        font.pixelSize: 11
                        color: (message.modelData && message.modelData.is_model_error) ? (panel.c.RED_STATUS || "#f87171") : panel.c.FG
                        background: null
                        padding: 0
                    }
                }
                RakLabel { visible: message.problem && !!(message.modelData && message.modelData.next); width: parent.width; text: (message.modelData && message.modelData.next) || ""; color: panel.c.FG_MUTED; font.pixelSize: 11; wrapMode: Text.WordWrap }
                Row {
                    visible: message.problem && !!(message.modelData && message.modelData.is_model_error)
                    spacing: 8
                    ActionButton {
                        colors: panel.c
                        text: panel.tr.switch_to_automatic || "Beralih ke Otomatis"
                        height: 25
                        font.pixelSize: 11
                        onClicked: chat.resetModelToAutomatic()
                    }
                }
                RakLabel { objectName: "agentChatWorkStatus"; visible: message.status; width: parent.width; text: message.modelData.title || ""; font.pixelSize: 11; color: panel.c.FG_MUTED; elide: Text.ElideRight }
                RakButton { id: activityButton; visible: message.activity
                    objectName: message.thought ? (message.modelData.thinking ? "agentChatThinking" : "agentChatThought") : message.activity ? ("agentChatTool_" + message.modelData.key) : ""
                    width: Math.min(parent.width, implicitWidth); height: 25
                    text: message.modelData.title || ""
                    font.pixelSize: 11; leftAligned: true
                    leftPadding: 4
                    rightPadding: 6; cornerRadius: 6
                    edgeWidth: 0
                    fillColor: "transparent"
                    hoverFillColor: panel.c.b03
                    labelColor: message.isFailed ? (panel.c.RED_STATUS || "#f87171") : panel.c.FG_MUTED
                    hoverLabelColor: panel.c.FG
                    onClicked: message.expanded = !message.expanded

                    contentItem: Item {
                        implicitWidth: opIconImg.width + 6 + titleText.implicitWidth + 8 + arrowIcon.width
                        implicitHeight: activityButton.height

                        Image {
                            id: opIconImg
                            anchors.left: parent.left
                            anchors.verticalCenter: parent.verticalCenter
                            width: 13; height: 13
                            sourceSize: Qt.size(16, 16)
                            source: "image://rak/" + message.opIconName + "?color=" + encodeURIComponent(activityButton.visualHovered ? activityButton.hoverLabelColor : activityButton.labelColor)
                            SequentialAnimation on opacity {
                                running: !!message.modelData.thinking && panel.preferences.settings.animations
                                loops: Animation.Infinite
                                NumberAnimation { to: .35; duration: 650 }
                                NumberAnimation { to: 1; duration: 650 }
                            }
                        }

                        Text {
                            id: titleText
                            anchors.left: opIconImg.right
                            anchors.leftMargin: 6
                            anchors.right: arrowIcon.left
                            anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            text: activityButton.text
                            font: activityButton.font
                            color: activityButton.visualHovered ? activityButton.hoverLabelColor : activityButton.labelColor
                            elide: Text.ElideRight
                            verticalAlignment: Text.AlignVCenter
                        }

                        ShaderEffectSource {
                            id: titleMask
                            sourceItem: titleText
                            hideSource: false
                            live: true
                            visible: message.isRunning
                        }

                        Item {
                            id: textShimmerLayer
                            anchors.fill: titleText
                            visible: message.isRunning
                            clip: true
                            layer.enabled: true
                            layer.effect: MultiEffect {
                                maskEnabled: true
                                maskSource: titleMask
                            }

                            Rectangle {
                                id: textShimmerBeam
                                width: Math.max(120, titleText.width * 0.75)
                                height: parent.height
                                property real phase: 0
                                x: -width + phase * (titleText.width + width * 2)
                                NumberAnimation on phase {
                                    from: 0; to: 1; duration: 1800
                                    loops: Animation.Infinite
                                    running: textShimmerLayer.visible && panel.preferences.settings.animations
                                }
                                gradient: Gradient {
                                    orientation: Gradient.Horizontal
                                    GradientStop { position: 0.00; color: "#00ffffff" }
                                    GradientStop { position: 0.25; color: "#06ffffff" }
                                    GradientStop { position: 0.50; color: "#36ffffff" }
                                    GradientStop { position: 0.75; color: "#06ffffff" }
                                    GradientStop { position: 1.00; color: "#00ffffff" }
                                }
                            }
                        }

                        Image {
                            id: arrowIcon
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            width: 8; height: 8
                            sourceSize: Qt.size(16, 16)
                            source: "image://rak/arrow_right?color=" + encodeURIComponent(activityButton.visualHovered ? activityButton.hoverLabelColor : activityButton.labelColor)
                            rotation: message.expanded ? 90 : 0
                            Behavior on rotation {
                                enabled: panel.preferences.settings.animations
                                NumberAnimation { duration: 160; easing.type: Easing.InOutQuad }
                            }
                        }
                    }
                }
                ViewerToolButton { visible: message.problem; colors: panel.c; text: message.expanded ? panel.tr.hide_details : panel.tr.details; font.pixelSize: 10; onClicked: message.expanded = !message.expanded }
                SmoothRectangle {
                    id: terminalBox
                    visible: (message.problem || message.activity) && message.expanded
                    width: parent.width
                    height: visible ? (boxHeader.height + scrollArea.height + (truncatedLabel.visible ? (truncatedLabel.implicitHeight + 8) : 0) + 4) : 0
                    radius: 7
                    color: panel.c.BG_DARK || "#0c0d14"
                    border.width: 1
                    border.color: message.isFailed ? panel.c.red22 : panel.c.b12
                    clip: true

                    Item {
                        id: boxHeader
                        width: parent.width
                        height: 28

                        SmoothRectangle {
                            anchors.fill: parent
                            color: panel.c.b03
                            radius: 7
                            Rectangle {
                                anchors.bottom: parent.bottom
                                width: parent.width
                                height: 1
                                color: panel.c.b06
                            }
                        }

                        Row {
                            anchors.left: parent.left
                            anchors.leftMargin: 10
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 6

                            Image {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 12; height: 12
                                sourceSize: Qt.size(16, 16)
                                source: "image://rak/" + message.opIconName + "?color=" + encodeURIComponent(panel.c.FG_MUTED)
                            }

                            RakLabel {
                                anchors.verticalCenter: parent.verticalCenter
                                text: message.thought ? panel.tr.thinking_details : (message.modelData.command ? (message.modelData.command.split(' ')[0] + " · Terminal") : "Terminal Output")
                                font.pixelSize: 10
                                font.family: "Poppins"
                                color: panel.c.FG_MUTED
                            }
                        }

                        SmoothRectangle {
                            visible: message.isFailed
                            anchors.right: copyOutputBtn.left
                            anchors.rightMargin: 8
                            anchors.verticalCenter: parent.verticalCenter
                            width: failedBadgeText.implicitWidth + 10
                            height: 17
                            radius: 4
                            color: panel.c.red14
                            border.width: 1
                            border.color: panel.c.red28

                            RakLabel {
                                id: failedBadgeText
                                anchors.centerIn: parent
                                text: message.modelData.exit_code ? ("Exit " + message.modelData.exit_code) : (panel.tr.tool_failed || "Failed")
                                font.pixelSize: 9
                                font.family: "Poppins"
                                font.weight: Font.Medium
                                color: panel.c.RED_STATUS
                            }
                        }

                        RakButton {
                            id: copyOutputBtn
                            anchors.right: parent.right
                            anchors.rightMargin: 6
                            anchors.verticalCenter: parent.verticalCenter
                            width: 22; height: 20
                            cornerRadius: 4
                            edgeWidth: 0
                            leftPadding: 0; rightPadding: 0
                            fillColor: "transparent"
                            hoverFillColor: panel.c.b06
                            iconSource: "image://rak/chat_copy?color=" + encodeURIComponent(visualHovered ? panel.c.FG : panel.c.FG_MUTED)
                            iconWidth: 12; iconHeight: 12
                            toolTip: panel.tr.copy
                            onClicked: chat.copyText(technical.text)
                        }
                    }

                    C.ScrollView {
                        id: scrollArea
                        anchors.top: boxHeader.bottom
                        anchors.left: parent.left
                        anchors.right: parent.right
                        height: Math.min(180, Math.max(32, technical.implicitHeight + 12))
                        clip: true
                        C.ScrollBar.vertical: RakScrollBar { width: 3; handleColor: panel.c.b20; hoverColor: panel.c.b35 }

                        C.TextArea {
                            id: technical
                            objectName: message.thought ? "agentChatThinkingText" : "agentChatToolText"
                            text: message.modelData.text || (message.thought ? (message.modelData.thinking ? panel.tr.thinking_empty : panel.tr.thought_empty) : "")
                            textFormat: message.thought ? TextEdit.MarkdownText : TextEdit.PlainText
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            padding: 8
                            font.family: message.thought ? "Poppins" : "Consolas"
                            font.pixelSize: 11
                            color: panel.c.FG2
                            selectionColor: panel.c.a40
                            selectedTextColor: panel.c.FG_BRIGHT
                            background: null
                        }
                    }

                    RakLabel {
                        id: truncatedLabel
                        visible: !!message.modelData.truncated
                        anchors.top: scrollArea.bottom
                        anchors.left: parent.left
                        anchors.leftMargin: 8
                        anchors.right: parent.right
                        anchors.rightMargin: 8
                        anchors.topMargin: 4
                        text: panel.tr.shortened
                        font.pixelSize: 10
                        color: panel.c.FG_MUTED
                        wrapMode: Text.WordWrap
                    }
                }
                Flow { width: body.width; spacing: 6
                  Repeater { model: message.modelData.attachments || []
                    delegate: AgentAttachmentChip { required property var modelData; attachment: modelData; chat: panel.chat; preferences: panel.preferences; width: Math.min(174,body.width) }
                  }
                }
                RakButton { visible: message.modelData.role==="assistant"; objectName: "agentChatCopy"; width: 24; height: 22; cornerRadius: 6; edgeWidth: 0; leftPadding: 0; rightPadding: 0
                    iconSource: "image://rak/chat_copy?color="+encodeURIComponent(panel.c.FG_MUTED); iconWidth: 14; iconHeight: 14; hoverFillColor: panel.c.b05; toolTip: panel.tr.copy; onClicked: chat.copyText(message.modelData.text)
                }
            }
        }
        Column {
            visible: conversation.count===0; x: 5; y: Math.max(20,(parent.height-height)*.38); width: parent.width-10; spacing: 12
            RakLabel { width: parent.width; text: panel.tr.welcome; font.pixelSize: 18; font.weight: Font.DemiBold; color: panel.c.FG_BRIGHT; wrapMode: Text.WordWrap }
            RakLabel { width: parent.width; text: panel.tr.welcome_body; font.pixelSize: 12; color: panel.c.FG_MUTED; wrapMode: Text.WordWrap }
            ActionButton { colors: panel.c; text: panel.tr.example; height: 30; font.pixelSize: 11; onClicked: {composer.text=panel.tr.example;composer.forceActiveFocus()} }
        }
    }
    RakButton {
        objectName: "agentChatJumpLatest"; z: 4
        visible: conversation.contentHeight>conversation.height && conversation.contentY+conversation.height<conversation.originY+conversation.contentHeight-24
        x: (panel.width-width)/2; y: requestCard.y-height-10; width: 32; height: 32; cornerRadius: 16
        leftPadding: 0; rightPadding: 0; edgeWidth: 1; edgeColor: panel.c.b14; fillColor: panel.c.BG3; hoverFillColor: panel.c.BG2
        iconSource: "image://rak/chat_arrow_down?color="+encodeURIComponent(panel.c.FG2); iconWidth: 18; iconHeight: 18
        toolTip: panel.tr.latest; onClicked: panel.latest()
    }
    SmoothRectangle {
        id: requestCard; objectName: "agentChatPermissionCard"; x: 16; y: composerBox.y-height-12; width: parent.width-32
        height: panel.request.id?Math.max(0,Math.min(270,composerBox.y-65,requestBody.implicitHeight+24)):0; visible: height>0; radius: 12; color: panel.c.BG2; border.width: 1; border.color: panel.c.a25
        C.ScrollView { anchors.fill: parent; anchors.margins: 12; clip: true
            Column { id: requestBody; width: requestCard.width-24; spacing: 9
                RakLabel { width: parent.width; text: panel.request.type==="question"?panel.tr.waiting:panel.tr.permission; color: panel.c.FG_BRIGHT; font.pixelSize: 13; font.weight: Font.DemiBold; wrapMode: Text.WordWrap }
                C.TextArea { visible: !!panel.request.detail; width: parent.width; text: panel.request.detail || panel.request.title || ""; textFormat: TextEdit.PlainText; readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; font.family: "Consolas"; font.pixelSize: 10; color: panel.c.FG2; padding: 0; background: null }
                Repeater { model: panel.request.questions || []
                    delegate: Column { required property var modelData; width: requestBody.width; spacing: 5
                        RakLabel { width: parent.width; text: parent.modelData.question; color: panel.c.FG; font.pixelSize: 12; wrapMode: Text.WordWrap }
                        ChoiceButton { visible: (modelData.options || []).length>0; colors: panel.c; width: parent.width; text: panel.tr.choose_answer; font.pixelSize: 11; choices: parent.modelData.options.map(o=>({key:o.label,label:o.label})); onChosen: key=>panel.answerQuestion(parent.modelData.id,key) }
                        ViewerField { objectName: "agentChatAnswer"; width: parent.width; colors: panel.c; placeholderText: panel.tr.your_answer; text: panel.answers[parent.modelData.id] || ""; onTextEdited: panel.answerQuestion(parent.modelData.id,text) }
                    }
                }
                Item { width: parent.width; height: 29
                  Row { anchors.right: parent.right; spacing: 6
                    Repeater { model: panel.request.type==="approval"?panel.approvalChoices:[]
                        delegate: DialogButton { required property var modelData; objectName: "agentChatApproval_"+modelData.kind; colors: panel.c; text: modelData.kind==="save"?panel.tr.save:modelData.kind==="yes"?panel.tr.allow:panel.tr.deny; primary: modelData.kind==="yes"; enabled: !!modelData.id; height: 27; width: Math.ceil(contentItem.implicitWidth)+22
                            toolTip: modelData.kind==="save"?(modelData.id?panel.tr.save_help:panel.tr.save_unavailable):""; onClicked: chat.answer(panel.request.id,modelData.id,{})
                        }
                    }
                    DialogButton { visible: panel.request.type==="question"; objectName: "agentChatSubmitAnswer"; colors: panel.c; primary: true; text: panel.tr.answer; enabled: (panel.request.questions || []).every(q=>!!(panel.answers[q.id] || "").trim()); onClicked: chat.answer(panel.request.id,"answer",panel.answers) }
                  }
                }
            }
        }
    }
    AgentRainbowShadow {
        id: composerRainbow
        objectName: "agentChatComposerRainbow"
        anchors.fill: composerBox
        radius: composerBox.radius
        active: panel.chat.running
        animated: panel.preferences.settings.animations
    }
    SmoothRectangle {
        id: composerBox; objectName: "agentChatComposerBox"; x: 14; y: parent.height-height-14; width: parent.width-28
        readonly property bool stacked: width<310
        height: footer.y+footer.height+9+(inputError.visible?inputError.implicitHeight+12:0)+(catalogHint.visible?catalogHint.height+10:0)
        radius: 18; color: panel.c.b03; border.width: 1; border.color: composer.activeFocus?panel.c.b20:panel.c.b09
        ListView {
            id: attachmentStrip; objectName: "agentChatAttachmentStrip"; x: 12; y: 10; width: parent.width-24; height: count?66:0
            model: chat.attachments; orientation: ListView.Horizontal; spacing: 6; clip: true; boundsBehavior: Flickable.StopAtBounds
            onCountChanged: Qt.callLater(()=>positionViewAtEnd())
            C.ScrollBar.horizontal: RakScrollBar { height: 3; handleColor: panel.c.b20; hoverColor: panel.c.b35 }
            delegate: AgentAttachmentChip { required property var modelData; attachment: modelData; chat: panel.chat; preferences: panel.preferences; removable: true }
        }
        C.ScrollView { id: inputScroll; x: 8; y: 12+attachmentStrip.height; width: parent.width-16; height: Math.max(43,Math.min(120,composer.contentHeight+8)); clip: true
            C.TextArea { id: composer; objectName: "agentChatComposer"; text: chat.draft; onTextChanged: chat.setDraft(text)
                placeholderText: panel.tr.placeholder; placeholderTextColor: panel.c.FG_MUTED; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap; selectByMouse: true; font.family: "Poppins"; font.pixelSize: 12; color: panel.c.FG; padding: 5; background: null
                Keys.priority: Keys.BeforeItem
                Keys.onPressed: event=>{if((event.key===Qt.Key_V && (event.modifiers & Qt.ControlModifier)) || (event.key===Qt.Key_Insert && (event.modifiers & Qt.ShiftModifier)))event.accepted=chat.pasteAttachments()}
                function handleReturn(event) {
                    if(panel.request.id && panel.request.type==="approval"){
                        var yesChoice=(panel.approvalChoices || []).find(c=>c.kind==="yes");
                        if(yesChoice && yesChoice.id){chat.answer(panel.request.id,yesChoice.id,{});event.accepted=true;return}
                    }
                    if(event.modifiers & Qt.ShiftModifier){
                        event.accepted=false
                    } else {
                        panel.sendMessage()
                        event.accepted=true
                    }
                }
                Keys.onReturnPressed: event => handleReturn(event)
                Keys.onEnterPressed: event => handleReturn(event)
            }
        }
        Item {
            id: footer; x: 8; y: inputScroll.y+inputScroll.height+5; width: parent.width-16; height: 32
            AgentChatChoice { id: attachmentMenu; objectName: "agentChatAttach"; x: 0; y: 2; width: 24; height: 28; text: "+"; showArrow: false; font.pixelSize: 18; colors: panel.c; toolTip: panel.tr.attachments; popupBoundsItem: panel
                choices: [{key:"selection",label:panel.tr.add_selection},{key:"files",label:panel.tr.add_files},{key:"folder",label:panel.tr.add_folder},{key:"clear",label:panel.tr.clear}]
                onChosen: key=>{attachmentMenu.previewCaption=null;if(key==="selection")chat.refreshAttachments();else if(key==="files")attachmentFiles.open();else if(key==="folder")attachmentFolder.open();else chat.clearAttachments()}
            }
            AgentModelPicker {
                id: modelPicker
                objectName: "agentChatModel"
                x: 28; y: 3
                width: Math.min(implicitWidth, Math.max(40, permissionPicker.x - x - 6))
                chat: panel.chat
                preferences: panel.preferences
                popupBoundsItem: panel
            }
            AgentPermissionChoice {
                id: permissionPicker
                objectName: "agentChatPermission"
                anchors.right: sendButton.left
                anchors.rightMargin: 6
                anchors.verticalCenter: parent.verticalCenter
                chat: panel.chat
                preferences: panel.preferences
                popupBoundsItem: panel
            }
            RakButton { id: sendButton; objectName: "agentChatSend"; x: parent.width-width; y: 0; width: 32; height: 32; cornerRadius: 16; leftPadding: 0; rightPadding: 0; edgeWidth: 0
                fillColor: panel.c.FG; hoverFillColor: panel.c.FG_BRIGHT; disabledFillColor: panel.c.b06
                iconSource: "image://rak/"+(chat.running?"chat_stop":"chat_arrow_up")+"?color="+encodeURIComponent(enabled?panel.c.BG:panel.c.FG_MUTED); iconWidth: 19; iconHeight: 19
                enabled: chat.running || ((!chat.busy || chat.state==="connecting") && (composer.text.trim().length>0 || chat.attachments.length>0))
                toolTip: chat.state==="stopping"?panel.tr.force_stop:chat.running?(chat.provider==="agy"?panel.tr.stop_after:panel.tr.stop):(panel.tr.send + " (Enter)")
                onClicked: {if(chat.running)chat.stop();else panel.sendMessage()}
            }
        }
        RakLabel { id: inputError; x: 13; y: footer.y+footer.height+7; width: parent.width-26; visible: !!chat.error && chat.state!=="failed"; text: visible?chat.error:""; color: panel.c.RED_STATUS; font.pixelSize: 10; wrapMode: Text.WordWrap }
        Column { id: catalogHint; objectName: "agentChatModelWarning"; visible: !!chat.modelCatalogError; x: 13; y: inputError.y+(inputError.visible?inputError.implicitHeight+8:0); width: parent.width-26; spacing: 4
            RakLabel { width: parent.width; text: chat.modelCatalogError; color: panel.c.FG_MUTED; font.pixelSize: 10; wrapMode: Text.WordWrap }
            Row { spacing: 5
                ViewerToolButton { objectName: "agentChatRetryModels"; colors: panel.c; text: panel.tr.retry; onClicked: chat.retryModels() }
                ViewerToolButton { colors: panel.c; text: panel.tr.details; toolTip: panel.tr.catalog_log; onClicked: chat.copyModelLog() }
            }
        }
    }
    SmoothRectangle { visible: panel.suggestions.length>0; x: composerBox.x; y: composerBox.y-height-7; width: composerBox.width; height: commandColumn.implicitHeight+8; radius: 8; color: panel.c.BG2; border.width: 1; border.color: panel.c.b14; z: 5
        Column { id: commandColumn; x: 4; y: 4; width: parent.width-8
            Repeater { model: panel.suggestions
                delegate: RakButton { required property var modelData; width: commandColumn.width; height: 29; text: "/"+modelData.name+"  "+modelData.description; font.pixelSize: 10; leftAligned: true; leftPadding: 8; edgeWidth: 0; fillColor: "transparent"; hoverFillColor: panel.c.b06; labelColor: panel.c.FG; hoverLabelColor: panel.c.FG_BRIGHT; onClicked: {composer.text="/"+modelData.name;composer.forceActiveFocus()} }
            }
        }
    }
    D.FileDialog { id: attachmentFiles; title: panel.tr.add_files; fileMode: D.FileDialog.OpenFiles; onAccepted: chat.addAttachments(selectedFiles.map(url=>url.toString())) }
    D.FolderDialog { id: attachmentFolder; title: panel.tr.add_folder; onAccepted: chat.addAttachments([selectedFolder.toString()]) }
    DropArea {
        id: fileDrop; objectName: "agentChatDropArea"; anchors.fill: parent
        function paths(event) {
            if(event.hasUrls)return event.urls.map(url=>url.toString())
            const mime=event.source && event.source.dragMime
            const internal=mime?mime["application/x-rak-tree"]:(event.formats.indexOf("application/x-rak-tree")>=0?event.getDataAsString("application/x-rak-tree"):"")
            if(internal){try{return JSON.parse(internal).paths || []}catch(error){return []}}
            return []
        }
        onEntered: event=>{event.accepted=event.hasUrls || event.formats.indexOf("application/x-rak-tree")>=0 || !!(event.source && event.source.dragMime && event.source.dragMime["application/x-rak-tree"])}
        onDropped: event=>{const added=chat.addAttachments(paths(event));if(added){event.accept(Qt.CopyAction);composer.forceActiveFocus()}else event.accepted=false}
    }
    SmoothRectangle { visible: fileDrop.containsDrag; anchors.fill: composerBox; z: 5; radius: 18; color: panel.c.BG2; border.width: 1; border.color: panel.c.accent_hex
        RakLabel { anchors.centerIn: parent; width: parent.width-24; text: panel.tr.drop_files; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 12; color: panel.c.FG; wrapMode: Text.WordWrap }
    }
    // Soft gradient fade at top of message list to smoothly dissolve scrolled content into header
    Item {
        x: conversation.x; y: conversation.y; width: conversation.width; height: 16
        z: 3; enabled: false
        opacity: !conversation.atYBeginning ? 1.0 : 0.0
        Behavior on opacity { enabled: !!(panel.preferences && panel.preferences.settings && panel.preferences.settings.animations); NumberAnimation { duration: (panel.preferences && panel.preferences.settings && panel.preferences.settings.animations) ? 140 : 0; easing.type: Easing.OutCubic } }
        Rectangle {
            anchors.fill: parent
            gradient: Gradient {
                GradientStop { position: 0.0; color: panel.c.BG3 }
                GradientStop { position: 1.0; color: "transparent" }
            }
        }
    }
}
