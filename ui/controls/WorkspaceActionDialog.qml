// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Dialogs as D

DialogWindow {
    id: dialog
    objectName: "workspaceActionDialog"
    required property var workspace
    property var details: ({})
    implicitWidth: details.width || 400
    implicitHeight: Math.max(details.height || 0,form.height+110)
    function showForm(data) {
        details=data
        for (let i=0;i<fields.count;++i) fields.itemAt(i)?.reset()
        open()
    }
    function submit() {
        let values={}
        for (let i=0;i<fields.count;++i) {
            const field=fields.itemAt(i)
            if (field) values[field.modelData.key]=field.value
        }
        workspace.dispatch(details.action || "submitForm",{token:details.token,values:values})
        dismiss()
    }
    D.FileDialog {
        id: fieldFilePicker
        property var targetField: null
        function urlToLocalPath(u) {
            let s = u.toString()
            if (s.startsWith("file:///")) {
                s = s.slice(8)
                if (/^[A-Za-z]:/.test(s)) return decodeURIComponent(s)
                return "/" + decodeURIComponent(s)
            }
            return decodeURIComponent(s)
        }
        onAccepted: {
            const local = urlToLocalPath(selectedFile)
            if (targetField) targetField.text = local
        }
    }
    RakLabel { x: 20; y: 18; text: dialog.details.title || ""; font.pixelSize: 14; font.weight: Font.DemiBold; color: dialog.c.FG }
    Column {
        id: form; x: 20; y: 52; width: parent.width-40; spacing: 12
        RakLabel { visible: text.length>0; width: parent.width; text: dialog.details.description || ""; wrapMode: Text.WordWrap; font.pixelSize: 12; color: dialog.c.FG2 }
        Repeater {
            id: fields
            model: dialog.details.fields || []
            delegate: Column {
                id: field
                required property var modelData
                readonly property string value: modelData.choices ? choice.selectedValue : textInput.text
                function reset() {
                    const initial=String(modelData.value === undefined ? "" : modelData.value)
                    textInput.text=initial
                    choice.selectedValue=initial
                }
                width: form.width; spacing: 5
                RakLabel { visible: text.length>0; text: field.modelData.label || ""; font.pixelSize: 11; color: dialog.c.FG2 }
                Row {
                    width: parent.width; spacing: 6; visible: !field.modelData.choices
                    RakField {
                        id: textInput
                        objectName: "workspaceForm_"+field.modelData.key
                        width: field.modelData.browseFile ? parent.width - 82 : parent.width
                        height: 36
                        text: field.modelData.value || ""; placeholderText: field.modelData.placeholder || ""
                        fillColor: dialog.c.BG_INPUT; edgeColor: dialog.c.b18; focusEdgeColor: dialog.c.a55
                        labelColor: dialog.c.FG; hintColor: dialog.c.placeholder
                        Keys.onReturnPressed: dialog.submit()
                    }
                    DialogButton {
                        visible: !!field.modelData.browseFile
                        colors: dialog.c; text: "Browse"; implicitWidth: 76; implicitHeight: 36
                        onClicked: {
                            fieldFilePicker.targetField = textInput
                            fieldFilePicker.title = field.modelData.browseTitle || ("Select " + (field.modelData.label || "File"))
                            fieldFilePicker.nameFilters = field.modelData.nameFilters || ["All Files (*)"]
                            fieldFilePicker.open()
                        }
                    }
                }
                WorkspaceChoiceButton {
                    id: choice
                    objectName: "workspaceFormChoice_"+field.modelData.key
                    workspace: dialog.workspace; popupBoundsItem: dialog.contentItem
                    popupBackdropSource: dialog.backdropSurface
                    property string selectedValue: String(field.modelData.value===undefined?"":field.modelData.value)
                    visible: !!field.modelData.choices; colors: dialog.c; currentKey: selectedValue
                    choices: field.modelData.choices || []
                    text: {
                        const selected=options.find(option=>option.key===choice.selectedValue)
                        return selected?selected.label:selectedValue
                    }
                    onChosen: key => {selectedValue=key}
                }
            }
        }
    }
    Row {
        anchors.right: parent.right; anchors.rightMargin: 20; y: form.y+form.height+18; spacing: 8
        DialogButton { colors: dialog.c; text: dialog.s.main_btn_cancel; onClicked: dialog.dismiss() }
        DialogButton { objectName: "workspaceFormSubmit"; colors: dialog.c; primary: true; text: dialog.details.accept || dialog.s.main_btn_apply; onClicked: dialog.submit() }
    }
}
