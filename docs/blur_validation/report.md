# Custom Rust Gaussian blur

The subsequent resize fix and its measurements are documented in
[`resize_report.md`](resize_report.md).

The Windows host-backdrop/Acrylic implementation was rejected and removed.
Production code has no Windows Composition backdrop, material attribute or
native blur window. Ordinary Qt popup geometry and the existing tint/border
styles are restored. The protected original checkout was not modified.

`native/rak_blur` now contains the custom Rust/OpenGL Gaussian implementation.
It shares the existing Qt/Skia GPU context. Two separable GLSL passes pair
adjacent Gaussian taps into bilinear fetches; there is no Acrylic noise, Windows
material coloring or font-rendering code. Qt renders foreground fonts and the
cached background label layers. Medium freezes once, High updates the retained
scene when source pixels change, and Low skips blur.

High is event driven, rather than a repeating screenshot timer. Panel movement
samples the cached scene without source recapture or re-filtering. Source damage
limits widget traversal to changed regions, clips to scroll viewports/ancestor
masks, and excludes foreground dialogs/login/card surfaces. Capture work is
split into approximately 4 ms GUI slices. Cached native QLabel layers are
bounded to 8 MiB/256 entries and invalidated on text/font/palette/style changes.
Source resolution remains bounded to 960 physical pixels on the longest axis.

The shader is genuinely GPU code, but the source remains Qt pixels and the
filtered result is read back for Qt's native popup compositor. This is not a
claim of capture-free rendering or universal 60 fps. Whole-window background
changes still require Qt source traversal. The stress fixture deliberately
repaints a 128-label background and the Automation dialog outside the desktop;
its dispatch intervals do not measure presented frame rate.

On the final Radeon RX 560/OpenGL 3.3 probe, the 640x460 Rust filter had median
4.20 ms / p95 5.71 ms; Qt fallback had median 7.28 ms / p95 8.32 ms. Context
initialization took 319 ms, first blur 113 ms. High's dispatch median was 15.88 ms
with p95 67.86 ms under forced full background repaints. Source capture work in
the last update was 25.55 ms spread over 87.14 ms; its filter/publication took
9.53 ms. There were 1,920 label cache hits across 128 retained labels and zero
idle shader updates. Those spikes remain a Qt-source/foreground workload limit;
the report does not hide them or claim that changing language made it free.

Validation completed:

- Full native Python suite: 199 passed, including source lifetime, global
  dropdown/morph, scroll viewport clipping and Qt-font regressions.
- Portable fallback suite: 174 passed, 25 native-only checks skipped.
- Rust kernel tests: two passed (normalized Gaussian and bilinear reconstruction).
- DPR 150% targeted run: 24 passed, including native GPU shader/ROM/effect levels.
- Real AppUI: 20 page changes with profile refresh, zero exceptions, zero
  stylesheet warnings, no blank pages or surviving transition overlays.
- Nuitka lists only the new `native/rak_blur.dll` for the blur package config.
- Compiles and `git diff --check` passed.
- Viewed `rust/automation_high.png`: original glass tint, Qt log font and rounded
  transparent outside corners. This Qt image contains the actual Rust-filtered
  backdrop; there is no hidden OS material needed to produce it.

Current artifacts are in `rust/` and `rust_navigation/`. `native/` contains the
older Skia implementation's reference measurements, not the current shader.

Reproduce:

```powershell
python tools/build_gpu_blur.py
$env:RAK_TEST_SKIA_GPU = '1'
python -m pytest tests -q
python tools/benchmark_gpu_blur.py --artifacts docs/blur_validation/rust
python tools/stress_app_navigation.py --cycles 10 --profile-refresh --exposed-probe --artifacts docs/blur_validation/rust_navigation
```
