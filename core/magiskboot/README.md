# RUST Magiskboot Tools

A high-performance Android boot image manipulation utility written entirely in Rust.

## Highlights

- **Full Boot Image Support**: Handles boot headers v0, v1, v2, v3, and v4.
- **Vendor Boot v4 Multi-Ramdisk**: Native extraction, differential routing, and repackaging of multi-ramdisk fragments along with dtb, bootconfig, and AVB hash recalculation.
- **Multi-threaded Compression**: Parallel chunk compression and decompression for high throughput.
- **Built-in Archive & Device Tree Engine**: Includes standalone CPIO archive handling and Flattened Device Tree (FDT) parsing and patching.
- **Broad Codec Coverage**: Supports gzip, zopfli, xz, lzma, bzip2, lz4 (frame and legacy/lg), and zstd.

## Usage

```bash
# Unpack boot or vendor_boot image
magiskboot unpack [-n] [-h] <image>

# Repack boot components back to image
magiskboot repack [-n] <origimage> [outimage]

# Manipulate CPIO archives
magiskboot cpio <incpio> [commands...]
magiskboot cpio pack [-c <config>] <infolder> <outcpio>

# Device tree utilities
magiskboot dtb <file> <print|patch|test>

# Kernel / DTB splitter
magiskboot split <kernel-with-dtb>

# Pattern and checksum tools
magiskboot hexpatch <file> <from_hex> <to_hex>
magiskboot sha1 <file>
```

## Credits & Acknowledgements

- Inspired by the original MagiskBoot utility by **topjohnwu**.
- Reference format specifications guided by the Android Open Source Project (AOSP).
- Re-architected and implemented independently in Rust.

## License

This project is licensed under the MIT License.
