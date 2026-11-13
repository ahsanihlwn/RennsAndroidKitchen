// SPDX-License-Identifier: MPL-2.0
import QtQuick

RakCard {
    id: card
    ContentHitRegion { id: hitRegion; item: card }
    containmentMask: hitRegion.viewport?hitRegion:null
    required property var modelData
    required property int index
    required property var model
    property bool trash: false
    property bool interactive: true
    property bool presentationCopy: false
    readonly property real pathOffset: pathCaption.offset
    readonly property var c: model.colors
    property alias menuAnchor: more
    property bool cardPressed: false
    property real cardScale: 1
    readonly property bool transformed: Math.abs(cardScale-1)>.0001
    readonly property bool isDeleting: card.model.deletingPath === card.modelData.path
    scale: cardScale
    function animateCard(target,duration) {
        hoverAnimation.stop()
        if (!interactive || !model.settings.animations) { cardScale=1; return }
        hoverAnimation.to=target; hoverAnimation.duration=duration; hoverAnimation.start()
    }
    onCardPressedChanged: animateCard(cardPressed?.97:cardHover.hovered?.995:1,cardPressed?110:170)
    onInteractiveChanged: if (!interactive) { cardPressed=false; hoverAnimation.stop(); cardScale=1 }
    NumberAnimation { id: hoverAnimation; target: card; property: "cardScale"; easing.type: Easing.OutCubic }
    Connections { target: card.model; function onSettingsChanged() { if (!card.model.settings.animations) { hoverAnimation.stop(); card.cardScale=1 } } }
    objectName: (presentationCopy ? "projectMotionCard_" : "projectCard_") + modelData.path
    height: 66
    color: cardHover.hovered ? card.c.BG_SELECTED : card.c.BG2
    border.color: isDeleting ? "#ef4444" : (cardHover.hovered ? card.c.a30 : card.c.b07)
    border.width: isDeleting ? 1.5 : 1
    radius: 10
    HoverHandler {
        id: cardHover; enabled: card.interactive && !card.isDeleting; cursorShape: Qt.PointingHandCursor
        onHoveredChanged: if (!card.cardPressed) card.animateCard(hovered?.995:1,hovered?170:150)
    }
    HoverTip { label: card.trash?(card.modelData.toolTip || ""):""; hovering: cardHover.hovered }
    TapHandler {
        enabled: card.interactive && !card.model.busy && !card.isDeleting
        onPressedChanged: if (!pressed || point.position.x<card.width-90) card.cardPressed=pressed
        onTapped: if (!card.trash) card.model.dispatch("openProject", {"path": card.modelData.path})
    }
    Image { sourceSize.width: width; sourceSize.height: height;
        x: 22; y: 17; width: 32; height: 32
        source: card.model.icon(card.modelData.icon || "project_list", "")
    }
    RakLabel {
        transformed: card.transformed
        x: 71; y: 15; width: Math.max(0, card.width - (card.trash?238:272)); height: 20
        text: card.modelData.project_name; elide: Text.ElideRight
        toolTip: text
        font.pixelSize: 14; font.weight: Font.DemiBold; color: card.c.FG_BRIGHT
    }
    RakLabel {
        transformed: card.transformed
        x: 71; y: 35; width: Math.max(0, card.width - (card.trash?238:272)); height: 16
        text: card.modelData.port1 + (card.modelData.port2 ? " - " + card.modelData.port2 : "")
        toolTip: text
        font.pixelSize: 11; color: card.c.FG2; elide: Text.ElideRight
    }
    Item {
        anchors.right: parent.right; anchors.rightMargin: card.trash?59:93
        y: 17; width: 96; height: 33
        Image { sourceSize.width: width; sourceSize.height: height; x: 0; y: 1; width: 12; height: 12; source: card.model.icon("project_disk", "") }
        ScrollingPath {
            id: pathCaption
            objectName: "projectPath_"+card.modelData.path
            x: 17; y: 0; width: Math.min(parent.width - 17 - (card.modelData.pinned ? 17 : 0),Math.round(implicitTextWidth)+2); height: 14
            text: card.modelData.parentPath || card.modelData.path; clip: true
            color: card.c.FG3; moving: card.interactive && !card.presentationCopy
            transformed: card.transformed
            frozenOffset: card.presentationCopy?(card.modelData._scrollOffset || 0):0
        }
        PinMarker { visible: !!card.modelData.pinned; x: 17+pathCaption.width+5; y: 1; color: card.c.accent_hex; toolTip: card.model.strings.tooltip_pinned }
        RakLabel {
            transformed: card.transformed
            x: 0; y: 17; width: 96; height: 14
            text: card.modelData.sizeLabel || ""; font.pixelSize: 10; color: card.c.FG_MUTED
        }
    }
    RakButton {
        id: more
        toolTip: card.model.strings.menu_project_options
        objectName: "projectMenu_" + card.index
        x: parent.width - (card.trash?53:87); y: 18; width: 24; height: 28
        cornerRadius: 6; fillColor: "transparent"; hoverFillColor: card.c.a12
        iconSource: card.model.icon("project_more", ""); iconWidth: 14; iconHeight: 14; leftPadding: 0; rightPadding: 0
        animatePress: card.model.settings.animations
        enabled: card.interactive && !card.model.busy && !card.isDeleting
        onClicked: {
            const position = mapToItem(null, 0, height)
            card.model.dispatch("projectMenu", {path: card.modelData.path, id: card.modelData._trash_id || "", x: position.x, y: position.y})
        }
    }
    Item {
        id: deletionEffect
        objectName: "deleteProgress_" + card.modelData.path
        anchors.fill: parent
        visible: card.isDeleting
        z: 2
        clip: true

        SmoothRectangle {
            anchors.fill: parent
            radius: card.radius
            color: "#ef4444"
            opacity: 0.12
        }

        SmoothRectangle {
            anchors.fill: parent
            radius: card.radius
            color: "transparent"
            border.width: 1.5
            border.color: "#ef4444"
            opacity: 0.85
        }

        Rectangle {
            id: shimmerBeam
            width: Math.max(160, card.width * 0.35)
            height: parent.height
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.00; color: "#00ef4444" }
                GradientStop { position: 0.25; color: "#25ff6b6b" }
                GradientStop { position: 0.50; color: "#60ffa3a3" }
                GradientStop { position: 0.75; color: "#25ff6b6b" }
                GradientStop { position: 1.00; color: "#00ef4444" }
            }

            NumberAnimation on x {
                from: -shimmerBeam.width
                to: card.width
                duration: 1100
                loops: Animation.Infinite
                running: deletionEffect.visible && card.model.settings.animations
            }
        }
    }
    RakButton {
        x: parent.width - 57; y: 18; width: 28; height: 28
        visible: !card.trash
        toolTip: card.trash?card.model.strings.btn_restore_project:card.model.strings.menu_open_project
        cornerRadius: 8; fillColor: cardHover.hovered ? card.c.a18 : card.c.BG4; hoverFillColor: card.c.a18
        edgeColor: cardHover.hovered ? card.c.a30 : card.c.b05; hoverEdgeColor: card.c.a30
        iconSource: card.model.icon("arrow_right", card.c.FG)
        iconWidth: 12; iconHeight: 12; leftPadding: 0; rightPadding: 0
        enabled: card.interactive && !card.model.busy && !card.isDeleting
        onClicked: card.model.dispatch(card.trash ? "restoreProject" : "openProject", {path: card.modelData.path, id: card.modelData._trash_id || ""})
    }
}
