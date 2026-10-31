// SPDX-License-Identifier: MPL-2.0
import QtQuick
import "../motion.js" as Motion

// Paint the reveal into the page's backdrop so glass samples the same animated
// pixels as the visible page. The shared clock also drives footer action motion.
Item {
    id: mask
    required property var motion
    required property var page
    property real topInset: 0
    property real bottomInset: 0
    visible: !!motion && motion.active && motion.targetPage===page
    enabled: false
    function coverPieces(top,bottom) {
        // Extend the nearest content band's reveal under each glass strip.
        // A strip boundary must not split a card into two animation phases.
        const a=Math.max(top,topInset), b=Math.min(bottom,height-bottomInset)
        if(b<=a || !motion)return []
        return motion.coverPieces(a===topInset?0:a,b===height-bottomInset?height:b,true)
    }
    Repeater {
        model: mask.motion ? mask.motion.bands : []
        delegate: Item {
            id: band
            required property var modelData
            required property int index
            readonly property real coverOpacity: Math.pow(1-Motion.clamp((mask.motion.elapsed-100-index*60)/280),3)
            Repeater {
                // A row keeps its page reveal while passing under the chrome.
                // Footer button motion has its own timing; it must not reveal
                // the obscured list/card pixels ahead of the same content.
                model: mask.coverPieces(band.modelData.top,band.modelData.bottom)
                delegate: SmoothRectangle {
                    required property var modelData
                    x: modelData.x; y: modelData.y; width: modelData.width; height: modelData.height
                    color: mask.motion.model.colors.BG; opacity: band.coverOpacity
                }
            }
        }
    }
}
