// SPDX-License-Identifier: MPL-2.0
import QtQuick

// Viewer models retain their index/value API; presentation and motion are global.
ChoiceButton {
    id: control
    property var model: []
    property string textRole: ""
    property string valueRole: ""
    readonly property int count: model ? model.length : 0
    property int currentIndex: count ? 0 : -1
    readonly property string currentText: currentIndex>=0 && currentIndex<count ? labelAt(currentIndex) : ""
    readonly property var currentValue: currentIndex>=0 && currentIndex<count ? valueAt(currentIndex) : undefined
    signal activated(int index)

    height: 28
    font.pixelSize: 11
    implicitWidth: {
        let longest=0
        for(let i=0;i<count;++i)longest=Math.max(longest,metrics.advanceWidth(labelAt(i)))
        return Math.max(73,Math.ceil(longest)+37)
    }
    FontMetrics { id: metrics; font: control.font }
    function labelAt(index) {return String(textRole ? model[index][textRole] : model[index])}
    function valueAt(index) {return valueRole ? model[index][valueRole] : model[index]}
    choices: {
        let entries=[]
        for(let i=0;i<count;++i)entries.push({key:String(i),label:labelAt(i)})
        return entries
    }
    text: currentText
    currentKey: String(currentIndex)
    onCountChanged: {
        if(!count)currentIndex=-1
        else if(currentIndex<0)currentIndex=0
        else if(currentIndex>=count)currentIndex=count-1
    }
    onChosen: key => {
        const index=Number(key)
        if(index<0 || index>=count)return
        currentIndex=index
        activated(index)
    }
}
