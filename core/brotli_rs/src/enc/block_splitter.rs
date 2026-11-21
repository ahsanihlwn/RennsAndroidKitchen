#![allow(dead_code)]

use crate::common::constants::BROTLI_MAX_NUMBER_OF_BLOCK_TYPES;
use crate::enc::bit_cost::brotli_population_cost;
use crate::enc::cluster::{
    HistogramPair, brotli_histogram_bit_cost_distance, brotli_histogram_combine,
};
use crate::enc::command::Command;
use crate::enc::fast_log::fast_log2;
use crate::enc::histogram::{
    HistogramCommand, HistogramDistance, HistogramLiteral, HistogramTrait, clear_histograms,
};
use crate::enc::params::BrotliEncoderParams;
use crate::enc::quality::HQ_ZOPFLIFICATION_QUALITY;

const K_MAX_LITERAL_HISTOGRAMS: usize = 100;
const K_MAX_COMMAND_HISTOGRAMS: usize = 50;
const K_LITERAL_BLOCK_SWITCH_COST: f64 = 28.1;
const K_COMMAND_BLOCK_SWITCH_COST: f64 = 13.5;
const K_DISTANCE_BLOCK_SWITCH_COST: f64 = 14.6;
const K_LITERAL_STRIDE_LENGTH: usize = 70;
const K_COMMAND_STRIDE_LENGTH: usize = 40;
const K_DISTANCE_STRIDE_LENGTH: usize = 40;
const K_SYMBOLS_PER_LITERAL_HISTOGRAM: usize = 544;
const K_SYMBOLS_PER_COMMAND_HISTOGRAM: usize = 530;
const K_SYMBOLS_PER_DISTANCE_HISTOGRAM: usize = 544;
const K_MIN_LENGTH_FOR_BLOCK_SPLITTING: usize = 128;
const K_ITER_MUL_FOR_REFINING: usize = 2;
const K_MIN_ITERS_FOR_REFINING: usize = 100;

const HISTOGRAMS_PER_BATCH: usize = 64;
const CLUSTERS_PER_BATCH: usize = 16;

#[derive(Debug, Clone, Default)]
pub struct BlockSplit {
    pub num_types: usize,
    pub num_blocks: usize,
    pub types: Vec<u8>,
    pub lengths: Vec<u32>,
}

impl BlockSplit {
    pub fn new() -> Self {
        Self::default()
    }
}

pub fn count_literals(cmds: &[Command]) -> usize {
    cmds.iter().map(|c| c.insert_len_ as usize).sum()
}

pub fn copy_literals_to_byte_array(
    cmds: &[Command],
    data: &[u8],
    offset: usize,
    mask: usize,
    literals: &mut [u8],
) {
    let mut pos = 0usize;
    let mut from_pos = offset & mask;
    for cmd in cmds {
        let mut insert_len = cmd.insert_len_ as usize;
        if from_pos + insert_len > mask {
            let head_size = mask + 1 - from_pos;
            literals[pos..pos + head_size].copy_from_slice(&data[from_pos..from_pos + head_size]);
            from_pos = 0;
            pos += head_size;
            insert_len -= head_size;
        }
        if insert_len > 0 {
            literals[pos..pos + insert_len].copy_from_slice(&data[from_pos..from_pos + insert_len]);
            pos += insert_len;
        }
        from_pos = (from_pos + insert_len + (cmd.copy_len() as usize)) & mask;
    }
}

#[inline(always)]
fn my_rand(seed: &mut u32) -> u32 {
    *seed = seed.wrapping_mul(16807);
    *seed
}

#[inline(always)]
fn bit_cost(count: u32) -> f64 {
    if count == 0 {
        -2.0
    } else {
        fast_log2(count as usize)
    }
}

pub trait SymbolData: Copy {
    fn as_index(self) -> usize;
}

impl SymbolData for u8 {
    #[inline(always)]
    fn as_index(self) -> usize {
        self as usize
    }
}

impl SymbolData for u16 {
    #[inline(always)]
    fn as_index(self) -> usize {
        self as usize
    }
}

fn histogram_add_symbol_slice<H: HistogramTrait, T: SymbolData>(h: &mut H, data: &[T]) {
    for &x in data {
        h.add(x.as_index());
    }
}

fn initial_entropy_codes<H: HistogramTrait, T: SymbolData>(
    data: &[T],
    stride: usize,
    num_histograms: usize,
    histograms: &mut [H],
) {
    let length = data.len();
    let mut seed = 7u32;
    let block_length = length / num_histograms;
    clear_histograms(histograms);
    for i in 0..num_histograms {
        let mut pos = length * i / num_histograms;
        if i != 0 {
            pos += (my_rand(&mut seed) as usize) % block_length;
        }
        if pos + stride >= length {
            pos = length - stride - 1;
        }
        histogram_add_symbol_slice(&mut histograms[i], &data[pos..pos + stride]);
    }
}

fn random_sample<H: HistogramTrait, T: SymbolData>(
    seed: &mut u32,
    data: &[T],
    mut stride: usize,
    sample: &mut H,
) {
    let length = data.len();
    let pos = if stride >= length {
        stride = length;
        0
    } else {
        (my_rand(seed) as usize) % (length - stride + 1)
    };
    histogram_add_symbol_slice(sample, &data[pos..pos + stride]);
}

fn refine_entropy_codes<H: HistogramTrait, T: SymbolData>(
    data: &[T],
    stride: usize,
    num_histograms: usize,
    histograms: &mut [H],
    tmp: &mut H,
) {
    let length = data.len();
    let mut iters = K_ITER_MUL_FOR_REFINING * length / stride + K_MIN_ITERS_FOR_REFINING;
    let mut seed = 7u32;
    iters = ((iters + num_histograms - 1) / num_histograms) * num_histograms;
    for iter in 0..iters {
        tmp.clear();
        random_sample(&mut seed, data, stride, tmp);
        histograms[iter % num_histograms].add_histogram(tmp);
    }
}

fn find_blocks<H: HistogramTrait, T: SymbolData>(
    data: &[T],
    alphabet_size: usize,
    block_switch_bitcost: f64,
    num_histograms: usize,
    histograms: &[H],
    insert_cost: &mut [f64],
    cost: &mut [f64],
    switch_signal: &mut [u8],
    block_id: &mut [u8],
) -> usize {
    let length = data.len();
    let bitmap_len = (num_histograms + 7) >> 3;
    let mut num_blocks = 1usize;
    debug_assert!(num_histograms <= 256);

    if num_histograms <= 1 {
        block_id[..length].fill(0);
        return 1;
    }

    insert_cost[..alphabet_size * num_histograms].fill(0.0);
    for i in 0..num_histograms {
        insert_cost[i] = fast_log2(histograms[i].total_count());
    }
    let mut i = alphabet_size;
    while i != 0 {
        i -= 1;
        for j in 0..num_histograms {
            insert_cost[i * num_histograms + j] =
                insert_cost[j] - bit_cost(histograms[j].data()[i]);
        }
    }

    cost[..num_histograms].fill(0.0);
    switch_signal[..length * bitmap_len].fill(0);
    const PROLOGUE_LENGTH: usize = 2000;
    const MULTIPLIER: f64 = 0.07 / 2000.0;
    for byte_ix in 0..length {
        let ix = byte_ix * bitmap_len;
        let symbol = data[byte_ix].as_index();
        let insert_cost_ix = symbol * num_histograms;
        let mut min_cost = 1e99f64;
        let mut block_switch_cost = block_switch_bitcost;
        for k in 0..num_histograms {
            cost[k] += insert_cost[insert_cost_ix + k];
            if cost[k] < min_cost {
                min_cost = cost[k];
                block_id[byte_ix] = k as u8;
            }
        }
        if byte_ix < PROLOGUE_LENGTH {
            block_switch_cost *= 0.77 + MULTIPLIER * (byte_ix as f64);
        }
        for k in 0..num_histograms {
            cost[k] -= min_cost;
            if cost[k] >= block_switch_cost {
                let mask = 1u8 << (k & 7);
                cost[k] = block_switch_cost;
                switch_signal[ix + (k >> 3)] |= mask;
            }
        }
    }

    let mut byte_ix = length - 1;
    let mut ix = byte_ix * bitmap_len;
    let mut cur_id = block_id[byte_ix];
    while byte_ix > 0 {
        let mask = 1u8 << (cur_id & 7);
        byte_ix -= 1;
        ix -= bitmap_len;
        if (switch_signal[ix + ((cur_id as usize) >> 3)] & mask) != 0 && cur_id != block_id[byte_ix]
        {
            cur_id = block_id[byte_ix];
            num_blocks += 1;
        }
        block_id[byte_ix] = cur_id;
    }
    num_blocks
}

fn remap_block_ids(
    block_ids: &mut [u8],
    new_id: &mut [u16],
    num_histograms: usize,
) -> usize {
    const K_INVALID_ID: u16 = 256;
    let mut next_id = 0u16;
    new_id[..num_histograms].fill(K_INVALID_ID);
    for &b in block_ids.iter() {
        if new_id[b as usize] == K_INVALID_ID {
            new_id[b as usize] = next_id;
            next_id += 1;
        }
    }
    for b in block_ids.iter_mut() {
        *b = new_id[*b as usize] as u8;
    }
    next_id as usize
}

fn build_block_histograms<H: HistogramTrait, T: SymbolData>(
    data: &[T],
    block_ids: &[u8],
    num_histograms: usize,
    histograms: &mut [H],
) {
    clear_histograms(&mut histograms[..num_histograms]);
    for i in 0..data.len() {
        histograms[block_ids[i] as usize].add(data[i].as_index());
    }
}

fn cluster_blocks<H: HistogramTrait, T: SymbolData>(
    data: &[T],
    num_blocks: usize,
    block_ids: &[u8],
    split: &mut BlockSplit,
) {
    let length = data.len();
    let mut histogram_symbols = vec![0u32; num_blocks];
    let mut block_lengths = vec![0u32; num_blocks];
    let mut all_histograms: Vec<H> = Vec::with_capacity(
        CLUSTERS_PER_BATCH * (num_blocks + HISTOGRAMS_PER_BATCH - 1) / HISTOGRAMS_PER_BATCH,
    );
    let mut cluster_size: Vec<u32> = Vec::with_capacity(all_histograms.capacity());
    let mut num_clusters = 0usize;
    let mut histograms = vec![H::default(); num_blocks.min(HISTOGRAMS_PER_BATCH)];
    let mut max_num_pairs = HISTOGRAMS_PER_BATCH * HISTOGRAMS_PER_BATCH / 2;
    let mut pairs = vec![HistogramPair::default(); max_num_pairs + 1];
    let mut pos = 0usize;
    let mut sizes = [0u32; HISTOGRAMS_PER_BATCH];
    let mut new_clusters = [0u32; HISTOGRAMS_PER_BATCH];
    let mut symbols = [0u32; HISTOGRAMS_PER_BATCH];
    let mut remap = [0u32; HISTOGRAMS_PER_BATCH];
    let mut tmp = [H::default(), H::default()];

    {
        let mut block_idx = 0usize;
        for i in 0..length {
            block_lengths[block_idx] += 1;
            if i + 1 == length || block_ids[i] != block_ids[i + 1] {
                block_idx += 1;
            }
        }
        debug_assert_eq!(block_idx, num_blocks);
    }

    let mut i = 0usize;
    while i < num_blocks {
        let num_to_combine = (num_blocks - i).min(HISTOGRAMS_PER_BATCH);
        for j in 0..num_to_combine {
            let block_length = block_lengths[i + j] as usize;
            histograms[j].clear();
            for _ in 0..block_length {
                histograms[j].add(data[pos].as_index());
                pos += 1;
            }
            let cost = brotli_population_cost(&histograms[j]);
            histograms[j].set_bit_cost(cost);
            new_clusters[j] = j as u32;
            symbols[j] = j as u32;
            sizes[j] = 1;
        }
        let num_new_clusters = brotli_histogram_combine(
            &mut histograms,
            &mut tmp[0],
            &mut sizes,
            &mut symbols,
            &mut new_clusters,
            &mut pairs,
            num_to_combine,
            num_to_combine,
            HISTOGRAMS_PER_BATCH,
            max_num_pairs,
        );
        for j in 0..num_new_clusters {
            all_histograms.push(histograms[new_clusters[j] as usize].clone());
            cluster_size.push(sizes[new_clusters[j] as usize]);
            remap[new_clusters[j] as usize] = j as u32;
        }
        for j in 0..num_to_combine {
            histogram_symbols[i + j] = (num_clusters as u32) + remap[symbols[j] as usize];
        }
        num_clusters += num_new_clusters;
        i += HISTOGRAMS_PER_BATCH;
    }

    max_num_pairs = (64 * num_clusters).min((num_clusters / 2) * num_clusters);
    if pairs.len() < max_num_pairs + 1 {
        pairs.resize(max_num_pairs + 1, HistogramPair::default());
    }
    let mut clusters: Vec<u32> = (0..num_clusters as u32).collect();
    let num_final_clusters = brotli_histogram_combine(
        &mut all_histograms,
        &mut tmp[0],
        &mut cluster_size,
        &mut histogram_symbols,
        &mut clusters,
        &mut pairs,
        num_clusters,
        num_blocks,
        BROTLI_MAX_NUMBER_OF_BLOCK_TYPES,
        max_num_pairs,
    );

    const K_INVALID_INDEX: u32 = u32::MAX;
    let mut new_index = vec![K_INVALID_INDEX; num_clusters];
    pos = 0;
    let mut next_index = 0u32;
    let [ref mut tmp0, ref mut tmp1] = tmp;
    for i in 0..num_blocks {
        tmp0.clear();
        for _ in 0..block_lengths[i] {
            tmp0.add(data[pos].as_index());
            pos += 1;
        }
        let mut best_out = if i == 0 {
            histogram_symbols[0]
        } else {
            histogram_symbols[i - 1]
        };
        let mut best_bits =
            brotli_histogram_bit_cost_distance(tmp0, &all_histograms[best_out as usize], tmp1);
        for j in 0..num_final_clusters {
            let cur_bits = brotli_histogram_bit_cost_distance(
                tmp0,
                &all_histograms[clusters[j] as usize],
                tmp1,
            );
            if cur_bits < best_bits {
                best_bits = cur_bits;
                best_out = clusters[j];
            }
        }
        histogram_symbols[i] = best_out;
        if new_index[best_out as usize] == K_INVALID_INDEX {
            new_index[best_out as usize] = next_index;
            next_index += 1;
        }
    }

    split.types.resize(num_blocks, 0);
    split.lengths.resize(num_blocks, 0);
    let mut cur_length = 0u32;
    let mut block_idx = 0usize;
    let mut max_type = 0u8;
    for i in 0..num_blocks {
        cur_length += block_lengths[i];
        if i + 1 == num_blocks || histogram_symbols[i] != histogram_symbols[i + 1] {
            let id = new_index[histogram_symbols[i] as usize] as u8;
            split.types[block_idx] = id;
            split.lengths[block_idx] = cur_length;
            max_type = max_type.max(id);
            cur_length = 0;
            block_idx += 1;
        }
    }
    split.num_blocks = block_idx;
    split.num_types = (max_type as usize) + 1;
    split.types.truncate(block_idx);
    split.lengths.truncate(block_idx);
}

fn split_byte_vector<H: HistogramTrait, T: SymbolData>(
    data: &[T],
    alphabet_size: usize,
    symbols_per_histogram: usize,
    max_histograms: usize,
    sampling_stride_length: usize,
    block_switch_cost: f64,
    params: &BrotliEncoderParams,
    split: &mut BlockSplit,
) {
    let length = data.len();
    let mut num_histograms = (length / symbols_per_histogram + 1).min(max_histograms);

    if length == 0 {
        split.num_types = 1;
        return;
    }

    if length < K_MIN_LENGTH_FOR_BLOCK_SPLITTING {
        split.num_types = 1;
        split.types.push(0);
        split.lengths.push(length as u32);
        split.num_blocks += 1;
        return;
    }

    let mut histograms = vec![H::default(); num_histograms];
    let mut tmp = H::default();
    initial_entropy_codes(data, sampling_stride_length, num_histograms, &mut histograms);
    refine_entropy_codes(
        data,
        sampling_stride_length,
        num_histograms,
        &mut histograms,
        &mut tmp,
    );

    let mut block_ids = vec![0u8; length];
    let mut num_blocks = 0usize;
    let bitmaplen = (num_histograms + 7) >> 3;
    let mut insert_cost = vec![0.0f64; alphabet_size * num_histograms];
    let mut cost = vec![0.0f64; num_histograms];
    let mut switch_signal = vec![0u8; length * bitmaplen];
    let mut new_id = vec![0u16; num_histograms];
    let iters = if params.quality < HQ_ZOPFLIFICATION_QUALITY {
        3
    } else {
        10
    };
    for _ in 0..iters {
        num_blocks = find_blocks(
            data,
            alphabet_size,
            block_switch_cost,
            num_histograms,
            &histograms,
            &mut insert_cost,
            &mut cost,
            &mut switch_signal,
            &mut block_ids,
        );
        num_histograms = remap_block_ids(&mut block_ids, &mut new_id, num_histograms);
        build_block_histograms(data, &block_ids, num_histograms, &mut histograms);
    }
    cluster_blocks::<H, T>(data, num_blocks, &block_ids, split);
}

pub fn brotli_split_block(
    cmds: &[Command],
    data: &[u8],
    pos: usize,
    mask: usize,
    params: &BrotliEncoderParams,
    literal_split: &mut BlockSplit,
    insert_and_copy_split: &mut BlockSplit,
    dist_split: &mut BlockSplit,
) {
    {
        let literals_count = count_literals(cmds);
        let mut literals = vec![0u8; literals_count];
        copy_literals_to_byte_array(cmds, data, pos, mask, &mut literals);
        split_byte_vector::<HistogramLiteral, u8>(
            &literals,
            crate::common::constants::BROTLI_NUM_LITERAL_SYMBOLS,
            K_SYMBOLS_PER_LITERAL_HISTOGRAM,
            K_MAX_LITERAL_HISTOGRAMS,
            K_LITERAL_STRIDE_LENGTH,
            K_LITERAL_BLOCK_SWITCH_COST,
            params,
            literal_split,
        );
    }

    {
        let insert_and_copy_codes: Vec<u16> = cmds.iter().map(|c| c.cmd_prefix_).collect();
        split_byte_vector::<HistogramCommand, u16>(
            &insert_and_copy_codes,
            crate::common::constants::BROTLI_NUM_COMMAND_SYMBOLS,
            K_SYMBOLS_PER_COMMAND_HISTOGRAM,
            K_MAX_COMMAND_HISTOGRAMS,
            K_COMMAND_STRIDE_LENGTH,
            K_COMMAND_BLOCK_SWITCH_COST,
            params,
            insert_and_copy_split,
        );
    }

    {
        let mut distance_prefixes = Vec::with_capacity(cmds.len());
        for cmd in cmds {
            if cmd.copy_len() != 0 && cmd.cmd_prefix_ >= 128 {
                distance_prefixes.push(cmd.dist_prefix_ & 0x3FF);
            }
        }
        split_byte_vector::<HistogramDistance, u16>(
            &distance_prefixes,
            crate::enc::histogram::BROTLI_NUM_HISTOGRAM_DISTANCE_SYMBOLS,
            K_SYMBOLS_PER_DISTANCE_HISTOGRAM,
            K_MAX_COMMAND_HISTOGRAMS,
            K_DISTANCE_STRIDE_LENGTH,
            K_DISTANCE_BLOCK_SWITCH_COST,
            params,
            dist_split,
        );
    }
}
