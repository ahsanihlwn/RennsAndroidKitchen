#![allow(dead_code)]

use crate::common::constants::*;
use crate::common::context::{ContextType, brotli_context, brotli_context_lut};
use crate::enc::command::{
    Command, get_copy_base, get_copy_extra, get_copy_length_code, get_insert_base,
    get_insert_extra, get_insert_length_code,
};
use crate::enc::entropy_encode::{
    HuffmanTree, brotli_convert_bit_depths_to_symbols, brotli_create_huffman_tree,
    brotli_set_depth, brotli_write_huffman_tree, sort_huffman_tree_items,
};
use crate::enc::entropy_encode_static::*;
use crate::enc::fast_log::log2_floor_non_zero;
use crate::enc::histogram::{
    HistogramCommand, HistogramDistance, HistogramLiteral, HistogramTrait,
};
use crate::enc::metablock::MetaBlockSplit;
use crate::enc::params::BrotliEncoderParams;
use crate::enc::write_bits::{brotli_write_bits, brotli_write_bits_prepare_storage};

pub static K_IS_BASE64: [u8; 256] = [
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1,
    0, 0, 0, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0,
    0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0,
    0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
];

pub const MAX_HUFFMAN_TREE_SIZE: usize = 2 * BROTLI_NUM_COMMAND_SYMBOLS + 1;
const MAX_SIMPLE_DISTANCE_ALPHABET_SIZE: usize =
    brotli_distance_alphabet_size(0, 0, BROTLI_LARGE_MAX_DISTANCE_BITS) as usize;

#[inline(always)]
fn block_length_prefix_code(len: u32) -> u32 {
    let mut code = if len >= 177 {
        if len >= 753 { 20 } else { 14 }
    } else if len >= 41 {
        7
    } else {
        0
    };
    while (code as usize) < (BROTLI_NUM_BLOCK_LEN_SYMBOLS - 1)
        && len >= K_BROTLI_PREFIX_CODE_RANGES[(code as usize) + 1].offset as u32
    {
        code += 1;
    }
    code
}

#[inline(always)]
pub fn get_block_length_prefix_code(len: u32) -> (usize, u32, u32) {
    let code = block_length_prefix_code(len) as usize;
    let n_extra = K_BROTLI_PREFIX_CODE_RANGES[code].nbits as u32;
    let extra = len - (K_BROTLI_PREFIX_CODE_RANGES[code].offset as u32);
    (code, n_extra, extra)
}

#[derive(Debug, Clone, Copy)]
struct BlockTypeCodeCalculator {
    last_type: usize,
    second_last_type: usize,
}

impl Default for BlockTypeCodeCalculator {
    fn default() -> Self {
        Self {
            last_type: 1,
            second_last_type: 0,
        }
    }
}

impl BlockTypeCodeCalculator {
    #[inline(always)]
    fn next(&mut self, type_: u8) -> usize {
        let t = type_ as usize;
        let type_code = if t == self.last_type + 1 {
            1
        } else if t == self.second_last_type {
            0
        } else {
            t + 2
        };
        self.second_last_type = self.last_type;
        self.last_type = t;
        type_code
    }
}

fn brotli_encode_mlen(length: usize) -> (u64, usize, u64) {
    let lg = if length == 1 {
        1
    } else {
        log2_floor_non_zero(length - 1) as usize + 1
    };
    let mnibbles = (if lg < 16 { 16 } else { lg + 3 }) / 4;
    ((length - 1) as u64, mnibbles * 4, (mnibbles - 4) as u64)
}

#[inline(always)]
fn store_command_extra(cmd: &Command, storage_ix: &mut usize, storage: &mut [u8]) {
    let copylen_code = cmd.copy_len_code();
    let inscode = get_insert_length_code(cmd.insert_len_ as usize);
    let copycode = get_copy_length_code(copylen_code as usize);
    let insnumextra = get_insert_extra(inscode) as usize;
    let insextraval = (cmd.insert_len_ - get_insert_base(inscode)) as u64;
    let copyextraval = (copylen_code - get_copy_base(copycode)) as u64;
    let bits = (copyextraval << insnumextra) | insextraval;
    brotli_write_bits(
        insnumextra + (get_copy_extra(copycode) as usize),
        bits,
        storage_ix,
        storage,
    );
}

#[derive(Debug, Clone)]
struct BlockSplitCode {
    type_code_calculator: BlockTypeCodeCalculator,
    type_depths: [u8; BROTLI_MAX_BLOCK_TYPE_SYMBOLS],
    type_bits: [u16; BROTLI_MAX_BLOCK_TYPE_SYMBOLS],
    length_depths: [u8; BROTLI_NUM_BLOCK_LEN_SYMBOLS],
    length_bits: [u16; BROTLI_NUM_BLOCK_LEN_SYMBOLS],
}

impl Default for BlockSplitCode {
    fn default() -> Self {
        Self {
            type_code_calculator: BlockTypeCodeCalculator::default(),
            type_depths: [0; BROTLI_MAX_BLOCK_TYPE_SYMBOLS],
            type_bits: [0; BROTLI_MAX_BLOCK_TYPE_SYMBOLS],
            length_depths: [0; BROTLI_NUM_BLOCK_LEN_SYMBOLS],
            length_bits: [0; BROTLI_NUM_BLOCK_LEN_SYMBOLS],
        }
    }
}

fn store_var_len_uint8(n: usize, storage_ix: &mut usize, storage: &mut [u8]) {
    if n == 0 {
        brotli_write_bits(1, 0, storage_ix, storage);
    } else {
        let nbits = log2_floor_non_zero(n) as usize;
        brotli_write_bits(1, 1, storage_ix, storage);
        brotli_write_bits(3, nbits as u64, storage_ix, storage);
        brotli_write_bits(nbits, (n - (1usize << nbits)) as u64, storage_ix, storage);
    }
}

fn store_compressed_meta_block_header(
    is_final_block: bool,
    length: usize,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    brotli_write_bits(1, is_final_block as u64, storage_ix, storage);
    if is_final_block {
        brotli_write_bits(1, 0, storage_ix, storage);
    }
    let (lenbits, nlenbits, nibblesbits) = brotli_encode_mlen(length);
    brotli_write_bits(2, nibblesbits, storage_ix, storage);
    brotli_write_bits(nlenbits, lenbits, storage_ix, storage);
    if !is_final_block {
        brotli_write_bits(1, 0, storage_ix, storage);
    }
}

fn brotli_store_uncompressed_meta_block_header(
    length: usize,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    brotli_write_bits(1, 0, storage_ix, storage);
    let (lenbits, nlenbits, nibblesbits) = brotli_encode_mlen(length);
    brotli_write_bits(2, nibblesbits, storage_ix, storage);
    brotli_write_bits(nlenbits, lenbits, storage_ix, storage);
    brotli_write_bits(1, 1, storage_ix, storage);
}

fn brotli_store_huffman_tree_of_huffman_tree_to_bit_mask(
    num_codes: i32,
    code_length_bitdepth: &[u8],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    const K_STORAGE_ORDER: [usize; BROTLI_CODE_LENGTH_CODES] = [
        1, 2, 3, 4, 0, 5, 17, 6, 16, 7, 8, 9, 10, 11, 12, 13, 14, 15,
    ];
    const K_HUFFMAN_BIT_LENGTH_HUFFMAN_CODE_SYMBOLS: [u64; 6] = [0, 7, 3, 2, 1, 15];
    const K_HUFFMAN_BIT_LENGTH_HUFFMAN_CODE_BIT_LENGTHS: [usize; 6] = [2, 4, 3, 2, 2, 4];

    let mut skip_some = 0usize;
    let mut codes_to_store = BROTLI_CODE_LENGTH_CODES;
    if num_codes > 1 {
        while codes_to_store > 0 {
            if code_length_bitdepth[K_STORAGE_ORDER[codes_to_store - 1]] != 0 {
                break;
            }
            codes_to_store -= 1;
        }
    }
    if code_length_bitdepth[K_STORAGE_ORDER[0]] == 0
        && code_length_bitdepth[K_STORAGE_ORDER[1]] == 0
    {
        skip_some = 2;
        if code_length_bitdepth[K_STORAGE_ORDER[2]] == 0 {
            skip_some = 3;
        }
    }
    brotli_write_bits(2, skip_some as u64, storage_ix, storage);
    for &ord in &K_STORAGE_ORDER[skip_some..codes_to_store] {
        let l = code_length_bitdepth[ord] as usize;
        brotli_write_bits(
            K_HUFFMAN_BIT_LENGTH_HUFFMAN_CODE_BIT_LENGTHS[l],
            K_HUFFMAN_BIT_LENGTH_HUFFMAN_CODE_SYMBOLS[l],
            storage_ix,
            storage,
        );
    }
}

fn brotli_store_huffman_tree_to_bit_mask(
    huffman_tree_size: usize,
    huffman_tree: &[u8],
    huffman_tree_extra_bits: &[u8],
    code_length_bitdepth: &[u8],
    code_length_bitdepth_symbols: &[u16],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    for i in 0..huffman_tree_size {
        let ix = huffman_tree[i] as usize;
        brotli_write_bits(
            code_length_bitdepth[ix] as usize,
            code_length_bitdepth_symbols[ix] as u64,
            storage_ix,
            storage,
        );
        if ix == BROTLI_REPEAT_PREVIOUS_CODE_LENGTH as usize {
            brotli_write_bits(2, huffman_tree_extra_bits[i] as u64, storage_ix, storage);
        } else if ix == BROTLI_REPEAT_ZERO_CODE_LENGTH as usize {
            brotli_write_bits(3, huffman_tree_extra_bits[i] as u64, storage_ix, storage);
        }
    }
}

fn store_simple_huffman_tree(
    depths: &[u8],
    symbols: &mut [usize; 4],
    num_symbols: usize,
    max_bits: usize,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    brotli_write_bits(2, 1, storage_ix, storage);
    brotli_write_bits(2, (num_symbols - 1) as u64, storage_ix, storage);

    for i in 0..num_symbols {
        for j in (i + 1)..num_symbols {
            if depths[symbols[j]] < depths[symbols[i]] {
                symbols.swap(j, i);
            }
        }
    }

    if num_symbols == 2 {
        brotli_write_bits(max_bits, symbols[0] as u64, storage_ix, storage);
        brotli_write_bits(max_bits, symbols[1] as u64, storage_ix, storage);
    } else if num_symbols == 3 {
        brotli_write_bits(max_bits, symbols[0] as u64, storage_ix, storage);
        brotli_write_bits(max_bits, symbols[1] as u64, storage_ix, storage);
        brotli_write_bits(max_bits, symbols[2] as u64, storage_ix, storage);
    } else {
        brotli_write_bits(max_bits, symbols[0] as u64, storage_ix, storage);
        brotli_write_bits(max_bits, symbols[1] as u64, storage_ix, storage);
        brotli_write_bits(max_bits, symbols[2] as u64, storage_ix, storage);
        brotli_write_bits(max_bits, symbols[3] as u64, storage_ix, storage);
        brotli_write_bits(
            1,
            if depths[symbols[0]] == 1 { 1 } else { 0 },
            storage_ix,
            storage,
        );
    }
}

pub fn brotli_store_huffman_tree(
    depths: &[u8],
    num: usize,
    tree: &mut [HuffmanTree],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let mut huffman_tree = [0u8; BROTLI_NUM_COMMAND_SYMBOLS];
    let mut huffman_tree_extra_bits = [0u8; BROTLI_NUM_COMMAND_SYMBOLS];
    let mut huffman_tree_size = 0usize;
    let mut code_length_bitdepth = [0u8; BROTLI_CODE_LENGTH_CODES];
    let mut code_length_bitdepth_symbols = [0u16; BROTLI_CODE_LENGTH_CODES];
    let mut huffman_tree_histogram = [0u32; BROTLI_CODE_LENGTH_CODES];
    let mut num_codes = 0i32;
    let mut code = 0usize;

    brotli_write_huffman_tree(
        depths,
        num,
        &mut huffman_tree_size,
        &mut huffman_tree,
        &mut huffman_tree_extra_bits,
    );

    for &sym in &huffman_tree[..huffman_tree_size] {
        huffman_tree_histogram[sym as usize] += 1;
    }

    for (i, &c) in huffman_tree_histogram.iter().enumerate() {
        if c != 0 {
            if num_codes == 0 {
                code = i;
                num_codes = 1;
            } else if num_codes == 1 {
                num_codes = 2;
                break;
            }
        }
    }

    brotli_create_huffman_tree(
        &huffman_tree_histogram,
        BROTLI_CODE_LENGTH_CODES,
        5,
        tree,
        &mut code_length_bitdepth,
    );
    brotli_convert_bit_depths_to_symbols(
        &code_length_bitdepth,
        BROTLI_CODE_LENGTH_CODES,
        &mut code_length_bitdepth_symbols,
    );

    brotli_store_huffman_tree_of_huffman_tree_to_bit_mask(
        num_codes,
        &code_length_bitdepth,
        storage_ix,
        storage,
    );

    if num_codes == 1 {
        code_length_bitdepth[code] = 0;
    }

    brotli_store_huffman_tree_to_bit_mask(
        huffman_tree_size,
        &huffman_tree,
        &huffman_tree_extra_bits,
        &code_length_bitdepth,
        &code_length_bitdepth_symbols,
        storage_ix,
        storage,
    );
}

fn build_and_store_huffman_tree(
    histogram: &[u32],
    histogram_length: usize,
    alphabet_size: usize,
    tree: &mut [HuffmanTree],
    depth: &mut [u8],
    bits: &mut [u16],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let mut count = 0usize;
    let mut s4 = [0usize; 4];
    for (i, &v) in histogram[..histogram_length].iter().enumerate() {
        if v != 0 {
            if count < 4 {
                s4[count] = i;
            } else if count > 4 {
                break;
            }
            count += 1;
        }
    }

    let mut max_bits = 0usize;
    let mut max_bits_counter = alphabet_size - 1;
    while max_bits_counter != 0 {
        max_bits_counter >>= 1;
        max_bits += 1;
    }

    if count <= 1 {
        brotli_write_bits(4, 1, storage_ix, storage);
        brotli_write_bits(max_bits, s4[0] as u64, storage_ix, storage);
        depth[s4[0]] = 0;
        bits[s4[0]] = 0;
        return;
    }

    depth[..histogram_length].fill(0);
    brotli_create_huffman_tree(histogram, histogram_length, 15, tree, depth);
    brotli_convert_bit_depths_to_symbols(depth, histogram_length, bits);

    if count <= 4 {
        store_simple_huffman_tree(depth, &mut s4, count, max_bits, storage_ix, storage);
    } else {
        brotli_store_huffman_tree(depth, histogram_length, tree, storage_ix, storage);
    }
}

pub fn brotli_build_and_store_huffman_tree_fast(
    tree: &mut [HuffmanTree],
    histogram: &[u32],
    histogram_total: usize,
    max_bits: usize,
    depth: &mut [u8],
    bits: &mut [u16],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let mut count = 0usize;
    let mut symbols = [0usize; 4];
    let mut length = 0usize;
    let mut total = histogram_total;
    while total != 0 {
        if histogram[length] != 0 {
            if count < 4 {
                symbols[count] = length;
            }
            count += 1;
            total -= histogram[length] as usize;
        }
        length += 1;
    }

    if count <= 1 {
        brotli_write_bits(4, 1, storage_ix, storage);
        brotli_write_bits(max_bits, symbols[0] as u64, storage_ix, storage);
        depth[symbols[0]] = 0;
        bits[symbols[0]] = 0;
        return;
    }

    depth[..length].fill(0);
    let mut count_limit = 1u32;
    loop {
        let mut node_idx = 0usize;
        let mut l = length;
        while l != 0 {
            l -= 1;
            if histogram[l] != 0 {
                let c = histogram[l].max(count_limit);
                tree[node_idx].init(c, -1, l as i16);
                node_idx += 1;
            }
        }
        let n = node_idx;
        sort_fast_huffman_tree_items(tree, n);

        let mut sentinel = HuffmanTree::default();
        sentinel.init(u32::MAX, -1, -1);
        tree[n] = sentinel;
        tree[n + 1] = sentinel;
        node_idx = n + 2;

        let mut i = 0usize;
        let mut j = n + 1;
        for _ in (1..n).rev() {
            let left = if tree[i].total_count_ <= tree[j].total_count_ {
                let v = i;
                i += 1;
                v
            } else {
                let v = j;
                j += 1;
                v
            };
            let right = if tree[i].total_count_ <= tree[j].total_count_ {
                let v = i;
                i += 1;
                v
            } else {
                let v = j;
                j += 1;
                v
            };
            tree[node_idx - 1].total_count_ =
                tree[left].total_count_.wrapping_add(tree[right].total_count_);
            tree[node_idx - 1].index_left_ = left as i16;
            tree[node_idx - 1].index_right_or_value_ = right as i16;
            tree[node_idx] = sentinel;
            node_idx += 1;
        }
        if brotli_set_depth((2 * n - 1) as i32, tree, depth, 14) {
            break;
        }
        count_limit *= 2;
    }

    brotli_convert_bit_depths_to_symbols(depth, length, bits);
    if count <= 4 {
        brotli_write_bits(2, 1, storage_ix, storage);
        brotli_write_bits(2, (count - 1) as u64, storage_ix, storage);
        for i in 0..count {
            for j in (i + 1)..count {
                if depth[symbols[j]] < depth[symbols[i]] {
                    symbols.swap(j, i);
                }
            }
        }
        if count == 2 {
            brotli_write_bits(max_bits, symbols[0] as u64, storage_ix, storage);
            brotli_write_bits(max_bits, symbols[1] as u64, storage_ix, storage);
        } else if count == 3 {
            brotli_write_bits(max_bits, symbols[0] as u64, storage_ix, storage);
            brotli_write_bits(max_bits, symbols[1] as u64, storage_ix, storage);
            brotli_write_bits(max_bits, symbols[2] as u64, storage_ix, storage);
        } else {
            brotli_write_bits(max_bits, symbols[0] as u64, storage_ix, storage);
            brotli_write_bits(max_bits, symbols[1] as u64, storage_ix, storage);
            brotli_write_bits(max_bits, symbols[2] as u64, storage_ix, storage);
            brotli_write_bits(max_bits, symbols[3] as u64, storage_ix, storage);
            brotli_write_bits(
                1,
                if depth[symbols[0]] == 1 { 1 } else { 0 },
                storage_ix,
                storage,
            );
        }
    } else {
        let mut previous_value = 8u8;
        store_static_code_length_code(storage_ix, storage);

        let mut i = 0usize;
        while i < length {
            let value = depth[i];
            let mut reps = 1usize;
            let mut k = i + 1;
            while k < length && depth[k] == value {
                reps += 1;
                k += 1;
            }
            i += reps;
            if value == 0 {
                brotli_write_bits(
                    K_ZERO_REPS_DEPTH[reps] as usize,
                    K_ZERO_REPS_BITS[reps],
                    storage_ix,
                    storage,
                );
            } else {
                if previous_value != value {
                    brotli_write_bits(
                        K_CODE_LENGTH_DEPTH[value as usize] as usize,
                        K_CODE_LENGTH_BITS[value as usize] as u64,
                        storage_ix,
                        storage,
                    );
                    reps -= 1;
                }
                if reps < 3 {
                    while reps != 0 {
                        reps -= 1;
                        brotli_write_bits(
                            K_CODE_LENGTH_DEPTH[value as usize] as usize,
                            K_CODE_LENGTH_BITS[value as usize] as u64,
                            storage_ix,
                            storage,
                        );
                    }
                } else {
                    reps -= 3;
                    brotli_write_bits(
                        K_NON_ZERO_REPS_DEPTH[reps] as usize,
                        K_NON_ZERO_REPS_BITS[reps],
                        storage_ix,
                        storage,
                    );
                }
                previous_value = value;
            }
        }
    }
}

fn sort_fast_huffman_tree_items(items: &mut [HuffmanTree], n: usize) {
    const GAPS: [usize; 6] = [132, 57, 23, 10, 4, 1];
    if n < 13 {
        for i in 1..n {
            let mut j = i;
            let tmp = items[j];
            while j > 0 && tmp.total_count_ < items[j - 1].total_count_ {
                items[j] = items[j - 1];
                j -= 1;
            }
            items[j] = tmp;
        }
        return;
    }
    let start_gap = if n < 57 { 2 } else { 0 };
    for &gap in &GAPS[start_gap..] {
        for i in gap..n {
            let mut j = i;
            let tmp = items[j];
            while j >= gap && tmp.total_count_ < items[j - gap].total_count_ {
                items[j] = items[j - gap];
                j -= gap;
            }
            items[j] = tmp;
        }
    }
    let _ = sort_huffman_tree_items;
}

fn move_to_front(v: &mut [u8], index: usize) {
    let value = v[index];
    for i in (1..=index).rev() {
        v[i] = v[i - 1];
    }
    v[0] = value;
}

fn move_to_front_transform(v_in: &[u32], v_out: &mut [u32]) {
    if v_in.is_empty() {
        return;
    }
    let max_value = *v_in.iter().max().unwrap_or(&0) as usize;
    debug_assert!(max_value < 256);
    let mtf_size = max_value + 1;
    let mut mtf = [0u8; 256];
    for i in 0..mtf_size {
        mtf[i] = i as u8;
    }
    for (i, &val) in v_in.iter().enumerate() {
        let index = mtf[..mtf_size].iter().position(|&x| x == val as u8).unwrap();
        v_out[i] = index as u32;
        move_to_front(&mut mtf, index);
    }
}

fn run_length_code_zeros(
    in_size: usize,
    v: &mut [u32],
    out_size: &mut usize,
    max_run_length_prefix: &mut u32,
) {
    let mut max_reps = 0u32;
    let mut i = 0usize;
    while i < in_size {
        let mut reps = 0u32;
        while i < in_size && v[i] != 0 {
            i += 1;
        }
        while i < in_size && v[i] == 0 {
            reps += 1;
            i += 1;
        }
        max_reps = max_reps.max(reps);
    }
    let max_prefix = if max_reps > 0 {
        log2_floor_non_zero(max_reps as usize)
    } else {
        0
    };
    *max_run_length_prefix = max_prefix.min(*max_run_length_prefix);
    *out_size = 0;
    let mut i = 0usize;
    while i < in_size {
        if v[i] != 0 {
            v[*out_size] = v[i] + *max_run_length_prefix;
            i += 1;
            *out_size += 1;
        } else {
            let mut reps = 1u32;
            let mut k = i + 1;
            while k < in_size && v[k] == 0 {
                reps += 1;
                k += 1;
            }
            i += reps as usize;
            while reps != 0 {
                if reps < (2u32 << *max_run_length_prefix) {
                    let run_length_prefix = log2_floor_non_zero(reps as usize);
                    let extra_bits = reps - (1u32 << run_length_prefix);
                    v[*out_size] = run_length_prefix + (extra_bits << 9);
                    *out_size += 1;
                    break;
                } else {
                    let extra_bits = (1u32 << *max_run_length_prefix) - 1;
                    v[*out_size] = *max_run_length_prefix + (extra_bits << 9);
                    reps -= (2u32 << *max_run_length_prefix) - 1;
                    *out_size += 1;
                }
            }
        }
    }
}

const SYMBOL_BITS: usize = 9;

struct EncodeContextMapArena {
    histogram: [u32; BROTLI_MAX_CONTEXT_MAP_SYMBOLS],
    depths: [u8; BROTLI_MAX_CONTEXT_MAP_SYMBOLS],
    bits: [u16; BROTLI_MAX_CONTEXT_MAP_SYMBOLS],
}

impl Default for EncodeContextMapArena {
    fn default() -> Self {
        Self {
            histogram: [0; BROTLI_MAX_CONTEXT_MAP_SYMBOLS],
            depths: [0; BROTLI_MAX_CONTEXT_MAP_SYMBOLS],
            bits: [0; BROTLI_MAX_CONTEXT_MAP_SYMBOLS],
        }
    }
}

fn encode_context_map(
    arena: &mut EncodeContextMapArena,
    context_map: &[u32],
    num_clusters: usize,
    tree: &mut [HuffmanTree],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let context_map_size = context_map.len();
    let mut max_run_length_prefix = 6u32;
    let mut num_rle_symbols = 0usize;
    const K_SYMBOL_MASK: u32 = (1u32 << SYMBOL_BITS) - 1;

    store_var_len_uint8(num_clusters - 1, storage_ix, storage);
    if num_clusters == 1 {
        return;
    }

    let mut rle_symbols = vec![0u32; context_map_size];
    move_to_front_transform(context_map, &mut rle_symbols);
    run_length_code_zeros(
        context_map_size,
        &mut rle_symbols,
        &mut num_rle_symbols,
        &mut max_run_length_prefix,
    );
    arena.histogram.fill(0);
    for &sym in &rle_symbols[..num_rle_symbols] {
        arena.histogram[(sym & K_SYMBOL_MASK) as usize] += 1;
    }
    let use_rle = max_run_length_prefix > 0;
    brotli_write_bits(1, use_rle as u64, storage_ix, storage);
    if use_rle {
        brotli_write_bits(4, (max_run_length_prefix - 1) as u64, storage_ix, storage);
    }
    let alpha = num_clusters + (max_run_length_prefix as usize);
    build_and_store_huffman_tree(
        &arena.histogram,
        alpha,
        alpha,
        tree,
        &mut arena.depths,
        &mut arena.bits,
        storage_ix,
        storage,
    );
    for &sym in &rle_symbols[..num_rle_symbols] {
        let rle_symbol = (sym & K_SYMBOL_MASK) as usize;
        let extra_bits_val = sym >> SYMBOL_BITS;
        brotli_write_bits(
            arena.depths[rle_symbol] as usize,
            arena.bits[rle_symbol] as u64,
            storage_ix,
            storage,
        );
        if rle_symbol > 0 && rle_symbol <= max_run_length_prefix as usize {
            brotli_write_bits(rle_symbol, extra_bits_val as u64, storage_ix, storage);
        }
    }
    brotli_write_bits(1, 1, storage_ix, storage);
}

#[inline(always)]
fn store_block_switch(
    code: &mut BlockSplitCode,
    block_len: u32,
    block_type: u8,
    is_first_block: bool,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let typecode = code.type_code_calculator.next(block_type);
    if !is_first_block {
        brotli_write_bits(
            code.type_depths[typecode] as usize,
            code.type_bits[typecode] as u64,
            storage_ix,
            storage,
        );
    }
    let (lencode, len_nextra, len_extra) = get_block_length_prefix_code(block_len);
    brotli_write_bits(
        code.length_depths[lencode] as usize,
        code.length_bits[lencode] as u64,
        storage_ix,
        storage,
    );
    brotli_write_bits(len_nextra as usize, len_extra as u64, storage_ix, storage);
}

fn build_and_store_block_split_code(
    types: &[u8],
    lengths: &[u32],
    num_blocks: usize,
    num_types: usize,
    tree: &mut [HuffmanTree],
    code: &mut BlockSplitCode,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let mut type_histo = [0u32; BROTLI_MAX_BLOCK_TYPE_SYMBOLS];
    let mut length_histo = [0u32; BROTLI_NUM_BLOCK_LEN_SYMBOLS];
    let mut calc = BlockTypeCodeCalculator::default();
    for i in 0..num_blocks {
        let type_code = calc.next(types[i]);
        if i != 0 {
            type_histo[type_code] += 1;
        }
        length_histo[block_length_prefix_code(lengths[i]) as usize] += 1;
    }
    store_var_len_uint8(num_types - 1, storage_ix, storage);
    if num_types > 1 {
        build_and_store_huffman_tree(
            &type_histo,
            num_types + 2,
            num_types + 2,
            tree,
            &mut code.type_depths,
            &mut code.type_bits,
            storage_ix,
            storage,
        );
        build_and_store_huffman_tree(
            &length_histo,
            BROTLI_NUM_BLOCK_LEN_SYMBOLS,
            BROTLI_NUM_BLOCK_LEN_SYMBOLS,
            tree,
            &mut code.length_depths,
            &mut code.length_bits,
            storage_ix,
            storage,
        );
        store_block_switch(code, lengths[0], types[0], true, storage_ix, storage);
    }
}

fn store_trivial_context_map(
    arena: &mut EncodeContextMapArena,
    num_types: usize,
    context_bits: usize,
    tree: &mut [HuffmanTree],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    store_var_len_uint8(num_types - 1, storage_ix, storage);
    if num_types > 1 {
        let repeat_code = context_bits - 1;
        let repeat_bits = (1usize << repeat_code) - 1;
        let alphabet_size = num_types + repeat_code;
        arena.histogram[..alphabet_size].fill(0);
        brotli_write_bits(1, 1, storage_ix, storage);
        brotli_write_bits(4, (repeat_code - 1) as u64, storage_ix, storage);
        arena.histogram[repeat_code] = num_types as u32;
        arena.histogram[0] = 1;
        for i in context_bits..alphabet_size {
            arena.histogram[i] = 1;
        }
        build_and_store_huffman_tree(
            &arena.histogram,
            alphabet_size,
            alphabet_size,
            tree,
            &mut arena.depths,
            &mut arena.bits,
            storage_ix,
            storage,
        );
        for i in 0..num_types {
            let code = if i == 0 { 0 } else { i + context_bits - 1 };
            brotli_write_bits(
                arena.depths[code] as usize,
                arena.bits[code] as u64,
                storage_ix,
                storage,
            );
            brotli_write_bits(
                arena.depths[repeat_code] as usize,
                arena.bits[repeat_code] as u64,
                storage_ix,
                storage,
            );
            brotli_write_bits(repeat_code, repeat_bits as u64, storage_ix, storage);
        }
        brotli_write_bits(1, 1, storage_ix, storage);
    }
}

struct BlockEncoder<'a> {
    histogram_length: usize,
    num_block_types: usize,
    block_types: &'a [u8],
    block_lengths: &'a [u32],
    num_blocks: usize,
    block_split_code: BlockSplitCode,
    block_ix: usize,
    block_len: u32,
    entropy_ix: usize,
    depths: Vec<u8>,
    bits: Vec<u16>,
}

impl<'a> BlockEncoder<'a> {
    fn new(
        histogram_length: usize,
        num_block_types: usize,
        block_types: &'a [u8],
        block_lengths: &'a [u32],
        num_blocks: usize,
    ) -> Self {
        Self {
            histogram_length,
            num_block_types,
            block_types,
            block_lengths,
            num_blocks,
            block_split_code: BlockSplitCode::default(),
            block_ix: 0,
            block_len: if num_blocks == 0 { 0 } else { block_lengths[0] },
            entropy_ix: 0,
            depths: Vec::new(),
            bits: Vec::new(),
        }
    }

    fn build_and_store_block_switch_entropy_codes(
        &mut self,
        tree: &mut [HuffmanTree],
        storage_ix: &mut usize,
        storage: &mut [u8],
    ) {
        build_and_store_block_split_code(
            self.block_types,
            self.block_lengths,
            self.num_blocks,
            self.num_block_types,
            tree,
            &mut self.block_split_code,
            storage_ix,
            storage,
        );
    }

    fn build_and_store_entropy_codes<H: HistogramTrait>(
        &mut self,
        histograms: &[H],
        alphabet_size: usize,
        tree: &mut [HuffmanTree],
        is_base64_histogram: Option<&[u8]>,
        storage_ix: &mut usize,
        storage: &mut [u8],
    ) {
        let histograms_size = histograms.len();
        let table_size = histograms_size * self.histogram_length;
        self.depths = vec![0u8; table_size];
        self.bits = vec![0u16; table_size];

        for i in 0..histograms_size {
            let ix = i * self.histogram_length;
            if self.histogram_length == 256
                && is_base64_histogram.is_some_and(|m| i < m.len() && m[i] != 0)
            {
                let hdata = histograms[i].data();
                let all_base64 = (0..256).all(|k| !(hdata[k] > 0 && K_IS_BASE64[k] == 0));
                if all_base64 {
                    self.depths[ix..ix + 256].fill(0);
                    for k in 0..256 {
                        if K_IS_BASE64[k] != 0 {
                            self.depths[ix + k] = 6;
                        }
                    }
                    brotli_convert_bit_depths_to_symbols(
                        &self.depths[ix..ix + 256],
                        256,
                        &mut self.bits[ix..ix + 256],
                    );
                    brotli_store_huffman_tree(
                        &self.depths[ix..ix + 256],
                        256,
                        tree,
                        storage_ix,
                        storage,
                    );
                    continue;
                }
            }
            build_and_store_huffman_tree(
                histograms[i].data(),
                self.histogram_length,
                alphabet_size,
                tree,
                &mut self.depths[ix..ix + self.histogram_length],
                &mut self.bits[ix..ix + self.histogram_length],
                storage_ix,
                storage,
            );
        }
    }

    #[inline(always)]
    fn store_symbol(&mut self, symbol: usize, storage_ix: &mut usize, storage: &mut [u8]) {
        if self.block_len == 0 {
            self.block_ix += 1;
            let block_len = self.block_lengths[self.block_ix];
            let block_type = self.block_types[self.block_ix];
            self.block_len = block_len;
            self.entropy_ix = (block_type as usize) * self.histogram_length;
            store_block_switch(
                &mut self.block_split_code,
                block_len,
                block_type,
                false,
                storage_ix,
                storage,
            );
        }
        self.block_len -= 1;
        let ix = self.entropy_ix + symbol;
        brotli_write_bits(
            self.depths[ix] as usize,
            self.bits[ix] as u64,
            storage_ix,
            storage,
        );
    }

    #[inline(always)]
    fn store_symbol_with_context(
        &mut self,
        symbol: usize,
        context: usize,
        context_map: &[u32],
        storage_ix: &mut usize,
        storage: &mut [u8],
        context_bits: usize,
    ) {
        if self.block_len == 0 {
            self.block_ix += 1;
            let block_len = self.block_lengths[self.block_ix];
            let block_type = self.block_types[self.block_ix];
            self.block_len = block_len;
            self.entropy_ix = (block_type as usize) << context_bits;
            store_block_switch(
                &mut self.block_split_code,
                block_len,
                block_type,
                false,
                storage_ix,
                storage,
            );
        }
        self.block_len -= 1;
        let histo_ix = context_map[self.entropy_ix + context] as usize;
        let ix = histo_ix * self.histogram_length + symbol;
        brotli_write_bits(
            self.depths[ix] as usize,
            self.bits[ix] as u64,
            storage_ix,
            storage,
        );
    }
}

#[inline(always)]
pub fn jump_to_byte_boundary(storage_ix: &mut usize, storage: &mut [u8]) {
    *storage_ix = (*storage_ix + 7) & !7;
    storage[*storage_ix >> 3] = 0;
}

pub fn brotli_store_meta_block(
    input: &[u8],
    start_pos: usize,
    length: usize,
    mask: usize,
    mut prev_byte: u8,
    mut prev_byte2: u8,
    is_last: bool,
    params: &BrotliEncoderParams,
    literal_context_mode: ContextType,
    commands: &[Command],
    mb: &MetaBlockSplit,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let mut pos = start_pos;
    let num_distance_symbols = params.dist.alphabet_size_max as usize;
    let num_effective_distance_symbols = params.dist.alphabet_size_limit as usize;
    let mut tree = vec![HuffmanTree::default(); MAX_HUFFMAN_TREE_SIZE];
    let literal_context_lut = brotli_context_lut(literal_context_mode);
    let mut context_map_arena = EncodeContextMapArena::default();
    let dist = &params.dist;

    store_compressed_meta_block_header(is_last, length, storage_ix, storage);

    let mut literal_enc = BlockEncoder::new(
        BROTLI_NUM_LITERAL_SYMBOLS,
        mb.literal_split.num_types,
        &mb.literal_split.types,
        &mb.literal_split.lengths,
        mb.literal_split.num_blocks,
    );
    let mut command_enc = BlockEncoder::new(
        BROTLI_NUM_COMMAND_SYMBOLS,
        mb.command_split.num_types,
        &mb.command_split.types,
        &mb.command_split.lengths,
        mb.command_split.num_blocks,
    );
    let mut distance_enc = BlockEncoder::new(
        num_effective_distance_symbols,
        mb.distance_split.num_types,
        &mb.distance_split.types,
        &mb.distance_split.lengths,
        mb.distance_split.num_blocks,
    );

    literal_enc.build_and_store_block_switch_entropy_codes(&mut tree, storage_ix, storage);
    command_enc.build_and_store_block_switch_entropy_codes(&mut tree, storage_ix, storage);
    distance_enc.build_and_store_block_switch_entropy_codes(&mut tree, storage_ix, storage);

    brotli_write_bits(2, dist.distance_postfix_bits as u64, storage_ix, storage);
    brotli_write_bits(
        4,
        (dist.num_direct_distance_codes >> dist.distance_postfix_bits) as u64,
        storage_ix,
        storage,
    );
    for _ in 0..mb.literal_split.num_types {
        brotli_write_bits(2, literal_context_mode as u64, storage_ix, storage);
    }

    match &mb.literal_context_map {
        None => {
            store_trivial_context_map(
                &mut context_map_arena,
                mb.literal_histograms.len(),
                BROTLI_LITERAL_CONTEXT_BITS,
                &mut tree,
                storage_ix,
                storage,
            );
        }
        Some(cmap) => {
            encode_context_map(
                &mut context_map_arena,
                cmap,
                mb.literal_histograms.len(),
                &mut tree,
                storage_ix,
                storage,
            );
        }
    }

    match &mb.distance_context_map {
        None => {
            store_trivial_context_map(
                &mut context_map_arena,
                mb.distance_histograms.len(),
                BROTLI_DISTANCE_CONTEXT_BITS,
                &mut tree,
                storage_ix,
                storage,
            );
        }
        Some(cmap) => {
            encode_context_map(
                &mut context_map_arena,
                cmap,
                mb.distance_histograms.len(),
                &mut tree,
                storage_ix,
                storage,
            );
        }
    }

    {
        let mut is_base64_histogram = [0u8; 256];
        let max_type_id = mb.literal_split.num_types.min(256);
        for type_id in 0..max_type_id {
            if (mb.literal_is_base64[type_id >> 3] & (1u8 << (type_id & 7))) != 0 {
                if let Some(ref cmap) = mb.literal_context_map {
                    for j in 0..(1usize << BROTLI_LITERAL_CONTEXT_BITS) {
                        let b64_histo_id =
                            cmap[(type_id << BROTLI_LITERAL_CONTEXT_BITS) + j] as usize;
                        if b64_histo_id < 256 {
                            is_base64_histogram[b64_histo_id] = 1;
                        }
                    }
                } else {
                    is_base64_histogram[type_id] = 1;
                }
            }
        }
        literal_enc.build_and_store_entropy_codes(
            &mb.literal_histograms,
            BROTLI_NUM_LITERAL_SYMBOLS,
            &mut tree,
            Some(&is_base64_histogram),
            storage_ix,
            storage,
        );
    }

    command_enc.build_and_store_entropy_codes(
        &mb.command_histograms,
        BROTLI_NUM_COMMAND_SYMBOLS,
        &mut tree,
        None,
        storage_ix,
        storage,
    );
    distance_enc.build_and_store_entropy_codes(
        &mb.distance_histograms,
        num_distance_symbols,
        &mut tree,
        None,
        storage_ix,
        storage,
    );

    for cmd in commands {
        let cmd_code = cmd.cmd_prefix_ as usize;
        command_enc.store_symbol(cmd_code, storage_ix, storage);
        store_command_extra(cmd, storage_ix, storage);
        match &mb.literal_context_map {
            None => {
                for _ in 0..cmd.insert_len_ {
                    literal_enc.store_symbol(input[pos & mask] as usize, storage_ix, storage);
                    pos += 1;
                }
            }
            Some(cmap) => {
                for _ in 0..cmd.insert_len_ {
                    let context =
                        brotli_context(prev_byte, prev_byte2, literal_context_lut) as usize;
                    let literal = input[pos & mask];
                    literal_enc.store_symbol_with_context(
                        literal as usize,
                        context,
                        cmap,
                        storage_ix,
                        storage,
                        BROTLI_LITERAL_CONTEXT_BITS,
                    );
                    prev_byte2 = prev_byte;
                    prev_byte = literal;
                    pos += 1;
                }
            }
        }
        let copy_len = cmd.copy_len() as usize;
        pos += copy_len;
        if copy_len != 0 {
            prev_byte2 = input[pos.wrapping_sub(2) & mask];
            prev_byte = input[pos.wrapping_sub(1) & mask];
            if cmd.cmd_prefix_ >= 128 {
                let dist_code = (cmd.dist_prefix_ & 0x3FF) as usize;
                let distnumextra = (cmd.dist_prefix_ >> 10) as usize;
                let distextra = cmd.dist_extra_ as u64;
                match &mb.distance_context_map {
                    None => distance_enc.store_symbol(dist_code, storage_ix, storage),
                    Some(cmap) => {
                        let context = cmd.distance_context() as usize;
                        distance_enc.store_symbol_with_context(
                            dist_code,
                            context,
                            cmap,
                            storage_ix,
                            storage,
                            BROTLI_DISTANCE_CONTEXT_BITS,
                        );
                    }
                }
                brotli_write_bits(distnumextra, distextra, storage_ix, storage);
            }
        }
    }
    if is_last {
        jump_to_byte_boundary(storage_ix, storage);
    }
}

fn build_histograms(
    input: &[u8],
    start_pos: usize,
    mask: usize,
    commands: &[Command],
    lit_histo: &mut HistogramLiteral,
    cmd_histo: &mut HistogramCommand,
    dist_histo: &mut HistogramDistance,
) {
    let mut pos = start_pos;
    for cmd in commands {
        cmd_histo.add(cmd.cmd_prefix_ as usize);
        for _ in 0..cmd.insert_len_ {
            lit_histo.add(input[pos & mask] as usize);
            pos += 1;
        }
        let copy_len = cmd.copy_len() as usize;
        pos += copy_len;
        if copy_len != 0 && cmd.cmd_prefix_ >= 128 {
            dist_histo.add((cmd.dist_prefix_ & 0x3FF) as usize);
        }
    }
}

fn store_data_with_huffman_codes(
    input: &[u8],
    start_pos: usize,
    mask: usize,
    commands: &[Command],
    lit_depth: &[u8],
    lit_bits: &[u16],
    cmd_depth: &[u8],
    cmd_bits: &[u16],
    dist_depth: &[u8],
    dist_bits: &[u16],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let mut pos = start_pos;
    for cmd in commands {
        let cmd_code = cmd.cmd_prefix_ as usize;
        brotli_write_bits(
            cmd_depth[cmd_code] as usize,
            cmd_bits[cmd_code] as u64,
            storage_ix,
            storage,
        );
        store_command_extra(cmd, storage_ix, storage);
        for _ in 0..cmd.insert_len_ {
            let literal = input[pos & mask] as usize;
            brotli_write_bits(
                lit_depth[literal] as usize,
                lit_bits[literal] as u64,
                storage_ix,
                storage,
            );
            pos += 1;
        }
        let copy_len = cmd.copy_len() as usize;
        pos += copy_len;
        if copy_len != 0 && cmd.cmd_prefix_ >= 128 {
            let dist_code = (cmd.dist_prefix_ & 0x3FF) as usize;
            let distnumextra = (cmd.dist_prefix_ >> 10) as usize;
            let distextra = cmd.dist_extra_ as u64;
            brotli_write_bits(
                dist_depth[dist_code] as usize,
                dist_bits[dist_code] as u64,
                storage_ix,
                storage,
            );
            brotli_write_bits(distnumextra, distextra, storage_ix, storage);
        }
    }
}

struct MetablockArena {
    lit_histo: HistogramLiteral,
    cmd_histo: HistogramCommand,
    dist_histo: HistogramDistance,
    lit_depth: [u8; BROTLI_NUM_LITERAL_SYMBOLS],
    lit_bits: [u16; BROTLI_NUM_LITERAL_SYMBOLS],
    cmd_depth: [u8; BROTLI_NUM_COMMAND_SYMBOLS],
    cmd_bits: [u16; BROTLI_NUM_COMMAND_SYMBOLS],
    dist_depth: [u8; MAX_SIMPLE_DISTANCE_ALPHABET_SIZE],
    dist_bits: [u16; MAX_SIMPLE_DISTANCE_ALPHABET_SIZE],
    tree: Vec<HuffmanTree>,
}

impl Default for MetablockArena {
    fn default() -> Self {
        Self {
            lit_histo: HistogramLiteral::default(),
            cmd_histo: HistogramCommand::default(),
            dist_histo: HistogramDistance::default(),
            lit_depth: [0; BROTLI_NUM_LITERAL_SYMBOLS],
            lit_bits: [0; BROTLI_NUM_LITERAL_SYMBOLS],
            cmd_depth: [0; BROTLI_NUM_COMMAND_SYMBOLS],
            cmd_bits: [0; BROTLI_NUM_COMMAND_SYMBOLS],
            dist_depth: [0; MAX_SIMPLE_DISTANCE_ALPHABET_SIZE],
            dist_bits: [0; MAX_SIMPLE_DISTANCE_ALPHABET_SIZE],
            tree: vec![HuffmanTree::default(); MAX_HUFFMAN_TREE_SIZE],
        }
    }
}

pub fn brotli_store_meta_block_trivial(
    input: &[u8],
    start_pos: usize,
    length: usize,
    mask: usize,
    is_last: bool,
    params: &BrotliEncoderParams,
    commands: &[Command],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let mut arena = MetablockArena::default();
    let num_distance_symbols = params.dist.alphabet_size_max as usize;

    store_compressed_meta_block_header(is_last, length, storage_ix, storage);

    arena.lit_histo.clear();
    arena.cmd_histo.clear();
    arena.dist_histo.clear();

    build_histograms(
        input,
        start_pos,
        mask,
        commands,
        &mut arena.lit_histo,
        &mut arena.cmd_histo,
        &mut arena.dist_histo,
    );

    brotli_write_bits(13, 0, storage_ix, storage);

    build_and_store_huffman_tree(
        &arena.lit_histo.data_,
        BROTLI_NUM_LITERAL_SYMBOLS,
        BROTLI_NUM_LITERAL_SYMBOLS,
        &mut arena.tree,
        &mut arena.lit_depth,
        &mut arena.lit_bits,
        storage_ix,
        storage,
    );
    build_and_store_huffman_tree(
        &arena.cmd_histo.data_,
        BROTLI_NUM_COMMAND_SYMBOLS,
        BROTLI_NUM_COMMAND_SYMBOLS,
        &mut arena.tree,
        &mut arena.cmd_depth,
        &mut arena.cmd_bits,
        storage_ix,
        storage,
    );
    build_and_store_huffman_tree(
        &arena.dist_histo.data_,
        MAX_SIMPLE_DISTANCE_ALPHABET_SIZE,
        num_distance_symbols,
        &mut arena.tree,
        &mut arena.dist_depth,
        &mut arena.dist_bits,
        storage_ix,
        storage,
    );
    store_data_with_huffman_codes(
        input,
        start_pos,
        mask,
        commands,
        &arena.lit_depth,
        &arena.lit_bits,
        &arena.cmd_depth,
        &arena.cmd_bits,
        &arena.dist_depth,
        &arena.dist_bits,
        storage_ix,
        storage,
    );
    if is_last {
        jump_to_byte_boundary(storage_ix, storage);
    }
}

pub fn brotli_store_meta_block_fast(
    input: &[u8],
    start_pos: usize,
    length: usize,
    mask: usize,
    is_last: bool,
    params: &BrotliEncoderParams,
    commands: &[Command],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let mut arena = MetablockArena::default();
    let num_distance_symbols = params.dist.alphabet_size_max as usize;
    let distance_alphabet_bits = (log2_floor_non_zero(num_distance_symbols - 1) + 1) as usize;

    store_compressed_meta_block_header(is_last, length, storage_ix, storage);
    brotli_write_bits(13, 0, storage_ix, storage);

    if commands.len() <= 128 {
        let mut histogram = [0u32; BROTLI_NUM_LITERAL_SYMBOLS];
        let mut pos = start_pos;
        let mut num_literals = 0usize;
        for cmd in commands {
            for _ in 0..cmd.insert_len_ {
                histogram[input[pos & mask] as usize] += 1;
                pos += 1;
            }
            num_literals += cmd.insert_len_ as usize;
            pos += cmd.copy_len() as usize;
        }
        brotli_build_and_store_huffman_tree_fast(
            &mut arena.tree,
            &histogram,
            num_literals,
            8,
            &mut arena.lit_depth,
            &mut arena.lit_bits,
            storage_ix,
            storage,
        );
        store_static_command_huffman_tree(storage_ix, storage);
        store_static_distance_huffman_tree(storage_ix, storage);
        store_data_with_huffman_codes(
            input,
            start_pos,
            mask,
            commands,
            &arena.lit_depth,
            &arena.lit_bits,
            &K_STATIC_COMMAND_CODE_DEPTH,
            &K_STATIC_COMMAND_CODE_BITS,
            &K_STATIC_DISTANCE_CODE_DEPTH,
            &K_STATIC_DISTANCE_CODE_BITS,
            storage_ix,
            storage,
        );
    } else {
        arena.lit_histo.clear();
        arena.cmd_histo.clear();
        arena.dist_histo.clear();
        build_histograms(
            input,
            start_pos,
            mask,
            commands,
            &mut arena.lit_histo,
            &mut arena.cmd_histo,
            &mut arena.dist_histo,
        );
        brotli_build_and_store_huffman_tree_fast(
            &mut arena.tree,
            &arena.lit_histo.data_,
            arena.lit_histo.total_count_,
            8,
            &mut arena.lit_depth,
            &mut arena.lit_bits,
            storage_ix,
            storage,
        );
        brotli_build_and_store_huffman_tree_fast(
            &mut arena.tree,
            &arena.cmd_histo.data_,
            arena.cmd_histo.total_count_,
            10,
            &mut arena.cmd_depth,
            &mut arena.cmd_bits,
            storage_ix,
            storage,
        );
        brotli_build_and_store_huffman_tree_fast(
            &mut arena.tree,
            &arena.dist_histo.data_,
            arena.dist_histo.total_count_,
            distance_alphabet_bits,
            &mut arena.dist_depth,
            &mut arena.dist_bits,
            storage_ix,
            storage,
        );
        store_data_with_huffman_codes(
            input,
            start_pos,
            mask,
            commands,
            &arena.lit_depth,
            &arena.lit_bits,
            &arena.cmd_depth,
            &arena.cmd_bits,
            &arena.dist_depth,
            &arena.dist_bits,
            storage_ix,
            storage,
        );
    }

    if is_last {
        jump_to_byte_boundary(storage_ix, storage);
    }
}

pub fn brotli_store_uncompressed_meta_block(
    is_final_block: bool,
    input: &[u8],
    position: usize,
    mask: usize,
    mut len: usize,
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let mut masked_pos = position & mask;
    brotli_store_uncompressed_meta_block_header(len, storage_ix, storage);
    jump_to_byte_boundary(storage_ix, storage);

    if masked_pos + len > mask + 1 {
        let len1 = mask + 1 - masked_pos;
        let out_pos = *storage_ix >> 3;
        storage[out_pos..out_pos + len1].copy_from_slice(&input[masked_pos..masked_pos + len1]);
        *storage_ix += len1 << 3;
        len -= len1;
        masked_pos = 0;
    }
    {
        let out_pos = *storage_ix >> 3;
        storage[out_pos..out_pos + len].copy_from_slice(&input[masked_pos..masked_pos + len]);
        *storage_ix += len << 3;
    }

    brotli_write_bits_prepare_storage(*storage_ix, storage);

    if is_final_block {
        brotli_write_bits(1, 1, storage_ix, storage);
        brotli_write_bits(1, 1, storage_ix, storage);
        jump_to_byte_boundary(storage_ix, storage);
    }
}
