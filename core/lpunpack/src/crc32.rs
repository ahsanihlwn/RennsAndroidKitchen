//! IEEE 802.3 CRC-32 calculation for Android sparse images.

const fn build_crc32_table() -> [u32; 256] {
    let mut table = [0u32; 256];
    let mut i = 0;
    while i < 256 {
        let mut c = i as u32;
        let mut k = 0;
        while k < 8 {
            if c & 1 != 0 {
                c = 0xedb8_8320 ^ (c >> 1);
            } else {
                c >>= 1;
            }
            k += 1;
        }
        table[i] = c;
        i += 1;
    }
    table
}

const CRC32_TABLE: [u32; 256] = build_crc32_table();

/// Update a running CRC-32 checksum with `data`. Pass `0` as initial `crc_in`.
pub fn sparse_crc32(crc_in: u32, data: &[u8]) -> u32 {
    let mut crc = !crc_in;
    for &b in data {
        let idx = ((crc ^ b as u32) & 0xff) as usize;
        crc = CRC32_TABLE[idx] ^ (crc >> 8);
    }
    !crc
}

#[cfg(test)]
mod tests {
    use super::sparse_crc32;

    #[test]
    fn test_crc32_check_value() {
        assert_eq!(sparse_crc32(0, b"123456789"), 0xcbf4_3926);
        let half1 = sparse_crc32(0, b"1234");
        let combined = sparse_crc32(half1, b"56789");
        assert_eq!(combined, 0xcbf4_3926);
    }
}
