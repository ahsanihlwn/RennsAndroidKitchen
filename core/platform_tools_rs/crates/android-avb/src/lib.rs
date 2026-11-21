//! Android Verified Boot 2.0 (`AVB0` `vbmeta`) header parsing and flag patching (MIT).
//!
//! Used by `fastboot --disable-verity` and `fastboot --disable-verification` when flashing
//! `vbmeta` partitions.

use thiserror::Error;

/// `AVB0` magic bytes at offset `0..4` of `AvbVBMetaImageHeader`.
pub const AVB_MAGIC: &[u8; 4] = b"AVB0";
/// Minimum byte size of `AvbVBMetaImageHeader`.
pub const AVB_VBMETA_IMAGE_HEADER_SIZE: usize = 256;
/// Byte offset of the 32-bit big-endian `flags` field inside `AvbVBMetaImageHeader`.
pub const AVB_FLAGS_OFFSET: usize = 120;

/// Flag in `AvbVBMetaImageHeader.flags` to disable dm-verity (`--disable-verity`).
pub const AVB_VBMETA_IMAGE_FLAGS_HASHTREE_DISABLED: u32 = 1 << 0;
/// Flag in `AvbVBMetaImageHeader.flags` to disable verification (`--disable-verification`).
pub const AVB_VBMETA_IMAGE_FLAGS_VERIFICATION_DISABLED: u32 = 1 << 1;

/// Errors when parsing or patching a `vbmeta` image.
#[derive(Debug, Error, PartialEq, Eq)]
pub enum AvbError {
    #[error("vbmeta image is too small ({0} bytes, need at least 256)")]
    TooSmall(usize),
    #[error("invalid vbmeta magic (expected 'AVB0')")]
    InvalidMagic,
}

/// Parsed subset of `AvbVBMetaImageHeader` (all integer fields stored big-endian on disk).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VbmetaHeader {
    pub required_libavb_version_major: u32,
    pub required_libavb_version_minor: u32,
    pub authentication_data_block_size: u64,
    pub auxiliary_data_block_size: u64,
    pub algorithm_type: u32,
    pub flags: u32,
    pub rollback_index: u64,
    pub release_string: String,
}

impl VbmetaHeader {
    /// Parses an `AvbVBMetaImageHeader` from the first 256 bytes of `data`.
    pub fn parse(data: &[u8]) -> Result<Self, AvbError> {
        if data.len() < AVB_VBMETA_IMAGE_HEADER_SIZE {
            return Err(AvbError::TooSmall(data.len()));
        }
        if &data[0..4] != AVB_MAGIC {
            return Err(AvbError::InvalidMagic);
        }
        let major = u32::from_be_bytes(data[4..8].try_into().unwrap());
        let minor = u32::from_be_bytes(data[8..12].try_into().unwrap());
        let auth_sz = u64::from_be_bytes(data[12..20].try_into().unwrap());
        let aux_sz = u64::from_be_bytes(data[20..28].try_into().unwrap());
        let algo = u32::from_be_bytes(data[28..32].try_into().unwrap());
        let flags = u32::from_be_bytes(
            data[AVB_FLAGS_OFFSET..AVB_FLAGS_OFFSET + 4]
                .try_into()
                .unwrap(),
        );
        let rollback = u64::from_be_bytes(data[124..132].try_into().unwrap());
        let rel_raw = &data[132..180];
        let rel_end = rel_raw.iter().position(|&b| b == 0).unwrap_or(rel_raw.len());
        let release_string = String::from_utf8_lossy(&rel_raw[..rel_end]).into_owned();

        Ok(Self {
            required_libavb_version_major: major,
            required_libavb_version_minor: minor,
            authentication_data_block_size: auth_sz,
            auxiliary_data_block_size: aux_sz,
            algorithm_type: algo,
            flags,
            rollback_index: rollback,
            release_string,
        })
    }
}

/// Returns `true` if `partition_name` is a `vbmeta` partition (`vbmeta`, `vbmeta_a`,
/// `vbmeta_system_b`, etc.).
pub fn is_vbmeta_partition(partition_name: &str) -> bool {
    partition_name == "vbmeta" || partition_name.starts_with("vbmeta_")
}

/// Patches the `flags` field at offset `120..124` of a `vbmeta` image in-place when
/// `disable_verity` and/or `disable_verification` are requested.
pub fn patch_vbmeta_flags(
    data: &mut [u8],
    disable_verity: bool,
    disable_verification: bool,
) -> Result<u32, AvbError> {
    let hdr = VbmetaHeader::parse(data)?;
    let mut flags = hdr.flags;
    if disable_verity {
        flags |= AVB_VBMETA_IMAGE_FLAGS_HASHTREE_DISABLED;
    }
    if disable_verification {
        flags |= AVB_VBMETA_IMAGE_FLAGS_VERIFICATION_DISABLED;
    }
    data[AVB_FLAGS_OFFSET..AVB_FLAGS_OFFSET + 4].copy_from_slice(&flags.to_be_bytes());
    Ok(flags)
}

/// Creates a minimal valid 256-byte `AVB0` `vbmeta` image for testing or blank flashing.
pub fn build_minimal_vbmeta(flags: u32) -> [u8; AVB_VBMETA_IMAGE_HEADER_SIZE] {
    let mut buf = [0u8; AVB_VBMETA_IMAGE_HEADER_SIZE];
    buf[0..4].copy_from_slice(AVB_MAGIC);
    buf[4..8].copy_from_slice(&1u32.to_be_bytes()); // major = 1
    buf[8..12].copy_from_slice(&2u32.to_be_bytes()); // minor = 2
    buf[AVB_FLAGS_OFFSET..AVB_FLAGS_OFFSET + 4].copy_from_slice(&flags.to_be_bytes());
    let rel = b"avbtool 1.2.0";
    buf[132..132 + rel.len()].copy_from_slice(rel);
    buf
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_vbmeta_parse_and_patch() {
        let mut img = build_minimal_vbmeta(0);
        let hdr = VbmetaHeader::parse(&img).unwrap();
        assert_eq!(hdr.required_libavb_version_major, 1);
        assert_eq!(hdr.required_libavb_version_minor, 2);
        assert_eq!(hdr.flags, 0);
        assert_eq!(hdr.release_string, "avbtool 1.2.0");

        let new_flags = patch_vbmeta_flags(&mut img, true, true).unwrap();
        assert_eq!(
            new_flags,
            AVB_VBMETA_IMAGE_FLAGS_HASHTREE_DISABLED
                | AVB_VBMETA_IMAGE_FLAGS_VERIFICATION_DISABLED
        );
        let hdr2 = VbmetaHeader::parse(&img).unwrap();
        assert_eq!(hdr2.flags, 3);
        assert!(is_vbmeta_partition("vbmeta"));
        assert!(is_vbmeta_partition("vbmeta_a"));
        assert!(is_vbmeta_partition("vbmeta_system_b"));
        assert!(!is_vbmeta_partition("boot_a"));
    }
}
