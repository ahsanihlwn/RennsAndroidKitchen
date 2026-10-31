// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    id: window
    required property var editorSession
    required property var terminalSession
    property string editorResult: ""
    property string terminalResult: ""
    property string scrollResult: ""
    width: 1080; height: 700; color: "#12131a"
    EditorPane { id: editor; width: parent.width; height: parent.height / 2; session: window.editorSession; fillColor: "#1a1b25" }
    TerminalPane { id: terminal; y: parent.height / 2; width: parent.width; height: parent.height / 2; session: window.terminalSession; fillColor: "#12131a" }
    Connections {
        target: terminal.webView
        function onJavaScriptConsoleMessage(level, message, lineNumber, sourceID) { console.warn("terminal fixture: " + message) }
        function onLoadingChanged(info) { if (info.errorString) console.warn("terminal load: " + info.errorString) }
    }
    function inspectContent() {
        editor.webView.runJavaScript("JSON.stringify({ready: !!editor, content: editor && editor.getValue()})", function(result) { window.editorResult = result })
        terminal.webView.runJavaScript("JSON.stringify({cols: window.term && window.term.cols, bridge: !!window.bridge, qt: typeof qt, url: location.href, content: window.term && Array.from({length: term.buffer.active.length}, (_, i) => term.buffer.active.getLine(i).translateToString()).join('\\n')})", function(result) { window.terminalResult = result })
    }
    function scrollEditorFixture() {editor.webView.runJavaScript("editor.setValue(('fixture line '+ 'x'.repeat(300)+'\\n').repeat(300));editor.setScrollTop(440);editor.setScrollLeft(80);")}
    function inspectScrollHints() {
        editor.webView.runJavaScript("JSON.stringify({active:document.documentElement.classList.contains('rak-scroll-hints'),opacity:getComputedStyle(document.querySelector('.monaco-scrollable-element > .scrollbar.vertical > .slider')).opacity})",function(value){window.scrollResult=value})
    }
}
