# Launcher startup and repeated navigation

Validated on Windows, Python 3.14, PySide6 6.10.1 and the native Skia OpenGL backend on Radeon RX 560.

## Changes

- Startup builds the existing UI in small GUI batches, including project rows. `startup_built` waits for both UI construction and all rows; editor/account startup ordering remains intact.
- Initial Windows disk enumeration uses the existing background disk worker. UI rebuilds reuse its last inventory.
- Native Qt controls/fonts and Skia surfaces warm behind the splash before the clock starts. The splash fade, page bands and drive counters share one opaque cached scene; live project rows do not repaint through a translucent full-window effect.
- Drive counter timing is shared with the existing usage animation. Cached Skia track/chunk assets resize during the reveal, and Qt draws the counter text. The real bars remain at their final values, avoiding synchronous `QProgressBar.setValue()` repaints on every frame.
- The bundled Poppins font is selected directly. ROM mode markers use cached dot icons rather than U+2B24, which Poppins does not contain. The hidden Create Project page previously triggered an expensive system font fallback search during layout measurement. Both the launcher form and Create Project dialog use the same marker helper; labels, translations and mode selection remain native Qt.
- Profile morph captions copy original QSS and append complete selector rules. They no longer append bare declarations to a renderer-modified stylesheet. Footer caption replicas also normalize declaration-only styles.
- Page changes cancel the previous scene before theme refresh. Page scenes have a separate completion deadline and invalidate the live page on cleanup, so interruption cannot leave their opaque cover behind.

Fonts remain Qt-rendered. Skia still supplies widget surfaces and cached surface assets. The cached transition is presented by Qt; there is no new persistent GL compositor or per-frame Skia framebuffer readback.

## Measurements

The tools use the real AppUI with synthetic projects. Service/network calls, project writes and filesystem comparisons are excluded. Timers measure GUI dispatch intervals, not monitor presentation FPS. Screenshots are captured after timing.

| 128-project fixture | Before | After |
| --- | ---: | ---: |
| Project construction dispatch p95 | 323.82 ms | 51.59 ms |
| Reveal dispatch p95, including preparation | 98.39 ms | 27.09 ms |
| Reveal GPU readbacks, including preparation | 118 | 36 |
| After: animation-only dispatch p95 | — | 16.90 ms |
| After: animation-only maximum | — | 17.74 ms |
| After: animation-only GPU readbacks | — | 0 |

`before/checks.json` records the original synchronous fixture and fixed observation interval. `after/checks.json` records asynchronous construction and waits for the finished signal. Construction and total elapsed times therefore include different observation tails; do not interpret them as application launch-time comparisons. The p95 and paint/readback counts identify the GUI stalls, while the new animation-only interval separately verifies the fade.

The separate splash-first 8-project fixture (`cold_start/checks.json`) exercises the real startup build entry. Animation p95 was 16.84 ms, maximum 17.49 ms, with zero readbacks during the clock. Preparation took about 370 ms spread over GUI turns, with a remaining one-off maximum around 102 ms. Initial Qt construction and preparation still have costs; this does not claim that all cold startup work fits in 16 ms. Removing the unsupported ROM marker eliminated the roughly 0.9-second fallback stall observed in the cold profiling fixture.

## Validation

- Full native suite: **175 passed** in 62.635 s.
- Portable Qt fallback suite: **175 completed**, 16 hardware tests skipped, in 42.807 s.
- Native startup tests at DPI 150%: **8 passed** in 9.591 s.
- Real launcher navigation stress: **120 page changes**, including immediate changes, interrupted/completed animations and profile badge refreshes. No Python errors, stylesheet warnings, visible leftover overlays or disabled page updates. See `navigation_stress_refresh/checks.json` and screenshots.
- Regression checks cover exact native Qt caption pixels at handoff, counter font pixels, suppression before the global GPU paint filter, zero live-caption paints/readbacks during the cached animation, build signal ordering, resize/hide/DPR cancellation, ROM marker translation/state, valid replica styles and the completion deadline.

## Reproduce

```powershell
python tools/benchmark_startup_reveal.py --rows 128 --artifacts docs/startup_validation/after
python tools/benchmark_startup_reveal.py --cold-start --rows 8 --artifacts docs/startup_validation/cold_start
python tools/stress_app_navigation.py --cycles 60 --profile-refresh --exposed-probe --artifacts docs/startup_validation/navigation_stress_refresh
$env:RAK_TEST_SKIA_GPU='1'
python -m unittest discover -s tests -v
```

The profile QSS fix is in the working repository's optional `rennsproject` package (ignored by git). The separate backup repository was not used as an editable source.
