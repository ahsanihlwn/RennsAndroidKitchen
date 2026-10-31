// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Dialogs as D

DialogWindow {
    id: dialog; objectName: "workspaceFlashable"
    required property var workspace
    readonly property var backend: workspace.flashableBuilder
    property bool showScript: false
    implicitWidth: 760
    implicitHeight: Math.min(hostWindow.height-24,showScript?760:560)
    function showBuilder(data) {showScript=false;globalZstd.checked=true;backend.prepare(data.path,data.base);open()}
    function dismiss() {if(backend.cancel())close()}
    closePolicy: C.Popup.NoAutoClose
    RakLabel { x: 28; y: 24; height: 22; text: dialog.s.fl_title; font.pixelSize: 14; font.weight: Font.DemiBold; color: dialog.c.FG }
    RakScrollView {
        colors: dialog.c
        id: scroll; x: 28; y: 56; width: parent.width-56; height: parent.height-y-footer.height-24; clip: true
        contentWidth: availableWidth; enabled: !dialog.backend.busy
        Column {
            width: scroll.availableWidth-6; spacing: 10
            RakLabel { text: dialog.s.fl_partitions; font.pixelSize: 11; font.weight: Font.DemiBold; color: dialog.c.FG_BLUE_MUTED }
            RakLabel { width: parent.width; text: dialog.s.fl_hint_parts; font.pixelSize: 10; color: dialog.c.FG_DESC; wrapMode: Text.WordWrap }
            Item {
                width: parent.width; height: 14
                RakLabel { x: 10; width: 178; text: dialog.s.fl_col_partition; font.pixelSize: 9; font.weight: Font.DemiBold; color: dialog.c.FG_DESC }
                RakLabel { x: 196; width: 70; text: dialog.s.fl_col_role; font.pixelSize: 9; font.weight: Font.DemiBold; color: dialog.c.FG_DESC }
                RakLabel { x: 274; width: 90; text: dialog.s.fl_col_size; horizontalAlignment: Text.AlignRight; font.pixelSize: 9; font.weight: Font.DemiBold; color: dialog.c.FG_DESC }
            }
            ListView {
                id: partitions; objectName: "flashablePartitions"; width: parent.width; height: 190; spacing: 4; clip: true; model: dialog.backend.rows
                C.ScrollBar.vertical: RakScrollBar { handleColor: dialog.c.BG_SCROLL }
                delegate: SmoothRectangle {
                    id: part; required property var modelData; required property int index
                    width: partitions.width; height: 42; radius: 8; color: dialog.c.b03; border.width: 1; border.color: dialog.c.b06
                    RoundCheck { x: 10; y: 11; colors: dialog.c; checked: modelData.checked; onToggled: dialog.backend.edit(index,"checked",checked) }
                    RakField { objectName: "flashableName"+index; x: 38; y: 6; width: 150; height: 30; text: modelData.name; font.pixelSize: 12; fillColor: dialog.c.b04; edgeColor: dialog.c.b10; labelColor: dialog.c.FG; leftPadding: 8; rightPadding: 8; topPadding: 1; bottomPadding: 0; onTextEdited: dialog.backend.edit(index,"name",text) }
                    AndroidToolButton { objectName: "flashableRole"+index; x: 196; y: 9; width: 84; colors: dialog.c; text: modelData.role==="dynamic"?"dynamic":"both slot"; onClicked: dialog.backend.edit(index,"role",modelData.role==="dynamic"?"both_slot":"dynamic") }
                    RakLabel { x: 288; width: 90; height: parent.height; text: modelData.sizeLabel; horizontalAlignment: Text.AlignRight; font.pixelSize: 11; color: modelData.sizeLabel==="ERR"?dialog.c.RED_LIGHT:dialog.c.FG_BLUE_MUTED }
                    RakLabel { x: 386; height: parent.height; text: dialog.s.fl_col_zstd; font.pixelSize: 10; color: dialog.c.FG_DESC }
                    RoundCheck { objectName: "flashableZstd"+index; x: 430; y: 11; colors: dialog.c; checked: modelData.zstd; enabled: modelData.role==="dynamic" && dialog.backend.vendor64; onToggled: dialog.backend.edit(index,"zstd",checked) }
                    RakButton { anchors.right: parent.right; anchors.rightMargin: 8; y: 10; width: 22; height: 22; text: "✕"; edgeWidth: 0; leftPadding: 0; rightPadding: 0; labelColor: dialog.c.FG_DESC; hoverLabelColor: dialog.c.RED_LIGHT; onClicked: dialog.backend.remove(index) }
                }
            }
            DialogButton { colors: dialog.c; text: dialog.s.fl_add_partition; onClicked: addPicker.open() }
            SmoothRectangle { width: parent.width; height: 1; color: dialog.c.b07 }
            Row {
                spacing: 8
                RoundCheck { objectName: "flashableGetprop"; colors: dialog.c; checked: dialog.backend.getprop; onToggled: dialog.backend.setGetprop(checked) }
                RakLabel { height: 20; text: dialog.s.fl_getprop; font.pixelSize: 12; color: dialog.c.FG_MUTED }
            }
            Row {
                width: parent.width; spacing: 8
                RakLabel { width: 180; height: 30; text: dialog.s.fl_device_code; font.pixelSize: 10; color: dialog.c.FG_DESC }
                RakField { objectName: "flashableDevice"; width: Math.max(80,parent.width-188-readVendor.width-8); height: 30; text: dialog.backend.device; enabled: dialog.backend.getprop; fillColor: dialog.c.b04; edgeColor: dialog.c.b10; labelColor: dialog.c.FG; onTextEdited: dialog.backend.setDevice(text) }
                DialogButton { id: readVendor; colors: dialog.c; enabled: dialog.backend.getprop; text: dialog.s.fl_read_vendor; onClicked: dialog.backend.readVendor() }
            }
            Row {
                spacing: 8
                RoundCheck { id: globalZstd; objectName: "flashableGlobalZstd"; colors: dialog.c; checked: true; enabled: dialog.backend.vendor64; onToggled: dialog.backend.setZstd(checked) }
                RakLabel { height: 20; text: dialog.s.fl_zstd_compress; font.pixelSize: 12; color: dialog.c.FG_MUTED }
            }
            RakLabel { visible: !dialog.backend.vendor64; text: dialog.s.fl_zstd_no64; font.pixelSize: 10; color: dialog.c.RED_LIGHT }
            SmoothRectangle { width: parent.width; height: 1; color: dialog.c.b07 }
            Item {
                width: parent.width; height: 30
                Row { spacing: 8; anchors.verticalCenter: parent.verticalCenter; RoundCheck { objectName: "flashableShowScript"; colors: dialog.c; checked: dialog.showScript; onToggled: dialog.showScript=checked } RakLabel { height: 20; text: dialog.s.fl_script_preview; font.pixelSize: 11; color: dialog.c.FG_BLUE_MUTED; font.weight: Font.DemiBold } }
                DialogButton { objectName: "flashableRegenerate"; anchors.right: parent.right; colors: dialog.c; visible: dialog.showScript; text: dialog.s.fl_regenerate; onClicked: dialog.backend.regenerate() }
            }
            RakScrollView {
                colors: dialog.c
                width: parent.width; height: 200; visible: dialog.showScript
                C.TextArea {
                    id: script; objectName: "flashableScript"; text: dialog.backend.script
                    font.family: "Cascadia Code"; font.pixelSize: 12; wrapMode: TextEdit.NoWrap
                    color: dialog.c.FG; selectionColor: dialog.c.a30; selectByMouse: true
                    padding: 8; onTextChanged: if(activeFocus)dialog.backend.editScript(text)
                    background: SmoothRectangle { radius: 8; color: dialog.c.b04; border.width: 1; border.color: script.activeFocus?dialog.c.a35:dialog.c.b10 }
                }
            }
        }
    }
    Item {
        id: footer; x: 28; width: parent.width-56; height: dialog.backend.busy || dialog.backend.progress>0?100:50
        anchors.bottom: parent.bottom; anchors.bottomMargin: 16
        SmoothRectangle { width: parent.width; height: 1; color: dialog.c.b07 }
        Item {
            width: parent.width; height: 44; y: 5; visible: footer.height>50
            RakLabel { width: parent.width-42; height: 24; text: dialog.backend.status; elide: Text.ElideRight; font.pixelSize: 11; color: dialog.c.FG_BLUE_MUTED }
            RakLabel { anchors.right: parent.right; height: 24; text: dialog.backend.progress+"%"; font.pixelSize: 11; color: dialog.c.FG_BLUE_LIGHT }
            SmoothRectangle { y: 30; width: parent.width; height: 6; radius: 3; color: dialog.c.b10; SmoothRectangle { width: parent.width*dialog.backend.progress/100; height: 6; radius: 3; color: dialog.c.accent_hex } }
        }
        Row {
            anchors.right: parent.right; anchors.bottom: parent.bottom; spacing: 8
            DialogButton { objectName: "flashableCancel"; colors: dialog.c; text: dialog.s.fl_cancel; onClicked: dialog.dismiss() }
            DialogButton { objectName: "flashableCreate"; colors: dialog.c; primary: true; text: dialog.backend.busy?dialog.s.fl_building:dialog.s.fl_create; enabled: !dialog.backend.busy && !dialog.backend.loading; onClicked: savePicker.open() }
        }
    }
    D.FileDialog { id: addPicker; title: dialog.s.fl_add_partition; fileMode: D.FileDialog.OpenFiles; nameFilters: ["Partition images (*.img *.zst *.zstd *.br *.dat *.bin)","All files (*)"]; onAccepted: dialog.backend.addFiles(selectedFiles) }
    D.FileDialog { id: savePicker; title: dialog.s.fl_save_as; fileMode: D.FileDialog.SaveFile; nameFilters: ["Flashable zip (*.zip)"]; selectedFile: dialog.backend.defaultOutput; onAccepted: dialog.backend.build(selectedFile) }
    Shortcut { sequence: "Escape"; enabled: dialog.visible && dialog.active; onActivated: dialog.dismiss() }
    Connections { target: dialog.backend; function onDismissRequested() {dialog.close()} }
}
