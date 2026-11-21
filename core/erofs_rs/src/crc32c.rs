// SPDX-License-Identifier: MIT

//! Pure-Rust CRC32C (Castagnoli) implementation matching EROFS superblock checksum semantics.

const CRC32C_POLY: u32 = 0x82F6_3B78;

const fn build_table() -> [u32; 256] {
    let mut table = [0u32; 256];
    let mut i = 0usize;
    while i < 256 {
        let mut crc = i as u32;
        let mut bit = 0;
        while bit < 8 {
            if (crc & 1) != 0 {
                crc = (crc >> 1) ^ CRC32C_POLY;
            } else {
                crc >>= 1;
            }
            bit += 1;
        }
        table[i] = crc;
        i += 1;
    }
    table
}

static CRC32C_TABLE: [u32; 256] = build_table();

/// Compute raw CRC32C (without final inversion, matching EROFS on-disk superblock CRC).
pub fn crc32c_raw(mut crc: u32, data: &[u8]) -> u32 {
    for &b in data {
        crc = (crc >> 8) ^ CRC32C_TABLE[((crc ^ b as u32) & 0xFF) as usize];
    }
    crc
}

/// Compute EROFS superblock CRC32C over the superblock region (with the checksum field zeroed).
pub fn erofs_superblock_crc(sb_region: &[u8]) -> u32 {
    let mut buf = sb_region.to_vec();
    if buf.len() >= 8 {
        buf[4..8].fill(0);
    }
    crc32c_raw(0xFFFF_FFFF, &buf)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_vector() {
        // Standard Castagnoli check value for "123456789" with final XOR is 0xE3069283.
        let raw = crc32c_raw(0xFFFF_FFFF, b"123456789");
        assert_eq!(raw ^ 0xFFFF_FFFF, 0xE306_9283);
    }
}
