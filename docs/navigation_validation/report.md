# Project scrolling and Settings/profile navigation

Measured in the real AppUI at 1080×700 on Windows, Python 3.14, PySide6 6.10.1,
Skia 144 and Radeon RX 560. The fixture adds 128 synthetic projects in memory;
no project, account or preference data is saved. Native fonts and the real
private profile transition are used. The original backup remains read-only.

## Changes

- Removed the graphics effect on the complete project viewport. Hover/press
  scales use a small opaque canvas and cached native row pixels. Scrolling
  hides this canvas and lets Qt scroll the opaque viewport backing store.
  Hover geometry, click targets, open/menu callbacks and selection remain.
- Replaced one marquee timer per project path with one visible-label clock.
  Clipped labels do not advance or measure glyphs. Text width is cached.
- Remembered empty native widget surfaces across page Hide/Show. Actual local
  style/state changes invalidate them; unrelated replica styles and PySide's
  internal pointer properties no longer reparse hundreds of captions.
- Theme refresh compares the original user stylesheet with its factory, so
  the transparent Skia override is not removed and reapplied on an unchanged
  palette. Hidden Settings styles/glyphs are prepared in short idle batches.
- Combined page bands, profile identity and footer motion in one opaque
  cached page canvas. The original transition models retain their geometry,
  duration and caption opacity curves. Qt captures each font at its native
  size once; avatar bitmaps may resize. Labels are not scaled.
- Rounded profile/footer surfaces use cached Skia corner/edge assets. Qt
  stretches only their straight regions and presents the cached layers.
  Animation frames do not render new Skia surfaces or read back GPU pixels.
  The asset cache is bounded to 128 entries/2 MiB. This remains a Qt Widgets
  presentation path, rather than a full Skia scene-graph replacement.
- Navigation no longer restarts the disk gauges from zero on every page
  return. Startup's explicit disk usage reveal is retained.

An experiment adding a persistent native OpenGL compositor regressed scrolling
and hung during AMD/Qt shutdown. It was removed. Qt's global graphics backend
and Main's WebEngine path are unchanged.

## Measurements

These measurements precede the subsequent removal of project hover animation
and the profile-model cancellation correction. The current canvas runs only
on press/release; see [UI corrections](../openwith_validation/report.md).

One sequential run per version without cProfile or animation grabs. A separate
profile identified whole-viewport sourcePixmap work, offscreen path timers,
stylesheet preparation and synchronous GPU readback. Separate visual captures
at 100/220/360 ms checked the moving avatar, both caption sizes and restoration.

| GUI dispatch metric, 128 cards | Before | After |
| --- | ---: | ---: |
| Scroll median / p95 | 17.8 / 52.7 ms | 16.1 / 25.9 ms |
| Hovered scroll median / p95 | 29.6 / 44.1 ms | 16.0 / 22.9 ms |
| Settings median / p95 | 16.3 / 30.8 ms | 16.0 / 16.9 ms |
| Return median / p95 | 74.0 / 598.3 ms | 16.0 / 20.6 ms |
| Largest Settings dispatch gap | 379 ms | 122 ms |
| Largest Return dispatch gap | 598 ms | 340 ms |
| Settings process CPU time | 1172 ms | 344 ms |
| Return process CPU time | 1469 ms | 594 ms |
| GPU readbacks in whole Settings case | 56 | 4 |
| GPU readbacks in whole Return case | 50 | 4 |

Artifacts: [before](before/timings.json), [after](after/timings.json).
Whole-case counts include preparation and the first live paint. Unit tests
isolate the animation itself and verify zero GPU readbacks and no underlying
caption paints after preparation.

**These are event-loop gaps, not screen FPS.** Returning still shows/hides many
native child widgets and captures the destination once: the remaining 340 ms
maximum is preparation, not a claim that every click/frame is below 16 ms.
Cold newly visible project buttons also account for 14 readbacks in the first
scroll; the second hovered scroll reuses those frames. Scanning real project
files, authorization/network activity and other system load are excluded.

Reproduce:

```powershell
python tools/benchmark_project_navigation.py --exposed-probe --artifacts docs/navigation_validation/after
```

Use `--profile` or `--capture-animation` in separate output directories; those
runs add measurement work and must not be compared as animation timings.

## Verification

- Native suite: all 129 tests passed, including shared dropdown selection,
  cancellation, preference persistence and page/profile cleanup.
- Portable suite: 129 tests completed, with 16 hardware checks skipped.
- Native navigation tests at 150% DPI: all 8 passed.
- Animation checks verify retained Qt caption pixels, zero GPU readbacks
  during prepared frames, and restoration after resize/cancellation.
- Separate native visual captures checked both profile captions during the
  morph and the restored mini card. The protected backup was not modified.

## Chromium comparison

Chromium retains painted content as layers/tiles, manages raster/decode caches,
and updates transform, clip and opacity properties independently. Its renderer
can process scrolling/animation on a compositor thread while Blink is busy.
It also schedules raster/decode work around the visible tiles. Skia alone does
not provide that whole pipeline. See Chromium's primary documentation:
[How cc Works](https://chromium.googlesource.com/chromium/src/+/refs/heads/main/docs/how_cc_works.md).

The changes here apply retained assets, visible work and fewer presentation
passes to the existing Qt app. They do not add Chromium's compositor thread or
promise equivalent behavior under every main-thread workload.
