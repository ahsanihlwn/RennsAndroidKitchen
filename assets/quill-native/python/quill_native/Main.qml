import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import Quill.Native 1.0

ApplicationWindow {
    id: window
    width: 1180
    height: 790
    minimumWidth: 700
    minimumHeight: 420
    visible: true
    color: "#11171e"
    title: (editor.dirty ? "● " : "") + editor.fileName + " — Quill Native"
    font.family: Qt.platform.os === "windows" ? "Segoe UI" : "DejaVu Sans"
    font.pixelSize: 13
    property bool allowClose: false
    property string toastText: ""
    property bool searchVisible: false
    property string loadOnStart: ""
    palette.window: "#11171e"
    palette.base: "#17212a"
    palette.text: "#d2dce5"
    palette.buttonText: "#bdcbd8"
    palette.button: "#1b2833"
    palette.highlight: "#497f7b"
    palette.highlightedText: "#ffffff"

    component ActionButton: Button {
        flat: true
        hoverEnabled: true
        font.pixelSize: 12
        padding: 11
        background: Rectangle {
            color: parent.down ? "#2a414c" : parent.hovered ? "#22313d" : "transparent"
            radius: 5
        }
        contentItem: Text {
            text: parent.text
            color: parent.enabled ? "#c9d5df" : "#4a5b68"
            font: parent.font
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    onClosing: function(close) {
        if (!allowClose) {
            close.accepted = false
            editor.request("close")
        }
    }

    Shortcut { sequences: [StandardKey.Open]; onActivated: editor.request("open") }
    Shortcut { sequences: [StandardKey.New]; onActivated: editor.request("new") }
    Shortcut { sequences: [StandardKey.Save]; onActivated: editor.saveCurrent() }
    Shortcut { sequences: [StandardKey.SaveAs]; onActivated: saveDialog.open() }
    Shortcut { sequences: [StandardKey.Find]; onActivated: { window.searchVisible = true; search.forceActiveFocus(); search.selectAll() } }
    Shortcut { sequence: "Ctrl++"; onActivated: editor.zoom(1) }
    Shortcut { sequence: "Ctrl+-"; onActivated: editor.zoom(-1) }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 63
            color: "#151e27"
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 23
                anchors.rightMargin: 18
                spacing: 8
                Rectangle {
                    width: 28; height: 28; radius: 7; color: "#85d6c4"
                    Text { anchors.centerIn: parent; text: "/"; font.pixelSize: 24; font.bold: true; color: "#12352f" }
                }
                Text { text: "QUILL"; color: "#e8f0f6"; font.pixelSize: 14; font.bold: true; font.letterSpacing: 2; leftPadding: 6 }
                Text { text: "NATIVE"; color: "#6b8c9f"; font.pixelSize: 10; font.letterSpacing: 2; leftPadding: 2; rightPadding: 25 }
                ActionButton { text: "New"; onClicked: editor.request("new"); ToolTip.text: "New document · Ctrl+N"; ToolTip.visible: hovered }
                ActionButton { text: "Open…"; onClicked: editor.request("open"); ToolTip.text: "Open file · Ctrl+O"; ToolTip.visible: hovered }
                ActionButton { text: "Save"; onClicked: editor.saveCurrent(); ToolTip.text: "Save · Ctrl+S"; ToolTip.visible: hovered }
                Rectangle { width: 1; height: 19; color: "#2a3844"; Layout.leftMargin: 8; Layout.rightMargin: 8 }
                ActionButton { text: "Undo"; enabled: editor.canUndo; onClicked: { editor.undo(); editor.forceActiveFocus() } }
                ActionButton { text: "Redo"; enabled: editor.canRedo; onClicked: { editor.redo(); editor.forceActiveFocus() } }
                Item { Layout.fillWidth: true }
                Rectangle {
                    implicitWidth: 90; implicitHeight: 25; radius: 4; color: "#1c302f"
                    Row { anchors.centerIn: parent; spacing: 6
                        Rectangle { width: 5; height: 5; radius: 3; color: "#85d6c4"; anchors.verticalCenter: parent.verticalCenter }
                        Text { text: "Floem core"; color: "#91bfb6"; font.pixelSize: 10 }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 44
            color: "#0e141a"
            RowLayout {
                anchors.fill: parent
                spacing: 0
                Rectangle {
                    Layout.preferredWidth: Math.min(310, nameLabel.implicitWidth + 69)
                    Layout.fillHeight: true
                    color: "#11171e"
                    Rectangle { anchors.top: parent.top; width: parent.width; height: 2; color: "#85d6c4" }
                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        x: 23
                        spacing: 12
                        Text { text: "⌘"; color: "#7bc0bd"; font.pixelSize: 15 }
                        Text { id: nameLabel; text: editor.fileName; width: Math.min(220, implicitWidth); elide: Text.ElideMiddle; color: "#d4dfe7"; font.pixelSize: 12 }
                        Text { text: editor.dirty ? "●" : ""; color: "#dcb477"; font.pixelSize: 9; anchors.verticalCenter: parent.verticalCenter }
                    }
                }
                Item { Layout.fillWidth: true }
                Text { text: "UTF-8"; color: "#577181"; font.pixelSize: 10; Layout.rightMargin: 15 }
                ComboBox {
                    id: languageBox
                    Layout.preferredWidth: 155
                    Layout.preferredHeight: 30
                    Layout.rightMargin: 14
                    model: editor.availableLanguages
                    currentIndex: editor.availableLanguages.indexOf(editor.language)
                    onActivated: { editor.setLanguage(currentText); editor.forceActiveFocus() }
                    font.pixelSize: 11
                    background: Rectangle { color: "#1a2530"; radius: 4; border.color: "#263744" }
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 41
            visible: window.searchVisible
            color: "#1a2631"
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 18
                anchors.rightMargin: 12
                spacing: 9
                Text { text: "FIND"; color: "#7a96aa"; font.pixelSize: 10; font.letterSpacing: 1 }
                TextField {
                    id: search
                    objectName: "searchField"
                    Layout.preferredWidth: 320
                    Layout.preferredHeight: 29
                    placeholderText: "Find in this file…"
                    selectByMouse: true
                    onAccepted: editor.findNext(text, false)
                    Keys.onEscapePressed: { window.searchVisible = false; editor.forceActiveFocus() }
                    background: Rectangle { color: "#101923"; border.color: search.activeFocus ? "#619f96" : "#2c4050"; radius: 4 }
                }
                ActionButton { text: "Previous"; onClicked: editor.findNext(search.text, true) }
                ActionButton { text: "Next"; onClicked: editor.findNext(search.text, false) }
                Text { text: "Case sensitive"; color: "#617f93"; font.pixelSize: 10 }
                Item { Layout.fillWidth: true }
                ActionButton { text: "Close"; onClicked: { window.searchVisible = false; editor.forceActiveFocus() } }
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            NativeEditor {
                id: editor
                objectName: "nativeEditor"
                anchors.fill: parent
                anchors.rightMargin: 12
                anchors.bottomMargin: horizontalBar.visible ? 11 : 0
                focus: true
            }
            ScrollBar {
                id: verticalBar
                orientation: Qt.Vertical
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.bottom: parent.bottom
                width: 12
                policy: ScrollBar.AlwaysOn
                size: Math.min(1, editor.height / Math.max(1, editor.totalHeight))
                position: editor.scrollY / Math.max(1, editor.totalHeight)
                onPositionChanged: if (pressed) editor.scrollY = position * editor.totalHeight
                contentItem: Rectangle { implicitWidth: 6; radius: 3; color: verticalBar.pressed ? "#627e8c" : "#2e4352" }
                background: Rectangle { color: "#11171e" }
            }
            ScrollBar {
                id: horizontalBar
                orientation: Qt.Horizontal
                anchors.left: parent.left
                anchors.leftMargin: 65
                anchors.right: parent.right
                anchors.rightMargin: 12
                anchors.bottom: parent.bottom
                height: 11
                visible: size < 0.999
                size: Math.min(1, editor.width / Math.max(1, editor.totalWidth))
                position: editor.scrollX / Math.max(1, editor.totalWidth)
                onPositionChanged: if (pressed) editor.scrollX = position * editor.totalWidth
                contentItem: Rectangle { implicitHeight: 5; radius: 3; color: horizontalBar.pressed ? "#627e8c" : "#2e4352" }
                background: Rectangle { color: "#11171e" }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 31
            color: "#18232c"
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 19
                anchors.rightMargin: 18
                spacing: 17
                Text { text: "●"; color: "#85d6c4"; font.pixelSize: 8 }
                Text { text: window.toastText || "Ready"; color: "#a2b7c7"; font.pixelSize: 11 }
                Text { text: editor.lineCount.toLocaleString() + " lines"; color: "#627f94"; font.pixelSize: 11 }
                Text { text: editor.sizeLabel; color: "#627f94"; font.pixelSize: 11 }
                Item { Layout.fillWidth: true }
                Text { text: editor.positionLabel; color: "#9fb4c3"; font.pixelSize: 11 }
                Text { text: editor.indentLabel; color: "#627f94"; font.pixelSize: 11 }
                Text { text: editor.eolLabel; color: "#627f94"; font.pixelSize: 11 }
                Text { text: editor.language; color: "#91bfb6"; font.pixelSize: 11 }
            }
        }
    }

    FileDialog {
        id: openDialog
        title: "Open a UTF-8 text file"
        fileMode: FileDialog.OpenFile
        onAccepted: editor.openUrl(selectedFile)
    }
    FileDialog {
        id: saveDialog
        title: "Save file"
        fileMode: FileDialog.SaveFile
        onAccepted: editor.saveUrl(selectedFile)
        onRejected: editor.cancelSave()
    }
    Dialog {
        id: unsavedDialog
        title: "Save changes?"
        modal: true
        anchors.centerIn: parent
        width: 420
        closePolicy: Popup.NoAutoClose
        ColumnLayout {
            width: parent.width
            Text { text: editor.fileName + " has unsaved changes."; color: "#c7d2df"; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            RowLayout {
                Layout.topMargin: 15
                Item { Layout.fillWidth: true }
                ActionButton { text: "Cancel"; onClicked: { unsavedDialog.close(); editor.resolveUnsaved("cancel") } }
                ActionButton { text: "Discard"; onClicked: { unsavedDialog.close(); editor.resolveUnsaved("discard") } }
                Button { text: "Save"; onClicked: { unsavedDialog.close(); editor.resolveUnsaved("save") } }
            }
        }
    }
    Dialog {
        id: errorDialog
        property string message: ""
        title: "File operation failed"
        modal: true
        anchors.centerIn: parent
        width: 510
        standardButtons: Dialog.Ok
        Text { width: parent.width; text: errorDialog.message; color: "#d6b5ac"; wrapMode: Text.WordWrap }
    }
    Timer { id: toastTimer; interval: 3000; onTriggered: window.toastText = "" }
    Connections {
        target: editor
        function onOpenRequested() { openDialog.open() }
        function onSaveAsRequested() { saveDialog.open() }
        function onConfirmRequested() { unsavedDialog.open() }
        function onCloseApproved() { window.allowClose = true; Qt.callLater(function() { window.close() }) }
        function onFindRequested() { window.searchVisible = true; search.forceActiveFocus(); search.selectAll() }
        function onErrorOccurred(message) { errorDialog.message = message; errorDialog.open() }
        function onNotice(message) { window.toastText = message; toastTimer.restart() }
    }
}
