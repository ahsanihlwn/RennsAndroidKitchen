// SPDX-License-Identifier: MPL-2.0
import QtQuick

Item {
    id: footer
    // PageMotion owns the visible button copies until the native handoff.
    enabled: opacity>.99
    required property var model
    property bool showProfile: true
    default property alias actions: actionRow.data
    height: 80
    property alias identityItem: profile
    onVisibleChanged: if(visible)Qt.callLater(actionRow.forceLayout)
    function actionEndpoints(host) {
        actionRow.forceLayout()
        let result=[]
        for (let i=actionRow.children.length-1; i>=0; --i) {
            const button=actionRow.children[i]
            if (button.actionVisible===false || button.width<=0) continue
            result.push({rect:button.parent.mapToItem(host,button.x,button.y,button.width,button.height), text:button.text,
                         icon:button.iconSource,iconWidth:button.iconWidth,iconHeight:button.iconHeight,font:button.font,
                         fill:button.enabled?button.fillColor:button.disabledFillColor,edge:button.enabled?button.edgeColor:button.disabledEdgeColor,
                         color:button.enabled?button.labelColor:button.disabledLabelColor})
        }
        return result
    }
    function identityEndpoint(host) {
        if (!showProfile || !model.profile.name) return null
        const origin=profile.mapToItem(host,0,0)
        return {card:false,surface:Qt.rect(origin.x-6,origin.y-6,46+Math.max(profileName.implicitWidth+(profileBadge.visible?18:0),profileStatus.implicitWidth),44),
                avatar:avatar.mapToItem(host,0,0,avatar.width,avatar.height),
                name:profileName.mapToItem(host,0,0,profileName.width,profileName.height),
                status:profileStatus.mapToItem(host,0,0,profileStatus.width,profileStatus.height),badge:profileBadge.visible?profileBadge.mapToItem(host,0,0,14,14):null,nameSize:12,statusSize:10,extras:[]}
    }
    Item {
        id: profile
        visible: footer.showProfile && !!footer.model.profile.name
        x: 36; y: 16; width: Math.min(350, footer.width / 3); height: 32
        RoundedImage {
            id: avatar
            x: 0; y: 2; width: 28; height: 28
            source: footer.model.profile.avatar || ""
        }
        RakLabel {
            id: profileName
            x: 34; y: 0; height: 17; text: footer.model.profile.name || ""
            font.pixelSize: 12; font.weight: Font.DemiBold; color: footer.model.colors.FG_BRIGHT
        }
        RakLabel {
            id: profileStatus
            x: 34; y: 17; height: 15; text: footer.model.profile.subtitle || ""
            font.pixelSize: 10; color: footer.model.colors.FG_MUTED
        }
        Image { id: profileBadge; x: profileName.x+profileName.width+4; y: 1.5; width: 14; height: 14; visible: !!footer.model.profile.badge; source: visible?footer.model.icon(footer.model.profile.badge, ""):""; sourceSize: Qt.size(14,14) }
    }
    Row { id: actionRow; anchors.right: parent.right; anchors.rightMargin: 36; y: 16; spacing: 12 }
}
