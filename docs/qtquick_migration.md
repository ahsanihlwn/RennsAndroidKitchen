`python app.py` now opens both the launcher and Main through Qt Quick. `main.Main`
owns `QuickWorkspace`; it no longer constructs a QWidget workspace. There is no
production QWidget fallback or `--widgets` launch branch. Qt WebEngine Quick is
initialized before QApplication, retaining the platform Direct3D11 renderer.
The production verifier passes create/open/return/reopen/cancel and records zero
QWidget instances throughout those flows. Remaining feature parity work is tracked
in `qtquick_workspace_audit.md`; the production switch does not claim that every
legacy feature has been ported.

Production startup paints the lightweight Launcher window and its StartupSplash
after font setup, before account/backend setup and deferred LauncherContent
creation. The same window and splash stay alive while content loads. Dialogs
register after incubation, early notifications wait for their presentation, and
services start after content and the first frame are ready. Main's readiness gate
depends on source recovery and initial trees rather than hidden Monaco readiness;
Monaco prepares after the entry transition or an earlier file-open request.
Cold QML/window construction can still take several seconds. The recursive Rust
path map at project open has been removed at the user's request. Initial views
load their roots; descendants load when opened. The native per-directory
enumerator remains in use.

The authoritative reference is the separate original tree at
`../rennsandroidkitchen`, supplied by the user. Its appearance and animation
classes are compared directly; the old QWidget launcher in this checkout is
not assumed to be the same reference. The audit of this checkout
found 83 Python sources importing QtWidgets and 123 directly declared QtWidgets
subclasses, including the private bundle available in this checkout. Captures
use synthetic project/file rows and cached local appearance/account information.
Generated reference files stay under the ignored `build/qtquick_migration/` tree.

| Area | Current state |
| --- | --- |
| Appearance reference | Projects (including local volumes), create form, actual anchored create popup in single/dual ROM mode, settings at both scroll positions, theme picker, and the real workspace shell captured with geometry/font/style metadata. |
| Shared controls | Native QML buttons, text inputs, labels, cards, ROM choices, dropdowns, switches, scrollbar, pin marker, footer, and theme previews. |
| Launcher presentation | QML project list, create form, settings, and theme picker render in QGuiApplication without hidden QWidget instances. Native input smoke check passes. |
| Editor documents | Shared QObject backend extracted to `core/editor_document.py`. QWidget frontend retains import compatibility and displays warning signals. BOM/EOL, virtual read-only documents, and queued loading are tested. |
| Monaco presentation | HTML extracted to `core/editor_html.py` with the same CSS and scripts; blank-line whitespace cleaned. The original QWidget editor and isolated Qt WebEngine Quick pane both load the real Monaco assets. |
| Terminal presentation | Qt WebEngine Quick pane uses the original xterm assets and PTY bridge. Channel readiness is independent of requestAnimationFrame so hidden panes can accept startup output. Real PTY echo/resize handshake verified. |
| Graphics lifetime | `ui.runtime.WindowLifetime` releases scene graph resources before unloading web sessions. Direct3D11 integration completed with exit code 0. Forced OpenGL crashed the AMD driver during validation and is not selected by the runtime. |
| Production integration | `ui.application.LauncherApplication` connects actual project metadata, native folder selection, settings persistence, account/session verification services, Trash operations and workspace handoff. Startup creates no hidden QWidget launcher. New project metadata is written atomically; folder names and duplicate destinations are validated. |
| Workspace | Native Qt Quick tree with C++ item storage and on-demand native directory enumeration, actions, splitters, tabs, editor/terminal, permissions, Hex/ELF/Payload/Super/Image/Audio/Video/Archive viewers, Android Tools, flashable builder, symlinks, conflicts, virtual mounts, CLI, OFCL comparison and automation. Remaining interaction/context/visual checks are tracked in the workspace audit. |
| Dialogs and motion | Create/project menus, loading, license, credits, packages, theme picker, profile/avatar, login, confirmation and error-log presentation use QML. The existing authentication/package services run behind QObject models. Startup, bands, shared profile/badge/extras, footer, press, choice/card morphs, project/disk scenes, path scrolling and appearance crossfades retain source timings. Following the latest user instruction, glass now uses a live GPU texture and shader blur. |
| Packaging | Build configuration includes the `ui` package, all QML and JavaScript motion assets and Qt QML plugins. Asset mappings are verified; a compiled release has not been built in this stage. |

An empty installation opens the original full-page create form. Once a project
exists, the footer create action opens the anchored popup. Returning from the
workspace selects the project list.

Project-list motion uses a retained old/new scene instead of ListView's generic
add/remove/displaced transitions. Exits last 240 ms (InOutSine); surviving cards
start moving at 168 ms with a 15.5 ms stagger and 310 ms OutCubic movement.
Returning cards reveal over 180 ms OutQuint, beginning halfway through movement.
Only moving survivors intersecting either viewport position count toward the
stagger. Scroll clamping includes old offscreen positions. Rapid drive changes
wait for the current scene and apply the latest filter; typed search cancels it.
Deletion shows the original 1300 ms indicator for at least 550 ms and keeps input
blocked until exit/reflow completes.

Disk removal uses 380 ms InOutSine, with survivor movement starting at 266 ms and
55 ms stagger. Disk insertion fades for 310 ms. Header/sidebar switches move the
real controls along the original cubic path for 440 ms with 22 ms stagger and
resize the real project list throughout the motion. Trash retains the layout.
The usage counter retains the 840 ms extended OutCubic tail and 42 ms stagger.

Choice popups reset press feedback immediately, measure the unscaled anchor,
and use fixed geometry under the window content item for their entire 240 ms
opening/180 ms closing morph. The content is clipped at its final size. This
avoids the release-transform drift and end-of-opening layout jump. Arrow icons
use the existing `arrow_right` asset. Card hover and disk/button presses use the
original scales, curves and hold/release timings.

Regenerate the original reference and view the QML forms:

```powershell
python tools/audit_ui.py --source-root ../rennsandroidkitchen --disks --artifacts build/qtquick_migration/original
python tools/audit_ui.py --source-root ../rennsandroidkitchen --exposed --palette dark_green --language id --artifacts build/qtquick_migration/original_exposed
python tools/audit_ui.py --disks --artifacts build/qtquick_migration/baseline_disks
python tools/preview_qtquick.py --fixture build/qtquick_migration/baseline/fixture.json
```

Capture and measure visual differences:

```powershell
python tools/preview_qtquick.py --fixture build/qtquick_migration/baseline/fixture.json --capture build/qtquick_migration/quick
python tools/compare_qtquick.py
```

The comparison produces `build/qtquick_migration/quick/visual_difference.json`.
It reports global and footer pixel differences, not a declaration of visual
parity. Large uniform backgrounds can conceal meaningful local differences.
The current 1080×700 dark-green/Indonesian captures have mean channel errors of
0.35–0.70 on a 0–255 scale. Local text/icon/border differences remain. Small
window layouts, other themes/languages, and interaction/motion states still
need further comparison with corresponding original captures. The default entry
was switched at the user's request to try `app.py` first; visual parity is not
claimed for all states. Main-window parity is not yet established. Launcher text geometry is compared
against the original at 550?400, 1080?700 and 1440?900, including dark/light and
English/Indonesian. Matched text centers differ by at most 0.5 px. High borders
remain deliberately softer following the user?s requested correction.

Validation commands:

```powershell
python tools/verify_qtquick.py
python tools/verify_qtquick_reference_motion.py --original ../rennsandroidkitchen
python tools/verify_qtquick_motion.py
python tools/verify_qtquick_application.py
python tools/verify_qtquick_geometry.py
python tools/verify_qtquick_windows.py
python tools/verify_qtquick_accounts.py
python tools/verify_qtquick_appearance.py
python tools/verify_qtquick_glass.py
python tools/verify_qtquick_popup.py
python tools/verify_qtquick_spinner.py
python tools/verify_qtquick_startup.py
python tools/verify_qtquick_choice_reference.py
python tools/verify_qtquick_choice_lifecycle.py
python tools/verify_qtquick_choice_return.py
python tools/verify_qtquick_choice_selection.py
python tools/verify_qtquick_choice_settings.py
python tools/verify_qtquick_portraits.py
python tools/verify_qtquick_create_backdrop.py
python tools/verify_qtquick_corners.py
python tools/verify_qtquick_chrome.py
python tools/verify_qtquick_web.py --real-pty
python -m unittest discover -s tests -p test_editor_document.py -v
python -m unittest discover -s tests -p test_qtquick_presentation.py -v
python -m unittest discover -s tests -p test_qtquick_sessions.py -v
python -m unittest discover -s tests -p test_quick_project_store.py -v
python -m unittest discover -s tests -p test_quick_rows.py -v
```

The launcher smoke check covers native mouse and keyboard input, navigation,
single/dual ROM validation, command payloads, filtering, switch/dropdown input, modal presentation, and
550×400/1080×700/1440×900 resizing. Resizing checks rendering and stability; it
does not establish appearance parity at those sizes. The web check uses real
Monaco/xterm assets, verifies their WebChannel messages and a temporary PTY echo,
then tears down the shared GPU images before closing Chromium. It creates no
QWidget views. The existing QWidget navigation/regression suites and real
Monaco entry probe also pass after the document/backend extraction.

The application integration probe uses temporary settings and project folders,
creates a dual-ROM project through the first-run create page and a second
project through the actual QML popup, waits for the real Main
workspace and Monaco to prepare/reveal, returns to Quick, and verifies pin/menu/
search, the deletion indicator/exit/reflow, a temporary Trash backend, restore,
and clean shutdown. CLI shim generation/autostart are disabled in this
probe. It does not modify the user's project list or appearance settings.

The reference-motion probe imports the actual ProjectCardsTransition and
DiskCardsTransition classes from the separate original tree. It compares scene
order, source/target positions, delays and total duration against the QML planner
for drive hide/return/replacement, deletion, scrollbar clamp, and disk plug/unplug.
The native motion probe additionally renders intermediate frames and exercises
real pointer input, dropdown anchor stability, sidebar/list travel, Trash fading,
rapid navigation, and animation disable/cancellation. Generated captures stay
under `build/qtquick_migration/motion/`.

Live glass is calibrated against the original `make_blur_pixmap` function
(QGraphicsBlurEffect QualityHint, radius 18). A high-contrast stripe test gives a
mean channel difference of 1.07/255, and verifies rounded masking, automatic
updates when the source changes, and Low bypass/re-enable. Blur uses
`ShaderEffectSource.live` and `MultiEffect` in the same scene, without screen
captures or CPU image readbacks. Dialogs now use in-scene modal Popups to share
that GPU source. Their usual dimensions, source content layout and fades remain;
small windows scroll the original content rather than clipping the footer.
Theme/language crossfades separately retain their one-time old-frame capture.

The create popup comparison now uses the original production show path with its
real backdrop and exposed screen pixels. An isolated `QWidget.grab()` painted
translucent layers differently and was an unsuitable color reference. The card
overlay is restored to 10/255 white and the title to FG_BRIGHT/700. An earlier
darkened-card baseline matched within 1/255; the user's subsequent backdrop
correction moves that darkening outside the card (see below). Single and
dual popup labels match source centers within 0.5 px. TextField content is
vertically centered, placeholder bounds are explicit, and the Poppins baseline
offset is calibrated: placeholder and typed glyph rows exactly match the source.
ROM dots use the original font glyphs. Spinners now request Shape.CurveRenderer,
retaining the original size, stroke, round caps, arc and period with shader
anti-aliasing; 16/28/38 px rasters are checked at three angles against QPainter.

High also uses live glass bands over project/settings headers and footers. The
underlying scrolling surface is a separate scene item, avoiding texture feedback.
Projects retain their original viewport and scroll coordinates, using display
margins to draw cards behind High chrome. Settings retains its content geometry.
Low/Medium keep clipped scrolling and flat chrome. Pointer events in the bands
cannot activate cards behind them. High chrome checks mutate real underlying
cards and verify that both band textures update automatically.

FirstFramePresenter keeps native launcher/loading windows transparent until their
first composed frame swaps. Startup services begin afterward. Project construction
also waits for the loading frame, matching the original show/processEvents barrier
without a nested event loop. The splash glint uses curve anti-aliasing too.

Choice opening is compared with the original ChoiceMorphPopup._set_progress
at seven points in both directions. Its fixed full-button caption, 4px content
clip, body offset, alpha threshold, radius and even rounding are preserved. All
option press feedback resets before closing; late input cannot change the choice.
The restored caption has mean channel difference 0.003/255 in the GPU probe.
Closing now uses the shared ButtonCardMorph spring (440 ms). The button surface
shrinks with the spring; the chosen caption travels from its option to the new
button rectangle and scales with the morph. The destination caption/width is
prepared while hidden, including asynchronous language commits. The original
ButtonCardMorph._advance is compared at 40 selected/cancelled/up/down samples.
Real GPU frames verify movement, scaling, short/long widths and final handoff.
An incoming opaque destination overlays the settled morph without fading its
underlay twice; this removes a measured 24% text contrast dip at the handoff.
Normal and small-window Settings are exercised through all nine languages.
The lifecycle probe also executes the original popup itself in an isolated
process. Dismissing presses are consumed, including throughout exit, so page
buttons underneath receive neither clicks nor press animation. Hit testing follows
the animated card. The source button's hover appearance stays frozen for its GPU
caption, then its real hover state resumes before the selection callback. The
hovered source/return image difference is 0.022/255; the earlier geometry-only
probe did not cover these focus/input transitions.

Portraits use smooth area downsampling, mipmaps and an antialiased GPU mask.
Credit's 1024px raster previously went through QPainter bilinear minification,
which aliased the small photo. The portrait probe checks 188 partially covered
edge pixels, a circle alpha error below 1/255, and credit interior error below
0.14/255 against a smooth Qt reference. Profile/footer/settings/page motion share
the same mask; animated portraits keep fixed texture resolution. Account photos
use their original file URL rather than a pre-cut 100px PNG.

Create Project's black layer is now a separate full-window modal backdrop
(150/255 black, 180 ms fade), matching the source overlay's placement. Its glass
samples the undimmed page with backdropDim=0; the black texture is no longer
inserted into the card. The backdrop probe checks outside darkening and complete
restoration on close. This supersedes the earlier card-only darkening calibration.

Rounded surfaces share SmoothRectangle (antialiasing only when radius>0).
The previous LiveGlass mask applied a hard alpha threshold, producing zero
partially covered edge pixels and visibly cut corners. Its GPU mask now preserves
smooth alpha at 2x/DPR resolution. The corner probe compares rectangle, glass
and button silhouettes at radii4/6/8/10/12/14 with Qt antialiased paths, passing
at 100%,125% and200% scale; mean alpha error stays below1/255. Flat boundaries
retain pixel alignment, and geometry/radius values remain unchanged.

A shutdown access violation after returning from Monaco was fixed by releasing
native modal graphics and deleting the QML engine while the GUI event loop is
still running. The production integration now exits with code 0 after the actual
Main preparation/reveal, create/delete/restore and native confirmation sequence.

Production startup now selects Qt Quick Controls Basic before loading QML;
Windows' native style rejected Profile's custom scrollbar and Error Log's
custom text-area background. The already-composed launcher reveals independently
of optional editor/service prewarming. Configuration process startup also runs
in that worker. An early project open waits asynchronously behind its live
loading window, and cancelling invalidates the queued editor construction.
First-import protection remains enabled until preparation finishes; fully
initialized cached imports bypass its lock so GUI image/property callbacks do
not wait for unrelated editor imports. Appearance, settings, profile and job
state now have separate notifications; cached color/translation maps avoid
rebuilding all hidden pages for a loading/status change.

Footer action collection now retains controls on an outgoing hidden page,
matching the reference's isHidden rather than effective visibility. Rightmost
actions pair through the 280 ms transition; unmatched buttons fade as a whole.
Press transforms do not alter the layout endpoints, and hiding/disabling a
button cancels its press feedback completely.

Effects/mode dropdowns now use the same text-based sizing as Language. Their
vertical layout is aligned to whole logical pixels, as in QWidget layouts:
the previous half-pixel placement caused a visible glyph shift between the
cached return frame and the restored native-rendered caption. The production
Settings probe checks all three dropdowns in all nine languages at 1080x700 and
550x400: 18 language selections plus 90 effects/mode selections pass, with
maximum final-frame mean green-channel difference below 0.06/255.

Pressed/hover-scaled captions use CurveRendering while transformed and retain
NativeRendering at rest. This covers regular, primary, ROM and choice buttons,
plus project, drive and Trash captions. Font, natural text dimensions and button
geometry stay fixed through the renderer change. The press probe exercises 28
control/palette combinations at 100%,125%,200% scale and compares the pressed
glyphs against QPainter outlines; curve text has lower error than the previous
scaled bitmap at every tested DPR.

LauncherModel.panelBorder supplies the same outline to Create, choice/project
menus, account login and auxiliary dialogs: palette-neutral RGB at 10% opacity
for Low/Medium and 5% for High. It updates on effect/palette changes without
invalidating cached appearance data for ordinary jobs. Hover tooltips use the
current theme's foreground, modal surface and flat border. The border probe
checks all 13 palettes across all 3 levels, with six panel bindings per case.
