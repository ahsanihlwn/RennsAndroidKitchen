#![allow(dead_code)]

use crate::common::constants::*;
use crate::enc::compound_dictionary::{
    CompoundDictionary, K_PREPARED_DICTIONARY_HASH_MUL64_LONG, PreparedDictionary,
    PreparedDictionaryView,
};
use crate::enc::encoder_dict::BrotliEncoderDictionary;
use crate::enc::fast_log::log2_floor_non_zero;
use crate::enc::find_match_length::find_match_length_with_limit;
use crate::enc::metablock::Base64Region;
use crate::enc::params::{BrotliEncoderParams, BrotliHasherParams};
use crate::enc::quality::{HQ_ZOPFLIFICATION_QUALITY, choose_hasher};
use crate::enc::static_dict::{
    BROTLI_MAX_STATIC_DICTIONARY_MATCH_LEN, K_HASH_MUL_32, K_HASH_MUL_64, K_INVALID_MATCH,
    brotli_find_all_static_dictionary_matches, hash14,
};

pub const MAX_NUM_MATCHES_H10: usize = 128;

pub const K_CUTOFF_TRANSFORMS_COUNT: u32 = 10;
pub const K_CUTOFF_TRANSFORMS: u64 = (0x071B520Au64 << 32) | 0xDA2D3200u64;

pub const BROTLI_LITERAL_BYTE_SCORE: usize = 135;
pub const BROTLI_DISTANCE_BIT_PENALTY: usize = 30;
pub const BROTLI_SCORE_BASE: usize = BROTLI_DISTANCE_BIT_PENALTY * 8 * std::mem::size_of::<usize>();

#[inline(always)]
pub fn backward_reference_score(copy_length: usize, backward_reference_offset: usize) -> usize {
    BROTLI_SCORE_BASE + BROTLI_LITERAL_BYTE_SCORE * copy_length
        - BROTLI_DISTANCE_BIT_PENALTY * (log2_floor_non_zero(backward_reference_offset) as usize)
}

#[inline(always)]
pub fn minimum_better_length(score: usize) -> usize {
    (score - (BROTLI_SCORE_BASE + 15)) / BROTLI_LITERAL_BYTE_SCORE
}

#[inline(always)]
pub fn backward_reference_score_using_last_distance(copy_length: usize) -> usize {
    BROTLI_LITERAL_BYTE_SCORE * copy_length + BROTLI_SCORE_BASE + 15
}

#[inline(always)]
pub fn backward_reference_penalty_using_last_distance(distance_short_code: usize) -> usize {
    39 + ((0x1CA10 >> (distance_short_code & 0xE)) & 0xE)
}

#[inline(always)]
pub fn read_u32_le(data: &[u8], pos: usize) -> u32 {
    u32::from_le_bytes(data[pos..pos + 4].try_into().unwrap())
}

#[inline(always)]
pub fn read_u16_le(data: &[u8], pos: usize) -> u16 {
    u16::from_le_bytes(data[pos..pos + 2].try_into().unwrap())
}

#[inline(always)]
pub fn read_u64_le(data: &[u8], pos: usize) -> u64 {
    u64::from_le_bytes(data[pos..pos + 8].try_into().unwrap())
}

#[derive(Debug, Clone, Copy, Default)]
pub struct HasherSearchResult {
    pub len: usize,
    pub distance: usize,
    pub score: usize,
    pub len_code_delta: i32,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct BackwardMatch {
    pub distance: u32,
    pub length_and_code: u32,
}

impl BackwardMatch {
    #[inline(always)]
    pub fn new(dist: usize, len: usize) -> Self {
        Self {
            distance: dist as u32,
            length_and_code: (len << 5) as u32,
        }
    }

    #[inline(always)]
    pub fn new_dict(dist: usize, len: usize, len_code: usize) -> Self {
        Self {
            distance: dist as u32,
            length_and_code: ((len << 5) | (if len == len_code { 0 } else { len_code })) as u32,
        }
    }

    #[inline(always)]
    pub fn length(&self) -> usize {
        (self.length_and_code >> 5) as usize
    }

    #[inline(always)]
    pub fn length_code(&self) -> usize {
        let code = (self.length_and_code & 31) as usize;
        if code != 0 { code } else { self.length() }
    }
}

#[derive(Debug, Clone, Default)]
pub struct HasherCommon {
    pub is_setup: bool,
    pub dict_num_lookups: usize,
    pub dict_num_matches: usize,
    pub params: BrotliHasherParams,
    pub is_prepared: bool,
    pub base64_regions: Vec<Base64Region>,
}

#[inline(always)]
pub fn prepare_distance_cache(distance_cache: &mut [i32], num_distances: usize) {
    if num_distances > 4 {
        let last_distance = distance_cache[0];
        distance_cache[4] = last_distance - 1;
        distance_cache[5] = last_distance + 1;
        distance_cache[6] = last_distance - 2;
        distance_cache[7] = last_distance + 2;
        distance_cache[8] = last_distance - 3;
        distance_cache[9] = last_distance + 3;
        if num_distances > 10 {
            let next_last_distance = distance_cache[1];
            distance_cache[10] = next_last_distance - 1;
            distance_cache[11] = next_last_distance + 1;
            distance_cache[12] = next_last_distance - 2;
            distance_cache[13] = next_last_distance + 2;
            distance_cache[14] = next_last_distance - 3;
            distance_cache[15] = next_last_distance + 3;
        }
    }
}

#[inline(always)]
fn test_static_dictionary_item(
    dictionary: &BrotliEncoderDictionary,
    len: usize,
    word_idx: usize,
    data: &[u8],
    max_length: usize,
    max_backward: usize,
    max_distance: usize,
    out: &mut HasherSearchResult,
) -> bool {
    let offset = dictionary.words.offsets_by_length[len] as usize + len * word_idx;
    if len > max_length {
        return false;
    }
    let matchlen =
        find_match_length_with_limit(data, &dictionary.words.data[offset..], len);
    if matchlen + (dictionary.cutoff_transforms_count as usize) <= len || matchlen == 0 {
        return false;
    }
    let cut = len - matchlen;
    let transform_id =
        (cut << 2) + (((dictionary.cutoff_transforms >> (cut * 6)) & 0x3F) as usize);
    let backward =
        max_backward + 1 + word_idx + (transform_id << dictionary.words.size_bits_by_length[len]);
    if backward > max_distance {
        return false;
    }
    let score = backward_reference_score(matchlen, backward);
    if score < out.score {
        return false;
    }
    out.len = matchlen;
    out.len_code_delta = (len as i32) - (matchlen as i32);
    out.distance = backward;
    out.score = score;
    true
}

#[inline(always)]
pub fn search_in_static_dictionary(
    dictionary: &BrotliEncoderDictionary,
    common: &mut HasherCommon,
    data: &[u8],
    max_length: usize,
    max_backward: usize,
    max_distance: usize,
    out: &mut HasherSearchResult,
    shallow: bool,
) {
    if common.dict_num_matches < (common.dict_num_lookups >> 7) {
        return;
    }
    let mut key = (hash14(data) as usize) << 1;
    let limit = if shallow { 1 } else { 2 };
    for _ in 0..limit {
        common.dict_num_lookups += 1;
        let len = dictionary.hash_table_lengths[key] as usize;
        if len != 0 {
            let item_matches = test_static_dictionary_item(
                dictionary,
                len,
                dictionary.hash_table_words[key] as usize,
                data,
                max_length,
                max_backward,
                max_distance,
                out,
            );
            if item_matches {
                common.dict_num_matches += 1;
            }
        }
        key += 1;
    }
}

#[inline(always)]
fn get_matching_tag_mask(chunk_count: usize, tag: u8, tag_bucket: &[u8], head: usize) -> u64 {
    #[cfg(all(target_arch = "x86_64", target_feature = "sse2"))]
    let matches: u64 = unsafe {
        use core::arch::x86_64::*;
        let mut m = 0u64;
        let comparison_mask = _mm_set1_epi8(tag as i8);
        let limit = chunk_count.min(4);
        for i in 0..limit {
            let chunk = _mm_loadu_si128(tag_bucket.as_ptr().add(16 * i) as *const __m128i);
            let equal_mask = _mm_cmpeq_epi8(chunk, comparison_mask);
            m |= (_mm_movemask_epi8(equal_mask) as u32 as u64) << (16 * i);
        }
        m
    };

    #[cfg(not(all(target_arch = "x86_64", target_feature = "sse2")))]
    let matches: u64 = {
        let mut m = 0u64;
        const CHUNK_SIZE: usize = std::mem::size_of::<usize>();
        const SHIFT_AMOUNT: usize = (CHUNK_SIZE * 8) - CHUNK_SIZE;
        let xff = !0usize;
        let x01 = xff / 0xFF;
        let x80 = x01 << 7;
        let splat_char = (tag as usize).wrapping_mul(x01);
        let extract_magic = (xff / 0x7F) >> CHUNK_SIZE;
        let mut i: isize = ((chunk_count * 16) - CHUNK_SIZE) as isize;
        while i >= 0 {
            let mut chunk =
                usize::from_le_bytes(tag_bucket[i as usize..i as usize + CHUNK_SIZE].try_into().unwrap());
            chunk ^= splat_char;
            chunk = (((chunk | x80).wrapping_sub(x01)) | chunk) & x80;
            m <<= CHUNK_SIZE;
            m |= (chunk.wrapping_mul(extract_magic) >> SHIFT_AMOUNT) as u64;
            i -= CHUNK_SIZE as isize;
        }
        !m
    };

    if chunk_count == 1 {
        (matches as u16).rotate_right(head as u32) as u64
    } else if chunk_count == 2 {
        (matches as u32).rotate_right(head as u32) as u64
    } else {
        matches.rotate_right(head as u32)
    }
}

// =========================================================================
// Compound dictionary matchers
// =========================================================================

fn find_compound_dictionary_match(
    dict: &PreparedDictionary,
    data: &[u8],
    ring_buffer_mask: usize,
    distance_cache: &[i32],
    cur_ix: usize,
    max_length: usize,
    distance_offset: usize,
    max_distance: usize,
    out: &mut HasherSearchResult,
) {
    let source_size = dict.source_size as usize;
    let boundary = distance_offset - source_size;
    let hash_shift = 64u32 - dict.bucket_bits;
    let slot_mask = u32::MAX >> (32 - dict.slot_bits);
    let hash_mask = u64::MAX >> (64 - dict.hash_bits);
    let cur_ix_masked = cur_ix & ring_buffer_mask;
    let mut best_score = out.score;
    let mut best_len = out.len;

    let h = (read_u64_le(data, cur_ix_masked) & hash_mask)
        .wrapping_mul(K_PREPARED_DICTIONARY_HASH_MUL64_LONG);
    let key = (h >> hash_shift) as usize;
    let slot = (key as u32 & slot_mask) as usize;
    let head = dict.heads[key];
    let mut chain_idx = dict.slot_offsets[slot] as usize + (head as usize);
    let mut item: u32 = if head == 0xFFFF { 1 } else { 0 };

    for i in 0..4 {
        let distance = distance_cache[i] as usize;
        if distance <= boundary || distance > distance_offset {
            continue;
        }
        let offset = distance_offset - distance;
        let limit = (source_size - offset).min(max_length);
        let len = find_match_length_with_limit(&dict.source[offset..], &data[cur_ix_masked..], limit);
        if len >= 2 {
            let mut score = backward_reference_score_using_last_distance(len);
            if best_score < score {
                if i != 0 {
                    score -= backward_reference_penalty_using_last_distance(i);
                }
                if best_score < score {
                    best_score = score;
                    if len > best_len {
                        best_len = len;
                    }
                    out.len = len;
                    out.len_code_delta = 0;
                    out.distance = distance;
                    out.score = best_score;
                }
            }
        }
    }

    if best_len < 3 {
        best_len = 3;
    }

    while item == 0 {
        let raw = dict.items[chain_idx];
        chain_idx += 1;
        let offset = (raw & 0x7FFFFFFF) as usize;
        item = raw & 0x80000000;
        let distance = distance_offset - offset;
        let limit = (source_size - offset).min(max_length);
        if distance > max_distance {
            continue;
        }
        if cur_ix_masked + best_len > ring_buffer_mask
            || best_len >= limit
            || read_u32_le(data, cur_ix_masked + best_len - 3)
                != read_u32_le(&dict.source, offset + best_len - 3)
        {
            continue;
        }
        let len = find_match_length_with_limit(&dict.source[offset..], &data[cur_ix_masked..], limit);
        if len >= 4 {
            let score = backward_reference_score(len, distance);
            if best_score < score {
                best_score = score;
                best_len = len;
                out.len = best_len;
                out.len_code_delta = 0;
                out.distance = distance;
                out.score = best_score;
            }
        }
    }
}

pub fn lookup_compound_dictionary_match(
    addon: &CompoundDictionary,
    data: &[u8],
    ring_buffer_mask: usize,
    distance_cache: &[i32],
    cur_ix: usize,
    max_length: usize,
    max_ring_buffer_distance: usize,
    max_distance: usize,
    sr: &mut HasherSearchResult,
) {
    let base_offset = max_ring_buffer_distance + 1 + addon.total_size - 1;
    for d in 0..addon.num_chunks {
        find_compound_dictionary_match(
            &addon.chunks[d],
            data,
            ring_buffer_mask,
            distance_cache,
            cur_ix,
            max_length,
            base_offset - addon.chunk_offsets[d],
            max_distance,
            sr,
        );
    }
}

fn find_all_compound_dictionary_matches(
    dict: &PreparedDictionary,
    data: &[u8],
    ring_buffer_mask: usize,
    cur_ix: usize,
    min_length: usize,
    max_length: usize,
    distance_offset: usize,
    max_distance: usize,
    matches: &mut [BackwardMatch],
) -> usize {
    let source_size = dict.source_size as usize;
    let hash_shift = 64u32 - dict.bucket_bits;
    let slot_mask = u32::MAX >> (32 - dict.slot_bits);
    let hash_mask = u64::MAX >> (64 - dict.hash_bits);
    let cur_ix_masked = cur_ix & ring_buffer_mask;
    let mut best_len = min_length;

    let h = (read_u64_le(data, cur_ix_masked) & hash_mask)
        .wrapping_mul(K_PREPARED_DICTIONARY_HASH_MUL64_LONG);
    let key = (h >> hash_shift) as usize;
    let slot = (key as u32 & slot_mask) as usize;
    let head = dict.heads[key];
    let mut chain_idx = dict.slot_offsets[slot] as usize + (head as usize);
    let mut item: u32 = if head == 0xFFFF { 1 } else { 0 };
    let mut found = 0usize;
    let match_limit = matches.len();

    while item == 0 {
        let raw = dict.items[chain_idx];
        chain_idx += 1;
        let offset = (raw & 0x7FFFFFFF) as usize;
        item = raw & 0x80000000;
        let distance = distance_offset - offset;
        let limit = (source_size - offset).min(max_length);
        if distance > max_distance {
            continue;
        }
        if cur_ix_masked + best_len > ring_buffer_mask
            || best_len >= limit
            || data[cur_ix_masked + best_len] != dict.source[offset + best_len]
        {
            continue;
        }
        let len = find_match_length_with_limit(&dict.source[offset..], &data[cur_ix_masked..], limit);
        if len > best_len {
            best_len = len;
            matches[found] = BackwardMatch::new(distance, len);
            found += 1;
            if found == match_limit {
                break;
            }
        }
    }
    found
}

pub fn lookup_all_compound_dictionary_matches(
    addon: &CompoundDictionary,
    data: &[u8],
    ring_buffer_mask: usize,
    cur_ix: usize,
    mut min_length: usize,
    max_length: usize,
    max_ring_buffer_distance: usize,
    max_distance: usize,
    matches: &mut [BackwardMatch],
) -> usize {
    let base_offset = max_ring_buffer_distance + 1 + addon.total_size - 1;
    let mut total_found = 0usize;
    let match_limit = matches.len();
    for d in 0..addon.num_chunks {
        let n = find_all_compound_dictionary_matches(
            &addon.chunks[d],
            data,
            ring_buffer_mask,
            cur_ix,
            min_length,
            max_length,
            base_offset - addon.chunk_offsets[d],
            max_distance,
            &mut matches[total_found..],
        );
        total_found += n;
        if total_found == match_limit {
            break;
        }
        if total_found > 0 {
            min_length = matches[total_found - 1].length();
        }
    }
    total_found
}

#[derive(Debug, Clone, Copy, Default)]
pub struct PreparedDictionaryProbe {
    pub chain_idx: usize,
    pub item: u32,
}

pub fn prefetch_compound_dictionary_match_opt(
    addon: &CompoundDictionary,
    data: &[u8],
    ring_buffer_mask: usize,
    cur_ix: usize,
    probes: &mut [PreparedDictionaryProbe],
) {
    let cur_ix_masked = cur_ix & ring_buffer_mask;
    let bytes = read_u64_le(data, cur_ix_masked);
    for d in 0..addon.num_chunks {
        let view: &PreparedDictionaryView = &addon.chunk_views[d];
        let dict = &addon.chunks[d];
        let h = (bytes & view.hash_mask).wrapping_mul(K_PREPARED_DICTIONARY_HASH_MUL64_LONG);
        let key = (h >> view.hash_shift) as usize;
        let slot = (key as u32 & view.slot_mask) as usize;
        let head = dict.heads[key];
        probes[d].chain_idx = dict.slot_offsets[slot] as usize + (head as usize);
        probes[d].item = if head == 0xFFFF { 1 } else { 0 };
    }
}

pub fn lookup_compound_dictionary_match_opt(
    addon: &CompoundDictionary,
    probes: &[PreparedDictionaryProbe],
    data: &[u8],
    ring_buffer_mask: usize,
    distance_cache: &[i32],
    cur_ix: usize,
    max_length: usize,
    max_ring_buffer_distance: usize,
    max_distance: usize,
    sr: &mut HasherSearchResult,
) {
    let base_offset = max_ring_buffer_distance + 1 + addon.total_size - 1;
    for d in 0..addon.num_chunks {
        let dict = &addon.chunks[d];
        let probe = &probes[d];
        let source_size = dict.source_size as usize;
        let distance_offset = base_offset - addon.chunk_offsets[d];
        let boundary = distance_offset - source_size;
        let cur_ix_masked = cur_ix & ring_buffer_mask;
        let mut best_score = sr.score;
        let mut best_len = sr.len;
        let mut chain_idx = probe.chain_idx;
        let mut item = probe.item;

        for i in 0..4 {
            let distance = distance_cache[i] as usize;
            if distance <= boundary || distance > distance_offset {
                continue;
            }
            let offset = distance_offset - distance;
            let limit = (source_size - offset).min(max_length);
            let len =
                find_match_length_with_limit(&dict.source[offset..], &data[cur_ix_masked..], limit);
            if len >= 2 {
                let mut score = backward_reference_score_using_last_distance(len);
                if best_score < score {
                    if i != 0 {
                        score -= backward_reference_penalty_using_last_distance(i);
                    }
                    if best_score < score {
                        best_score = score;
                        if len > best_len {
                            best_len = len;
                        }
                        sr.len = len;
                        sr.len_code_delta = 0;
                        sr.distance = distance;
                        sr.score = best_score;
                    }
                }
            }
        }
        if best_len < 3 {
            best_len = 3;
        }
        while item == 0 {
            let raw = dict.items[chain_idx];
            chain_idx += 1;
            let offset = (raw & 0x7FFFFFFF) as usize;
            item = raw & 0x80000000;
            let distance = distance_offset - offset;
            let limit = (source_size - offset).min(max_length);
            if distance > max_distance {
                continue;
            }
            if best_len >= limit
                || read_u32_le(data, cur_ix_masked + best_len - 3)
                    != read_u32_le(&dict.source, offset + best_len - 3)
            {
                continue;
            }
            let len =
                find_match_length_with_limit(&dict.source[offset..], &data[cur_ix_masked..], limit);
            if len >= 4 {
                let score = backward_reference_score(len, distance);
                if best_score < score {
                    best_score = score;
                    best_len = len;
                    sr.len = best_len;
                    sr.len_code_delta = 0;
                    sr.distance = distance;
                    sr.score = best_score;
                }
            }
        }
    }
}

// =========================================================================
// Hasher implementations
// =========================================================================

pub trait GenericHasher {
    fn hash_type_length(&self) -> usize;
    fn store_lookahead(&self) -> usize;
    fn prepare(&mut self, one_shot: bool, input_size: usize, data: &[u8]);
    fn store(&mut self, data: &[u8], mask: usize, ix: usize);
    fn store_range(&mut self, data: &[u8], mask: usize, ix_start: usize, ix_end: usize) {
        for i in ix_start..ix_end {
            self.store(data, mask, i);
        }
    }
    fn stitch_to_previous_block(
        &mut self,
        num_bytes: usize,
        position: usize,
        ringbuffer: &[u8],
        ringbuffer_mask: usize,
    );
    fn prepare_distance_cache(&self, distance_cache: &mut [i32]);
    fn find_longest_match(
        &mut self,
        common: &mut HasherCommon,
        dictionary: &BrotliEncoderDictionary,
        data: &[u8],
        ring_buffer_mask: usize,
        distance_cache: &[i32],
        cur_ix: usize,
        max_length: usize,
        max_backward: usize,
        dictionary_distance: usize,
        max_distance: usize,
        out: &mut HasherSearchResult,
    );
}

// -------------------------------------------------------------------------
// 1. HashLongestMatchQuickly (H2, H3, H4, H54)
// -------------------------------------------------------------------------
#[derive(Debug, Clone)]
pub struct HashLongestMatchQuickly<
    const BUCKET_BITS: usize,
    const BUCKET_SWEEP_BITS: usize,
    const HASH_LEN: usize,
    const USE_DICTIONARY: bool,
> {
    pub buckets: Vec<u32>,
}

impl<
    const BUCKET_BITS: usize,
    const BUCKET_SWEEP_BITS: usize,
    const HASH_LEN: usize,
    const USE_DICTIONARY: bool,
> HashLongestMatchQuickly<BUCKET_BITS, BUCKET_SWEEP_BITS, HASH_LEN, USE_DICTIONARY>
{
    const BUCKET_SIZE: usize = 1 << BUCKET_BITS;
    const BUCKET_MASK: usize = Self::BUCKET_SIZE - 1;
    const BUCKET_SWEEP: usize = 1 << BUCKET_SWEEP_BITS;
    const BUCKET_SWEEP_MASK: usize = (Self::BUCKET_SWEEP - 1) << 3;

    pub fn new() -> Self {
        Self {
            buckets: vec![0u32; Self::BUCKET_SIZE],
        }
    }

    #[inline(always)]
    fn hash_bytes(data: &[u8], pos: usize) -> usize {
        let v = read_u64_le(data, pos);
        let h = (v << (64 - 8 * HASH_LEN)).wrapping_mul(K_HASH_MUL_64);
        (h >> (64 - BUCKET_BITS)) as usize
    }
}

impl<
    const BUCKET_BITS: usize,
    const BUCKET_SWEEP_BITS: usize,
    const HASH_LEN: usize,
    const USE_DICTIONARY: bool,
> GenericHasher
    for HashLongestMatchQuickly<BUCKET_BITS, BUCKET_SWEEP_BITS, HASH_LEN, USE_DICTIONARY>
{
    #[inline(always)]
    fn hash_type_length(&self) -> usize {
        8
    }
    #[inline(always)]
    fn store_lookahead(&self) -> usize {
        8
    }

    fn prepare(&mut self, one_shot: bool, input_size: usize, data: &[u8]) {
        let partial_prepare_threshold = Self::BUCKET_SIZE >> 5;
        if one_shot && input_size <= partial_prepare_threshold {
            for i in 0..input_size {
                let key = Self::hash_bytes(data, i);
                if Self::BUCKET_SWEEP == 1 {
                    self.buckets[key] = 0;
                } else {
                    for j in 0..Self::BUCKET_SWEEP {
                        self.buckets[(key + (j << 3)) & Self::BUCKET_MASK] = 0;
                    }
                }
            }
        } else {
            self.buckets.fill(0);
        }
    }

    #[inline(always)]
    fn store(&mut self, data: &[u8], mask: usize, ix: usize) {
        let key = Self::hash_bytes(data, ix & mask);
        if Self::BUCKET_SWEEP == 1 {
            self.buckets[key] = ix as u32;
        } else {
            let off = ix & Self::BUCKET_SWEEP_MASK;
            self.buckets[(key + off) & Self::BUCKET_MASK] = ix as u32;
        }
    }

    fn stitch_to_previous_block(
        &mut self,
        num_bytes: usize,
        position: usize,
        ringbuffer: &[u8],
        ringbuffer_mask: usize,
    ) {
        if num_bytes >= self.hash_type_length() - 1 && position >= 3 {
            self.store(ringbuffer, ringbuffer_mask, position - 3);
            self.store(ringbuffer, ringbuffer_mask, position - 2);
            self.store(ringbuffer, ringbuffer_mask, position - 1);
        }
    }

    #[inline(always)]
    fn prepare_distance_cache(&self, _distance_cache: &mut [i32]) {}

    fn find_longest_match(
        &mut self,
        common: &mut HasherCommon,
        dictionary: &BrotliEncoderDictionary,
        data: &[u8],
        ring_buffer_mask: usize,
        distance_cache: &[i32],
        cur_ix: usize,
        max_length: usize,
        max_backward: usize,
        dictionary_distance: usize,
        max_distance: usize,
        out: &mut HasherSearchResult,
    ) {
        let best_len_in = out.len;
        let cur_ix_masked = cur_ix & ring_buffer_mask;
        let mut compare_char = data[cur_ix_masked + best_len_in];
        let key = Self::hash_bytes(data, cur_ix_masked);
        let min_score = out.score;
        let mut best_score = out.score;
        let mut best_len = best_len_in;
        let cached_backward = distance_cache[0] as usize;
        let mut prev_ix = cur_ix.wrapping_sub(cached_backward);

        out.len_code_delta = 0;
        if prev_ix < cur_ix && cached_backward <= max_backward {
            prev_ix &= ring_buffer_mask;
            if compare_char == data[prev_ix + best_len] {
                let len =
                    find_match_length_with_limit(&data[prev_ix..], &data[cur_ix_masked..], max_length);
                if len >= 4 {
                    let score = backward_reference_score_using_last_distance(len);
                    if best_score < score {
                        out.len = len;
                        out.distance = cached_backward;
                        out.score = score;
                        if Self::BUCKET_SWEEP == 1 {
                            self.buckets[key] = cur_ix as u32;
                            return;
                        } else {
                            best_len = len;
                            best_score = score;
                            compare_char = data[cur_ix_masked + len];
                        }
                    }
                }
            }
        }
        if Self::BUCKET_SWEEP == 1 {
            prev_ix = self.buckets[key] as usize;
            self.buckets[key] = cur_ix as u32;
            let backward = cur_ix.wrapping_sub(prev_ix);
            prev_ix &= ring_buffer_mask;
            if compare_char != data[prev_ix + best_len_in] {
                return;
            }
            if backward == 0 || backward > max_backward {
                return;
            }
            let len =
                find_match_length_with_limit(&data[prev_ix..], &data[cur_ix_masked..], max_length);
            if len >= 4 {
                let score = backward_reference_score(len, backward);
                if best_score < score {
                    out.len = len;
                    out.distance = backward;
                    out.score = score;
                    return;
                }
            }
        } else {
            let mut key_out = 0usize;
            let sweep_idx = (cur_ix & Self::BUCKET_SWEEP_MASK) >> 3;
            for i in 0..Self::BUCKET_SWEEP {
                let k = (key + (i << 3)) & Self::BUCKET_MASK;
                if i == sweep_idx {
                    key_out = k;
                }
                prev_ix = self.buckets[k] as usize;
                let backward = cur_ix.wrapping_sub(prev_ix);
                prev_ix &= ring_buffer_mask;
                if compare_char != data[prev_ix + best_len] {
                    continue;
                }
                if backward == 0 || backward > max_backward {
                    continue;
                }
                let len = find_match_length_with_limit(
                    &data[prev_ix..],
                    &data[cur_ix_masked..],
                    max_length,
                );
                if len >= 4 {
                    let score = backward_reference_score(len, backward);
                    if best_score < score {
                        best_len = len;
                        out.len = len;
                        compare_char = data[cur_ix_masked + len];
                        best_score = score;
                        out.score = score;
                        out.distance = backward;
                    }
                }
            }
            if USE_DICTIONARY && min_score == out.score {
                search_in_static_dictionary(
                    dictionary,
                    common,
                    &data[cur_ix_masked..],
                    max_length,
                    dictionary_distance,
                    max_distance,
                    out,
                    true,
                );
            }
            self.buckets[key_out] = cur_ix as u32;
            return;
        }
        if USE_DICTIONARY && min_score == out.score {
            search_in_static_dictionary(
                dictionary,
                common,
                &data[cur_ix_masked..],
                max_length,
                dictionary_distance,
                max_distance,
                out,
                true,
            );
        }
    }
}

// -------------------------------------------------------------------------
// 2. HashLongestMatch (H5) and HashLongestMatch64 (H6)
// -------------------------------------------------------------------------
#[derive(Debug, Clone)]
pub struct HashLongestMatch {
    pub bucket_size: usize,
    pub block_size: usize,
    pub hash_shift: i32,
    pub block_mask: u32,
    pub block_bits: i32,
    pub num_last_distances_to_check: i32,
    pub num: Vec<u16>,
    pub buckets: Vec<u32>,
}

impl HashLongestMatch {
    pub fn new(params: &BrotliHasherParams) -> Self {
        let bucket_size = 1usize << params.bucket_bits;
        let block_size = 1usize << params.block_bits;
        Self {
            bucket_size,
            block_size,
            hash_shift: 32 - params.bucket_bits,
            block_mask: (block_size - 1) as u32,
            block_bits: params.block_bits,
            num_last_distances_to_check: params.num_last_distances_to_check,
            num: vec![0u16; bucket_size],
            buckets: vec![0u32; bucket_size * block_size],
        }
    }

    #[inline(always)]
    fn hash_bytes(&self, data: &[u8], pos: usize) -> usize {
        let h = read_u32_le(data, pos).wrapping_mul(K_HASH_MUL_32);
        (h >> self.hash_shift) as usize
    }
}

impl GenericHasher for HashLongestMatch {
    #[inline(always)]
    fn hash_type_length(&self) -> usize {
        4
    }
    #[inline(always)]
    fn store_lookahead(&self) -> usize {
        4
    }

    fn prepare(&mut self, one_shot: bool, input_size: usize, data: &[u8]) {
        let partial_prepare_threshold = self.bucket_size >> 6;
        if one_shot && input_size <= partial_prepare_threshold {
            for i in 0..input_size {
                let key = self.hash_bytes(data, i);
                self.num[key] = 0;
            }
        } else {
            self.num.fill(0);
        }
    }

    #[inline(always)]
    fn store(&mut self, data: &[u8], mask: usize, ix: usize) {
        let key = self.hash_bytes(data, ix & mask);
        let minor_ix = (self.num[key] as u32 & self.block_mask) as usize;
        let offset = minor_ix + (key << self.block_bits);
        self.num[key] = self.num[key].wrapping_add(1);
        self.buckets[offset] = ix as u32;
    }

    fn stitch_to_previous_block(
        &mut self,
        num_bytes: usize,
        position: usize,
        ringbuffer: &[u8],
        ringbuffer_mask: usize,
    ) {
        if num_bytes >= 3 && position >= 3 {
            self.store(ringbuffer, ringbuffer_mask, position - 3);
            self.store(ringbuffer, ringbuffer_mask, position - 2);
            self.store(ringbuffer, ringbuffer_mask, position - 1);
        }
    }

    #[inline(always)]
    fn prepare_distance_cache(&self, distance_cache: &mut [i32]) {
        prepare_distance_cache(distance_cache, self.num_last_distances_to_check as usize);
    }

    fn find_longest_match(
        &mut self,
        common: &mut HasherCommon,
        dictionary: &BrotliEncoderDictionary,
        data: &[u8],
        ring_buffer_mask: usize,
        distance_cache: &[i32],
        cur_ix: usize,
        max_length: usize,
        max_backward: usize,
        dictionary_distance: usize,
        max_distance: usize,
        out: &mut HasherSearchResult,
    ) {
        let cur_ix_masked = cur_ix & ring_buffer_mask;
        let min_score = out.score;
        let mut best_score = out.score;
        let mut best_len = out.len;
        let key = self.hash_bytes(data, cur_ix_masked);
        let bucket_base = key << self.block_bits;
        out.len = 0;
        out.len_code_delta = 0;

        for i in 0..(self.num_last_distances_to_check as usize) {
            let backward = distance_cache[i] as usize;
            let mut prev_ix = cur_ix.wrapping_sub(backward);
            if prev_ix >= cur_ix || backward > max_backward {
                continue;
            }
            prev_ix &= ring_buffer_mask;
            if cur_ix_masked + best_len > ring_buffer_mask {
                break;
            }
            if prev_ix + best_len > ring_buffer_mask
                || data[cur_ix_masked + best_len] != data[prev_ix + best_len]
            {
                continue;
            }
            let len =
                find_match_length_with_limit(&data[prev_ix..], &data[cur_ix_masked..], max_length);
            if len >= 3 || (len == 2 && i < 2) {
                let mut score = backward_reference_score_using_last_distance(len);
                if best_score < score {
                    if i != 0 {
                        score -= backward_reference_penalty_using_last_distance(i);
                    }
                    if best_score < score {
                        best_score = score;
                        best_len = len;
                        out.len = best_len;
                        out.distance = backward;
                        out.score = best_score;
                    }
                }
            }
        }
        if best_len < 3 {
            best_len = 3;
        }
        let num_val = self.num[key] as usize;
        let down = if num_val > self.block_size {
            num_val - self.block_size
        } else {
            0
        };
        let mut i = num_val;
        while i > down {
            i -= 1;
            let mut prev_ix = self.buckets[bucket_base + (i & (self.block_mask as usize))] as usize;
            let backward = cur_ix.wrapping_sub(prev_ix);
            if backward > max_backward {
                break;
            }
            prev_ix &= ring_buffer_mask;
            if cur_ix_masked + best_len > ring_buffer_mask {
                break;
            }
            if prev_ix + best_len > ring_buffer_mask
                || read_u32_le(data, cur_ix_masked + best_len - 3)
                    != read_u32_le(data, prev_ix + best_len - 3)
            {
                continue;
            }
            let len =
                find_match_length_with_limit(&data[prev_ix..], &data[cur_ix_masked..], max_length);
            if len >= 4 {
                let score = backward_reference_score(len, backward);
                if best_score < score {
                    best_score = score;
                    best_len = len;
                    out.len = best_len;
                    out.distance = backward;
                    out.score = best_score;
                }
            }
        }
        self.buckets[bucket_base + (num_val & (self.block_mask as usize))] = cur_ix as u32;
        self.num[key] = self.num[key].wrapping_add(1);
        if min_score == out.score {
            search_in_static_dictionary(
                dictionary,
                common,
                &data[cur_ix_masked..],
                max_length,
                dictionary_distance,
                max_distance,
                out,
                false,
            );
        }
    }
}

#[derive(Debug, Clone)]
pub struct HashLongestMatch64 {
    pub bucket_size: usize,
    pub block_size: usize,
    pub hash_mul: u64,
    pub block_mask: u32,
    pub block_bits: i32,
    pub num_last_distances_to_check: i32,
    pub num: Vec<u16>,
    pub buckets: Vec<u32>,
}

impl HashLongestMatch64 {
    pub fn new(params: &BrotliHasherParams) -> Self {
        let bucket_size = 1usize << params.bucket_bits;
        let block_size = 1usize << params.block_bits;
        Self {
            bucket_size,
            block_size,
            hash_mul: K_HASH_MUL_64 << (64 - 5 * 8),
            block_mask: (block_size - 1) as u32,
            block_bits: params.block_bits,
            num_last_distances_to_check: params.num_last_distances_to_check,
            num: vec![0u16; bucket_size],
            buckets: vec![0u32; bucket_size * block_size],
        }
    }

    #[inline(always)]
    fn hash_bytes(&self, data: &[u8], pos: usize) -> usize {
        let h = read_u64_le(data, pos).wrapping_mul(self.hash_mul);
        (h >> (64 - 15)) as usize
    }
}

impl GenericHasher for HashLongestMatch64 {
    #[inline(always)]
    fn hash_type_length(&self) -> usize {
        8
    }
    #[inline(always)]
    fn store_lookahead(&self) -> usize {
        8
    }

    fn prepare(&mut self, one_shot: bool, input_size: usize, data: &[u8]) {
        let partial_prepare_threshold = self.bucket_size >> 6;
        if one_shot && input_size <= partial_prepare_threshold {
            for i in 0..input_size {
                let key = self.hash_bytes(data, i);
                self.num[key] = 0;
            }
        } else {
            self.num.fill(0);
        }
    }

    #[inline(always)]
    fn store(&mut self, data: &[u8], mask: usize, ix: usize) {
        let key = self.hash_bytes(data, ix & mask);
        let minor_ix = (self.num[key] as u32 & self.block_mask) as usize;
        let offset = minor_ix + (key << self.block_bits);
        self.num[key] = self.num[key].wrapping_add(1);
        self.buckets[offset] = ix as u32;
    }

    fn stitch_to_previous_block(
        &mut self,
        num_bytes: usize,
        position: usize,
        ringbuffer: &[u8],
        ringbuffer_mask: usize,
    ) {
        if num_bytes >= 7 && position >= 3 {
            self.store(ringbuffer, ringbuffer_mask, position - 3);
            self.store(ringbuffer, ringbuffer_mask, position - 2);
            self.store(ringbuffer, ringbuffer_mask, position - 1);
        }
    }

    #[inline(always)]
    fn prepare_distance_cache(&self, distance_cache: &mut [i32]) {
        prepare_distance_cache(distance_cache, self.num_last_distances_to_check as usize);
    }

    fn find_longest_match(
        &mut self,
        common: &mut HasherCommon,
        dictionary: &BrotliEncoderDictionary,
        data: &[u8],
        ring_buffer_mask: usize,
        distance_cache: &[i32],
        cur_ix: usize,
        max_length: usize,
        max_backward: usize,
        dictionary_distance: usize,
        max_distance: usize,
        out: &mut HasherSearchResult,
    ) {
        let cur_ix_masked = cur_ix & ring_buffer_mask;
        let min_score = out.score;
        let mut best_score = out.score;
        let mut best_len = out.len;
        let key = self.hash_bytes(data, cur_ix_masked);
        let bucket_base = key << self.block_bits;
        out.len = 0;
        out.len_code_delta = 0;

        for i in 0..(self.num_last_distances_to_check as usize) {
            let backward = distance_cache[i] as usize;
            let mut prev_ix = cur_ix.wrapping_sub(backward);
            if prev_ix >= cur_ix || backward > max_backward {
                continue;
            }
            prev_ix &= ring_buffer_mask;
            if cur_ix_masked + best_len > ring_buffer_mask {
                break;
            }
            if prev_ix + best_len > ring_buffer_mask
                || data[cur_ix_masked + best_len] != data[prev_ix + best_len]
            {
                continue;
            }
            let len =
                find_match_length_with_limit(&data[prev_ix..], &data[cur_ix_masked..], max_length);
            if len >= 3 || (len == 2 && i < 2) {
                let mut score = backward_reference_score_using_last_distance(len);
                if best_score < score {
                    if i != 0 {
                        score -= backward_reference_penalty_using_last_distance(i);
                    }
                    if best_score < score {
                        best_score = score;
                        best_len = len;
                        out.len = best_len;
                        out.distance = backward;
                        out.score = best_score;
                    }
                }
            }
        }
        if best_len < 3 {
            best_len = 3;
        }
        let num_val = self.num[key] as usize;
        let down = if num_val > self.block_size {
            num_val - self.block_size
        } else {
            0
        };
        let first4 = read_u32_le(data, cur_ix_masked);
        let max_length_m4 = max_length - 4;
        let mut i = num_val;
        while i > down {
            i -= 1;
            let mut prev_ix = self.buckets[bucket_base + (i & (self.block_mask as usize))] as usize;
            let backward = cur_ix.wrapping_sub(prev_ix);
            if backward > max_backward {
                break;
            }
            prev_ix &= ring_buffer_mask;
            if cur_ix_masked + best_len > ring_buffer_mask {
                break;
            }
            if prev_ix + best_len > ring_buffer_mask
                || read_u32_le(data, cur_ix_masked + best_len - 3)
                    != read_u32_le(data, prev_ix + best_len - 3)
            {
                continue;
            }
            if first4 != read_u32_le(data, prev_ix) {
                continue;
            }
            let len = 4 + find_match_length_with_limit(
                &data[prev_ix + 4..],
                &data[cur_ix_masked + 4..],
                max_length_m4,
            );
            let score = backward_reference_score(len, backward);
            if best_score < score {
                best_score = score;
                best_len = len;
                out.len = best_len;
                out.distance = backward;
                out.score = best_score;
            }
        }
        self.buckets[bucket_base + (num_val & (self.block_mask as usize))] = cur_ix as u32;
        self.num[key] = self.num[key].wrapping_add(1);
        if min_score == out.score {
            search_in_static_dictionary(
                dictionary,
                common,
                &data[cur_ix_masked..],
                max_length,
                dictionary_distance,
                max_distance,
                out,
                false,
            );
        }
    }
}

// -------------------------------------------------------------------------
// 3. SIMD Tag Hashers (H58, H59, H68)
// -------------------------------------------------------------------------
const TAG_HASH_BITS: usize = 8;
const TAG_HASH_MASK: u32 = (1 << TAG_HASH_BITS) - 1;

#[derive(Debug, Clone)]
pub struct HashLongestMatchSimd<const OPT: bool> {
    pub bucket_size: usize,
    pub block_size: usize,
    pub hash_shift: i32,
    pub block_mask: u32,
    pub block_bits: i32,
    pub num_last_distances_to_check: i32,
    pub num: Vec<u16>,
    pub tags: Vec<u8>,
    pub buckets: Vec<u32>,
}

impl<const OPT: bool> HashLongestMatchSimd<OPT> {
    pub fn new(params: &BrotliHasherParams) -> Self {
        let bucket_size = 1usize << params.bucket_bits;
        let block_size = 1usize << params.block_bits;
        Self {
            bucket_size,
            block_size,
            hash_shift: 32 - params.bucket_bits - (TAG_HASH_BITS as i32),
            block_mask: (block_size - 1) as u32,
            block_bits: params.block_bits,
            num_last_distances_to_check: params.num_last_distances_to_check,
            num: vec![65535u16; bucket_size],
            tags: vec![0u8; bucket_size * block_size],
            buckets: vec![0u32; bucket_size * block_size],
        }
    }

    #[inline(always)]
    fn hash_bytes(&self, data: &[u8], pos: usize) -> u32 {
        let h = read_u32_le(data, pos).wrapping_mul(K_HASH_MUL_32);
        h >> self.hash_shift
    }
}

impl<const OPT: bool> GenericHasher for HashLongestMatchSimd<OPT> {
    #[inline(always)]
    fn hash_type_length(&self) -> usize {
        4
    }
    #[inline(always)]
    fn store_lookahead(&self) -> usize {
        4
    }

    fn prepare(&mut self, one_shot: bool, input_size: usize, data: &[u8]) {
        let partial_prepare_threshold = self.bucket_size >> 6;
        if one_shot && input_size <= partial_prepare_threshold {
            for i in 0..input_size {
                let hash = self.hash_bytes(data, i);
                let key = (hash >> TAG_HASH_BITS) as usize;
                self.num[key] = 65535;
            }
        } else {
            self.num.fill(65535);
        }
    }

    #[inline(always)]
    fn store(&mut self, data: &[u8], mask: usize, ix: usize) {
        let hash = self.hash_bytes(data, ix & mask);
        let key = (hash >> TAG_HASH_BITS) as usize;
        let tag = (hash & TAG_HASH_MASK) as u8;
        let minor_ix = (self.num[key] as u32 & self.block_mask) as usize;
        let offset = minor_ix + (key << self.block_bits);
        self.num[key] = self.num[key].wrapping_sub(1);
        self.buckets[offset] = ix as u32;
        self.tags[offset] = tag;
    }

    fn stitch_to_previous_block(
        &mut self,
        num_bytes: usize,
        position: usize,
        ringbuffer: &[u8],
        ringbuffer_mask: usize,
    ) {
        if num_bytes >= 3 && position >= 3 {
            self.store(ringbuffer, ringbuffer_mask, position - 3);
            self.store(ringbuffer, ringbuffer_mask, position - 2);
            self.store(ringbuffer, ringbuffer_mask, position - 1);
        }
    }

    #[inline(always)]
    fn prepare_distance_cache(&self, distance_cache: &mut [i32]) {
        prepare_distance_cache(distance_cache, self.num_last_distances_to_check as usize);
    }

    fn find_longest_match(
        &mut self,
        common: &mut HasherCommon,
        dictionary: &BrotliEncoderDictionary,
        data: &[u8],
        ring_buffer_mask: usize,
        distance_cache: &[i32],
        cur_ix: usize,
        max_length: usize,
        max_backward: usize,
        dictionary_distance: usize,
        max_distance: usize,
        out: &mut HasherSearchResult,
    ) {
        let cur_ix_masked = cur_ix & ring_buffer_mask;
        let min_score = out.score;
        let mut best_score = out.score;
        let mut best_len = if OPT {
            if common.dict_num_matches < (common.dict_num_lookups >> 7) {
                out.len
            } else {
                0
            }
        } else {
            out.len
        };
        let hash = self.hash_bytes(data, cur_ix_masked);
        let key = (hash >> TAG_HASH_BITS) as usize;
        let bucket_base = key << self.block_bits;
        out.len = 0;
        out.len_code_delta = 0;

        if OPT && best_len < 1 {
            best_len = 1;
        }

        for i in 0..(self.num_last_distances_to_check as usize) {
            let backward = distance_cache[i] as usize;
            let mut prev_ix = cur_ix.wrapping_sub(backward);
            if prev_ix >= cur_ix || backward > max_backward {
                continue;
            }
            prev_ix &= ring_buffer_mask;
            if OPT {
                if read_u16_le(data, cur_ix_masked + best_len - 1)
                    != read_u16_le(data, prev_ix + best_len - 1)
                {
                    continue;
                }
            } else {
                if cur_ix_masked + best_len > ring_buffer_mask {
                    break;
                }
                if prev_ix + best_len > ring_buffer_mask
                    || data[cur_ix_masked + best_len] != data[prev_ix + best_len]
                {
                    continue;
                }
            }
            let len =
                find_match_length_with_limit(&data[prev_ix..], &data[cur_ix_masked..], max_length);
            if len >= 3 || (len == 2 && i < 2) {
                let mut score = backward_reference_score_using_last_distance(len);
                if best_score < score {
                    if i != 0 {
                        score -= backward_reference_penalty_using_last_distance(i);
                    }
                    if best_score < score {
                        best_score = score;
                        best_len = len;
                        out.len = best_len;
                        out.distance = backward;
                        out.score = best_score;
                    }
                }
            }
        }
        if best_len < 3 {
            best_len = 3;
        }
        let tag = (hash & TAG_HASH_MASK) as u8;
        let num_key = self.num[key];
        let head = ((num_key.wrapping_add(1)) as u32 & self.block_mask) as usize;
        let mut matches = get_matching_tag_mask(
            self.block_size / 16,
            tag,
            &self.tags[bucket_base..bucket_base + self.block_size],
            head,
        );
        let n = 65535u16.wrapping_sub(num_key);
        let block_has_unused_slots: u64 = if self.block_size > (n as usize) { 1 } else { 0 };
        let mask = (block_has_unused_slots << (n & 63)).wrapping_sub(1);
        matches &= mask;
        while matches > 0 {
            let tz = matches.trailing_zeros() as usize;
            matches &= matches - 1;
            let rb_index = (head + tz) & (self.block_mask as usize);
            let mut prev_ix = self.buckets[bucket_base + rb_index] as usize;
            let backward = cur_ix.wrapping_sub(prev_ix);
            if backward > max_backward {
                break;
            }
            prev_ix &= ring_buffer_mask;
            if OPT {
                if read_u32_le(data, cur_ix_masked + best_len - 3)
                    != read_u32_le(data, prev_ix + best_len - 3)
                {
                    continue;
                }
            } else {
                if cur_ix_masked + best_len > ring_buffer_mask {
                    break;
                }
                if prev_ix + best_len > ring_buffer_mask
                    || read_u32_le(data, cur_ix_masked + best_len - 3)
                        != read_u32_le(data, prev_ix + best_len - 3)
                {
                    continue;
                }
            }
            let len =
                find_match_length_with_limit(&data[prev_ix..], &data[cur_ix_masked..], max_length);
            if len >= 4 {
                let score = backward_reference_score(len, backward);
                if best_score < score {
                    best_score = score;
                    best_len = len;
                    out.len = best_len;
                    out.distance = backward;
                    out.score = best_score;
                }
            }
        }
        let store_slot = (num_key as u32 & self.block_mask) as usize;
        self.buckets[bucket_base + store_slot] = cur_ix as u32;
        self.tags[bucket_base + store_slot] = tag;
        self.num[key] = num_key.wrapping_sub(1);
        if min_score == out.score {
            search_in_static_dictionary(
                dictionary,
                common,
                &data[cur_ix_masked..],
                max_length,
                dictionary_distance,
                max_distance,
                out,
                false,
            );
        }
    }
}

#[derive(Debug, Clone)]
pub struct HashLongestMatch64Simd {
    pub bucket_size: usize,
    pub block_size: usize,
    pub hash_mul: u64,
    pub block_mask: u32,
    pub block_bits: i32,
    pub num_last_distances_to_check: i32,
    pub num: Vec<u16>,
    pub tags: Vec<u8>,
    pub buckets: Vec<u32>,
}

impl HashLongestMatch64Simd {
    pub fn new(params: &BrotliHasherParams) -> Self {
        let bucket_size = 1usize << params.bucket_bits;
        let block_size = 1usize << params.block_bits;
        Self {
            bucket_size,
            block_size,
            hash_mul: K_HASH_MUL_64 << (64 - 5 * 8),
            block_mask: (block_size - 1) as u32,
            block_bits: params.block_bits,
            num_last_distances_to_check: params.num_last_distances_to_check,
            num: vec![65535u16; bucket_size],
            tags: vec![0u8; bucket_size * block_size],
            buckets: vec![0u32; bucket_size * block_size],
        }
    }

    #[inline(always)]
    fn hash_bytes(&self, data: &[u8], pos: usize) -> usize {
        let h = read_u64_le(data, pos).wrapping_mul(self.hash_mul);
        (h >> (64 - 15 - TAG_HASH_BITS)) as usize
    }
}

impl GenericHasher for HashLongestMatch64Simd {
    #[inline(always)]
    fn hash_type_length(&self) -> usize {
        8
    }
    #[inline(always)]
    fn store_lookahead(&self) -> usize {
        8
    }

    fn prepare(&mut self, one_shot: bool, input_size: usize, data: &[u8]) {
        let partial_prepare_threshold = self.bucket_size >> 6;
        if one_shot && input_size <= partial_prepare_threshold {
            for i in 0..input_size {
                let hash = self.hash_bytes(data, i);
                let key = hash >> TAG_HASH_BITS;
                self.num[key] = 65535;
            }
        } else {
            self.num.fill(65535);
        }
    }

    #[inline(always)]
    fn store(&mut self, data: &[u8], mask: usize, ix: usize) {
        let hash = self.hash_bytes(data, ix & mask);
        let key = hash >> TAG_HASH_BITS;
        let tag = (hash & (TAG_HASH_MASK as usize)) as u8;
        let minor_ix = (self.num[key] as u32 & self.block_mask) as usize;
        let offset = minor_ix + (key << self.block_bits);
        self.num[key] = self.num[key].wrapping_sub(1);
        self.buckets[offset] = ix as u32;
        self.tags[offset] = tag;
    }

    fn stitch_to_previous_block(
        &mut self,
        num_bytes: usize,
        position: usize,
        ringbuffer: &[u8],
        ringbuffer_mask: usize,
    ) {
        if num_bytes >= 7 && position >= 3 {
            self.store(ringbuffer, ringbuffer_mask, position - 3);
            self.store(ringbuffer, ringbuffer_mask, position - 2);
            self.store(ringbuffer, ringbuffer_mask, position - 1);
        }
    }

    #[inline(always)]
    fn prepare_distance_cache(&self, distance_cache: &mut [i32]) {
        prepare_distance_cache(distance_cache, self.num_last_distances_to_check as usize);
    }

    fn find_longest_match(
        &mut self,
        common: &mut HasherCommon,
        dictionary: &BrotliEncoderDictionary,
        data: &[u8],
        ring_buffer_mask: usize,
        distance_cache: &[i32],
        cur_ix: usize,
        max_length: usize,
        max_backward: usize,
        dictionary_distance: usize,
        max_distance: usize,
        out: &mut HasherSearchResult,
    ) {
        let cur_ix_masked = cur_ix & ring_buffer_mask;
        let min_score = out.score;
        let mut best_score = out.score;
        let mut best_len = out.len;
        let hash = self.hash_bytes(data, cur_ix_masked);
        let key = hash >> TAG_HASH_BITS;
        let bucket_base = key << self.block_bits;
        out.len = 0;
        out.len_code_delta = 0;

        for i in 0..(self.num_last_distances_to_check as usize) {
            let backward = distance_cache[i] as usize;
            let mut prev_ix = cur_ix.wrapping_sub(backward);
            if prev_ix >= cur_ix || backward > max_backward {
                continue;
            }
            prev_ix &= ring_buffer_mask;
            if cur_ix_masked + best_len > ring_buffer_mask {
                break;
            }
            if prev_ix + best_len > ring_buffer_mask
                || data[cur_ix_masked + best_len] != data[prev_ix + best_len]
            {
                continue;
            }
            let len =
                find_match_length_with_limit(&data[prev_ix..], &data[cur_ix_masked..], max_length);
            if len >= 3 || (len == 2 && i < 2) {
                let mut score = backward_reference_score_using_last_distance(len);
                if best_score < score {
                    if i != 0 {
                        score -= backward_reference_penalty_using_last_distance(i);
                    }
                    if best_score < score {
                        best_score = score;
                        best_len = len;
                        out.len = best_len;
                        out.distance = backward;
                        out.score = best_score;
                    }
                }
            }
        }
        if best_len < 3 {
            best_len = 3;
        }
        let tag = (hash & (TAG_HASH_MASK as usize)) as u8;
        let first4 = read_u32_le(data, cur_ix_masked);
        let max_length_m4 = max_length - 4;
        let num_key = self.num[key];
        let head = ((num_key.wrapping_add(1)) as u32 & self.block_mask) as usize;
        let mut matches = get_matching_tag_mask(
            self.block_size / 16,
            tag,
            &self.tags[bucket_base..bucket_base + self.block_size],
            head,
        );
        let n = 65535u16.wrapping_sub(num_key);
        let block_has_unused_slots: u64 = if self.block_size > (n as usize) { 1 } else { 0 };
        let mask = (block_has_unused_slots << (n & 63)).wrapping_sub(1);
        matches &= mask;
        while matches > 0 {
            let tz = matches.trailing_zeros() as usize;
            matches &= matches - 1;
            let rb_index = (head + tz) & (self.block_mask as usize);
            let mut prev_ix = self.buckets[bucket_base + rb_index] as usize;
            let backward = cur_ix.wrapping_sub(prev_ix);
            if backward > max_backward {
                break;
            }
            prev_ix &= ring_buffer_mask;
            if cur_ix_masked + best_len > ring_buffer_mask {
                break;
            }
            if prev_ix + best_len > ring_buffer_mask
                || read_u32_le(data, cur_ix_masked + best_len - 3)
                    != read_u32_le(data, prev_ix + best_len - 3)
            {
                continue;
            }
            if first4 != read_u32_le(data, prev_ix) {
                continue;
            }
            let len = 4 + find_match_length_with_limit(
                &data[prev_ix + 4..],
                &data[cur_ix_masked + 4..],
                max_length_m4,
            );
            let score = backward_reference_score(len, backward);
            if best_score < score {
                best_score = score;
                best_len = len;
                out.len = best_len;
                out.distance = backward;
                out.score = best_score;
            }
        }
        let store_slot = (num_key as u32 & self.block_mask) as usize;
        self.buckets[bucket_base + store_slot] = cur_ix as u32;
        self.tags[bucket_base + store_slot] = tag;
        self.num[key] = num_key.wrapping_sub(1);
        if min_score == out.score {
            search_in_static_dictionary(
                dictionary,
                common,
                &data[cur_ix_masked..],
                max_length,
                dictionary_distance,
                max_distance,
                out,
                false,
            );
        }
    }
}

// -------------------------------------------------------------------------
// 4. HashForgetfulChain (H40, H41, H42)
// -------------------------------------------------------------------------
#[derive(Debug, Clone, Copy, Default)]
pub struct ChainSlot {
    pub delta: u16,
    pub next: u16,
}

#[derive(Debug, Clone)]
pub struct HashForgetfulChain<
    const BUCKET_BITS: usize,
    const NUM_BANKS: usize,
    const BANK_BITS: usize,
    const NUM_LAST_DISTANCES: usize,
> {
    pub free_slot_idx: Vec<u16>,
    pub max_hops: usize,
    pub addr: Vec<u32>,
    pub head: Vec<u16>,
    pub tiny_hash: Vec<u8>,
    pub banks: Vec<ChainSlot>,
}

impl<
    const BUCKET_BITS: usize,
    const NUM_BANKS: usize,
    const BANK_BITS: usize,
    const NUM_LAST_DISTANCES: usize,
> HashForgetfulChain<BUCKET_BITS, NUM_BANKS, BANK_BITS, NUM_LAST_DISTANCES>
{
    const BUCKET_SIZE: usize = 1 << BUCKET_BITS;
    const BANK_SIZE: usize = 1 << BANK_BITS;

    pub fn new(params: &BrotliEncoderParams) -> Self {
        Self {
            free_slot_idx: vec![0u16; NUM_BANKS],
            max_hops: (if params.quality > 6 { 7usize } else { 8usize }) << (params.quality - 4),
            addr: vec![0xCCCCCCCCu32; Self::BUCKET_SIZE],
            head: vec![0u16; Self::BUCKET_SIZE],
            tiny_hash: vec![0u8; 65536],
            banks: vec![ChainSlot::default(); NUM_BANKS * Self::BANK_SIZE],
        }
    }

    #[inline(always)]
    fn hash_bytes(data: &[u8], pos: usize) -> usize {
        let h = read_u32_le(data, pos).wrapping_mul(K_HASH_MUL_32);
        (h >> (32 - BUCKET_BITS)) as usize
    }
}

impl<
    const BUCKET_BITS: usize,
    const NUM_BANKS: usize,
    const BANK_BITS: usize,
    const NUM_LAST_DISTANCES: usize,
> GenericHasher
    for HashForgetfulChain<BUCKET_BITS, NUM_BANKS, BANK_BITS, NUM_LAST_DISTANCES>
{
    #[inline(always)]
    fn hash_type_length(&self) -> usize {
        4
    }
    #[inline(always)]
    fn store_lookahead(&self) -> usize {
        4
    }

    fn prepare(&mut self, one_shot: bool, input_size: usize, data: &[u8]) {
        let partial_prepare_threshold = Self::BUCKET_SIZE >> 6;
        if one_shot && input_size <= partial_prepare_threshold {
            for i in 0..input_size {
                let bucket = Self::hash_bytes(data, i);
                self.addr[bucket] = 0xCCCCCCCC;
                self.head[bucket] = 0xCCCC;
            }
        } else {
            self.addr.fill(0xCCCCCCCC);
            self.head.fill(0);
        }
        self.tiny_hash.fill(0);
        self.free_slot_idx.fill(0);
    }

    #[inline(always)]
    fn store(&mut self, data: &[u8], mask: usize, ix: usize) {
        let key = Self::hash_bytes(data, ix & mask);
        let bank = key & (NUM_BANKS - 1);
        let idx = (self.free_slot_idx[bank] as usize) & (Self::BANK_SIZE - 1);
        self.free_slot_idx[bank] = self.free_slot_idx[bank].wrapping_add(1);
        let mut delta = ix.wrapping_sub(self.addr[key] as usize);
        self.tiny_hash[(ix as u16) as usize] = key as u8;
        if delta > 0xFFFF {
            delta = 0xFFFF;
        }
        let slot_pos = bank * Self::BANK_SIZE + idx;
        self.banks[slot_pos].delta = delta as u16;
        self.banks[slot_pos].next = self.head[key];
        self.addr[key] = ix as u32;
        self.head[key] = idx as u16;
    }

    fn stitch_to_previous_block(
        &mut self,
        num_bytes: usize,
        position: usize,
        ringbuffer: &[u8],
        ringbuffer_mask: usize,
    ) {
        if num_bytes >= 3 && position >= 3 {
            self.store(ringbuffer, ringbuffer_mask, position - 3);
            self.store(ringbuffer, ringbuffer_mask, position - 2);
            self.store(ringbuffer, ringbuffer_mask, position - 1);
        }
    }

    #[inline(always)]
    fn prepare_distance_cache(&self, distance_cache: &mut [i32]) {
        prepare_distance_cache(distance_cache, NUM_LAST_DISTANCES);
    }

    fn find_longest_match(
        &mut self,
        common: &mut HasherCommon,
        dictionary: &BrotliEncoderDictionary,
        data: &[u8],
        ring_buffer_mask: usize,
        distance_cache: &[i32],
        cur_ix: usize,
        max_length: usize,
        max_backward: usize,
        dictionary_distance: usize,
        max_distance: usize,
        out: &mut HasherSearchResult,
    ) {
        let cur_ix_masked = cur_ix & ring_buffer_mask;
        let min_score = out.score;
        let mut best_score = out.score;
        let mut best_len = out.len;
        let key = Self::hash_bytes(data, cur_ix_masked);
        let tiny_hash = key as u8;
        out.len = 0;
        out.len_code_delta = 0;

        for i in 0..NUM_LAST_DISTANCES {
            let backward = distance_cache[i] as usize;
            let mut prev_ix = cur_ix.wrapping_sub(backward);
            if i > 0 && self.tiny_hash[(prev_ix as u16) as usize] != tiny_hash {
                continue;
            }
            if prev_ix >= cur_ix || backward > max_backward {
                continue;
            }
            prev_ix &= ring_buffer_mask;
            let len =
                find_match_length_with_limit(&data[prev_ix..], &data[cur_ix_masked..], max_length);
            if len >= 2 {
                let mut score = backward_reference_score_using_last_distance(len);
                if best_score < score {
                    if i != 0 {
                        score -= backward_reference_penalty_using_last_distance(i);
                    }
                    if best_score < score {
                        best_score = score;
                        best_len = len;
                        out.len = best_len;
                        out.distance = backward;
                        out.score = best_score;
                    }
                }
            }
        }
        if best_len < 3 {
            best_len = 3;
        }
        let bank = key & (NUM_BANKS - 1);
        let bank_base = bank * Self::BANK_SIZE;
        let mut backward = 0usize;
        let mut hops = self.max_hops;
        let mut delta = cur_ix.wrapping_sub(self.addr[key] as usize);
        let mut slot = self.head[key] as usize;
        while hops > 0 {
            hops -= 1;
            let last = slot;
            backward += delta;
            if backward > max_backward {
                break;
            }
            let prev_ix = cur_ix.wrapping_sub(backward) & ring_buffer_mask;
            slot = self.banks[bank_base + last].next as usize;
            delta = self.banks[bank_base + last].delta as usize;
            if cur_ix_masked + best_len > ring_buffer_mask
                || prev_ix + best_len > ring_buffer_mask
                || read_u32_le(data, cur_ix_masked + best_len - 3)
                    != read_u32_le(data, prev_ix + best_len - 3)
            {
                continue;
            }
            let len =
                find_match_length_with_limit(&data[prev_ix..], &data[cur_ix_masked..], max_length);
            if len >= 4 {
                let score = backward_reference_score(len, backward);
                if best_score < score {
                    best_score = score;
                    best_len = len;
                    out.len = best_len;
                    out.distance = backward;
                    out.score = best_score;
                }
            }
        }
        self.store(data, ring_buffer_mask, cur_ix);
        if out.score == min_score {
            search_in_static_dictionary(
                dictionary,
                common,
                &data[cur_ix_masked..],
                max_length,
                dictionary_distance,
                max_distance,
                out,
                false,
            );
        }
    }
}

// -------------------------------------------------------------------------
// 5. HashRolling & HashComposite (H35, H55, H65)
// -------------------------------------------------------------------------
const K_ROLLING_HASH_MUL32: u32 = 69069;
const K_INVALID_POS: u32 = 0xFFFFFFFF;

#[derive(Debug, Clone)]
pub struct HashRolling<
    const JUMP: usize,
    const NUM_BUCKETS: usize,
    const MASK: u32,
    const CHUNKLEN: usize,
> {
    pub state: u32,
    pub table: Vec<u32>,
    pub next_ix: usize,
    pub factor: u32,
    pub factor_remove: u32,
}

impl<const JUMP: usize, const NUM_BUCKETS: usize, const MASK: u32, const CHUNKLEN: usize>
    HashRolling<JUMP, NUM_BUCKETS, MASK, CHUNKLEN>
{
    pub fn new() -> Self {
        let factor = K_ROLLING_HASH_MUL32;
        let mut factor_remove = 1u32;
        let mut i = 0usize;
        while i < CHUNKLEN {
            factor_remove = factor_remove.wrapping_mul(factor);
            i += JUMP;
        }
        Self {
            state: 0,
            table: vec![K_INVALID_POS; NUM_BUCKETS],
            next_ix: 0,
            factor,
            factor_remove,
        }
    }

    #[inline(always)]
    fn hash_byte(b: u8) -> u32 {
        (b as u32) + 1
    }

    #[inline(always)]
    fn hash_rolling_initial(state: u32, add: u8, factor: u32) -> u32 {
        factor.wrapping_mul(state).wrapping_add(Self::hash_byte(add))
    }

    #[inline(always)]
    fn hash_rolling(state: u32, add: u8, rem: u8, factor: u32, factor_remove: u32) -> u32 {
        factor
            .wrapping_mul(state)
            .wrapping_add(Self::hash_byte(add))
            .wrapping_sub(factor_remove.wrapping_mul(Self::hash_byte(rem)))
    }
}

impl<const JUMP: usize, const NUM_BUCKETS: usize, const MASK: u32, const CHUNKLEN: usize>
    GenericHasher for HashRolling<JUMP, NUM_BUCKETS, MASK, CHUNKLEN>
{
    #[inline(always)]
    fn hash_type_length(&self) -> usize {
        4
    }
    #[inline(always)]
    fn store_lookahead(&self) -> usize {
        4
    }

    fn prepare(&mut self, _one_shot: bool, input_size: usize, data: &[u8]) {
        if input_size < CHUNKLEN {
            return;
        }
        self.state = 0;
        let mut i = 0usize;
        while i < CHUNKLEN {
            self.state = Self::hash_rolling_initial(self.state, data[i], self.factor);
            i += JUMP;
        }
    }

    #[inline(always)]
    fn store(&mut self, _data: &[u8], _mask: usize, _ix: usize) {}

    #[inline(always)]
    fn store_range(&mut self, _data: &[u8], _mask: usize, _ix_start: usize, _ix_end: usize) {}

    fn stitch_to_previous_block(
        &mut self,
        num_bytes: usize,
        mut position: usize,
        ringbuffer: &[u8],
        ringbuffer_mask: usize,
    ) {
        let mut available = num_bytes;
        if (position & (JUMP - 1)) != 0 {
            let diff = JUMP - (position & (JUMP - 1));
            available = available.saturating_sub(diff);
            position += diff;
        }
        let position_masked = position & ringbuffer_mask;
        if available > ringbuffer_mask - position_masked {
            available = ringbuffer_mask - position_masked;
        }
        self.prepare(false, available, &ringbuffer[position_masked..]);
        self.next_ix = position;
    }

    #[inline(always)]
    fn prepare_distance_cache(&self, _distance_cache: &mut [i32]) {}

    fn find_longest_match(
        &mut self,
        _common: &mut HasherCommon,
        _dictionary: &BrotliEncoderDictionary,
        data: &[u8],
        ring_buffer_mask: usize,
        _distance_cache: &[i32],
        cur_ix: usize,
        max_length: usize,
        max_backward: usize,
        _dictionary_distance: usize,
        _max_distance: usize,
        out: &mut HasherSearchResult,
    ) {
        let cur_ix_masked = cur_ix & ring_buffer_mask;
        if (cur_ix & (JUMP - 1)) != 0 || max_length < CHUNKLEN {
            return;
        }
        let mut pos = self.next_ix;
        while pos <= cur_ix {
            let code = (self.state & MASK) as usize;
            let rem = data[pos & ring_buffer_mask];
            let add = data[(pos + CHUNKLEN) & ring_buffer_mask];
            self.state =
                Self::hash_rolling(self.state, add, rem, self.factor, self.factor_remove);
            if code < NUM_BUCKETS {
                let found_ix = self.table[code];
                self.table[code] = pos as u32;
                if pos == cur_ix && found_ix != K_INVALID_POS {
                    let backward = (cur_ix as u32).wrapping_sub(found_ix) as usize;
                    if backward <= max_backward {
                        let found_ix_masked = (found_ix as usize) & ring_buffer_mask;
                        let len = find_match_length_with_limit(
                            &data[found_ix_masked..],
                            &data[cur_ix_masked..],
                            max_length,
                        );
                        if len >= 4 && len > out.len {
                            let score = backward_reference_score(len, backward);
                            if score > out.score {
                                out.len = len;
                                out.distance = backward;
                                out.score = score;
                                out.len_code_delta = 0;
                            }
                        }
                    }
                }
            }
            pos += JUMP;
        }
        self.next_ix = cur_ix + JUMP;
    }
}

#[derive(Debug, Clone)]
pub struct HashComposite<A: GenericHasher, B: GenericHasher> {
    pub ha: A,
    pub hb: B,
    pub ha_common: HasherCommon,
    pub hb_common: HasherCommon,
}

impl<A: GenericHasher, B: GenericHasher> HashComposite<A, B> {
    pub fn new(ha: A, hb: B, common: &HasherCommon) -> Self {
        Self {
            ha,
            hb,
            ha_common: common.clone(),
            hb_common: common.clone(),
        }
    }
}

impl<A: GenericHasher, B: GenericHasher> GenericHasher for HashComposite<A, B> {
    #[inline(always)]
    fn hash_type_length(&self) -> usize {
        self.ha.hash_type_length().max(self.hb.hash_type_length())
    }
    #[inline(always)]
    fn store_lookahead(&self) -> usize {
        self.ha.store_lookahead().max(self.hb.store_lookahead())
    }

    fn prepare(&mut self, one_shot: bool, input_size: usize, data: &[u8]) {
        self.ha.prepare(one_shot, input_size, data);
        self.hb.prepare(one_shot, input_size, data);
    }

    #[inline(always)]
    fn store(&mut self, data: &[u8], mask: usize, ix: usize) {
        self.ha.store(data, mask, ix);
        self.hb.store(data, mask, ix);
    }

    #[inline(always)]
    fn store_range(&mut self, data: &[u8], mask: usize, ix_start: usize, ix_end: usize) {
        self.ha.store_range(data, mask, ix_start, ix_end);
        self.hb.store_range(data, mask, ix_start, ix_end);
    }

    fn stitch_to_previous_block(
        &mut self,
        num_bytes: usize,
        position: usize,
        ringbuffer: &[u8],
        ringbuffer_mask: usize,
    ) {
        self.ha
            .stitch_to_previous_block(num_bytes, position, ringbuffer, ringbuffer_mask);
        self.hb
            .stitch_to_previous_block(num_bytes, position, ringbuffer, ringbuffer_mask);
    }

    #[inline(always)]
    fn prepare_distance_cache(&self, distance_cache: &mut [i32]) {
        self.ha.prepare_distance_cache(distance_cache);
        self.hb.prepare_distance_cache(distance_cache);
    }

    fn find_longest_match(
        &mut self,
        common: &mut HasherCommon,
        dictionary: &BrotliEncoderDictionary,
        data: &[u8],
        ring_buffer_mask: usize,
        distance_cache: &[i32],
        cur_ix: usize,
        max_length: usize,
        max_backward: usize,
        dictionary_distance: usize,
        max_distance: usize,
        out: &mut HasherSearchResult,
    ) {
        self.ha_common.dict_num_lookups = common.dict_num_lookups;
        self.ha_common.dict_num_matches = common.dict_num_matches;
        self.ha.find_longest_match(
            &mut self.ha_common,
            dictionary,
            data,
            ring_buffer_mask,
            distance_cache,
            cur_ix,
            max_length,
            max_backward,
            dictionary_distance,
            max_distance,
            out,
        );
        self.hb.find_longest_match(
            &mut self.hb_common,
            dictionary,
            data,
            ring_buffer_mask,
            distance_cache,
            cur_ix,
            max_length,
            max_backward,
            dictionary_distance,
            max_distance,
            out,
        );
        common.dict_num_lookups = self.ha_common.dict_num_lookups;
        common.dict_num_matches = self.ha_common.dict_num_matches;
    }
}

// -------------------------------------------------------------------------
// 6. HashToBinaryTree (H10)
// -------------------------------------------------------------------------
#[derive(Debug, Clone)]
pub struct HashToBinaryTree {
    pub window_mask: usize,
    pub buckets: Vec<u32>,
    pub invalid_pos: u32,
    pub forest: Vec<u32>,
}

impl HashToBinaryTree {
    const BUCKET_BITS: usize = 17;
    const BUCKET_SIZE: usize = 1 << Self::BUCKET_BITS;
    const MAX_TREE_SEARCH_DEPTH: usize = 64;
    const MAX_TREE_COMP_LENGTH: usize = 128;

    pub fn new(params: &BrotliEncoderParams, one_shot: bool, input_size: usize) -> Self {
        let mut num_nodes = 1usize << params.lgwin;
        if one_shot && input_size < num_nodes {
            num_nodes = input_size;
        }
        let window_mask = (1usize << params.lgwin) - 1;
        let invalid_pos = (0u32).wrapping_sub(window_mask as u32);
        Self {
            window_mask,
            buckets: vec![invalid_pos; Self::BUCKET_SIZE],
            invalid_pos,
            forest: vec![0u32; 2 * num_nodes],
        }
    }

    #[inline(always)]
    pub fn hash_type_length(&self) -> usize {
        4
    }
    #[inline(always)]
    pub fn store_lookahead(&self) -> usize {
        Self::MAX_TREE_COMP_LENGTH
    }

    #[inline(always)]
    fn hash_bytes(data: &[u8], pos: usize) -> usize {
        let h = read_u32_le(data, pos).wrapping_mul(K_HASH_MUL_32);
        (h >> (32 - Self::BUCKET_BITS)) as usize
    }

    #[inline(always)]
    fn left_child_index(&self, pos: usize) -> usize {
        2 * (pos & self.window_mask)
    }

    #[inline(always)]
    fn right_child_index(&self, pos: usize) -> usize {
        2 * (pos & self.window_mask) + 1
    }

    pub fn prepare(&mut self, _one_shot: bool, _input_size: usize, _data: &[u8]) {
        self.buckets.fill(self.invalid_pos);
    }

    fn store_and_find_matches(
        &mut self,
        data: &[u8],
        cur_ix: usize,
        ring_buffer_mask: usize,
        max_length: usize,
        max_backward: usize,
        mut best_len: Option<&mut usize>,
        mut matches: Option<&mut [BackwardMatch]>,
    ) -> usize {
        let cur_ix_masked = cur_ix & ring_buffer_mask;
        let max_comp_len = max_length.min(Self::MAX_TREE_COMP_LENGTH);
        let should_reroot_tree = max_length >= Self::MAX_TREE_COMP_LENGTH;
        let key = Self::hash_bytes(data, cur_ix_masked);
        let mut prev_ix = self.buckets[key] as usize;
        let mut node_left = self.left_child_index(cur_ix);
        let mut node_right = self.right_child_index(cur_ix);
        let mut best_len_left = 0usize;
        let mut best_len_right = 0usize;
        let mut matches_written = 0usize;

        if should_reroot_tree {
            self.buckets[key] = cur_ix as u32;
        }
        let mut depth_remaining = Self::MAX_TREE_SEARCH_DEPTH;
        loop {
            let backward = cur_ix.wrapping_sub(prev_ix);
            let prev_ix_masked = prev_ix & ring_buffer_mask;
            if backward == 0 || backward > max_backward || depth_remaining == 0 {
                if should_reroot_tree {
                    self.forest[node_left] = self.invalid_pos;
                    self.forest[node_right] = self.invalid_pos;
                }
                break;
            }
            depth_remaining -= 1;
            let cur_len = best_len_left.min(best_len_right);
            let len = cur_len
                + find_match_length_with_limit(
                    &data[cur_ix_masked + cur_len..],
                    &data[prev_ix_masked + cur_len..],
                    max_length - cur_len,
                );
            if let (Some(bl), Some(ms)) = (best_len.as_deref_mut(), matches.as_deref_mut()) {
                if len > *bl {
                    *bl = len;
                    ms[matches_written] = BackwardMatch::new(backward, len);
                    matches_written += 1;
                }
            }
            if len >= max_comp_len {
                if should_reroot_tree {
                    self.forest[node_left] = self.forest[self.left_child_index(prev_ix)];
                    self.forest[node_right] = self.forest[self.right_child_index(prev_ix)];
                }
                break;
            }
            if data[cur_ix_masked + len] > data[prev_ix_masked + len] {
                best_len_left = len;
                if should_reroot_tree {
                    self.forest[node_left] = prev_ix as u32;
                }
                node_left = self.right_child_index(prev_ix);
                prev_ix = self.forest[node_left] as usize;
            } else {
                best_len_right = len;
                if should_reroot_tree {
                    self.forest[node_right] = prev_ix as u32;
                }
                node_right = self.left_child_index(prev_ix);
                prev_ix = self.forest[node_right] as usize;
            }
        }
        matches_written
    }

    pub fn find_all_matches(
        &mut self,
        dictionary: &BrotliEncoderDictionary,
        data: &[u8],
        ring_buffer_mask: usize,
        cur_ix: usize,
        max_length: usize,
        max_backward: usize,
        dictionary_distance: usize,
        params: &BrotliEncoderParams,
        matches: &mut [BackwardMatch],
    ) -> usize {
        let cur_ix_masked = cur_ix & ring_buffer_mask;
        let mut best_len = 1usize;
        let short_match_max_backward = if params.quality != HQ_ZOPFLIFICATION_QUALITY {
            16
        } else {
            64
        };
        let stop = cur_ix.saturating_sub(short_match_max_backward);
        let mut dict_matches = [K_INVALID_MATCH; BROTLI_MAX_STATIC_DICTIONARY_MATCH_LEN + 1];
        let mut m_count = 0usize;

        if cur_ix > 0 {
            let mut i = cur_ix - 1;
            while i > stop && best_len <= 2 {
                let backward = cur_ix - i;
                if backward > max_backward {
                    break;
                }
                let prev_ix = i & ring_buffer_mask;
                if data[cur_ix_masked] == data[prev_ix]
                    && data[cur_ix_masked + 1] == data[prev_ix + 1]
                {
                    let len = find_match_length_with_limit(
                        &data[prev_ix..],
                        &data[cur_ix_masked..],
                        max_length,
                    );
                    if len > best_len {
                        best_len = len;
                        matches[m_count] = BackwardMatch::new(backward, len);
                        m_count += 1;
                    }
                }
                if i == 0 {
                    break;
                }
                i -= 1;
            }
        }

        if best_len < max_length {
            let added = self.store_and_find_matches(
                data,
                cur_ix,
                ring_buffer_mask,
                max_length,
                max_backward,
                Some(&mut best_len),
                Some(&mut matches[m_count..]),
            );
            m_count += added;
        }

        let minlen = 4usize.max(best_len + 1);
        if brotli_find_all_static_dictionary_matches(
            dictionary,
            &data[cur_ix_masked..],
            minlen,
            max_length,
            &mut dict_matches,
        ) {
            let maxlen = BROTLI_MAX_STATIC_DICTIONARY_MATCH_LEN.min(max_length);
            for l in minlen..=maxlen {
                let dict_id = dict_matches[l];
                if dict_id < K_INVALID_MATCH {
                    let distance = dictionary_distance + ((dict_id >> 5) as usize) + 1;
                    if distance <= params.dist.max_distance {
                        matches[m_count] =
                            BackwardMatch::new_dict(distance, l, (dict_id & 31) as usize);
                        m_count += 1;
                    }
                }
            }
        }
        m_count
    }

    #[inline(always)]
    pub fn store(&mut self, data: &[u8], mask: usize, ix: usize) {
        let max_backward = self.window_mask - BROTLI_WINDOW_GAP + 1;
        self.store_and_find_matches(
            data,
            ix,
            mask,
            Self::MAX_TREE_COMP_LENGTH,
            max_backward,
            None,
            None,
        );
    }

    pub fn store_range(&mut self, data: &[u8], mask: usize, ix_start: usize, ix_end: usize) {
        let mut i = ix_start;
        let mut j = ix_start;
        if ix_start + 63 <= ix_end {
            i = ix_end - 63;
        }
        if ix_start + 512 <= i {
            while j < i {
                self.store(data, mask, j);
                j += 8;
            }
        }
        while i < ix_end {
            self.store(data, mask, i);
            i += 1;
        }
    }

    pub fn stitch_to_previous_block(
        &mut self,
        num_bytes: usize,
        position: usize,
        ringbuffer: &[u8],
        ringbuffer_mask: usize,
    ) {
        if num_bytes >= self.hash_type_length() - 1 && position >= Self::MAX_TREE_COMP_LENGTH {
            let i_start = position - Self::MAX_TREE_COMP_LENGTH + 1;
            let i_end = position.min(i_start + num_bytes);
            for i in i_start..i_end {
                let max_backward =
                    self.window_mask - (BROTLI_WINDOW_GAP - 1).max(position - i);
                self.store_and_find_matches(
                    ringbuffer,
                    i,
                    ringbuffer_mask,
                    Self::MAX_TREE_COMP_LENGTH,
                    max_backward,
                    None,
                    None,
                );
            }
        }
    }
}

// =========================================================================
// Unified Hasher wrapper
// =========================================================================
pub type H2 = HashLongestMatchQuickly<16, 0, 5, true>;
pub type H3 = HashLongestMatchQuickly<16, 1, 5, false>;
pub type H4 = HashLongestMatchQuickly<17, 2, 5, true>;
pub type H54 = HashLongestMatchQuickly<20, 2, 7, false>;
pub type H40 = HashForgetfulChain<15, 1, 16, 4>;
pub type H41 = HashForgetfulChain<15, 1, 16, 10>;
pub type H42 = HashForgetfulChain<15, 512, 9, 16>;
pub type HRollingFast = HashRolling<4, 16777216, { (16777216u32 * 64) - 1 }, 32>;
pub type HRolling = HashRolling<1, 16777216, { (16777216u32 * 64) - 1 }, 32>;
pub type H35 = HashComposite<H3, HRollingFast>;
pub type H55 = HashComposite<H54, HRollingFast>;
pub type H65 = HashComposite<HashLongestMatch64, HRolling>;

#[derive(Debug, Clone)]
pub enum HasherInner {
    None,
    H2(H2),
    H3(H3),
    H4(H4),
    H5(HashLongestMatch),
    H6(HashLongestMatch64),
    H58(HashLongestMatchSimd<false>),
    H59(HashLongestMatchSimd<true>),
    H68(HashLongestMatch64Simd),
    H40(H40),
    H41(H41),
    H42(H42),
    H54(H54),
    H35(Box<H35>),
    H55(Box<H55>),
    H65(Box<H65>),
    H10(HashToBinaryTree),
}

#[derive(Debug, Clone)]
pub struct Hasher {
    pub common: HasherCommon,
    pub inner: HasherInner,
}

impl Default for Hasher {
    fn default() -> Self {
        Self::new()
    }
}

impl Hasher {
    pub fn new() -> Self {
        Self {
            common: HasherCommon::default(),
            inner: HasherInner::None,
        }
    }

    pub fn reset(&mut self) {
        self.common.is_prepared = false;
    }

    pub fn setup(
        &mut self,
        params: &mut BrotliEncoderParams,
        data: &[u8],
        position: usize,
        input_size: usize,
        is_last: bool,
    ) {
        let one_shot = position == 0 && is_last;
        if !self.common.is_setup {
            let mut hparams = params.hasher;
            choose_hasher(params, &mut hparams);
            params.hasher = hparams;
            self.common.params = params.hasher;
            self.common.is_setup = true;
            self.common.is_prepared = false;
            self.common.dict_num_lookups = 0;
            self.common.dict_num_matches = 0;
            self.inner = match params.hasher.type_ {
                2 => HasherInner::H2(H2::new()),
                3 => HasherInner::H3(H3::new()),
                4 => HasherInner::H4(H4::new()),
                5 => HasherInner::H5(HashLongestMatch::new(&params.hasher)),
                6 => HasherInner::H6(HashLongestMatch64::new(&params.hasher)),
                58 => HasherInner::H58(HashLongestMatchSimd::<false>::new(&params.hasher)),
                59 => HasherInner::H59(HashLongestMatchSimd::<true>::new(&params.hasher)),
                68 => HasherInner::H68(HashLongestMatch64Simd::new(&params.hasher)),
                40 => HasherInner::H40(H40::new(params)),
                41 => HasherInner::H41(H41::new(params)),
                42 => HasherInner::H42(H42::new(params)),
                54 => HasherInner::H54(H54::new()),
                35 => HasherInner::H35(Box::new(HashComposite::new(
                    H3::new(),
                    HRollingFast::new(),
                    &self.common,
                ))),
                55 => HasherInner::H55(Box::new(HashComposite::new(
                    H54::new(),
                    HRollingFast::new(),
                    &self.common,
                ))),
                65 => HasherInner::H65(Box::new(HashComposite::new(
                    HashLongestMatch64::new(&params.hasher),
                    HRolling::new(),
                    &self.common,
                ))),
                10 => HasherInner::H10(HashToBinaryTree::new(params, one_shot, input_size)),
                _ => HasherInner::None,
            };
        }
        if !self.common.is_prepared {
            match &mut self.inner {
                HasherInner::H2(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H3(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H4(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H5(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H6(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H58(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H59(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H68(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H40(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H41(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H42(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H54(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H35(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H55(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H65(h) => h.prepare(one_shot, input_size, data),
                HasherInner::H10(h) => h.prepare(one_shot, input_size, data),
                HasherInner::None => {}
            }
            self.common.is_prepared = true;
        }
    }

    pub fn init_or_stitch_to_previous_block(
        &mut self,
        params: &mut BrotliEncoderParams,
        data: &[u8],
        mask: usize,
        position: usize,
        input_size: usize,
        is_last: bool,
    ) {
        self.setup(params, data, position, input_size, is_last);
        match &mut self.inner {
            HasherInner::H2(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H3(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H4(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H5(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H6(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H58(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H59(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H68(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H40(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H41(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H42(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H54(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H35(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H55(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H65(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::H10(h) => h.stitch_to_previous_block(input_size, position, data, mask),
            HasherInner::None => {}
        }
    }
}
