// SPDX-License-Identifier: MPL-2.0
import QtQuick

Item {
    id: binding
    required property var target
    required property string kind
    property string label: ""
    property var activate: null
    property bool permitted: true
    readonly property var api: typeof rakAgentControls!=="undefined"?rakAgentControls:null
    readonly property var descriptor: ({name:target.objectName || (label?kind+":"+label:""),label:label || target.objectName,kind:kind,visible:target.visible,enabled:target.enabled && permitted})
    property string registration: ""
    property bool highlighted: false
    signal actionRequested(string requestId,var payload)
    anchors.fill: parent
    Component.onCompleted: if(api)registration=api.register(binding)
    function reveal() {
        let child=target,parent=target.parent
        while(parent) {
            if(parent.contentY!==undefined && parent.contentHeight!==undefined) {
                const point=child.mapToItem(parent.contentItem || parent,0,0)
                parent.contentY=Math.max(0,Math.min(parent.contentHeight-parent.height,point.y-(parent.height-child.height)/2))
            }
            child=parent;parent=parent.parent
        }
        highlighted=true;highlightTimer.restart()
    }
    onActionRequested: (requestId,payload)=>{
        try {
            if(!target.visible || !target.enabled || !permitted)throw new Error("This control is unavailable")
            reveal()
            if(!payload.scroll) {
                if(!activate)throw new Error("This control has no registered action")
                activate(payload.value)
            }
            api.finished(requestId,true,payload.scroll?"Control revealed":"Control activated; check operation events for completion")
        }catch(error){api.finished(requestId,false,String(error))}
    }
    Timer { id: highlightTimer; interval: 4500; onTriggered: binding.highlighted=false }
    AgentRainbow { anchors.fill: parent; active: binding.highlighted; animated: typeof rakPreferences!=="undefined" && rakPreferences.settings.animations }
}
