// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: dialog
    objectName: "workspaceSettings"
    agentProtected: true
    required property var workspace
    property var draft: ({})
    property string guidedTarget: ""
    property var settingItems: ({})
    implicitWidth: 520; implicitHeight: 540
    function showSettings(target) {
        draft=JSON.parse(JSON.stringify(workspace.projectSettings));guidedTarget=target || "";open()
        Qt.callLater(()=>{
            const item=settingItems[guidedTarget]
            if(item){options.contentY=Math.max(0,Math.min(options.contentHeight-options.height,item.y-30));guideTimer.restart()}
        })
    }
    Timer { id: guideTimer; interval: 10000; onTriggered: dialog.guidedTarget="" }
    function setValue(section,key,value) {let next=JSON.parse(JSON.stringify(draft));next[section][key]=value;draft=next}
    Row {
        x: 16; y: 16; spacing: 8
        Image { width: 18; height: 18; source: "image://icons/set_project"; sourceSize: Qt.size(15,15) }
        RakLabel { text: dialog.s.main_project_settings; color: dialog.c.FG; font.pixelSize: 14; font.weight: Font.DemiBold }
    }
    SmoothRectangle { x: 16; y: 46; width: parent.width-32; height: 1; color: dialog.c.b07 }
    Flickable {
        id: options
        objectName: "workspaceSettingsScroll"
        x: 16; y: 57; width: parent.width-32; height: parent.height-122
        contentWidth: width; contentHeight: cards.height+8; clip: true; boundsBehavior: Flickable.StopAtBounds
        C.ScrollBar.vertical: RakScrollBar { objectName: "workspaceSettingsScrollHint"; handleColor: dialog.c.BG_SCROLL }
        Column {
            id: cards; y: 4; width: parent.width; spacing: 10
            Repeater {
                model: [
                    {key:"super",title:"Normalize SUPER",description:"Normalize partition slots from *_a and *_b to single image",field:"norm"},
                    {key:"erofs",title:"EROFS",description:"Default",field:"default"},
                    {key:"brotli",title:"Brotli",description:"Default",field:"default"},
                    {key:"selinux",title:"SELinux Patch",description:"Apply SELinux Patch",field:"enabled"}
                ]
                delegate: SmoothRectangle {
                    id: card
                    required property var modelData
                    readonly property string section: modelData.key
                    readonly property bool bodyVisible: section==="selinux"?toggle.checked:section!=="super"&&!toggle.checked
                    width: cards.width; height: 82+(section==="super"?14:bodyVisible?details.height+14:0)
                    radius: 10; color: dialog.c.b03; border.width: 1; border.color: dialog.c.b08
                    RakLabel { x: 15; y: 13; text: card.modelData.title; color: dialog.c.FG; font.pixelSize: 13; font.weight: Font.DemiBold }
                    RakLabel { x: 15; y: 36; height: 24; width: parent.width-87; text: card.modelData.description; verticalAlignment: Text.AlignVCenter; color: dialog.c.FG_DESC; font.pixelSize: 11; elide: Text.ElideRight }
                    RakSwitch {
                        id: toggle; objectName: "workspaceSettingsToggle_"+card.section
                        anchors.right: parent.right; anchors.rightMargin: 15; y: 36
                        checked: !!(dialog.draft[card.section] && dialog.draft[card.section][card.modelData.field])
                        accentColor: dialog.c.accent_hex; offColor: dialog.c.offToggle; animationsEnabled: dialog.model.settings.animations
                        onToggled: dialog.setValue(card.section,card.modelData.field,checked)
                    }
                    SmoothRectangle { x: 14; y: 68; width: parent.width-28; height: 1; color: dialog.c.b06 }
                    Column {
                        id: details; visible: card.bodyVisible; x: 15; y: 83; width: parent.width-30; spacing: 8
                        Item {
                            visible: card.section==="erofs"; width: parent.width; height: visible?28:0
                            RakLabel { anchors.verticalCenter: parent.verticalCenter; text: "Compression Method"; font.pixelSize: 12; color: dialog.c.FG_BLUE_MUTED }
                            WorkspaceChoiceButton {
                                objectName: "workspaceCompressionMethod"; anchors.right: parent.right; colors: dialog.c; width: 92
                                workspace: dialog.workspace; popupBoundsItem: dialog.contentItem
                                popupBackdropSource: dialog.backdropSurface
                                text: dialog.draft.erofs?dialog.draft.erofs.method:"lz4hc"; currentKey: text
                                choices: [{value:"lz4",label:"lz4"},{value:"lz4hc",label:"lz4hc"},{value:"lzma",label:"lzma"},{value:"deflate",label:"deflate"},{value:"zstd",label:"zstd"}]
                                onChosen: key => dialog.setValue("erofs","method",key)
                            }
                        }
                        RakLabel { visible: card.section!=="selinux"; text: "Compression Level"; font.pixelSize: 12; color: dialog.c.FG_BLUE_MUTED }
                        RakLabel { visible: card.section!=="selinux"; text: "Level: "+Math.round(level.value); font.pixelSize: 12; color: dialog.c.FG_BLUE_MUTED }
                        ViewerSlider {
                            id: level; visible: card.section!=="selinux"; width: parent.width; height: visible?24:0
                            from: 1; to: 9; stepSize: 1
                            preferences: dialog.model; colors: dialog.c; knobDiameter: 16
                            trackColor: dialog.c.b15; trackFillColor: dialog.c.accent_hex
                            value: dialog.draft[card.section]?dialog.draft[card.section].level || 1:1
                            onMoved: dialog.setValue(card.section,"level",Math.round(value))
                        }
                        Repeater {
                            model: card.section==="selinux"?[{title:"AI neocat Model 1.0",description:"Enhanced contextual accuracy with intelligent policy handling",checked:false},{title:"Plain Context",description:"Faster context processing with minimal overhead",checked:true}]:[]
                            delegate: Item {
                                required property var modelData
                                width: details.width; height: 54
                                Column { spacing: 6; RakLabel { text: modelData.title; color: dialog.c.FG_MUTED; font.pixelSize: 12 } RakLabel { text: modelData.description; color: dialog.c.b25; font.pixelSize: 10 } }
                                RakSwitch { anchors.right: parent.right; y: 8; enabled: false; checked: modelData.checked; accentColor: dialog.c.accent_hex; offColor: dialog.c.offToggle }
                            }
                        }
                    }
                }
            }
            Repeater {
                model: [
                    {key:"autosave",title:dialog.s.languageKey==="id"?"Autosave perubahan agent":"Autosave agent changes",description:dialog.s.languageKey==="id"?"Simpan edit agent yang berhasil secara otomatis.":"Save successful agent edits automatically."},
                    {key:"highlight",title:dialog.s.languageKey==="id"?"Highlight aktivitas agent":"Highlight agent activity",description:dialog.s.languageKey==="id"?"Efek rainbow tipis pada file dan folder yang disentuh.":"Subtle rainbow feedback on touched files and folders."},
                    {key:"ui_triggers",title:dialog.s.languageKey==="id"?"Akses UI agent":"Agent UI access",description:dialog.s.languageKey==="id"?"Izinkan membuka editor, window, dan kontrol project. Settings tetap hanya untuk user.":"Allow editors, project windows and controls. Settings remain user-only."}
                ]
                delegate: SmoothRectangle {
                    id: agentCard
                    required property var modelData
                    objectName: "workspaceSetting_agent_"+modelData.key
                    width: cards.width; height: 96; radius: 10; color: dialog.c.b03; border.width: 1; border.color: dialog.c.b08
                    Component.onCompleted: dialog.settingItems["agent."+modelData.key]=agentCard
                    AgentRainbow { anchors.fill: parent; active: dialog.guidedTarget==="agent."+agentCard.modelData.key; animated: dialog.model.settings.animations }
                    RakLabel { x: 15; y: 12; width: parent.width-80; text: agentCard.modelData.title; color: dialog.c.FG; font.pixelSize: 12; font.weight: Font.Medium }
                    RakLabel { x: 15; y: 38; width: parent.width-85; text: agentCard.modelData.description; color: dialog.c.FG_DESC; font.pixelSize: 10; wrapMode: Text.WordWrap }
                    RakSwitch { objectName: "workspaceSettingsToggle_agent_"+agentCard.modelData.key; anchors.right: parent.right; anchors.rightMargin: 15; y: 35
                        checked: !!(dialog.draft.agent && dialog.draft.agent[agentCard.modelData.key]); accentColor: dialog.c.accent_hex; offColor: dialog.c.offToggle; animationsEnabled: dialog.model.settings.animations
                        onToggled: dialog.setValue("agent",agentCard.modelData.key,checked)
                    }
                }
            }
        }
    }
    SmoothRectangle { x: 16; y: parent.height-55; width: parent.width-32; height: 1; color: dialog.c.b07 }
    Row {
        anchors.right: parent.right; anchors.rightMargin: 16; y: parent.height-44; spacing: 8
        DialogButton { objectName: "workspaceSettingsCancel"; colors: dialog.c; text: dialog.s.main_btn_cancel; onClicked: dialog.dismiss() }
        DialogButton { objectName: "workspaceSettingsApply"; colors: dialog.c; text: dialog.s.main_btn_apply; primary: true; onClicked: { workspace.applySettings(dialog.draft);dialog.dismiss() } }
    }
}
