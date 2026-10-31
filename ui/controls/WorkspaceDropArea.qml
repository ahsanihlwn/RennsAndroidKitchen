// SPDX-License-Identifier: MPL-2.0
import QtQuick
DropArea {
    id: area
    required property var workspace
    required property int treeId
    required property string destination
    property var treePanel: null
    property var payload: ({})
    function updateTarget(event,value) {
        if(!treePanel)return
        const point=area.mapToItem(treePanel.view,event.x,event.y)
        treePanel.updateDrop(point.x,point.y,value)
    }
    function data(event) {
        const sourceData=event.source && event.source.dragMime?event.source.dragMime:null
        function value(format){return sourceData?(sourceData[format] || ""):event.formats.indexOf(format)>=0?event.getDataAsString(format):""}
        const archive=value("application/x-rak-archive-source")
        const internal=value("application/x-rak-tree")
        const mounted=value("application/x-rdt-mount")
        if(mounted){try{return {mounted:JSON.parse(mounted)}}catch(error){return {}}}
        if(archive)return {archive:archive,members:value("application/x-rdt-archive").split("\n")}
        if(internal){try{return JSON.parse(internal)}catch(error){return {}}}
        return {urls:event.urls}
    }
    onEntered: event => {
        event.accepted=!!(event.source && event.source.dragMime) || event.hasUrls || event.formats.indexOf("application/x-rak-tree")>=0 || event.formats.indexOf("application/x-rdt-archive")>=0 || event.formats.indexOf("application/x-rdt-mount")>=0
        if(event.accepted){payload=data(event);updateTarget(event,payload)}
    }
    onPositionChanged: event => updateTarget(event,payload)
    onExited: {if(treePanel)treePanel.clearDrop();payload=({})}
    onDropped: event => {
        const dropped=data(event)
        updateTarget(event,dropped)
        if(treePanel && !treePanel.dropAccepted){event.accepted=false;treePanel.clearDrop();return}
        const target=treePanel?treePanel.dropDestination:destination
        if(treePanel)treePanel.clearDrop()
        workspace.transfers.dropInto(treeId,target,dropped,!!dropped.base)
        event.acceptProposedAction();payload=({})
    }
}
