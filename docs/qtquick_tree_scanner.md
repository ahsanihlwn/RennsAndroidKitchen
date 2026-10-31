# Qt Quick tree: read directories on demand

Production Main no longer starts a recursive Rust PathIndex when a project
opens. After interrupted source transactions are restored, the initial directory
views load only their root entries. Unopened descendants are not enumerated.
Expanding a tree node or entering a folder submits that single directory to the
existing asynchronous job queue. Watcher refreshes re-enumerate opened folders.

The per-directory Rust enumerator is retained. It applies the existing small-file
placeholder links, image/container signatures, NeoBranch overlays and deletions,
APK mirrors, metadata, sorting and hidden-folder rules only to the requested
folder. Thumbnail decoding has its own bounded queue. Obsolete navigation results
cannot replace the current folder. No background path-map priority queue or
recursive indexing worker is connected to WorkspaceSession or DirectoryModel.

The native PathIndex API remains available for its isolated native tests; it is
not instantiated by production project opening or by the UI tree benchmark.

## Qt Quick rendering

Quick consumes a C++ `QStandardItemModel` (QtGui, without QWidget instances).
The Python directory controller retains the original entry/operation rules;
Qt performs flattened tree traversal in C++. Per-entry QObject properties
update thumbnails and branch lines without rebuilding the hierarchy.

Directory construction, including native item allocation, and reconciliation
yield after 80 rows or an 8 ms GUI budget. The first prepared batch is shown
immediately; later rows are coalesced into native range insertions. Refresh
keeps surviving model indexes and in-progress inline drafts. A cancelled
navigation cannot apply old rows to the new location. Unattached prepared rows
are retired when their scan is superseded. Pooled delegates drop references
before their presentation object is destroyed.

File icons load directly from the existing SVG assets through Qt; decoded
thumbnails use a separate synchronous in-memory provider, avoiding Python
provider requests from Qt's asynchronous image-loading threads. The production
Quick launch inherits the app's antialiased font without installing the legacy
global QWidget polish event filter.

An earlier fixture with recursive indexing enabled recorded a .641s first batch and 3.258s complete
population. Its p95 GUI timer gap was 11.45ms, with a 126.69ms maximum: scene
update spikes remain, so this is not a claim of entirely stutter-free rendering.
The production renderer/loop was retained after diagnostic comparisons. Native
range insertions coalesce 1024 prepared rows; construction still yields at the
8ms/80-row boundary. The current benchmark runs without a recursive map.

Tree arrows use the reference `arrow_right.svg`, at 12 logical pixels and 80%
opacity; opening rotates the arrow by 90 degrees. A single click in the 20px
branch slot toggles expansion. Ancestor and sibling connector segments follow
the reference's continuation rules, with the palette's 18% neutral line color.

## Build and validation

`python tools/build_native.py --dev` builds an isolated, fingerprinted Rust
runtime. The next source launch selects it through `build/native/runtime/current.json`.
A running process can retain its previous Windows DLL. Release builds use
`build.py`'s native runtime selection. The extension is version 0.3.3, API 3.

Validation commands:

```text
python -m unittest tests.test_path_index tests.test_quick_directory
python tools/benchmark_qtquick_tree.py
python tools/verify_qtquick_workspace.py
python tools/verify_qtquick_application.py
```

Native API tests independently cover the retained optional PathIndex. Production
directory tests verify that unopened descendants are not scanned, navigation
loads only its requested folder, and missing/empty folder links remain empty.
The tree benchmark creates 2,500 mixed files plus a folder link and unopened
folders. It records batch sizes, UI
timer gaps, opening latency, and cancellation in
`build/qtquick_migration/tree/report.json`. Add `--profile` only for diagnosis;
profiling changes the timings substantially.
