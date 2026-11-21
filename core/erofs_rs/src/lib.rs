// SPDX-License-Identifier: MIT

//! `erofs-rs`: Clean-room MIT-licensed Rust implementation of EROFS inspection,
//! extraction, packing, verification, direct in-place editing, and compaction.

pub mod android_cfg;
pub mod avb;
pub mod compact;
pub mod compress;
pub mod crc32c;
pub mod edit;
pub mod error;
pub mod extract;
pub mod format;
pub mod io;
pub mod mount_index;
pub mod pack;
pub mod reader;
pub mod sparse;
pub mod verify;
pub mod xattr;
pub mod zmap;
