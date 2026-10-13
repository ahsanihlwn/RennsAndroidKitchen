# Compare picker and terminal dropdown

The terminal root selector uses the shared `ChoiceDropdown` and
`ChoiceMorphPopup`, including global palette bindings, press feedback and the
same closing spring as Settings. Its compact 1/2/Linux caption is resolved from
the root ID separately from the menu's folder names. Duplicate folder names
still animate from the clicked row. Switching sessions, WSL source selection,
cancellation and failed session startup retain their existing behavior.

The Compare Filesystem picker now labels source tabs with the actual folder
names (for example `bedah` and `target`). Full paths remain available as
tooltips, and long names elide within the dialog's fixed width.

Partition hover and selection repaint cached Skia surface patches without
changing stylesheet rules. Text and icons remain painted by Qt. Both columns
use the existing smooth-scroll behavior, including direct touchpad pixel
deltas. Each column scans once on construction; old cards are deleted when
changing source, and folder icons are cached per column. The selection slot is
registered up front, avoiding the PySide access violation reproduced while
connecting dynamically during construction.

The actual picker was measured with 128 in-memory partitions per column and a
frozen glass backdrop. Empty temporary source directories were used; no
filesystem comparison was run and no project data was changed. Measurements
are GUI dispatch intervals, not presentation FPS. The new scroll area also
uses the app's shared 40-pixel mouse-wheel step.

| Measurement | Before | After |
| --- | ---: | ---: |
| Median dispatch interval | 21.1 ms | 16.0 ms |
| p95 dispatch interval | 41.9 ms | 17.4 ms |
| Maximum dispatch interval | 50.6 ms | 20.4 ms |
| Stylesheet change events | 496 | 0 |
| GPU readback frames | 58 | 36 |
| CPU time during 1.5 s probe | 1875 ms | 1219 ms |

The baseline includes the native slot registration needed to run the probe
without a PySide crash; its rendering and scrolling paths were still unchanged.
Raw measurements and native screenshots are in `before/` and `after/`.

```powershell
python tools/benchmark_compare_picker.py --rows 128 --artifacts docs/compare_picker_validation/after
```

Native tests cover folder names, long names, clipping, pixel/mouse scrolling,
selection paths, deleted old cards, and stable footer pixels. The terminal
tests cover shared morph pixels, palette changes, duplicate names, session
rollback and Linux source selection. All 19 targeted checks passed at 150% DPI.
The complete portable suite ran 165 tests with 16 hardware checks skipped.
The complete native suite passed all 165 tests in 50.1 seconds.
