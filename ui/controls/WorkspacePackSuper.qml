// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Dialogs as D

DialogWindow {
    id: dialog
    objectName: "workspacePackSuper"
    required property var workspace
    readonly property var backend: workspace.packSuper
    implicitWidth: Math.min(820, hostWindow.width - 24)
    implicitHeight: Math.min(540, hostWindow.height - 24)
    panelRadius: 12
    closePolicy: C.Popup.CloseOnEscape

    function showPackSuper(data) {
        searchField.text = ""
        groupField.text = "qti_dynamic_partitions"
        outputField.text = "super.img"
        backend.prepare((data && data.paths) || [], (data && data.base) || "")
        open()
    }

    Connections {
        target: dialog.backend
        function onChanged() {
            if (!sizeField.activeFocus && sizeField.text !== dialog.backend.superSize)
                sizeField.text = dialog.backend.superSize
            if (!groupField.activeFocus && groupField.text !== dialog.backend.groupName)
                groupField.text = dialog.backend.groupName
            if (!outputField.activeFocus && outputField.text !== dialog.backend.outputName)
                outputField.text = dialog.backend.outputName
        }
        function onDismissRequested() {
            dialog.close()
        }
    }

    D.FileDialog {
        id: addPicker
        title: "Add Partition Image"
        fileMode: D.FileDialog.OpenFiles
        nameFilters: ["Partition images (*.img)", "All files (*)"]
        onAccepted: dialog.backend.addFiles(selectedFiles)
    }

    Item {
        anchors.fill: parent

        Item {
            id: header
            x: 24; y: 18; width: parent.width - 48; height: 42
            Column {
                width: parent.width - 30; spacing: 2
                RakLabel {
                    text: dialog.s.ps_title
                    font.pixelSize: 15; font.weight: Font.DemiBold; color: dialog.c.FG
                }
                RakLabel {
                    width: parent.width
                    text: dialog.backend.outputDir || ""
                    elide: Text.ElideMiddle; font.pixelSize: 10; font.family: "Consolas"; color: dialog.c.FG3
                }
            }
            Spinner {
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                width: 16; height: 16
                visible: dialog.backend.loading; spinning: dialog.backend.loading
                accent: dialog.c.accent_hex; thickness: 2; inset: 2
            }
        }

        SmoothRectangle { x: 24; y: 64; width: parent.width - 48; height: 1; color: dialog.c.b08 }

        Row {
            id: body
            x: 24; y: 74
            width: parent.width - 48
            height: parent.height - y - footer.height - 20
            spacing: 16

            // Left column: Partition(s) list (matching MIO Kitchen layout)
            Column {
                width: Math.floor((parent.width - 16) * 0.48)
                height: parent.height
                spacing: 8

                Item {
                    width: parent.width; height: 18
                    RakLabel {
                        text: dialog.s.ps_partitions
                        font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1
                        color: dialog.c.FG_BLUE_MUTED; height: parent.height
                    }
                    RakLabel {
                        anchors.right: parent.right; height: parent.height
                        text: dialog.backend.selectedCount + " · " + dialog.backend.totalRawLabel
                        font.pixelSize: 10; color: dialog.c.FG3
                    }
                }

                Row {
                    width: parent.width; spacing: 6
                    RakField {
                        id: searchField
                        objectName: "packSuperSearch"
                        width: parent.width - addBtn.width - 6; height: 30
                        placeholderText: dialog.s.ps_search
                        font.pixelSize: 11; cornerRadius: 6
                        leftPadding: 10; rightPadding: 10; topPadding: 1; bottomPadding: 0
                        fillColor: dialog.c.b04; edgeColor: dialog.c.b10; focusEdgeColor: dialog.c.a45
                        labelColor: dialog.c.FG; hintColor: dialog.c.FG_MUTED
                        onTextChanged: dialog.backend.setSearchQuery(text)
                    }
                    DialogButton {
                        id: addBtn
                        objectName: "packSuperAdd"
                        colors: dialog.c; text: "+ Add"; implicitHeight: 30
                        onClicked: addPicker.open()
                    }
                }

                Row {
                    width: parent.width; height: 22; spacing: 8
                    RoundCheck {
                        objectName: "packSuperSelectAll"
                        colors: dialog.c
                        checked: dialog.backend.allSelected
                        onToggled: dialog.backend.setSelectAll(checked)
                    }
                    RakLabel {
                        height: 20; text: dialog.s.ps_select_all
                        font.pixelSize: 11; color: dialog.c.FG_MUTED
                    }
                }

                SmoothRectangle {
                    width: parent.width
                    height: Math.max(120, parent.height - 96)
                    radius: 8; color: dialog.c.b02; border.width: 1; border.color: dialog.c.b08

                    ListView {
                        id: partList
                        objectName: "packSuperPartitions"
                        anchors.fill: parent; anchors.margins: 6
                        clip: true; spacing: 4
                        model: dialog.backend.rows
                        C.ScrollBar.vertical: RakScrollBar { handleColor: dialog.c.BG_SCROLL }
                        delegate: SmoothRectangle {
                            required property var modelData
                            required property int index
                            objectName: "packSuperRow" + index
                            width: partList.width; height: 38; radius: 6
                            color: modelData.checked ? dialog.c.a08 : dialog.c.b03
                            border.width: 1
                            border.color: modelData.checked ? dialog.c.a30 : dialog.c.b06

                            RoundCheck {
                                x: 10; y: 9; colors: dialog.c
                                checked: modelData.checked
                                onToggled: dialog.backend.toggleKey(modelData.key, checked)
                            }
                            RakLabel {
                                x: 38; width: parent.width - 178; height: parent.height
                                text: modelData.slot ? (modelData.baseName + "_" + modelData.slot) : (modelData.baseName || modelData.name)
                                elide: Text.ElideRight; font.pixelSize: 12; color: dialog.c.FG
                            }
                            SmoothRectangle {
                                anchors.right: sizeLbl.left; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter
                                height: 18; width: fsLbl.implicitWidth + 10; radius: 4
                                color: modelData.badge === "EROFS" ? "#3a5f8a" : modelData.badge === "SPARSE" ? "#5a3a70" : "#3a6b4a"
                                RakLabel {
                                    id: fsLbl; anchors.centerIn: parent
                                    text: modelData.fsType
                                    font.pixelSize: 9; font.weight: Font.DemiBold
                                    color: modelData.badge === "EROFS" ? "#7eb8f7" : modelData.badge === "SPARSE" ? "#b07ae0" : "#7fcf9a"
                                }
                            }
                            RakLabel {
                                id: sizeLbl
                                anchors.right: parent.right; anchors.rightMargin: 10; height: parent.height
                                width: 64; horizontalAlignment: Text.AlignRight
                                text: modelData.sizeLabel; font.pixelSize: 11; color: dialog.c.FG_BLUE_MUTED
                            }
                        }
                    }
                    RakLabel {
                        anchors.centerIn: parent; width: parent.width - 24
                        visible: partList.count === 0
                        horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                        text: dialog.backend.loading ? "Scanning partitions..." : "No EROFS / EXT partition images."
                        font.pixelSize: 11; color: dialog.c.FG3
                    }
                }
            }

            // Right column: Super Type, Attribute, Settings, Options (from MIO Kitchen)
            Column {
                width: Math.floor((parent.width - 16) * 0.52)
                height: parent.height
                spacing: 10

                // Super Type
                RakLabel {
                    text: dialog.s.ps_super_type
                    font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1
                    color: dialog.c.FG_BLUE_MUTED
                }
                Row {
                    width: parent.width; height: 32; spacing: 6
                    Repeater {
                        model: [
                            {key: "a_only", label: "A-only"},
                            {key: "virtual_ab", label: "Virtual-AB"},
                            {key: "ab", label: "A/B"}
                        ]
                        delegate: RakButton {
                            required property var modelData
                            readonly property bool selected: dialog.backend.superType === modelData.key
                            objectName: "packSuperType_" + modelData.key
                            width: Math.floor((parent.width - 12) / 3); height: 32
                            text: modelData.label; cornerRadius: 6
                            font.pixelSize: 11; font.weight: selected ? Font.DemiBold : Font.Normal
                            fillColor: selected ? dialog.c.a28 : dialog.c.b05
                            hoverFillColor: selected ? dialog.c.a36 : dialog.c.b09
                            downFillColor: hoverFillColor
                            edgeColor: selected ? dialog.c.a55 : dialog.c.b12
                            hoverEdgeColor: selected ? dialog.c.a55 : dialog.c.b20
                            labelColor: selected ? dialog.c.FG_BLUE_LIGHT : dialog.c.FG_MUTED
                            onClicked: dialog.backend.setSuperType(modelData.key)
                        }
                    }
                }

                // Attribute
                RakLabel {
                    text: dialog.s.ps_attribute
                    font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1
                    color: dialog.c.FG_BLUE_MUTED
                }
                Row {
                    width: parent.width; height: 32; spacing: 6
                    Repeater {
                        model: [
                            {key: "readonly", label: "Readonly"},
                            {key: "none", label: "None"}
                        ]
                        delegate: RakButton {
                            required property var modelData
                            readonly property bool selected: dialog.backend.attrib === modelData.key
                            objectName: "packSuperAttr_" + modelData.key
                            width: Math.floor((parent.width - 6) / 2); height: 32
                            text: modelData.label; cornerRadius: 6
                            font.pixelSize: 11; font.weight: selected ? Font.DemiBold : Font.Normal
                            fillColor: selected ? dialog.c.a28 : dialog.c.b05
                            hoverFillColor: selected ? dialog.c.a36 : dialog.c.b09
                            downFillColor: hoverFillColor
                            edgeColor: selected ? dialog.c.a55 : dialog.c.b12
                            hoverEdgeColor: selected ? dialog.c.a55 : dialog.c.b20
                            labelColor: selected ? dialog.c.FG_BLUE_LIGHT : dialog.c.FG_MUTED
                            onClicked: dialog.backend.setAttrib(modelData.key)
                        }
                    }
                }

                // Group Name
                RakLabel {
                    text: dialog.s.ps_group_name
                    font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1
                    color: dialog.c.FG_BLUE_MUTED
                }
                Row {
                    width: parent.width; spacing: 6
                    Repeater {
                        model: ["qti_dynamic_partitions", "main", "mot_dp_group"]
                        delegate: AndroidToolButton {
                            required property string modelData
                            colors: dialog.c; text: modelData
                            selected: dialog.backend.groupName === modelData
                            onClicked: {
                                groupField.text = modelData
                                dialog.backend.setGroupName(modelData)
                            }
                        }
                    }
                }
                RakField {
                    id: groupField
                    objectName: "packSuperGroup"
                    width: parent.width; height: 30
                    text: dialog.backend.groupName; font.pixelSize: 12; cornerRadius: 6
                    leftPadding: 10; rightPadding: 10; topPadding: 1; bottomPadding: 0
                    fillColor: dialog.c.b04; edgeColor: dialog.c.b10; focusEdgeColor: dialog.c.a45
                    labelColor: dialog.c.FG
                    onTextEdited: dialog.backend.setGroupName(text)
                }

                // Super Size + Auto button
                Item {
                    width: parent.width; height: 16
                    RakLabel {
                        text: dialog.s.ps_super_size
                        font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1
                        color: dialog.c.FG_BLUE_MUTED
                    }
                    RakLabel {
                        anchors.right: parent.right
                        text: dialog.backend.superSizeLabel + "  (min " + dialog.backend.minSuperSizeLabel + ")"
                        font.pixelSize: 10; color: dialog.c.FG3
                    }
                }
                Row {
                    width: parent.width; spacing: 6
                    RakField {
                        id: sizeField
                        objectName: "packSuperSize"
                        width: parent.width - recalcBtn.width - 6; height: 30
                        text: dialog.backend.superSize; font.pixelSize: 12; cornerRadius: 6
                        leftPadding: 10; rightPadding: 10; topPadding: 1; bottomPadding: 0
                        fillColor: dialog.c.b04; edgeColor: dialog.c.b10; focusEdgeColor: dialog.c.a45
                        labelColor: dialog.c.FG
                        onTextEdited: dialog.backend.setSuperSize(text)
                    }
                    DialogButton {
                        id: recalcBtn
                        objectName: "packSuperRecalc"
                        colors: dialog.c; text: dialog.s.ps_recalc; implicitHeight: 30
                        onClicked: dialog.backend.recalcSize()
                    }
                }

                // Output File
                RakLabel {
                    text: dialog.s.ps_output_name
                    font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 1
                    color: dialog.c.FG_BLUE_MUTED
                }
                RakField {
                    id: outputField
                    objectName: "packSuperOutput"
                    width: parent.width; height: 30
                    text: dialog.backend.outputName; font.pixelSize: 12; cornerRadius: 6
                    leftPadding: 10; rightPadding: 10; topPadding: 1; bottomPadding: 0
                    fillColor: dialog.c.b04; edgeColor: dialog.c.b10; focusEdgeColor: dialog.c.a45
                    labelColor: dialog.c.FG
                    onTextEdited: dialog.backend.setOutputName(text)
                }

                // Options: Enable Sparse & Remove source files
                Row {
                    width: parent.width; spacing: 18; topPadding: 4
                    Row {
                        spacing: 8
                        RakSwitch {
                            objectName: "packSuperSparse"
                            preferences: dialog.model
                            checked: dialog.backend.sparse
                            onToggled: dialog.backend.setSparse(checked)
                        }
                        RakLabel {
                            height: 24; text: dialog.s.ps_sparse
                            font.pixelSize: 12; color: dialog.c.FG_MUTED
                        }
                    }
                    Row {
                        spacing: 8
                        RakSwitch {
                            objectName: "packSuperDeleteSources"
                            preferences: dialog.model
                            checked: dialog.backend.deleteSources
                            onToggled: dialog.backend.setDeleteSources(checked)
                        }
                        RakLabel {
                            height: 24; text: dialog.s.ps_delete_src
                            font.pixelSize: 12; color: dialog.c.FG_MUTED
                        }
                    }
                }
            }
        }

        Item {
            id: footer
            x: 24; width: parent.width - 48; height: 48
            anchors.bottom: parent.bottom; anchors.bottomMargin: 16
            SmoothRectangle { width: parent.width; height: 1; color: dialog.c.b08 }
            RakLabel {
                x: 0; anchors.bottom: parent.bottom; height: 32
                width: Math.max(80, parent.width - btns.width - 16)
                text: dialog.backend.validationHint
                elide: Text.ElideRight; font.pixelSize: 11
                color: dialog.c.RED_LIGHT || "#e06c75"
                visible: text.length > 0
            }
            Row {
                id: btns
                anchors.right: parent.right; anchors.bottom: parent.bottom; spacing: 8
                DialogButton {
                    objectName: "packSuperCancel"
                    colors: dialog.c; text: dialog.s.ps_cancel
                    onClicked: dialog.dismiss()
                }
                DialogButton {
                    objectName: "packSuperSubmit"
                    colors: dialog.c; primary: true
                    text: dialog.s.ps_pack
                    enabled: dialog.backend.canPack
                    onClicked: dialog.backend.pack()
                }
            }
        }
    }
}
