# Main entry: native window composition

Validated on Windows, Python 3.14, PySide6 6.10.1, skia-python 144.0.post2
and Radeon RX 560. The original backup was read-only throughout.

The previous frozen Skia reveal still rendered each changed fade band into an
offscreen GPU target, synchronously read its pixels back to the GUI thread,
converted the QImage and submitted it to Qt. Capturing Main once stopped tree
repaints, but did not remove that synchronization/presentation bottleneck.
Earlier small stand-in measurements understated stalls in the actual Main
hierarchy with WebEngine.

Main entry now uses `WindowContentReveal`. It maps Main at zero window opacity
while the loader remains visible, lets the first Qt paint and deferred style
work settle, dismisses the loader, then animates native window opacity.
The nominal reveal duration remains 620 ms. Windows composites Main's existing
backing store; the animation does not grab Main, compose Skia fade bands,
read GPU pixels or use a child opacity effect on each frame. Skia widget
surfaces and Qt font rendering remain in place.

Deferred icons pause during preparation and both fades, then resume with the
same tasks. Prescan deferral is preserved. Cancellation cannot reopen Main,
and completion restores opacity to 1. Disabling animations skips the Main fade.
Native composition follows resize/DPI changes without scaling a frozen font
snapshot. Other App page reveals retain their behavior.

The actual Main probe also exposed access violations while PySide dynamically
registered callbacks during signal connection on this Python build. Connected
Main methods now declare Qt `Slot` signatures, including the optional automation
callback inherited from its private feature mixin. No private bundle source or
callback behavior was changed. Main construction and WebEngine cleanup now
execute successfully.

## Measurements

The comparison constructs the repository's real Main: toolbar, FileTreePanel,
splitters, three trees, UnifiedTabContainer and Monaco. The visible tree holds
12,000 synthetic rows and deferred icons. Monaco prepares before entry and
displays a read-only virtual file afterward. The native window is exposed
without activation. No grabs run during animation. Both subprocesses exit 0.

Three sequential runs per implementation, first run cold:

| Metric | Previous Skia snapshot bands | Native window fade |
| --- | ---: | ---: |
| Cold median update gap | 168.4 ms | 16.0 ms |
| Per-run p95 update gaps | 264.4–267.2 ms | 17.1–18.0 ms |
| Largest update gap | 267.2 ms | 21.3 ms |
| Update ticks per reveal | 6–10 | 39 |
| GPU readbacks during reveal | 3–6 | 0 |
| Visible tree paints during reveal | 0 | 0 |
| Cold process CPU time during reveal | 500 ms | 78 ms |
| Pending icons retained | 12,000 | 12,000 |

Artifacts: [previous Main path](readback_main/timings.json),
[current Main path](compositor_main/timings.json),
[current tree pixels](compositor_main/tree.png),
[Monaco after entry](compositor_main/webeditor.png).

Update gaps measure GUI-thread animation/property changes, **not screen FPS or
DWM presentation**. Disk scans, ConfigEngine, account/authorization and private
service startup are bypassed. The profile provider supplies an empty widget
instead of reading account state. Secondary trees remain empty.

Cold preparation still cost 641 ms before either fade; warm preparation took
48–50 ms. A separate preparation profile attributed about 305 ms to widget/QSS
preparation and 132 ms to initial Skia rendering. First-show, glyphs and tree
painting add work. This removes repeated work from the animation; it does not
eliminate startup latency or all project-loading stalls. Experiments moving
style preparation across timers regressed entry and were not retained.

Earlier stand-in artifacts in `before`, `after`, `native_before` and
`native_after` describe previous implementations.

## Compatibility and verification

WebEngine keeps its platform backend. Main entry adds no QOpenGLWidget and
does not force Qt Quick to OpenGL. Qt documents
[graphics API compatibility](https://doc.qt.io/qt-6.10/qquickwidget.html) and
[WebEngine backend behavior](https://doc.qt.io/qt-6.10/qtwebengine-features.html).

Entry tests exercise production AppUI/Main handoff methods, first paint,
loader dismissal, no-loader entry, cancellation/deletion, restored opacity,
disabled animations, deferred icons and absence of fade-time tree paints/GPU
readbacks. Existing glyph, dropdown, dialog and fractional-DPI tests remain.
The native probe verifies Monaco after entry and normal shutdown.

- Native suite: 116 tests passed in 21.5 seconds.
- Portable suite: 116 tests completed, with 16 hardware checks skipped.
- Native entry tests at 150% DPI: all 22 passed.
- `core/fonts.py` and `main_ext/class_ThemeUi.py` hashes still match the
  protected original backup.

Reproduce the comparison sequentially:

```powershell
python tools/benchmark_entry_reveal.py --snapshot --pause-icons --actual-main --hidden-entry --exposed-probe --verify-webengine --runs 3 --artifacts docs/entry_validation/readback_main
python tools/benchmark_entry_reveal.py --window-opacity --pause-icons --actual-main --hidden-entry --exposed-probe --verify-webengine --runs 3 --artifacts docs/entry_validation/compositor_main
```

Use `--profile-prepare` in a separate invocation/output directory. Profiling
runs are excluded from timing comparisons.
