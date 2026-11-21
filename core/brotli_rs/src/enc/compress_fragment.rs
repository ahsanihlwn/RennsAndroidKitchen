#![allow(dead_code)]

use crate::common::constants::*;
use crate::enc::brotli_bit_stream::brotli_build_and_store_huffman_tree_fast;
use crate::enc::entropy_encode::{
    HuffmanTree, brotli_convert_bit_depths_to_symbols, brotli_create_huffman_tree,
};
use crate::enc::fast_log::{fast_log2, log2_floor_non_zero};
use crate::enc::find_match_length::find_match_length_with_limit;
use crate::enc::static_dict::K_HASH_MUL_32;
use crate::enc::write_bits::brotli_write_bits;
use crate::enc::brotli_bit_stream::brotli_store_huffman_tree;

const MAX_DISTANCE: isize = brotli_max_backward_limit(18) as isize;

pub struct BrotliOnePassArena {
    pub lit_depth: [u8; 256],
    pub lit_bits: [u16; 256],
    pub cmd_depth: [u8; 128],
    pub cmd_bits: [u16; 128],
    pub cmd_histo: [u32; 128],
    pub cmd_code: [u8; 512],
    pub cmd_code_numbits: usize,
    pub tree: [HuffmanTree; 2 * BROTLI_NUM_LITERAL_SYMBOLS + 1],
    pub histogram: [u32; 256],
    pub tmp_depth: [u8; BROTLI_NUM_COMMAND_SYMBOLS],
    pub tmp_bits: [u16; 64],
}

impl Default for BrotliOnePassArena {
    fn default() -> Self {
        Self::new()
    }
}

impl BrotliOnePassArena {
    pub fn new() -> Self {
        Self {
            lit_depth: [0; 256],
            lit_bits: [0; 256],
            cmd_depth: [0; 128],
            cmd_bits: [0; 128],
            cmd_histo: [0; 128],
            cmd_code: [0; 512],
            cmd_code_numbits: 0,
            tree: [HuffmanTree::default(); 2 * BROTLI_NUM_LITERAL_SYMBOLS + 1],
            histogram: [0; 256],
            tmp_depth: [0; BROTLI_NUM_COMMAND_SYMBOLS],
            tmp_bits: [0; 64],
        }
    }
}

#[inline(always)]
fn load_u64_le(data: &[u8], pos: usize) -> u64 {
    if pos + 8 <= data.len() {
        u64::from_le_bytes(data[pos..pos + 8].try_into().unwrap())
    } else {
        let mut buf = [0u8; 8];
        let avail = data.len().saturating_sub(pos);
        buf[..avail].copy_from_slice(&data[pos..pos + avail]);
        u64::from_le_bytes(buf)
    }
}

#[inline(always)]
fn load_u32_le(data: &[u8], pos: usize) -> u32 {
    if pos + 4 <= data.len() {
        u32::from_le_bytes(data[pos..pos + 4].try_into().unwrap())
    } else {
        let mut buf = [0u8; 4];
        let avail = data.len().saturating_sub(pos);
        buf[..avail].copy_from_slice(&data[pos..pos + avail]);
        u32::from_le_bytes(buf)
    }
}

#[inline(always)]
fn hash(data: &[u8], pos: usize, shift: usize) -> u32 {
    let h = (load_u64_le(data, pos) << 24).wrapping_mul(K_HASH_MUL_32 as u64);
    (h >> shift) as u32
}

#[inline(always)]
fn hash_bytes_at_offset(v: u64, offset: usize, shift: usize) -> u32 {
    let h = ((v >> (8 * offset)) << 24).wrapping_mul(K_HASH_MUL_32 as u64);
    (h >> shift) as u32
}

#[inline(always)]
fn is_match(data: &[u8], p1: isize, p2: isize) -> bool {
    if p1 < 0 || p2 < 0 {
        return false;
    }
    let u1 = p1 as usize;
    let u2 = p2 as usize;
    if u1 + 5 > data.len() || u2 + 5 > data.len() {
        return false;
    }
    load_u32_le(data, u1) == load_u32_le(data, u2) && data[u1 + 4] == data[u2 + 4]
}

fn build_and_store_literal_prefix_code(
    s: &mut BrotliOnePassArena,
    input: &[u8],
    input_size: usize,
    storage_ix: &mut usize,
    storage: &mut [u8],
) -> usize {
    s.histogram.fill(0);
    let mut histogram_total;
    if input_size < (1 << 15) {
        for &b in &input[..input_size] {
            s.histogram[b as usize] += 1;
        }
        histogram_total = input_size;
        for h in &mut s.histogram {
            let adjust = 2 * (*h).min(11);
            *h += adjust;
            histogram_total += adjust as usize;
        }
    } else {
        const K_SAMPLE_RATE: usize = 29;
        let mut i = 0usize;
        while i < input_size {
            s.histogram[input[i] as usize] += 1;
            i += K_SAMPLE_RATE;
        }
        histogram_total = (input_size + K_SAMPLE_RATE - 1) / K_SAMPLE_RATE;
        for h in &mut s.histogram {
            let adjust = 1 + 2 * (*h).min(11);
            *h += adjust;
            histogram_total += adjust as usize;
        }
    }
    brotli_build_and_store_huffman_tree_fast(
        &mut s.tree,
        &s.histogram,
        histogram_total,
        8,
        &mut s.lit_depth,
        &mut s.lit_bits,
        storage_ix,
        storage,
    );
    let mut literal_ratio = 0usize;
    for i in 0..256 {
        if s.histogram[i] != 0 {
            literal_ratio += (s.histogram[i] as usize) * (s.lit_depth[i] as usize);
        }
    }
    (literal_ratio * 125) / histogram_total
}

pub fn build_and_store_command_prefix_code_one_pass(
    s: &mut BrotliOnePassArena,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    s.tmp_depth.fill(0);
    brotli_create_huffman_tree(&s.cmd_histo[..64], 64, 15, &mut s.tree, &mut s.cmd_depth[..64]);
    brotli_create_huffman_tree(
        &s.cmd_histo[64..128],
        64,
        14,
        &mut s.tree,
        &mut s.cmd_depth[64..128],
    );

    s.tmp_depth[0..24].copy_from_slice(&s.cmd_depth[0..24]);
    s.tmp_depth[24..32].copy_from_slice(&s.cmd_depth[40..48]);
    s.tmp_depth[32..40].copy_from_slice(&s.cmd_depth[24..32]);
    s.tmp_depth[40..48].copy_from_slice(&s.cmd_depth[48..56]);
    s.tmp_depth[48..56].copy_from_slice(&s.cmd_depth[32..40]);
    s.tmp_depth[56..64].copy_from_slice(&s.cmd_depth[56..64]);
    brotli_convert_bit_depths_to_symbols(&s.tmp_depth[..64], 64, &mut s.tmp_bits);
    s.cmd_bits[0..24].copy_from_slice(&s.tmp_bits[0..24]);
    s.cmd_bits[24..32].copy_from_slice(&s.tmp_bits[32..40]);
    s.cmd_bits[32..40].copy_from_slice(&s.tmp_bits[48..56]);
    s.cmd_bits[40..48].copy_from_slice(&s.tmp_bits[24..32]);
    s.cmd_bits[48..56].copy_from_slice(&s.tmp_bits[40..48]);
    s.cmd_bits[56..64].copy_from_slice(&s.tmp_bits[56..64]);
    brotli_convert_bit_depths_to_symbols(&s.cmd_depth[64..128], 64, &mut s.cmd_bits[64..128]);

    s.tmp_depth[0..64].fill(0);
    s.tmp_depth[0..8].copy_from_slice(&s.cmd_depth[0..8]);
    s.tmp_depth[64..72].copy_from_slice(&s.cmd_depth[8..16]);
    s.tmp_depth[128..136].copy_from_slice(&s.cmd_depth[16..24]);
    s.tmp_depth[192..200].copy_from_slice(&s.cmd_depth[24..32]);
    s.tmp_depth[384..392].copy_from_slice(&s.cmd_depth[32..40]);
    for i in 0..8 {
        s.tmp_depth[128 + 8 * i] = s.cmd_depth[40 + i];
        s.tmp_depth[256 + 8 * i] = s.cmd_depth[48 + i];
        s.tmp_depth[448 + 8 * i] = s.cmd_depth[56 + i];
    }
    brotli_store_huffman_tree(
        &s.tmp_depth,
        BROTLI_NUM_COMMAND_SYMBOLS,
        &mut s.tree,
        storage_ix,
        storage,
    );
    brotli_store_huffman_tree(&s.cmd_depth[64..128], 64, &mut s.tree, storage_ix, storage);
}

#[inline(always)]
fn emit_insert_len(
    insertlen: usize,
    depth: &[u8; 128],
    bits: &[u16; 128],
    histo: &mut [u32; 128],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    if insertlen < 6 {
        let code = insertlen + 40;
        brotli_write_bits(depth[code] as usize, bits[code] as u64, storage_ix, storage);
        histo[code] += 1;
    } else if insertlen < 130 {
        let tail = insertlen - 2;
        let nbits = log2_floor_non_zero(tail) - 1;
        let prefix = tail >> nbits;
        let inscode = ((nbits as usize) << 1) + prefix + 42;
        brotli_write_bits(depth[inscode] as usize, bits[inscode] as u64, storage_ix, storage);
        brotli_write_bits(nbits as usize, (tail - (prefix << nbits)) as u64, storage_ix, storage);
        histo[inscode] += 1;
    } else if insertlen < 2114 {
        let tail = insertlen - 66;
        let nbits = log2_floor_non_zero(tail);
        let code = (nbits as usize) + 50;
        brotli_write_bits(depth[code] as usize, bits[code] as u64, storage_ix, storage);
        brotli_write_bits(nbits as usize, (tail - (1usize << nbits)) as u64, storage_ix, storage);
        histo[code] += 1;
    } else {
        brotli_write_bits(depth[61] as usize, bits[61] as u64, storage_ix, storage);
        brotli_write_bits(12, (insertlen - 2114) as u64, storage_ix, storage);
        histo[61] += 1;
    }
}

#[inline(always)]
fn emit_long_insert_len(
    insertlen: usize,
    depth: &[u8; 128],
    bits: &[u16; 128],
    histo: &mut [u32; 128],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    if insertlen < 22594 {
        brotli_write_bits(depth[62] as usize, bits[62] as u64, storage_ix, storage);
        brotli_write_bits(14, (insertlen - 6210) as u64, storage_ix, storage);
        histo[62] += 1;
    } else {
        brotli_write_bits(depth[63] as usize, bits[63] as u64, storage_ix, storage);
        brotli_write_bits(24, (insertlen - 22594) as u64, storage_ix, storage);
        histo[63] += 1;
    }
}

#[inline(always)]
fn emit_copy_len(
    copylen: usize,
    depth: &[u8; 128],
    bits: &[u16; 128],
    histo: &mut [u32; 128],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    if copylen < 10 {
        let code = copylen + 14;
        brotli_write_bits(depth[code] as usize, bits[code] as u64, storage_ix, storage);
        histo[code] += 1;
    } else if copylen < 134 {
        let tail = copylen - 6;
        let nbits = log2_floor_non_zero(tail) - 1;
        let prefix = tail >> nbits;
        let code = ((nbits as usize) << 1) + prefix + 20;
        brotli_write_bits(depth[code] as usize, bits[code] as u64, storage_ix, storage);
        brotli_write_bits(nbits as usize, (tail - (prefix << nbits)) as u64, storage_ix, storage);
        histo[code] += 1;
    } else if copylen < 2118 {
        let tail = copylen - 70;
        let nbits = log2_floor_non_zero(tail);
        let code = (nbits as usize) + 28;
        brotli_write_bits(depth[code] as usize, bits[code] as u64, storage_ix, storage);
        brotli_write_bits(nbits as usize, (tail - (1usize << nbits)) as u64, storage_ix, storage);
        histo[code] += 1;
    } else {
        brotli_write_bits(depth[39] as usize, bits[39] as u64, storage_ix, storage);
        brotli_write_bits(24, (copylen - 2118) as u64, storage_ix, storage);
        histo[39] += 1;
    }
}

#[inline(always)]
fn emit_copy_len_last_distance(
    copylen: usize,
    depth: &[u8; 128],
    bits: &[u16; 128],
    histo: &mut [u32; 128],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    if copylen < 12 {
        let code = copylen - 4;
        brotli_write_bits(depth[code] as usize, bits[code] as u64, storage_ix, storage);
        histo[code] += 1;
    } else if copylen < 72 {
        let tail = copylen - 8;
        let nbits = log2_floor_non_zero(tail) - 1;
        let prefix = tail >> nbits;
        let code = ((nbits as usize) << 1) + prefix + 4;
        brotli_write_bits(depth[code] as usize, bits[code] as u64, storage_ix, storage);
        brotli_write_bits(nbits as usize, (tail - (prefix << nbits)) as u64, storage_ix, storage);
        histo[code] += 1;
    } else if copylen < 136 {
        let tail = copylen - 8;
        let code = (tail >> 5) + 30;
        brotli_write_bits(depth[code] as usize, bits[code] as u64, storage_ix, storage);
        brotli_write_bits(5, (tail & 31) as u64, storage_ix, storage);
        brotli_write_bits(depth[64] as usize, bits[64] as u64, storage_ix, storage);
        histo[code] += 1;
        histo[64] += 1;
    } else if copylen < 2120 {
        let tail = copylen - 72;
        let nbits = log2_floor_non_zero(tail);
        let code = (nbits as usize) + 28;
        brotli_write_bits(depth[code] as usize, bits[code] as u64, storage_ix, storage);
        brotli_write_bits(nbits as usize, (tail - (1usize << nbits)) as u64, storage_ix, storage);
        brotli_write_bits(depth[64] as usize, bits[64] as u64, storage_ix, storage);
        histo[code] += 1;
        histo[64] += 1;
    } else {
        brotli_write_bits(depth[39] as usize, bits[39] as u64, storage_ix, storage);
        brotli_write_bits(24, (copylen - 2120) as u64, storage_ix, storage);
        brotli_write_bits(depth[64] as usize, bits[64] as u64, storage_ix, storage);
        histo[39] += 1;
        histo[64] += 1;
    }
}

#[inline(always)]
fn emit_distance(
    distance: usize,
    depth: &[u8; 128],
    bits: &[u16; 128],
    histo: &mut [u32; 128],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let d = distance + 3;
    let nbits = log2_floor_non_zero(d) - 1;
    let prefix = (d >> nbits) & 1;
    let offset = (2 + prefix) << nbits;
    let distcode = 2 * ((nbits as usize) - 1) + prefix + 80;
    brotli_write_bits(depth[distcode] as usize, bits[distcode] as u64, storage_ix, storage);
    brotli_write_bits(nbits as usize, (d - offset) as u64, storage_ix, storage);
    histo[distcode] += 1;
}

#[inline(always)]
fn emit_literals(
    input: &[u8],
    depth: &[u8; 256],
    bits: &[u16; 256],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    for &lit in input {
        brotli_write_bits(depth[lit as usize] as usize, bits[lit as usize] as u64, storage_ix, storage);
    }
}

fn brotli_store_meta_block_header(
    len: usize,
    is_uncompressed: bool,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let mut nibbles = 6usize;
    brotli_write_bits(1, 0, storage_ix, storage);
    if len <= (1usize << 16) {
        nibbles = 4;
    } else if len <= (1usize << 20) {
        nibbles = 5;
    }
    brotli_write_bits(2, (nibbles - 4) as u64, storage_ix, storage);
    brotli_write_bits(nibbles * 4, (len - 1) as u64, storage_ix, storage);
    brotli_write_bits(1, is_uncompressed as u64, storage_ix, storage);
}

fn update_bits(mut n_bits: usize, mut bits: u32, mut pos: usize, array: &mut [u8]) {
    while n_bits > 0 {
        let byte_pos = pos >> 3;
        let n_unchanged_bits = pos & 7;
        let n_changed_bits = n_bits.min(8 - n_unchanged_bits);
        let total_bits = n_unchanged_bits + n_changed_bits;
        let mask = (!((1u32 << total_bits) - 1)) | ((1u32 << n_unchanged_bits) - 1);
        let unchanged_bits = (array[byte_pos] as u32) & mask;
        let changed_bits = bits & ((1u32 << n_changed_bits) - 1);
        array[byte_pos] = ((changed_bits << n_unchanged_bits) | unchanged_bits) as u8;
        n_bits -= n_changed_bits;
        bits >>= n_changed_bits;
        pos += n_changed_bits;
    }
}

fn rewind_bit_position(new_storage_ix: usize, storage_ix: &mut usize, storage: &mut [u8]) {
    let bitpos = new_storage_ix & 7;
    let mask = (1u8 << bitpos) - 1;
    storage[new_storage_ix >> 3] &= mask;
    *storage_ix = new_storage_ix;
}

fn should_merge_block(s: &mut BrotliOnePassArena, data: &[u8], len: usize) -> bool {
    const K_SAMPLE_RATE: usize = 43;
    s.histogram.fill(0);
    let mut i = 0usize;
    while i < len {
        s.histogram[data[i] as usize] += 1;
        i += K_SAMPLE_RATE;
    }
    let total = (len + K_SAMPLE_RATE - 1) / K_SAMPLE_RATE;
    let mut r = (fast_log2(total) + 0.5) * (total as f64) + 200.0;
    for i in 0..256 {
        r -= (s.histogram[i] as f64) * ((s.lit_depth[i] as f64) + fast_log2(s.histogram[i] as usize));
    }
    r >= 0.0
}

const MIN_RATIO: usize = 980;

#[inline(always)]
fn should_use_uncompressed_mode(
    metablock_start: usize,
    next_emit: usize,
    insertlen: usize,
    literal_ratio: usize,
) -> bool {
    let compressed = next_emit - metablock_start;
    if compressed * 50 > insertlen {
        false
    } else {
        literal_ratio > MIN_RATIO
    }
}

fn emit_uncompressed_meta_block(
    begin: &[u8],
    storage_ix_start: usize,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let len = begin.len();
    rewind_bit_position(storage_ix_start, storage_ix, storage);
    brotli_store_meta_block_header(len, true, storage_ix, storage);
    *storage_ix = (*storage_ix + 7) & !7;
    let out = *storage_ix >> 3;
    storage[out..out + len].copy_from_slice(begin);
    *storage_ix += len << 3;
    storage[*storage_ix >> 3] = 0;
}

static K_CMD_HISTO_SEED: [u32; 128] = [
    0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 0, 0, 0, 0,
];

fn compress_fragment_fast_impl(
    s: &mut BrotliOnePassArena,
    input: &[u8],
    total_input_size: usize,
    is_last: bool,
    table: &mut [i32],
    table_bits: usize,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    const K_FIRST_BLOCK_SIZE: usize = 3 << 15;
    const K_MERGE_BLOCK_SIZE: usize = 1 << 16;
    const K_INPUT_MARGIN_BYTES: usize = BROTLI_WINDOW_GAP;
    const K_MIN_MATCH_LEN: usize = 5;

    let mut input_pos = 0usize;
    let mut input_size = total_input_size;
    let mut next_emit = 0usize;
    let mut metablock_start = 0usize;
    let mut block_size = input_size.min(K_FIRST_BLOCK_SIZE);
    let mut total_block_size = block_size;
    let mut mlen_storage_ix = *storage_ix + 3;
    let shift = 64 - table_bits;

    brotli_store_meta_block_header(block_size, false, storage_ix, storage);
    brotli_write_bits(13, 0, storage_ix, storage);

    let mut literal_ratio = build_and_store_literal_prefix_code(
        s,
        &input[input_pos..],
        block_size,
        storage_ix,
        storage,
    );

    let mut i = 0usize;
    while i + 7 < s.cmd_code_numbits {
        brotli_write_bits(8, s.cmd_code[i >> 3] as u64, storage_ix, storage);
        i += 8;
    }
    brotli_write_bits(
        s.cmd_code_numbits & 7,
        s.cmd_code[s.cmd_code_numbits >> 3] as u64,
        storage_ix,
        storage,
    );

    'block_loop: loop {
        'emit_commands: loop {
            s.cmd_histo.copy_from_slice(&K_CMD_HISTO_SEED);
            let mut ip = input_pos;
            let mut last_distance: isize = -1;
            let ip_end = input_pos + block_size;

            if block_size >= K_INPUT_MARGIN_BYTES {
                let len_limit = (block_size - K_MIN_MATCH_LEN).min(input_size - K_INPUT_MARGIN_BYTES);
                let ip_limit = input_pos + len_limit;

                ip += 1;
                let mut next_hash = hash(input, ip, shift);

                'outer_search: loop {
                    let mut skip = 32u32;
                    let mut next_ip = ip;
                    let mut candidate: isize;

                    loop {
                        let mut hash_val = next_hash;
                        let bytes_between = (skip >> 5) as usize;
                        skip += 1;
                        ip = next_ip;
                        next_ip = ip + bytes_between;
                        if next_ip > ip_limit {
                            break 'outer_search;
                        }
                        next_hash = hash(input, next_ip, shift);
                        candidate = (ip as isize) - last_distance;
                        if is_match(input, ip as isize, candidate) && candidate < (ip as isize) {
                            table[hash_val as usize] = ip as i32;
                            break;
                        }
                        candidate = table[hash_val as usize] as isize;
                        table[hash_val as usize] = ip as i32;
                        if is_match(input, ip as isize, candidate) {
                            if (ip as isize) - candidate <= MAX_DISTANCE {
                                break;
                            } else {
                                // Restart trawl loop as in `goto trawl;`
                                skip = skip; // keep skip
                                let _ = &mut hash_val;
                                continue;
                            }
                        }
                    }

                    // Step 2: Emit match
                    {
                        let base = ip;
                        let cand_u = candidate as usize;
                        let matched = 5 + find_match_length_with_limit(
                            &input[cand_u + 5..],
                            &input[ip + 5..],
                            (ip_end - ip) - 5,
                        );
                        let distance = (base as isize - candidate) as usize;
                        let insert = base - next_emit;
                        ip += matched;
                        if insert < 6210 {
                            emit_insert_len(
                                insert,
                                &s.cmd_depth,
                                &s.cmd_bits,
                                &mut s.cmd_histo,
                                storage_ix,
                                storage,
                            );
                        } else if should_use_uncompressed_mode(
                            metablock_start,
                            next_emit,
                            insert,
                            literal_ratio,
                        ) {
                            emit_uncompressed_meta_block(
                                &input[metablock_start..base],
                                mlen_storage_ix - 3,
                                storage_ix,
                                storage,
                            );
                            input_size -= base - input_pos;
                            input_pos = base;
                            next_emit = input_pos;
                            break 'emit_commands;
                        } else {
                            emit_long_insert_len(
                                insert,
                                &s.cmd_depth,
                                &s.cmd_bits,
                                &mut s.cmd_histo,
                                storage_ix,
                                storage,
                            );
                        }
                        emit_literals(
                            &input[next_emit..next_emit + insert],
                            &s.lit_depth,
                            &s.lit_bits,
                            storage_ix,
                            storage,
                        );
                        if (distance as isize) == last_distance {
                            brotli_write_bits(
                                s.cmd_depth[64] as usize,
                                s.cmd_bits[64] as u64,
                                storage_ix,
                                storage,
                            );
                            s.cmd_histo[64] += 1;
                        } else {
                            emit_distance(
                                distance,
                                &s.cmd_depth,
                                &s.cmd_bits,
                                &mut s.cmd_histo,
                                storage_ix,
                                storage,
                            );
                            last_distance = distance as isize;
                        }
                        emit_copy_len_last_distance(
                            matched,
                            &s.cmd_depth,
                            &s.cmd_bits,
                            &mut s.cmd_histo,
                            storage_ix,
                            storage,
                        );

                        next_emit = ip;
                        if ip >= ip_limit {
                            break 'outer_search;
                        }
                        let input_bytes = load_u64_le(input, ip - 3);
                        let mut prev_hash = hash_bytes_at_offset(input_bytes, 0, shift);
                        let cur_hash = hash_bytes_at_offset(input_bytes, 3, shift);
                        table[prev_hash as usize] = (ip - 3) as i32;
                        prev_hash = hash_bytes_at_offset(input_bytes, 1, shift);
                        table[prev_hash as usize] = (ip - 2) as i32;
                        prev_hash = hash_bytes_at_offset(input_bytes, 2, shift);
                        table[prev_hash as usize] = (ip - 1) as i32;
                        candidate = table[cur_hash as usize] as isize;
                        table[cur_hash as usize] = ip as i32;
                    }

                    while is_match(input, ip as isize, candidate) {
                        let base = ip;
                        if (ip as isize) - candidate > MAX_DISTANCE {
                            break;
                        }
                        let cand_u = candidate as usize;
                        let matched = 5 + find_match_length_with_limit(
                            &input[cand_u + 5..],
                            &input[ip + 5..],
                            (ip_end - ip) - 5,
                        );
                        ip += matched;
                        last_distance = (base as isize) - candidate;
                        emit_copy_len(
                            matched,
                            &s.cmd_depth,
                            &s.cmd_bits,
                            &mut s.cmd_histo,
                            storage_ix,
                            storage,
                        );
                        emit_distance(
                            last_distance as usize,
                            &s.cmd_depth,
                            &s.cmd_bits,
                            &mut s.cmd_histo,
                            storage_ix,
                            storage,
                        );

                        next_emit = ip;
                        if ip >= ip_limit {
                            break 'outer_search;
                        }
                        let input_bytes = load_u64_le(input, ip - 3);
                        let mut prev_hash = hash_bytes_at_offset(input_bytes, 0, shift);
                        let cur_hash = hash_bytes_at_offset(input_bytes, 3, shift);
                        table[prev_hash as usize] = (ip - 3) as i32;
                        prev_hash = hash_bytes_at_offset(input_bytes, 1, shift);
                        table[prev_hash as usize] = (ip - 2) as i32;
                        prev_hash = hash_bytes_at_offset(input_bytes, 2, shift);
                        table[prev_hash as usize] = (ip - 1) as i32;
                        candidate = table[cur_hash as usize] as isize;
                        table[cur_hash as usize] = ip as i32;
                    }

                    ip += 1;
                    next_hash = hash(input, ip, shift);
                }
            }

            // emit_remainder:
            input_pos += block_size;
            input_size -= block_size;
            block_size = input_size.min(K_MERGE_BLOCK_SIZE);

            if input_size > 0
                && total_block_size + block_size <= (1 << 20)
                && should_merge_block(s, &input[input_pos..], block_size)
            {
                total_block_size += block_size;
                update_bits(20, (total_block_size - 1) as u32, mlen_storage_ix, storage);
                continue 'emit_commands;
            }

            if next_emit < ip_end {
                let insert = ip_end - next_emit;
                if insert < 6210 {
                    emit_insert_len(
                        insert,
                        &s.cmd_depth,
                        &s.cmd_bits,
                        &mut s.cmd_histo,
                        storage_ix,
                        storage,
                    );
                    emit_literals(
                        &input[next_emit..next_emit + insert],
                        &s.lit_depth,
                        &s.lit_bits,
                        storage_ix,
                        storage,
                    );
                } else if should_use_uncompressed_mode(
                    metablock_start,
                    next_emit,
                    insert,
                    literal_ratio,
                ) {
                    emit_uncompressed_meta_block(
                        &input[metablock_start..ip_end],
                        mlen_storage_ix - 3,
                        storage_ix,
                        storage,
                    );
                } else {
                    emit_long_insert_len(
                        insert,
                        &s.cmd_depth,
                        &s.cmd_bits,
                        &mut s.cmd_histo,
                        storage_ix,
                        storage,
                    );
                    emit_literals(
                        &input[next_emit..next_emit + insert],
                        &s.lit_depth,
                        &s.lit_bits,
                        storage_ix,
                        storage,
                    );
                }
            }
            next_emit = ip_end;
            break 'emit_commands;
        }

        if input_size > 0 {
            metablock_start = input_pos;
            block_size = input_size.min(K_FIRST_BLOCK_SIZE);
            total_block_size = block_size;
            mlen_storage_ix = *storage_ix + 3;
            brotli_store_meta_block_header(block_size, false, storage_ix, storage);
            brotli_write_bits(13, 0, storage_ix, storage);
            literal_ratio = build_and_store_literal_prefix_code(
                s,
                &input[input_pos..],
                block_size,
                storage_ix,
                storage,
            );
            build_and_store_command_prefix_code_one_pass(s, storage_ix, storage);
            continue 'block_loop;
        }
        break 'block_loop;
    }

    if !is_last {
        s.cmd_code[0] = 0;
        s.cmd_code_numbits = 0;
        let mut numbits = 0usize;
        let mut code_buf = [0u8; 512];
        build_and_store_command_prefix_code_one_pass(s, &mut numbits, &mut code_buf);
        s.cmd_code_numbits = numbits;
        s.cmd_code = code_buf;
    }
}

pub fn brotli_compress_fragment_fast(
    s: &mut BrotliOnePassArena,
    input: &[u8],
    input_size: usize,
    is_last: bool,
    table: &mut [i32],
    table_size: usize,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let initial_storage_ix = *storage_ix;
    let table_bits = log2_floor_non_zero(table_size) as usize;

    if input_size == 0 {
        brotli_write_bits(1, 1, storage_ix, storage);
        brotli_write_bits(1, 1, storage_ix, storage);
        *storage_ix = (*storage_ix + 7) & !7;
        return;
    }

    match table_bits {
        9 | 11 | 13 | 15 => compress_fragment_fast_impl(
            s,
            input,
            input_size,
            is_last,
            table,
            table_bits,
            storage_ix,
            storage,
        ),
        _ => unreachable!("Invalid table_bits in CompressFragmentFast"),
    }

    if *storage_ix - initial_storage_ix > 31 + (input_size << 3) {
        emit_uncompressed_meta_block(
            &input[..input_size],
            initial_storage_ix,
            storage_ix,
            storage,
        );
    }

    if is_last {
        brotli_write_bits(1, 1, storage_ix, storage);
        brotli_write_bits(1, 1, storage_ix, storage);
        *storage_ix = (*storage_ix + 7) & !7;
    }
}
