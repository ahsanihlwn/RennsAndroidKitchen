# make_ext4-rust

Fast ext4 filesystem and Android sparse image builder (`make_ext4fs`, `img2simg`, and `simg2img`) written in Rust with buffered streaming I/O and SIMD-accelerated CRC32.

## Features

- Full ext4 image creation (`-l`, `-b`, `-g`, `-i`, `-I`, `-j`, `-J`, `-L`, `-f`, `-T`, `-v`, `-B`)
- Android `fs_config` built-in rules (`-a`) and canned `fs_config` (`-C`, including `-1` field preservation and Linux capabilities xattrs)
- SELinux `file_contexts` labeling (`-S`, including `.homedirs`/`.local`, stem compression, inline and external block xattrs)
- Android sparse image format (`-s`) with optional CRC32 chunk (`-c`)
- Gzip compression (`-z`, level 9 via `zlib-rs`)
- Windows Cygwin Unicode symlink support (`!<symlink>\xff\xfe`) and Unix symlinks
- Included sparse utilities: `img2simg` and `simg2img`

## Build & Test

```bash
cargo build --release
cargo test
```

Binaries produced in `target/release/`:
- `make_ext4fs`
- `img2simg`
- `simg2img`

## License

[MIT](LICENSE)
