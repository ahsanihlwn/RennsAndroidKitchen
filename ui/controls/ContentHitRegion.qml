// SPDX-License-Identifier: MPL-2.0
import QtQuick
import Renns.Input 1.0

// Glass may show content outside its interactive viewport. Apply the same
// boundary to press/release hit tests, including an already grabbed button.
ContentInputRegion {
    viewport: {
        let ancestor=item?item.parent:null
        while(ancestor) {
            if(ancestor.inputBounds!==undefined)return ancestor
            ancestor=ancestor.parent
        }
        return null
    }
}
