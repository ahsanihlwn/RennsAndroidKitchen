#![allow(dead_code)]

#[inline(always)]
pub fn find_match_length_with_limit(s1: &[u8], s2: &[u8], mut limit: usize) -> usize {
    debug_assert!(s1.len() >= limit && s2.len() >= limit);
    let mut matched = 0usize;
    while limit >= 8 {
        let a = u64::from_le_bytes(s1[matched..matched + 8].try_into().unwrap());
        let b = u64::from_le_bytes(s2[matched..matched + 8].try_into().unwrap());
        let x = a ^ b;
        if x != 0 {
            let matching_bits = x.trailing_zeros() as usize;
            return matched + (matching_bits >> 3);
        }
        matched += 8;
        limit -= 8;
    }
    while limit > 0 && s1[matched] == s2[matched] {
        matched += 1;
        limit -= 1;
    }
    matched
}
