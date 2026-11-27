// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Dialogs as D

D.FolderDialog {
    required property var hostWindow
    property string purpose: ""
    title: hostWindow ? hostWindow.strings.dlg_choose_folder : "Choose Folder"
    onAccepted: if (hostWindow && hostWindow.uiModel) hostWindow.uiModel.dispatch("folderSelected", {purpose: purpose, url: selectedFolder.toString()})
}
