#![allow(dead_code)]

use crate::common::constants::*;
use crate::enc::bit_cost::brotli_bits_entropy;
use crate::enc::brotli_bit_stream::{
    brotli_build_and_store_huffman_tree_fast, brotli_store_huffman_tree,
};
use crate::enc::entropy_encode::{
    HuffmanTree, brotli_convert_bit_depths_to_symbols, brotli_create_huffman_tree,
};
use crate::enc::fast_log::log2_floor_non_zero;
use crate::enc::find_match_length::find_match_length_with_limit;
use crate::enc::static_dict::K_HASH_MUL_32;
use crate::enc::write_bits::brotli_write_bits;

pub const K_COMPRESS_FRAGMENT_TWO_PASS_BLOCK_SIZE: usize = 1 << 17;
const MAX_DISTANCE: isize = brotli_max_backward_limit(18) as isize;

pub struct BrotliTwoPassArena {
    pub lit_histo: [u32; 256],
    pub lit_depth: [u8; 256],
    pub lit_bits: [u16; 256],
    pub cmd_histo: [u32; 128],
    pub cmd_depth: [u8; 128],
    pub cmd_bits: [u16; 128],
    pub tmp_tree: [HuffmanTree; 2 * BROTLI_NUM_LITERAL_SYMBOLS + 1],
    pub tmp_depth: [u8; BROTLI_NUM_COMMAND_SYMBOLS],
    pub tmp_bits: [u16; 64],
}

impl Default for BrotliTwoPassArena {
    fn default() -> Self {
        Self::new()
    }
}

impl BrotliTwoPassArena {
    pub fn new() -> Self {
        Self {
            lit_histo: [0; 256],
            lit_depth: [0; 256],
            lit_bits: [0; 256],
            cmd_histo: [0; 128],
            cmd_depth: [0; 128],
            cmd_bits: [0; 128],
            tmp_tree: [HuffmanTree::default(); 2 * BROTLI_NUM_LITERAL_SYMBOLS + 1],
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
fn hash(data: &[u8], pos: usize, shift: usize, length: usize) -> u32 {
    let h = (load_u64_le(data, pos) << ((8 - length) * 8)).wrapping_mul(K_HASH_MUL_32 as u64);
    (h >> shift) as u32
}

#[inline(always)]
fn hash_bytes_at_offset(v: u64, offset: usize, shift: usize, length: usize) -> u32 {
    let h = ((v >> (8 * offset)) << ((8 - length) * 8)).wrapping_mul(K_HASH_MUL_32 as u64);
    (h >> shift) as u32
}

#[inline(always)]
fn is_match(data: &[u8], p1: isize, p2: isize, length: usize) -> bool {
    if p1 < 0 || p2 < 0 {
        return false;
    }
    let u1 = p1 as usize;
    let u2 = p2 as usize;
    if u1 + length > data.len() || u2 + length > data.len() {
        return false;
    }
    if load_u32_le(data, u1) == load_u32_le(data, u2) {
        if length == 4 {
            return true;
        }
        return data[u1 + 4] == data[u2 + 4] && data[u1 + 5] == data[u2 + 5];
    }
    false
}

fn build_and_store_command_prefix_code(
    s: &mut BrotliTwoPassArena,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    s.tmp_depth.fill(0);
    brotli_create_huffman_tree(&s.cmd_histo[..64], 64, 15, &mut s.tmp_tree, &mut s.cmd_depth[..64]);
    brotli_create_huffman_tree(
        &s.cmd_histo[64..128],
        64,
        14,
        &mut s.tmp_tree,
        &mut s.cmd_depth[64..128],
    );

    s.tmp_depth[0..24].copy_from_slice(&s.cmd_depth[24..48]);
    s.tmp_depth[24..32].copy_from_slice(&s.cmd_depth[0..8]);
    s.tmp_depth[32..40].copy_from_slice(&s.cmd_depth[48..56]);
    s.tmp_depth[40..48].copy_from_slice(&s.cmd_depth[8..16]);
    s.tmp_depth[48..56].copy_from_slice(&s.cmd_depth[56..64]);
    s.tmp_depth[56..64].copy_from_slice(&s.cmd_depth[16..24]);
    brotli_convert_bit_depths_to_symbols(&s.tmp_depth[..64], 64, &mut s.tmp_bits);
    s.cmd_bits[0..8].copy_from_slice(&s.tmp_bits[24..32]);
    s.cmd_bits[8..16].copy_from_slice(&s.tmp_bits[40..48]);
    s.cmd_bits[16..24].copy_from_slice(&s.tmp_bits[56..64]);
    s.cmd_bits[24..48].copy_from_slice(&s.tmp_bits[0..24]);
    s.cmd_bits[48..56].copy_from_slice(&s.tmp_bits[32..40]);
    s.cmd_bits[56..64].copy_from_slice(&s.tmp_bits[48..56]);
    brotli_convert_bit_depths_to_symbols(&s.cmd_depth[64..128], 64, &mut s.cmd_bits[64..128]);

    s.tmp_depth[0..64].fill(0);
    s.tmp_depth[0..8].copy_from_slice(&s.cmd_depth[24..32]);
    s.tmp_depth[64..72].copy_from_slice(&s.cmd_depth[32..40]);
    s.tmp_depth[128..136].copy_from_slice(&s.cmd_depth[40..48]);
    s.tmp_depth[192..200].copy_from_slice(&s.cmd_depth[48..56]);
    s.tmp_depth[384..392].copy_from_slice(&s.cmd_depth[56..64]);
    for i in 0..8 {
        s.tmp_depth[128 + 8 * i] = s.cmd_depth[i];
        s.tmp_depth[256 + 8 * i] = s.cmd_depth[8 + i];
        s.tmp_depth[448 + 8 * i] = s.cmd_depth[16 + i];
    }
    brotli_store_huffman_tree(
        &s.tmp_depth,
        BROTLI_NUM_COMMAND_SYMBOLS,
        &mut s.tmp_tree,
        storage_ix,
        storage,
    );
    brotli_store_huffman_tree(
        &s.cmd_depth[64..128],
        64,
        &mut s.tmp_tree,
        storage_ix,
        storage,
    );
}

#[inline(always)]
fn emit_insert_len(insertlen: u32, commands: &mut [u32], cmd_idx: &mut usize) {
    let val = if insertlen < 6 {
        insertlen
    } else if insertlen < 130 {
        let tail = insertlen - 2;
        let nbits = log2_floor_non_zero(tail as usize) - 1;
        let prefix = tail >> nbits;
        let inscode = (nbits << 1) + prefix + 2;
        let extra = tail - (prefix << nbits);
        inscode | (extra << 8)
    } else if insertlen < 2114 {
        let tail = insertlen - 66;
        let nbits = log2_floor_non_zero(tail as usize);
        let code = nbits + 10;
        let extra = tail - (1u32 << nbits);
        code | (extra << 8)
    } else if insertlen < 6210 {
        21 | ((insertlen - 2114) << 8)
    } else if insertlen < 22594 {
        22 | ((insertlen - 6210) << 8)
    } else {
        23 | ((insertlen - 22594) << 8)
    };
    commands[*cmd_idx] = val;
    *cmd_idx += 1;
}

#[inline(always)]
fn emit_copy_len(copylen: usize, commands: &mut [u32], cmd_idx: &mut usize) {
    let val = if copylen < 10 {
        (copylen + 38) as u32
    } else if copylen < 134 {
        let tail = copylen - 6;
        let nbits = (log2_floor_non_zero(tail) - 1) as usize;
        let prefix = tail >> nbits;
        let code = (nbits << 1) + prefix + 44;
        let extra = tail - (prefix << nbits);
        (code | (extra << 8)) as u32
    } else if copylen < 2118 {
        let tail = copylen - 70;
        let nbits = log2_floor_non_zero(tail) as usize;
        let code = nbits + 52;
        let extra = tail - (1usize << nbits);
        (code | (extra << 8)) as u32
    } else {
        let extra = copylen - 2118;
        (63 | (extra << 8)) as u32
    };
    commands[*cmd_idx] = val;
    *cmd_idx += 1;
}

#[inline(always)]
fn emit_copy_len_last_distance(copylen: usize, commands: &mut [u32], cmd_idx: &mut usize) {
    if copylen < 12 {
        commands[*cmd_idx] = (copylen + 20) as u32;
        *cmd_idx += 1;
    } else if copylen < 72 {
        let tail = copylen - 8;
        let nbits = (log2_floor_non_zero(tail) - 1) as usize;
        let prefix = tail >> nbits;
        let code = (nbits << 1) + prefix + 28;
        let extra = tail - (prefix << nbits);
        commands[*cmd_idx] = (code | (extra << 8)) as u32;
        *cmd_idx += 1;
    } else if copylen < 136 {
        let tail = copylen - 8;
        let code = (tail >> 5) + 54;
        let extra = tail & 31;
        commands[*cmd_idx] = (code | (extra << 8)) as u32;
        *cmd_idx += 1;
        commands[*cmd_idx] = 64;
        *cmd_idx += 1;
    } else if copylen < 2120 {
        let tail = copylen - 72;
        let nbits = log2_floor_non_zero(tail) as usize;
        let code = nbits + 52;
        let extra = tail - (1usize << nbits);
        commands[*cmd_idx] = (code | (extra << 8)) as u32;
        *cmd_idx += 1;
        commands[*cmd_idx] = 64;
        *cmd_idx += 1;
    } else {
        let extra = copylen - 2120;
        commands[*cmd_idx] = (63 | (extra << 8)) as u32;
        *cmd_idx += 1;
        commands[*cmd_idx] = 64;
        *cmd_idx += 1;
    }
}

#[inline(always)]
fn emit_distance(distance: u32, commands: &mut [u32], cmd_idx: &mut usize) {
    let d = distance + 3;
    let nbits = log2_floor_non_zero(d as usize) - 1;
    let prefix = (d >> nbits) & 1;
    let offset = (2 + prefix) << nbits;
    let distcode = 2 * (nbits - 1) + prefix + 80;
    let extra = d - offset;
    commands[*cmd_idx] = distcode | (extra << 8);
    *cmd_idx += 1;
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

fn create_commands(
    data: &[u8],
    input_pos: usize,
    block_size: usize,
    input_size: usize,
    table: &mut [i32],
    table_bits: usize,
    min_match: usize,
    literal_buf: &mut [u8],
    lit_idx: &mut usize,
    command_buf: &mut [u32],
    cmd_idx: &mut usize,
) {
    let mut ip = input_pos;
    let shift = 64 - table_bits;
    let ip_end = input_pos + block_size;
    let mut next_emit = input_pos;
    let mut last_distance: isize = -1;
    const K_INPUT_MARGIN_BYTES: usize = BROTLI_WINDOW_GAP;

    if block_size >= K_INPUT_MARGIN_BYTES {
        let len_limit = (block_size - min_match).min(input_size - K_INPUT_MARGIN_BYTES);
        let ip_limit = input_pos + len_limit;

        ip += 1;
        let mut next_hash = hash(data, ip, shift, min_match);

        'outer: loop {
            let mut skip = 32u32;
            let mut next_ip = ip;
            let mut candidate: isize;

            loop {
                let hash_val = next_hash;
                let bytes_between = (skip >> 5) as usize;
                skip += 1;
                ip = next_ip;
                next_ip = ip + bytes_between;
                if next_ip > ip_limit {
                    break 'outer;
                }
                next_hash = hash(data, next_ip, shift, min_match);
                candidate = (ip as isize) - last_distance;
                if is_match(data, ip as isize, candidate, min_match) && candidate < (ip as isize) {
                    table[hash_val as usize] = ip as i32;
                    break;
                }
                candidate = table[hash_val as usize] as isize;
                table[hash_val as usize] = ip as i32;
                if is_match(data, ip as isize, candidate, min_match)
                    && (ip as isize) - candidate <= MAX_DISTANCE
                {
                    break;
                }
            }

            {
                let base = ip;
                let cand_u = candidate as usize;
                let matched = min_match
                    + find_match_length_with_limit(
                        &data[cand_u + min_match..],
                        &data[ip + min_match..],
                        (ip_end - ip) - min_match,
                    );
                let distance = (base as isize) - candidate;
                let insert = base - next_emit;
                ip += matched;
                emit_insert_len(insert as u32, command_buf, cmd_idx);
                literal_buf[*lit_idx..*lit_idx + insert]
                    .copy_from_slice(&data[next_emit..next_emit + insert]);
                *lit_idx += insert;
                if distance == last_distance {
                    command_buf[*cmd_idx] = 64;
                    *cmd_idx += 1;
                } else {
                    emit_distance(distance as u32, command_buf, cmd_idx);
                    last_distance = distance;
                }
                emit_copy_len_last_distance(matched, command_buf, cmd_idx);

                next_emit = ip;
                if ip >= ip_limit {
                    break 'outer;
                }

                let cur_hash;
                if min_match == 4 {
                    let input_bytes = load_u64_le(data, ip - 3);
                    cur_hash = hash_bytes_at_offset(input_bytes, 3, shift, min_match);
                    let p0 = hash_bytes_at_offset(input_bytes, 0, shift, min_match);
                    table[p0 as usize] = (ip - 3) as i32;
                    let p1 = hash_bytes_at_offset(input_bytes, 1, shift, min_match);
                    table[p1 as usize] = (ip - 2) as i32;
                    let p2 = hash_bytes_at_offset(input_bytes, 0, shift, min_match);
                    table[p2 as usize] = (ip - 1) as i32;
                } else {
                    let input_bytes5 = load_u64_le(data, ip - 5);
                    let p0 = hash_bytes_at_offset(input_bytes5, 0, shift, min_match);
                    table[p0 as usize] = (ip - 5) as i32;
                    let p1 = hash_bytes_at_offset(input_bytes5, 1, shift, min_match);
                    table[p1 as usize] = (ip - 4) as i32;
                    let p2 = hash_bytes_at_offset(input_bytes5, 2, shift, min_match);
                    table[p2 as usize] = (ip - 3) as i32;
                    let input_bytes2 = load_u64_le(data, ip - 2);
                    cur_hash = hash_bytes_at_offset(input_bytes2, 2, shift, min_match);
                    let p3 = hash_bytes_at_offset(input_bytes2, 0, shift, min_match);
                    table[p3 as usize] = (ip - 2) as i32;
                    let p4 = hash_bytes_at_offset(input_bytes2, 1, shift, min_match);
                    table[p4 as usize] = (ip - 1) as i32;
                }
                candidate = table[cur_hash as usize] as isize;
                table[cur_hash as usize] = ip as i32;
            }

            while (ip as isize) - candidate <= MAX_DISTANCE
                && is_match(data, ip as isize, candidate, min_match)
            {
                let base = ip;
                let cand_u = candidate as usize;
                let matched = min_match
                    + find_match_length_with_limit(
                        &data[cand_u + min_match..],
                        &data[ip + min_match..],
                        (ip_end - ip) - min_match,
                    );
                ip += matched;
                last_distance = (base as isize) - candidate;
                emit_copy_len(matched, command_buf, cmd_idx);
                emit_distance(last_distance as u32, command_buf, cmd_idx);

                next_emit = ip;
                if ip >= ip_limit {
                    break 'outer;
                }

                let cur_hash;
                if min_match == 4 {
                    let input_bytes = load_u64_le(data, ip - 3);
                    cur_hash = hash_bytes_at_offset(input_bytes, 3, shift, min_match);
                    let p0 = hash_bytes_at_offset(input_bytes, 0, shift, min_match);
                    table[p0 as usize] = (ip - 3) as i32;
                    let p1 = hash_bytes_at_offset(input_bytes, 1, shift, min_match);
                    table[p1 as usize] = (ip - 2) as i32;
                    let p2 = hash_bytes_at_offset(input_bytes, 2, shift, min_match);
                    table[p2 as usize] = (ip - 1) as i32;
                } else {
                    let input_bytes5 = load_u64_le(data, ip - 5);
                    let p0 = hash_bytes_at_offset(input_bytes5, 0, shift, min_match);
                    table[p0 as usize] = (ip - 5) as i32;
                    let p1 = hash_bytes_at_offset(input_bytes5, 1, shift, min_match);
                    table[p1 as usize] = (ip - 4) as i32;
                    let p2 = hash_bytes_at_offset(input_bytes5, 2, shift, min_match);
                    table[p2 as usize] = (ip - 3) as i32;
                    let input_bytes2 = load_u64_le(data, ip - 2);
                    cur_hash = hash_bytes_at_offset(input_bytes2, 2, shift, min_match);
                    let p3 = hash_bytes_at_offset(input_bytes2, 0, shift, min_match);
                    table[p3 as usize] = (ip - 2) as i32;
                    let p4 = hash_bytes_at_offset(input_bytes2, 1, shift, min_match);
                    table[p4 as usize] = (ip - 1) as i32;
                }
                candidate = table[cur_hash as usize] as isize;
                table[cur_hash as usize] = ip as i32;
            }

            ip += 1;
            next_hash = hash(data, ip, shift, min_match);
        }
    }

    if next_emit < ip_end {
        let insert = ip_end - next_emit;
        emit_insert_len(insert as u32, command_buf, cmd_idx);
        literal_buf[*lit_idx..*lit_idx + insert]
            .copy_from_slice(&data[next_emit..next_emit + insert]);
        *lit_idx += insert;
    }
}

fn store_commands(
    s: &mut BrotliTwoPassArena,
    literals: &[u8],
    num_literals: usize,
    commands: &[u32],
    num_commands: usize,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    static K_NUM_EXTRA_BITS: [u32; 128] = [
        0, 0, 0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5,
        6, 7, 8, 9, 10, 12, 14, 24, 0, 0, 0, 0, 0, 0, 0, 0,
        1, 1, 2, 2, 3, 3, 4, 4, 0, 0, 0, 0, 0, 0, 0, 0,
        1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 7, 8, 9, 10, 24,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8,
        9, 9, 10, 10, 11, 11, 12, 12, 13, 13, 14, 14, 15, 15, 16, 16,
        17, 17, 18, 18, 19, 19, 20, 20, 21, 21, 22, 22, 23, 23, 24, 24,
    ];
    static K_INSERT_OFFSET: [u32; 24] = [
        0, 1, 2, 3, 4, 5, 6, 8, 10, 14, 18, 26,
        34, 50, 66, 98, 130, 194, 322, 578, 1090, 2114, 6210, 22594,
    ];

    s.lit_histo.fill(0);
    s.cmd_depth.fill(0);
    s.cmd_bits.fill(0);
    s.cmd_histo.fill(0);

    for &b in &literals[..num_literals] {
        s.lit_histo[b as usize] += 1;
    }
    brotli_build_and_store_huffman_tree_fast(
        &mut s.tmp_tree,
        &s.lit_histo,
        num_literals,
        8,
        &mut s.lit_depth,
        &mut s.lit_bits,
        storage_ix,
        storage,
    );

    for &cmd in &commands[..num_commands] {
        let code = (cmd & 0xFF) as usize;
        s.cmd_histo[code] += 1;
    }
    s.cmd_histo[1] += 1;
    s.cmd_histo[2] += 1;
    s.cmd_histo[64] += 1;
    s.cmd_histo[84] += 1;
    build_and_store_command_prefix_code(s, storage_ix, storage);

    let mut lit_pos = 0usize;
    for &cmd in &commands[..num_commands] {
        let code = (cmd & 0xFF) as usize;
        let extra = cmd >> 8;
        brotli_write_bits(
            s.cmd_depth[code] as usize,
            s.cmd_bits[code] as u64,
            storage_ix,
            storage,
        );
        brotli_write_bits(K_NUM_EXTRA_BITS[code] as usize, extra as u64, storage_ix, storage);
        if code < 24 {
            let insert = (K_INSERT_OFFSET[code] + extra) as usize;
            for _ in 0..insert {
                let lit = literals[lit_pos] as usize;
                brotli_write_bits(
                    s.lit_depth[lit] as usize,
                    s.lit_bits[lit] as u64,
                    storage_ix,
                    storage,
                );
                lit_pos += 1;
            }
        }
    }
}

fn should_compress(
    s: &mut BrotliTwoPassArena,
    input: &[u8],
    input_size: usize,
    num_literals: usize,
) -> bool {
    const MIN_RATIO: f64 = 0.98;
    const SAMPLE_RATE: usize = 43;
    let corpus_size = input_size as f64;
    if (num_literals as f64) < MIN_RATIO * corpus_size {
        true
    } else {
        let max_total_bit_cost = corpus_size * 8.0 * MIN_RATIO / (SAMPLE_RATE as f64);
        s.lit_histo.fill(0);
        let mut i = 0usize;
        while i < input_size {
            s.lit_histo[input[i] as usize] += 1;
            i += SAMPLE_RATE;
        }
        brotli_bits_entropy(&s.lit_histo) < max_total_bit_cost
    }
}

fn rewind_bit_position(new_storage_ix: usize, storage_ix: &mut usize, storage: &mut [u8]) {
    let bitpos = new_storage_ix & 7;
    let mask = (1u8 << bitpos) - 1;
    storage[new_storage_ix >> 3] &= mask;
    *storage_ix = new_storage_ix;
}

fn emit_uncompressed_meta_block(input: &[u8], storage_ix: &mut usize, storage: &mut [u8]) {
    let input_size = input.len();
    brotli_store_meta_block_header(input_size, true, storage_ix, storage);
    *storage_ix = (*storage_ix + 7) & !7;
    let out = *storage_ix >> 3;
    storage[out..out + input_size].copy_from_slice(input);
    *storage_ix += input_size << 3;
    storage[*storage_ix >> 3] = 0;
}

fn compress_fragment_two_pass_impl(
    s: &mut BrotliTwoPassArena,
    input: &[u8],
    total_input_size: usize,
    command_buf: &mut [u32],
    literal_buf: &mut [u8],
    table: &mut [i32],
    table_bits: usize,
    min_match: usize,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let mut input_pos = 0usize;
    let mut input_size = total_input_size;
    while input_size > 0 {
        let block_size = input_size.min(K_COMPRESS_FRAGMENT_TWO_PASS_BLOCK_SIZE);
        let mut cmd_idx = 0usize;
        let mut lit_idx = 0usize;
        create_commands(
            input,
            input_pos,
            block_size,
            input_size,
            table,
            table_bits,
            min_match,
            literal_buf,
            &mut lit_idx,
            command_buf,
            &mut cmd_idx,
        );
        if should_compress(s, &input[input_pos..], block_size, lit_idx) {
            brotli_store_meta_block_header(block_size, false, storage_ix, storage);
            brotli_write_bits(13, 0, storage_ix, storage);
            store_commands(
                s,
                literal_buf,
                lit_idx,
                command_buf,
                cmd_idx,
                storage_ix,
                storage,
            );
        } else {
            emit_uncompressed_meta_block(&input[input_pos..input_pos + block_size], storage_ix, storage);
        }
        input_pos += block_size;
        input_size -= block_size;
    }
}

pub fn brotli_compress_fragment_two_pass(
    s: &mut BrotliTwoPassArena,
    input: &[u8],
    input_size: usize,
    is_last: bool,
    command_buf: &mut [u32],
    literal_buf: &mut [u8],
    table: &mut [i32],
    table_size: usize,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let initial_storage_ix = *storage_ix;
    let table_bits = log2_floor_non_zero(table_size) as usize;
    let min_match = if table_bits <= 15 { 4 } else { 6 };

    match table_bits {
        8..=17 => compress_fragment_two_pass_impl(
            s,
            input,
            input_size,
            command_buf,
            literal_buf,
            table,
            table_bits,
            min_match,
            storage_ix,
            storage,
        ),
        _ => unreachable!("Invalid table_bits in CompressFragmentTwoPass"),
    }

    if *storage_ix - initial_storage_ix > 31 + (input_size << 3) {
        rewind_bit_position(initial_storage_ix, storage_ix, storage);
        emit_uncompressed_meta_block(&input[..input_size], storage_ix, storage);
    }

    if is_last {
        brotli_write_bits(1, 1, storage_ix, storage);
        brotli_write_bits(1, 1, storage_ix, storage);
        *storage_ix = (*storage_ix + 7) & !7;
    }
}
