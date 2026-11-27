// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import "../motion.js" as Motion

C.Popup {
    id: popup
    property var sourceItem: null
    property Item backdropSource: null
    property var preferences: typeof rakPreferences!=="undefined" ? rakPreferences : null
    property bool effectsEnabled: !preferences || preferences.settings.effects!=="low"
    property bool animationsEnabled: !preferences || preferences.settings.animations
    property bool springMotion: true
    property bool morphOnClose: false
    property bool trackReturnHover: false
    property bool showOpenCaption: true
    property color panelColor: "#1a1a1a"
    property color tintBaseColor: panelColor
    property real tintAlpha: .5
    property real backdropDim: 0
    property color panelBorder: "#18ffffff"
    property color sourceBackdropColor: "transparent"
    property real panelRadius: springMotion ? 14 : 10
    property bool opening: true
    property bool immediateClose: false
    property bool upward: false
    property bool returnToSelection: false
    property var captionStart: null
    readonly property bool buttonReturning: morphOnClose && !opening
    property real closePhase: 1
    property real progress: 1
    property rect anchorInParent: Qt.rect(0,0,width,height)
    readonly property rect sourceRect: Qt.rect(anchorInParent.x-popup.x,anchorInParent.y-popup.y,anchorInParent.width,anchorInParent.height)
    property rect closeRect: Qt.rect(0,0,width,height)
    property real closeAlpha: 1
    property real launchScale: 1
    property color launchFill: "transparent"
    property color launchEdge: "transparent"
    readonly property real sourceRadius: sourceItem && typeof sourceItem.cornerRadius!=="undefined" ? sourceItem.cornerRadius : 6
    readonly property real collapseMotion: Motion.outCubic(Motion.clamp(progress/.86))
    readonly property real motion: springMotion ? Motion.spring(opening ? progress : Math.min(1,progress/.68)) : (buttonReturning ? collapseMotion : Motion.outCubic(progress))
    readonly property real choicePhase: opening ? motion : closePhase*(1-motion)
    readonly property rect surfaceRect: springMotion ? Motion.rect(opening ? sourceRect : closeRect,opening ? Qt.rect(0,0,width,height) : sourceRect,motion) : buttonReturning ? Motion.rect(closeRect,sourceRect,collapseMotion) : Motion.roundRect(Motion.rect(sourceRect,Qt.rect(0,0,width,height),choicePhase))
    readonly property real expansion: Motion.clamp((surfaceRect.width-sourceRect.width)/Math.max(1,(buttonReturning?closeRect.width:width)-sourceRect.width))
    readonly property real returnBlend: !buttonReturning ? 0 : springMotion ? Motion.clamp((progress-.68)/.17) : Motion.clamp((progress-.72)/.24)
    readonly property real returnUnderlay: returnBlend<1 ? 1 : 0
    readonly property real returnCaptionScale: springMotion ? (1+.25*expansion) : (1+.12*expansion)
    readonly property point returnCaptionCenter: captionStart!=null ? Qt.point(Motion.mix(captionStart.x,sourceRect.x+sourceRect.width/2,motion),Motion.mix(captionStart.y,sourceRect.y+sourceRect.height/2,motion)) : Qt.point(surfaceRect.x+surfaceRect.width/2,surfaceRect.y+surfaceRect.height/2)
    readonly property real eased: Motion.outCubic(progress)
    readonly property real formAlpha: buttonReturning ? (springMotion ? closeAlpha*Motion.opacity(eased,1,0,[[.25,0]])*(1-returnBlend) : closeAlpha*(1-Motion.clamp(progress/.28))) : (!showOpenCaption && opening) ? 1 : !springMotion ? Motion.clamp((choicePhase-.16)/.52) : opening ? Motion.opacity(progress,0,1,[[.65,0]]) : closeAlpha*Motion.opacity(eased,1,0,[[.25,0]])
    readonly property real captionAlpha: buttonReturning ? 0 : (!showOpenCaption && opening) ? 0 : !springMotion ? Motion.clamp(1-choicePhase/.28) : opening ? Motion.opacity(progress,1,0,[[.04,1],[.36,0]]) : Motion.opacity(progress,0,1,[[.12,0],[.55,1]])
    default property alias body: form.data
    popupType: C.Popup.Item
    padding: 0
    function anchorRect() {
        if (!popup.sourceItem) return Qt.rect(popup.width/2,popup.height/2,0,0)
        const rect=popup.sourceItem.mapToItem(popup.parent,0,0,popup.sourceItem.width,popup.sourceItem.height)
        return rect
    }
    function dismissImmediate() { immediateClose=true; close() }
    function returnHoverState() {
        const p=returnHover.point.position,r=sourceRect
        return returnHover.hovered && p.x>=r.x && p.x<r.x+r.width && p.y>=r.y && p.y<r.y+r.height ? 1 : 0
    }
    function syncReturnHover() {
        if(!visible || !trackReturnHover || !buttonReturning || !sourceItem || typeof sourceItem.morphHoverState==="undefined")return
        const state=returnHoverState()
        if(sourceItem.morphHoverState!==state) {
            sourceItem.morphHoverState=state
            buttonReturn.prepare()
        }
    }
    onAboutToShow: {
        immediateClose=false
        returnToSelection=false
        captionStart=null
        launchScale=springMotion && sourceItem && typeof sourceItem.pressScale!=="undefined" ? sourceItem.pressScale : 1
        if(springMotion && sourceItem && sourceItem.background) {
            launchFill=sourceItem.background.color
            launchEdge=sourceItem.background.border.color
        }
        if (!springMotion && sourceItem && typeof sourceItem.resetPressFeedback==="function") sourceItem.resetPressFeedback()
        opening=true
        // Preserve the pressed bounds, then stop the independent release
        // animation before the morph takes ownership of the button.
        anchorInParent=anchorRect()
        if(springMotion && sourceItem && typeof sourceItem.resetPressFeedback==="function") sourceItem.resetPressFeedback()
        progress=animationsEnabled?0:1
        caption.scheduleUpdate()
        if (sourceItem) {
            if (typeof sourceItem.morphHoverState!=="undefined") sourceItem.morphHoverState=sourceItem.hovered?1:0
            sourceItem.pressFeedback=false
            sourceItem.morphHidden=true
        }
    }
    onAboutToHide: {
        if (springMotion && opening && progress<1) immediateClose=true
        closePhase=choicePhase
        closeRect=surfaceRect; closeAlpha=formAlpha
        opening=false; progress=0
        if (sourceItem && typeof sourceItem.resetPressFeedback==="function") sourceItem.resetPressFeedback()
        anchorInParent=anchorRect()
        if (buttonReturning) {
            if (sourceItem && typeof sourceItem.morphHoverState!=="undefined") sourceItem.morphHoverState=trackReturnHover?returnHoverState():sourceItem.hovered?1:0
            buttonReturn.prepare()
        }
    }
    onClosed: if (sourceItem) {
        sourceItem.morphHidden=false
        if (typeof sourceItem.morphHoverState!=="undefined") sourceItem.morphHoverState=-1
    }
    enter: Transition { NumberAnimation { target: popup; property: "progress"; from: 0; to: 1; duration: popup.animationsEnabled ? (popup.springMotion?440:240) : 0; easing.type: Easing.Linear } }
    exit: Transition { NumberAnimation { target: popup; property: "progress"; from: 0; to: 1; duration: popup.animationsEnabled && !popup.immediateClose ? (popup.springMotion ? 440 : popup.morphOnClose ? Math.max(120,Math.round(250*Math.max(0.2,popup.closePhase))) : Math.max(60,Math.round(180*popup.closePhase))) : 0; easing.type: Easing.Linear } }
    background: Item {
        LiveGlass {
            id: glass
            objectName: "popupGlass"
            preferences: popup.preferences
            x: popup.surfaceRect.x; y: popup.surfaceRect.y; width: popup.surfaceRect.width; height: popup.surfaceRect.height
            cornerRadius: popup.buttonReturning ? (popup.springMotion ? 7+7*(1-Motion.clamp(popup.motion)) : Motion.mix(popup.panelRadius,popup.sourceRadius,popup.motion)) : popup.springMotion ? 7*popup.launchScale+(popup.panelRadius-7*popup.launchScale)*Motion.clamp(popup.opening?popup.motion:1-popup.motion) : popup.sourceRadius+(popup.panelRadius-popup.sourceRadius)*popup.choicePhase
            opacity: popup.buttonReturning ? (popup.springMotion ? Motion.opacity(popup.eased,0,0,[[.25,1],[.30,1],[.65,0]]) : (1-Motion.clamp((popup.progress-.46)/.38)))*popup.returnUnderlay : 1
            backdropSource: popup.backdropSource
            effectsEnabled: popup.effectsEnabled
            backdropDim: popup.backdropDim
            origin: Qt.point(popup.x+x,popup.y+y)
            sampleRect: Qt.rect(popup.x-32,popup.y-32,popup.width+64,popup.height+64)
            flatColor: popup.panelColor
            tintColor: Qt.rgba(popup.tintBaseColor.r,popup.tintBaseColor.g,popup.tintBaseColor.b,popup.tintAlpha)
            edgeColor: popup.springMotion ? popup.panelBorder : Qt.rgba(popup.panelBorder.r, popup.panelBorder.g, popup.panelBorder.b, popup.panelBorder.a * (popup.buttonReturning ? (1 - Motion.clamp(popup.progress / .45)) : Motion.clamp(popup.choicePhase / .45)))
        }
        SmoothRectangle {
            visible: popup.springMotion || popup.buttonReturning
            x: popup.surfaceRect.x; y: popup.surfaceRect.y; width: popup.surfaceRect.width; height: popup.surfaceRect.height
            radius: popup.buttonReturning ? glass.cornerRadius : 7
            color: popup.springMotion && popup.opening ? popup.launchFill : popup.sourceItem ? popup.sourceItem.fillColor : "transparent"
            border.width: (popup.sourceItem && typeof popup.sourceItem.edgeWidth!=="undefined") ? popup.sourceItem.edgeWidth : 1
            border.color: popup.springMotion && popup.opening ? popup.launchEdge : (popup.sourceItem && border.width>0 ? popup.sourceItem.edgeColor : "transparent")
            opacity: popup.buttonReturning ? (popup.springMotion ? Motion.opacity(popup.eased,0,1,[[.30,0],[.65,1]]) : Motion.clamp((popup.progress-.46)/.38))*popup.returnUnderlay : popup.springMotion ? (popup.opening ? Motion.opacity(popup.eased,1,0,[[.30,0]]) : Motion.opacity(popup.eased,0,1,[[.30,0],[.65,1]])) : popup.captionAlpha
        }
        MouseArea {
            anchors.fill: parent
            enabled: !popup.springMotion
            acceptedButtons: Qt.AllButtons
            onPressed: mouse => {
                // The reference tests the moving card, rather than the
                // popup's final rectangular bounds, during both transitions.
                const rect=popup.surfaceRect
                if (mouse.x<rect.x || mouse.x>=rect.x+rect.width || mouse.y<rect.y || mouse.y>=rect.y+rect.height) popup.close()
                mouse.accepted=true
            }
        }
    }
    contentItem: Item {
        HoverHandler {
            id: returnHover
            enabled: popup.trackReturnHover
            blocking: false
            onHoveredChanged: popup.syncReturnHover()
            onPointChanged: popup.syncReturnHover()
        }
        Item {
            id: clip
            objectName: "popupFormClip"
            readonly property real inset: popup.springMotion || popup.buttonReturning?0:4
            x: popup.surfaceRect.x+inset; y: popup.surfaceRect.y+inset
            width: Math.max(0,popup.surfaceRect.width-2*inset); height: Math.max(0,popup.surfaceRect.height-2*inset)
            clip: true
            Item {
                id: form
                objectName: "popupForm"
                x: popup.springMotion || popup.buttonReturning ? -clip.x : -clip.inset
                y: popup.springMotion || popup.buttonReturning ? -clip.y : popup.upward ? popup.surfaceRect.height-popup.height-clip.inset : -clip.inset
                width: popup.width; height: popup.height
                opacity: popup.formAlpha
            }
        }
        Item {
            objectName: "morphSourceCaption"
            width: openingCaption.active ? popup.sourceItem.width : caption.sourceItem?caption.sourceItem.width:0
            height: openingCaption.active ? popup.sourceItem.height : caption.sourceItem?caption.sourceItem.height:0
            x: popup.springMotion ? popup.surfaceRect.x+(popup.surfaceRect.width-width)/2 : popup.sourceRect.x+(popup.sourceRect.width-width)/2
            y: popup.springMotion ? popup.surfaceRect.y+(popup.surfaceRect.height-height)/2 : popup.sourceRect.y+(popup.sourceRect.height-height)/2
            scale: popup.springMotion ? popup.launchScale+(1.25-popup.launchScale)*popup.expansion : 1
            opacity: popup.captionAlpha
            layer.enabled: !popup.springMotion
            SmoothRectangle { anchors.fill: parent; color: popup.sourceBackdropColor; visible: !popup.springMotion }
            Loader {
                id: openingCaption; anchors.fill: parent
                active: popup.springMotion && !!popup.sourceItem && typeof popup.sourceItem.morphCaption!=="undefined"
                sourceComponent: active ? popup.sourceItem.morphCaption : null
            }
            ShaderEffectSource {
                id: caption
                anchors.fill: parent
                sourceItem: !openingCaption.active && popup.sourceItem ? (popup.springMotion?popup.sourceItem.contentItem:popup.sourceItem) : null
                live: false; hideSource: false
            }
        }
        ButtonReturn {
            id: buttonReturn
            anchors.fill: parent
            popup: popup
            visible: popup.buttonReturning
        }
    }
}
