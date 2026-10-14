# Blur resize validation

Resize now stretches the retained blurred scene while the pointer is dragging.
Root resize cancels obsolete capture jobs and preserves source damage until
the geometry has been quiet for 120 ms. The final background is captured in
bounded GUI slices, filtered once by the existing Rust/OpenGL Gaussian, and
blended in over 120 ms. Interrupting that blend retains its visible texture.
The cached texture includes scaled padding, so growing the window cannot
expose unfilled strips at its old bounds.

Blur cards use a Qt image composition path during resize, with small cached
Skia GPU antialiased corner/border patches. This avoids allocating and reading
back a full GPU surface for every card size. Patch edges match the physical
pixel phase at fractional display scales; their centers are not redrawn as
full-size masks. Foreground text continues to be painted by Qt. Medium's frozen
texture stretches with its card, while pinned morph backdrops keep their
world coordinates. Normal settled card rendering still uses Skia GPU.
Stretched frozen textures fill their final physical pixel before the rounded
mask is applied, so a fractional image edge is not clipped twice at the handoff.

The Rust shader and its DLL were not changed for this resize fix. There is no
Acrylic or Windows material path.

## Measurements

Radeon RX 560 / OpenGL 3.3, 90 synthetic resize steps, native Qt outside the
desktop. The fixture forces source painting and grabs the blurred widget;
these are GUI work/dispatch measurements, not presented frame rates.

| Card resize at 100% | Before | After |
| --- | ---: | ---: |
| Resize + blur paint median | 12.33 ms | 7.15 ms |
| Resize + blur paint p95 | 19.31 ms | 9.77 ms |
| Full or patch GPU composites during drag | 89 full frames | 2 small patches |
| Gaussian updates during drag | 0 | 0 |

Root resize keeps one texture throughout the drag and performs one Gaussian
update afterward. The final 706x523 root has a 794x611 logical texture, including
44 logical pixels of padding on each side. The old path kept a texture from the
initial 1000x720 root when source paint was occluded.

At an actual 150% display scale, the final probe measured root resize/blur
paint median 6.76 ms / p95 9.50 ms, and card resize/blur paint median 14.08 ms /
p95 20.41 ms. Four fractional edge combinations need eight small patches.
Card dispatch p95 was 41.73 ms in this deliberately forced-paint/grab fixture,
so this report does not claim universal 60 fps. An exploratory 100% run also
had an isolated 208 ms dispatch stall; a subsequent isolated probe with step
timings had root paint max 7.77 ms and card paint max 13.43 ms. Foreground Qt
layout/paint and native scheduling remain part of the overall window cost.

## Checks

- Complete native Python suite: 205 passed, including dropdowns, morphs, Qt
  fonts, source clipping/lifetime and resize cancellation.
- Complete portable suite: 174 passed, 31 native-only checks skipped.
- Actual 150% scale targeted run: 30 passed, including blur, effect levels
  and the Qt ROM button captions.
- Corner coverage compared with normal Skia rendering at 100%, 125%, 150%,
  175% and 200%, including odd/even dimensions and fractional final edges.
- Frozen-texture edge coverage also compared across the fast/GPU handoff at
  all five scales; no uncovered quarter-pixel last row or column.
- Root resize reuses the same blur texture during drag and publishes the
  correct final size once. New exposed edges remain opaque.
- Medium card resize keeps blur, transparent outside corners, and does not
  invoke another Gaussian pass.
- Closing the scene cancels queued capture, resize and blend timers.
- Viewed the during-drag images at 100% and 150%: no empty strips, rounded
  transparent card corners and continuous background sampling.

Artifacts: `resize_before/checks.json`, `resize_after/checks.json` and
`resize_after_150/checks.json`. Each after directory includes during-drag and
settled PNGs. The older `resize_aa/` images were diagnostic intermediate frames.

```powershell
$env:RAK_TEST_SKIA_GPU = '1'
$env:QT_QPA_PLATFORM = 'windows'
python -m pytest tests -q
python tools/benchmark_blur_resize.py --artifacts docs/blur_validation/resize_after
$env:QT_SCALE_FACTOR = '1.5'
python tools/benchmark_blur_resize.py --artifacts docs/blur_validation/resize_after_150
```
