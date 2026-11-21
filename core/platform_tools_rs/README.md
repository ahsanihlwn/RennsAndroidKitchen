# platform-tools-rs

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Rust](https://img.shields.io/badge/Rust-1.85%2B-orange.svg)](Cargo.toml)
[![Baseline](https://img.shields.io/badge/AOSP%20platform--tools-37.0.3--16887417-green.svg)](config/source.properties)

A Rust reimplementation of the Android SDK `platform-tools` suite targeting AOSP **`37.0.3-16887417`** (ADB protocol **`1.0.41`**).

Every CLI tool and supporting library in this workspace is written in native Rust with full CLI, wire-protocol, and on-disk format compatibility with the official Android SDK `platform-tools` distribution, with no external C++ Android runtime libraries or Windows WinUSB wrapper DLLs required.

---

## Key Features

- **Native USB, TCP, and UDP Transports**:
  - Uses asynchronous USB bulk transfers via [`nusb`](https://crates.io/crates/nusb) (`0xFF/0x42/0x01` for ADB, `0xFF/0x42/0x03` for Fastboot) alongside TCP (`5037` / `5555` / `FB01`) and UDP transports—no `AdbWinApi.dll` or `AdbWinUsbApi.dll` needed on Windows.
- **Full `adb` Client & Host Server (`1.0.41`)**:
  - Supports RSA-2048 authentication (`adb keygen` + `A_AUTH`), `host:tport:` & `host:transport*`, `shell,v2:` (stdout/stderr demux, exit code propagation, interactive PTY stdin), recursive `push`/`pull`/`sync` with 64-bit `STA2`/`LST2` and `sendrecv_v2` compression (`Brotli` via [`ahsanihlwn/brotli-rs`](https://github.com/ahsanihlwn/brotli-rs), `LZ4F`, `Zstd`), `install`/`install-multiple`, `forward`/`reverse`, `logcat`, `bugreport`, and `sideload`/`sideload-host`.
- **Standalone Partition, Boot, & Image Tools**:
  - Built-in Rust implementations of `mke2fs` (`Ext2/Ext3/Ext4 + JBD2`), `make_f2fs` / `make_f2fs_casefold` (`F2FS v1.16.0`), `android-sparse`, `android-avb` (`vbmeta`), `android-bootimg` (`boot.img` v0–v4 & `vendor_boot.img` v3–v4), `android-lp` (`super` dynamic partitions), `etc1tool`, `hprof-conv`, and `sqlite3`.

---

## Workspace Architecture (15 Crates)

### 8 CLI Binaries

| Binary | Crate | Description |
| :--- | :--- | :--- |
| **`adb`** | [`crates/adb`](crates/adb) | Android Debug Bridge CLI client & TCP `5037` host server daemon (`1.0.41` / `37.0.3-16887417`). Supports USB (`nusb`) & TCP `adbd` multiplexing, RSA-2048 `adb keygen`, `A_CNXN` / `A_AUTH`, `shell,v2:`, recursive `push`/`pull`/`sync` (`STAT`/`STA2`, `LIST`/`DENT` & `LST2`/`DNT2`, `SEND`/`RECV`, `SND2`/`RCV2` with `Brotli`, `LZ4F`, `Zstd`), `install`/`install-multiple`/`install-multi-package`, `forward`, `reverse`, `logcat`, `bugreport`, `sideload`/`sideload-host`, `root`, and `remount`. |
| **`fastboot`** | [`crates/fastboot`](crates/fastboot) | Android Fastboot flashing, bootloader, and logical partition tool (`37.0.3-16887417`). Supports USB (`nusb`), TCP (`FB01`), and UDP transports, sparse splitting (`-S`), `AVB0` `vbmeta` patching (`--disable-verity`, `--disable-verification`), `boot.img` & `vendor_boot.img` live repacking, `format:ext4` / `format:f2fs`, `liblp` dynamic partitions, `snapshot-update`, `flashall`, and `update`. |
| **`mke2fs`** | [`crates/mke2fs`](crates/mke2fs) | Ext2 / Ext3 / Ext4 + JBD2 journal formatter (`1.47.2` CLI compatible). Supports `-E android_sparse`, `-O casefold`, `-b 1024/2048/4096`, multi-group metadata layout, and `-d <root_dir>` recursive directory tree population. |
| **`make_f2fs`** | [`crates/make_f2fs`](crates/make_f2fs) | F2FS `v1.16.0` formatter supporting raw and Android sparse (`-S`) output, user/group/project quotas (`-q`), casefolding (`-C utf8`), encryption, compression, extra attributes, and verity. |
| **`make_f2fs_casefold`** | [`crates/make_f2fs_casefold`](crates/make_f2fs_casefold) | F2FS `v1.16.0` formatter pre-configured with `casefold` (`utf8`), `quota`, and `prjquota` defaults. |
| **`etc1tool`** | [`crates/etc1tool`](crates/etc1tool) | ETC1 texture encoder and decoder (`.png` <-> `.pkm`, `--encode`, `--encodeNoHeader`, `--decode`, `--showDifference`). |
| **`hprof-conv`** | [`crates/hprof-conv`](crates/hprof-conv) | Dalvik / ART HPROF `1.0.3` to standard Java HPROF `1.0.2` heap dump converter. |
| **`sqlite3`** | [`crates/sqlite3`](crates/sqlite3) | SQLite 3 CLI shell supporting standard output modes (`list`, `csv`, `column`, `line`, `html`, `insert`, `quote`, `tabs`) and dot-commands (`.dump`, `.schema`, `.tables`, `.indices`, `.import`, `.backup`, `.read`, `.output`), backed by `rusqlite`. |

### 7 Core Library Crates

| Library Crate | Path | Purpose |
| :--- | :--- | :--- |
| **`pt-common`** | [`crates/pt-common`](crates/pt-common) | Shared `platform-tools` version constants (`37.0.3-16887417`, `1.0.41`) and platform helpers. |
| **`android-sparse`** | [`crates/android-sparse`](crates/android-sparse) | Android sparse image (`libsparse`, magic `0xED26FF3A`) reader, writer, sparse CRC-32 (`RAW`, `FILL`, `DONT_CARE`, `CRC32`), unsparser, and multi-image resparser/splitter. |
| **`f2fs-mkfs`** | [`crates/f2fs-mkfs`](crates/f2fs-mkfs) | F2FS `v1.16.0` on-disk layout engine, SIT/NAT/SSA/Checkpoint/Root-Inode/Quota builder, and structural/CRC-32 verifier. |
| **`extfs-mkfs`** | [`crates/extfs-mkfs`](crates/extfs-mkfs) | Ext2 / Ext3 / Ext4 + JBD2 journal + CRC-16 / CRC-32C + directory population engine. |
| **`android-avb`** | [`crates/android-avb`](crates/android-avb) | Android Verified Boot 2.0 (`AVB0` `vbmeta`) header parser and `--disable-verity` / `--disable-verification` flag patcher. |
| **`android-bootimg`** | [`crates/android-bootimg`](crates/android-bootimg) | Android `boot.img` (`v0`–`v4`, `"ANDROID!"`) and `vendor_boot.img` (`v3`–`v4`, `"VNDRBOOT"`) parser, builder, and kernel/ramdisk/DTB repacker. |
| **`android-lp`** | [`crates/android-lp`](crates/android-lp) | Android Dynamic Partitions (`liblp`) `super` partition geometry (`0x616C4467`) and metadata (`0x30504C41`) parser and builder. |

---

## Related Projects & Upstream AOSP References

### Ecosystem
- **[`ahsanihlwn/brotli-rs`](https://github.com/ahsanihlwn/brotli-rs)**: Native Rust implementation of Brotli (`v1.2.0`, RFC 7932) compression and decompression, used by `crates/adb` for `sendrecv_v2_brotli` (`adb push/pull -z brotli`).

### Upstream AOSP Repositories
- **AOSP Platform Build & SDK Packaging**: [`platform/development` (`build/Android.bp`)](https://android.googlesource.com/platform/development/+/master/build/Android.bp) & [`platform/build`](https://android.googlesource.com/platform/build)
- **Android Debug Bridge (`adb`)**: [`platform/packages/modules/adb`](https://android.googlesource.com/platform/packages/modules/adb)
- **Fastboot, `libsparse`, `mkbootimg`, and `liblp`**: [`platform/system/core`](https://android.googlesource.com/platform/system/core)
- **Filesystem & System Extras**: [`platform/system/extras`](https://android.googlesource.com/platform/system/extras/)
- **Android Verified Boot (`libavb`)**: [`platform/external/avb`](https://android.googlesource.com/platform/external/avb)
- **F2FS On-Disk Format**: [`platform/external/f2fs-tools`](https://android.googlesource.com/platform/external/f2fs-tools)
- **Ext4 & JBD2 On-Disk Format**: [`platform/external/e2fsprogs`](https://android.googlesource.com/platform/external/e2fsprogs)
- **Dalvik `hprof-conv` & Native `etc1tool`**: [`platform/dalvik`](https://android.googlesource.com/platform/dalvik) & [`platform/frameworks/native`](https://android.googlesource.com/platform/frameworks/native)

---

## Building from Source

### Prerequisites
- **Rust** `1.85` or newer (`cargo` and `rustc`)

### Build All 8 Release Binaries

```bash
cargo build --workspace --release --bins
```

Compiled binaries are written to `target/release/`:
- `adb` (`adb.exe` on Windows)
- `fastboot` (`fastboot.exe` on Windows)
- `mke2fs` (`mke2fs.exe` on Windows)
- `make_f2fs` (`make_f2fs.exe` on Windows)
- `make_f2fs_casefold` (`make_f2fs_casefold.exe` on Windows)
- `etc1tool` (`etc1tool.exe` on Windows)
- `hprof-conv` (`hprof-conv.exe` on Windows)
- `sqlite3` (`sqlite3.exe` on Windows)

Default configuration files live in [`config/`](config):
- [`config/source.properties`](config/source.properties) (`Pkg.Revision=37.0.3`)
- [`config/mke2fs.conf`](config/mke2fs.conf)

### Run Tests & Lint

```bash
cargo test --workspace
cargo clippy --workspace --all-targets -- -D warnings
```

---

## License

This project is licensed under the [MIT License](LICENSE).
