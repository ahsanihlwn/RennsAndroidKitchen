# Verifikasi UI dan beban Skia GPU

Pengukuran lokal 12 Oktober 2026: Windows, Python 3.14.0, PySide6 6.10.1,
skia-python 144.0.post2, Radeon RX 560, OpenGL 3.3. Jendela 1080 × 700,
DPR 1, font engine Qt FreeType, tema Ocean, blur dan animasi aktif.

## Sumber UI asli tetap utuh

`C:\Users\ahsanihlwn\PROJ\rennsandroidkitchen` hanya dibaca. Tidak ada kode
yang diimpor/dijalankan atau file yang ditulis ke backup tersebut.
`app.py` di repo kerja sama dengan sumber itu setelah menghapus dua baris
registrasi `install_widget_gpu_rendering`. Definisi UI, ukuran kartu, layout,
label dan logika halaman tidak diganti. Tiga file berikut identik byte:

| File | SHA-256 sumber backup |
| --- | --- |
| core/fonts.py | 4c6734601136bc4cb57a3581bf32c537abfb08fede0d88a824e6a4080135fb6b |
| core/theme_binding.py | 1a23ecdb9f4620cb42e2cceac1e8a7de78392dc3e553ed8634ed67fad4e024d1 |
| main_ext/class_ThemeUi.py | 14cb98b6b9fbdf8b56f70b9de2671a1f857036bbe38e4ae1c813b059ac1d6a7c |

Kesalahan judul berasal dari renderer: stylesheet berupa deklarasi langsung
seperti `font-size:22px; font-weight:600;` dicampur dengan selector tambahan.
Qt menolak gabungan itu sehingga font turun ke ukuran bawaan 13 px. Renderer
sekarang menormalkan deklarasi menjadi `* { ... }` sebelum menambah aturan
transparan. Font asli 22 px, padding dan ukuran widget tetap berlaku.

Font dropdown hilang karena source tombol belum menyertakan child QLabel.
Qt sekarang tetap menggambar caption dan panahnya; saat efek tekan aktif,
renderer menyertakan capture child tersebut dan memakai cache selama isinya
tidak berubah. Font tidak digambar ulang dengan Skia.

Referensi Qt dibuat dengan menjalankan **repo kerja** memakai
`RAK_DISABLE_SKIA_GPU=1`, tanpa menjalankan backup. Inventaris visible widget
membandingkan jenis/nama, teks, font, geometri, contents rect dan stylesheet
asli. Semua field sama pada 34 widget My Projects, 74 Settings dan 35 New
Project. Perbedaan antialiasing tepi GPU memang diharapkan.

- [My Projects yang diperbaiki](skia/projects.png), [referensi Qt](qt/projects.png).
- [Settings dengan kedua dropdown](skia/settings_dropdown.png), [referensi Qt](qt/settings_dropdown.png).
- [Dropdown saat ditekan](skia/settings_pressed.png), [popup pilihan](skia/mode_popup.png).
- [New Project / Single ROM](skia/create.png), [referensi Qt](qt/create.png).

## Metode ukur

`tools/benchmark_ui_gpu.py` membangun shell aplikasi native yang tidak tampil
di layar, tanpa listener/polling startup. Setelah warm-up, setiap kasus memakai
45 sampel. Tabel pertama mengukur `QWidget.grab()` seluruh jendela ditambah
dispatch event dengan cProfile aktif. Angka ini **bukan FPS presentasi layar**.
Qt dan Skia diukur berurutan, bukan bersamaan dengan tes/profil lain.

| Halaman | Qt median / p95 (ms) | Skia median / p95 (ms) | Qt CPU / Skia CPU (s, 45 sampel) | Frame GPU baru saat idle |
| --- | --- | --- | --- | --- |
| My Projects | 8.22 / 11.68 | 12.36 / 13.99 | 0.344 / 0.562 | 0 |
| Settings | 9.60 / 11.11 | 11.91 / 16.01 | 0.422 / 0.562 | 0 |
| New Project | 8.56 / 10.02 | 9.50 / 15.04 | 0.375 / 0.469 | 0 |

Pengukuran awal sesi ini, sebelum perbaikan cache/effect, mencatat Settings
median 33.04 ms, p95 59.68 ms dan CPU 1.891 s untuk 45 snapshot. Total graphics
effect dalam shell turun dari 14 menjadi 4; tombol idle tidak lagi memakai
effect tambahan. Empat effect yang tersisa milik UI yang sudah ada. Halaman
Settings tidak memiliki effect idle yang terlihat.

Perbaikan utama: cache selector terkompilasi dengan identitas C++ stabil,
cache surface/label, cache toggle saat scroll, penanda widget tanpa surface,
presentasi ARGB premultiplied, dan bypass komposisi opacity 100%. Pada uji
scroll yang sama dalam sesi ini, render GPU baru turun dari 174 menjadi 12.
Popup turun dari 69 menjadi 39 render GPU setelah pass opaque dibuang.

## Interaksi dan jeda dispatch

Kasus berikut memakai timer presisi 16 ms, tanpa cProfile. Scroll/press tetap
memaksa snapshot seluruh jendela untuk mengukur beban repaint. Popup memakai
jalur capture/blur/open/close asli dan ditutup tanpa memilih opsi. Interval
yang memuat capture verifikasi dikeluarkan; PNG ditulis setelah pengukuran.

| Kasus | Qt median / p95 work (ms) | Skia median / p95 work (ms) | Qt p95 interval timer / Skia (ms) |
| --- | --- | --- | --- |
| Scroll setelah warm-up | 9.22 / 11.78 | 11.00 / 14.59 | 19.76 / 26.10 |
| Skala tekan dropdown | 10.26 / 14.54 | 11.91 / 15.28 | 17.14 / 17.63 |
| Dispatch popup | — | — | 16.89 / 18.99 |

Interval terpanjang pada popup adalah 38.87 ms untuk Skia dan 21.32 ms untuk
Qt. Jadi masih ada stall singkat; hasil ini tidak menyatakan seluruh lag hilang.
Jalur hybrid tetap memakai readback sinkron pada frame yang berubah dan
presentasi CPU oleh Qt. Qt murni masih lebih ringan dalam pengukuran ini.
Pembuatan bitmap blur tetap memakai jalur Qt. Profil shell ini juga belum
mencakup proyek besar, WebEngine/editor atau pekerjaan engine di background.

Backend akhir: `skia-gpu`, Radeon RX 560, tanpa error frame. Cache texture
terkelola 765,312 byte dan render target 4,776,264 byte. Ini ukuran cache yang
dilaporkan bridge, bukan total VRAM proses/driver. Cache dibatasi 32 MiB untuk
texture dan 24 MiB untuk render target. Repaint idle memakai cache dan tidak
menambah upload/readback GPU.

Data lengkap: [Skia timings](skia/timings.json), [Qt timings](qt/timings.json).
Inventaris dan screenshot ada di direktori yang sama. Perintah untuk mengulang
pengukuran ada di [dokumentasi renderer](../button_gpu_rendering.md).

## Pemeriksaan

- Native Windows GPU: 81 tes lulus.
- Portable/offscreen: 81 tes selesai, 12 pemeriksaan hardware dilewati; sisanya lulus.
- `py_compile` dan `git diff --check` pada file yang diubah lulus.
- Tes memeriksa font Qt, caption/panah dropdown, ukuran judul 22 px, seleksi dan
  pengetikan input, geometri subcontrol, DPI pecahan, coverage border, invalidasi
  cache/theme, mask opacity, kepemilikan effect dan fallback context.

## Kolaborasi eksternal

Script `D:\Projects\antigravity_agent\run_agent.py` dipanggil sesuai permintaan
dalam mode audit baca saja. Proses pertama timeout setelah 600 detik. Percobaan
kedua mengeluarkan sebagian audit sebelum timeout 300 detik. Catatan tentang
penjadwalan prepare berulang pada widget tanpa surface dan capture child label
setiap frame sudah ditangani. Tidak ada klaim bahwa review eksternal lengkap
sudah selesai; implementasi dan pengukuran akhir diverifikasi lewat tes lokal.
