# EXT Unpacker (Rust)

Fast, zero-dependency command-line utility written in Rust to inspect and unpack **EXT2 / EXT3 / EXT4** filesystem images (raw and Android sparse images), generating repack-ready metadata (`fs_config`, `file_contexts`, `info`).

## Features

- **EXT2 / EXT3 / EXT4 Support**:
  - Automatic filesystem type and feature detection (`EXT2`, `EXT3`, `EXT4`, `EXT4 Legacy`, 64-bit, journal, extents)
  - Full support for direct, singly/doubly/triply indirect block maps (EXT2/EXT3), multi-level extent trees (EXT4), unwritten extents, sparse holes, inline data, and fast/slow symlinks
  - Transparent Android sparse image (`0xED26FF3A`) reading without creating temporary raw copies
- **Metadata & Config Generation**:
  - Extracts `security.selinux` and `security.capability` (full 64-bit capability masks) from inline xattrs, external xattr blocks, and large EA inodes
  - Generates repack-compatible `<partition>_fs_config`, deduplicated `<partition>_file_contexts`, `<partition>_info`, and `<partition>_space.txt`
  - Preserves symlinks cross-platform (native symlinks on Unix, Cygwin Unicode symlinks on Windows)
- **Fast Streaming I/O**:
  - Buffered 1 MiB streaming directly from image blocks to disk with minimal memory footprint

## Usage

### Inspect an image (`--read`)

```bash
ext_cli --read path/to/image.img
```

### Unpack an image (`--unpack`)

```bash
ext_cli --unpack path/to/image.img
```

Both `--read <path>` and `--read --x <path>` (likewise `--unpack --x <path>`) are accepted.

### Output Layout (`--unpack`)

Given `<dir>/<partition>.img`:

```
<dir>/
├── <partition>/                  # Extracted filesystem contents
└── config/
    ├── <partition>_fs_config     # Path, UID, GID, octal mode, capabilities, symlink target
    ├── <partition>_file_contexts # Deduplicated SELinux file_contexts rules
    ├── <partition>_info          # Partition format, size, block info, UUID
    └── <partition>_space.txt     # Original paths containing spaces (if any)
```

## Build & Test

```bash
cargo build --release
cargo test
```

Release binary: `target/release/ext_cli` (`ext_cli.exe` on Windows).

## License

[MIT](LICENSE)
