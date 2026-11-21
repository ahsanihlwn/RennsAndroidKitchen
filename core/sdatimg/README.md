# sdatimg

Tool serbaguna berbasis Rust untuk memproses Android block-based OTA image (`transfer.list` dan `new.dat`) serta sparse/raw partition image secara dua arah dalam satu binary tunggal.

## Fitur Utama

- **sdat2img**: Mengekstrak file Android OTA sparse data (`system.transfer.list` + `system.new.dat`) menjadi image partisi (raw atau sparse). Mendukung transfer list format versi 1 hingga 4 (Android 5.0 Lollipop sampai Android terkini).
- **img2sdat**: Mengonversi image partisi Android (sparse image atau raw block image) menjadi paket block OTA (`.new.dat`, `.transfer.list`, `.patch.dat`). Mendukung deteksi otomatis format input serta opsi incremental diffing.
- **simg2img**: Mengonversi Android sparse image (`0xED26FF3A`) menjadi raw disk image.
- **img2simg**: Mengonversi raw disk image menjadi Android sparse image.

## Kompilasi / Instalasi

Pastikan Rust toolchain sudah terpasang, lalu jalankan:

```bash
cargo build --release
```

Binary yang dihasilkan berada di `target/release/sdatimg` (atau `target/release/sdatimg.exe` di Windows).

---

## Panduan Penggunaan & Contoh

### 1. `sdat2img` (Mengekstrak OTA .dat ke .img)

Mengonversi file `system.transfer.list` dan `system.new.dat` menjadi raw image `system.img`:

```bash
sdatimg sdat2img system.transfer.list system.new.dat system.img
```

Jika tidak menentukan nama output, default file output adalah `system.img`:

```bash
sdatimg sdat2img vendor.transfer.list vendor.new.dat
```

Opsi tambahan:
- `--sparse`: Menghasilkan Android sparse image alih-alih raw image.
- `--no-clobber`: Mencegah penulisan ulang jika file output sudah ada.
- `-q, --quiet`: Menyembunyikan log progress.

Contoh output sparse:
```bash
sdatimg sdat2img system.transfer.list system.new.dat system.sparse.img --sparse
```

---

### 2. `img2sdat` (Mengonversi .img ke OTA .dat)

Mengonversi image (sparse atau raw) menjadi file `.new.dat`, `.transfer.list`, dan `.patch.dat`:

```bash
sdatimg img2sdat system.img -o output_dir -p system
```

File yang dihasilkan di dalam folder `output_dir/`:
- `system.new.dat`
- `system.transfer.list`
- `system.patch.dat`

Opsi tambahan:
- `-o, --outdir <DIR>`: Direktori tujuan output (default: current directory `.`).
- `-p, --prefix <NAME>`: Prefix nama file output (default: `system`).
- `-v, --version <1..4>`: Versi format transfer list (default: `4` untuk Android 7.0+).
- `--format <auto|sparse|raw>`: Memaksa format input image (default: `auto`).
- `--file-map <FILE>`: Path file block map opsional.
- `--clobbered <RANGES>`: Range set block clobbered (default: `0`).
- `--src <SRC_IMG>`: Source image untuk menghasilkan incremental OTA patch.
- `-q, --quiet`: Mode senyap / tanpa log progress.

Contoh dengan versi dan prefix kustom:
```bash
sdatimg img2sdat vendor.img -o out -v 4 -p vendor
```

---

### 3. `simg2img` (Sparse ke Raw)

Mengekstrak Android sparse image menjadi raw image:

```bash
sdatimg simg2img system.sparse.img system.raw.img
```

---

### 4. `img2simg` (Raw ke Sparse)

Mengemas raw partition image menjadi Android sparse image:

```bash
sdatimg img2simg system.raw.img system.sparse.img
```

---

## Lisensi

Proyek ini dilisensikan di bawah lisensi [MIT](LICENSE).
