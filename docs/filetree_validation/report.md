# Dialog corners and FileTree paint load

The transparent dialog window was receiving an opaque inherited Main
background through the generic Skia widget renderer. The rounded child box
could not erase that rectangle. Translucent top-level windows now retain native
Qt ownership; `_Dlg` explicitly keeps its root background transparent. The
rounded box and BlurCard still use Skia surfaces, with Qt text and input.

The repro's four corner alpha values changed from **255/255/255/255 to
0/0/0/0**. Tests also check resize/repaint. Dialog sizing now uses polished Qt
fonts and invalidates both layouts before calculating wrapped text height;
the full return-to-project message fits at its original 320 px width.

[Fixed dialog](after/dialog.png) · [Original broken render](before/dialog.png)

## FileTree changes

- Cache native Qt cell images for filenames, size labels, badges, branch marks,
  symlink indicators and context text. The cache is limited to 8 MiB and 256
  entries. Qt still draws all fonts. Opaque RGB targets, native logical DPI and
  pixel phase preserve the direct Qt glyph pixels. Nonstandard scaled render
  targets retain direct Qt painting.
- Invalidate changed cells on model data signals; clear on removal/reset/layout,
  font, palette and style changes. Large scroll jumps reuse existing images but
  avoid allocating tiles for unseen cells until the scroll settles.
- Hover requests repaint only the old/new full-width rows. The observed damage
  is 934 × 28 px per row instead of a 934 × 639 px viewport.
- The viewport is opaque and explicitly clears damaged/empty regions, so
  scrolling can reuse backing pixels without exposing stale text or repainting
  the editor underneath.
- Use native pixel scroll mode and a 120 ms OutCubic wheel animation. Repeated
  wheel input accumulates its target. Keyboard, mouse press, scrollbar dragging,
  range changes and shutdown stop pending animation. Trackpad pixel input,
  horizontal/modified wheel input and disabled animations retain Qt handling.
- Ignore PySide's internal `_PySideInvalidatePtr` bookkeeping when invalidating
  the QSS resolver.

## Local measurement

Windows native Qt FreeType, Radeon RX 560/OpenGL 3.3, 960 × 700 window, DPR 1.
The fixture contains 4,000 synthetic populated files with cached metadata;
no real project is opened. Measurements use 60 samples of whole-window
`grab()` plus event dispatch. Profiled numbers include cProfile overhead and
are **not screen FPS**. The 16 ms timer case does not enable cProfile.

| Case | Before median / p95 (ms) | After median / p95 (ms) |
| --- | --- | --- |
| Repaint unchanged tree | 39.46 / 47.01 | 18.95 / 30.90 |
| Large jump, 19 rows per sample | 66.74 / 89.13 | 66.08 / 86.17 |
| Small movement, 7 px per sample | Not measured | 25.31 / 40.38 |
| Timer-driven small movement, unprofiled | Not measured | 16.54 / 26.49 |

Unprofiled small-scroll timer spacing: median 18.36 ms, p95 29.10 ms. Cached
tree repaint is substantially cheaper; large jumps still incur native Qt font
and row painting costs. This does not claim that every stall is gone. Real UI
hover/scroll usually damages part of the viewport, while this benchmark forces
a whole-window snapshot on each sample. Filesystem scanning, configuration
engines and WebEngine rendering are outside this fixture.

Final cell cache: 4,953,312 bytes / 108 entries; 8,924 hits / 122 image misses
in this run. Backend remained `skia-gpu` with no frame error. Font/icon/badge
comparisons against uncached native Qt yielded identical pixels, including a
native DPR 1.5 probe. No backup file or user preference was written.

Raw data: [before](before/timings.json), [after](after/timings.json).

```powershell
$env:QT_QPA_PLATFORM = 'windows:fontengine=freetype'
python tools/benchmark_filetree_gpu.py --artifacts docs/filetree_validation/after
```

The regression suite covers cell data/style changes, selection, RTL, column
width, scroll position, memory bounds, removed/reset cells, empty viewport
clearing, hover damage, wheel/keyboard/trackpad behavior and dialog transparency.
Native Windows: 94 tests pass. Portable: 94 tests complete, 13 hardware tests
skipped, remaining tests pass. `py_compile` and targeted `git diff --check` pass.
