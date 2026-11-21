//! Clean-room MIT CRC-16 (`0xA001`) and CRC-32C (Castagnoli `0x82F63B78`) for Ext4 & JBD2.

/// Computes the reflected ANSI/IBM CRC-16 (polynomial `0x8005`, reflected `0xA001`)
/// used by `RO_COMPAT_GDT_CSUM` (`uninit_bg`) for block group descriptors.
pub fn crc16(seed: u16, data: &[u8]) -> u16 {
    let mut crc = seed;
    for &b in data {
        crc ^= u16::from(b);
        for _ in 0..8 {
            if (crc & 1) != 0 {
                crc = (crc >> 1) ^ 0xA001;
            } else {
                crc >>= 1;
            }
        }
    }
    crc
}

/// Computes raw (uninverted) CRC-32C (Castagnoli, reflected polynomial `0x82F63B78`)
/// matching the Linux kernel `crc32c_le(seed, data)` used by Ext4 `metadata_csum` and JBD2.
pub fn crc32c_raw(seed: u32, data: &[u8]) -> u32 {
    let mut crc = seed;
    for &b in data {
        crc ^= u32::from(b);
        for _ in 0..8 {
            if (crc & 1) != 0 {
                crc = (crc >> 1) ^ 0x82F6_3B78;
            } else {
                crc >>= 1;
            }
        }
    }
    crc
}

/// Computes the GDT (`bg_checksum`) CRC-16 for a 32-byte or 64-byte block group descriptor
/// when `RO_COMPAT_GDT_CSUM` (`uninit_bg`) is enabled.
pub fn gdt_crc16(uuid: &[u8; 16], group_nr: u32, desc: &[u8]) -> u16 {
    let mut crc = crc16(0xFFFF, uuid);
    crc = crc16(crc, &group_nr.to_le_bytes());
    crc = crc16(crc, &desc[..30]);
    if desc.len() > 32 {
        crc = crc16(crc, &desc[32..]);
    }
    crc
}

/// Computes the GDT (`bg_checksum`) low-16-bit CRC-32C for a block group descriptor
/// when `RO_COMPAT_METADATA_CSUM` is enabled.
pub fn gdt_crc32c(csum_seed: u32, group_nr: u32, desc: &[u8]) -> u16 {
    let mut crc = crc32c_raw(csum_seed, &group_nr.to_le_bytes());
    crc = crc32c_raw(crc, &desc[..30]);
    crc = crc32c_raw(crc, &[0, 0]);
    if desc.len() > 32 {
        crc = crc32c_raw(crc, &desc[32..]);
    }
    (crc & 0xFFFF) as u16
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_gdt_crc16_vector() {
        // Group 0 descriptor from dumpe2fs probe (`csum 0xf1e5`):
        // UUID = 11111111-2222-3333-4444-555555555555
        let uuid = [
            0x11, 0x11, 0x11, 0x11, 0x22, 0x22, 0x33, 0x33, 0x44, 0x44, 0x55, 0x55, 0x55, 0x55,
            0x55, 0x55,
        ];
        // Build Group 0 descriptor for 4K ext4_4k.img:
        // block_bitmap=2, inode_bitmap=3, inode_table=4, free_blocks=14839, free_inodes=16373,
        // used_dirs=2, bg_flags=0x0000, itable_unused=16373 -> csum 0xf1e5
        let mut desc = [0u8; 32];
        desc[0..4].copy_from_slice(&2u32.to_le_bytes());
        desc[4..8].copy_from_slice(&3u32.to_le_bytes());
        desc[8..12].copy_from_slice(&4u32.to_le_bytes());
        desc[12..14].copy_from_slice(&14839u16.to_le_bytes());
        desc[14..16].copy_from_slice(&16373u16.to_le_bytes());
        desc[16..18].copy_from_slice(&2u16.to_le_bytes());
        desc[28..30].copy_from_slice(&16373u16.to_le_bytes());
        assert_eq!(gdt_crc16(&uuid, 0, &desc), 0xf1e5);

        // And with 256-byte inodes (free_blocks=14327 = 0x37f7) -> csum 0x9281
        desc[12..14].copy_from_slice(&14327u16.to_le_bytes());
        assert_eq!(gdt_crc16(&uuid, 0, &desc), 0x9281);
    }
}
