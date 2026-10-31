# Workspace context menu and transfers

Reference: `C:/Users/ahsanihlwn/PROJ/rennsandroidkitchen`, especially
`main_ext/FileTree_ext/RightClick.py`, `FileTree_ext/Clipboard.py` and
`class_FileTree.py` (paintEvent, dragMoveEvent, dropEvent and startDrag).
The audit imports that separate original and renders its actual popup controls
against temporary files. It does not launch the user's project or save theme
preferences.

## Geometry and appearance

`ui/menu_metrics.py` measures Poppins text using Qt's mnemonic-aware button
metrics. Single, batch, submenu and mounted menus keep their separate padding
and action/header heights. Menu geometry is resolved before opening, including
when a tall image menu is followed immediately by a shorter file menu.

At a 1080x700 window, captured original and Quick rectangles are identical:

| State, dark_green/id | x | y | Width | Height |
| --- | ---: | ---: | ---: | ---: |
| Basic file, top | 20 | 20 | 144 | 310 |
| Basic file, bottom right | 935 | 389 | 144 | 310 |
| EXT4 image, bottom right | 933 | 251 | 146 | 448 |
| Image conversion submenu, bottom right | 931 | 554 | 148 | 145 |
| Two text files, bottom right | 960 | 527 | 119 | 172 |

The original clamps to width-1/height-1. Its submenu overlaps its parent at the
right/bottom edge; it does not switch to a generic left-opening submenu.
The Quick implementation preserves that rule. Exceptionally tall menus are
capped to the viewport and scroll so their actions remain reachable.

The source BlurCard's 18 is blur radius, not corner radius. Menus use 10px
corners, BG2 at alpha128 and the neutral border at alpha45. Disabled actions use
their normal foreground at alpha128; batch headers keep FG_MUTED. Medium/High
use live GPU backdrop textures. Low uses the source flat BG_MODAL. Production
blur does not capture screenshots or read pixels back to the CPU.

Captured flat dark crops differ from the original by less than 0.5 RGB levels
per channel on average for these four menu states. This is evidence for the
captured controls, not a claim of identical pixels throughout the application.

Menus retain source action/group ordering, batch member filtering, separators,
hover/press feedback and 12px submenu arrows. They open on right release, close
on Escape/outside press and close when the host moves, resizes, hides or loses
activation. The popup is nonmodal. Both top and bottom-right submenu choices
are exercised with real pointer input and an inert tool invocation.

## Move, drag and clipboard

- An expanded folder highlights its row plus immediate children. A hovered file
  highlights its sibling group. Same-directory moves suppress the group but keep
  the source paintEvent's fallback highlight on the hovered row. The old note
  about suppressing the whole box was incorrect.
- Edge scrolling uses the source 30ms timer, 30% edge zone, power2.5 speed curve
  and 0.15..8 pixel range with subpixel accumulation. Leaving stops the timer.
- Pressing an already-selected row preserves a multi-file drag selection; a
  completed ordinary click collapses it. Row hover is suppressed during menus
  and drag. Selection and hover tint composite over row text/icons like the
  original post-paint overlay.
- A drag image samples selected visible rows, retaining gaps, metadata and the
  original pointer offset. It is generated only for starting a drag, with a
  viewport-sized bound. This drag pixmap is unrelated to live blur rendering.
- Drag uses an independent source item outside pooled delegates. Using the
  whole tree panel as Drag.source made its descendant DropArea ineligible in Qt
  ([Qt's source guard, QTBUG-64128](https://github.com/qt/qtdeclarative/blob/v6.10.1/src/quick/items/qquickdroparea.cpp#L220)),
  so source start/cancel tests could pass while real drops failed.
  In-window pointer movement/drop stays in the Quick scene, with the preview
  following the pointer; leaving the window promotes to native drag. Source
  payloads work for both routes. All columns support start/Escape cancellation.
  Complete pointer gestures verify same-ROM folder, cross-ROM folder and blank
  root drops, including actual source removal and destination file bytes.
- A persistent viewport MouseArea owns the pointer gesture. A pooled source
  delegate cancelled the drag when auto-scroll recycled its row, even with an
  independent Drag.source. Complete input now moves a folder past 50 rows with
  its source outside the viewport. Cancelling invalidates pending asynchronous
  drag-image callbacks, including a release followed by another immediate press.
- Windows SendInput exposed another failure: a single coalesced mouse movement
  did not activate MouseArea.drag. Threshold detection now starts the payload
  immediately, independently of asynchronous drag-image readiness. Windows input
  verifies two complete same-application moves and their actual bytes; the
  report is `drag_native/native_mouse_24.json`.
- APK folder rows and their Paste action target APKTOOL_LINK_ROLE's decompiled
  directory, matching the source dropEvent. A single-move gesture verifies
  extraction-folder contents and original APK byte preservation.
- Internal drops move; external URL/archive payloads copy. Cutting to the same
  directory consumes the cut clipboard without moving anything. Same-directory
  copies receive `(2)`, `(3)`, etc., including stripping an existing number before
  finding the next free name. Self-descendant rejection preserves the clipboard.
- Internal drag onto the dragged file/folder, or into its own descendants, is
  ignored before enqueueing and displays no error. The plan also treats such
  moves as no-ops; valid other members of a multi-selection can still move.
- Moves use OS rename for ordinary same-volume files/folders, including
  cross-ROM moves and self-contained branch overlays. Cross-device operations
  and inherited/merged branch views materialize bytes as required. Tests check
  original file/directory identity, not just matching destination bytes.
- Copy/Cut completion uses the original LoadingManager, including its fade
  timer and refusal to overwrite an active task's progress.
- Dropping a tree selection onto the terminal inserts its physical file paths,
  without moving files or submitting a command. Quick's in-scene payload is read
  explicitly; local file URIs decode before Windows/WSL shell quoting. The
  pointer verifier delivers a path containing a space through TerminalPane,
  with separate terminal-session checks for the actual quoted PTY writes.
- Tree scroll handles reveal on scroll/hover/press and hide after 1000ms idle.
- Drag auto-scroll starts in the outer 40% at either edge, with a neutral
  center and progressively higher speed toward the edge. The 16ms timer reaches
  24px/tick; this intentionally replaces the reference's slower 30%/8px profile.
- Empty directory rows retain italic gray text at alpha130 without a file icon.
- Missing/empty folder symlinks use that same Empty row, in both directory and
  tree modes. Generic Rust OSError for an unavailable folder-link target is
  handled as empty; failures listing an existing directory still propagate.
- Mounted selections use the source `application/x-rdt-mount` payload and copy
  extraction, with nested selections deduplicated. Dropping into virtual mounted
  nodes is rejected. A real EXT-image folder extraction/byte-preservation test
  passes. Cross-ROM APK moves use the source paired APK/decompile helper.
- Moving the project config folder or a filesystem root with an active branch
  is blocked before transfer. Stale transfer preflight callbacks are ignored.

APK context actions also restore source build/build-and-sign/keep-original/view
mode choices. Build targets the original APK; signing follows only a successful
build. View-mode persistence uses the source `.view_modes.json` format.

## Main dropdowns (2026-10-22)

The user explicitly requires the shared global dropdown style for Main as well
as the launcher. `WorkspaceChoiceButton` is a thin subclass of `ChoiceButton`;
it has no separate popup implementation. Settings, generated forms and the
terminal root share the global opening/closing morph, return-to-button logic,
arrow rendering, border and live GPU glass. Main constrains popup placement to
its owning settings dialog. The root selector retains the compact 44x24 `1`/`2`
button (70px for Linux), with the shared global caption/arrow rendering.
Hex page sizes and ELF symbol-kind/executable-section selectors use that same
component through ViewerCombo's index/value adapter. No QML ComboBox popup
implementation remains. Both viewer interaction suites pass after the change.
Settings/form popup glass now samples the containing modal's complete visual
root rather than the Main page. LiveGlass maps window sample coordinates into
that source. Nested modal backdrops use the preceding modal. Color-marker pixel
checks exercise live modal content changes in dark/id and light/en.

Settings/form choices used `value` while the launcher component expected `key`,
causing selection errors. Main's component accepts both schemas. Draft changes
notify bindings; cancelling and reopening restores the committed method. Escape
and outside press dismiss the dropdown before the parent settings dialog.
The parent stack tracks transient popups for Escape routing and shutdown.

The compact Main toolbar shares that global MorphPopup. Its segmented group
opens from and returns to the expand button, with press/hover restoration and
actions deferred until the morph ends. The panel's top aligns with the button.
Collapse, outside click, Escape, resize, Low/light and disabled-animation paths
have focused GUI coverage (`verify_qtquick_toolbar.py`); the actual workspace
also opens Settings through the expanded action group.
Inline/compact mode changes now morph a single toolbar surface on editor
open/close and SplitView resize. Native-size icons travel/fade; reversing a drag
retargets the displayed amount. The 56/100px threshold gap prevents oscillation,
and disabled animations/startup settle directly. Both real splitter directions
are covered by `verify_qtquick_toolbar_workspace.py`.

Editor/terminal headers sit flush inside the 1px frame border, without tab
gutters. Individual tabs are square; a GPU viewport mask follows the upper-left
frame curve for any tab passing through it, including while horizontally
scrolled. The editor strip also follows the upper-right frame curve when it
reaches that edge. Corner/accent pixel checks cover both kinds and scrolling.
Body content retains its original 3px padding below the 34px header.
Tree header columns clip to their viewport and share the body's contentX, so
Selinux/Permission labels cannot escape into the editor when the tree narrows.

Workspace panel reflow uses persistent native panes over SplitView target slots.
Tree 2 moves between horizontal and vertical layouts as editor/terminal panes
open/close. The 280ms OutQuint motion has a small .96/1 reveal scale, retargets
from the displayed state on reversal, and settles directly for resize/drag or
disabled animations. Scale pins to the upper-left divider corner (lower-left
for an editor above a visible terminal), with no extra x/y reveal offset; the
origin stays fixed during interruption. Tabs pin their upper-left frame corner
without translation. Editor/terminal follow each other's moving edge; clipped
cards contain delayed WebEngine frames. New tabs enter over 170ms and remaining
tabs reposition over 160ms, retaining the flush frame geometry above.
`verify_qtquick_workspace_layout.py` checks these paths, all three actual splitter
handles and a real cross-tree file move after the second tree relocates.

## Metadata and idle hints (2026-10-24)

Branch metadata writes never fall back to Source: a missing branch file is
seeded from the source file into its own branch config. Branch move/delete
removes old branch metadata keys and refreshes the view's tombstone cache.
Tests verify Source bytes, UID/GID, mode and capabilities. A cached operation
whose active branch changed is rejected before copying bytes.

Folder imports batch-fill missing descendant entries only in existing metadata
pairs. Recognized whole-filesystem transfers between ROM roots preserve their
authoritative metadata, regex rules/comments and filesystem identity files;
root merges retain skipped source members. Ordinary folder copies/duplicates
and APK mirror destinations do not invent new filesystem metadata. Malformed
metadata aborts a file replacement before either file's bytes change.
Transfer rollback also restores original payload identity, replaced destination,
metadata bytes and branch tracking if metadata fails after a physical rename.

RakScrollBar owns the shared 1000ms idle timer, with hover/press reveal. Profile,
native ScrollViews, dialogs, viewers and tree bars all use it for horizontal and
vertical hints. Monaco/markdown hint opacity also follows scroll/idle. xterm and
the minimap remain independent. `verify_qtquick_workspace_choices.py` checks
native idle hiding; `verify_qtquick_web.py` checks actual editor DOM opacity.

## Reproduce validation

Run GUI commands sequentially:

```powershell
python tools/audit_context_reference.py
python tools/audit_context_reference.py --palette light --language en
python tools/verify_qtquick_context.py
python tools/verify_qtquick_context.py --palette light --language en
python tools/verify_qtquick_drag.py
python tools/verify_qtquick_pointer_drop.py
python tools/audit_drag_reference.py
python tools/verify_qtquick_workspace_choices.py
python tools/verify_qtquick_workspace_choices.py --palette light --language en
python tools/verify_qtquick_toolbar.py
python tools/verify_qtquick_toolbar_workspace.py
python tools/verify_qtquick_workspace_layout.py
python tools/verify_qtquick_hex.py
python tools/verify_qtquick_so.py
python tools/verify_qtquick_units.py
```

Original captures: `build/qtquick_migration/context_reference/<palette>/<lang>`.
Quick captures/reports: `build/qtquick_migration/context_quick/<palette>/<lang>`.
Drag captures/report: `build/qtquick_migration/drag`.
The focused Quick GUI probes pass without QML diagnostics or QWidget instances;
134 migration unit tests pass, including actual numbered file/folder copies,
clipboard lifetime, source progress fading, APK mirror mode changes and failure
guards. Drag QA sends native Qt window drag events and verifies actual moved
file bytes. It also checks pointer selection, the multi-row drag image and
drag start/cancellation from every column. The pointer-drop verifier performs
complete gestures through the actual controls, including the single-move APK
case, file/folder self-drops without work/errors, and broken/empty folder links.
Same-volume pointer moves retain file/directory identity, including across ROMs
and into APK mirrors. Windows SendInput additionally verifies same-application folder/ROM moves
with actual file bytes (the native pipe is available as of October 24).
`audit_drag_reference.py` executes
the actual original dragMoveEvent and custom paintEvent against temporary items,
then compares painted row pixels with Quick's target map in five states.
The complete workspace verifier and production create/open/return/reopen/cancel
probe pass with clean exit0. Workspace rendering remains Direct3D11, with no
QML diagnostics or QWidget instances.

Remaining broader coverage includes actual Windows OLE mouse dragging between
applications, mounted-image drag/export across applications, deep linked-folder aliases and all
device-tree/tool pipelines. See `qtquick_workspace_audit.md` for the overall
migration's remaining work.
