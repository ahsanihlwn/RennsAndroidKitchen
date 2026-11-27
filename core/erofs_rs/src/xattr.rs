// SPDX-License-Identifier: MIT

//! EROFS extended attribute (xattr) decoding, encoding, long prefixes, SELinux, and capabilities.

use crate::error::{ErofsError, Result};
use crate::format::{
    XATTR_FILTER_SEED, XATTR_INDEX_LUSTRE, XATTR_INDEX_POSIX_ACL_ACCESS,
    XATTR_INDEX_POSIX_ACL_DEFAULT, XATTR_INDEX_SECURITY, XATTR_INDEX_TRUSTED, XATTR_INDEX_USER,
    XATTR_LONG_PREFIX_FLAG, XATTR_LONG_PREFIX_MASK, align_up_usize, read_u16_le, read_u32_le,
};
use serde::Serialize;
use xxhash_rust::xxh32::xxh32;

pub const XATTR_NAME_SELINUX: &str = "security.selinux";
pub const XATTR_NAME_CAPABILITY: &str = "security.capability";

// Linux VFS file capability revisions
const VFS_CAP_REVISION_MASK: u32 = 0xFF00_0000;
const VFS_CAP_REVISION_1: u32 = 0x0100_0000;
const VFS_CAP_REVISION_2: u32 = 0x0200_0000;
const VFS_CAP_REVISION_3: u32 = 0x0300_0000;
const VFS_CAP_FLAGS_EFFECTIVE: u32 = 0x0000_0001;

#[derive(Debug, Clone)]
pub struct LongXattrPrefix {
    pub base_index: u8,
    pub infix: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct XattrEntry {
    pub name: String,
    pub value: Vec<u8>,
    pub shared: bool,
}

impl XattrEntry {
    pub fn value_utf8_trimmed(&self) -> String {
        let end = self
            .value
            .iter()
            .position(|&b| b == 0)
            .unwrap_or(self.value.len());
        String::from_utf8_lossy(&self.value[..end]).into_owned()
    }
}

pub fn base_prefix_str(index: u8) -> Option<&'static str> {
    match index {
        XATTR_INDEX_USER => Some("user."),
        XATTR_INDEX_POSIX_ACL_ACCESS => Some("system.posix_acl_access"),
        XATTR_INDEX_POSIX_ACL_DEFAULT => Some("system.posix_acl_default"),
        XATTR_INDEX_TRUSTED => Some("trusted."),
        XATTR_INDEX_LUSTRE => Some("lustre."),
        XATTR_INDEX_SECURITY => Some("security."),
        _ => None,
    }
}

pub fn split_xattr_name(full_name: &str) -> Option<(u8, &str)> {
    const TABLE: &[(u8, &str)] = &[
        (XATTR_INDEX_USER, "user."),
        (XATTR_INDEX_POSIX_ACL_ACCESS, "system.posix_acl_access"),
        (XATTR_INDEX_POSIX_ACL_DEFAULT, "system.posix_acl_default"),
        (XATTR_INDEX_TRUSTED, "trusted."),
        (XATTR_INDEX_LUSTRE, "lustre."),
        (XATTR_INDEX_SECURITY, "security."),
    ];
    for &(idx, pfx) in TABLE {
        if let Some(rest) = full_name.strip_prefix(pfx) {
            return Some((idx, rest));
        }
    }
    None
}

pub fn resolve_xattr_name(
    name_index: u8,
    suffix: &[u8],
    long_prefixes: &[LongXattrPrefix],
) -> Option<String> {
    let suffix_str = String::from_utf8_lossy(suffix);
    if (name_index & XATTR_LONG_PREFIX_FLAG) != 0 {
        let idx = (name_index & XATTR_LONG_PREFIX_MASK) as usize;
        let lp = long_prefixes.get(idx)?;
        let base = base_prefix_str(lp.base_index)?;
        Some(format!("{base}{}{suffix_str}", lp.infix))
    } else {
        let base = base_prefix_str(name_index)?;
        Some(format!("{base}{suffix_str}"))
    }
}

/// Decode a single `erofs_xattr_entry` at `pos` inside `buf`.
/// Returns `(Option<(name_index, name_bytes, value_bytes)>, next_aligned_pos)`.
pub fn decode_raw_xattr_entry(buf: &[u8], pos: usize) -> Option<(u8, &[u8], &[u8], usize)> {
    if pos + 4 > buf.len() {
        return None;
    }
    let name_len = buf[pos] as usize;
    let name_index = buf[pos + 1];
    let value_size = read_u16_le(buf, pos + 2) as usize;
    if name_len == 0 && name_index == 0 && value_size == 0 {
        return None;
    }
    let name_start = pos + 4;
    let name_end = name_start + name_len;
    let val_end = name_end + value_size;
    if val_end > buf.len() {
        return None;
    }
    let next = align_up_usize(val_end, 4);
    Some((
        name_index,
        &buf[name_start..name_end],
        &buf[name_end..val_end],
        next,
    ))
}

/// Encode a single xattr entry (4-byte header + name + value, padded to 4-byte alignment).
pub fn encode_raw_xattr_entry(name_index: u8, name: &[u8], value: &[u8]) -> Result<Vec<u8>> {
    if name.len() > 255 {
        return Err(ErofsError::InvalidArg("xattr name exceeds 255 bytes".into()));
    }
    if value.len() > u16::MAX as usize {
        return Err(ErofsError::InvalidArg(
            "xattr value exceeds 65535 bytes".into(),
        ));
    }
    let raw_len = 4 + name.len() + value.len();
    let aligned = align_up_usize(raw_len, 4);
    let mut out = vec![0u8; aligned];
    out[0] = name.len() as u8;
    out[1] = name_index;
    out[2..4].copy_from_slice(&(value.len() as u16).to_le_bytes());
    out[4..4 + name.len()].copy_from_slice(name);
    out[4 + name.len()..4 + name.len() + value.len()].copy_from_slice(value);
    Ok(out)
}

/// Parse Linux `security.capability` binary xattr value into a 64-bit permitted capability bitmask.
pub fn parse_vfs_capability(val: &[u8]) -> Option<u64> {
    if val.len() < 12 {
        return None;
    }
    let magic_etc = read_u32_le(val, 0);
    match magic_etc & VFS_CAP_REVISION_MASK {
        VFS_CAP_REVISION_1 if val.len() == 12 => {
            let permitted = read_u32_le(val, 4) as u64;
            Some(permitted)
        }
        VFS_CAP_REVISION_2 if val.len() == 20 => {
            let lo = read_u32_le(val, 4) as u64;
            let hi = read_u32_le(val, 12) as u64;
            Some(lo | (hi << 32))
        }
        VFS_CAP_REVISION_3 if val.len() == 24 => {
            let lo = read_u32_le(val, 4) as u64;
            let hi = read_u32_le(val, 12) as u64;
            Some(lo | (hi << 32))
        }
        _ => None,
    }
}

/// Encode a 64-bit capability bitmask into Linux VFS capability v2 binary xattr format (20 bytes).
pub fn encode_vfs_capability_v2(caps: u64) -> [u8; 20] {
    let mut out = [0u8; 20];
    let magic_etc = VFS_CAP_REVISION_2 | VFS_CAP_FLAGS_EFFECTIVE;
    out[0..4].copy_from_slice(&magic_etc.to_le_bytes());
    out[4..8].copy_from_slice(&(caps as u32).to_le_bytes());
    // inheritable[0] = 0 at 8..12
    out[12..16].copy_from_slice(&((caps >> 32) as u32).to_le_bytes());
    // inheritable[1] = 0 at 16..20
    out
}

/// Compute EROFS xattr name filter bitmask contribution (0 bit = present, 1 bit = not present).
pub fn xattr_name_filter_bit(name_index: u8, name: &[u8]) -> u32 {
    let h = xxh32(name, XATTR_FILTER_SEED.wrapping_add(name_index as u32));
    1u32 << (h & 31)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn xattr_filter_bit_matches_spec() {
        let bit = xattr_name_filter_bit(XATTR_INDEX_SECURITY, b"selinux");
        assert_eq!(bit, 0x0000_0800);
        assert_eq!(!bit, 0xFFFF_F7FF);

        let cap_bit = xattr_name_filter_bit(XATTR_INDEX_SECURITY, b"capability");
        assert_ne!(cap_bit, 0);
        assert_eq!(
            cap_bit,
            xattr_name_filter_bit(XATTR_INDEX_SECURITY, b"capability")
        );
    }
}
