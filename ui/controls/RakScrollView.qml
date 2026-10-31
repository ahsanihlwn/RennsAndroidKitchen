// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C

C.ScrollView {
    id: control
    required property var colors
    C.ScrollBar.vertical: RakScrollBar { handleColor: control.colors.BG_SCROLL; hoverColor: control.colors.b50 }
    C.ScrollBar.horizontal: RakScrollBar { orientation: Qt.Horizontal; handleColor: control.colors.BG_SCROLL; hoverColor: control.colors.b50 }
}
