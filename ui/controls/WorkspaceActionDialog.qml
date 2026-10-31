// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

DialogWindow {
    id: dialog
    objectName: "workspaceActionDialog"
    required property var workspace
    property var details: ({})
    property var values: ({})
    implicitWidth: details.width || 400
    implicitHeight: Math.max(details.height || 0,form.height+110)
    function showForm(data) {
        details=data;let initial={}
        for (let i=0;i<(data.fields || []).length;++i)initial[data.fields[i].key]=data.fields[i].value || ""
        values=initial;open()
    }
    function submit() { workspace.dispatch(details.action || "submitForm",{token:details.token,values:values});dismiss() }
    RakLabel { x: 20; y: 18; text: dialog.details.title || ""; font.pixelSize: 14; font.weight: Font.DemiBold; color: dialog.c.FG }
    Column {
        id: form; x: 20; y: 52; width: parent.width-40; spacing: 12
        RakLabel { visible: text.length>0; width: parent.width; text: dialog.details.description || ""; wrapMode: Text.WordWrap; font.pixelSize: 12; color: dialog.c.FG2 }
        Repeater {
            model: dialog.details.fields || []
            delegate: Column {
                id: field
                required property var modelData
                width: form.width; spacing: 5
                RakLabel { visible: text.length>0; text: field.modelData.label || ""; font.pixelSize: 11; color: dialog.c.FG2 }
                RakField {
                    objectName: "workspaceForm_"+field.modelData.key
                    width: parent.width; height: 36; visible: !field.modelData.choices
                    text: field.modelData.value || ""; placeholderText: field.modelData.placeholder || ""
                    fillColor: dialog.c.BG_INPUT; edgeColor: dialog.c.b18; focusEdgeColor: dialog.c.a55
                    labelColor: dialog.c.FG; hintColor: dialog.c.placeholder
                    onTextChanged: dialog.values[field.modelData.key]=text
                    Keys.onReturnPressed: dialog.submit()
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
                    onChosen: key => {selectedValue=key;dialog.values[field.modelData.key]=key}
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
