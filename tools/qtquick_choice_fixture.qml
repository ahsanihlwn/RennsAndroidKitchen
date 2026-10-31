// SPDX-License-Identifier: MPL-2.0
import QtQuick
import QtQuick.Window
import "../ui/controls"

Window {
    id: window
    width: 700; height: 500
    required property var uiModel
    readonly property Item backdropSurface: surface
    property int behindClicks: 0
    property int choicesCommitted: 0
    property bool commitWhileHidden: false
    property string widthKey: "en"
    property string deferredWidthKey: ""
    property int commitDelay: 0
    color: uiModel.colors.BG2
    Rectangle {
        id: surface
        anchors.fill: parent
        color: uiModel.colors.BG2
        ActionButton {
            objectName: "behindButton"
            x: 20; y: 20; width: 150; height: 32
            colors: window.uiModel.colors; text: "Behind dropdown"
            onClicked: window.behindClicks++
        }
        ChoiceButton {
            objectName: "fixtureChoice"
            x: 460; y: 300; width: 180; height: 28
            colors: window.uiModel.colors
            text: "Medium"
            currentKey: "medium"
            choices: [{key: "low", label: "Low"}, {key: "medium", label: "Medium"}, {key: "high", label: "High"}]
            onChosen: key => { window.commitWhileHidden=morphHidden; window.choicesCommitted++; text=choices.find(choice=>choice.key===key).label; currentKey=key }
        }
        ChoiceButton {
            objectName: "widthChoice"
            anchors.right: parent.right; anchors.rightMargin: 60
            y: 150; height: 28
            colors: window.uiModel.colors
            currentKey: window.widthKey
            choices: [{key: "en", label: "English"}, {key: "long", label: "A much longer translated option"}, {key: "id", label: "ID"}]
            text: choices.find(choice=>choice.key===window.widthKey).label
            onChosen: key => {
                if (window.commitDelay) { window.deferredWidthKey=key; deferredCommit.restart() }
                else window.widthKey=key
            }
        }
    }
    Timer { id: deferredCommit; interval: window.commitDelay; onTriggered: window.widthKey=window.deferredWidthKey }
}
