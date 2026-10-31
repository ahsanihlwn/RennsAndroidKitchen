// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtWebEngine
import QtWebChannel

Item {
    id: pane
    required property var session
    required property color fillColor
    property bool nativeTabs: false
    readonly property var webView: loader.item
    function find() {
        if(!webView || !visible)return
        webView.forceActiveFocus(Qt.ShortcutFocusReason)
        webView.runJavaScript("if(typeof editor!=='undefined' && editor && editor.getModel()){editor.focus();editor.getAction('actions.find').run();}")
    }
    SmoothRectangle { anchors.fill: parent; color: pane.fillColor }
    Loader {
        id: loader
        anchors.fill: parent
        active: pane.session.active
        sourceComponent: WebEngineView {
            id: view
            objectName: "quickMonacoView"
            backgroundColor: pane.fillColor
            settings.localContentCanAccessRemoteUrls: true
            settings.localContentCanAccessFileUrls: true
            settings.javascriptEnabled: true
            settings.scrollAnimatorEnabled: true
            webChannel: WebChannel { id: channel }
            Component.onCompleted: {
                channel.registerObject("bridge", pane.session.bridge)
                loadHtml(pane.session.html, pane.session.baseUrl)
            }
            Connections {
                target: pane.session.bridge
                function onEditorReady() {
                    if(pane.nativeTabs)view.runJavaScript("var bar=document.getElementById('tab-bar');if(bar)bar.style.display='none';")
                }
                function onNavigateRequested(path,line,column,endLine,endColumn) {
                    view.forceActiveFocus(Qt.OtherFocusReason)
                }
            }
            Connections {
                target: pane.session
                function onReloadRequested() { view.loadHtml(pane.session.html, pane.session.baseUrl) }
                function onSaved(path) { view.runJavaScript("window.dispatchEvent(new Event('bridge-saved'));") }
            }
        }
    }
}
