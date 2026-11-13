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
        Image { anchors.verticalCenter: parent.verticalCenter; sourceSize.width: width; sourceSize.height: height; width: 20; height: 20; source: page.model.icon(parent.iconName, "") }
        RakLabel { x: 28; height: parent.height; text: parent.text; color: page.c.FG_LABEL; font.pixelSize: 14; font.weight: Font.DemiBold }
    }
    component SmallButton: ActionButton {
        colors: page.c; height: 28; cornerRadius: 6; leftPadding: 12; rightPadding: 12
        animatePress: page.model.settings.animations
    }
    component SettingText: Item {
        property string title: ""
        property string description: ""
        property string referenceDescription: description
        property real referenceWidth: width
        implicitHeight: label.height + 2 + descriptionLabel.height
        RakLabel { id: label; width: parent.width; height: 17; text: parent.title; color: page.c.FG; font.pixelSize: 12; font.weight: Font.DemiBold }
        RakLabel { id: descriptionLabel; y: 19; width: parent.width; height: Math.max(16, implicitHeight+(lineCount>1?1:0), referenceText.implicitHeight+(referenceText.lineCount>1?1:0)); text: parent.description; color: page.c.FG_DESC; font.pixelSize: 11; wrapMode: Text.WordWrap }
        RakLabel { id: referenceText; visible: false; width: parent.referenceWidth; text: parent.referenceDescription; font.pixelSize: 11; wrapMode: Text.WordWrap }
    }
    RakLabel { z: 2; opacity: page.headingOpacity; x: 32; y: 28; height: 44; text: page.s.hdr_settings; font.pixelSize: 22; font.weight: Font.DemiBold; color: page.c.FG }
    SmoothRectangle {
        id: settingsSurface
        readonly property rect inputBounds: Qt.rect(0,headerGlass.height,width,Math.max(0,height-headerGlass.height-footer.height))
        anchors.fill: parent; color: page.c.BG; clip: true
    Flickable {
        id: scroll
        objectName: "settingsScroll"
        x: 16; y: 77; width: parent.width - 32; height: Math.max(0, parent.height - 155)
        clip: !page.glassChrome; contentHeight: content.height + 32; boundsBehavior: Flickable.StopAtBounds
        Column {
            id: content
            x: 16; y: 8; width: parent.width - 32; spacing: 20
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
                SmallButton { anchors.right: changeLocation.left; anchors.rightMargin: 6; y: 44; width: 55; text: page.s.btn_reset; fillColor: "transparent"; labelColor: page.c.FG_DESC; onClicked: page.model.dispatch("resetLocation", {}) }
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
                    description: page.s.txt_current_style.replace("{style}",page.s.themeName)
                }
                SmallButton {
                    id: themeChange
                    objectName: "themeChangeButton"
                    anchors.right: parent.right; anchors.rightMargin: 14
                    y: Math.floor(themeText.y+(themeText.height-height)/2)
                    width: Math.max(98,implicitWidth); text: page.s.btn_change_short
                    onClicked: page.model.dispatch("themePicker",{})
                }
                SmoothRectangle { id: themeDivider; x: 14; y: themeText.y+themeText.height+18; width: parent.width - 28; height: 1; color: page.c.b06 }
                SettingText { id: effectText; x: 14; y: themeDivider.y+19; width: parent.width - 122; height: implicitHeight; title: page.s.lbl_effect_level; description: page.s.txt_effect_level_desc; referenceDescription: page.s.txt_blur_effect_desc; referenceWidth: parent.width-80 }
                ChoiceButton {
                    objectName: "effectLevelChoice"
                    anchors.right: parent.right; anchors.rightMargin: 14; y: Math.floor(effectText.y + (effectText.height - height) / 2)
                    width: Math.max(98,implicitWidth); height: 28; colors: page.c; text: page.model.settings.effects.charAt(0).toUpperCase() + page.model.settings.effects.slice(1)
                    choices: [{key: "low", label: "Low"}, {key: "medium", label: "Medium"}, {key: "high", label: "High"}]
                    currentKey: page.model.settings.effects; animatePress: page.model.settings.animations
                    onChosen: key => page.model.chooseSetting("effects", key)
                }
                Item {
                    id: blurOptions
                    objectName: "blurRadiusSettings"
                    property real reveal: page.model.settings.effects==="high"?1:0
                    property bool animateChanges: page.model.settings.animations
                    onAnimateChangesChanged: if(!animateChanges) Qt.callLater(function(){if(!blurOptions.animateChanges)blurOptions.reveal=Qt.binding(function(){return page.model.settings.effects==="high"?1:0})})
                    readonly property real fullHeight: 19+Math.max(blurText.height,28)
                    Behavior on reveal { enabled: blurOptions.animateChanges; NumberAnimation { duration: 240; easing.type: Easing.InOutCubic } }
                    visible: reveal>.001; opacity: reveal; enabled: reveal>.99; clip: reveal<1
                    x: 14; y: effectText.y+effectText.height+18; width: parent.width-28
                    height: fullHeight*reveal
                    SmoothRectangle { width: parent.width; height: 1; color: page.c.b06 }
                    SettingText { id: blurText; y: 19; width: parent.width-122; height: implicitHeight; title: page.s.lbl_glass_style; description: page.s.txt_glass_style_desc }
                    ChoiceButton {
                        objectName: "blurStyleChoice"
                        anchors.right: parent.right; y: 19+Math.floor((blurText.height-height)/2)
                        width: 98; height: 28; colors: page.c
                        currentKey: page.model.settings.blurRadius<12?"clear":"frost"
                        text: currentKey==="clear"?"Clear":"Frost"
                        choices: [{key:"clear",label:"Clear"},{key:"frost",label:"Frost"}]
                        animatePress: page.model.settings.animations
                        onChosen: key => page.model.chooseSetting("blurRadius",key==="clear"?6:17)
                    }
                }
                SmoothRectangle { id: animationDivider; x: 14; y: blurOptions.y+(blurOptions.fullHeight+18)*blurOptions.reveal; width: parent.width - 28; height: 1; color: page.c.b06 }
                SettingText { id: animationText; x: 14; y: animationDivider.y + 19; width: parent.width - 80; height: implicitHeight; title: page.s.lbl_animations; description: page.s.txt_animations_desc }
                RakSwitch {
                    objectName: "animationsSwitch"
                    anchors.right: parent.right; anchors.rightMargin: 14; y: animationText.y + (animationText.height - height) / 2
                    width: 42; height: 24; accentColor: page.c.neutralToggle; offColor: page.c.offToggle
                    checked: page.model.settings.animations; animationsEnabled: page.model.settings.animations
                    onToggled: page.model.chooseSetting("animations", checked)
                }
                SmoothRectangle { id: modeDivider; x: 14; y: animationText.y + animationText.height + 18; width: parent.width - 28; height: 1; color: page.c.b06 }
                SettingText { id: modeText; x: 14; y: modeDivider.y + 19; width: parent.width - 38 - projectMode.width; height: implicitHeight; title: page.s.lbl_project_mode; description: page.s.txt_tree_view_mode_desc }
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
                        Image { sourceSize.width: width; sourceSize.height: height; width: 20; height: 20; y: 3; source: page.model.icon(parent.modelData.icon, "") }
                        RakLabel { x: 30; height: 26; text: parent.modelData.title || ""; color: page.c.FG; font.weight: Font.DemiBold }
                        SmallButton { anchors.right: parent.right; width: 55; height: 26; text: page.s.btn_view; onClicked: page.model.dispatch(parent.modelData.action, {}) }
                    }
                }
            }
        }
    }
    PageRevealMask { anchors.fill: parent; motion: page.presentationMotion; page: page; topInset: headerGlass.height; bottomInset: footer.height }
    }
    GlassBand { id: headerGlass; objectName: "settingsHeaderGlass"; z: 1; width: parent.width; height: 77; model: page.model; backdropSource: settingsSurface; origin: Qt.point(0,0) }
    GlassBand { objectName: "settingsFooterGlass"; z: 1; y: parent.height-80; width: parent.width; height: 80; model: page.model; backdropSource: settingsSurface; origin: Qt.point(0,y) }
    PageFooter {
        id: footer
        z: 2
        width: parent.width; anchors.bottom: parent.bottom; model: page.model; showProfile: false
        ActionButton {
            objectName: "settingsBackButton"
            width: 99; height: 32; colors: page.c; text: page.s.btn_back
            iconSource: page.model.icon("arrow_right", page.c.FG2); iconWidth: 15; iconHeight: 15
            animatePress: page.model.settings.animations; onClicked: page.model.back()
        }
    }
}
