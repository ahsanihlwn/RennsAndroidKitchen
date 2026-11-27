// SPDX-License-Identifier: MPL-2.0
import QtQuick
import "../motion.js" as Motion

Item {
    id: scene
    required property var model
    property var pages: []
    property var actions: []
    property var oldIdentity: null
    property var newIdentity: null
    property var extras: []
    property var sourcePage: null
    property var targetPage: null
    property int direction: 1
    property bool active: false
    readonly property bool hasFooterMorph: actions.length > 0
    readonly property real footerProgress: Motion.inOutCubic(clock.elapsed / 280)
    readonly property real elapsed: clock.elapsed
    readonly property real profileProgress: Motion.outCubic(clock.elapsed / 380)
    objectName: "pageMotion"
    visible: active
    // Transitional copies are visual only; real page controls keep the input.
    enabled: false
    z: 20

    function pageRank(page) {
        for (let i = 0; i < pages.length; ++i) {
            if (pages[i] === page) return i
        }
        return 0
    }
    function involves(page) {
        if (!active || !page) return false
        if (targetPage === page) return true
        return sourcePage === page && elapsed < 165
    }
    function isExiting(page) {
        return active && sourcePage === page && elapsed < 165
    }
    function pageOpacity(page) {
        if (!active || sourcePage !== page) return 1
        return 1 - Motion.outCubic(Motion.clamp(elapsed / 160))
    }
    function pageOffsetY(page) {
        if (!active || sourcePage !== page) return 0
        return -12 * direction * Motion.outCubic(Motion.clamp(elapsed / 160))
    }
    function headerOpacity(page) {
        if (!active || targetPage !== page) return 1
        return Motion.outCubic(Motion.clamp((elapsed - 20) / 180))
    }
    function headerOffsetY(page) {
        if (!active || targetPage !== page) return 0
        return 11 * direction * (1 - Motion.outCubic(Motion.clamp((elapsed - 20) / 230)))
    }
    function itemOpacity(page, index) {
        if (!active || targetPage !== page) return 1
        const idx = Math.max(0, Math.min(7, index))
        const delay = 25 + idx * 18
        return Motion.outCubic(Motion.clamp((elapsed - delay) / 170))
    }
    function itemOffsetY(page, index) {
        if (!active || targetPage !== page) return 0
        const idx = Math.max(0, Math.min(7, index))
        const delay = 25 + idx * 18
        return 14 * direction * (1 - Motion.outCubic(Motion.clamp((elapsed - delay) / 220)))
    }
    function footerOpacity(page) {
        if (!active || targetPage !== page || hasFooterMorph) return 1
        return Motion.outCubic(Motion.clamp((elapsed - 45) / 180))
    }
    function footerOffsetY(page) {
        if (!active || targetPage !== page || hasFooterMorph) return 0
        return 9 * direction * (1 - Motion.outCubic(Motion.clamp((elapsed - 45) / 220)))
    }

    function restore() {
        for (let i = 0; i < pages.length; ++i) {
            if (pages[i].footer) pages[i].footer.opacity = 1
            if (pages[i].identityItem) pages[i].identityItem.opacity = 1
        }
    }
    function cancel() {
        clock.seek(clock.duration)
        active = false
        sourcePage = null
        targetPage = null
        actions = []
        restore()
    }
    function begin(source, target) {
        cancel()
        if (!model.settings.animations || !target || source === target) return
        sourcePage = source
        targetPage = target
        direction = source ? (pageRank(target) >= pageRank(source) ? 1 : -1) : 1
        const endpoints = source && source.identityEndpoint && target.identityEndpoint
            ? [source.identityEndpoint(scene), target.identityEndpoint(scene)]
            : [null, null]
        const shared = endpoints[0] && endpoints[1] && endpoints[0].card !== endpoints[1].card
        oldIdentity = shared ? endpoints[0] : null
        newIdentity = shared ? endpoints[1] : null
        let additional = []
        if (shared) {
            for (let e = 0; e < 2; ++e) {
                const endpoint = endpoints[e], other = endpoints[1 - e]
                for (const extra of (endpoint.extras || [])) {
                    const a = endpoint.surface, b = other.surface, r = extra.rect
                    const moved = Qt.rect(b.x + Math.round((r.x - a.x) * b.width / a.width), b.y + Math.round((r.y - a.y) * b.height / a.height), r.width, r.height)
                    additional.push({source: e === 1 ? moved : r, target: e === 1 ? r : moved, entering: e === 1, content: extra})
                }
            }
        }
        extras = additional
        let oldActions = source && source.footer ? source.footer.actionEndpoints(scene) : []
        let newActions = source && target.footer ? target.footer.actionEndpoints(scene) : []
        let paired = []
        if (oldActions.length > 0 && newActions.length > 0) {
            for (let i = 0; i < Math.max(oldActions.length, newActions.length); ++i)
                paired.push({old: oldActions[i] || null, next: newActions[i] || null})
        }
        actions = paired
        if (target.footer) target.footer.opacity = paired.length > 0 ? 0 : footerOpacity(target)
        if (newIdentity && target.identityItem) target.identityItem.opacity = 0
        active = true
        clock.start(380)
    }

    MotionClock {
        id: clock
        objectName: "pageMotionClock"
        onElapsedChanged: {
            if (scene.targetPage && scene.targetPage.footer) {
                scene.targetPage.footer.opacity = scene.hasFooterMorph
                    ? (elapsed >= 280 ? 1 : 0)
                    : scene.footerOpacity(scene.targetPage)
            }
            if (scene.targetPage && elapsed >= 380 && scene.targetPage.identityItem) scene.targetPage.identityItem.opacity = 1
        }
        onFinished: {
            scene.active = false
            scene.sourcePage = null
            scene.targetPage = null
            scene.actions = []
            scene.restore()
        }
    }

    Repeater {
        model: scene.actions
        delegate: Item {
            id: pair
            required property var modelData
            required property int index
            readonly property var old: modelData.old
            readonly property var next: modelData.next
            readonly property var source: old || next
            readonly property var destination: next || old
            readonly property bool enteringOnly: !old && Boolean(next)
            readonly property bool exitingOnly: Boolean(old) && !next
            readonly property bool morphing: Boolean(old) && Boolean(next)
            readonly property rect geometry: Motion.rect(source.rect, destination.rect, scene.footerProgress)
            visible: clock.elapsed < 280

            // Staggered slide + scale-up for entering buttons
            readonly property real enterDelay: pair.index * 25
            readonly property real enterElapsed: Math.max(0, clock.elapsed - enterDelay)
            readonly property real enterProgress: Motion.outCubic(Motion.clamp(enterElapsed / 220))
            readonly property real enterSlideX: enteringOnly ? (1.0 - enterProgress) * 22 : 0
            readonly property real enterScale: enteringOnly ? (0.86 + 0.14 * enterProgress) : 1.0

            // Exit animation for leaving buttons
            readonly property real exitSlideX: exitingOnly ? scene.footerProgress * 16 : 0
            readonly property real exitScale: exitingOnly ? (1.0 - 0.10 * scene.footerProgress) : 1.0

            SmoothRectangle {
                objectName: "footerOldSurface_" + pair.index
                x: pair.exitingOnly ? (pair.source.rect.x + pair.exitSlideX) : pair.geometry.x
                y: pair.geometry.y
                width: pair.geometry.width
                height: pair.geometry.height
                radius: 7
                scale: pair.exitingOnly ? pair.exitScale : 1.0
                transformOrigin: Item.Center
                color: pair.source.fill; border.color: pair.source.edge
                opacity: pair.old ? (1 - scene.footerProgress) : 0
            }
            SmoothRectangle {
                objectName: "footerNewSurface_" + pair.index
                x: pair.enteringOnly ? (pair.destination.rect.x + pair.enterSlideX) : pair.geometry.x
                y: pair.enteringOnly ? pair.destination.rect.y : pair.geometry.y
                width: pair.enteringOnly ? pair.destination.rect.width : pair.geometry.width
                height: pair.enteringOnly ? pair.destination.rect.height : pair.geometry.height
                radius: 7
                scale: pair.enteringOnly ? pair.enterScale : 1.0
                transformOrigin: Item.Center
                color: pair.destination.fill; border.color: pair.destination.edge
                opacity: pair.enteringOnly ? pair.enterProgress : (pair.next ? scene.footerProgress : 0)
            }
            RakButton {
                objectName: "footerOldCaption_" + pair.index
                x: pair.source.rect.x + (pair.exitingOnly ? pair.exitSlideX : 0)
                y: pair.source.rect.y
                width: pair.source.rect.width
                height: pair.source.rect.height
                scale: pair.exitingOnly ? pair.exitScale : 1.0
                transformOrigin: Item.Center
                text: pair.source.text
                iconKey: pair.source.iconKey || ""
                iconSource: pair.source.icon
                iconWidth: pair.source.iconWidth
                iconHeight: pair.source.iconHeight
                labelColor: pair.source.color; font: pair.source.font; enabled: false; disabledLabelColor: labelColor
                opacity: pair.old ? (pair.next ? Motion.opacity(scene.footerProgress, 1, 0, [[.45, 0]]) : (1 - scene.footerProgress)) : 0
            }
            RakButton {
                objectName: "footerNewCaption_" + pair.index
                x: pair.destination.rect.x + (pair.enteringOnly ? pair.enterSlideX : 0)
                y: pair.destination.rect.y
                width: pair.destination.rect.width
                height: pair.destination.rect.height
                scale: pair.enteringOnly ? pair.enterScale : 1.0
                transformOrigin: Item.Center
                text: pair.destination.text
                iconKey: pair.destination.iconKey || ""
                iconSource: pair.destination.icon
                iconWidth: pair.destination.iconWidth
                iconHeight: pair.destination.iconHeight
                labelColor: pair.destination.color; font: pair.destination.font; enabled: false; disabledLabelColor: labelColor
                opacity: pair.enteringOnly ? pair.enterProgress : (pair.next ? (pair.old ? Motion.opacity(scene.footerProgress, 0, 1, [[.25, 0], [.65, 1]]) : scene.footerProgress) : 0)
            }
        }
    }
    Item {
        id: identity
        visible: !!scene.oldIdentity && !!scene.newIdentity && clock.elapsed < 380
        readonly property var old: scene.oldIdentity || ({surface: Qt.rect(0, 0, 0, 0), avatar: Qt.rect(0, 0, 0, 0), name: Qt.rect(0, 0, 0, 0), status: Qt.rect(0, 0, 0, 0), nameSize: 12, statusSize: 11, card: false})
        readonly property var next: scene.newIdentity || old
        readonly property rect surface: Motion.rect(old.surface, next.surface, scene.profileProgress)
        readonly property rect avatar: Motion.rect(old.avatar, next.avatar, scene.profileProgress)
        SmoothRectangle {
            x: identity.surface.x; y: identity.surface.y; width: identity.surface.width; height: identity.surface.height; radius: 10
            color: scene.model.colors.BG2; border.color: scene.model.colors.b08
            opacity: Motion.mix(Number(identity.old.card), Number(identity.next.card), scene.profileProgress)
        }
        RoundedImage {
            x: identity.avatar.x; y: identity.avatar.y; width: identity.avatar.width; height: identity.avatar.width
            source: scene.model.profile.avatar || ""
            samplingExtent: 48
        }
        Image {
            readonly property var old: identity.old.badge
            readonly property var next: identity.next.badge
            readonly property rect rect: old && next ? Motion.rect(old, next, scene.profileProgress) : Qt.rect(0, 0, 0, 0)
            x: rect.x; y: rect.y; width: rect.width; height: rect.width
            visible: !!old && !!next
            source: visible ? scene.model.icon(scene.model.profile.badge, "") : ""; sourceSize: Qt.size(18, 18)
        }
        Repeater {
            model: scene.extras
            delegate: Item {
                id: extra
                required property var modelData
                readonly property rect rect: Motion.rect(modelData.source, modelData.target, scene.profileProgress)
                x: rect.x; y: rect.y; width: rect.width; height: rect.height
                opacity: modelData.entering ? Motion.opacity(scene.profileProgress, 0, 1, [[.85, 0]]) : Motion.opacity(scene.profileProgress, 1, 0, [[.15, 0]])
                Image { anchors.centerIn: parent; width: 20; height: 20; visible: !!extra.modelData.content.icon; source: extra.modelData.content.icon || ""; sourceSize: Qt.size(20, 20) }
                RakLabel { anchors.fill: parent; visible: !!extra.modelData.content.text; text: extra.modelData.content.text || ""; color: extra.modelData.content.color || "transparent"; font: extra.modelData.content.font || Qt.font({pixelSize: 11}) }
            }
        }
        Repeater {
            model: ["name", "status"]
            delegate: Item {
                id: caption
                required property string modelData
                readonly property var source: identity.old[modelData]
                readonly property var destination: identity.next[modelData]
                readonly property string label: modelData === "name" ? (scene.model.profile.name || "") : (scene.model.profile.subtitle || "")
                RakLabel {
                    x: Motion.mix(caption.source.x, caption.destination.x, scene.profileProgress)
                    y: Motion.mix(caption.source.y, caption.destination.y + (caption.destination.height - caption.source.height) / 2, scene.profileProgress)
                    height: caption.source.height; text: caption.label
                    font.pixelSize: identity.old[caption.modelData + "Size"]; font.weight: caption.modelData === "name" ? Font.DemiBold : Font.Normal
                    color: caption.modelData === "name" ? scene.model.colors.FG : scene.model.colors.FG_MUTED
                    opacity: Motion.opacity(scene.profileProgress, 1, 0, [[.45, 0]])
                }
                RakLabel {
                    x: Motion.mix(caption.source.x, caption.destination.x, scene.profileProgress)
                    y: Motion.mix(caption.source.y + (caption.source.height - caption.destination.height) / 2, caption.destination.y, scene.profileProgress)
                    height: caption.destination.height; text: caption.label
                    font.pixelSize: identity.next[caption.modelData + "Size"]; font.weight: caption.modelData === "name" ? Font.DemiBold : Font.Normal
                    color: caption.modelData === "name" ? scene.model.colors.FG : scene.model.colors.FG_MUTED
                    opacity: Motion.opacity(scene.profileProgress, 0, 1, [[.25, 0], [.65, 1]])
                }
            }
        }
    }
}
