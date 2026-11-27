# sdatimg

A versatile Rust-based tool for bidirectionally processing Android block-based OTA images (`transfer.list` and `new.dat`) as well as sparse/raw partition images in a single binary.

## Key Features

- **sdat2img**: Extracts Android OTA sparse data (`system.transfer.list` + `system.new.dat`) into a partition image (raw or sparse). Supports transfer list formats v1 through v4 (Android 5.0 Lollipop through current Android).
- **img2sdat**: Converts an Android partition image (sparse or raw block image) into block OTA packages (`.new.dat`, `.transfer.list`, `.patch.dat`). Supports automatic input format detection and incremental diffing options.
- **simg2img**: Converts an Android sparse image (`0xED26FF3A`) into a raw disk image.
- **img2simg**: Converts a raw disk image into an Android sparse image.

## Build / Installation

Make sure the Rust toolchain is installed, then run:

```bash
cargo build --release
```

The compiled binary will be placed at `target/release/sdatimg` (or `target/release/sdatimg.exe` on Windows).

---

## Usage Guide & Examples

### 1. `sdat2img` (Extract OTA `.dat` to `.img`)

Convert `system.transfer.list` and `system.new.dat` into a raw `system.img`:

```bash
sdatimg sdat2img system.transfer.list system.new.dat system.img
```

If no output filename is specified, it defaults to `system.img`:

```bash
sdatimg sdat2img vendor.transfer.list vendor.new.dat
```

Additional options:
- `--sparse`: Output an Android sparse image instead of a raw image.
- `--no-clobber`: Prevent overwriting if the output file already exists.
- `-q, --quiet`: Suppress progress logs.

Sparse output example:
```bash
sdatimg sdat2img system.transfer.list system.new.dat system.sparse.img --sparse
```

---

### 2. `img2sdat` (Convert `.img` to OTA `.dat`)

Convert an image (sparse or raw) into `.new.dat`, `.transfer.list`, and `.patch.dat`:

```bash
sdatimg img2sdat system.img -o output_dir -p system
```

Generated files inside `output_dir/`:
- `system.new.dat`
- `system.transfer.list`
- `system.patch.dat`

Additional options:
- `-o, --outdir <DIR>`: Output directory (default: current directory `.`).
- `-p, --prefix <NAME>`: Output filename prefix (default: `system`).
- `-v, --version <1..4>`: Transfer list format version (default: `4` for Android 7.0+).
- `--format <auto|sparse|raw>`: Force input image format (default: `auto`).
- `--file-map <FILE>`: Optional block map file path.
- `--clobbered <RANGES>`: Clobbered block range set (default: `0`).
- `--src <SRC_IMG>`: Source image for generating an incremental OTA patch.
- `-q, --quiet`: Quiet mode / suppress progress logs.

Example with custom version and prefix:
```bash
sdatimg img2sdat vendor.img -o out -v 4 -p vendor
```

---

### 3. `simg2img` (Sparse to Raw)

Extract an Android sparse image into a raw image:

```bash
sdatimg simg2img system.sparse.img system.raw.img
```

---

### 4. `img2simg` (Raw to Sparse)

Pack a raw partition image into an Android sparse image:

```bash
sdatimg img2simg system.raw.img system.sparse.img
```

---

## License

This project is licensed under the [MIT](LICENSE) license.
