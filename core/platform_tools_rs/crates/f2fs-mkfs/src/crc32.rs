use crc32fast::Hasher;

/// Computes the raw (uninverted) IEEE 802.3 CRC-32 (`0xEDB88320`) used by F2FS on-disk structures.
///
/// Since standard IEEE CRC-32 (`crc32fast`) pre-inverts its initial state (`init ^ !0`) and
/// post-inverts its output (`state ^ !0`), passing `!seed` and inverting the result computes
/// the exact uninverted F2FS CRC-32 with SIMD acceleration.
#[inline]
pub fn f2fs_crc32_raw(seed: u32, data: &[u8]) -> u32 {
    let mut hasher = Hasher::new_with_initial(!seed);
    hasher.update(data);
    !hasher.finalize()
}

/// Computes `i_inode_checksum` for a 4096-byte F2FS inode block per the Linux F2FS specification:
/// - `s_chksum_seed = f2fs_crc32_raw(0xFFFF_FFFF, uuid)`
/// - `chksum = f2fs_crc32_raw(s_chksum_seed, &ino.to_le_bytes())`
/// - `chksum = f2fs_crc32_raw(chksum, &generation.to_le_bytes())`
/// - `chksum = f2fs_crc32_raw(chksum, &node_block[0..368])`
/// - `chksum = f2fs_crc32_raw(chksum, &[0u8; 4])`
/// - `chksum = f2fs_crc32_raw(chksum, &node_block[372..4096])`
pub fn f2fs_inode_checksum(
    uuid: &[u8; 16],
    ino: u32,
    generation: u32,
    node_block: &[u8; 4096],
) -> u32 {
    let s_seed = f2fs_crc32_raw(0xffff_ffff, uuid);
    let c1 = f2fs_crc32_raw(s_seed, &ino.to_le_bytes());
    let c2 = f2fs_crc32_raw(c1, &generation.to_le_bytes());
    let c3 = f2fs_crc32_raw(c2, &node_block[..368]);
    let c4 = f2fs_crc32_raw(c3, &[0u8; 4]);
    f2fs_crc32_raw(c4, &node_block[372..])
}

/// Computes the 32-bit F2FS directory entry hash code (`f2fs_dentry_hash`).
///
///Per the F2FS specification, `"."` and `".."` have hash `0`, while all other names use the
/// 16-round TEA (Tiny Encryption Algorithm) hash with the lowest 2 bits cleared (`& !3`).
pub fn f2fs_dentry_hash(name: &[u8]) -> u32 {
    if name == b"." || name == b".." {
        return 0;
    }

    let mut buf: [u32; 4] = [0x6745_2301, 0xefcd_ab89, 0x98ba_dcfe, 0x1032_5476];
    let total_len = name.len() as u32;
    let pad_byte = total_len & 0xff;
    let pad_word = pad_byte | (pad_byte << 8) | (pad_byte << 16) | (pad_byte << 24);

    for chunk in name.chunks(16) {
        let mut inp = [0u32; 4];
        let mut val = pad_word;
        for i in 0..16 {
            if i < chunk.len() {
                val = val.wrapping_shl(8).wrapping_add(u32::from(chunk[i]));
            }
            if i % 4 == 3 {
                inp[i / 4] = val;
                val = pad_word;
            }
        }

        let mut b0 = buf[0];
        let mut b1 = buf[1];
        let (a, b, c, d) = (inp[0], inp[1], inp[2], inp[3]);
        let mut sum = 0u32;
        for _ in 0..16 {
            sum = sum.wrapping_add(0x9e37_79b9);
            let t0 = b1
                .wrapping_shl(4)
                .wrapping_add(a)
                ^ b1.wrapping_add(sum)
                ^ (b1 >> 5).wrapping_add(b);
            b0 = b0.wrapping_add(t0);
            let t1 = b0
                .wrapping_shl(4)
                .wrapping_add(c)
                ^ b0.wrapping_add(sum)
                ^ (b0 >> 5).wrapping_add(d);
            b1 = b1.wrapping_add(t1);
        }
        buf[0] = buf[0].wrapping_add(b0);
        buf[1] = buf[1].wrapping_add(b1);
    }

    buf[0] & !3
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::spec::F2FS_SUPER_MAGIC;

    fn slow_f2fs_crc32(seed: u32, data: &[u8]) -> u32 {
        let mut crc = seed;
        for &b in data {
            crc ^= u32::from(b);
            for _ in 0..8 {
                if (crc & 1) != 0 {
                    crc = (crc >> 1) ^ 0xedb8_8320;
                } else {
                    crc >>= 1;
                }
            }
        }
        crc
    }

    #[test]
    fn test_f2fs_crc32_matches_bitwise_reference() {
        let data = b"Flash-Friendly File System v1.16.0 test payload";
        assert_eq!(
            f2fs_crc32_raw(F2FS_SUPER_MAGIC, data),
            slow_f2fs_crc32(F2FS_SUPER_MAGIC, data)
        );
        assert_eq!(
            f2fs_crc32_raw(0xffff_ffff, data),
            slow_f2fs_crc32(0xffff_ffff, data)
        );
    }

    #[test]
    fn test_f2fs_dentry_hash_vectors() {
        assert_eq!(f2fs_dentry_hash(b"."), 0);
        assert_eq!(f2fs_dentry_hash(b".."), 0);
        assert_eq!(f2fs_dentry_hash(b"lost+found"), 0x2dbf_9e80);
    }
}
