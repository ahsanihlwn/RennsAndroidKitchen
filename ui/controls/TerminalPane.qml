// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import QtTerm 1.0

Item {
    id: pane
    required property var session
    required property color fillColor
    property bool inputAllowed: true
    property bool terminalReady: true
    property bool focusPending: true
    property bool layoutSettled: true
    property bool contentReady: true
    readonly property var terminal: termItem

    function focusInput() {
        if (!inputAllowed || !visible || !enabled || !termItem) return
        if (pane.Window.window && pane.Window.window.mouseGrabberItem) {
            Qt.callLater(focusInput)
            return
        }
        termItem.forceActiveFocus(Qt.OtherFocusReason)
        focusPending = false
    }

    function activate() {
        focusPending = true
        Qt.callLater(focusInput)
    }

    onVisibleChanged: {
        if (visible) activate()
    }
    onLayoutSettledChanged: {
        if (layoutSettled && visible) {
            if (!termItem.running && termItem.autoStart) termItem.start()
            activate()
        }
    }
    onInputAllowedChanged: {
        if (inputAllowed) Qt.callLater(focusInput)
    }

    SmoothRectangle {
        anchors.fill: parent
        color: pane.fillColor
    }

    Terminal {
        id: termItem
        objectName: "quickTerminalView"
        anchors.fill: parent
        anchors.margins: 4
        focus: true
        program: pane.session ? (pane.session.program || "") : ""
        arguments: pane.session ? (pane.session.arguments || []) : []
        workingDirectory: pane.session ? (pane.session.workingDirectory || "") : ""
        autoStart: (pane.session ? (pane.session.autoStart !== undefined ? pane.session.autoStart : true) : true) && pane.layoutSettled
        wslMode: pane.session ? Boolean(pane.session.wslMode) : false
        fontFamily: pane.session ? (pane.session.fontFamily || "Cascadia Mono") : "Cascadia Mono"
        fontPixelSize: pane.session ? (pane.session.fontPixelSize || 15) : 15
        cursorShape: pane.session ? (pane.session.cursorShape || "beam") : "beam"
        cursorWidth: pane.session ? (pane.session.cursorWidth || 5.5) : 5.5
        padding: 6
        foreground: pane.session ? (pane.session.foreground || "#e2e8f0") : "#e2e8f0"
        background: pane.fillColor
        cursorColor: pane.session ? (pane.session.cursorColor || "#38bdf8") : "#38bdf8"
        selectionColor: pane.session ? (pane.session.selectionColor || "#38bdf840") : "#38bdf840"
        ansiPalette: pane.session ? (pane.session.palette || []) : []
        enabled: pane.inputAllowed

        Component.onCompleted: {
            if (pane.session && typeof pane.session.bindTerminal === "function") {
                pane.session.bindTerminal(termItem)
            }
            pane.activate()
        }
        Component.onDestruction: termItem.stop()
    }

    // Overlay scrollbar
    Rectangle {
        id: track
        visible: termItem.historySize > 0 && !termItem.mouseTracking
        anchors { right: parent.right; top: parent.top; bottom: parent.bottom; margins: 2 }
        width: 6
        radius: 3
        color: "#18ffffff"
        Rectangle {
            readonly property real total: termItem.historySize + termItem.rows
            width: parent.width
            radius: 3
            color: dragArea.pressed ? "#99cdd6f4" : "#44cdd6f4"
            height: Math.max(20, parent.height * termItem.rows / Math.max(1, total))
            y: (parent.height - height) * (1 - termItem.scrollOffset / Math.max(1, termItem.historySize))
        }
        MouseArea {
            id: dragArea
            anchors.fill: parent
            anchors.leftMargin: -6
            function seek(my) {
                var f = 1 - Math.min(1, Math.max(0, my / height))
                termItem.scrollOffset = Math.round(f * termItem.historySize)
            }
            onPressed: (m) => seek(m.y)
            onPositionChanged: (m) => { if (pressed) seek(m.y) }
        }
    }

    DropArea {
        anchors.fill: parent
        function sourcePaths(event) {
            const mime = event.source && event.source.dragMime
            if (!mime) return []
            if (mime["text/uri-list"]) return mime["text/uri-list"].split(/\r?\n/).filter(path => path.length && path[0] !== "#")
            if (mime["application/x-rak-tree"]) {
                try { return JSON.parse(mime["application/x-rak-tree"]).paths || [] } catch (error) { return [] }
            }
            if (mime["application/x-rdt-archive"]) return mime["application/x-rdt-archive"].split("\n")
            return []
        }
        onEntered: event => {
            event.accepted = sourcePaths(event).length > 0 || event.hasUrls || event.hasText || event.formats.indexOf("application/x-rdt-archive") >= 0
        }
        onDropped: event => {
            let paths = sourcePaths(event)
            if (!paths.length) {
                if (event.hasUrls) paths = event.urls
                else if (event.formats.indexOf("application/x-rdt-archive") >= 0) paths = event.getDataAsString("application/x-rdt-archive").split("\n")
                else if (event.hasText) paths = [event.text.trim()]
            }
            if (pane.session && typeof pane.session.insertDroppedPaths === "function") {
                pane.session.insertDroppedPaths(paths)
            }
            event.acceptProposedAction()
            pane.activate()
        }
    }
}
