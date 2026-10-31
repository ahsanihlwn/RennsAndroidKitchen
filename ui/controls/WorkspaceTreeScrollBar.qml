// SPDX-License-Identifier: MPL-2.0
import QtQuick

RakScrollBar {
    id: bar
    required property var colors
    handleColor: colors.b35; hoverColor: colors.b55; downColor: hoverColor
    minimumSize: Math.min(1,20/Math.max(1,orientation===Qt.Vertical?height:width))
}
