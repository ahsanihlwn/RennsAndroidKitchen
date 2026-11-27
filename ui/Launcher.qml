// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "controls"

Window {
    id: window
    required property var uiModel
    readonly property var colors: uiModel.colors
    readonly property var strings: uiModel.strings
    width: 1080; height: 700
    minimumWidth: 550; minimumHeight: 400
    visible: true
    color: colors.BG
    title: strings.windowTitle
    property bool managedClose: false
    readonly property Item backdropSurface: contentLoader.item ? contentLoader.item.backdropSurface : startupBackground
    property var dialogStack: null
    property var packageBackend: null
    property var profileBackend: null
    property var authBackend: null
    readonly property var managedDialogs: contentLoader.item ? contentLoader.item.managedDialogs : []
    property url appearanceFrame: ""
    property int appearanceDuration: 240
    function startAppearanceFade() { appearance.start() }
    readonly property bool nativeDialogActive: contentLoader.item ? contentLoader.item.nativeDialogActive : false
    onClosing: event => {
        if (managedClose) {
            event.accepted = false
            uiModel.dispatch("quit", {})
        }
    }

    property string previousPage: "projects"
    Component.onCompleted: previousPage=uiModel.page
    function pageItem(name) { return contentLoader.item ? contentLoader.item.pageItem(name) : null }
    property bool contentReady: true
    property bool asynchronousContent: false
    property bool splashActive: asynchronousContent && uiModel.startupActive
    property bool contentRevealed: !asynchronousContent
    property bool pendingReveal: false
    readonly property bool contentLoaded: contentLoader.status===Loader.Ready
    signal contentPrepared()
    signal contentFailed()
    function revealSplash() { revealContent() }
    function beginSkeletonDissolve() {
        pendingReveal = false
        contentRevealed = true
        if (uiModel.settings.animations) revealDoneTimer.restart()
        else uiModel.dispatch("startupRevealed", {})
    }
    function revealContent() {
        if (contentRevealed || pendingReveal) return
        if (!uiModel.settings.animations) {
            if (splash.visible) splash.reveal()
            beginSkeletonDissolve()
            return
        }
        if (splash.visible) {
            pendingReveal = true
            splash.reveal()
        } else {
            beginSkeletonDissolve()
        }
    }
    Timer { id: splashTimer; interval: window.uiModel.settings.animations ? 340 : 0; running: window.splashActive; onTriggered: splash.reveal() }
    Timer { id: skeletonHoldTimer; interval: 80; onTriggered: window.beginSkeletonDissolve() }
    Timer { id: revealDoneTimer; interval: 380; onTriggered: window.uiModel.dispatch("startupRevealed", {}) }
    SmoothRectangle { id: startupBackground; anchors.fill: parent; color: window.colors.BG }
    Loader {
        id: contentLoader
        objectName: "launcherContentLoader"
        anchors.fill: parent
        active: window.contentReady
        asynchronous: window.asynchronousContent
        // Pre-render real content (LiveGlass FBOs & icons) behind the opaque skeleton
        // so the first frame of the dissolve has zero shader/texture upload hitch.
        opacity: window.contentLoaded ? 1 : 0
        enabled: window.contentRevealed
        function loadContent() {
            if(!source.toString().length)setSource(Qt.resolvedUrl("LauncherContent.qml"), {hostWindow: window})
        }
        onActiveChanged: if(active)loadContent()
        Component.onCompleted: if(active)loadContent()
        onLoaded: window.contentPrepared()
        onStatusChanged: if(status===Loader.Error)window.contentFailed()
    }
    LauncherSkeleton {
        id: launcherSkeleton
        objectName: "launcherSkeleton"
        anchors.fill: parent
        z: 50
        uiModel: window.uiModel
        revealing: window.contentRevealed
        opacity: window.contentRevealed ? 0 : 1
        visible: opacity > 0.001
        layer.enabled: visible && window.contentRevealed
        layer.smooth: true
        Behavior on opacity { NumberAnimation { duration: window.uiModel.settings.animations ? 380 : 0; easing.type: Easing.InOutCubic } }
    }
    StartupSplash {
        id: splash
        anchors.fill: parent; z: 100
        model: window.uiModel
        active: window.splashActive
        onRevealed: {
            window.splashActive = false
            if (window.pendingReveal) {
                if (window.uiModel.settings.animations) skeletonHoldTimer.restart()
                else window.beginSkeletonDissolve()
            }
        }
    }
    AppearanceFade { id: appearance; objectName: "appearanceFade"; anchors.fill: parent; z: 110; source: window.appearanceFrame; duration: window.appearanceDuration; animationsEnabled: window.uiModel.settings.animations }
    onWidthChanged: if(contentLoader.item)contentLoader.item.cancelPageMotion()
    onHeightChanged: if(contentLoader.item)contentLoader.item.cancelPageMotion()
}
