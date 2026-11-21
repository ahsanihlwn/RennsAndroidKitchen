// SPDX-License-Identifier: MPL-2.0
import QtQuick

// SplitView owns the target slot; this persistent native pane owns its displayed
// geometry. Reflow never reparents the tree, editor or terminal; the small
// appear/disappear scale settles at 1 for the native content.
Item {
    id: pane
    required property Item layoutTarget
    required property var motion
    property bool requestedVisible: true
    property Item adjacentPane: null
    property bool revealBelow: false
    property real adjacentGap: 12
    property bool initialized: false
    property rect startRect: Qt.rect(0,0,0,0)
    property rect endRect: Qt.rect(0,0,0,0)
    property real startOpacity: 0
    property real endOpacity: 0
    property real startScale: 1
    property real endScale: 1
    readonly property real settledWidth: (motion && motion.running)
        ? Math.max(1, requestedVisible ? (endRect.width > 0 ? endRect.width : width) : (startRect.width > 0 ? startRect.width : width))
        : width
    readonly property real settledHeight: (motion && motion.running)
        ? Math.max(1, requestedVisible ? (endRect.height > 0 ? endRect.height : height) : (startRect.height > 0 ? startRect.height : height))
        : height
    clip: motion && motion.running
    opacity: 0
    visible: opacity>0
    enabled: requestedVisible
    transformOrigin: Item.TopLeft

    function destination() {
        const point=layoutTarget.mapToItem(parent,0,0)
        return Qt.rect(point.x,point.y,layoutTarget.width,layoutTarget.height)
    }
    function needsUpdate() {
        if(!initialized || endOpacity!==(requestedVisible?1:0))return true
        if(!requestedVisible)return false
        const target=destination()
        return Math.abs(target.x-endRect.x)>.01 || Math.abs(target.y-endRect.y)>.01
            || Math.abs(target.width-endRect.width)>.01 || Math.abs(target.height-endRect.height)>.01
    }
    function prepare(animate) {
        startRect=Qt.rect(x,y,width,height);startOpacity=opacity;startScale=scale
        // Pin scaling to the divider corner. An editor above a terminal grows
        // from its lower edge; other panes grow from their upper-left edge.
        // Keep the origin through an interrupted reveal to avoid a visual jump.
        if(scale===1 || opacity===0)
            transformOrigin=adjacentPane && adjacentPane.requestedVisible && !revealBelow ? Item.BottomLeft : Item.TopLeft
        endOpacity=requestedVisible?1:0
        endScale=animate && !requestedVisible ? .96 : 1
        if(requestedVisible) {
            endRect=destination()
            if(!initialized || opacity===0) {
                startRect=endRect
                startOpacity=animate?0:1
                startScale=animate ? .96 : 1
                if(animate && adjacentPane && adjacentPane.requestedVisible && adjacentPane.opacity>0) {
                    // Open beside the moving sibling edge, rather than laying
                    // a translucent terminal over the editor while it shrinks.
                    startRect=Qt.rect(adjacentPane.x,revealBelow?adjacentPane.y+adjacentPane.height+adjacentGap:adjacentPane.y,adjacentPane.width,0)
                }
            }
        } else {
            endRect=startRect
            if(animate && adjacentPane && adjacentPane.requestedVisible && opacity>0) {
                const neighbor=adjacentPane.destination()
                endRect=Qt.rect(neighbor.x,revealBelow?neighbor.y+neighbor.height+adjacentGap:neighbor.y,neighbor.width,0)
            }
        }
        initialized=true
    }
    function step(amount) {
        // Keep layout coordinates on item pixels. The reveal scale is separate
        // from sizing, so settled content always returns to its native size.
        x=Math.round(startRect.x+(endRect.x-startRect.x)*amount)
        y=Math.round(startRect.y+(endRect.y-startRect.y)*amount)
        width=Math.max(0,Math.round(startRect.width+(endRect.width-startRect.width)*amount))
        height=Math.max(0,Math.round(startRect.height+(endRect.height-startRect.height)*amount))
        opacity=startOpacity+(endOpacity-startOpacity)*amount
        scale=startScale+(endScale-startScale)*amount
    }
    onRequestedVisibleChanged: if(motion)motion.schedule()
    onLayoutTargetChanged: if(motion)motion.schedule()
    Connections {
        target: pane.layoutTarget
        function onXChanged(){if(pane.motion)pane.motion.schedule()}
        function onYChanged(){if(pane.motion)pane.motion.schedule()}
        function onWidthChanged(){if(pane.motion)pane.motion.schedule()}
        function onHeightChanged(){if(pane.motion)pane.motion.schedule()}
    }
}
