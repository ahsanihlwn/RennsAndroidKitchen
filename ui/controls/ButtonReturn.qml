// SPDX-License-Identifier: MPL-2.0
import QtQuick
import "../motion.js" as Motion

Item {
    id: returning
    required property var popup
    function prepare() { destination.scheduleUpdate() }
    Loader {
        objectName: "returningButtonCaption"
        readonly property point center: returning.popup.returnCaptionCenter
        width: returning.popup.sourceRect.width; height: returning.popup.sourceRect.height
        x: center.x-width/2; y: center.y-height/2
        scale: returning.popup.returnCaptionScale
        // Keep the settled morph underneath the incoming opaque button.
        // Fading both layers attenuates the same glyph twice (25% at midpoint).
        opacity: (returning.popup.returnToSelection ? 1 : Motion.opacity(returning.popup.progress,0,1,[[.12,0],[.55,1]]))*returning.popup.returnUnderlay
        sourceComponent: !returning.popup.sourceItem ? null
            : typeof returning.popup.sourceItem.returnCaption!=="undefined" ? returning.popup.sourceItem.returnCaption
            : typeof returning.popup.sourceItem.morphCaption!=="undefined" ? returning.popup.sourceItem.morphCaption : null
    }
    Item {
        objectName: "returningButtonDestination"
        x: returning.popup.sourceRect.x; y: returning.popup.sourceRect.y
        width: returning.popup.sourceRect.width; height: returning.popup.sourceRect.height
        opacity: returning.popup.returnBlend
        layer.enabled: true
        SmoothRectangle { anchors.fill: parent; color: returning.popup.sourceBackdropColor }
        ShaderEffectSource {
            id: destination
            anchors.fill: parent
            sourceItem: returning.popup.sourceItem
            live: false; hideSource: false
        }
    }
}
