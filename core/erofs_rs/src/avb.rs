// SPDX-License-Identifier: MIT

//! Android Verified Boot (AVB) footer detection, inspection, and stripping.

use crate::error::{ErofsError, Result};
use crate::format::SuperBlock;
use serde::Serialize;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom, Write};

pub const AVB_FOOTER_MAGIC: &[u8; 4] = b"AVBf";
pub const AVB_FOOTER_SIZE: u64 = 64;

/// Parsed 64-byte AVB footer located at the end of a signed Android partition image.
#[derive(Debug, Clone, Serialize)]
pub struct AvbFooter {
    pub version_major: u32,
    pub version_minor: u32,
    pub original_image_size: u64,
    pub vbmeta_offset: u64,
    pub vbmeta_size: u64,
    pub total_file_size: u64,
}

/// Inspect an open seekable stream for a valid AVB footer at EOF.
pub fn read_avb_footer<R: Read + Seek>(r: &mut R) -> Result<Option<AvbFooter>> {
    let saved = r.stream_position()?;
    let end = r.seek(SeekFrom::End(0))?;
    if end < AVB_FOOTER_SIZE {
        r.seek(SeekFrom::Start(saved))?;
        return Ok(None);
    }
    r.seek(SeekFrom::End(-(AVB_FOOTER_SIZE as i64)))?;
    let mut buf = [0u8; 64];
    let res = r.read_exact(&mut buf);
    r.seek(SeekFrom::Start(saved))?;
    if res.is_err() {
        return Ok(None);
    }
    if &buf[0..4] != AVB_FOOTER_MAGIC {
        return Ok(None);
    }
    let version_major = u32::from_be_bytes([buf[4], buf[5], buf[6], buf[7]]);
    let version_minor = u32::from_be_bytes([buf[8], buf[9], buf[10], buf[11]]);
    let original_image_size = u64::from_be_bytes([
        buf[12], buf[13], buf[14], buf[15], buf[16], buf[17], buf[18], buf[19],
    ]);
    let vbmeta_offset = u64::from_be_bytes([
        buf[20], buf[21], buf[22], buf[23], buf[24], buf[25], buf[26], buf[27],
    ]);
    let vbmeta_size = u64::from_be_bytes([
        buf[28], buf[29], buf[30], buf[31], buf[32], buf[33], buf[34], buf[35],
    ]);

    let payload_limit = end - AVB_FOOTER_SIZE;
    if original_image_size == 0
        || original_image_size > vbmeta_offset
        || vbmeta_offset > payload_limit
        || vbmeta_size == 0
        || vbmeta_size > payload_limit - vbmeta_offset
    {
        return Ok(None);
    }

    Ok(Some(AvbFooter {
        version_major,
        version_minor,
        original_image_size,
        vbmeta_offset,
        vbmeta_size,
        total_file_size: end,
    }))
}

/// Strip the AVB footer and trailing vbmeta/hashtree metadata from an EROFS image,
/// truncating back to the verified `original_image_size`.
pub fn strip_avb_footer(f: &mut File, sb: &SuperBlock) -> Result<AvbFooter> {
    let info = read_avb_footer(f)?
        .ok_or_else(|| ErofsError::Edit("no valid AVB footer found to strip".into()))?;
    let bs = sb.block_size as u64;
    let expected = sb.blocks * bs;

    if info.original_image_size % bs != 0 {
        return Err(ErofsError::Edit(format!(
            "AVB original_image_size ({}) is not a multiple of block_size ({})",
            info.original_image_size, bs
        )));
    }
    if info.original_image_size != expected {
        return Err(ErofsError::Edit(format!(
            "AVB original_image_size ({}) != superblock size ({expected}); image may be corrupted or modified",
            info.original_image_size
        )));
    }

    f.set_len(info.original_image_size)?;
    f.flush()?;
    Ok(info)
}
