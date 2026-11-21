// SPDX-License-Identifier: MPL-2.0
import QtQuick

WorkspaceFilePickerDialog {
    id: dialog
    objectName: "workspaceRenxecPicker"
    backend: workspace.privateFeatures.filePicker
    pickerTitle: "Select Renxec File"
    openText: "Open"
    emptyText: "No .renxec or .zip files found"
    multiSelect: false
}
