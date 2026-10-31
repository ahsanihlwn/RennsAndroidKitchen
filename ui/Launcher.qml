// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import QtQuick.Controls as C
import QtQuick.Dialogs as D
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
    readonly property bool contentLoaded: contentLoader.status===Loader.Ready
    signal contentPrepared()
    signal contentFailed()
    function revealSplash() { splash.reveal() }
    SmoothRectangle { id: startupBackground; anchors.fill: parent; color: window.colors.BG }
    Loader {
        id: contentLoader
        objectName: "launcherContentLoader"
        anchors.fill: parent
        active: window.contentReady
        asynchronous: window.asynchronousContent
        function loadContent() {
            if(!source.toString().length)setSource(Qt.resolvedUrl("LauncherContent.qml"), {hostWindow: window})
        }
        onActiveChanged: if(active)loadContent()
        Component.onCompleted: if(active)loadContent()
        onLoaded: window.contentPrepared()
        onStatusChanged: if(status===Loader.Error)window.contentFailed()
    }
    StartupSplash {
        id: splash
        anchors.fill: parent; z: 100
        model: window.uiModel
        onRevealed: window.uiModel.dispatch("startupRevealed", {})
    }
    AppearanceFade { id: appearance; objectName: "appearanceFade"; anchors.fill: parent; z: 110; source: window.appearanceFrame; duration: window.appearanceDuration; animationsEnabled: window.uiModel.settings.animations }
    onWidthChanged: if(contentLoader.item)contentLoader.item.cancelPageMotion()
    onHeightChanged: if(contentLoader.item)contentLoader.item.cancelPageMotion()
}
