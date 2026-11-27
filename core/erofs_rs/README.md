# erofs-rs

Native Rust toolkit for **EROFS** (Enhanced Read-Only File System) images. High performance, zero external C library dependencies for extraction/inspection, and full compatibility with Android ROM kitchens.

Licensed under the permissive **MIT License**.

---

## Features

- **Android ROM Kitchen Ready**: Multi-threaded unpack emitting standard AOSP `fs_config`, SELinux `file_contexts`, and `fs_options` metadata.
- **Windows Unprivileged Compatibility**: Automatic NTFS per-directory case-sensitivity (preserving case-colliding filenames on extract) and unprivileged Cygwin UTF-16LE symlink support (`--symlink-mode auto|cygwin|native`) on unpack and pack.
- **Multiple Compression Engines**: Supports `none`, `lz4`, `lz4hc`, `deflate`, `zstd`, and `lzma` (MicroLZMA) with configurable cluster sizes (4K, 16K, etc.).
- **Direct Image Editing**: Modify files (`edit write`), file modes (`edit chmod`), SELinux contexts (`edit selinux`), or delete entries (`edit rm`) directly inside the image without unpacking.
- **In-Place Compaction**: Reclaim internal slack and relocate metadata blocks in-place (`compact`).
- **Image Verification**: Superblock CRC32C, dirent ordering, full stream decompression, and SHA-256 xattr digest verification (`verify`).
- **Android Sparse Support**: Native read and extraction support for Android Sparse images (`0xED26FF3A`) without requiring manual unsparsing.
- **AVB Footer Management**: Inspect and strip Android Verified Boot (`AVBf`) footers and vbmeta structures.
- **Cross-Partition Resolution**: Resolve cross-partition symlinks across multiple partition images (`resolve`).
- **Script & Tool Friendly**: Every subcommand supports `--json` for machine-readable output.

---

## Build

Requirements: Rust 1.85+ (Edition 2024)

```bash
cargo build --release
```

Binary output: `target/release/erofs-rs` (or `erofs-rs.exe` on Windows).

---

## CLI Reference

All commands accept global `--json` (for scripts and GUI integration) and `--offset <BYTES>` (for embedded images).

### Inspection & Exploration

```bash
# Detect filesystem type (EROFS/EXT) and sparse status
erofs-rs detect product.img

# Inspect superblock, feature bitmasks, algorithms, UUID, CRC32C, and AVB footer
erofs-rs info product.img

# List directory contents (flat, long, tree, or filtered)
erofs-rs list product.img --long
erofs-rs list product.img --tree
erofs-rs list product.img /etc --filter prop

# Inspect detailed inode metadata, xattrs, SELinux, capabilities, and physical block spans
erofs-rs stat product.img /etc/build.prop

# Read a file directly from the image to stdout or file
erofs-rs read product.img /etc/build.prop
erofs-rs read product.img /etc/build.prop -o ./extracted_build.prop
erofs-rs read product.img /etc/build.prop --check-text --json
```

### Unpack & Metadata Generation

Multi-threaded extraction with hardlink deduplication, NTFS per-directory case-sensitivity & unprivileged Cygwin symlinks on Windows (`--symlink-mode auto|cygwin|native`), and complete Android ROM kitchen metadata export:

```bash
# Extract full partition (writes extracted files + config/ directory)
erofs-rs unpack product.img -o ./out --overwrite

# Explicitly select symlink format (auto = Cygwin on Windows, native on Unix)
erofs-rs unpack product.img -o ./out --symlink-mode cygwin --overwrite

# Generate only metadata configs (fs_config, file_contexts, fs_options) without extracting files
erofs-rs unpack product.img -o ./out --config-only

# Targeted extraction (extract specific files/directories)
erofs-rs unpack product.img -o ./out --target /etc --recursive
erofs-rs unpack product.img -o ./out --targets-file ./targets.txt
```

Generated metadata files in `<out>/config/`:
- `<partition>_fs_config` — AOSP filesystem configuration (UID, GID, octal mode, capabilities).
- `<partition>_file_contexts` — SELinux context rules with standard regex escaping.
- `<partition>_fs_options` — ROM kitchen repack parameters and filesystem timestamps.

### Image Packing

Build clean EROFS images from a directory with hardlink preservation, Cygwin/native symlink support, automatic `--mount-point` inference from Android configs, and full compression control:

```bash
# Pack with LZ4HC and Android configs
erofs-rs pack ./out/product product_repack.img \
  --algo lz4hc \
  --pcluster 16384 \
  --mount-point /product \
  --fs-config ./out/config/product_fs_config \
  --file-contexts ./out/config/product_file_contexts

# Pack uncompressed or with Zstandard
erofs-rs pack ./out/vendor vendor.img --algo none
erofs-rs pack ./out/system system.img --algo zstd --level 6
```

### Direct In-Place Editing (Unpackless)

Modify or delete entries inside an existing EROFS image without unpacking or rebuilding:

```bash
# Replace file content in-place (preserves or updates superblock CRC32C)
erofs-rs edit write product.img /etc/build.prop --from ./new_build.prop --dry-run
erofs-rs edit write product.img /etc/build.prop --from ./new_build.prop

# Update permissions or SELinux label
erofs-rs edit chmod product.img /etc/build.prop 0644
erofs-rs edit selinux product.img /etc/build.prop u:object_r:system_file:s0

# Remove files or directories
erofs-rs edit rm product.img /app/Bloatware --recursive
```

### Compaction & Space Analysis

```bash
# Calculate data, metadata, wasted, and reclaimable space
erofs-rs usage product.img

# Reclaim holes and relocate metadata in-place
erofs-rs compact product.img --dry-run
erofs-rs compact product.img
```

### Verification & AVB Management

```bash
# Full verification: superblock CRC32C, dirent ordering, full decompression & SHA-256 digests
erofs-rs verify product.img --threads 8

# Inspect or strip AVB footer and vbmeta structures
erofs-rs avb inspect product.img
erofs-rs avb strip product.img

# Cross-partition symlink resolution
erofs-rs resolve system.img /product --mount product.img --mount vendor.img
```

---

## License

This project is licensed under the [MIT License](LICENSE).
