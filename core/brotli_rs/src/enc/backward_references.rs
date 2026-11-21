#![allow(dead_code)]

use crate::common::constants::*;
use crate::common::context::{ContextLut, brotli_context};
use crate::common::shared_dictionary::SHARED_BROTLI_MAX_COMPOUND_DICTS;
use crate::enc::brotli_bit_stream::K_IS_BASE64;
use crate::enc::command::Command;
use crate::enc::hash::{
    BROTLI_SCORE_BASE, GenericHasher, Hasher, HasherCommon, HasherInner, HasherSearchResult,
    PreparedDictionaryProbe, lookup_compound_dictionary_match,
    lookup_compound_dictionary_match_opt, minimum_better_length,
    prefetch_compound_dictionary_match_opt,
};
use crate::enc::metablock::Base64Region;
use crate::enc::params::BrotliEncoderParams;
use crate::enc::quality::{
    MIN_QUALITY_FOR_EXTENSIVE_REFERENCE_SEARCH, literal_spree_length_for_sparse_search,
};

const K_BASE64_TRIGGER_LEN: usize = 8;
const K_BASE64_TRIGGER64: u64 = (0x2C343665u64 << 32) | 0x7361623Bu64; // ";base64,"

#[inline(always)]
fn is_base64_char(c: u8) -> bool {
    K_IS_BASE64[c as usize] != 0
}

#[inline(always)]
fn match_trigger(ringbuffer: &[u8], mask: usize, pos: usize) -> bool {
    const TRIGGER: &[u8; 8] = b";base64,";
    for i in 0..K_BASE64_TRIGGER_LEN {
        if ringbuffer[(pos + i) & mask] != TRIGGER[i] {
            return false;
        }
    }
    true
}

fn find_next_base64_trigger(ringbuffer: &[u8], mask: usize, mut pos: usize, end: usize) -> usize {
    let ringbuffer_size = mask + 1;
    while pos + K_BASE64_TRIGGER_LEN <= end {
        let pos_index = pos & mask;
        let contiguous_len = ringbuffer_size - pos_index;
        let max_scan_len = end - pos;
        let scan_len = contiguous_len.min(max_scan_len);
        let slice = &ringbuffer[pos_index..pos_index + scan_len];
        if let Some(offset) = slice.iter().position(|&b| b == b';') {
            if offset + K_BASE64_TRIGGER_LEN <= scan_len {
                let w = u64::from_le_bytes(
                    ringbuffer[pos_index + offset..pos_index + offset + 8]
                        .try_into()
                        .unwrap(),
                );
                if w == K_BASE64_TRIGGER64 {
                    return pos + offset;
                }
            } else if pos + offset + K_BASE64_TRIGGER_LEN <= end {
                if match_trigger(ringbuffer, mask, pos + offset) {
                    return pos + offset;
                }
            } else {
                return end;
            }
            pos += offset + 1;
        } else {
            pos += scan_len;
        }
    }
    end
}

#[inline(always)]
fn compare_ringbuffer(
    ringbuffer: &[u8],
    mask: usize,
    mut pos1: usize,
    mut pos2: usize,
    mut len: usize,
) -> bool {
    let idx1 = pos1 & mask;
    let idx2 = pos2 & mask;
    if idx1 + len <= mask + 1 && idx2 + len <= mask + 1 {
        return ringbuffer[idx1..idx1 + len] == ringbuffer[idx2..idx2 + len];
    }
    while len > 0 {
        let chunk1 = mask + 1 - (pos1 & mask);
        let chunk2 = mask + 1 - (pos2 & mask);
        let chunk = len.min(chunk1.min(chunk2));
        let a = &ringbuffer[pos1 & mask..(pos1 & mask) + chunk];
        let b = &ringbuffer[pos2 & mask..(pos2 & mask) + chunk];
        if a != b {
            return false;
        }
        pos1 += chunk;
        pos2 += chunk;
        len -= chunk;
    }
    true
}

#[inline(always)]
pub fn compute_distance_code(distance: usize, max_distance: usize, dist_cache: &[i32]) -> usize {
    if distance <= max_distance {
        let distance_plus_3 = distance + 3;
        let offset0 = distance_plus_3.wrapping_sub(dist_cache[0] as usize);
        let offset1 = distance_plus_3.wrapping_sub(dist_cache[1] as usize);
        if distance == dist_cache[0] as usize {
            return 0;
        } else if distance == dist_cache[1] as usize {
            return 1;
        } else if offset0 < 7 {
            return (0x9750468 >> (4 * offset0)) & 0xF;
        } else if offset1 < 7 {
            return (0xFDB1ACE >> (4 * offset1)) & 0xF;
        } else if distance == dist_cache[2] as usize {
            return 2;
        } else if distance == dist_cache[3] as usize {
            return 3;
        }
    }
    distance + BROTLI_NUM_DISTANCE_SHORT_CODES - 1
}

fn create_backward_references_impl<
    H: GenericHasher,
    const ENABLE_COMPOUND_DICTIONARY: bool,
    const OPT: bool,
>(
    num_bytes: usize,
    mut position: usize,
    ringbuffer: &[u8],
    ringbuffer_mask: usize,
    literal_context_lut: ContextLut,
    params: &BrotliEncoderParams,
    hasher_common: &mut HasherCommon,
    privat: &mut H,
    dist_cache: &mut [i32],
    last_insert_len: &mut usize,
    commands: &mut Vec<Command>,
    num_commands: &mut usize,
    num_literals: &mut usize,
) {
    let max_backward_limit = brotli_max_backward_limit(params.lgwin as u32);
    let position_offset = params.stream_offset;
    let orig_commands_len = commands.len();
    let mut insert_length = *last_insert_len;
    let pos_end = position + num_bytes;
    let store_end = if num_bytes >= privat.store_lookahead() {
        position + num_bytes - privat.store_lookahead() + 1
    } else {
        position
    };

    let random_heuristics_window_size = literal_spree_length_for_sparse_search(params);
    let mut apply_random_heuristics = position + random_heuristics_window_size;
    let gap = params.dictionary.compound.total_size;
    let k_min_score = BROTLI_SCORE_BASE + 100;
    let mut dict_probes = [PreparedDictionaryProbe::default(); SHARED_BROTLI_MAX_COMPOUND_DICTS + 1];

    privat.prepare_distance_cache(dist_cache);

    let mut next_base64_pos = pos_end;
    let mut base64_end = 0usize;
    if params.base64_mode != 0
        && hasher_common.base64_regions.len() < params.max_base64_regions
    {
        next_base64_pos =
            find_next_base64_trigger(ringbuffer, ringbuffer_mask, position, pos_end);
    }

    while position + privat.hash_type_length() < pos_end {
        if position >= next_base64_pos {
            let mut handle_trigger = true;
            if !OPT && position > next_base64_pos {
                let mut payload_end = next_base64_pos + K_BASE64_TRIGGER_LEN;
                while payload_end < pos_end
                    && is_base64_char(ringbuffer[payload_end & ringbuffer_mask])
                {
                    payload_end += 1;
                }
                if payload_end - (next_base64_pos + K_BASE64_TRIGGER_LEN)
                    >= params.min_base64_region_len
                {
                    base64_end = payload_end;
                }
                next_base64_pos = find_next_base64_trigger(
                    ringbuffer,
                    ringbuffer_mask,
                    position.max(payload_end),
                    pos_end,
                );
                if position < next_base64_pos {
                    handle_trigger = false;
                }
            }
            if handle_trigger {
                let mut scan_pos = position + K_BASE64_TRIGGER_LEN;
                let mut first_equal_pos = 0usize;
                while scan_pos < pos_end {
                    let c = ringbuffer[scan_pos & ringbuffer_mask];
                    if is_base64_char(c) {
                        if first_equal_pos != 0 {
                            scan_pos = first_equal_pos;
                            break;
                        }
                        scan_pos += 1;
                    } else if c == b'=' {
                        if first_equal_pos == 0 {
                            first_equal_pos = scan_pos;
                        }
                        scan_pos += 1;
                    } else {
                        break;
                    }
                }
                let start_pos = position + K_BASE64_TRIGGER_LEN;
                let mut length = scan_pos - start_pos;
                while length > 0
                    && ringbuffer[(start_pos + length - 1) & ringbuffer_mask] == b'='
                {
                    length -= 1;
                }
                if OPT {
                    if length > 0 {
                        hasher_common.base64_regions.push(Base64Region {
                            start_literal_pos: start_pos,
                            length,
                        });
                    }
                    insert_length += scan_pos - position;
                    position = scan_pos;
                    if hasher_common.base64_regions.len() < params.max_base64_regions {
                        next_base64_pos =
                            find_next_base64_trigger(ringbuffer, ringbuffer_mask, position, pos_end);
                    } else {
                        next_base64_pos = pos_end;
                    }
                    continue;
                } else {
                    let copy_len = scan_pos - position;
                    let mut is_dupe = false;
                    let mut dupe_dist = 0usize;
                    if length > 0 && !hasher_common.base64_regions.is_empty() {
                        for reg in hasher_common.base64_regions.iter().rev() {
                            if reg.length != length {
                                continue;
                            }
                            let prev_pos = reg.start_literal_pos - K_BASE64_TRIGGER_LEN;
                            let distance = position - prev_pos;
                            if distance == 0 || distance > max_backward_limit {
                                continue;
                            }
                            if compare_ringbuffer(
                                ringbuffer,
                                ringbuffer_mask,
                                prev_pos,
                                position,
                                copy_len,
                            ) {
                                is_dupe = true;
                                dupe_dist = distance;
                                break;
                            }
                        }
                    }
                    if is_dupe {
                        let dictionary_start =
                            (position + position_offset).min(max_backward_limit);
                        let distance_code =
                            compute_distance_code(dupe_dist, dictionary_start + gap, dist_cache);
                        if dupe_dist <= (dictionary_start + gap) && distance_code > 0 {
                            dist_cache[3] = dist_cache[2];
                            dist_cache[2] = dist_cache[1];
                            dist_cache[1] = dist_cache[0];
                            dist_cache[0] = dupe_dist as i32;
                            privat.prepare_distance_cache(dist_cache);
                        }
                        commands.push(Command::new(
                            &params.dist,
                            insert_length,
                            copy_len,
                            0,
                            distance_code,
                        ));
                        *num_literals += insert_length;
                        insert_length = 0;
                        position = scan_pos;
                        apply_random_heuristics =
                            position + 2 * copy_len + random_heuristics_window_size;
                        if hasher_common.base64_regions.len() < params.max_base64_regions {
                            next_base64_pos = find_next_base64_trigger(
                                ringbuffer,
                                ringbuffer_mask,
                                position,
                                pos_end,
                            );
                        } else {
                            next_base64_pos = pos_end;
                        }
                        continue;
                    }
                    if length >= params.min_base64_region_len && length > 0 {
                        hasher_common.base64_regions.push(Base64Region {
                            start_literal_pos: start_pos,
                            length,
                        });
                        insert_length += scan_pos - position;
                        position = scan_pos;
                        if hasher_common.base64_regions.len() < params.max_base64_regions {
                            next_base64_pos = find_next_base64_trigger(
                                ringbuffer,
                                ringbuffer_mask,
                                position,
                                pos_end,
                            );
                        } else {
                            next_base64_pos = pos_end;
                        }
                        continue;
                    } else {
                        next_base64_pos = find_next_base64_trigger(
                            ringbuffer,
                            ringbuffer_mask,
                            position + 1,
                            pos_end,
                        );
                    }
                }
            }
        }

        if !OPT && position < base64_end && position > apply_random_heuristics {
            insert_length += base64_end - position;
            position = base64_end;
            continue;
        }

        let mut max_length = pos_end - position;
        let mut max_distance = position.min(max_backward_limit);
        let mut dictionary_start = (position + position_offset).min(max_backward_limit);
        #[allow(unused_assignments)]
        let (mut p1, mut p2, mut dict_id) = if params.dictionary.contextual.context_based {
            let p1 = if position >= 1 {
                ringbuffer[(position - 1) & ringbuffer_mask]
            } else {
                0
            };
            let p2 = if position >= 2 {
                ringbuffer[(position - 2) & ringbuffer_mask]
            } else {
                0
            };
            let dict_id = params.dictionary.contextual.context_map
                [brotli_context(p1, p2, literal_context_lut) as usize] as usize;
            (p1, p2, dict_id)
        } else {
            (0, 0, 0)
        };
        let mut sr = HasherSearchResult {
            len: 0,
            len_code_delta: 0,
            distance: 0,
            score: k_min_score,
        };
        if OPT && ENABLE_COMPOUND_DICTIONARY {
            prefetch_compound_dictionary_match_opt(
                &params.dictionary.compound,
                ringbuffer,
                ringbuffer_mask,
                position,
                &mut dict_probes,
            );
        }
        privat.find_longest_match(
            hasher_common,
            params.dictionary.contextual.dict_for(dict_id),
            ringbuffer,
            ringbuffer_mask,
            dist_cache,
            position,
            max_length,
            max_distance,
            dictionary_start + gap,
            params.dist.max_distance,
            &mut sr,
        );
        if ENABLE_COMPOUND_DICTIONARY && (OPT || position >= base64_end) {
            if OPT {
                lookup_compound_dictionary_match_opt(
                    &params.dictionary.compound,
                    &dict_probes,
                    ringbuffer,
                    ringbuffer_mask,
                    dist_cache,
                    position,
                    max_length,
                    dictionary_start,
                    params.dist.max_distance,
                    &mut sr,
                );
            } else {
                lookup_compound_dictionary_match(
                    &params.dictionary.compound,
                    ringbuffer,
                    ringbuffer_mask,
                    dist_cache,
                    position,
                    max_length,
                    dictionary_start,
                    params.dist.max_distance,
                    &mut sr,
                );
            }
        }

        if sr.score > k_min_score {
            let mut delayed_backward_references_in_row = 0i32;
            max_length -= 1;
            loop {
                const COST_DIFF_LAZY: usize = 175;
                let init_len = if params.quality < MIN_QUALITY_FOR_EXTENSIVE_REFERENCE_SEARCH {
                    (sr.len - 1).min(max_length)
                } else if OPT {
                    minimum_better_length(sr.score + COST_DIFF_LAZY - 1).min(max_length)
                } else {
                    0
                };
                let mut sr2 = HasherSearchResult {
                    len: init_len,
                    len_code_delta: 0,
                    distance: 0,
                    score: k_min_score,
                };
                max_distance = (position + 1).min(max_backward_limit);
                dictionary_start = (position + 1 + position_offset).min(max_backward_limit);
                if params.dictionary.contextual.context_based {
                    p2 = p1;
                    p1 = ringbuffer[position & ringbuffer_mask];
                    dict_id = params.dictionary.contextual.context_map
                        [brotli_context(p1, p2, literal_context_lut) as usize]
                        as usize;
                }
                if OPT && ENABLE_COMPOUND_DICTIONARY {
                    prefetch_compound_dictionary_match_opt(
                        &params.dictionary.compound,
                        ringbuffer,
                        ringbuffer_mask,
                        position + 1,
                        &mut dict_probes,
                    );
                }
                privat.find_longest_match(
                    hasher_common,
                    params.dictionary.contextual.dict_for(dict_id),
                    ringbuffer,
                    ringbuffer_mask,
                    dist_cache,
                    position + 1,
                    max_length,
                    max_distance,
                    dictionary_start + gap,
                    params.dist.max_distance,
                    &mut sr2,
                );
                if ENABLE_COMPOUND_DICTIONARY && (OPT || position + 1 >= base64_end) {
                    if OPT {
                        lookup_compound_dictionary_match_opt(
                            &params.dictionary.compound,
                            &dict_probes,
                            ringbuffer,
                            ringbuffer_mask,
                            dist_cache,
                            position + 1,
                            max_length,
                            dictionary_start,
                            params.dist.max_distance,
                            &mut sr2,
                        );
                    } else {
                        lookup_compound_dictionary_match(
                            &params.dictionary.compound,
                            ringbuffer,
                            ringbuffer_mask,
                            dist_cache,
                            position + 1,
                            max_length,
                            dictionary_start,
                            params.dist.max_distance,
                            &mut sr2,
                        );
                    }
                }
                if sr2.score >= sr.score + COST_DIFF_LAZY {
                    position += 1;
                    insert_length += 1;
                    sr = sr2;
                    delayed_backward_references_in_row += 1;
                    if delayed_backward_references_in_row < 4
                        && position + privat.hash_type_length() < pos_end
                    {
                        max_length -= 1;
                        continue;
                    }
                }
                break;
            }
            apply_random_heuristics = position + 2 * sr.len + random_heuristics_window_size;
            dictionary_start = (position + position_offset).min(max_backward_limit);
            let distance_code =
                compute_distance_code(sr.distance, dictionary_start + gap, dist_cache);
            if sr.distance <= (dictionary_start + gap) && distance_code > 0 {
                dist_cache[3] = dist_cache[2];
                dist_cache[2] = dist_cache[1];
                dist_cache[1] = dist_cache[0];
                dist_cache[0] = sr.distance as i32;
                privat.prepare_distance_cache(dist_cache);
            }
            commands.push(Command::new(
                &params.dist,
                insert_length,
                sr.len,
                sr.len_code_delta,
                distance_code,
            ));
            *num_literals += insert_length;
            insert_length = 0;

            let mut range_start = position + 2;
            let range_end = (position + sr.len).min(store_end);
            if sr.distance < (sr.len >> 2) {
                range_start = range_end.min(range_start.max(position + sr.len - (sr.distance << 2)));
            }
            privat.store_range(ringbuffer, ringbuffer_mask, range_start, range_end);
            position += sr.len;
        } else {
            insert_length += 1;
            position += 1;
            if position > apply_random_heuristics {
                if position > apply_random_heuristics + 4 * random_heuristics_window_size {
                    let k_margin = (privat.store_lookahead() - 1).max(4);
                    let pos_jump = (position + 16).min(pos_end - k_margin);
                    while position < pos_jump {
                        privat.store(ringbuffer, ringbuffer_mask, position);
                        insert_length += 4;
                        position += 4;
                    }
                } else {
                    let k_margin = (privat.store_lookahead() - 1).max(2);
                    let pos_jump = (position + 8).min(pos_end - k_margin);
                    while position < pos_jump {
                        privat.store(ringbuffer, ringbuffer_mask, position);
                        insert_length += 2;
                        position += 2;
                    }
                }
            }
        }
    }
    insert_length += pos_end - position;
    *last_insert_len = insert_length;
    *num_commands += commands.len() - orig_commands_len;
}

pub fn brotli_create_backward_references(
    num_bytes: usize,
    position: usize,
    ringbuffer: &[u8],
    ringbuffer_mask: usize,
    literal_context_lut: ContextLut,
    params: &BrotliEncoderParams,
    hasher: &mut Hasher,
    dist_cache: &mut [i32],
    last_insert_len: &mut usize,
    commands: &mut Vec<Command>,
    num_commands: &mut usize,
    num_literals: &mut usize,
) {
    let has_compound = params.dictionary.compound.num_chunks != 0;
    let common = &mut hasher.common;
    macro_rules! dispatch {
        ($h:expr, $opt:expr) => {
            if has_compound {
                create_backward_references_impl::<_, true, $opt>(
                    num_bytes,
                    position,
                    ringbuffer,
                    ringbuffer_mask,
                    literal_context_lut,
                    params,
                    common,
                    $h,
                    dist_cache,
                    last_insert_len,
                    commands,
                    num_commands,
                    num_literals,
                );
            } else {
                create_backward_references_impl::<_, false, $opt>(
                    num_bytes,
                    position,
                    ringbuffer,
                    ringbuffer_mask,
                    literal_context_lut,
                    params,
                    common,
                    $h,
                    dist_cache,
                    last_insert_len,
                    commands,
                    num_commands,
                    num_literals,
                );
            }
        };
    }

    match &mut hasher.inner {
        HasherInner::H2(h) => dispatch!(h, false),
        HasherInner::H3(h) => dispatch!(h, false),
        HasherInner::H4(h) => dispatch!(h, false),
        HasherInner::H5(h) => dispatch!(h, false),
        HasherInner::H6(h) => dispatch!(h, false),
        HasherInner::H58(h) => dispatch!(h, false),
        HasherInner::H59(h) => dispatch!(h, true),
        HasherInner::H68(h) => dispatch!(h, false),
        HasherInner::H40(h) => dispatch!(h, false),
        HasherInner::H41(h) => dispatch!(h, false),
        HasherInner::H42(h) => dispatch!(h, false),
        HasherInner::H54(h) => dispatch!(h, false),
        HasherInner::H35(h) => dispatch!(&mut **h, false),
        HasherInner::H55(h) => dispatch!(&mut **h, false),
        HasherInner::H65(h) => dispatch!(&mut **h, false),
        _ => unreachable!("Unexpected hasher in CreateBackwardReferences"),
    }
}
