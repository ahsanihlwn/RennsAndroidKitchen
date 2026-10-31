# Main / main_ext migration

Authoritative source: `C:/Users/ahsanihlwn/PROJ/rennsandroidkitchen`.
Source inventory: `build/qtquick_migration/workspace/source_inventory.json`
(49 files, 40,459 lines). The inventory is an index, not a claim that all
interactions have been migrated or verified.

Production `python app.py` now opens Qt Quick for both the launcher and Main.
`main.Main` owns `QuickWorkspace`; no QWidget workspace is constructed or used
as a fallback. The production integration verifier checks opening, returning,
reopening, and cancelling with zero QWidget instances. Historical notes below
describe earlier migration stages; they do not describe the current launch route.

October 24 follow-up: Settings/form menus sample their owning modal, and all
native/Monaco scrollbar hints hide on idle. Windows SendInput now verifies
same-application moves; the missing single-move threshold and APK-folder drop
routes are fixed. Metadata checks cover descendant import, whole-filesystem
identity, branch Source isolation, tombstone cleanup and stale-branch rejection.
Same-volume transfers now use OS rename for ordinary/self-contained overlay
items; rollback preserves payload identity and metadata on config failures.
The compact toolbar uses the global morph and aligns with its source button.
Inline/compact transitions also animate on editor open/close and divider drag,
including reversal. Editor/terminal tabs sit flush against the frame, with
rounding at the frame's viewport edge instead of on each individual tab, and
tree headers clip/synchronize with the horizontal viewport.
Drag auto-scroll has wider progressive bands; self-drops are silent no-ops and
missing/empty folder symlinks show Empty. 134 focused tests, pointer checks,
the complete workspace and production handoff pass; see qtquick_context_menu.md.

## Reference contracts already read

- `main.py:270`: outer margins 11, spacing 6, toolbar margins 8/8/8/6,
  private mini profile 46 high, action group 32, reserved loading row 20.
- `main.py:2882`: single, side by side, stacked; mirror tree when no second ROM;
  active selection in one tree clears the other, each view retains navigation.
- `main.py:1911`: opening a tab switches side by side trees to stacked, initially
  reserves 220–280 pixels on the left. Closing the right side restores its layout.
- `main_ext/class_FileTree.py:2983`: same-parent multi selection, right click
  selects without opening, left double click dispatches by column and file kind.
- `main_ext/class_FileTree.py:7215`, `:2882`,
  `FileTree_ext/RightClick.py:5122`: rename and new item edit inside the tree;
  dummy cancellation creates no file. Rename caret starts at the end.
- `class_FileTreePanel.py`: 10px corners; 1/1/1/11 inner margins; 36px title row;
  24px project icon, 13px semibold title, 10px item count; 3 columns, 28px rows.
- `class_SmartActionBar.py`: background mixes BG 45% / BG4 55%, 28px segments,
  56/100px compact hysteresis, proxy action buttons, popup fades in 160ms.
- `class_UnifiedTabContainer.py`: shared 34px tab bar, one Monaco instance,
  unsaved dot/close confirmation, close chooses neighboring tab, aux viewers
  retain their own state; browser tab bar is hidden after WebChannel readiness.
- `main.py` terminal creation/root/first-command handlers: independent sessions
  per ROM/Linux, New Session / New Session 2, random shell icon, first command
  renames its tab, hide retains PTY, close targets only that session.
- `main.py:1440`: project settings 520x540, scroll body and fixed footer,
  inverted EROFS/Brotli Default body visibility, disabled AI/plain toggles;
  changes remain a draft until Apply. Shared settings file includes Spam Enter.
- `class_PermDialog.py`: 316px card, read/write/execute grid, octal output.
- `main.py:783`: returning to projects always asks, running work and unsaved
  documents have separate lifecycle handling.

## Implemented foundations

Native QQuickWindow owner and session, directory TreeView, inline edits/new item,
native splitter/toolbar/tab controls, Monaco and PTYs, settings/permissions,
common context menu, source/NeoBranch file mutation gateway and async jobs.
No QWidget UI instances are constructed by the native preview.

Native filename search selects/reveals a tree item, with priority/global results
and cancellation. Unknown types use the original seven-card Open With picker and
per-extension persistence. Run File reuses a dedicated terminal with its file's CWD.

NeoBranch create/picker dialogs retain source dimensions (440x318 / 480x420),
colors, default selection, validation, Source/Switch/Delete/Create flows. The
editor switch guard rejects unsaved tabs and closes clean partition tabs before
changing the active layer. Creation/deletion use the original manager. Startup
restores interrupted repacks before any tree reads the filesystem.

Make FileSystem uses its original 420px dialog, two filesystem toggle buttons,
locked partition name and mount field. The dialog stays open through its job and
closes after the success acknowledgement. Worker integration in GUI QA uses an
inert fixture; real image creation has not been verified through the native UI.

Workspace tasks now use core.task_queue.TaskQueue and the original QObject
LoadingManager. The native history popup retains the 260px width, 240px body,
pending/history updates, clear action and live shader backdrop. Source restore
failures halt queued tasks. The source loading manager retains completion/fade
timings and history across clean transitions.

Running work now presents Force Close / Wait. Wait leaves the workspace open
after the job finishes. Force Close cancels the project queue, invokes the
original managed-child/worker cleanup, and retains the shared service on Return.
The native GUI probe substitutes cleanup for its own paused fixture worker;
real managed-tool cancellation still needs a full workspace operation test.

`tests/test_workspace_files.py` verifies source metadata, copy, rename, permission
fields/capabilities, source preservation under branch rename/delete, containment.
`tools/verify_qtquick_workspace.py` uses pointer/key input and real WebChannels.

## Remaining feature parity

- Broaden remaining keyboard/selection edge cases. Source hover/selection
  overlays, scrollbar idle reveal/hide, context menu geometry and edge placement,
  batch subsets, drag highlight/edge scrolling and clipboard behavior are now
  implemented and tested; see `qtquick_context_menu.md`. SVG connectors,
  thumbnails, shared columns, transfer conflicts, virtual mounts and the
  prioritized Rust path map are implemented. Broaden actual Windows pointer drag
  QA, including cross-application mounted-image exports and linked folders.
  Quick source drag now starts/cancels from all three columns, and complete
  pointer gestures move actual files within/between ROMs, into blank root space,
  and past 50 rows during auto-scroll with a recycled source delegate. Terminal
  path drops and shared global dropdowns (Settings/forms/root/Hex/ELF) also pass.
  Mounted folder
  extraction and paired APK/decompile moves have real temporary-file tests.
- Finish remaining device-tree/tool availability and pipelines. APK source build
  variants/view-mode persistence and batch context member routing are restored.
  DAT/Brotli round trips, repack variants, flashable ZIP generation, symlink
  presets and partition selection already have native implementations.
- Refine visual geometry and complete conversion/drag input QA for the native
  Image/Audio/Video/Archive viewers. Hex/ELF and Payload/Super use separate
  native viewers as recorded below. Verify Super extraction on a real temporary
  image; its GUI metadata and command-routing tests currently use fixtures.
- Complete Android Tools reference pixel comparisons, drag input and modal
  geometry QA. The native flow chart, explicit Flash All queue, commands and
  image selection are implemented and tested with inert QProcess fixtures.
- OFCL compare and automation are native and exercise their original engines;
  broaden reference pixel/geometry and production authorization coverage.
- Broaden CLI/mounted-image integration and real managed-tool cancellation.
  Core CLI commands use the native session (or a QCoreApplication headless
  session), and Rust path-map caching is integrated into all native tree views.
- Pixel/geometry and real interaction comparisons against each original state;
  production loader/return/cancel/quit, restart/repeated reopen, real PTY teardown.
- Broaden production handoff coverage as the remaining feature ports land.

## Hex and ELF viewer ports (2026-10-19)

HexViewer now has its four original tabs, entropy/hashes, five page sizes,
decimal/hex jump, forward/reverse text/hex search, current-page copy, raw string
filter/minimum-length extraction/double-click jump, export and reload. It retains
the reference 64KiB entropy sample, 20MiB hash limit and 10MiB reverse-search
limit. Copy during a pending read uses the page at the click rather than stale
bytes. The minimum width of the original Hex toolbar exceeds the native right
pane; the native toolbar can scroll horizontally so every action stays reachable.
The complete workspace's narrow-pane geometry still needs a separate audit.

SoViewer now has all nine source tabs, kind/name/mangled filters, relocations,
expanded/filterable path tree, security, executable-section selection and byte
limit, disassembly, raw strings, JSON export, compare and reload. Section/path
double click opens native QQuickWindow dialogs with source sizes. Embedded
inspection includes image preview, exact byte saving and nested ELF analysis;
path inspection includes Copy feedback and jump to Raw Strings; compare uses
the original exported-symbol comparison. The actual analyzer is reused.
Analysis, section reads, comparison and exports run in background jobs with
generation/close guards. No QWidget UI is imported by either controller.

Verification:

- `tools/audit_hex_viewer.py` and `tools/audit_so_viewer.py` capture actual
  reference widgets with deterministic temporary binary/ELF fixtures.
- `tools/verify_qtquick_hex.py` covers all four tabs and their interactions.
- `tools/verify_qtquick_so.py` covers all nine tabs, table keyboard selection,
  filters, inspection/save/nested dialogs, native file pickers, compare/reload,
  and a 758px pane. Pointer/keyboard checks pass at DPR1 and DPR1.25.
- `tools/compare_qtquick_so.py` executes the original overview/string handlers
  and compares seven stable rendered crops. Mean differences are below .5/255;
  this does not claim identical pixels for entire windows or platform controls.
- The workspace verifier opens both viewers alongside Monaco/terminal, checks
  retained tab/page state, opens a section dialog and closes the viewers cleanly.
  Latest run: Direct3D11, zero QWidgets, no QML diagnostics, exit0.
- 45 focused tests pass across both viewers and the existing workspace models.

Production Main is still QWidget. These two completed viewer flows do not
complete the outstanding workspace migration listed above.

## Payload and Super viewers (2026-10-19)

The native Payload list retains the 38px toolbar, 32px rows, original selection
counter/hint, Select All/Deselect and 52px action bar. Super keeps the two slot
lists, 26px rows and two full-width extraction actions. Ctrl preserves choices
across slots; an ordinary selection clears the other slot. Both support range,
toggle and drag selection. Group/default names and entries without slot suffixes
remain excluded from Super's lists, following the original code.

The workspace owns extraction jobs, so closing an inspection tab does not discard
task state. Multiple selected partitions share one LoadingManager batch with
independent queued workers and completion reports. Successful extraction reloads
directory metadata, including filesystem badges. Payload keeps the original
service/local fallback; Super uses original lpunpack argument ordering and does
not add the separate normalization policy to the viewer's Extract All operation.

`tools/audit_partition_viewers.py` captures both original UIs with inert data.
`tools/verify_qtquick_partitions.py` passes range/all/none, both-slot Ctrl selection,
and explicit selected/all extraction routing with zero QWidgets. Stable text
crop errors are .008 and .175/255; selection fills differ by at most one channel
level in the sampled regions. `tests/test_partition_viewer.py` extracts a real
generated payload and checks exact output bytes. The full workspace verifier
also clicks Extract and verifies the real payload output and clean teardown.
Super metadata and extraction arguments are tested with inert command fixtures;
an actual Super image/tool round trip remains outstanding. The installed tool
is present, but no fixture builder is installed. No user image was extracted.

The combined focused suite now contains 51 tests. Production Main remains
QWidget while the larger migration checklist is still outstanding.
