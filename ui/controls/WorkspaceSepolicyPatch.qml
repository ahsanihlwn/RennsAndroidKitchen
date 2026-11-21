// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Dialogs as D

DialogWindow {
    id: dialog
    objectName: "workspaceSepolicyPatch"
    required property var workspace
    readonly property var backend: workspace.toolkit
    property int outputTab: 0
    property bool clearAfterCapture: false
    property bool includeUntrusted: false

    implicitWidth: Math.max(660, Math.min(hostWindow.width * 0.72, 900))
    implicitHeight: Math.max(460, Math.min(hostWindow.height * 0.78, 620))
    panelRadius: 12

    function showSepolicyPatch(folderPath) {
        outputTab = 0
        backend.openSepolicyPatch(folderPath || "")
        open()
    }

    onDismissed: backend.dismiss()

    D.FileDialog {
        id: logFilePicker
        title: "Select Logcat / AVC Log (.txt)"
        nameFilters: ["Text / Log (*.txt *.log)", "All Files (*)"]
        onAccepted: dialog.backend.loadLogFile(selectedFile.toString())
    }

    Item {
        anchors.fill: parent

        Item {
            id: titlebar
            width: parent.width
            height: 48
            Row {
                x: 18
                width: parent.width - 70
                height: parent.height
                spacing: 10
                RakLabel {
                    text: "Sepolicy Rules Patch"
                    font.pixelSize: 13
                    font.weight: Font.Bold
                    color: dialog.c.FG
                    height: parent.height
                }
                RakLabel {
                    width: Math.max(120, parent.width - 170)
                    height: parent.height
                    text: dialog.backend.selinuxDir || ""
                    font.pixelSize: 10
                    font.family: "Consolas"
                    color: dialog.c.FG3
                    elide: Text.ElideMiddle
                }
            }
            Spinner {
                anchors.right: parent.right
                anchors.rightMargin: 18
                anchors.verticalCenter: parent.verticalCenter
                width: 16
                height: 16
                visible: dialog.backend.busy
                spinning: dialog.backend.busy
                accent: dialog.c.accent_hex
                thickness: 2
                inset: 2
            }
        }

        SmoothRectangle { y: 48; width: parent.width; height: 1; color: dialog.c.b09 }

        Column {
            x: 18
            y: 58
            width: parent.width - 36
            height: parent.height - 108
            spacing: 10

            // Log source controls: Live Logcat (ADB) or Load .txt
            Row {
                width: parent.width
                spacing: 8
                DialogButton {
                    colors: dialog.c
                    primary: true
                    text: "Capture Live Logcat (ADB)"
                    implicitHeight: 32
                    enabled: !dialog.backend.busy
                    onClicked: dialog.backend.captureLiveLogcat(dialog.clearAfterCapture)
                }
                DialogButton {
                    colors: dialog.c
                    text: "Load log file (.txt)..."
                    implicitHeight: 32
                    enabled: !dialog.backend.busy
                    onClicked: logFilePicker.open()
                }
                DialogButton {
                    colors: dialog.c
                    text: "Parse Pasted Log"
                    implicitHeight: 32
                    onClicked: dialog.backend.generateSelinuxRules(avcLogInput.text, dialog.includeUntrusted)
                }
                Row {
                    height: 32
                    spacing: 6
                    RakSwitch {
                        checked: dialog.includeUntrusted
                        anchors.verticalCenter: parent.verticalCenter
                        onToggled: {
                            dialog.includeUntrusted = !dialog.includeUntrusted
                            dialog.backend.generateSelinuxRules(avcLogInput.text, dialog.includeUntrusted)
                        }
                    }
                    RakLabel {
                        text: "Include untrusted_app"
                        font.pixelSize: 10
                        color: dialog.c.FG2
                        height: 32
                    }
                }
            }

            // AVC Denial log editor
            SmoothRectangle {
                width: parent.width
                height: parent.height * 0.40 - 10
                radius: 8
                color: dialog.c.BG_INPUT
                border.width: 1
                border.color: dialog.c.b12
                C.ScrollView {
                    anchors.fill: parent
                    anchors.margins: 8
                    C.TextArea {
                        id: avcLogInput
                        text: dialog.backend.selinuxLogPreview
                        placeholderText: "Capture live logcat via ADB, load a .txt file, or paste `avc: denied` lines here..."
                        placeholderTextColor: dialog.c.placeholder
                        color: dialog.c.FG
                        font.family: "Consolas"
                        font.pixelSize: 10
                        wrapMode: Text.NoWrap
                        background: null
                    }
                }
            }

            // Generated Rule Tabs + Target CIL file apply bar
            Row {
                width: parent.width
                spacing: 6
                DialogButton {
                    colors: dialog.c
                    primary: dialog.outputTab === 0
                    text: "CIL Rules"
                    implicitHeight: 28
                    onClicked: dialog.outputTab = 0
                }
                DialogButton {
                    colors: dialog.c
                    primary: dialog.outputTab === 1
                    text: "TE Rules"
                    implicitHeight: 28
                    onClicked: dialog.outputTab = 1
                }
                DialogButton {
                    colors: dialog.c
                    primary: dialog.outputTab === 2
                    text: "MagiskPolicy"
                    implicitHeight: 28
                    onClicked: dialog.outputTab = 2
                }
                Item { width: 10; height: 1 }
                RakLabel {
                    text: dialog.backend.selinuxStats || ""
                    font.pixelSize: 10
                    color: dialog.c.FG3
                    height: 28
                }
            }

            SmoothRectangle {
                width: parent.width
                height: parent.height * 0.42 - 20
                radius: 8
                color: dialog.c.BG2
                border.width: 1
                border.color: dialog.c.b08
                C.ScrollView {
                    anchors.fill: parent
                    anchors.margins: 8
                    C.TextArea {
                        readOnly: true
                        selectByMouse: true
                        text: dialog.outputTab === 0
                              ? dialog.backend.selinuxCil
                              : (dialog.outputTab === 1 ? dialog.backend.selinuxTe : dialog.backend.selinuxMagisk)
                        color: dialog.c.FG
                        font.family: "Consolas"
                        font.pixelSize: 10
                        background: null
                    }
                }
            }

            Row {
                width: parent.width
                spacing: 8
                RakLabel {
                    text: "Target .cil:"
                    font.pixelSize: 11
                    color: dialog.c.FG2
                    height: 32
                }
                WorkspaceChoiceButton {
                    workspace: dialog.workspace
                    popupBoundsItem: dialog.contentItem
                    popupBackdropSource: dialog.backdropSurface
                    width: Math.max(220, parent.width - 270)
                    height: 32
                    colors: dialog.c
                    currentKey: dialog.backend.selectedCilFile
                    choices: (dialog.backend.selinuxCilFiles || []).map(f => ({key: f, label: f}))
                    text: dialog.backend.selectedCilFile || "Select .cil file..."
                    onChosen: key => dialog.backend.selectCilFile(key)
                }
                DialogButton {
                    colors: dialog.c
                    primary: true
                    text: "Apply Rules to .cil"
                    implicitHeight: 32
                    enabled: !dialog.backend.busy && (dialog.backend.selinuxCil || "").length > 0
                    onClicked: dialog.backend.applyCilRules(dialog.backend.selectedCilFile)
                }
            }
        }

        // Bottom Status Bar
        SmoothRectangle { y: parent.height - 42; width: parent.width; height: 1; color: dialog.c.b09 }
        Item {
            y: parent.height - 41
            width: parent.width
            height: 41
            RakLabel {
                x: 18
                width: parent.width - 120
                height: parent.height
                text: dialog.backend.status
                font.pixelSize: 11
                color: dialog.c.FG2
                elide: Text.ElideRight
            }
            DialogButton {
                anchors.right: parent.right
                anchors.rightMargin: 16
                anchors.verticalCenter: parent.verticalCenter
                colors: dialog.c
                text: "Close"
                onClicked: dialog.dismiss()
            }
        }
    }
}
