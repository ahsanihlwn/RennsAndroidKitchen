#![allow(dead_code)]

#[inline(always)]
pub fn brotli_write_bits(n_bits: usize, bits: u64, pos: &mut usize, array: &mut [u8]) {
    debug_assert!(n_bits <= 56);
    debug_assert!(n_bits == 64 || (bits >> n_bits) == 0);
    let byte_pos = *pos >> 3;
    let v = (array[byte_pos] as u64) | (bits << (*pos & 7));
    array[byte_pos..byte_pos + 8].copy_from_slice(&v.to_le_bytes());
    *pos += n_bits;
}

#[inline(always)]
pub fn brotli_write_bits_prepare_storage(pos: usize, array: &mut [u8]) {
    debug_assert!((pos & 7) == 0);
    array[pos >> 3] = 0;
}
