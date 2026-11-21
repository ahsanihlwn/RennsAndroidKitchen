// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Dialogs as D

DialogWindow {
    id: dialog
    objectName: "workspaceApkDebloater"
    required property var workspace
    readonly property var backend: workspace.toolkit
    property string partitionFilter: "All"
    property string riskFilter: "All"

    implicitWidth: Math.max(700, Math.min(hostWindow.width * 0.78, 980))
    implicitHeight: Math.max(460, Math.min(hostWindow.height * 0.80, 640))
    panelRadius: 12

    function showDebloater(targetDir) {
        partitionFilter = "All"
        riskFilter = "All"
        apkSearch.text = ""
        backend.openApkDebloater(targetDir || "")
        open()
    }

    onDismissed: backend.dismiss()

    D.FileDialog {
        id: importListPicker
        title: "Import Debloat List"
        nameFilters: ["Text / JSON (*.txt *.json)", "All Files (*)"]
        onAccepted: dialog.backend.importDebloatList(selectedFile.toString())
    }

    D.FileDialog {
        id: exportListPicker
        title: "Export Debloat List"
        fileMode: D.FileDialog.SaveFile
        nameFilters: ["Text list (*.txt)", "All Files (*)"]
        onAccepted: dialog.backend.exportDebloatList(selectedFile.toString())
    }

    Item {
        anchors.fill: parent

        Item {
            id: titlebar
            width: parent.width
            height: 48
            Row {
                x: 18
                width: parent.width - 140
                height: parent.height
                spacing: 10
                Image {
                    width: 16
                    height: 16
                    source: "image://icons/android_tools"
                    anchors.verticalCenter: parent.verticalCenter
                    smooth: true
                }
                RakLabel {
                    text: "APK Debloater"
                    font.pixelSize: 13
                    font.weight: Font.Bold
                    color: dialog.c.FG
                    height: parent.height
                }
                RakLabel {
                    width: Math.max(120, parent.width - 210)
                    height: parent.height
                    text: dialog.backend.apkTargetDir || ""
                    font.pixelSize: 10
                    font.family: "Consolas"
                    color: dialog.c.FG3
                    elide: Text.ElideMiddle
                }
            }
            Row {
                anchors.right: parent.right
                anchors.rightMargin: 16
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8
                Spinner {
                    width: 16
                    height: 16
                    anchors.verticalCenter: parent.verticalCenter
                    visible: dialog.backend.busy
                    spinning: dialog.backend.busy
                    accent: dialog.c.accent_hex
                    thickness: 2
                    inset: 2
                }
                DialogButton {
                    colors: dialog.c
                    text: "Rescan"
                    implicitHeight: 28
                    onClicked: dialog.backend.scanApks()
                }
            }
        }

        SmoothRectangle { y: 48; width: parent.width; height: 1; color: dialog.c.b09 }

        Item {
            x: 18
            y: 58
            width: parent.width - 36
            height: parent.height - 108

            Row {
                id: filterBar
                width: parent.width
                spacing: 8
                RakField {
                    id: apkSearch
                    width: Math.max(180, parent.width - 520)
                    height: 32
                    placeholderText: "Search app, package, or origin folder..."
                    fillColor: dialog.c.BG_INPUT
                    edgeColor: dialog.c.b18
                    focusEdgeColor: dialog.c.a55
                    labelColor: dialog.c.FG
                    hintColor: dialog.c.placeholder
                    onTextChanged: dialog.backend.setApkFilters(text, dialog.partitionFilter, dialog.riskFilter)
                }
                WorkspaceChoiceButton {
                    workspace: dialog.workspace
                    popupBoundsItem: dialog.contentItem
                    popupBackdropSource: dialog.backdropSurface
                    width: 120
                    height: 32
                    colors: dialog.c
                    currentKey: dialog.partitionFilter
                    choices: (dialog.backend.apkPartitions || ["All"]).map(p => ({key: p, label: p}))
                    text: dialog.partitionFilter
                    onChosen: key => {
                        dialog.partitionFilter = key
                        dialog.backend.setApkFilters(apkSearch.text, dialog.partitionFilter, dialog.riskFilter)
                    }
                }
                WorkspaceChoiceButton {
                    workspace: dialog.workspace
                    popupBoundsItem: dialog.contentItem
                    popupBackdropSource: dialog.backdropSurface
                    width: 110
                    height: 32
                    colors: dialog.c
                    currentKey: dialog.riskFilter
                    choices: [
                        {key: "All", label: "All Risk"},
                        {key: "safe", label: "Safe"},
                        {key: "caution", label: "Caution"},
                        {key: "core", label: "Core"}
                    ]
                    text: dialog.riskFilter === "All" ? "All Risk" : dialog.riskFilter
                    onChosen: key => {
                        dialog.riskFilter = key
                        dialog.backend.setApkFilters(apkSearch.text, dialog.partitionFilter, dialog.riskFilter)
                    }
                }
                DialogButton {
                    colors: dialog.c
                    text: "Import"
                    implicitHeight: 32
                    onClicked: importListPicker.open()
                }
                DialogButton {
                    colors: dialog.c
                    text: "Export"
                    implicitHeight: 32
                    onClicked: exportListPicker.open()
                }
                DialogButton {
                    colors: dialog.c
                    primary: true
                    text: "Quarantine (" + dialog.backend.checkedApkCount + ")"
                    implicitHeight: 32
                    enabled: !dialog.backend.busy && dialog.backend.checkedApkCount > 0
                    onClicked: dialog.backend.quarantineSelectedApks()
                }
            }

            Row {
                y: 40
                width: parent.width
                height: parent.height - 40
                spacing: 12

                // Left pane: APK list with explicit origin folder column/subtitle
                SmoothRectangle {
                    width: parent.width * 0.63
                    height: parent.height
                    radius: 8
                    color: dialog.c.BG2
                    border.width: 1
                    border.color: dialog.c.b08

                    Item {
                        id: listHeader
                        width: parent.width
                        height: 36
                        Row {
                            x: 10
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 8
                            DialogButton {
                                colors: dialog.c
                                text: "Check Visible"
                                implicitHeight: 24
                                onClicked: dialog.backend.selectAllFilteredApks(true)
                            }
                            DialogButton {
                                colors: dialog.c
                                text: "Uncheck All"
                                implicitHeight: 24
                                onClicked: dialog.backend.selectAllFilteredApks(false)
                            }
                            RakLabel {
                                text: dialog.backend.totalApkCount + " APK(s) found"
                                font.pixelSize: 10
                                color: dialog.c.FG3
                                height: 24
                            }
                        }
                        SmoothRectangle {
                            anchors.bottom: parent.bottom
                            width: parent.width
                            height: 1
                            color: dialog.c.b08
                        }
                    }

                    ListView {
                        x: 6
                        y: 38
                        width: parent.width - 12
                        height: parent.height - 44
                        clip: true
                        topMargin: 4
                        bottomMargin: 4
                        boundsBehavior: Flickable.StopAtBounds
                        spacing: 4
                        model: dialog.backend.apks
                        C.ScrollBar.vertical: RakScrollBar { handleColor: dialog.c.BG_SCROLL }
                        delegate: SmoothRectangle {
                            required property var modelData
                            width: ListView.view.width - 8
                            height: 52
                            radius: 6
                            color: (dialog.backend.selectedApk && dialog.backend.selectedApk.rel_path === modelData.rel_path) ? dialog.c.a10 : dialog.c.BG3
                            border.width: 1
                            border.color: modelData.checked ? dialog.c.accent_hex : dialog.c.b08

                            MouseArea {
                                anchors.fill: parent
                                onClicked: dialog.backend.selectApk(modelData.rel_path)
                            }

                            Row {
                                x: 10
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width - 20
                                spacing: 10

                                SmoothRectangle {
                                    width: 18
                                    height: 18
                                    anchors.verticalCenter: parent.verticalCenter
                                    radius: 4
                                    color: modelData.checked ? dialog.c.accent_hex : dialog.c.BG_INPUT
                                    border.width: 1
                                    border.color: modelData.checked ? dialog.c.accent_hex : dialog.c.b18
                                    RakLabel {
                                        anchors.centerIn: parent
                                        visible: !!modelData.checked
                                        text: "✓"
                                        font.pixelSize: 11
                                        font.weight: Font.Bold
                                        color: "#FFFFFF"
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        onClicked: dialog.backend.toggleApkCheck(modelData.rel_path)
                                    }
                                }

                                Column {
                                    width: parent.width - 165
                                    anchors.verticalCenter: parent.verticalCenter
                                    spacing: 1
                                    RakLabel {
                                        width: parent.width
                                        text: (modelData.name || modelData.filename) + (modelData.package ? "  ·  " + modelData.package : "")
                                        font.pixelSize: 11
                                        font.weight: Font.DemiBold
                                        color: dialog.c.FG
                                        elide: Text.ElideRight
                                    }
                                    RakLabel {
                                        width: parent.width
                                        text: "Folder: " + (modelData.origin_folder || ".") + "  ·  " + modelData.size_human
                                        font.pixelSize: 10
                                        font.family: "Consolas"
                                        color: dialog.c.FG3
                                        elide: Text.ElideMiddle
                                    }
                                }

                                SmoothRectangle {
                                    width: 64
                                    height: 20
                                    anchors.verticalCenter: parent.verticalCenter
                                    radius: 4
                                    color: modelData.risk === "core" ? dialog.c.b08 : (modelData.risk === "caution" ? dialog.c.a10 : dialog.c.b04)
                                    border.width: 1
                                    border.color: modelData.risk === "core" ? dialog.c.RED_STATUS : dialog.c.b08
                                    RakLabel {
                                        anchors.centerIn: parent
                                        text: (modelData.risk || "safe").toUpperCase()
                                        font.pixelSize: 9
                                        font.weight: Font.Bold
                                        color: modelData.risk === "core" ? dialog.c.RED_STATUS : (modelData.risk === "caution" ? dialog.c.accent_hex : dialog.c.FG2)
                                    }
                                }
                            }
                        }
                    }
                }

                // Right pane: APK details (including origin path) + Lossless Quarantine
                Column {
                    width: parent.width * 0.37 - 12
                    height: parent.height
                    spacing: 8

                    SmoothRectangle {
                        width: parent.width
                        height: parent.height * 0.56
                        radius: 8
                        color: dialog.c.BG2
                        border.width: 1
                        border.color: dialog.c.b08

                        C.ScrollView {
                            anchors.fill: parent
                            anchors.margins: 10
                            clip: true
                            Column {
                                width: parent.width - 8
                                spacing: 6
                                Row {
                                    width: parent.width
                                    spacing: 10
                                    SmoothRectangle {
                                        width: 40
                                        height: 40
                                        radius: 8
                                        color: dialog.c.BG3
                                        border.width: 1
                                        border.color: dialog.c.b08
                                        Image {
                                            anchors.fill: parent
                                            anchors.margins: 4
                                            source: dialog.backend.selectedApkIcon || ""
                                            fillMode: Image.PreserveAspectFit
                                            visible: source !== ""
                                        }
                                        RakLabel {
                                            anchors.centerIn: parent
                                            visible: !dialog.backend.selectedApkIcon
                                            text: "APK"
                                            font.pixelSize: 10
                                            font.weight: Font.Bold
                                            color: dialog.c.FG3
                                        }
                                    }
                                    Column {
                                        width: parent.width - 52
                                        spacing: 2
                                        RakLabel {
                                            width: parent.width
                                            text: dialog.backend.selectedApk.name || "Select an APK"
                                            font.pixelSize: 12
                                            font.weight: Font.DemiBold
                                            color: dialog.c.FG
                                            elide: Text.ElideRight
                                        }
                                        RakLabel {
                                            width: parent.width
                                            text: dialog.backend.selectedApk.package || ""
                                            font.pixelSize: 10
                                            color: dialog.c.FG3
                                            elide: Text.ElideRight
                                        }
                                    }
                                }
                                RakLabel {
                                    width: parent.width
                                    wrapMode: Text.WrapAnywhere
                                    text: dialog.backend.selectedApk.rel_path
                                          ? ("Origin Folder: " + (dialog.backend.selectedApk.origin_folder || ".") +
                                             "\nRelative Path: " + dialog.backend.selectedApk.rel_path +
                                             "\nPartition: " + (dialog.backend.selectedApk.partition || "-") +
                                             "  ·  v" + (dialog.backend.selectedApk.version_name || "-") +
                                             "  ·  SDK " + (dialog.backend.selectedApk.min_sdk || "?") + "/" + (dialog.backend.selectedApk.target_sdk || "?") +
                                             "\n\nPermissions:\n" + (dialog.backend.selectedApk.permissions_text || ""))
                                          : "Click an APK on the left to inspect its origin folder, package ID, SDK level, and permissions."
                                    font.pixelSize: 10
                                    color: dialog.c.FG2
                                }
                            }
                        }
                    }

                    SmoothRectangle {
                        width: parent.width
                        height: parent.height * 0.44 - 8
                        radius: 8
                        color: dialog.c.BG2
                        border.width: 1
                        border.color: dialog.c.b08

                        Row {
                            x: 10
                            y: 6
                            width: parent.width - 20
                            height: 24
                            RakLabel {
                                text: "Quarantine (" + dialog.backend.quarantinedCount + ")"
                                font.pixelSize: 11
                                font.weight: Font.DemiBold
                                color: dialog.c.FG
                                width: parent.width - 80
                                height: 24
                            }
                            DialogButton {
                                colors: dialog.c
                                text: "Restore All"
                                implicitHeight: 22
                                enabled: dialog.backend.quarantinedCount > 0
                                onClicked: dialog.backend.restoreSelectedApks("")
                            }
                        }

                        ListView {
                            x: 8
                            y: 34
                            width: parent.width - 16
                            height: parent.height - 40
                            clip: true
                            spacing: 4
                            model: dialog.backend.quarantined
                            delegate: Row {
                                required property var modelData
                                width: ListView.view.width
                                height: 26
                                spacing: 6
                                RakLabel {
                                    width: parent.width - 68
                                    height: 26
                                    text: (modelData.name || modelData.package) + " (" + (modelData.origin_folder || ".") + ")"
                                    font.pixelSize: 10
                                    color: dialog.c.FG2
                                    elide: Text.ElideMiddle
                                }
                                DialogButton {
                                    colors: dialog.c
                                    text: "Restore"
                                    implicitHeight: 22
                                    onClicked: dialog.backend.restoreSelectedApks(modelData.rel_path)
                                }
                            }
                        }
                    }
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
