// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Window

RakButton {
    id: control
    required property var chat
    required property var preferences
    readonly property var colors: preferences.colors
    readonly property var tr: chat.labels(preferences.strings.languageKey)
    property bool showReport: false
    property bool showLimits: false
    readonly property bool reportAvailable: !(chat.limits.groups || []).length && !!chat.limits.report
    onReportAvailableChanged: if(!reportAvailable)showReport=false
    function cleanModelLabel(val) {
        if(!val)return ""
        return String(val).replace(/^opencode\//i, "")
    }
    height: 26; font.pixelSize: 11; leftPadding: 6; rightPadding: 6; cornerRadius: 7; edgeWidth: 0
    text: cleanModelLabel((chat.models.find(m=>m.id===chat.model)||{}).label || chat.model) || tr.automatic
    labelColor: colors.FG2; hoverLabelColor: colors.FG; hoverFillColor: colors.b06
    enabled: true // Limits remain readable while model changes are locked.
    contentItem: Item {
        implicitWidth: caption.implicitWidth+15
        Text { id: caption; width: parent.width-14; anchors.verticalCenter: parent.verticalCenter; text: control.text; font: control.font; color: control.labelColor; elide: Text.ElideRight }
        Image { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; width: 8; height: 8; sourceSize: Qt.size(16,16); rotation: 90; source: "image://rak/arrow_right?color="+encodeURIComponent(control.labelColor) }
    }
    function positionPopup() {
        const window=control.Window.window
        if(!window)return
        const anchor=control.mapToItem(window.contentItem,0,0)
        menu.x=Math.max(10,Math.min(anchor.x+control.width-menu.width,window.width-menu.width-10))
        menu.y=Math.max(10,Math.min(anchor.y-menu.height-8,window.height-menu.height-10))
    }
    function openChoices() {
        showLimits=false;showReport=false
        positionPopup();menu.open();chat.refreshEfforts()
        Qt.callLater(()=>{
            const index=chat.models.findIndex(model=>model.id===chat.model)
            modelScroller.contentItem.contentY=Math.max(0,Math.min(modelRows.implicitHeight-modelScroller.height,index*34+32-modelScroller.height))
        })
    }
    function windowLabel(bucket) {
        const minutes=Number(bucket.windowMinutes)
        if(minutes>0) {
            if(minutes===10080)return tr.quota_weekly
            if(minutes%1440===0)return tr.quota_days.replace("{n}",minutes/1440)
            if(minutes%60===0)return tr.quota_hours.replace("{n}",minutes/60)
            return tr.quota_minutes.replace("{n}",minutes)
        }
        return bucket.label==="primary" || bucket.label==="secondary"?tr.quota_window:bucket.label || tr.quota_window
    }
    function resetLabel(bucket) {
        const date=bucket.resetAt?new Date(bucket.resetAt*1000):new Date(bucket.reset || "")
        if(!isNaN(date.getTime()))return date.toLocaleString(Qt.locale(preferences.strings.languageKey),"d MMM, HH:mm")
        return bucket.reset || ""
    }
    function effortLabel(value) {return value?value.charAt(0).toUpperCase()+value.slice(1):tr.automatic}
    Connections {
        target: control.Window.window
        function onWidthChanged() {if(menu.opened)Qt.callLater(control.positionPopup)}
        function onHeightChanged() {if(menu.opened)Qt.callLater(control.positionPopup)}
    }
    onClicked: openChoices()
    C.Popup {
        id: menu; objectName: "agentModelPopup"
        parent: control.Window.window?control.Window.window.contentItem:null
        width: Math.min(304,control.Window.window?control.Window.window.width-20:304)
        height: Math.min(body.implicitHeight+20,control.Window.window?control.Window.window.height-24:500)
        padding: 10; modal: true; dim: false; focus: true
        closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside
        onHeightChanged: if(opened)control.positionPopup()
        background: SmoothRectangle { radius: 12; color: control.colors.BG2; border.width: 1; border.color: control.colors.b14 }
        C.ScrollView { anchors.fill: parent; clip: true
            C.ScrollBar.horizontal.policy: C.ScrollBar.AlwaysOff
            C.ScrollBar.vertical: RakScrollBar { width: 3; handleColor: control.colors.b20; hoverColor: control.colors.b35 }
            Column {
                id: body; width: menu.availableWidth; spacing: 0
                RakLabel { x: 9; height: 27; text: control.tr.model; font.pixelSize: 10; color: control.colors.FG_MUTED }
                C.ScrollView { id: modelScroller; width: parent.width; height: Math.min(196,modelRows.implicitHeight); clip: true
                    C.ScrollBar.horizontal.policy: C.ScrollBar.AlwaysOff
                    C.ScrollBar.vertical: RakScrollBar { width: 3; handleColor: control.colors.b20; hoverColor: control.colors.b35 }
                    Column { id: modelRows; width: body.width; spacing: 2
                        Repeater { model: control.chat.models
                            delegate: RakButton {
                                required property var modelData; objectName: "agentModel_"+(modelData.id || "__default__")
                                width: modelRows.width; height: 32; leftPadding: 9; rightPadding: 9; edgeWidth: 0; cornerRadius: 7
                                enabled: !control.chat.busy
                                fillColor: control.chat.model===modelData.id?control.colors.b07:"transparent"; hoverFillColor: control.colors.b09
                                contentItem: Item {
                                    RakLabel { width: parent.width-24; anchors.verticalCenter: parent.verticalCenter; text: control.cleanModelLabel(modelData.label || modelData.id); font.pixelSize: 11; color: control.colors.FG; elide: Text.ElideRight; toolTip: modelData.description || "" }
                                    Image {
                                        visible: control.chat.model===modelData.id
                                        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                        width: 13; height: 13; sourceSize: Qt.size(16, 16)
                                        source: "image://rak/check_white?color=" + encodeURIComponent(control.colors.FG2)
                                    }
                                }
                                onClicked: {control.chat.selectModel(modelData.id);menu.close()}
                            }
                        }
                    }
                }
                Item { width: parent.width; height: 40
                    RakLabel { x: 9; height: parent.height; text: control.tr.thinking_level; font.pixelSize: 11; color: control.colors.FG2 }
                    AgentChatChoice { objectName: "agentChatEffort"; anchors.right: parent.right; anchors.rightMargin: 4; anchors.verticalCenter: parent.verticalCenter; width: 112; height: 29
                        colors: control.colors; enabled: !control.chat.busy && control.chat.efforts.length>0
                        fillColor: control.colors.b04; disabledFillColor: control.colors.b03; labelColor: control.colors.FG2; leftPadding: 9; rightPadding: 9
                        text: control.effortLabel((control.chat.efforts.find(e=>e.id===control.chat.effort)||{}).label)
                        choices: control.chat.efforts.map(e=>({key:e.id || "__default__",label:control.effortLabel(e.label)})); currentKey: control.chat.effort || "__default__"
                        toolTip: control.chat.efforts.length?control.tr.thinking_level:control.tr.effort_unavailable
                        onChosen: key=>{control.chat.selectEffort(key==="__default__"?"":key);previewCaption=null}
                    }
                }
                SmoothRectangle { width: parent.width; height: 1; color: control.colors.b09 }
                Item { width: parent.width; height: 34
                    ViewerToolButton { objectName: "agentChatToggleLimits"; width: control.showLimits?parent.width-65:parent.width; height: parent.height; colors: control.colors; text: control.tr.account_limits; leftAligned: true; leftPadding: 9; rightPadding: 20; font.pixelSize: 10; labelColor: control.colors.FG2
                        onClicked: {control.showLimits=!control.showLimits;if(control.showLimits)control.chat.refreshUsage()}
                        Image { anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter; width: 8; height: 8; sourceSize: Qt.size(16,16); rotation: control.showLimits?90:0; source: "image://rak/arrow_right?color="+encodeURIComponent(control.colors.FG_MUTED) }
                    }
                    ViewerToolButton { objectName: "agentChatRefreshUsage"; visible: control.showLimits; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; colors: control.colors; text: control.tr.refresh_usage; font.pixelSize: 9; labelColor: control.colors.FG_MUTED; enabled: !control.chat.limitsLoading; onClicked: control.chat.refreshUsage() }
                }
                C.ScrollView { visible: control.showLimits; width: parent.width; height: visible?Math.min(170,quotaBody.implicitHeight):0; clip: true
                  C.ScrollBar.horizontal.policy: C.ScrollBar.AlwaysOff
                  C.ScrollBar.vertical: RakScrollBar { width: 3; handleColor: control.colors.b20; hoverColor: control.colors.b35 }
                  Column { id: quotaBody; width: body.width; spacing: 0
                RakLabel { visible: control.chat.limitsLoading && !(control.chat.limits.groups || []).length; x: 9; width: parent.width-18; text: control.tr.limits_loading; color: control.colors.FG_MUTED; font.pixelSize: 10; wrapMode: Text.WordWrap }
                Repeater { model: control.chat.limits.groups || []
                    delegate: Column { required property var modelData; x: 9; width: body.width-18; spacing: 0
                        RakLabel { visible: (control.chat.limits.groups || []).length>1 || !["account","codex"].includes(parent.modelData.label.toLowerCase()); width: parent.width; bottomPadding: 9; text: parent.modelData.label; toolTip: parent.modelData.scope || ""; font.pixelSize: 10; color: control.colors.FG_MUTED; wrapMode: Text.WordWrap }
                        Repeater { model: parent.modelData.buckets || []
                            delegate: Item { id: bucket; required property var modelData; width: parent.width; height: resetText.visible?62:44
                                RakLabel { objectName: "agentQuotaWindow"; x: 0; y: 0; width: parent.width*.45; text: control.windowLabel(bucket.modelData); font.pixelSize: 11; color: control.colors.FG2; elide: Text.ElideRight }
                                RakLabel { anchors.right: parent.right; y: 0; text: Number(bucket.modelData.remaining).toLocaleString(Qt.locale(control.preferences.strings.languageKey),'f',Number(bucket.modelData.remaining)%1?1:0)+"% "+control.tr.remaining; font.pixelSize: 10; color: control.colors.FG; horizontalAlignment: Text.AlignRight }
                                SmoothRectangle { x: 0; y: 25; width: parent.width; height: 4; radius: 2; color: control.colors.b08
                                    SmoothRectangle { width: parent.width*Math.max(0,Math.min(100,Number(bucket.modelData.remaining)))/100; height: parent.height; radius: 2; color: control.colors.FG_MUTED }
                                }
                                RakLabel { id: resetText; x: 0; y: 35; visible: !!(bucket.modelData.resetAt || bucket.modelData.reset); width: parent.width; text: control.tr.reset+" "+control.resetLabel(bucket.modelData); font.pixelSize: 9; color: control.colors.FG_MUTED; elide: Text.ElideRight }
                            }
                        }
                    }
                }
                RakLabel { visible: !control.chat.limitsLoading && !(control.chat.limits.groups || []).length; x: 9; width: parent.width-18; bottomPadding: 8; text: control.chat.limitsError || (control.chat.limits.report?control.tr.usage_only:control.tr.limits_unavailable); font.pixelSize: 10; color: control.colors.FG_MUTED; wrapMode: Text.WordWrap }
                ViewerToolButton { objectName: "agentChatViewUsage"; visible: control.reportAvailable; width: parent.width; colors: control.colors; text: control.tr.usage_details+(control.showReport?"  ⌃":"  ›"); leftAligned: true; leftPadding: 9; font.pixelSize: 10; labelColor: control.colors.FG_MUTED; onClicked: control.showReport=!control.showReport }
                C.ScrollView { visible: control.showReport && control.reportAvailable; width: parent.width; height: visible?Math.min(150,usageText.implicitHeight+8):0; clip: true
                    C.TextArea { id: usageText; objectName: "agentChatUsageReport"; text: control.chat.limits.report || ""; readOnly: true; selectByMouse: true; wrapMode: TextEdit.Wrap; textFormat: TextEdit.PlainText; padding: 4; font.family: "Consolas"; font.pixelSize: 10; color: control.colors.FG2; background: null }
                }
                  }
                }
                ViewerToolButton { objectName: "agentChatRefreshModels"; colors: control.colors; text: control.tr.retry; visible: !!control.chat.modelCatalogError; onClicked: control.chat.retryModels() }
            }
        }
    }
}
