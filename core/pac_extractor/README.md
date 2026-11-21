# PAC Extractor with RUST

A fast, zero-dependency Rust CLI and library for inspecting, CRC-16 verifying, and extracting Spreadtrum/Unisoc `.pac` firmware packages.

## Features

- Parses `BP_R1.0.0` and `BP_R2.0.1` `.pac` headers and 64-bit partition descriptors
- Verifies header (`CRC1`) and payload (`CRC2`) integrity using CRC-16/ARC (`0xA001`)
- Streams partition payloads to disk with buffered I/O and path-traversal sanitization
- Zero external runtime or crate dependencies (Rust standard library only)

## Build

```bash
cargo build --release
```

The compiled binary will be placed at `target/release/pac-extractor` (or `target\release\pac-extractor.exe` on Windows).

## Usage

```bash
pac-extractor [OPTIONS] <pacfile> [outdir]
```

### Arguments

- `<pacfile>` — Path to the `.pac` firmware image
- `[outdir]` — Destination directory for extracted files (defaults to `./outdir`)

### Options

- `-d`, `--debug` — Print decoded primary header and partition table metadata
- `-c`, `--crc` — Verify `CRC1` (header) and `CRC2` (payload) checksums before extraction
- `-h`, `--help` — Display help information

### Example

```bash
pac-extractor -d -c firmware.pac ./extracted
```

## Testing

```bash
cargo test
```

## License

Licensed under the [MIT License](LICENSE).
