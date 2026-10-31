// SPDX-License-Identifier: MPL-2.0
import QtQuick

Rectangle {
    // Keep straight pixel-aligned surfaces crisp; every rounded surface uses
    // the same antialiased edge, including animated fractional geometry.
    antialiasing: radius>0
}
