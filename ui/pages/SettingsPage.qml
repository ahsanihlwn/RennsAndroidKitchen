// SPDX-License-Identifier: MPL-2.0
import QtQuick
import "../controls"

Item {
    id: page
    required property var model
    readonly property var c: model.colors
    readonly property var s: model.strings
    readonly property bool glassChrome: headerGlass.rendering
    property var presentationMotion: null
    readonly property bool motionInBackdrop: true
    readonly property real headingOpacity: presentationMotion ? presentationMotion.opacityAt(page,40) : 1
    property alias footer: footer
    readonly property var identityItem: null
    function identityEndpoint(host) {
        return null
    }
    function revealBands() {
        let bands=[]
        for (let i=4;i>=0;--i) bands.push({top:Math.round(height*i/5),bottom:Math.round(height*(i+1)/5)})
        return bands
    }
    component Card: RakCard {
        width: content.width; color: page.c.BG2; border.color: page.c.b08; radius: 10
    }
    component CardTitle: Item {
        property string text: ""
        property string iconName: ""
        x: 14; y: 12; width: parent.width - 28; height: 20
        WorkspaceNativeIcon {
            anchors.verticalCenter: parent.verticalCenter
            iconKey: parent.iconName
            iconSize: 20
            strokeColor: page.c.FG_LABEL
            accentColor: page.c.accent_hex
            isLight: (page.c && page.c.is_light !== undefined) ? page.c.is_light : false
        }
        RakLabel { x: 28; height: parent.height; text: parent.text; color: page.c.FG_LABEL; font.pixelSize: 14; font.weight: Font.DemiBold }
    }
    component SmallButton: ActionButton {
        colors: page.c; height: 28; cornerRadius: 6; leftPadding: 12; rightPadding: 12
        animatePress: page.model.settings.animations
    }
    component SettingText: Item {
        property string title: ""
        implicitHeight: 28
        RakLabel { id: label; anchors.verticalCenter: parent.verticalCenter; width: parent.width; height: 20; text: parent.title; color: page.c.FG; font.pixelSize: 12; font.weight: Font.DemiBold }
    }
    RakLabel { z: 2; opacity: page.headingOpacity; x: 16; y: 16; height: 32; text: page.s.hdr_settings; font.pixelSize: 22; font.weight: Font.DemiBold; color: page.c.FG }
    SmoothRectangle {
        id: settingsSurface
        readonly property rect inputBounds: Qt.rect(0,headerGlass.height,width,Math.max(0,height-headerGlass.height-footer.height))
        anchors.fill: parent; color: page.c.BG; clip: true
    Flickable {
        id: scroll
        objectName: "settingsScroll"
        x: 16; y: 68; width: parent.width - 32; height: Math.max(0, parent.height - 68 - 16)
        clip: !page.glassChrome; contentHeight: content.height; boundsBehavior: Flickable.StopAtBounds
        Column {
            id: content
            x: 0; y: 0; width: parent.width; spacing: 20
            Card {
                height: 139
                CardTitle { text: page.s.hdr_product_info; iconName: "info" }
                Column {
                    x: 14; y: 52; width: parent.width - 28; spacing: 8
                    Repeater {
                        model: [[page.s.form_name, page.s.appName], [page.s.form_version, page.s.appVersion], [page.s.form_edition, page.s.edition]]
                        delegate: Item {
                            required property var modelData
                            width: parent.width; height: 19
                            RakLabel { width: 49; height: 19; text: parent.modelData[0]; color: page.c.FG }
                            RakLabel { x: 57; width: parent.width - 57; height: 19; text: parent.modelData[1]; color: page.c.FG }
                        }
                    }
                }
            }
            Card {
                height: 54
                CardTitle { text: page.s.hdr_packages; iconName: "package"; height: 28 }
                SmallButton { anchors.right: parent.right; anchors.rightMargin: 14; y: 12; width: 63; text: page.s.btn_manage; onClicked: page.model.dispatch("packages", {}) }
            }
            Card {
                height: 86
                CardTitle { text: page.s.hdr_default_location; iconName: "default_location" }
                RakLabel { x: 14; y: 44; width: parent.width - 160; height: 28; text: page.model.defaultLocation || page.s.lbl_docs_system_default; color: page.c.FG_MUTED; font.pixelSize: 12; elide: Text.ElideRight }
                SmallButton { anchors.right: changeLocation.left; anchors.rightMargin: 6; y: 44; width: 55; text: page.s.btn_reset; fillColor: "transparent"; edgeColor: "transparent"; labelColor: page.c.FG3; hoverLabelColor: page.c.FG; onClicked: page.model.dispatch("resetLocation", {}) }
                SmallButton { id: changeLocation; anchors.right: parent.right; anchors.rightMargin: 14; y: 44; width: 65; text: page.s.btn_change; onClicked: page.model.dispatch("defaultLocation", {}) }
            }
            Card {
                id: appearance
                height: modeText.y + modeText.height + 14
                CardTitle { text: page.s.hdr_appearance; iconName: "theme" }
                SettingText {
                    id: themeText
                    objectName: "themeSetting"
                    x: 14; y: 52; width: parent.width-38-themeChange.width; height: implicitHeight
                    title: page.s.lbl_theme
                }
                SmallButton {
                    id: themeChange
                    objectName: "themeChangeButton"
                    anchors.right: parent.right; anchors.rightMargin: 14
                    y: Math.floor(themeText.y+(themeText.height-height)/2)
                    width: Math.max(98,implicitWidth); text: page.s.btn_change_short
                    onClicked: page.model.dispatch("themePicker",{})
                }
                SmoothRectangle { id: themeDivider; x: 14; y: themeText.y+themeText.height+12; width: parent.width - 28; height: 1; color: page.c.b06 }
                SettingText { id: effectText; x: 14; y: themeDivider.y+13; width: parent.width - 122; height: implicitHeight; title: page.s.lbl_effect_level }
                ChoiceButton {
                    objectName: "effectLevelChoice"
                    anchors.right: parent.right; anchors.rightMargin: 14; y: Math.floor(effectText.y + (effectText.height - height) / 2)
                    width: Math.max(98,implicitWidth); height: 28; colors: page.c; text: page.model.settings.effects.charAt(0).toUpperCase() + page.model.settings.effects.slice(1)
                    choices: [{key: "low", label: "Low"}, {key: "medium", label: "Medium"}, {key: "high", label: "High"}]
                    currentKey: page.model.settings.effects; animatePress: page.model.settings.animations
                    onChosen: key => page.model.chooseSetting("effects", key)
                }
                SmoothRectangle { id: animationDivider; x: 14; y: effectText.y + effectText.height + 12; width: parent.width - 28; height: 1; color: page.c.b06 }
                SettingText { id: animationText; x: 14; y: animationDivider.y + 13; width: parent.width - 80; height: implicitHeight; title: page.s.lbl_animations }
                RakSwitch {
                    objectName: "animationsSwitch"
                    anchors.right: parent.right; anchors.rightMargin: 14; y: animationText.y + (animationText.height - height) / 2
                    width: 42; height: 24; 
                    checked: page.model.settings.animations; animationsEnabled: page.model.settings.animations
                    onToggled: page.model.chooseSetting("animations", checked)
                }
                SmoothRectangle { id: modeDivider; x: 14; y: animationText.y + animationText.height + 12; width: parent.width - 28; height: 1; color: page.c.b06 }
                SettingText { id: modeText; x: 14; y: modeDivider.y + 13; width: parent.width - 38 - projectMode.width; height: implicitHeight; title: page.s.lbl_project_mode }
                ChoiceButton {
                    id: projectMode
                    objectName: "projectModeChoice"
                    anchors.right: parent.right; anchors.rightMargin: 14; y: Math.floor(modeText.y + (modeText.height - height) / 2)
                    width: Math.max(100,implicitWidth); height: 28; colors: page.c
                    text: page.model.settings.treeMode === "tree" ? page.s.lbl_mode_tree : page.s.lbl_mode_single_dir
                    currentKey: page.model.settings.treeMode
                    choices: [{key: "single_dir", label: page.s.lbl_mode_single_dir}, {key: "tree", label: page.s.lbl_mode_tree}]
                    animatePress: page.model.settings.animations; onChosen: key => page.model.chooseSetting("treeMode", key)
                }
            }
            Card {
                height: 54
                CardTitle { text: page.s.hdr_language; iconName: "language"; height: 28 }
                ChoiceButton {
                    objectName: "languageChoice"
                    anchors.right: parent.right; anchors.rightMargin: 14; y: 12
                    height: 28; colors: page.c; text: page.s.languageName; choices: page.model.languages
                    currentKey: page.s.languageKey
                    animatePress: page.model.settings.animations; onChosen: key => page.model.chooseLanguage(key)
                }
            }
            Card {
                visible: !!page.model.profile.name
                height: visible ? 112 : 0
                CardTitle { text: page.s.hdr_activation; iconName: "key" }
                RakLabel { x: 14; y: 52; height: 19; text: page.s.form_status; color: page.c.FG }
                RakLabel { x: 71; y: 52; height: 19; text: page.model.profile.status || "…"; color: page.model.profile.pending?page.c.FG_MUTED:page.model.profile.active?page.c.GREEN_STATUS:page.c.RED_STATUS; font.weight: Font.DemiBold }
                RakLabel { x: 14; y: 79; height: 19; text: page.s.form_details; color: page.c.FG }
                RakLabel { x: 71; y: 79; width: parent.width - 85; height: 19; text: page.model.profile.details || ""; color: page.c.FG_MUTED; font.pixelSize: 12; elide: Text.ElideRight }
            }
            Card {
                height: 95
                Repeater {
                    model: [{title: page.s.lbl_license, icon: "legal", action: "license"}, {title: page.s.lbl_credits, icon: "user", action: "credit"}]
                    delegate: Item {
                        required property var modelData
                        required property int index
                        x: 14; y: 12 + index * 43; width: parent.width - 28; height: 26
                        WorkspaceNativeIcon {
                            y: 3
                            iconKey: parent.modelData.icon
                            iconSize: 20
                            strokeColor: page.c.FG
                            accentColor: page.c.accent_hex
                            isLight: (page.c && page.c.is_light !== undefined) ? page.c.is_light : false
                        }
                        RakLabel { x: 30; height: 26; text: parent.modelData.title || ""; color: page.c.FG; font.weight: Font.DemiBold }
                        SmallButton { anchors.right: parent.right; width: 55; height: 26; text: page.s.btn_view; onClicked: page.model.dispatch(parent.modelData.action, {}) }
                    }
                }
            }
        }
    }
    PageRevealMask { anchors.fill: parent; motion: page.presentationMotion; page: page; topInset: headerGlass.height; bottomInset: 0 }
    }
    GlassBand { id: headerGlass; objectName: "settingsHeaderGlass"; z: 1; width: parent.width; height: 68; model: page.model; backdropSource: settingsSurface; origin: Qt.point(0,0) }
    PageFooter {
        id: footer
        visible: false
        height: 0
        width: parent.width
        anchors.bottom: parent.bottom
        model: page.model
        showProfile: false
    }
}
