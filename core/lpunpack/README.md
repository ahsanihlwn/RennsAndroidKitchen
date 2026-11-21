# lpunpack & lpmake (Rust)

Fast, dependency-free Rust CLI utilities (`lpunpack` and `lpmake`) and library (`lp_tools`) for inspecting, unpacking, and creating Android logical partition (`super`) images, with built-in support for raw, Android sparse, Virtual A/B (v10.2), multi-slot, and split super images.

## Build

```sh
cargo build --release
```

Compiled binaries:
- `target/release/lpunpack`
- `target/release/lpmake`

## Usage

### `lpunpack`

Inspect metadata or extract partition images from a `super.img` (raw, sparse, or metadata-only `super_empty.img`):

```sh
# Extract all partitions into out_dir
lpunpack super.img out_dir

# Extract specific partitions (repeatable or comma/colon-delimited)
lpunpack -p system_a,vendor_a super.img out_dir
lpunpack -p system_a -p vendor_a super.img out_dir

# Select metadata slot (0 = _a, 1 = _b)
lpunpack -S 1 super.img out_dir

# Display partition metadata without extracting
lpunpack --info super.img
lpunpack --info -f json super.img
```

### `lpmake`

Create raw, sparse, metadata-only, or split Android `super` partition images:

```sh
lpmake \
  --device-size 4294967296 \
  --metadata-size 65536 \
  --metadata-slots 2 \
  --group main_a:4290772992 \
  --partition system_a:readonly:2147483648:main_a \
  --image system_a=system.img \
  --virtual-ab \
  --sparse \
  --output super.img
```

## Testing

```sh
cargo test
```

## License

[MIT](LICENSE)
