use crc32fast::Hasher;

const CHUNK_BUF_LEN: usize = 4096;

/// Computes the IEEE CRC-32 checksum matching AOSP `sparse_crc32`.
pub fn sparse_crc32(crc_in: u32, data: &[u8]) -> u32 {
    let mut hasher = Hasher::new_with_initial(crc_in);
    hasher.update(data);
    hasher.finalize()
}

/// Updates `crc_in` as if `byte_len` bytes of repeated 32-bit little-endian `fill_val` were hashed.
pub fn sparse_crc32_fill(crc_in: u32, fill_val: u32, mut byte_len: u64) -> u32 {
    if byte_len == 0 {
        return crc_in;
    }
    let pattern = fill_val.to_le_bytes();
    let mut buf = [0u8; CHUNK_BUF_LEN];
    for chunk in buf.chunks_exact_mut(4) {
        chunk.copy_from_slice(&pattern);
    }

    let mut hasher = Hasher::new_with_initial(crc_in);
    while byte_len >= CHUNK_BUF_LEN as u64 {
        hasher.update(&buf);
        byte_len -= CHUNK_BUF_LEN as u64;
    }
    if byte_len > 0 {
        hasher.update(&buf[..byte_len as usize]);
    }
    hasher.finalize()
}

/// Updates `crc_in` as if `byte_len` zero bytes were hashed.
pub fn sparse_crc32_zeros(crc_in: u32, mut byte_len: u64) -> u32 {
    if byte_len == 0 {
        return crc_in;
    }
    let buf = [0u8; CHUNK_BUF_LEN];
    let mut hasher = Hasher::new_with_initial(crc_in);
    while byte_len >= CHUNK_BUF_LEN as u64 {
        hasher.update(&buf);
        byte_len -= CHUNK_BUF_LEN as u64;
    }
    if byte_len > 0 {
        hasher.update(&buf[..byte_len as usize]);
    }
    hasher.finalize()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_sparse_crc32_vectors() {
        assert_eq!(sparse_crc32(0, b"123456789"), 0xcbf4_3926);
        let part1 = sparse_crc32(0, b"1234");
        let part2 = sparse_crc32(part1, b"56789");
        assert_eq!(part2, 0xcbf4_3926);
    }

    #[test]
    fn test_sparse_crc32_fill_and_zeros() {
        let pattern = 0xdead_beef_u32;
        let expected_buf: Vec<u8> = pattern
            .to_le_bytes()
            .into_iter()
            .cycle()
            .take(8196)
            .collect();
        assert_eq!(
            sparse_crc32_fill(0, pattern, 8196),
            sparse_crc32(0, &expected_buf)
        );

        let zeros = vec![0u8; 10_000];
        assert_eq!(sparse_crc32_zeros(0, 10_000), sparse_crc32(0, &zeros));
    }
}
