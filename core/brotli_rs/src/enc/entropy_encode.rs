#![allow(dead_code)]

use crate::common::constants::*;

#[derive(Debug, Clone, Copy, Default)]
pub struct HuffmanTree {
    pub total_count_: u32,
    pub index_left_: i16,
    pub index_right_or_value_: i16,
}

impl HuffmanTree {
    #[inline(always)]
    pub fn init(&mut self, count: u32, left: i16, right: i16) {
        self.total_count_ = count;
        self.index_left_ = left;
        self.index_right_or_value_ = right;
    }
}

pub fn brotli_set_depth(p: i32, pool: &[HuffmanTree], depth: &mut [u8], max_depth: i32) -> bool {
    let mut stack = [-p; 16];
    let mut level = 0i32;
    let mut cur = p;
    loop {
        let node = &pool[cur as usize];
        if node.index_left_ >= 0 {
            level += 1;
            if level > max_depth {
                return false;
            }
            stack[level as usize] = node.index_right_or_value_ as i32;
            cur = node.index_left_ as i32;
            continue;
        } else {
            depth[node.index_right_or_value_ as usize] = level as u8;
        }
        while level >= 0 && stack[level as usize] < 0 {
            level -= 1;
        }
        if level < 0 {
            return true;
        }
        cur = stack[level as usize];
        stack[level as usize] = -stack[level as usize];
    }
}

#[inline(always)]
fn sort_huffman_tree(v0: &HuffmanTree, v1: &HuffmanTree) -> bool {
    if v0.total_count_ != v1.total_count_ {
        v0.total_count_ < v1.total_count_
    } else {
        v0.index_right_or_value_ > v1.index_right_or_value_
    }
}

static K_BROTLI_SHELL_GAPS: [usize; 6] = [132, 57, 23, 10, 4, 1];

pub fn sort_huffman_tree_items(items: &mut [HuffmanTree], n: usize) {
    if n < 13 {
        for i in 1..n {
            let mut j = i;
            let tmp = items[j];
            while j > 0 && sort_huffman_tree(&tmp, &items[j - 1]) {
                items[j] = items[j - 1];
                j -= 1;
            }
            items[j] = tmp;
        }
        return;
    }
    let start_gap = if n < 57 { 2 } else { 0 };
    for &gap in &K_BROTLI_SHELL_GAPS[start_gap..] {
        for i in gap..n {
            let mut j = i;
            let tmp = items[j];
            while j >= gap && sort_huffman_tree(&tmp, &items[j - gap]) {
                items[j] = items[j - gap];
                j -= gap;
            }
            items[j] = tmp;
        }
    }
}

pub fn brotli_create_huffman_tree(
    data: &[u32],
    length: usize,
    tree_limit: i32,
    tree: &mut [HuffmanTree],
    depth: &mut [u8],
) {
    let mut count_limit = 1u32;
    loop {
        let mut n = 0usize;
        let mut i = length;
        while i > 0 {
            i -= 1;
            if data[i] != 0 {
                let count = data[i].max(count_limit);
                tree[n].init(count, -1, i as i16);
                n += 1;
            }
        }

        if n == 1 {
            depth[tree[0].index_right_or_value_ as usize] = 1;
            break;
        }

        sort_huffman_tree_items(tree, n);

        let mut sentinel = HuffmanTree::default();
        sentinel.init(u32::MAX, -1, -1);
        tree[n] = sentinel;
        tree[n + 1] = sentinel;

        let mut i_idx = 0usize;
        let mut j_idx = n + 1;
        for k in (1..n).rev() {
            let left = if tree[i_idx].total_count_ <= tree[j_idx].total_count_ {
                let v = i_idx;
                i_idx += 1;
                v
            } else {
                let v = j_idx;
                j_idx += 1;
                v
            };
            let right = if tree[i_idx].total_count_ <= tree[j_idx].total_count_ {
                let v = i_idx;
                i_idx += 1;
                v
            } else {
                let v = j_idx;
                j_idx += 1;
                v
            };
            let j_end = 2 * n - k;
            tree[j_end].init(
                tree[left].total_count_.saturating_add(tree[right].total_count_),
                left as i16,
                right as i16,
            );
            tree[j_end + 1] = sentinel;
        }

        if brotli_set_depth((2 * n - 1) as i32, tree, depth, tree_limit) {
            break;
        }
        count_limit *= 2;
    }
}

fn reverse_slice<T: Copy>(a: &mut [T], mut start: usize, mut end: usize) {
    end -= 1;
    while start < end {
        a.swap(start, end);
        start += 1;
        end -= 1;
    }
}

fn brotli_write_huffman_tree_repetitions(
    previous_value: u8,
    value: u8,
    mut repetitions: usize,
    tree_size: &mut usize,
    tree: &mut [u8],
    extra_bits_data: &mut [u8],
) {
    debug_assert!(repetitions > 0);
    if previous_value != value {
        tree[*tree_size] = value;
        extra_bits_data[*tree_size] = 0;
        *tree_size += 1;
        repetitions -= 1;
    }
    if repetitions == 7 {
        tree[*tree_size] = value;
        extra_bits_data[*tree_size] = 0;
        *tree_size += 1;
        repetitions -= 1;
    }
    if repetitions < 3 {
        for _ in 0..repetitions {
            tree[*tree_size] = value;
            extra_bits_data[*tree_size] = 0;
            *tree_size += 1;
        }
    } else {
        let start = *tree_size;
        repetitions -= 3;
        loop {
            tree[*tree_size] = BROTLI_REPEAT_PREVIOUS_CODE_LENGTH as u8;
            extra_bits_data[*tree_size] = (repetitions & 0x3) as u8;
            *tree_size += 1;
            repetitions >>= 2;
            if repetitions == 0 {
                break;
            }
            repetitions -= 1;
        }
        reverse_slice(tree, start, *tree_size);
        reverse_slice(extra_bits_data, start, *tree_size);
    }
}

fn brotli_write_huffman_tree_repetitions_zeros(
    mut repetitions: usize,
    tree_size: &mut usize,
    tree: &mut [u8],
    extra_bits_data: &mut [u8],
) {
    if repetitions == 11 {
        tree[*tree_size] = 0;
        extra_bits_data[*tree_size] = 0;
        *tree_size += 1;
        repetitions -= 1;
    }
    if repetitions < 3 {
        for _ in 0..repetitions {
            tree[*tree_size] = 0;
            extra_bits_data[*tree_size] = 0;
            *tree_size += 1;
        }
    } else {
        let start = *tree_size;
        repetitions -= 3;
        loop {
            tree[*tree_size] = BROTLI_REPEAT_ZERO_CODE_LENGTH as u8;
            extra_bits_data[*tree_size] = (repetitions & 0x7) as u8;
            *tree_size += 1;
            repetitions >>= 3;
            if repetitions == 0 {
                break;
            }
            repetitions -= 1;
        }
        reverse_slice(tree, start, *tree_size);
        reverse_slice(extra_bits_data, start, *tree_size);
    }
}

pub fn brotli_optimize_huffman_counts_for_rle(mut length: usize, counts: &mut [u32]) {
    let mut nonzero_count = 0usize;
    for &c in &counts[..length] {
        if c != 0 {
            nonzero_count += 1;
        }
    }
    if nonzero_count < 16 {
        return;
    }
    while length != 0 && counts[length - 1] == 0 {
        length -= 1;
    }
    if length == 0 {
        return;
    }
    {
        let mut nonzeros = 0usize;
        let mut smallest_nonzero = 1u32 << 30;
        for &c in &counts[..length] {
            if c != 0 {
                nonzeros += 1;
                if smallest_nonzero > c {
                    smallest_nonzero = c;
                }
            }
        }
        if nonzeros < 5 {
            return;
        }
        if smallest_nonzero < 4 {
            let zeros = length - nonzeros;
            if zeros < 6 {
                for i in 1..length - 1 {
                    if counts[i - 1] != 0 && counts[i] == 0 && counts[i + 1] != 0 {
                        counts[i] = 1;
                    }
                }
            }
        }
        if nonzeros < 28 {
            return;
        }
    }
    let mut good_for_rle = vec![0u8; length];
    {
        let mut symbol = counts[0];
        let mut step = 0usize;
        for i in 0..=length {
            if i == length || counts[i] != symbol {
                if (symbol == 0 && step >= 5) || (symbol != 0 && step >= 7) {
                    for k in 0..step {
                        good_for_rle[i - k - 1] = 1;
                    }
                }
                step = 1;
                if i != length {
                    symbol = counts[i];
                }
            } else {
                step += 1;
            }
        }
    }
    let streak_limit = 1240usize;
    let mut stride = 0usize;
    let mut limit = (256 * ((counts[0] + counts[1] + counts[2]) as usize)) / 3 + 420;
    let mut sum = 0usize;
    for i in 0..=length {
        if i == length
            || good_for_rle[i] != 0
            || (i != 0 && good_for_rle[i - 1] != 0)
            || (256 * (counts[i] as usize))
                .wrapping_sub(limit)
                .wrapping_add(streak_limit)
                >= 2 * streak_limit
        {
            if stride >= 4 || (stride >= 3 && sum == 0) {
                let mut count = (sum + stride / 2) / stride;
                if count == 0 {
                    count = 1;
                }
                if sum == 0 {
                    count = 0;
                }
                for k in 0..stride {
                    counts[i - k - 1] = count as u32;
                }
            }
            stride = 0;
            sum = 0;
            if i + 2 < length {
                limit = (256 * ((counts[i] + counts[i + 1] + counts[i + 2]) as usize)) / 3 + 420;
            } else if i < length {
                limit = 256 * (counts[i] as usize);
            } else {
                limit = 0;
            }
        }
        stride += 1;
        if i != length {
            sum += counts[i] as usize;
            if stride >= 4 {
                limit = (256 * sum + stride / 2) / stride;
            }
            if stride == 4 {
                limit += 120;
            }
        }
    }
}

fn decide_over_rle_use(depth: &[u8], length: usize) -> (bool, bool) {
    let mut total_reps_zero = 0usize;
    let mut total_reps_non_zero = 0usize;
    let mut count_reps_zero = 1usize;
    let mut count_reps_non_zero = 1usize;
    let mut i = 0usize;
    while i < length {
        let value = depth[i];
        let mut reps = 1usize;
        let mut k = i + 1;
        while k < length && depth[k] == value {
            reps += 1;
            k += 1;
        }
        if reps >= 3 && value == 0 {
            total_reps_zero += reps;
            count_reps_zero += 1;
        }
        if reps >= 4 && value != 0 {
            total_reps_non_zero += reps;
            count_reps_non_zero += 1;
        }
        i += reps;
    }
    (
        total_reps_non_zero > count_reps_non_zero * 2,
        total_reps_zero > count_reps_zero * 2,
    )
}

pub fn brotli_write_huffman_tree(
    depth: &[u8],
    length: usize,
    tree_size: &mut usize,
    tree: &mut [u8],
    extra_bits_data: &mut [u8],
) {
    let mut previous_value = BROTLI_INITIAL_REPEATED_CODE_LENGTH as u8;
    let mut use_rle_for_non_zero = false;
    let mut use_rle_for_zero = false;

    let mut new_length = length;
    for i in 0..length {
        if depth[length - i - 1] == 0 {
            new_length -= 1;
        } else {
            break;
        }
    }

    if length > 50 {
        let (nz, z) = decide_over_rle_use(depth, new_length);
        use_rle_for_non_zero = nz;
        use_rle_for_zero = z;
    }

    let mut i = 0usize;
    while i < new_length {
        let value = depth[i];
        let mut reps = 1usize;
        if (value != 0 && use_rle_for_non_zero) || (value == 0 && use_rle_for_zero) {
            let mut k = i + 1;
            while k < new_length && depth[k] == value {
                reps += 1;
                k += 1;
            }
        }
        if value == 0 {
            brotli_write_huffman_tree_repetitions_zeros(reps, tree_size, tree, extra_bits_data);
        } else {
            brotli_write_huffman_tree_repetitions(
                previous_value,
                value,
                reps,
                tree_size,
                tree,
                extra_bits_data,
            );
            previous_value = value;
        }
        i += reps;
    }
}

#[inline(always)]
fn brotli_reverse_bits(num_bits: usize, mut bits: u16) -> u16 {
    const K_LUT: [usize; 16] = [
        0x00, 0x08, 0x04, 0x0C, 0x02, 0x0A, 0x06, 0x0E,
        0x01, 0x09, 0x05, 0x0D, 0x03, 0x0B, 0x07, 0x0F,
    ];
    let mut retval = K_LUT[(bits & 0x0F) as usize];
    let mut i = 4usize;
    while i < num_bits {
        retval <<= 4;
        bits >>= 4;
        retval |= K_LUT[(bits & 0x0F) as usize];
        i += 4;
    }
    retval >>= 0usize.wrapping_sub(num_bits) & 0x03;
    retval as u16
}

const MAX_HUFFMAN_BITS: usize = 16;

pub fn brotli_convert_bit_depths_to_symbols(depth: &[u8], len: usize, bits: &mut [u16]) {
    let mut bl_count = [0u16; MAX_HUFFMAN_BITS];
    let mut next_code = [0u16; MAX_HUFFMAN_BITS];
    for &d in &depth[..len] {
        bl_count[d as usize] += 1;
    }
    bl_count[0] = 0;
    next_code[0] = 0;
    let mut code = 0i32;
    for i in 1..MAX_HUFFMAN_BITS {
        code = (code + bl_count[i - 1] as i32) << 1;
        next_code[i] = code as u16;
    }
    for i in 0..len {
        let d = depth[i] as usize;
        if d != 0 {
            bits[i] = brotli_reverse_bits(d, next_code[d]);
            next_code[d] += 1;
        }
    }
}
