// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtWebEngine
import QtWebChannel

Item {
    id: pane
    required property var session
    required property color fillColor
    property bool inputAllowed: true
    property bool terminalReady: false
    property bool focusPending: true
    property bool layoutSettled: true
    property bool contentReady: false
    property bool displayPending: false
    property int displayRequest: 0
    readonly property var webView: loader.item
    property string inspection: ""
    property bool inspectionPending: false
    function prepareDisplay() {
        if (contentReady || displayPending || !terminalReady || !visible || !layoutSettled || !webView || width < 20 || height < 20) return
        displayPending = true
        displayRequest++
        webView.runJavaScript("window.prepareTerminalDisplay && window.prepareTerminalDisplay("+displayRequest+");")
    }
    function invalidateDisplay() {
        displayPending = false
        displayRequest++
        Qt.callLater(prepareDisplay)
    }
    function focusInput() {
        if (!focusPending || !terminalReady || !inputAllowed || !visible || !enabled || !webView) return
        webView.forceActiveFocus(Qt.OtherFocusReason)
        webView.runJavaScript("window.term && window.term.focus();")
        focusPending = false
    }
    function activate() {
        focusPending = true
        if (webView) webView.runJavaScript("window.requestFit && window.requestFit();")
        Qt.callLater(prepareDisplay)
        Qt.callLater(focusInput)
    }
    onVisibleChanged: {
        contentReady = false
        invalidateDisplay()
        if (visible) activate()
    }
    onWidthChanged: if (!contentReady) invalidateDisplay()
    onHeightChanged: if (!contentReady) invalidateDisplay()
    onLayoutSettledChanged: if (layoutSettled) Qt.callLater(prepareDisplay)
    onInputAllowedChanged: if (inputAllowed) Qt.callLater(focusInput)
    Connections {
        target: pane.session.bridge
        function onTerminalReady() {
            pane.terminalReady = true
            Qt.callLater(pane.prepareDisplay)
            Qt.callLater(pane.focusInput)
        }
        function onTerminalRendered(request) {
            if (request !== pane.displayRequest || !pane.visible) return
            pane.displayPending = false
            if (pane.layoutSettled) pane.contentReady = true
        }
    }
    function inspect() {
        if(!loader.item || inspectionPending)return
        inspectionPending=true
        loader.item.runJavaScript("JSON.stringify({href:location.href,ready:document.readyState,terminal:typeof terminal,term:typeof term,body:document.body.innerText.slice(0,200)})",function(value){
            pane.inspection=value || ""
            pane.inspectionPending=false
        })
    }
    SmoothRectangle { anchors.fill: parent; color: pane.fillColor }
    Loader {
        id: loader
        anchors.fill: parent
        // Keep the native fill visible until Chromium has painted the fitted,
        // themed terminal. A ready WebChannel alone does not provide a frame.
        opacity: pane.contentReady ? 1 : 0
        active: pane.session.active
        sourceComponent: WebEngineView {
            id: view
            objectName: "quickTerminalView"
            backgroundColor: pane.fillColor
            settings.localContentCanAccessRemoteUrls: true
            settings.localContentCanAccessFileUrls: true
            settings.javascriptEnabled: true
            settings.scrollAnimatorEnabled: false
            webChannel: WebChannel { id: channel }
            Component.onCompleted: {
                channel.registerObject("bridge", pane.session.bridge)
                url = pane.session.url
            }
            onWidthChanged: runJavaScript("window.requestFit && window.requestFit();")
            onHeightChanged: runJavaScript("window.requestFit && window.requestFit();")
        }
    }
    DropArea {
        anchors.fill: parent
        function sourcePaths(event) {
            const mime=event.source && event.source.dragMime
            if(!mime)return []
            if(mime["text/uri-list"])return mime["text/uri-list"].split(/\r?\n/).filter(path=>path.length && path[0]!=="#")
            if(mime["application/x-rak-tree"]){try{return JSON.parse(mime["application/x-rak-tree"]).paths || []}catch(error){return []}}
            if(mime["application/x-rdt-archive"])return mime["application/x-rdt-archive"].split("\n")
            return []
        }
        onEntered: event => {event.accepted=sourcePaths(event).length>0 || event.hasUrls || event.hasText || event.formats.indexOf("application/x-rdt-archive")>=0}
        onDropped: event => {
            let paths=sourcePaths(event)
            if(!paths.length){
                if(event.hasUrls)paths=event.urls
                else if(event.formats.indexOf("application/x-rdt-archive")>=0)paths=event.getDataAsString("application/x-rdt-archive").split("\n")
                else if(event.hasText)paths=[event.text.trim()]
            }
            pane.session.insertDroppedPaths(paths);event.acceptProposedAction()
            pane.activate()
        }
    }
}
