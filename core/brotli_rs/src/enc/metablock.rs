#![allow(dead_code)]

use crate::common::constants::*;
use crate::common::context::{ContextLut, ContextType, brotli_context};
use crate::enc::bit_cost::{brotli_bits_entropy, brotli_population_cost};
use crate::enc::block_splitter::{BlockSplit, brotli_split_block};
use crate::enc::cluster::brotli_cluster_histograms;
use crate::enc::command::{Command, prefix_encode_copy_distance};
use crate::enc::entropy_encode::brotli_optimize_huffman_counts_for_rle;
use crate::enc::histogram::{
    HistogramCommand, HistogramDistance, HistogramLiteral, HistogramTrait,
    brotli_build_histograms_with_context, clear_histograms,
};
use crate::enc::params::{BrotliDistanceParams, BrotliEncoderParams};

pub const BROTLI_MAX_STATIC_CONTEXTS: usize = 13;

#[derive(Debug, Clone, Copy, Default)]
pub struct Base64Region {
    pub start_literal_pos: usize,
    pub length: usize,
}

#[derive(Debug, Clone, Default)]
pub struct MetaBlockSplit {
    pub literal_split: BlockSplit,
    pub command_split: BlockSplit,
    pub distance_split: BlockSplit,
    pub literal_context_map: Option<Vec<u32>>,
    pub distance_context_map: Option<Vec<u32>>,
    pub literal_histograms: Vec<HistogramLiteral>,
    pub command_histograms: Vec<HistogramCommand>,
    pub distance_histograms: Vec<HistogramDistance>,
    pub literal_is_base64: [u8; 32],
}

impl MetaBlockSplit {
    pub fn new() -> Self {
        Self::default()
    }
}

fn force_base64_literal_splits(
    split: &mut BlockSplit,
    base64_regions: &[Base64Region],
    last_flush_pos: usize,
    literal_is_base64: &mut [u8; 32],
) {
    let new_alloc_size = split.num_blocks + base64_regions.len() * 2 + 1;
    let mut new_types = Vec::with_capacity(new_alloc_size);
    let mut new_lengths = Vec::with_capacity(new_alloc_size);
    let mut curr_offset = 0usize;

    *literal_is_base64 = [0u8; 32];
    let base64_type_id = split.num_types as u8;
    literal_is_base64[(base64_type_id >> 3) as usize] |= 1u8 << (base64_type_id & 7);

    for block_idx in 0..split.num_blocks {
        let mut block_len = split.lengths[block_idx];
        let block_type = split.types[block_idx];
        let mut block_start = curr_offset;
        let block_end = curr_offset + (block_len as usize);

        let mut intersected = false;
        for reg in base64_regions {
            let b64_start = reg.start_literal_pos - last_flush_pos;
            let b64_end = b64_start + reg.length;

            if b64_start < block_end && b64_end > block_start {
                if b64_start > block_start {
                    new_types.push(block_type);
                    new_lengths.push((b64_start - block_start) as u32);
                }
                let intersect_start = block_start.max(b64_start);
                let intersect_end = block_end.min(b64_end);
                new_types.push(base64_type_id);
                new_lengths.push((intersect_end - intersect_start) as u32);

                if b64_end < block_end {
                    block_start = b64_end;
                    block_len = (block_end - b64_end) as u32;
                    continue;
                } else {
                    block_len = 0;
                    intersected = true;
                    break;
                }
            }
        }

        if !intersected && block_len > 0 {
            new_types.push(block_type);
            new_lengths.push(block_len);
        }
        curr_offset = block_end;
    }

    split.num_blocks = new_types.len();
    split.types = new_types;
    split.lengths = new_lengths;
    split.num_types += 1;
}

fn append_base64_histogram(mb: &mut MetaBlockSplit) {
    let base64_type_id = mb.literal_split.num_types - 1;
    let b64_histo_id = mb.literal_histograms.len();
    let new_context_map_size = mb.literal_split.num_types << BROTLI_LITERAL_CONTEXT_BITS;
    mb.literal_histograms.push(HistogramLiteral::default());
    mb.literal_histograms[b64_histo_id].clear();

    let mut new_context_map = vec![0u32; new_context_map_size];
    if let Some(ref old_map) = mb.literal_context_map {
        new_context_map[..old_map.len()].copy_from_slice(old_map);
    } else {
        for t in 0..base64_type_id {
            for i in 0..64 {
                new_context_map[(t << 6) + i] = t as u32;
            }
        }
    }
    for i in 0..64 {
        new_context_map[(base64_type_id << 6) + i] = b64_histo_id as u32;
    }
    mb.literal_context_map = Some(new_context_map);
}

fn map_base64_regions_to_literal_positions(
    commands: &[Command],
    last_flush_pos: usize,
    in_regions: &[Base64Region],
) -> Vec<Base64Region> {
    let mut out_regions = Vec::with_capacity(in_regions.len());
    for reg in in_regions {
        let target_ring_pos = reg.start_literal_pos;
        let mut curr_ring_pos = last_flush_pos;
        let mut curr_lit_pos = 0usize;
        for cmd in commands {
            let ins = cmd.insert_len_ as usize;
            if curr_ring_pos + ins >= target_ring_pos {
                let offset = target_ring_pos.saturating_sub(curr_ring_pos);
                curr_lit_pos += offset;
                break;
            }
            curr_lit_pos += ins;
            curr_ring_pos += ins + (cmd.copy_len() as usize);
        }
        out_regions.push(Base64Region {
            start_literal_pos: curr_lit_pos,
            length: reg.length,
        });
    }
    out_regions
}

pub fn brotli_init_distance_params(
    dist_params: &mut BrotliDistanceParams,
    npostfix: u32,
    ndirect: u32,
    large_window: bool,
) {
    dist_params.distance_postfix_bits = npostfix;
    dist_params.num_direct_distance_codes = ndirect;

    let mut alphabet_size_max =
        brotli_distance_alphabet_size(npostfix, ndirect, BROTLI_MAX_DISTANCE_BITS) as u32;
    let mut alphabet_size_limit = alphabet_size_max;
    let mut max_distance =
        ndirect + (1u32 << (BROTLI_MAX_DISTANCE_BITS + npostfix + 2)) - (1u32 << (npostfix + 2));

    if large_window {
        let limit =
            brotli_calculate_distance_code_limit(BROTLI_MAX_ALLOWED_DISTANCE as u32, npostfix, ndirect);
        alphabet_size_max =
            brotli_distance_alphabet_size(npostfix, ndirect, BROTLI_LARGE_MAX_DISTANCE_BITS) as u32;
        alphabet_size_limit = limit.max_alphabet_size;
        max_distance = limit.max_distance;
    }

    dist_params.alphabet_size_max = alphabet_size_max;
    dist_params.alphabet_size_limit = alphabet_size_limit;
    dist_params.max_distance = max_distance as usize;
}

fn recompute_distance_prefixes(
    cmds: &mut [Command],
    orig_params: &BrotliDistanceParams,
    new_params: &BrotliDistanceParams,
) {
    if orig_params.distance_postfix_bits == new_params.distance_postfix_bits
        && orig_params.num_direct_distance_codes == new_params.num_direct_distance_codes
    {
        return;
    }
    for cmd in cmds {
        if cmd.copy_len() != 0 && cmd.cmd_prefix_ >= 128 {
            let (dp, de) = prefix_encode_copy_distance(
                cmd.restore_distance_code(orig_params) as usize,
                new_params.num_direct_distance_codes as usize,
                new_params.distance_postfix_bits as usize,
            );
            cmd.dist_prefix_ = dp;
            cmd.dist_extra_ = de;
        }
    }
}

fn compute_distance_cost(
    cmds: &[Command],
    orig_params: &BrotliDistanceParams,
    new_params: &BrotliDistanceParams,
    tmp: &mut HistogramDistance,
) -> Option<f64> {
    let mut extra_bits = 0.0f64;
    tmp.clear();
    let equal_params = orig_params.distance_postfix_bits == new_params.distance_postfix_bits
        && orig_params.num_direct_distance_codes == new_params.num_direct_distance_codes;

    for cmd in cmds {
        if cmd.copy_len() != 0 && cmd.cmd_prefix_ >= 128 {
            let dist_prefix = if equal_params {
                cmd.dist_prefix_
            } else {
                let distance = cmd.restore_distance_code(orig_params) as usize;
                if distance > new_params.max_distance {
                    return None;
                }
                let (dp, _) = prefix_encode_copy_distance(
                    distance,
                    new_params.num_direct_distance_codes as usize,
                    new_params.distance_postfix_bits as usize,
                );
                dp
            };
            tmp.add((dist_prefix & 0x3FF) as usize);
            extra_bits += (dist_prefix >> 10) as f64;
        }
    }

    Some(brotli_population_cost(tmp) + extra_bits)
}

pub fn brotli_build_meta_block(
    ringbuffer: &[u8],
    pos: usize,
    mask: usize,
    base64_regions: &[Base64Region],
    params: &mut BrotliEncoderParams,
    prev_byte: u8,
    prev_byte2: u8,
    cmds: &mut [Command],
    literal_context_mode: ContextType,
    mb: &mut MetaBlockSplit,
) {
    const K_MAX_NUMBER_OF_HISTOGRAMS: usize = 256;
    let mut check_orig = true;
    let mut best_dist_cost = 1e99f64;
    let orig_params = params.dist;
    let mut new_params = params.dist;
    let mut tmp = HistogramDistance::default();
    let mut ndirect_msb = 0u32;

    for npostfix in 0..=BROTLI_MAX_NPOSTFIX as u32 {
        while ndirect_msb < 16 {
            let ndirect = ndirect_msb << npostfix;
            brotli_init_distance_params(&mut new_params, npostfix, ndirect, params.large_window);
            if npostfix == orig_params.distance_postfix_bits
                && ndirect == orig_params.num_direct_distance_codes
            {
                check_orig = false;
            }
            match compute_distance_cost(cmds, &orig_params, &new_params, &mut tmp) {
                Some(dist_cost) if dist_cost <= best_dist_cost => {
                    best_dist_cost = dist_cost;
                    params.dist = new_params;
                }
                _ => break,
            }
            ndirect_msb += 1;
        }
        if ndirect_msb > 0 {
            ndirect_msb -= 1;
        }
        ndirect_msb /= 2;
    }
    if check_orig {
        if let Some(dist_cost) = compute_distance_cost(cmds, &orig_params, &orig_params, &mut tmp) {
            if dist_cost < best_dist_cost {
                params.dist = orig_params;
            }
        }
    }
    recompute_distance_prefixes(cmds, &orig_params, &params.dist);

    brotli_split_block(
        cmds,
        ringbuffer,
        pos,
        mask,
        params,
        &mut mb.literal_split,
        &mut mb.command_split,
        &mut mb.distance_split,
    );

    let mut literal_context_multiplier = 1usize;
    let mut literal_context_modes_vec = Vec::new();
    let literal_context_modes = if !params.disable_literal_context_modeling {
        literal_context_multiplier = 1 << BROTLI_LITERAL_CONTEXT_BITS;
        literal_context_modes_vec.resize(mb.literal_split.num_types, literal_context_mode);
        Some(literal_context_modes_vec.as_slice())
    } else {
        None
    };

    let literal_histograms_size = mb.literal_split.num_types * literal_context_multiplier;
    let mut literal_histograms = vec![HistogramLiteral::default(); literal_histograms_size];
    clear_histograms(&mut literal_histograms);

    let distance_histograms_size = mb.distance_split.num_types << BROTLI_DISTANCE_CONTEXT_BITS;
    let mut distance_histograms = vec![HistogramDistance::default(); distance_histograms_size];
    clear_histograms(&mut distance_histograms);

    mb.command_histograms = vec![HistogramCommand::default(); mb.command_split.num_types];
    clear_histograms(&mut mb.command_histograms);

    brotli_build_histograms_with_context(
        cmds,
        &mb.literal_split,
        &mb.command_split,
        &mb.distance_split,
        ringbuffer,
        pos,
        mask,
        prev_byte,
        prev_byte2,
        literal_context_modes,
        &mut literal_histograms,
        &mut mb.command_histograms,
        &mut distance_histograms,
    );

    let lit_cmap_size = mb.literal_split.num_types << BROTLI_LITERAL_CONTEXT_BITS;
    let mut lit_cmap = vec![0u32; lit_cmap_size];
    let mut out_lit_hist_size = 0usize;
    brotli_cluster_histograms(
        &literal_histograms,
        K_MAX_NUMBER_OF_HISTOGRAMS,
        &mut mb.literal_histograms,
        &mut out_lit_hist_size,
        &mut lit_cmap,
    );

    if params.disable_literal_context_modeling {
        for i in (0..mb.literal_split.num_types).rev() {
            let v = lit_cmap[i];
            for j in 0..(1 << BROTLI_LITERAL_CONTEXT_BITS) {
                lit_cmap[(i << BROTLI_LITERAL_CONTEXT_BITS) + j] = v;
            }
        }
    }
    mb.literal_context_map = Some(lit_cmap);

    if !base64_regions.is_empty()
        && mb.literal_split.num_types < 256
        && mb.literal_histograms.len() < 256
    {
        let safe_num_b64 = base64_regions.len().min(256);
        let local_b64 =
            map_base64_regions_to_literal_positions(cmds, pos, &base64_regions[..safe_num_b64]);
        force_base64_literal_splits(
            &mut mb.literal_split,
            &local_b64,
            0,
            &mut mb.literal_is_base64,
        );
        append_base64_histogram(mb);
    }

    let dist_cmap_size = mb.distance_split.num_types << BROTLI_DISTANCE_CONTEXT_BITS;
    let mut dist_cmap = vec![0u32; dist_cmap_size];
    let mut out_dist_hist_size = 0usize;
    brotli_cluster_histograms(
        &distance_histograms,
        K_MAX_NUMBER_OF_HISTOGRAMS,
        &mut mb.distance_histograms,
        &mut out_dist_hist_size,
        &mut dist_cmap,
    );
    mb.distance_context_map = Some(dist_cmap);
}

struct BlockSplitter<'a, H: HistogramTrait> {
    alphabet_size: usize,
    min_block_size: usize,
    split_threshold: f64,
    num_blocks: usize,
    split: &'a mut BlockSplit,
    histograms: &'a mut Vec<H>,
    target_block_size: usize,
    block_size: usize,
    curr_histogram_ix: usize,
    last_histogram_ix: [usize; 2],
    last_entropy: [f64; 2],
    merge_last_count: usize,
}

impl<'a, H: HistogramTrait> BlockSplitter<'a, H> {
    fn new(
        alphabet_size: usize,
        min_block_size: usize,
        split_threshold: f64,
        num_symbols: usize,
        split: &'a mut BlockSplit,
        histograms: &'a mut Vec<H>,
    ) -> Self {
        let max_num_blocks = num_symbols / min_block_size + 1;
        let max_num_types = max_num_blocks.min(BROTLI_MAX_NUMBER_OF_BLOCK_TYPES + 1);
        split.types.resize(max_num_blocks, 0);
        split.lengths.resize(max_num_blocks, 0);
        split.num_blocks = max_num_blocks;
        split.num_types = 0;
        histograms.resize(max_num_types, H::default());
        histograms[0].clear();
        Self {
            alphabet_size,
            min_block_size,
            split_threshold,
            num_blocks: 0,
            split,
            histograms,
            target_block_size: min_block_size,
            block_size: 0,
            curr_histogram_ix: 0,
            last_histogram_ix: [0, 0],
            last_entropy: [0.0, 0.0],
            merge_last_count: 0,
        }
    }

    fn finish_block(&mut self, is_final: bool) {
        if self.block_size < self.min_block_size {
            self.block_size = self.min_block_size;
        }
        if self.num_blocks == 0 {
            self.split.lengths[0] = self.block_size as u32;
            self.split.types[0] = 0;
            self.last_entropy[0] =
                brotli_bits_entropy(&self.histograms[0].data()[..self.alphabet_size]);
            self.last_entropy[1] = self.last_entropy[0];
            self.num_blocks += 1;
            self.split.num_types += 1;
            self.curr_histogram_ix += 1;
            if self.curr_histogram_ix < self.histograms.len() {
                self.histograms[self.curr_histogram_ix].clear();
            }
            self.block_size = 0;
        } else if self.block_size > 0 {
            let entropy = brotli_bits_entropy(
                &self.histograms[self.curr_histogram_ix].data()[..self.alphabet_size],
            );
            let mut combined_histo = [H::default(), H::default()];
            let mut combined_entropy = [0.0f64; 2];
            let mut diff = [0.0f64; 2];
            for j in 0..2 {
                let last_ix = self.last_histogram_ix[j];
                combined_histo[j] = self.histograms[self.curr_histogram_ix].clone();
                combined_histo[j].add_histogram(&self.histograms[last_ix]);
                combined_entropy[j] =
                    brotli_bits_entropy(&combined_histo[j].data()[..self.alphabet_size]);
                diff[j] = combined_entropy[j] - entropy - self.last_entropy[j];
            }

            if self.split.num_types < BROTLI_MAX_NUMBER_OF_BLOCK_TYPES
                && diff[0] > self.split_threshold
                && diff[1] > self.split_threshold
            {
                self.split.lengths[self.num_blocks] = self.block_size as u32;
                self.split.types[self.num_blocks] = self.split.num_types as u8;
                self.last_histogram_ix[1] = self.last_histogram_ix[0];
                self.last_histogram_ix[0] = self.split.num_types;
                self.last_entropy[1] = self.last_entropy[0];
                self.last_entropy[0] = entropy;
                self.num_blocks += 1;
                self.split.num_types += 1;
                self.curr_histogram_ix += 1;
                if self.curr_histogram_ix < self.histograms.len() {
                    self.histograms[self.curr_histogram_ix].clear();
                }
                self.block_size = 0;
                self.merge_last_count = 0;
                self.target_block_size = self.min_block_size;
            } else if diff[1] < diff[0] - 20.0 {
                self.split.lengths[self.num_blocks] = self.block_size as u32;
                self.split.types[self.num_blocks] = self.split.types[self.num_blocks - 2];
                self.last_histogram_ix.swap(0, 1);
                self.histograms[self.last_histogram_ix[0]] = combined_histo[1].clone();
                self.last_entropy[1] = self.last_entropy[0];
                self.last_entropy[0] = combined_entropy[1];
                self.num_blocks += 1;
                self.block_size = 0;
                self.histograms[self.curr_histogram_ix].clear();
                self.merge_last_count = 0;
                self.target_block_size = self.min_block_size;
            } else {
                self.split.lengths[self.num_blocks - 1] += self.block_size as u32;
                self.histograms[self.last_histogram_ix[0]] = combined_histo[0].clone();
                self.last_entropy[0] = combined_entropy[0];
                if self.split.num_types == 1 {
                    self.last_entropy[1] = self.last_entropy[0];
                }
                self.block_size = 0;
                self.histograms[self.curr_histogram_ix].clear();
                self.merge_last_count += 1;
                if self.merge_last_count > 1 {
                    self.target_block_size += self.min_block_size;
                }
            }
        }
        if is_final {
            self.histograms.truncate(self.split.num_types);
            self.split.num_blocks = self.num_blocks;
            self.split.types.truncate(self.num_blocks);
            self.split.lengths.truncate(self.num_blocks);
        }
    }

    #[inline(always)]
    fn add_symbol(&mut self, symbol: usize) {
        self.histograms[self.curr_histogram_ix].add(symbol);
        self.block_size += 1;
        if self.block_size == self.target_block_size {
            self.finish_block(false);
        }
    }
}

struct ContextBlockSplitter<'a> {
    alphabet_size: usize,
    num_contexts: usize,
    max_block_types: usize,
    min_block_size: usize,
    split_threshold: f64,
    num_blocks: usize,
    split: &'a mut BlockSplit,
    histograms: &'a mut Vec<HistogramLiteral>,
    target_block_size: usize,
    block_size: usize,
    curr_histogram_ix: usize,
    last_histogram_ix: [usize; 2],
    last_entropy: [f64; 2 * BROTLI_MAX_STATIC_CONTEXTS],
    merge_last_count: usize,
}

impl<'a> ContextBlockSplitter<'a> {
    fn new(
        alphabet_size: usize,
        num_contexts: usize,
        min_block_size: usize,
        split_threshold: f64,
        num_symbols: usize,
        split: &'a mut BlockSplit,
        histograms: &'a mut Vec<HistogramLiteral>,
    ) -> Self {
        let max_num_blocks = num_symbols / min_block_size + 1;
        let max_block_types = BROTLI_MAX_NUMBER_OF_BLOCK_TYPES / num_contexts;
        let max_num_types = max_num_blocks.min(max_block_types + 1);
        split.types.resize(max_num_blocks, 0);
        split.lengths.resize(max_num_blocks, 0);
        split.num_blocks = max_num_blocks;
        split.num_types = 0;
        histograms.resize(max_num_types * num_contexts, HistogramLiteral::default());
        clear_histograms(&mut histograms[..num_contexts]);
        Self {
            alphabet_size,
            num_contexts,
            max_block_types,
            min_block_size,
            split_threshold,
            num_blocks: 0,
            split,
            histograms,
            target_block_size: min_block_size,
            block_size: 0,
            curr_histogram_ix: 0,
            last_histogram_ix: [0, 0],
            last_entropy: [0.0; 2 * BROTLI_MAX_STATIC_CONTEXTS],
            merge_last_count: 0,
        }
    }

    fn finish_block(&mut self, is_final: bool) {
        let num_contexts = self.num_contexts;
        if self.block_size < self.min_block_size {
            self.block_size = self.min_block_size;
        }
        if self.num_blocks == 0 {
            self.split.lengths[0] = self.block_size as u32;
            self.split.types[0] = 0;
            for i in 0..num_contexts {
                self.last_entropy[i] =
                    brotli_bits_entropy(&self.histograms[i].data()[..self.alphabet_size]);
                self.last_entropy[num_contexts + i] = self.last_entropy[i];
            }
            self.num_blocks += 1;
            self.split.num_types += 1;
            self.curr_histogram_ix += num_contexts;
            if self.curr_histogram_ix < self.histograms.len() {
                clear_histograms(
                    &mut self.histograms[self.curr_histogram_ix..self.curr_histogram_ix + num_contexts],
                );
            }
            self.block_size = 0;
        } else if self.block_size > 0 {
            let mut entropy = [0.0f64; BROTLI_MAX_STATIC_CONTEXTS];
            let mut combined_histo = vec![HistogramLiteral::default(); 2 * num_contexts];
            let mut combined_entropy = [0.0f64; 2 * BROTLI_MAX_STATIC_CONTEXTS];
            let mut diff = [0.0f64; 2];
            for i in 0..num_contexts {
                let curr_histo_ix = self.curr_histogram_ix + i;
                entropy[i] = brotli_bits_entropy(
                    &self.histograms[curr_histo_ix].data()[..self.alphabet_size],
                );
                for j in 0..2 {
                    let jx = j * num_contexts + i;
                    let last_ix = self.last_histogram_ix[j] + i;
                    combined_histo[jx] = self.histograms[curr_histo_ix].clone();
                    combined_histo[jx].add_histogram(&self.histograms[last_ix]);
                    combined_entropy[jx] =
                        brotli_bits_entropy(&combined_histo[jx].data()[..self.alphabet_size]);
                    diff[j] += combined_entropy[jx] - entropy[i] - self.last_entropy[jx];
                }
            }

            if self.split.num_types < self.max_block_types
                && diff[0] > self.split_threshold
                && diff[1] > self.split_threshold
            {
                self.split.lengths[self.num_blocks] = self.block_size as u32;
                self.split.types[self.num_blocks] = self.split.num_types as u8;
                self.last_histogram_ix[1] = self.last_histogram_ix[0];
                self.last_histogram_ix[0] = self.split.num_types * num_contexts;
                for i in 0..num_contexts {
                    self.last_entropy[num_contexts + i] = self.last_entropy[i];
                    self.last_entropy[i] = entropy[i];
                }
                self.num_blocks += 1;
                self.split.num_types += 1;
                self.curr_histogram_ix += num_contexts;
                if self.curr_histogram_ix < self.histograms.len() {
                    clear_histograms(
                        &mut self.histograms
                            [self.curr_histogram_ix..self.curr_histogram_ix + num_contexts],
                    );
                }
                self.block_size = 0;
                self.merge_last_count = 0;
                self.target_block_size = self.min_block_size;
            } else if diff[1] < diff[0] - 20.0 {
                self.split.lengths[self.num_blocks] = self.block_size as u32;
                self.split.types[self.num_blocks] = self.split.types[self.num_blocks - 2];
                self.last_histogram_ix.swap(0, 1);
                for i in 0..num_contexts {
                    self.histograms[self.last_histogram_ix[0] + i] =
                        combined_histo[num_contexts + i].clone();
                    self.last_entropy[num_contexts + i] = self.last_entropy[i];
                    self.last_entropy[i] = combined_entropy[num_contexts + i];
                    self.histograms[self.curr_histogram_ix + i].clear();
                }
                self.num_blocks += 1;
                self.block_size = 0;
                self.merge_last_count = 0;
                self.target_block_size = self.min_block_size;
            } else {
                self.split.lengths[self.num_blocks - 1] += self.block_size as u32;
                for i in 0..num_contexts {
                    self.histograms[self.last_histogram_ix[0] + i] = combined_histo[i].clone();
                    self.last_entropy[i] = combined_entropy[i];
                    if self.split.num_types == 1 {
                        self.last_entropy[num_contexts + i] = self.last_entropy[i];
                    }
                    self.histograms[self.curr_histogram_ix + i].clear();
                }
                self.block_size = 0;
                self.merge_last_count += 1;
                if self.merge_last_count > 1 {
                    self.target_block_size += self.min_block_size;
                }
            }
        }
        if is_final {
            self.histograms.truncate(self.split.num_types * num_contexts);
            self.split.num_blocks = self.num_blocks;
            self.split.types.truncate(self.num_blocks);
            self.split.lengths.truncate(self.num_blocks);
        }
    }

    #[inline(always)]
    fn add_symbol(&mut self, symbol: usize, context: usize) {
        self.histograms[self.curr_histogram_ix + context].add(symbol);
        self.block_size += 1;
        if self.block_size == self.target_block_size {
            self.finish_block(false);
        }
    }
}

fn map_static_contexts(num_contexts: usize, static_context_map: &[u32], mb: &mut MetaBlockSplit) {
    let size = mb.literal_split.num_types << BROTLI_LITERAL_CONTEXT_BITS;
    let mut map = vec![0u32; size];
    for i in 0..mb.literal_split.num_types {
        let offset = (i * num_contexts) as u32;
        for j in 0..(1usize << BROTLI_LITERAL_CONTEXT_BITS) {
            map[(i << BROTLI_LITERAL_CONTEXT_BITS) + j] = offset + static_context_map[j];
        }
    }
    mb.literal_context_map = Some(map);
}

pub fn brotli_build_meta_block_greedy(
    ringbuffer: &[u8],
    mut pos: usize,
    mask: usize,
    base64_regions: &[Base64Region],
    mut prev_byte: u8,
    mut prev_byte2: u8,
    literal_context_lut: ContextLut,
    num_contexts: usize,
    static_context_map: &[u32],
    commands: &[Command],
    mb: &mut MetaBlockSplit,
) {
    let start_pos = pos;
    let num_literals: usize = commands.iter().map(|c| c.insert_len_ as usize).sum();

    if num_contexts == 1 {
        let mut lit_splitter = BlockSplitter::new(
            256,
            512,
            400.0,
            num_literals,
            &mut mb.literal_split,
            &mut mb.literal_histograms,
        );
        let mut cmd_splitter = BlockSplitter::new(
            BROTLI_NUM_COMMAND_SYMBOLS,
            1024,
            500.0,
            commands.len(),
            &mut mb.command_split,
            &mut mb.command_histograms,
        );
        let mut dist_splitter = BlockSplitter::new(
            64,
            512,
            100.0,
            commands.len(),
            &mut mb.distance_split,
            &mut mb.distance_histograms,
        );

        for cmd in commands {
            cmd_splitter.add_symbol(cmd.cmd_prefix_ as usize);
            for _ in 0..cmd.insert_len_ {
                let literal = ringbuffer[pos & mask];
                lit_splitter.add_symbol(literal as usize);
                pos += 1;
            }
            let copy_len = cmd.copy_len() as usize;
            pos += copy_len;
            if copy_len > 0 && cmd.cmd_prefix_ >= 128 {
                dist_splitter.add_symbol((cmd.dist_prefix_ & 0x3FF) as usize);
            }
        }
        lit_splitter.finish_block(true);
        cmd_splitter.finish_block(true);
        dist_splitter.finish_block(true);
    } else {
        let mut lit_splitter = ContextBlockSplitter::new(
            256,
            num_contexts,
            512,
            400.0,
            num_literals,
            &mut mb.literal_split,
            &mut mb.literal_histograms,
        );
        let mut cmd_splitter = BlockSplitter::new(
            BROTLI_NUM_COMMAND_SYMBOLS,
            1024,
            500.0,
            commands.len(),
            &mut mb.command_split,
            &mut mb.command_histograms,
        );
        let mut dist_splitter = BlockSplitter::new(
            64,
            512,
            100.0,
            commands.len(),
            &mut mb.distance_split,
            &mut mb.distance_histograms,
        );

        for cmd in commands {
            cmd_splitter.add_symbol(cmd.cmd_prefix_ as usize);
            for _ in 0..cmd.insert_len_ {
                let literal = ringbuffer[pos & mask];
                let context = brotli_context(prev_byte, prev_byte2, literal_context_lut) as usize;
                lit_splitter.add_symbol(literal as usize, static_context_map[context] as usize);
                prev_byte2 = prev_byte;
                prev_byte = literal;
                pos += 1;
            }
            let copy_len = cmd.copy_len() as usize;
            pos += copy_len;
            if copy_len > 0 {
                prev_byte2 = ringbuffer[pos.wrapping_sub(2) & mask];
                prev_byte = ringbuffer[pos.wrapping_sub(1) & mask];
                if cmd.cmd_prefix_ >= 128 {
                    dist_splitter.add_symbol((cmd.dist_prefix_ & 0x3FF) as usize);
                }
            }
        }
        lit_splitter.finish_block(true);
        cmd_splitter.finish_block(true);
        dist_splitter.finish_block(true);
    }

    if !base64_regions.is_empty()
        && mb.literal_split.num_types < 256
        && mb.literal_histograms.len() + num_contexts <= 256
    {
        let safe_num_b64 = base64_regions.len().min(256);
        let local_b64 = map_base64_regions_to_literal_positions(
            commands,
            start_pos,
            &base64_regions[..safe_num_b64],
        );
        force_base64_literal_splits(
            &mut mb.literal_split,
            &local_b64,
            0,
            &mut mb.literal_is_base64,
        );
        for _ in 0..num_contexts {
            let mut h = HistogramLiteral::default();
            h.clear();
            mb.literal_histograms.push(h);
        }
    }

    if num_contexts > 1 {
        map_static_contexts(num_contexts, static_context_map, mb);
    }
}

pub fn brotli_optimize_histograms(num_distance_codes: u32, mb: &mut MetaBlockSplit) {
    for h in &mut mb.literal_histograms {
        brotli_optimize_huffman_counts_for_rle(256, &mut h.data_);
    }
    for h in &mut mb.command_histograms {
        brotli_optimize_huffman_counts_for_rle(BROTLI_NUM_COMMAND_SYMBOLS, &mut h.data_);
    }
    for h in &mut mb.distance_histograms {
        brotli_optimize_huffman_counts_for_rle(num_distance_codes as usize, &mut h.data_);
    }
}
