// SPDX-License-Identifier: MIT
//! magiskboot — library interface for boot image manipulation.
//!
//! All public functions in this crate are designed to be called from the
//! native PyO3 layer (rak_native) or from the CLI binary (src/main.rs).
//! Nothing in this module depends on `std::process::exit`; callers receive
//! `Result<_, String>` and decide how to handle failures.

pub mod bootimg;
pub mod compress;
pub mod cpio;
pub mod dtb;
pub mod format;
pub mod hexpatch;
pub mod pattern;

// Re-export the most commonly used entry points at the crate root so callers
// don't need to reach into sub-modules.
pub use bootimg::{BootHeaderInfo, cmd_repack, cmd_split, cmd_unpack, inspect_boot_image};
pub use bootimg::{
    BOOTCONFIG_FILE, DTB_FILE, EXTRA_FILE, HEADER_FILE, KER_DTB_FILE, KERNEL_FILE, NEW_BOOT,
    RAMDISK_FILE, RECV_DTBO_FILE, SECOND_FILE, V4_META_FILE,
};
pub use compress::{
    cmd_compress, cmd_decompress, compress_bytes, compress_lz4_legacy, decompress_bytes,
    decompress_lz4_legacy,
};
pub use cpio::{CpioArchive, CpioEntry, cmd_cpio};
pub use dtb::cmd_dtb;
pub use format::{Format, detect_format, detect_format_lg};
pub use hexpatch::cmd_hexpatch;
