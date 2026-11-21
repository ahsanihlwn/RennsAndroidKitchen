//! Utilities for building canonical and multi-level Huffman decoding tables.

use crate::common::BROTLI_CODE_LENGTH_CODES;

pub const BROTLI_HUFFMAN_MAX_CODE_LENGTH: usize = 15;
pub const BROTLI_HUFFMAN_MAX_SIZE_26: usize = 396;
pub const BROTLI_HUFFMAN_MAX_SIZE_258: usize = 632;
pub const BROTLI_HUFFMAN_MAX_SIZE_272: usize = 646;
pub const BROTLI_HUFFMAN_MAX_CODE_LENGTH_CODE_LENGTH: usize = 5;

#[derive(Debug, Clone, Copy, Default)]
pub struct HuffmanCode {
    pub bits: u8,
    pub value: u16,
}

#[inline(always)]
pub const fn construct_huffman_code(bits: u8, value: u16) -> HuffmanCode {
    HuffmanCode { bits, value }
}

#[derive(Debug, Clone, Default)]
pub struct HuffmanTreeGroup {
    pub htree_offsets: Vec<usize>,
    pub codes: Vec<HuffmanCode>,
    pub alphabet_size_max: u16,
    pub alphabet_size_limit: u16,
    pub num_htrees: u16,
}

const BROTLI_REVERSE_BITS_MAX: usize = 8;
const BROTLI_REVERSE_BITS_LOWEST: u32 = 1u32 << (BROTLI_REVERSE_BITS_MAX - 1);

#[rustfmt::skip]
static K_REVERSE_BITS: [u8; 1 << BROTLI_REVERSE_BITS_MAX] = [
  0x00, 0x80, 0x40, 0xC0, 0x20, 0xA0, 0x60, 0xE0,
  0x10, 0x90, 0x50, 0xD0, 0x30, 0xB0, 0x70, 0xF0,
  0x08, 0x88, 0x48, 0xC8, 0x28, 0xA8, 0x68, 0xE8,
  0x18, 0x98, 0x58, 0xD8, 0x38, 0xB8, 0x78, 0xF8,
  0x04, 0x84, 0x44, 0xC4, 0x24, 0xA4, 0x64, 0xE4,
  0x14, 0x94, 0x54, 0xD4, 0x34, 0xB4, 0x74, 0xF4,
  0x0C, 0x8C, 0x4C, 0xCC, 0x2C, 0xAC, 0x6C, 0xEC,
  0x1C, 0x9C, 0x5C, 0xDC, 0x3C, 0xBC, 0x7C, 0xFC,
  0x02, 0x82, 0x42, 0xC2, 0x22, 0xA2, 0x62, 0xE2,
  0x12, 0x92, 0x52, 0xD2, 0x32, 0xB2, 0x72, 0xF2,
  0x0A, 0x8A, 0x4A, 0xCA, 0x2A, 0xAA, 0x6A, 0xEA,
  0x1A, 0x9A, 0x5A, 0xDA, 0x3A, 0xBA, 0x7A, 0xFA,
  0x06, 0x86, 0x46, 0xC6, 0x26, 0xA6, 0x66, 0xE6,
  0x16, 0x96, 0x56, 0xD6, 0x36, 0xB6, 0x76, 0xF6,
  0x0E, 0x8E, 0x4E, 0xCE, 0x2E, 0xAE, 0x6E, 0xEE,
  0x1E, 0x9E, 0x5E, 0xDE, 0x3E, 0xBE, 0x7E, 0xFE,
  0x01, 0x81, 0x41, 0xC1, 0x21, 0xA1, 0x61, 0xE1,
  0x11, 0x91, 0x51, 0xD1, 0x31, 0xB1, 0x71, 0xF1,
  0x09, 0x89, 0x49, 0xC9, 0x29, 0xA9, 0x69, 0xE9,
  0x19, 0x99, 0x59, 0xD9, 0x39, 0xB9, 0x79, 0xF9,
  0x05, 0x85, 0x45, 0xC5, 0x25, 0xA5, 0x65, 0xE5,
  0x15, 0x95, 0x55, 0xD5, 0x35, 0xB5, 0x75, 0xF5,
  0x0D, 0x8D, 0x4D, 0xCD, 0x2D, 0xAD, 0x6D, 0xED,
  0x1D, 0x9D, 0x5D, 0xDD, 0x3D, 0xBD, 0x7D, 0xFD,
  0x03, 0x83, 0x43, 0xC3, 0x23, 0xA3, 0x63, 0xE3,
  0x13, 0x93, 0x53, 0xD3, 0x33, 0xB3, 0x73, 0xF3,
  0x0B, 0x8B, 0x4B, 0xCB, 0x2B, 0xAB, 0x6B, 0xEB,
  0x1B, 0x9B, 0x5B, 0xDB, 0x3B, 0xBB, 0x7B, 0xFB,
  0x07, 0x87, 0x47, 0xC7, 0x27, 0xA7, 0x67, 0xE7,
  0x17, 0x97, 0x57, 0xD7, 0x37, 0xB7, 0x77, 0xF7,
  0x0F, 0x8F, 0x4F, 0xCF, 0x2F, 0xAF, 0x6F, 0xEF,
  0x1F, 0x9F, 0x5F, 0xDF, 0x3F, 0xBF, 0x7F, 0xFF,
];

#[inline(always)]
fn brotli_reverse_bits(num: u32) -> usize {
    K_REVERSE_BITS[num as usize] as usize
}

#[inline(always)]
fn replicate_value(table: &mut [HuffmanCode], offset: usize, step: usize, mut end: usize, code: HuffmanCode) {
    loop {
        end -= step;
        table[offset + end] = code;
        if end == 0 {
            break;
        }
    }
}

#[inline]
fn next_table_bit_size(count: &[u16], mut len: usize, root_bits: usize) -> usize {
    let mut left: i32 = 1 << (len - root_bits);
    while len < BROTLI_HUFFMAN_MAX_CODE_LENGTH {
        left -= count[len] as i32;
        if left <= 0 {
            break;
        }
        len += 1;
        left <<= 1;
    }
    len - root_bits
}

pub fn brotli_build_code_lengths_huffman_table(
    table: &mut [HuffmanCode],
    code_lengths: &[u8],
    count: &[u16],
) {
    let mut sorted = [0usize; BROTLI_CODE_LENGTH_CODES];
    let mut offset = [0isize; BROTLI_HUFFMAN_MAX_CODE_LENGTH_CODE_LENGTH + 1];

    let mut symbol = -1isize;
    for bits in 1..=BROTLI_HUFFMAN_MAX_CODE_LENGTH_CODE_LENGTH {
        symbol += count[bits] as isize;
        offset[bits] = symbol;
    }
    offset[0] = (BROTLI_CODE_LENGTH_CODES - 1) as isize;

    for sym in (0..BROTLI_CODE_LENGTH_CODES).rev() {
        let len = code_lengths[sym] as usize;
        let idx = offset[len] as usize;
        offset[len] -= 1;
        sorted[idx] = sym;
    }

    let table_size = 1usize << BROTLI_HUFFMAN_MAX_CODE_LENGTH_CODE_LENGTH;

    if offset[0] == 0 {
        let code = construct_huffman_code(0, sorted[0] as u16);
        for slot in table[..table_size].iter_mut() {
            *slot = code;
        }
        return;
    }

    let mut key = 0u32;
    let mut key_step = BROTLI_REVERSE_BITS_LOWEST;
    let mut sym_idx = 0usize;
    let mut step = 2usize;
    for bits in 1..=BROTLI_HUFFMAN_MAX_CODE_LENGTH_CODE_LENGTH {
        for _ in 0..count[bits] {
            let code = construct_huffman_code(bits as u8, sorted[sym_idx] as u16);
            sym_idx += 1;
            replicate_value(table, brotli_reverse_bits(key), step, table_size, code);
            key += key_step;
        }
        step <<= 1;
        key_step >>= 1;
    }
}

/// `symbols_lists_array` stores the symbol chains with base index `BROTLI_HUFFMAN_MAX_CODE_LENGTH + 1`
/// so that negative indices `-1..-16` map to `0..15` in `symbols_lists_array`.
pub fn brotli_build_huffman_table(
    root_table: &mut [HuffmanCode],
    root_bits: usize,
    symbols_lists_array: &[u16],
    count: &mut [u16],
) -> u32 {
    const BASE: isize = (BROTLI_HUFFMAN_MAX_CODE_LENGTH + 1) as isize;
    let mut max_length = -1isize;
    while symbols_lists_array[(BASE + max_length) as usize] == 0xFFFF {
        max_length -= 1;
    }
    max_length += BASE;
    let max_length = max_length as usize;

    let mut table_offset = 0usize;
    let mut table_bits = root_bits;
    let mut table_size = 1usize << table_bits;
    let mut total_size = table_size;

    if table_bits > max_length {
        table_bits = max_length;
        table_size = 1usize << table_bits;
    }

    let mut key = 0u32;
    let mut key_step = BROTLI_REVERSE_BITS_LOWEST;
    let mut bits = 1usize;
    let mut step = 2usize;
    loop {
        let mut sym = (bits as isize) - BASE;
        for _ in 0..count[bits] {
            sym = symbols_lists_array[(BASE + sym) as usize] as isize;
            let code = construct_huffman_code(bits as u8, sym as u16);
            replicate_value(
                root_table,
                table_offset + brotli_reverse_bits(key),
                step,
                table_size,
                code,
            );
            key += key_step;
        }
        step <<= 1;
        key_step >>= 1;
        bits += 1;
        if bits > table_bits {
            break;
        }
    }

    while total_size != table_size {
        root_table.copy_within(table_offset..table_offset + table_size, table_offset + table_size);
        table_size <<= 1;
    }

    key_step = BROTLI_REVERSE_BITS_LOWEST >> (root_bits - 1);
    let mut sub_key = BROTLI_REVERSE_BITS_LOWEST << 1;
    let mut sub_key_step = BROTLI_REVERSE_BITS_LOWEST;
    let mut step = 2usize;
    for len in (root_bits + 1)..=max_length {
        let mut sym = (len as isize) - BASE;
        while count[len] != 0 {
            if sub_key == (BROTLI_REVERSE_BITS_LOWEST << 1) {
                table_offset += table_size;
                table_bits = next_table_bit_size(count, len, root_bits);
                table_size = 1usize << table_bits;
                total_size += table_size;
                let root_idx = brotli_reverse_bits(key);
                key += key_step;
                root_table[root_idx] = construct_huffman_code(
                    (table_bits + root_bits) as u8,
                    (table_offset - root_idx) as u16,
                );
                sub_key = 0;
            }
            sym = symbols_lists_array[(BASE + sym) as usize] as isize;
            let code = construct_huffman_code((len - root_bits) as u8, sym as u16);
            replicate_value(
                root_table,
                table_offset + brotli_reverse_bits(sub_key),
                step,
                table_size,
                code,
            );
            sub_key += sub_key_step;
            count[len] -= 1;
        }
        step <<= 1;
        sub_key_step >>= 1;
    }

    total_size as u32
}

pub fn brotli_build_simple_huffman_table(
    table: &mut [HuffmanCode],
    root_bits: usize,
    val: &mut [u16],
    num_symbols: u32,
) -> u32 {
    let mut table_size = 1usize;
    let goal_size = 1usize << root_bits;
    match num_symbols {
        0 => {
            table[0] = construct_huffman_code(0, val[0]);
        }
        1 => {
            if val[1] > val[0] {
                table[0] = construct_huffman_code(1, val[0]);
                table[1] = construct_huffman_code(1, val[1]);
            } else {
                table[0] = construct_huffman_code(1, val[1]);
                table[1] = construct_huffman_code(1, val[0]);
            }
            table_size = 2;
        }
        2 => {
            table[0] = construct_huffman_code(1, val[0]);
            table[2] = construct_huffman_code(1, val[0]);
            if val[2] > val[1] {
                table[1] = construct_huffman_code(2, val[1]);
                table[3] = construct_huffman_code(2, val[2]);
            } else {
                table[1] = construct_huffman_code(2, val[2]);
                table[3] = construct_huffman_code(2, val[1]);
            }
            table_size = 4;
        }
        3 => {
            for i in 0..3 {
                for k in (i + 1)..4 {
                    if val[k] < val[i] {
                        val.swap(i, k);
                    }
                }
            }
            table[0] = construct_huffman_code(2, val[0]);
            table[2] = construct_huffman_code(2, val[1]);
            table[1] = construct_huffman_code(2, val[2]);
            table[3] = construct_huffman_code(2, val[3]);
            table_size = 4;
        }
        4 => {
            if val[3] < val[2] {
                val.swap(2, 3);
            }
            table[0] = construct_huffman_code(1, val[0]);
            table[1] = construct_huffman_code(2, val[1]);
            table[2] = construct_huffman_code(1, val[0]);
            table[3] = construct_huffman_code(3, val[2]);
            table[4] = construct_huffman_code(1, val[0]);
            table[5] = construct_huffman_code(2, val[1]);
            table[6] = construct_huffman_code(1, val[0]);
            table[7] = construct_huffman_code(3, val[3]);
            table_size = 8;
        }
        _ => {}
    }
    while table_size != goal_size {
        table.copy_within(0..table_size, table_size);
        table_size <<= 1;
    }
    goal_size as u32
}
