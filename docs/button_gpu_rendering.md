# Qt fonts with Skia GPU widget surfaces

Solid button, panel, card and input backgrounds/borders use antialiased Skia
Ganesh vectors through one GUI-thread OpenGL context. Qt owns fonts, shaping,
icons, layout, focus, caret, selection, accessibility and input. There is no
Skia text renderer. Qt also presents the resulting premultiplied QImages.

`core/skia_button.py` resolves the solid uniform rounded QSS rules used by the
app. It retains scope precedence, specificity, properties, object names,
descendant/direct-child selectors and supported states. Its stylesheet cache
uses stable C++ widget identities and invalidates on style, property, font,
palette, ancestry and object-name changes. Fractional border widths remain
fractional, including the Single ROM selector's 1.5 px border.

## Integration

- Idle QPushButtons paint cached vector frames directly without a passive
  graphics effect. The existing 0.96 press animation installs a temporary
  effect; release/recovery removes it. Foreign effects retain priority.
- Qt paints button text/icons with `CE_PushButtonLabel`. Active effects also
  capture visible child widgets: the Settings dropdown captions and arrow
  QLabels remain visible. Stable native leaf labels are cached during a press.
  At idle Qt paints these children normally, once.
- Settings Language, Project Mode and Main Compression Method use one
  `app_ext/class_ChoiceDropdown.py` button/style/layout and popup launcher.
  All three open the existing `ChoiceMorphPopup`, with identical animation,
  keyboard input, caption capture and source restoration. The popup belongs
  to the anchor's window, so Main's modal Settings dialog supplies its bounds
  and backdrop. Closing that dialog cancels any pending selection callback.
- `core/skia_widgets.py` supplies cached vector surfaces for native QWidget,
  QFrame, QLabel, line edits, combo/spin boxes, determinate progress bars,
  sliders and scrollbars. Qt still paints native text, caret, selection, icons
  and editing content. Slider/scrollbar subcontrol rectangles come from Qt.
  Progress labels are rasterized by Qt and included in the GPU image.
- A property-scoped QSS override suppresses only native background/border
  colors. It preserves widths, padding and font declarations. Declaration-only
  stylesheets are wrapped in `* { ... }` before appending the override; otherwise
  Qt rejects the combined sheet and silently loses fonts such as the original
  22 px My Projects heading. The resolver sees the original sheet.
- WebToggle tracks, knobs and shadows are GPU vectors. Stable repaint/scroll
  reuses the frame; position, press scale, palette, disabled state or DPR changes
  refresh it. Left presses retain the existing single-toggle signal behavior.
- BlurCard/LiveBlurCard backgrounds use rounded GPU clips, backdrop textures,
  tint and borders. Preparing the blurred source remains the existing Qt path.
- Create-project morph and footer transitions use supported vector surfaces
  and fixed-size Qt content. Choice popup opacity uses GPU composition while
  retaining Qt option layout, keyboard navigation, focus, masks and input.
  Fully opaque effects draw their source directly, avoiding an extra capture,
  upload and readback. Opacity masks retain native Qt semantics.
- App-to-Main entry prepares the real Main while the loader is visible, then
  uses `WindowContentReveal` to animate native window opacity. Windows
  composites the existing Skia/Qt backing store; there is no fade-time Skia
  readback, recapture, child opacity effect or per-frame tree repaint. Fonts
  remain live Qt contents. Icons pause for the handoff and resume with their
  queued tasks intact. WebEngine keeps its existing graphics backend. Other
  page reveals and their excluded interactive widgets keep their behavior.
- App page navigation combines band reveal, profile morph and footer motion in
  one cached opaque canvas. Qt supplies native caption pixels; small Skia
  corner/edge assets resize surfaces without new per-frame GPU readbacks.
  My Projects uses an opaque scrolling viewport and per-card hover snapshots,
  with one clock for visible path labels. See [navigation validation](navigation_validation/report.md).

Native item-view cells/viewports, WebEngine content and unsupported custom
painters retain Qt rendering. Gradients, images, nonuniform borders, menu
indicators, slider ticks and indeterminate progress use native fallback. This
does not replace the whole application with a Skia scene graph.

## Load and fallback

Widget surfaces, Qt labels, selector rules and GPU textures/render targets are
cached. Unstyled widgets remember that they have no vector surface; they do not
schedule a new zero-delay preparation timer on every paint. Failed surfaces
restore their original QSS and stop retrying. Theme changes refresh the original
rules and native contents. No QSS is changed during a paint callback.

Ordinary widget GPU results use synchronous `readPixels` on changed frames. Cached frames avoid
readback; opaque composition avoids a redundant pass. Cached widget frames use
Qt's native ARGB32 premultiplied format for presentation. The managed texture
cache is limited to 32 MiB/128 entries and render targets to 24 MiB/16 entries.
GPU resources are released with their context current.

Unsupported drivers/platforms, missing Skia, context/allocation failures and
offscreen/minimal Qt platforms retain Qt fallback. Set `RAK_DISABLE_SKIA_GPU=1`
before launching to force Qt. This hybrid path has CPU presentation overhead;
it does not claim zero-copy delivery or faster performance than native Qt.
See [measured load and visual verification](gpu_validation/report.md).
See also [dialog corner repair and FileTree profiling](filetree_validation/report.md)
for translucent-window ownership, bounded native cell caching and pixel scrolling.
The [Main entry measurements](entry_validation/report.md) compare the previous
snapshot path with native composition in the actual Main hierarchy, including
preparation cost and animation update spacing.

## Verification

```powershell
python -m unittest discover -s tests -v

$env:RAK_TEST_SKIA_GPU = '1'
$env:QT_QPA_PLATFORM = 'windows'
python -m unittest discover -s tests -v
```

The suite covers input/click/check behavior, interrupted animations, ownership,
Qt glyph pixels, dropdown child captions, fractional DPI/corner coverage,
inline-font preservation, editor selection/keyboard input, cache invalidation,
subcontrol geometry, opacity masks and context/failure cleanup. Theme fixtures
stay in memory and do not save preferences.

```powershell
$env:QT_QPA_PLATFORM = 'windows:fontengine=freetype'
python tools/benchmark_ui_gpu.py --profile --exercise --artifacts docs/gpu_validation/skia
python tools/benchmark_ui_gpu.py --profile --exercise --disable-gpu --artifacts docs/gpu_validation/qt
```

The benchmark runs hidden native windows in the working repo, without startup
listeners/polling or selecting controls. It records whole-window snapshot paint,
scroll/press work, timer spacing, popup dispatch, CPU time, frames/uploads,
screenshots and original stylesheet/font/layout inventories. The timer-driven
cases do not enable cProfile. Snapshot verification/PNG output is excluded from
popup dispatch measurements. These are paint/dispatch measurements, not screen
presentation FPS. Runs should be sequential without other profiling workloads.

`core.skia_gpu.gpu_backend_status()` reports the renderer/device, frame/upload
counts, managed cache bytes and last error without initializing a context.

## Read-only original UI reference

`C:\Users\ahsanihlwn\PROJ\rennsandroidkitchen` was only read as the original UI
reference. No code was imported/executed there and no file was modified there.
The working `app.py` adds widget renderer installation and the Main handoff.
`core/fonts.py` and
`main_ext/class_ThemeUi.py` also match the reference byte-for-byte. Font/layout
inventories of the working Qt reference and Skia paths match on all three main
pages; GPU border antialiasing is intentionally different.
The working theme binding also skips unchanged original QSS when a Skia
override is installed, avoiding repeated repolish without changing font rules.
