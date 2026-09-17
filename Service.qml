import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons
import qs.Ui

// Service: keeps the companion daemon running while the shell is up and
// renders the companion's spoken output as always-on-top toasts.
Item {
  id: root
  property var shell: null

  readonly property string home: Quickshell.env("HOME")
  readonly property string stateDir: (Quickshell.env("XDG_STATE_HOME") !== "" ? Quickshell.env("XDG_STATE_HOME") : home + "/.local/state") + "/hermes-companion"

  // ---------------------------------------------------------------- daemon
  // Start the unit only when the Hermes runtime the installer recorded is present;
  // otherwise the widget shows "Hermes not found" instead of a restart loop.
  property string hermesDir: home + "/.hermes/hermes-agent"
  Process { id: svc }
  FileView {
    path: root.pluginDir + "/companion.json"
    printErrors: false
    onLoaded: { try { var c = JSON.parse(String(text() || "")); if (c.hermes_dir) root.hermesDir = c.hermes_dir } catch (e) {} ; installed.reload() }
    onLoadFailed: installed.reload()
  }
  readonly property string pluginDir: home + "/.config/omarchy/plugins/hermes.companion"
  // Written by install.sh on success. Missing => first enable after `omarchy plugin add`:
  // open the installer in a floating terminal (deps, systemd unit, keybinds).
  FileView {
    id: installed
    path: root.pluginDir + "/.installed"
    printErrors: false
    onLoaded: probe.reload()
    onLoadFailed: {
      console.log("hermes-companion: not installed yet, launching install.sh")
      svc.command = ["omarchy-launch-floating-terminal-with-presentation", root.pluginDir + "/install.sh"]
      svc.running = true
    }
  }
  FileView {
    id: probe
    path: root.hermesDir + "/venv/bin/python"
    printErrors: false
    onLoaded: { svc.command = ["systemctl", "--user", "start", "hermes-companion.service"]; svc.running = true }
    onLoadFailed: console.warn("hermes-companion: Hermes not found at", root.hermesDir, "- not starting daemon")
  }

  // ---------------------------------------------------------------- toasts
  property bool toastsEnabled: true
  readonly property int toastLifetime: 12000     // ms visible (pauses on hover)
  readonly property int toastWidth: Style.space(420)

  FileView {
    path: root.stateDir + "/state.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: { try { var s = JSON.parse(String(text() || "")); root.toastsEnabled = s.toasts !== false } catch (e) {} }
  }

  property double lastToastTs: 0
  FileView {
    id: toastFile
    path: root.stateDir + "/toast.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: root.ingest(text())
  }

  function ingest(content) {
    var t
    try { t = JSON.parse(String(content || "")) } catch (e) { return }
    if (!t || !t.ts || t.ts <= root.lastToastTs) return
    root.lastToastTs = t.ts
    if (!root.toastsEnabled) return
    toastModel.insert(0, { text: String(t.text || ""), kind: String(t.kind || "remark"), note: String(t.note || ""), ts: t.ts })
    while (toastModel.count > 5) toastModel.remove(toastModel.count - 1)
  }

  ListModel { id: toastModel }

  Variants {
    model: Quickshell.screens

    PanelWindow {
      id: win
      required property var modelData
      screen: modelData
      visible: toastModel.count > 0
      color: "transparent"
      WlrLayershell.namespace: "hermes-companion-toasts"
      WlrLayershell.layer: WlrLayer.Overlay
      WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
      exclusionMode: ExclusionMode.Ignore
      anchors { top: true; bottom: true; left: true; right: true }
      mask: Region { item: col }

      ColumnLayout {
        id: col
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.rightMargin: Style.gapsOut + Style.space(8)
        anchors.bottomMargin: Style.gapsOut + Style.space(8)
        spacing: Style.space(8)

        // Newest toast is inserted at index 0 (top of the stack); each toast
        // has its own timer so older ones leave last (first-in, last-out).
        Repeater {
          model: toastModel

          delegate: Item {
            id: slot
            required property int index
            required property string text
            required property string kind
            required property string note
            required property double ts
            readonly property bool quiet: kind === "observation"
            readonly property int lifetime: quiet ? root.toastLifetime * 0.6 : root.toastLifetime

            Layout.preferredWidth: root.toastWidth
            Layout.alignment: Qt.AlignRight
            implicitHeight: card.height

            property real remaining: 1.0
            property bool leaving: false
            readonly property bool ticking: !leaving && !hover.hovered

            Timer {
              interval: 50; repeat: true; running: slot.ticking
              onTriggered: {
                slot.remaining -= 50.0 / slot.lifetime
                if (slot.remaining <= 0) { slot.remaining = 0; slot.dismiss() }
              }
            }

            function dismiss() {
              if (slot.leaving) return
              slot.leaving = true
              leaveAnim.start()
            }

            SequentialAnimation {
              id: leaveAnim
              ParallelAnimation {
                NumberAnimation { target: card; property: "opacity"; to: 0; duration: 350; easing.type: Easing.InQuad }
                NumberAnimation { target: card; property: "x"; to: Style.space(40); duration: 350; easing.type: Easing.InQuad }
              }
              ScriptAction { script: { for (var i = 0; i < toastModel.count; i++) if (toastModel.get(i).ts === slot.ts) { toastModel.remove(i); break } } }
            }

            Rectangle {
              id: card
              width: root.toastWidth
              height: body.implicitHeight + Style.space(28)
              radius: Style.cornerRadius
              color: Util.alpha("#0b0d10", slot.quiet ? 0.72 : 0.86)
              border.width: 1
              border.color: Util.alpha(Color.popups.border, 0.7)
              opacity: 0
              x: Style.space(40)

              Component.onCompleted: enterAnim.start()
              ParallelAnimation {
                id: enterAnim
                NumberAnimation { target: card; property: "opacity"; to: 1; duration: 300; easing.type: Easing.OutQuad }
                NumberAnimation { target: card; property: "x"; to: 0; duration: 300; easing.type: Easing.OutCubic }
              }

              HoverHandler { id: hover }

              Column {
                anchors.fill: parent
                anchors.margins: Style.space(14)
                spacing: Style.space(6)

                Row {
                  spacing: Style.space(8)
                  Text {
                    text: slot.kind === "reply" ? "󰍬" : (slot.kind === "urgent" ? "󰀦" : (slot.kind === "held" ? "󰖁" : "󰛐"))
                    color: slot.kind === "urgent" ? Color.urgent : (slot.quiet ? Util.alpha("#ffffff", 0.5) : Color.accent)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                  }
                  Text {
                    text: "Hermes" + (slot.kind === "urgent" ? "  ·  urgent" : (slot.kind === "held" ? "  ·  held: " + slot.note : (slot.quiet ? "  ·  observing" : "")))
                    color: Util.alpha("#ffffff", 0.75)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    font.bold: true
                  }
                  Text {
                    text: Qt.formatTime(new Date(slot.ts * 1000), "HH:mm")
                    color: Util.alpha("#ffffff", 0.45)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                  }
                }

                Text {
                  id: body
                  width: card.width - Style.space(28)
                  wrapMode: Text.Wrap
                  text: slot.text
                  color: slot.quiet ? Util.alpha("#f4f4f4", 0.7) : "#f4f4f4"
                  font.family: Style.font.family
                  font.pixelSize: slot.quiet ? Style.font.bodySmall : Style.font.body
                  font.italic: slot.quiet
                }
              }

              // lifetime progress (hidden while hovered)
              Rectangle {
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                anchors.margins: 1
                height: 2
                color: "transparent"
                Rectangle {
                  height: parent.height
                  width: parent.width * slot.remaining
                  radius: 1
                  color: Util.alpha(Color.accent, hover.hovered ? 0.25 : 0.8)
                }
              }

              MouseArea {
                anchors.fill: parent
                acceptedButtons: Qt.LeftButton | Qt.MiddleButton
                onClicked: slot.dismiss()
              }
            }
          }
        }
      }
    }
  }
}

