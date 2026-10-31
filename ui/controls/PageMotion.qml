// SPDX-License-Identifier: MPL-2.0
import QtQuick
import "../motion.js" as Motion

Item {
    id: scene
    required property var model
    property var pages: []
    property var bands: []
    property var actions: []
    property var oldIdentity: null
    property var newIdentity: null
    property var extras: []
    property var targetPage: null
    property bool active: false
    readonly property real footerProgress: Motion.inOutCubic(clock.elapsed / 280)
    readonly property real elapsed: clock.elapsed
    readonly property real profileProgress: Motion.outCubic(clock.elapsed / 380)
    objectName: "pageMotion"
    visible: active
    // Transitional copies are visual only; real page controls keep the input.
    enabled: false
    z: 20

    function restore() {
        for (let i=0; i<pages.length; ++i) {
            pages[i].footer.opacity = 1
            if (pages[i].identityItem) pages[i].identityItem.opacity = 1
        }
    }
    function cancel() { clock.seek(clock.duration); active=false; restore() }
    function begin(source, target) {
        cancel()
        if (!model.settings.animations || !target) return
        targetPage = target
        const endpoints = source ? [source.identityEndpoint(scene), target.identityEndpoint(scene)] : [null,null]
        const shared = endpoints[0] && endpoints[1] && endpoints[0].card !== endpoints[1].card
        oldIdentity = shared ? endpoints[0] : null
        newIdentity = shared ? endpoints[1] : null
        let additional=[]
        if (shared) {
            for (let e=0;e<2;++e) {
                const endpoint=endpoints[e],other=endpoints[1-e]
                for (const extra of (endpoint.extras || [])) {
                    const a=endpoint.surface,b=other.surface,r=extra.rect
                    const moved=Qt.rect(b.x+Math.round((r.x-a.x)*b.width/a.width),b.y+Math.round((r.y-a.y)*b.height/a.height),r.width,r.height)
                    additional.push({source:e===1?moved:r,target:e===1?r:moved,entering:e===1,content:extra})
                }
            }
        }
        extras=additional
        let oldActions = source ? source.footer.actionEndpoints(scene) : []
        let newActions = source ? target.footer.actionEndpoints(scene) : []
        let paired=[]
        for (let i=0; i<Math.max(oldActions.length,newActions.length); ++i)
            paired.push({old:oldActions[i] || null, next:newActions[i] || null})
        actions=paired
        bands=target.revealBands()
        if (source) target.footer.opacity=0
        if (newIdentity && target.identityItem) target.identityItem.opacity=0
        active=true
        clock.start(Math.max(380,100+Math.max(0,bands.length-1)*60+280))
    }
    function coverPieces(top,bottom,includeChrome) {
        let hole = newIdentity ? newIdentity.surface : null
        if(!includeChrome)bottom=Math.min(bottom,height-80)
        if (bottom<=top) return []
        if (!hole || hole.y>=bottom || hole.y+hole.height<=top) return [Qt.rect(0,top,width,bottom-top)]
        let a=Math.max(top,hole.y), b=Math.min(bottom,hole.y+hole.height), pieces=[]
        if (a>top) pieces.push(Qt.rect(0,top,width,a-top))
        pieces.push(Qt.rect(0,a,hole.x,b-a))
        pieces.push(Qt.rect(hole.x+hole.width,a,Math.max(0,width-hole.x-hole.width),b-a))
        if (b<bottom) pieces.push(Qt.rect(0,b,width,bottom-b))
        return pieces
    }
    function opacityAt(page,y) {
        if(!active || targetPage!==page)return 1
        for(let index=0;index<bands.length;++index) {
            const band=bands[index]
            if(y>=band.top && y<band.bottom)
                return 1-Math.pow(1-Motion.clamp((clock.elapsed-100-index*60)/280),3)
        }
        return 1
    }
    MotionClock {
        id: clock
        objectName: "pageMotionClock"
        onElapsedChanged: {
            if (scene.targetPage && elapsed>=280) scene.targetPage.footer.opacity=1
            if (scene.targetPage && elapsed>=380 && scene.targetPage.identityItem) scene.targetPage.identityItem.opacity=1
        }
        onFinished: { scene.active=false; scene.restore() }
    }
    Repeater {
        model: scene.bands
        delegate: Item {
            id: band
            visible: !scene.targetPage || scene.targetPage.motionInBackdrop!==true
            required property var modelData
            required property int index
            readonly property real coverOpacity: Math.pow(1-Motion.clamp((clock.elapsed-100-index*60)/280),3)
            Repeater {
                model: scene.coverPieces(band.modelData.top,band.modelData.bottom)
                delegate: SmoothRectangle {
                    required property var modelData
                    x: modelData.x; y: modelData.y; width: modelData.width; height: modelData.height
                    color: scene.model.colors.BG; opacity: band.coverOpacity
                }
            }
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
            readonly property rect geometry: Motion.rect(source.rect,destination.rect,scene.footerProgress)
            visible: clock.elapsed<280
            SmoothRectangle {
                objectName: "footerOldSurface_"+pair.index
                x: pair.geometry.x; y: pair.geometry.y; width: pair.geometry.width; height: pair.geometry.height; radius: 7
                color: pair.source.fill; border.color: pair.source.edge
                opacity: pair.old ? 1-scene.footerProgress : 0
            }
            SmoothRectangle {
                objectName: "footerNewSurface_"+pair.index
                x: pair.geometry.x; y: pair.geometry.y; width: pair.geometry.width; height: pair.geometry.height; radius: 7
                color: pair.destination.fill; border.color: pair.destination.edge
                opacity: pair.next ? scene.footerProgress : 0
            }
            RakButton {
                objectName: "footerOldCaption_"+pair.index
                x: pair.source.rect.x; y: pair.source.rect.y; width: pair.source.rect.width; height: pair.source.rect.height
                text: pair.source.text; iconSource: pair.source.icon; iconWidth: pair.source.iconWidth; iconHeight: pair.source.iconHeight
                labelColor: pair.source.color; font: pair.source.font; enabled: false; disabledLabelColor: labelColor
                opacity: pair.old ? (pair.next ? Motion.opacity(scene.footerProgress,1,0,[[.45,0]]) : 1-scene.footerProgress) : 0
            }
            RakButton {
                objectName: "footerNewCaption_"+pair.index
                x: pair.destination.rect.x; y: pair.destination.rect.y; width: pair.destination.rect.width; height: pair.destination.rect.height
                text: pair.destination.text; iconSource: pair.destination.icon; iconWidth: pair.destination.iconWidth; iconHeight: pair.destination.iconHeight
                labelColor: pair.destination.color; font: pair.destination.font; enabled: false; disabledLabelColor: labelColor
                opacity: pair.next ? (pair.old ? Motion.opacity(scene.footerProgress,0,1,[[.25,0],[.65,1]]) : scene.footerProgress) : 0
            }
        }
    }
    Item {
        id: identity
        visible: !!scene.oldIdentity && !!scene.newIdentity && clock.elapsed<380
        readonly property var old: scene.oldIdentity || ({surface:Qt.rect(0,0,0,0),avatar:Qt.rect(0,0,0,0),name:Qt.rect(0,0,0,0),status:Qt.rect(0,0,0,0),nameSize:12,statusSize:11,card:false})
        readonly property var next: scene.newIdentity || old
        readonly property rect surface: Motion.rect(old.surface,next.surface,scene.profileProgress)
        readonly property rect avatar: Motion.rect(old.avatar,next.avatar,scene.profileProgress)
        SmoothRectangle {
            x: identity.surface.x; y: identity.surface.y; width: identity.surface.width; height: identity.surface.height; radius: 10
            color: scene.model.colors.BG2; border.color: scene.model.colors.b08
            opacity: Motion.mix(Number(identity.old.card),Number(identity.next.card),scene.profileProgress)
        }
        RoundedImage {
            x: identity.avatar.x; y: identity.avatar.y; width: identity.avatar.width; height: identity.avatar.width
            source: scene.model.profile.avatar || ""
            samplingExtent: 48
        }
        Image {
            readonly property var old: identity.old.badge
            readonly property var next: identity.next.badge
            readonly property rect rect: old && next?Motion.rect(old,next,scene.profileProgress):Qt.rect(0,0,0,0)
            x: rect.x; y: rect.y; width: rect.width; height: rect.width
            visible: !!old && !!next
            source: visible?scene.model.icon(scene.model.profile.badge, ""):""; sourceSize: Qt.size(18,18)
        }
        Repeater {
            model: scene.extras
            delegate: Item {
                id: extra
                required property var modelData
                readonly property rect rect: Motion.rect(modelData.source,modelData.target,scene.profileProgress)
                x: rect.x; y: rect.y; width: rect.width; height: rect.height
                opacity: modelData.entering?Motion.opacity(scene.profileProgress,0,1,[[.85,0]]):Motion.opacity(scene.profileProgress,1,0,[[.15,0]])
                Image { anchors.centerIn: parent; width: 20; height: 20; visible: !!extra.modelData.content.icon; source: extra.modelData.content.icon || ""; sourceSize: Qt.size(20,20) }
                RakLabel { anchors.fill: parent; visible: !!extra.modelData.content.text; text: extra.modelData.content.text || ""; color: extra.modelData.content.color || "transparent"; font: extra.modelData.content.font || Qt.font({pixelSize:11}) }
            }
        }
        Repeater {
            model: ["name","status"]
            delegate: Item {
                id: caption
                required property string modelData
                readonly property var source: identity.old[modelData]
                readonly property var destination: identity.next[modelData]
                readonly property string label: modelData==="name" ? (scene.model.profile.name || "") : (scene.model.profile.subtitle || "")
                RakLabel {
                    x: Motion.mix(caption.source.x,caption.destination.x,scene.profileProgress)
                    y: Motion.mix(caption.source.y,caption.destination.y+(caption.destination.height-caption.source.height)/2,scene.profileProgress)
                    height: caption.source.height; text: caption.label
                    font.pixelSize: identity.old[caption.modelData+"Size"]; font.weight: caption.modelData==="name" ? Font.DemiBold : Font.Normal
                    color: caption.modelData==="name" ? scene.model.colors.FG : scene.model.colors.FG_MUTED
                    opacity: Motion.opacity(scene.profileProgress,1,0,[[.45,0]])
                }
                RakLabel {
                    x: Motion.mix(caption.source.x,caption.destination.x,scene.profileProgress)
                    y: Motion.mix(caption.source.y+(caption.source.height-caption.destination.height)/2,caption.destination.y,scene.profileProgress)
                    height: caption.destination.height; text: caption.label
                    font.pixelSize: identity.next[caption.modelData+"Size"]; font.weight: caption.modelData==="name" ? Font.DemiBold : Font.Normal
                    color: caption.modelData==="name" ? scene.model.colors.FG : scene.model.colors.FG_MUTED
                    opacity: Motion.opacity(scene.profileProgress,0,1,[[.25,0],[.65,1]])
                }
            }
        }
    }
}
