// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Effects
import QtQuick.Window

Item {
    id: glass
    property var preferences: typeof rakPreferences!=="undefined" ? rakPreferences : null
    property Item backdropSource: null
    property bool effectsEnabled: !preferences || preferences.settings.effects!=="low"
    property bool animateChanges: false
    property int transitionDuration: 240
    property real effectMix: effectsEnabled ? 1 : 0
    readonly property bool wantsRefraction: refractionEnabled && (!preferences || preferences.settings.effects==="high")
    property real refractionMix: wantsRefraction ? 1 : 0
    Behavior on effectMix { enabled: glass.animateChanges; NumberAnimation { duration: glass.transitionDuration; easing.type: Easing.InOutCubic } }
    Behavior on refractionMix { enabled: glass.animateChanges; NumberAnimation { duration: glass.transitionDuration; easing.type: Easing.InOutCubic } }
    Behavior on renderedBlurRadius { enabled: glass.animateChanges; NumberAnimation { duration: glass.transitionDuration; easing.type: Easing.InOutCubic } }
    onAnimateChangesChanged: if(!animateChanges) Qt.callLater(settleChanges)
    function settleChanges() {
        if(animateChanges)return
        effectMix=Qt.binding(function(){return glass.effectsEnabled?1:0})
        refractionMix=Qt.binding(function(){return glass.wantsRefraction?1:0})
        renderedBlurRadius=Qt.binding(function(){return glass.blurRadius})
    }
    property color flatColor: "#1a1a1a"
    property color tintColor: "#801a1a1a"
    property color edgeColor: "#18ffffff"
    property real cornerRadius: 14
    property real blurRadius: preferences && preferences.effectiveBlurRadius!==undefined ? preferences.effectiveBlurRadius : 18
    property real renderedBlurRadius: blurRadius
    // Every High glass surface uses the lens, including launcher chrome and
    // overlays. Medium retains ordinary blur through the same shared component.
    property bool refractionEnabled: !!preferences && preferences.settings.effects==="high"
    property real refractionHeight: 12
    property real refractionAmount: 24
    property real backdropDim: 0
    property point origin: Qt.point(0,0)
    property bool sampleInSourceCoordinates: false
    property string fadeEdge: ""
    property real fadeSize: 24
    readonly property int padding: 32
    property rect sampleRect: Qt.rect(origin.x-padding,origin.y-padding,width+2*padding,height+2*padding)
    // Default samples use window coordinates. Embedded glass can instead
    // follow its source's local viewport without tracking ancestor movement.
    readonly property rect sourceSampleRect: sampleInSourceCoordinates ? sampleRect : backdropSource && glass.Window.window
        ? backdropSource.mapFromItem(glass.Window.window.contentItem,sampleRect.x,sampleRect.y,sampleRect.width,sampleRect.height) : sampleRect
    readonly property bool live: texture.live
    readonly property bool rendering: visible && (effectsEnabled || effectMix>.001) && backdropSource !== null && width>0 && height>0
    readonly property bool refracting: rendering && refractionMix>.001 && refractionHeight>0 && refractionAmount>0
    readonly property bool edgeFading: fadeEdge!=="" && fadeSize>0 && rendering
    readonly property bool masked: cornerRadius>0 || edgeFading
    // The texture stays on the GPU: no screen grab, QImage readback, timer,
    // or frozen frame. Sample the page alone to avoid blur feedback.
    ShaderEffectSource {
        id: texture
        sourceItem: glass.rendering ? glass.backdropSource : null
        sourceRect: glass.sourceSampleRect
        live: true; hideSource: false; visible: false; smooth: true
    }
    SmoothRectangle {
        id: maskShape
        width: glass.width; height: glass.height; radius: glass.cornerRadius
        color: "white"; visible: false
    }
    Rectangle {
        id: fadeMask
        width: glass.width; height: glass.height
        visible: false; color: "transparent"
        gradient: Gradient {
            GradientStop {
                position: 0.0
                color: glass.fadeEdge==="top" ? Qt.rgba(1,1,1,1-glass.effectMix) : "#ffffffff"
            }
            GradientStop {
                position: glass.fadeEdge==="top" ? Math.min(.999, glass.fadeSize/Math.max(1,glass.height)) : Math.max(.001, 1-glass.fadeSize/Math.max(1,glass.height))
                color: "#ffffffff"
            }
            GradientStop {
                position: 1.0
                color: glass.fadeEdge==="bottom" ? Qt.rgba(1,1,1,1-glass.effectMix) : "#ffffffff"
            }
        }
    }
    ShaderEffectSource {
        id: maskTexture; sourceItem: glass.cornerRadius>0 ? maskShape : (glass.edgeFading ? fadeMask : null); live: true; hideSource: true; visible: false; smooth: true
        textureSize: glass.cornerRadius>0 ? Qt.size(Math.max(1,Math.ceil(glass.width*2*Screen.devicePixelRatio)),Math.max(1,Math.ceil(glass.height*2*Screen.devicePixelRatio))) : (glass.edgeFading ? Qt.size(Math.max(1,Math.ceil(glass.width*Screen.devicePixelRatio)),Math.max(1,Math.ceil(glass.height*Screen.devicePixelRatio))) : Qt.size(1,1))
    }
    Item {
        anchors.fill: parent
        clip: !glass.masked
        layer.enabled: glass.masked
        layer.smooth: true
        layer.effect: MultiEffect {
            maskEnabled: true; maskSource: maskTexture
            maskThresholdMin: .5; maskSpreadAtMin: 1
        }
        SmoothRectangle { anchors.fill: parent; color: glass.flatColor }
        Item {
            anchors.fill: parent
            opacity: glass.rendering ? glass.effectMix : 0
            layer.enabled: glass.animateChanges && glass.rendering
        MultiEffect {
            id: blurredBackdrop
            visible: glass.rendering
            x: glass.sampleRect.x-glass.origin.x; y: glass.sampleRect.y-glass.origin.y
            width: glass.sampleRect.width; height: glass.sampleRect.height
            source: texture
            blurEnabled: glass.renderedBlurRadius>0; blurMax: 64; blur: Math.max(0,Math.min(1,glass.renderedBlurRadius/26.5))
            autoPaddingEnabled: false
            // Refract the live blurred image before applying the panel's tint.
            // The extra GPU layer is allocated only for the liquid-glass mode.
            layer.enabled: glass.refracting
            layer.smooth: true
            layer.effect: ShaderEffect {
                objectName: "liquidRefractionShader"
                property var source
                property vector2d glassSize: Qt.vector2d(glass.width,glass.height)
                property vector2d sampleSize: Qt.vector2d(blurredBackdrop.width,blurredBackdrop.height)
                property vector2d sampleInset: Qt.vector2d(-blurredBackdrop.x,-blurredBackdrop.y)
                property real cornerRadius: glass.cornerRadius
                property real refractionHeight: glass.refractionHeight
                property real refractionAmount: glass.refractionAmount*glass.refractionMix
                fragmentShader: "../shaders/liquid_refraction.frag.qsb"
            }
        }
        SmoothRectangle { anchors.fill: parent; visible: glass.rendering; color: Qt.rgba(0,0,0,glass.backdropDim) }
        SmoothRectangle { anchors.fill: parent; color: glass.tintColor }
        }
    }
    SmoothRectangle { anchors.fill: parent; color: "transparent"; radius: glass.cornerRadius; border.color: glass.edgeColor }
}
