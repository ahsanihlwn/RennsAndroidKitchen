# QtTerm: native terminal for Qt Quick, Rust core

Replacement for xterm.js (+ fit + web-links) with **no Chromium / QtWebEngine**.

```
 Qt Quick (QML / Python)     C++ (thin)                          Rust (termcore)
┌──────────────┐   ┌──────────────────────────────┐   ┌──────────────────────────────────┐
│ Terminal {}  │──▶│ TerminalItem : QQuickPainted │──▶│ portable-pty  (PTY / ConPTY)     │
│ scrollbar    │   │  paints grid snapshot        │   │ alacritty_terminal (VT, grid,    │
│ theme/props  │   │  Qt events → term_key/mouse  │   │   scrollback, reflow, selection) │
└──────────────┘   └──────────────────────────────┘   │ key encoder (legacy + kitty)     │
                        C ABI (include/termcore.h)     │ mouse encoder, URL/OSC8 links    │
                                                       └──────────────────────────────────┘
```

## Build
Qt ≥ 6.4 (Quick), CMake ≥ 3.21, **Rust ≥ 1.88** (dependency requirement).
```
cmake -S . -B build -DCARGO=/path/to/cargo      # cargo auto-detected if recent
cmake --build build -j
./build/qtterm                    # your shell
./build/qtterm adb logcat         # any program + args
```
Outputs: `build/qtterm` (demo app) and `build/qml/QtTerm/` (QML plugin).
Tests: `cd core && cargo test --release` (12, real PTY) · `ctest --test-dir build` (9 Qt input tests; run with `QT_QPA_PLATFORM=offscreen QT_QUICK_BACKEND=software`).

## Use from Python (PySide6 / PyQt6)
```python
engine = QQmlApplicationEngine()
engine.addImportPath("/path/to/build/qml")
engine.loadData(b'import QtTerm 1.0\nTerminal { program: "adb"; arguments: ["logcat"] }')
```
`python/qtterm_demo.py` shows Python ↔ terminal: `sendText`, `screenText()`, signals `exited(code)`, `bell()`.
Verified with **PyQt6 on Qt 6.4.2** (PySide6 uses the same QML plugin mechanism; not run here).

**ABI rule (important):** the plugin is a QQuickItem, so it must be built against the *same Qt X.Y* your Python binding runs on
(`python -c "from PySide6 import QtCore; print(QtCore.qVersion())"`). pip wheels ship Qt without headers, so build the plugin once per
Qt version against a matching Qt SDK (`cmake -DCMAKE_PREFIX_PATH=<Qt X.Y prefix>`), or ship your own Qt instead of the wheel's.

## Heavy output (adb logcat)
Measured on 1 vCPU with *software* rendering (GPU machines should do better):
* core: 100 MB of coloured, wrapping logcat-style lines in 2.6 s (~50 MB/s parse; real logcat is a few MB/s)
* UI under a 300 MB flood: snapshots capped at ~120/s (≥8 ms apart), paint ≈1.2 ms/frame, input and timers stay responsive
* scrollback 10 000 lines by default (`scrollback:` property)

## QML API
```qml
import QtTerm 1.0
Terminal {
    anchors.fill: parent; focus: true
    program: "opencode"; arguments: []; workingDirectory: "/path"    // empty program = $SHELL
    fontFamily: "JetBrains Mono"; fontPixelSize: 15; padding: 8
    foreground: "#cdd6f4"; background: "#1e1e2e"; cursorColor: "#f5e0dc"; selectionColor: "#585b7099"
    palette: [ /* 16 colors */ ]
    onBell: ...; onExited: (code) => ...
}
```
Properties: `title running exitCode columns rows historySize scrollOffset mouseTracking hasSelection`.
Methods: `start() stop() sendText() copy() paste() selectAll() scrollToBottom() screenText()`.

## xterm.js feature map
| xterm.js | here |
|---|---|
| parser / buffer / reflow / alt screen | alacritty_terminal |
| FitAddon | `fit()` in `terminal_item.cpp` + `term_resize` → SIGWINCH |
| WebLinksAddon | same regex in Rust (wrapped-line aware) + OSC 8; hover underline, click opens http(s) |
| DOM renderer | QPainter: bg runs, ASCII text runs, wide/combining per cell, 5 underline styles, strike, dim, box/block glyphs |
| selection | word / line / Alt+column / Shift+click / auto-scroll, X11 primary, middle-click paste |
| mouse modes (X10/UTF8/SGR) | yes (1016 SGR-pixels: no) |
| bracketed paste, focus reporting, alt-screen wheel→arrows | yes (paste strips embedded `ESC[201~`) |
| OSC 0/2, 4/10/11/12 (set+query), 8, 52 copy; DA/DSR/DECRQM/XTWINOPS replies | yes |
| IME | preedit drawn at cursor (not verified with a real IME) |
| new vs your xterm.js | kitty keyboard protocol (Shift+Enter…), sync output (2026), modern Unicode widths |

## Known limits
* QPainter (CPU) renderer; fine for logcat-class floods per the numbers above. A glyph-atlas `QSGNode` renderer is the upgrade path for 4K/HiDPI.
* No ligatures, sixel, kitty graphics. Colour emoji depend on font/backend. Double-line box chars and powerline glyphs come from the font.
* Kitty keyboard: disambiguate, report-all, event types, associated text done; "alternate keys", keypad and modifier-only keys not.
* Verified on Linux / Qt 6.4 / offscreen+software only. Windows (ConPTY) and macOS (Ctrl/Cmd swap) are written, untested. No real GPU/X11/Wayland/IME run.
* OSC 8 links spanning several rows highlight only the first row; Alt+click cursor move, context menu, search not implemented.

## Dev helpers
`QTTERM_SCRIPT="wait:20000,shot:/tmp/a.png,ctrl+p,text:hello,enter,quit" ./build/qtterm <prog>` drives the app headlessly.
`QTTERM_FONT` picks the font. `core/examples/{dump,bench}.rs` inspect the emulator state / measure throughput.
