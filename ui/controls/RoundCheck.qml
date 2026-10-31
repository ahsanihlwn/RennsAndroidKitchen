// SPDX-License-Identifier: MPL-2.0
import QtQuick
RakButton {
    required property var colors
    width: 20; height: 20; implicitWidth: 20; implicitHeight: 20
    checkable: true; leftPadding: 0; rightPadding: 0; cornerRadius: 10
    fillColor: checked?colors.a55:colors.b05; edgeColor: checked?colors.a80:colors.b14
    hoverFillColor: checked?colors.a65:colors.a08; hoverEdgeColor: colors.a50
    disabledFillColor: colors.b03; disabledEdgeColor: colors.b05
    iconSource: checked?"image://icons/check_white":""; iconWidth: 14; iconHeight: 14
}
