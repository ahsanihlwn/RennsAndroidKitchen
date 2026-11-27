# Quill Native 0.3 — Floem editor-core

A desktop code editor with a **Rust engine, Python/PySide6 host, and Qt Quick UI**. It does not use Chromium, Electron, Qt WebEngine, Monaco JavaScript, or a localhost server. QML continues to use Qt's expression runtime; this is not a browser.

This version replaces the prototype custom document engine with **`floem-editor-core = 0.2.0` (MIT)** and **`lapce-xi-rope = 0.3.2` (Apache-2.0)**. Text buffers, edit/delta transactions, undo/redo, cursor/selection structures, grapheme/word movement, and indent/outdent actions use the upstream core. Floem UI, winit, wgpu, browser, and Chromium crates are not pulled in as dependencies.

PySide/Qt Quick remains the UI layer. The adapter in `native/src/editor_floem.rs` handles Qt coordinates, I/O, history compaction, and the Python bridge. The 19-language lexical colorer remains the lightweight prototype lexer; **this integration does not add Tree-sitter, LSP, or all Lapce/Monaco UI features**.

The upstream core is consumed via pinned Cargo dependencies without forking or patching upstream. Dependency versions and licenses are recorded in [DEPENDENCY_LICENSES.md](DEPENDENCY_LICENSES.md); full core license texts are included in `THIRD_PARTY_LICENSES/` and the Python package.

## Running on Windows

Prerequisites: 64-bit Python 3.10+, stable Rust via [rustup](https://rustup.rs/), and MSVC C++ build tools with the Windows SDK to compile the extension. Development and testing of this package were performed on Linux with Python 3.12.14; no Windows `.exe` or wheel has been tested yet.

Extract the ZIP, enter the `quill-native` folder, and run in PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m quill_native samples\hello.py
```

The first build requires internet access to fetch Rust/Python dependencies. Once installed, the editor runs offline and makes no network requests. To open an empty document:

```powershell
.\.venv\Scripts\python.exe -m quill_native
```

`run.ps1` provides a shortcut to create the environment and install the project on first run:

```powershell
.\run.ps1 samples\hello.rs
```

## Running on Linux/macOS

Requires Python 3.10+, stable Rust, a platform compiler/linker, and Qt desktop support. Linux also requires the system libraries for the active Qt platform plugin.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m quill_native samples/hello.rs
```

Or `sh run.sh samples/hello.py`. macOS has not been tested. The launcher script installs the package if missing or if its version is not yet `0.3.0`. After modifying source files, reinstall with `python -m pip install --force-reinstall --no-deps .` using that environment's interpreter.

## Wired features

- Native Qt Quick UI: code viewport, line numbers, active line highlight, position/size/EOL status, scrollbars, find, and language selector.
- Open/save UTF-8 files, preserve original BOM and line endings, save via temporary file in the same directory followed by atomic replace.
- Save/Discard/Cancel confirmation for unsaved documents on New/Open/Close.
- Cursor and selection via mouse/keyboard, double-click word selection, select all, copy/cut/paste, word movement, Home/End/Page Up/Page Down.
- Undo/redo with adjacent-typing coalescing, preserved cursor positions, and dirty tracking that returns clean when undoing back to the saved state.
- Basic auto-indent on Enter following detected indentation. Tab inserts an indent unit; with a selection, Tab/Shift+Tab runs block indent/outdent via Floem's built-in actions. The status bar shows the indent unit.
- Stateful lexical coloring for 19 languages plus Plain Text, detected by extension or selected manually.
- Basic IME preedit/commit, UTF-16 coordinates for Qt, and per-grapheme navigation/deletion for emoji and combining characters.
- Font zoom with Ctrl+wheel or Ctrl++ / Ctrl+-.

Languages: Rust, Python, JavaScript, TypeScript, C, C++, C#, Java, Go, JSON, HTML, XML, CSS, Shell, SQL, TOML, YAML, Markdown, and QML. Highlighting covers keywords, strings, comments, numbers, simple type/function identifiers, operators, and tags per language profile. Multiline comments, nested Rust comments, Python triple-quoted strings, Rust raw strings, and JS templates maintain cross-line state. This profile is not a full Monaco/TextMate/tree-sitter grammar: JS regex literals, JSX, f-string/template interpolation, complex macros, and embedded languages are not fully parsed.

## Why storage and rendering stay lightweight

| Layer | Implementation |
| --- | --- |
| Document | `floem_editor_core::buffer::Buffer` backed by UTF-8 Xi Rope; one active engine in Rust |
| Edits | `Buffer::edit` produces deltas and line invalidations. Cursors/selections move via upstream deltas |
| History | Upstream `Buffer::do_undo` / `do_redo`. The host caps the undo window at 128 groups. Since the core release lacks a public GC API, the adapter periodically rebuilds the Buffer by replaying retained groups via the core API |
| Python/Qt | Receives only viewport text and spans; never stores a `QTextDocument` or a full-file string copy |
| Rendering | Viewport-sized `QQuickPaintedItem` using `QTextLayout` for shaping, rather than a full-document canvas |
| Layout cache | Up to 192 lines, displaying at most 8,192 characters per line |
| Coloring | Per-line Rust lexer; caches only line-start state rather than tokens for the whole document. Edits invalidate suffix state |
| Color propagation | Incremental work, at most 1,500 lines or ~128 KiB of text per step, resumed on a timer. Distant lines may briefly appear uncolored |
| Search | Streaming KMP over the rope without allocating a single full-file string |
| Qt dependency | Uses `PySide6-Essentials`; does not install Qt WebEngine |

`QQuickPaintedItem` uses `QPainter` to draw the viewport onto a Qt Quick surface. This is not yet a dedicated full-GPU glyph renderer. Text layout is handled by Qt so shaping and font fallback are not reimplemented in Python. `QSG_RENDER_LOOP=basic` is selected so Python paint callbacks do not contend over the GIL with a separate GUI render thread. Platform GPU renderers remain usable; the software renderer is forced only in headless tests.

History uses a replay metadata cache and Xi Rope snapshots with structural sharing. Reconstruction runs amortized around every 256 groups, 1,024 core operations, or ~32 MiB of added edit cost since the last compaction, targeting a 16 MiB soft budget. Large groups or redo stacks can exceed that target. This is not a hard RAM cap, and history reconstruction can still cause a pause on large edits. No full UTF-8 document copy lives in Python.

Core offsets are UTF-8 byte offsets. Qt UTF-16 column conversion uses Xi Rope metrics; positions inside a surrogate pair snap before the emoji. Calling `char_count()` explicitly counts scalars across the whole document and is not used on render/edit hot paths.

Source ZIP size does not match application disk/RAM requirements. Qt, Python, and compiled extension packages are installed separately.

## Actual measurements performed

Tested on Linux x86-64, Python 3.12.14, Qt/PySide6 6.10.3, Rust release build. Headless `offscreen/software` GUI backend, one process per case; these are not Windows/GPU numbers and not a comparison against Chromium.

| Case | File size | Lines | Process RSS after ~800 ms | Child processes |
| --- | --- | --- | --- | --- |
| Small Python | 67 bytes | 4 | 86.98 MiB | 0 |
| Large Rust | 10.68 MiB | 160,001 | 98.47 MiB | 0 |

On the large file, the backend streamed the file open in 33.382 ms in one run. Across 1,000 single-character inserts at scattered positions, median was 0.0186 ms and p95 was 0.0491 ms. **These insert timings cover only the backend plus PyO3/history calls, excluding paint, layout, coloring, or end-to-end input latency.** First frame across two runs took ~168–286 ms. These are measurements of this build, not proof of speedup over the previous prototype. Results depend on OS caches, hardware, backend, and drivers; RSS also includes shared mappings.

See [BENCHMARK.json](BENCHMARK.json) for raw results and [benchmark.py](benchmark.py) to repeat them. Install `psutil` so the probe reports RSS:

```sh
python -m pip install psutil
python benchmark.py --headless --output BENCHMARK.json
```

Omit `--headless` to measure with your own desktop backend. Local measurements are the ones most relevant to your environment.

## Testing

Already executed:

- 11 Rust unit tests for Unicode/CRLF, surrogate offsets, cross-chunk UTF-8 decoding, core history, compaction, cursor/dirty tracking, and the lexer.
- 20 Python/Qt tests: 2,500 random edits plus 5,000 edit/undo/redo/branching operations compared against an independent reference, keyboard/mouse via `QQuickWindow`, clipboard, synthetic IME, AltGr, unsaved guards, load/save failures, long-line mode, block indent/outdent, and bounded caches. Includes testing history past the 128-group limit and compaction.
- Clippy across all targets with `-D warnings`.
- Built the release wheel, installed it, and ran tests/probes from the installed package. Verified QML and native extensions are bundled in the wheel.
- Rendered and visually inspected the UI in a sandbox.

```sh
python -m pip install -r requirements-dev.txt
cargo test --locked --manifest-path native/Cargo.toml
cargo clippy --locked --manifest-path native/Cargo.toml --all-targets -- -D warnings
python -m pytest -q
```

UI tests use `offscreen/software` by default. IME tests use synthetic Qt events and have not verified all real Windows/macOS IME behavior. For editable development installs:

```sh
maturin develop --release --locked
```

## Using in your PySide application

`NativeEditor` is a Qt Quick type that can be registered into an existing application's QML:

```python
from PySide6.QtQml import qmlRegisterType
from quill_native.editor import NativeEditor

qmlRegisterType(NativeEditor, "Quill.Native", 1, 0, "NativeEditor")
```

```qml
import QtQuick
import Quill.Native 1.0

NativeEditor {
    anchors.fill: parent
    focus: true
}
```

Register the type before calling `engine.load()`. When integrating into your own app, set `QSG_RENDER_LOOP=basic` before creating `QGuiApplication`. The host can call `loadPath(path)`, `saveCurrent()`, `setLanguage(name)`, `undo()`, `redo()`, and `findNext(text, backwards)`. Signals `openRequested`, `saveAsRequested`, `confirmRequested`, `errorOccurred`, and `closeApproved` can be wired to host dialogs/UI; see `Main.qml` for a full example. For user-driven file switches, use `request()`/`openUrl()` so unsaved guards run; `loadPath()` is a direct host/startup operation.

The core can also be used without Qt:

```python
from quill_native._engine import Document

doc = Document("Hello 🦀\n", "example.rs")
assert doc.backend() == "floem-editor-core/0.2.0"
doc.set_cursor(0, 5)  # zero-based line and UTF-16 column
doc.insert("Rust ")
rows, color_ready = doc.viewport(0, 30)
doc.undo()
```

## Limitations of this version

- Single active document; no multi-tab/workspace, minimap, folding, autocomplete, LSP, debugger, or Monaco extension ecosystem.
- No word wrap, regex search/replace, UI multi-cursor, or full accessibility parity with built-in Qt text controls. Vertical cursor movement tracks UTF-16 columns rather than preserving visual horizontal position across tabbed or bidirectional lines.
- Long lines display only the first 8,192 characters to keep layout fast. Lines over 32 KiB are not syntax-highlighted. Full contents are preserved on disk/in buffer, but visual navigation/hit-testing in the truncated tail is incomplete. This is not yet suited for editing huge single-line minified files.
- All I/O and UI operations still run on the GUI thread. Opening/saving/searching very large files can stall the UI; color propagation is incremental. The history cap is a soft budget, not a total RAM guarantee.
- Accepts valid UTF-8 files with LF/CRLF, including mixed LF/CRLF. Files containing bare CR are explicitly rejected so upstream `Buffer::new` does not silently alter line endings; original files remain untouched. The `Document(text)` constructor follows the core's built-in bare-CR normalization. No other encodings or binary file decoding are supported yet.
- No Windows/macOS test runs or executables yet. The source can be built on those platforms, but results should be verified on the target machine.

## Technical references

- [Floem editor core 0.2.0](https://docs.rs/floem-editor-core/0.2.0/floem_editor_core/)
- [Lapce Xi Rope 0.3.2](https://docs.rs/lapce-xi-rope/0.3.2/lapce_xi_rope/)
- [PyO3](https://pyo3.rs/) and [maturin](https://www.maturin.rs/)
- [PySide6 QQuickPaintedItem](https://doc.qt.io/qtforpython-6/PySide6/QtQuick/QQuickPaintedItem.html)
- [PySide6 QTextLayout](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QTextLayout.html)

Project source license: MIT. Rust/Qt/Python dependencies retain their respective licenses. An SPDX Rust report and permissive license texts are included; dependency sources are fetched by Cargo rather than bundled in this ZIP. Qt usage remains subject to the Qt/PySide license terms chosen by the user.
