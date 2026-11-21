# brotli-rs

A faithful, 100% pure-Rust port of Google's official C Brotli (`1.2.0`) compression and decompression library — with zero external crate dependencies and zero C FFI.

## Features

- **Pure Rust, Zero Dependencies**: Standard library only; no C compiler or `cc` build scripts required.
- **Full Format & Algorithmic Parity (Google Brotli v1.2.0)**:
  - **Decoder (`dec`)**: Streaming & one-shot decompression, RFC 7932 compliant, Large Window Brotli (up to 30-bit window), metadata block callbacks, raw & serialized shared/compound dictionaries.
  - **Encoder (`enc`)**: All quality levels (`0..=11`), Generic / Text / Font modes, fast one-pass (`Q0`) & two-pass (`Q1`), greedy & HQ Zopfli (`Q10`/`Q11`), histogram clustering & block splitting, literal/distance context modeling, Base64 region detection, SIMD SSE2/SWAR hashers (`H58`/`H59`/`H68`) alongside `H2`–`H6`, `H40`–`H42`, `H54`, `H35`/`H55`/`H65`, `H10`, stream-offset flint, and raw/compound/shared dictionaries.
  - **Idiomatic Rust I/O (`io`)**: `BrotliReader<R: Read>`, `BrotliWriter<W: Write>`, plus convenience helpers `compress`, `compress_with_params`, and `decompress`.
  - **CLI Binary (`brotli`)**: Drop-in CLI compatible with Google's `brotli` tool (`-0`..`-9`, `-q`, `-w`, `--large_window`, `-Z`, `-d`, `-t`, `-c`, `-o`, `-f`, `-j`, `-k`, `-s`, `-n`, `-C`, `-D`, `-K`, `-S`, `-v`, `-V`, `-h`, plus `brcat` / `unbrotli` aliases).

## Usage

### Library

Add to `Cargo.toml`:

```toml
[dependencies]
brotli-rs = { git = "https://github.com/ahsanihlwn/brotli-rs" }
```

One-shot compression & decompression:

```rust
use brotli_rs::enc::BrotliEncoderMode;
use brotli_rs::io::{compress, decompress};

fn main() -> std::io::Result<()> {
    let data = b"Hello, pure-Rust Brotli v1.2.0!";
    let compressed = compress(data, 6, 22, BrotliEncoderMode::Text)?;
    let restored = decompress(&compressed)?;
    assert_eq!(restored, data);
    Ok(())
}
```

Streaming via `std::io::{Read, Write}`:

```rust
use std::io::{Read, Write};
use brotli_rs::enc::BrotliEncoderMode;
use brotli_rs::io::{BrotliReader, BrotliWriter};

fn main() -> std::io::Result<()> {
    let mut encoded = Vec::new();
    {
        let mut writer = BrotliWriter::with_params(&mut encoded, 5, 22, BrotliEncoderMode::Generic);
        writer.write_all(b"Streaming Brotli payload")?;
        writer.finish()?;
    }

    let mut decoded = Vec::new();
    let mut reader = BrotliReader::new(&encoded[..]);
    reader.read_to_end(&mut decoded)?;
    assert_eq!(decoded, b"Streaming Brotli payload");
    Ok(())
}
```

### CLI

```bash
cargo build --release --bin brotli

# Compress a file
./target/release/brotli -q 6 -v input.bin

# Decompress / test integrity
./target/release/brotli -t -v input.bin.br
./target/release/brotli -d -v input.bin.br
```

## Verification

- Passes Google's official compatibility test vectors (one-shot and chunked streaming).
- Passes full encoder/decoder roundtrip tests across qualities `0..=11`, modes `Generic` / `Text` / `Font`, window bits `10..=24` and large-window `25..=30`, Base64 detection, SIMD hashers, prepared dictionaries, metadata blocks, and incompressible fallback.
- Verified against real-world ~923 MiB Android `system.img` / `system.img.br` images (`967,692,288` bytes -> `608,789,588` bytes at `-q 1`): **bit-for-bit identical** SHA-256 output compared to Google's reference Brotli output.

## Architecture

- `src/common/`: Constants, static dictionary (`dictionary.bin`), word transforms (121 transforms), context lookup tables, shared dictionary types.
- `src/dec/`: Streaming bit reader, canonical/2-level Huffman table builder, prefix tables, decoder state machine.
- `src/enc/`: Full v1.2.0 compressor pipeline (ringbuffer, hashers, static/compound/contextual dictionaries, Zopfli shortest-path optimizer, histogram clustering, block splitters, metablock bit-stream writer).
- `src/io.rs`: `BrotliReader`, `BrotliWriter`, and one-shot wrappers.
- `src/bin/brotli.rs`: Command-line interface.

## Credits & References

- **[Google Brotli (`google/brotli`)](https://github.com/google/brotli)** — Primary specification and reference C implementation (v1.2.0) by Jyrki Alakuijala, Zoltan Szabadka, Evgenii Kliuchnikov, and the Brotli Authors (MIT License).
- **[Dropbox `rust-brotli` (`dropbox/rust-brotli`)](https://github.com/dropbox/rust-brotli)** — Referenced for Rust Brotli design patterns and idiomatic API ideas (MIT / BSD-3-Clause).

## License

Licensed under the **MIT License**. See [LICENSE](LICENSE) for full details.
