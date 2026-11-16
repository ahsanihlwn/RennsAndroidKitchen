# Quill Native 0.3 — Floem editor-core

Editor kode desktop dengan **engine Rust, host Python/PySide6, dan UI Qt Quick**. Tidak menggunakan Chromium, Electron, Qt WebEngine, Monaco JavaScript, atau server localhost. QML tetap memakai runtime ekspresi milik Qt; ini bukan browser.

Versi ini mengganti engine dokumen buatan prototipe dengan **`floem-editor-core = 0.2.0` (MIT)** dan **`lapce-xi-rope = 0.3.2` (Apache-2.0)**. Buffer, transaksi edit/delta, undo/redo, struktur cursor/selection, gerakan grapheme/kata, dan action indent/outdent menggunakan core upstream. Floem UI, winit, wgpu, browser, dan Chromium tidak ditarik sebagai dependency aplikasi ini.

PySide/Qt Quick tetap menjadi UI. Adapter di `native/src/editor_floem.rs` menangani koordinat Qt, I/O, pemangkasan history, dan jembatan Python. Lexer coloring 19 bahasa tetap lexer ringan prototipe; **integrasi ini tidak menambahkan Tree-sitter, LSP atau semua fitur UI Lapce/Monaco**.

Source core dipakai melalui dependency Cargo yang dipin, tanpa fork atau patch upstream. Lisensi dan versi dependency dicatat di [DEPENDENCY_LICENSES.md](DEPENDENCY_LICENSES.md); teks lisensi core disertakan di `THIRD_PARTY_LICENSES/` dan package Python.

## Jalankan di Windows

Prasyarat: Python 3.10+ 64-bit, Rust stable melalui [rustup](https://rustup.rs/), dan MSVC C++ build tools dengan Windows SDK untuk mengompilasi extension. Pengembangan dan pengujian paket ini dilakukan di Linux dengan Python 3.12.14; belum ada `.exe` atau wheel Windows yang telah diuji.

Ekstrak ZIP, masuk folder `quill-native`, lalu jalankan di PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -m quill_native samples\hello.py
```

Build pertama membutuhkan internet untuk mengambil dependency Rust/Python. Setelah terpasang, editor dapat berjalan offline dan tidak melakukan akses jaringan. Untuk membuka dokumen kosong:

```powershell
.\.venv\Scripts\python.exe -m quill_native
```

`run.ps1` menyediakan cara singkat untuk membuat environment dan menginstal project saat pertama digunakan:

```powershell
.\run.ps1 samples\hello.rs
```

## Jalankan di Linux/macOS

Butuh Python 3.10+, Rust stable, compiler/linker platform, dan dukungan desktop Qt. Linux juga memerlukan library sistem untuk plugin platform Qt yang dipakai.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python -m quill_native samples/hello.rs
```

Atau `sh run.sh samples/hello.py`. macOS belum diuji. Script launcher menginstal jika package belum ada atau versinya belum 0.3.0. Setelah mengubah source, instal ulang dengan `python -m pip install --force-reinstall --no-deps .` menggunakan interpreter environment tersebut.

## Fitur yang sudah terhubung

- UI Qt Quick native: area kode, nomor baris, baris aktif, status posisi/ukuran/EOL, scrollbar, pencarian, dan pemilih bahasa.
- Buka/simpan file UTF-8, pertahankan BOM dan line ending asli, simpan melalui temporary file di direktori yang sama lalu atomic replace.
- Konfirmasi Save/Discard/Cancel untuk dokumen yang belum disimpan ketika New/Open/Close.
- Cursor, selection dengan mouse/keyboard, double-click memilih kata, select all, copy/cut/paste, gerakan kata, Home/End/Page Up/Page Down.
- Undo/redo dengan penggabungan ketikan berdekatan, posisi cursor tersimpan, serta dirty tracking yang kembali bersih saat undo ke state tersimpan.
- Auto-indent dasar saat Enter mengikuti indent yang terdeteksi. Tab memasukkan unit indent; jika ada selection, Tab/Shift+Tab menjalankan indent/outdent blok lewat action bawaan Floem. Status bar menampilkan unit indent.
- Coloring leksikal stateful untuk 19 pilihan bahasa, ditambah Plain Text. Deteksi dari ekstensi dan pilihan manual.
- Input method preedit/commit dasar, koordinat UTF-16 untuk Qt, navigasi/delete per grapheme untuk emoji dan combining characters.
- Zoom font dengan Ctrl+wheel atau Ctrl++ / Ctrl+-.

Bahasa: Rust, Python, JavaScript, TypeScript, C, C++, C#, Java, Go, JSON, HTML, XML, CSS, Shell, SQL, TOML, YAML, Markdown, dan QML. Highlight mencakup keyword, string, comment, number, type/function identifier sederhana, operator, dan tag sesuai profil bahasa. Multiline comment, nested Rust comment, Python triple string, Rust raw string, dan JS template memiliki state antarbaris. Profil ini bukan grammar lengkap Monaco/TextMate/tree-sitter: regex literal JS, JSX, f-string/template interpolation, macro kompleks, dan embedded language belum diparse penuh.

## Kenapa penyimpanan dan renderingnya ringan

| Lapisan | Implementasi |
| --- | --- |
| Dokumen | `floem_editor_core::buffer::Buffer` dengan Xi Rope UTF-8; satu engine aktif di Rust |
| Edit | `Buffer::edit` menghasilkan delta dan invalidasi baris. Cursor/selection dipindahkan dengan delta upstream |
| History | `Buffer::do_undo` / `do_redo` upstream. Host membatasi jendela undo ke 128 grup. Karena rilis core belum punya API GC publik, adapter berkala merekonstruksi Buffer dengan replay grup yang dipertahankan lewat API core |
| Python/Qt | Hanya menerima teks dan span untuk viewport; tidak menyimpan `QTextDocument` atau salinan string seluruh file |
| Rendering | `QQuickPaintedItem` berukuran viewport memakai `QTextLayout` untuk shaping; bukan canvas sebesar seluruh dokumen |
| Cache layout | Maksimal 192 baris, dengan maksimum 8.192 karakter yang ditampilkan per baris |
| Coloring | Lexer Rust per baris; cache hanya state awal baris, bukan token semua dokumen. Setelah edit, suffix state dibatalkan |
| Propagasi warna | Dikerjakan bertahap, paling banyak 1.500 baris atau sekitar 128 KiB teks per langkah, lalu dilanjutkan timer. Baris jauh dapat tampil tanpa warna sementara |
| Pencarian | KMP streaming terhadap rope; tidak mengalokasikan satu string sebesar seluruh file |
| Dependency Qt | Menggunakan `PySide6-Essentials`, tidak menginstal Qt WebEngine sebagai dependency |

`QQuickPaintedItem` menggunakan QPainter untuk menggambar viewport ke surface Qt Quick. Ini belum renderer glyph khusus yang seluruhnya berjalan di GPU. Layout dilakukan lewat Qt, sehingga shaping/font fallback tidak ditulis ulang di Python. `QSG_RENDER_LOOP=basic` dipilih agar callback paint Python tidak berebut GIL dengan thread GUI. Renderer GPU/platform tetap boleh digunakan; software renderer hanya dipaksa dalam pengujian headless.

History memakai cache metadata replay dan snapshot Xi Rope dengan structural sharing. Rekonstruksi dilakukan secara amortized sekitar 256 grup, 1.024 operasi core, atau penambahan biaya perubahan sekitar 32 MiB sejak pemangkasan terakhir; saat itu adapter menargetkan soft budget 16 MiB. Grup besar atau redo dapat melampaui target. Ini bukan batas keras RAM, dan rekonstruksi history masih dapat menghasilkan jeda pada edit besar. Tidak ada salinan dokumen UTF-8 penuh di Python.

Offset core berupa byte UTF-8. Konversi kolom Qt UTF-16 menggunakan metric Xi Rope; posisi di tengah surrogate pair dipindah sebelum emoji. Pemanggilan eksplisit `char_count()` menghitung scalar seluruh dokumen; fungsi itu tidak dipakai pada hot path render/edit.

Ukuran ZIP source tidak sama dengan kebutuhan disk/RAM aplikasi. Paket Qt, Python, dan extension hasil kompilasi dipasang terpisah.

## Pengukuran yang benar-benar dijalankan

Pengujian pada Linux x86-64, Python 3.12.14, Qt/PySide6 6.10.3, release build Rust. Backend GUI `offscreen/software`, satu proses per kasus, bukan pengukuran Windows/GPU dan bukan perbandingan dengan Chromium.

| Kasus | Ukuran file | Baris | RSS proses setelah sekitar 800 ms | Proses anak |
| --- | --- | --- | --- | --- |
| Python kecil | 67 byte | 4 | 86,98 MiB | 0 |
| Rust besar | 10,68 MiB | 160.001 | 98,47 MiB | 0 |

Pada file besar tersebut, backend membuka file secara streaming dalam 33,382 ms pada satu run. Dari 1.000 insert satu karakter di posisi tersebar, median 0,0186 ms dan p95 0,0491 ms. **Angka insert ini hanya backend + pemanggilan PyO3/history, belum termasuk paint, layout, coloring, atau waktu input sampai layar.** First frame pada dua run sekitar 168–286 ms. Ini pengukuran run versi ini, bukan bukti lebih cepat dari prototipe sebelumnya. Hasil dipengaruhi cache OS, mesin, backend dan driver; RSS juga mencakup shared mappings.

Lihat [BENCHMARK.json](BENCHMARK.json) untuk hasil mentah dan [benchmark.py](benchmark.py) untuk mengulang. Install `psutil` agar probe mencetak RSS:

```sh
python -m pip install psutil
python benchmark.py --headless --output BENCHMARK.json
```

Hilangkan `--headless` jika ingin mengukur dengan backend desktop mesin sendiri. Angka lokal itulah yang lebih relevan untuk penggunaan lo.

## Pengujian

Sudah dijalankan:

- 11 unit test Rust untuk Unicode/CRLF, surrogate offsets, decoder UTF-8 antar-chunk, core history, compaction, cursor/dirty tracking, dan lexer.
- 20 test Python/Qt: 2.500 edit acak ditambah 5.000 operasi edit/undo/redo/branching dibanding referensi independen, keyboard/mouse lewat QQuickWindow, clipboard, IME sintetis, AltGr, unsaved guard, gagal load/save, long-line mode, block indent/outdent, dan cache terbatas. Termasuk pengujian history melewati batas 128 grup dan compaction.
- Clippy seluruh target dengan `-D warnings`.
- Build wheel release, instal wheel tersebut, dan jalankan test/probe dari package hasil instalasi. QML dan extension native sudah diverifikasi ikut masuk wheel.
- UI dirender dan diperiksa secara visual di sandbox.

```sh
python -m pip install -r requirements-dev.txt
cargo test --locked --manifest-path native/Cargo.toml
cargo clippy --locked --manifest-path native/Cargo.toml --all-targets -- -D warnings
python -m pytest -q
```

Pengujian UI memakai offscreen/software secara default. Tes IME memakai event Qt sintetis, belum memverifikasi seluruh perilaku IME Windows/macOS yang sebenarnya. Jika ingin pengembangan editable:

```sh
maturin develop --release --locked
```

## Dipakai di aplikasi PySide lo

`NativeEditor` adalah tipe Qt Quick yang bisa didaftarkan ke QML milik aplikasi yang sudah ada:

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

Daftarkan tipe sebelum `engine.load()`. Jika menggabungkan ke aplikasi sendiri, set `QSG_RENDER_LOOP=basic` sebelum membuat `QGuiApplication`. Host dapat memanggil `loadPath(path)`, `saveCurrent()`, `setLanguage(name)`, `undo()`, `redo()`, dan `findNext(text, backwards)`. Signal `openRequested`, `saveAsRequested`, `confirmRequested`, `errorOccurred`, dan `closeApproved` dapat dihubungkan ke dialog/UI host; contoh lengkap ada di `Main.qml`. Untuk perubahan file yang dilakukan pengguna, pakai alur `request()`/`openUrl()` agar unsaved guard dijalankan; `loadPath()` adalah operasi langsung untuk host/startup.

Core dapat dipakai tanpa Qt:

```python
from quill_native._engine import Document

doc = Document("Halo 🦀\n", "contoh.rs")
assert doc.backend() == "floem-editor-core/0.2.0"
doc.set_cursor(0, 5)  # baris dan kolom zero-based; kolom UTF-16
doc.insert("Rust ")
rows, color_ready = doc.viewport(0, 30)
doc.undo()
```

## Batasan versi ini

- Satu dokumen aktif, belum workspace/multitab, minimap, folding, autocomplete, LSP, debugger, atau extension ecosystem Monaco.
- Belum word wrap, regex search/replace, multi-cursor pada UI, atau aksesibilitas editor setara komponen Qt bawaan. Gerakan vertikal memakai kolom UTF-16, belum mempertahankan posisi visual horizontal pada baris bertab/bidirectional.
- Baris panjang ditampilkan hanya 8.192 karakter pertama untuk menjaga waktu layout. Baris di atas 32 KiB tidak diwarnai. Isi asli tetap disimpan utuh, tetapi navigasi/hit testing visual pada bagian yang tidak terlihat belum lengkap. Ini belum editor yang cocok untuk mengedit file minified satu baris berukuran besar.
- Semua I/O dan operasi UI masih di thread GUI. Buka/simpan/pencarian file sangat besar dapat menahan UI; propagasi coloring sudah bertahap. Batas history adalah soft budget, bukan jaminan RAM total.
- Input file UTF-8 valid dengan LF/CRLF, termasuk mixed LF/CRLF. File dengan bare CR ditolak secara eksplisit agar `Buffer::new` upstream tidak diam-diam mengubah line ending; file asli tidak diubah. Constructor `Document(text)` mengikuti normalisasi bare CR bawaan core. Belum pilihan encoding lain atau decoding file biner.
- Belum ada pengujian atau executable Windows/macOS. Source dapat dibangun di platform tersebut, tetapi hasilnya perlu diverifikasi di mesin target.

## Sumber teknis

- [Floem editor core 0.2.0](https://docs.rs/floem-editor-core/0.2.0/floem_editor_core/)
- [Lapce Xi Rope 0.3.2](https://docs.rs/lapce-xi-rope/0.3.2/lapce_xi_rope/)
- [PyO3](https://pyo3.rs/) dan [maturin](https://www.maturin.rs/)
- [PySide6 QQuickPaintedItem](https://doc.qt.io/qtforpython-6/PySide6/QtQuick/QQuickPaintedItem.html)
- [PySide6 QTextLayout](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QTextLayout.html)

Lisensi source project: MIT. Dependency Rust/Qt/Python memiliki lisensi masing-masing. Laporan SPDX Rust beserta pilihan lisensi permisif disertakan; source dependency diambil oleh Cargo, bukan dibundel dalam ZIP ini. Penggunaan Qt tetap mengikuti ketentuan lisensi Qt/PySide yang dipilih pengguna.
