// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Controls as C
import QtQuick.Window

ActionButton {
    id: control
    property var choices: []
    readonly property var options: choices.map(choice=>({key:String(choice.key===undefined?choice.value:choice.key),label:choice.label}))
    property Item popupBoundsItem: null
    property Item popupBackdropSource: null
    property string currentKey: ""
    property var previewCaption: null
    readonly property string displayText: previewCaption===null ? text : previewCaption
    readonly property bool arrowReady: caption.arrowReady
    signal chosen(string key)
    implicitHeight: 28
    implicitWidth: Math.max(80, Math.ceil(caption.implicitWidth) + 35)
    property color backdropColor: colors.BG2
    cornerRadius: 6
    fillColor: "transparent"
    hoverFillColor: colors.b06
    downFillColor: colors.b10
    disabledFillColor: "transparent"
    edgeWidth: 0
    edgeColor: "transparent"
    hoverEdgeColor: "transparent"
    disabledEdgeColor: "transparent"
    labelColor: colors.FG
    hoverLabelColor: colors.FG_BRIGHT
    leftAligned: true
    leftPadding: 12
    rightPadding: 9
    contentItem: ChoiceCaption {
        id: caption
        colors: control.colors; text: control.displayText; captionFont: control.font
        labelColor: control.visualHovered ? control.hoverLabelColor : control.labelColor
        moving: control.transformed
        arrowName: "dropdownArrow_"+control.objectName
    }
    property Component returnCaption: Component {
        Item {
            ChoiceCaption {
                x: control.leftPadding; y: control.topPadding
                width: parent.width-control.leftPadding-control.rightPadding
                height: parent.height-control.topPadding-control.bottomPadding
                colors: control.colors; text: control.displayText; captionFont: control.font
                labelColor: control.labelColor
                moving: true
            }
        }
    }
    onTextChanged: if (previewCaption!==null && text===previewCaption) previewCaption=null
    function selectionCaption(choice){return choice.label}
    function pickChoice(index) {
        if (!menu.opening || menu.picked.length || index<0 || index>=options.length) return
        const choice=options[index]
        const option=optionRepeater.itemAt(index)
        menu.captionStart=option ? option.mapToItem(menu.contentItem,option.width/2,option.height/2) : null
        menu.picked=choice.key
        // Lay out the destination while the real button is hidden. Returning
        // to the opening image then changing text/width onClosed causes a pop.
        previewCaption=selectionCaption(choice)
        menu.returnToSelection=true
        menu.close()
    }
    function openChoices() {
        resetPressFeedback()
        const host=control.Window.window.contentItem
        const bounds=popupBoundsItem?popupBoundsItem.mapToItem(host,0,0,popupBoundsItem.width,popupBoundsItem.height):Qt.rect(0,0,host.width,host.height)
        const anchor=control.mapToItem(host,0,0,control.width,control.height)
        menu.width=Math.min(bounds.width,Math.ceil(Math.max(140,control.width,menu.optionWidth+8)))
        const desired=8+control.choices.length*33-1
        const below=bounds.y+bounds.height-anchor.y, above=anchor.y+anchor.height-bounds.y
        menu.upward=desired>below && above>below
        menu.height=Math.min(desired,menu.upward?above:below)
        menu.x=Math.max(bounds.x,Math.min(anchor.x+anchor.width-menu.width,bounds.x+bounds.width-menu.width))
        menu.y=menu.upward ? anchor.y+anchor.height-menu.height : anchor.y
        menu.open()
    }
    onClicked: openChoices()
    onVisibleChanged: if (!visible) menu.dismissImmediate()
    Connections {
        target: control.Window.window
        function onWidthChanged() { menu.dismissImmediate() }
        function onHeightChanged() { menu.dismissImmediate() }
        function onXChanged() { menu.dismissImmediate() }
        function onYChanged() { menu.dismissImmediate() }
    }
    MorphPopup {
        id: menu
        z: 10000
        parent: control.Window.window ? control.Window.window.contentItem : null
        objectName: "choicePopup_" + control.objectName
        sourceItem: control
        springMotion: false
        morphOnClose: true
        animationsEnabled: control.animatePress
        backdropSource: control.popupBackdropSource || (control.Window.window ? control.Window.window.backdropSurface : null)
        effectsEnabled: typeof rakPreferences!=="undefined" ? rakPreferences.settings.effects!=="low" : true
        property string picked: ""
        property int focusedIndex: 0
        property int optionsRevision: 0
        // Qt.Popup in the reference consumes the dismissing press. A
        // non-modal Quick popup forwards it to buttons on the page below.
        modal: true
        dim: false
        focus: true
        function focusOption(index) {
            focusedIndex=(index+optionRepeater.count)%optionRepeater.count
            const button=optionRepeater.itemAt(focusedIndex)
            if (!button) return
            button.forceActiveFocus(Qt.PopupFocusReason)
            if (button.y<optionsScroll.contentY) optionsScroll.contentY=button.y
            else if (button.y+button.height>optionsScroll.contentY+optionsScroll.height) optionsScroll.contentY=button.y+button.height-optionsScroll.height
        }
        readonly property real optionWidth: {
            const revision=optionsRevision
            let result=0
            for (let i=0;i<optionRepeater.count;++i) {
                const option=optionRepeater.itemAt(i)
                if(option)result=Math.max(result,option.implicitWidth)
            }
            return result
        }
        padding: 0
        closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside
        panelColor: control.colors.BG_MODAL
        tintBaseColor: control.colors.BG2
        panelBorder: typeof rakPreferences!=="undefined" ? rakPreferences.panelBorder : control.colors.b10
        sourceBackdropColor: control.backdropColor
        onAboutToShow: {
            picked=""
            if(control.Window.window.dialogStack)control.Window.window.dialogStack.registerPopup(menu)
        }
        onAboutToHide: { for (let i=0;i<optionRepeater.count;++i) optionRepeater.itemAt(i).resetPressFeedback() }
        onOpened: {
            let index=control.options.findIndex(choice=>choice.key===control.currentKey)
            focusOption(Math.max(0,index))
        }
        onClosed: {
          if(control.Window.window.dialogStack)control.Window.window.dialogStack.unregisterPopup(menu)
          if (picked.length) {
            const key=picked
            picked=""
            control.chosen(key)
            // Appearance changes can commit asynchronously after an old-frame
            // capture. Keep the selected caption until its bound text catches up.
            if (control.text===control.previewCaption) control.previewCaption=null
          }
        }
        Flickable {
            id: optionsScroll
            x: 4; y: 4; width: menu.width-8; height: menu.height-8; clip: true
            contentHeight: items.height; boundsBehavior: Flickable.StopAtBounds
            Keys.onPressed: event => {
                if (!menu.opening) { event.accepted=true; return }
                if ([Qt.Key_Up,Qt.Key_Down,Qt.Key_Home,Qt.Key_End,Qt.Key_Return,Qt.Key_Enter].indexOf(event.key)<0) return
                if (event.key===Qt.Key_Return || event.key===Qt.Key_Enter) control.pickChoice(menu.focusedIndex)
                else if (event.key===Qt.Key_Home) menu.focusOption(0)
                else if (event.key===Qt.Key_End) menu.focusOption(control.choices.length-1)
                else menu.focusOption(menu.focusedIndex+(event.key===Qt.Key_Down?1:-1))
                event.accepted=true
            }
            Column {
                id: items
                width: parent.width; spacing: 1
                Repeater {
                    id: optionRepeater
                    onItemAdded: menu.optionsRevision++
                    onItemRemoved: menu.optionsRevision++
                    model: control.options
                    delegate: RakButton {
                    required property var modelData
                    required property int index
                    objectName: "choice_" + control.objectName + "_" + modelData.key
                    width: items.width; height: 32
                    text: !menu.opening && menu.picked===modelData.key ? "" : modelData.label
                    leftAligned: true
                    edgeWidth: 0
                    edgeColor: "transparent"
                    hoverEdgeColor: "transparent"
                    fillColor: modelData.key === control.currentKey ? control.colors.a12 : "transparent"
                    hoverFillColor: control.colors.a14
                    downFillColor: control.colors.b08
                    labelColor: modelData.key === control.currentKey ? control.colors.FG_BLUE : control.colors.FG
                    hoverLabelColor: control.colors.FG_BRIGHT
                    font.weight: modelData.key === control.currentKey ? Font.DemiBold : Font.Normal
                    onActiveFocusChanged: if (activeFocus) menu.focusedIndex=index
                    onClicked: control.pickChoice(index)
                    }
                }
            }
        }
    }
}
