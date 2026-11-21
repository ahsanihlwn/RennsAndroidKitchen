#![allow(dead_code)]

use crate::common::constants::*;
use crate::common::context::{ContextLut, brotli_context};
use crate::enc::command::{
    Command, combine_length_codes, get_copy_extra, get_copy_length_code, get_insert_extra,
    get_insert_length_code, prefix_encode_copy_distance,
};
use crate::enc::fast_log::fast_log2;
use crate::enc::find_match_length::find_match_length_with_limit;
use crate::enc::hash::{
    BackwardMatch, HashToBinaryTree, Hasher, HasherInner, MAX_NUM_MATCHES_H10,
    lookup_all_compound_dictionary_matches,
};
use crate::enc::histogram::BROTLI_NUM_HISTOGRAM_DISTANCE_SYMBOLS;
use crate::enc::literal_cost::brotli_estimate_bit_costs_for_literals;
use crate::enc::params::{BrotliDistanceParams, BrotliEncoderParams};
use crate::enc::quality::{
    BROTLI_LONG_COPY_QUICK_STEP, MAX_ZOPFLI_LEN_QUALITY_11, max_zopfli_candidates, max_zopfli_len,
};

const K_INFINITY: f32 = 1.7e38f32;

static K_DISTANCE_CACHE_INDEX: [usize; 16] = [0, 1, 2, 3, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1];
static K_DISTANCE_CACHE_OFFSET: [isize; 16] = [0, 0, 0, 0, -1, 1, -2, 2, -3, 3, -1, 1, -2, 2, -3, 3];

#[derive(Clone, Copy)]
pub union ZopfliUnion {
    pub cost: f32,
    pub next: u32,
    pub shortcut: u32,
}

#[derive(Clone, Copy)]
pub struct ZopfliNode {
    pub length: u32,
    pub distance: u32,
    pub dcode_insert_length: u32,
    pub u: ZopfliUnion,
}

impl Default for ZopfliNode {
    fn default() -> Self {
        Self {
            length: 1,
            distance: 0,
            dcode_insert_length: 0,
            u: ZopfliUnion { cost: K_INFINITY },
        }
    }
}

impl ZopfliNode {
    #[inline(always)]
    fn copy_length(&self) -> u32 {
        self.length & 0x1FFFFFF
    }

    #[inline(always)]
    fn length_code(&self) -> u32 {
        let modifier = self.length >> 25;
        self.copy_length() + 9 - modifier
    }

    #[inline(always)]
    fn copy_distance(&self) -> u32 {
        self.distance
    }

    #[inline(always)]
    fn distance_code(&self) -> u32 {
        let short_code = self.dcode_insert_length >> 27;
        if short_code == 0 {
            self.copy_distance() + (BROTLI_NUM_DISTANCE_SHORT_CODES as u32) - 1
        } else {
            short_code - 1
        }
    }

    #[inline(always)]
    fn command_length(&self) -> u32 {
        self.copy_length() + (self.dcode_insert_length & 0x7FFFFFF)
    }
}

pub fn brotli_init_zopfli_nodes(array: &mut [ZopfliNode]) {
    let stub = ZopfliNode::default();
    array.fill(stub);
}

struct ZopfliCostModel {
    cost_cmd: [f32; BROTLI_NUM_COMMAND_SYMBOLS],
    cost_dist: Vec<f32>,
    distance_histogram_size: usize,
    literal_costs: Vec<f32>,
    min_cost_cmd: f32,
    num_bytes: usize,
}

impl ZopfliCostModel {
    fn new(dist: &BrotliDistanceParams, num_bytes: usize) -> Self {
        let distance_histogram_size = dist.alphabet_size_limit as usize;
        Self {
            cost_cmd: [0.0; BROTLI_NUM_COMMAND_SYMBOLS],
            cost_dist: vec![0.0; distance_histogram_size],
            distance_histogram_size,
            literal_costs: vec![0.0; num_bytes + 2],
            min_cost_cmd: 0.0,
            num_bytes,
        }
    }

    fn set_cost(histogram: &[u32], literal_histogram: bool, cost: &mut [f32]) {
        let histogram_size = histogram.len();
        let sum: usize = histogram.iter().map(|&v| v as usize).sum();
        let log2sum = fast_log2(sum) as f32;
        let mut missing_symbol_sum = sum;
        if !literal_histogram {
            for &v in histogram {
                if v == 0 {
                    missing_symbol_sum += 1;
                }
            }
        }
        let missing_symbol_cost = (fast_log2(missing_symbol_sum) as f32) + 2.0;
        for i in 0..histogram_size {
            if histogram[i] == 0 {
                cost[i] = missing_symbol_cost;
                continue;
            }
            let c = log2sum - (fast_log2(histogram[i] as usize) as f32);
            cost[i] = if c < 1.0 { 1.0 } else { c };
        }
    }

    fn set_from_commands(
        &mut self,
        position: usize,
        ringbuffer: &[u8],
        ringbuffer_mask: usize,
        commands: &[Command],
        last_insert_len: usize,
    ) {
        let mut pos = position - last_insert_len;
        let mut histogram_literal = [0u32; BROTLI_NUM_LITERAL_SYMBOLS];
        let mut histogram_cmd = [0u32; BROTLI_NUM_COMMAND_SYMBOLS];
        let mut histogram_dist = [0u32; BROTLI_NUM_HISTOGRAM_DISTANCE_SYMBOLS];
        let mut cost_literal = [0.0f32; BROTLI_NUM_LITERAL_SYMBOLS];

        for cmd in commands {
            let inslength = cmd.insert_len_ as usize;
            let copylength = cmd.copy_len() as usize;
            let distcode = (cmd.dist_prefix_ & 0x3FF) as usize;
            let cmdcode = cmd.cmd_prefix_ as usize;

            histogram_cmd[cmdcode] += 1;
            if cmdcode >= 128 {
                histogram_dist[distcode] += 1;
            }
            for j in 0..inslength {
                histogram_literal[ringbuffer[(pos + j) & ringbuffer_mask] as usize] += 1;
            }
            pos += inslength + copylength;
        }

        Self::set_cost(&histogram_literal, true, &mut cost_literal);
        Self::set_cost(&histogram_cmd, false, &mut self.cost_cmd);
        Self::set_cost(
            &histogram_dist[..self.distance_histogram_size],
            false,
            &mut self.cost_dist,
        );

        let mut min_cost_cmd = K_INFINITY;
        for &c in &self.cost_cmd {
            if c < min_cost_cmd {
                min_cost_cmd = c;
            }
        }
        self.min_cost_cmd = min_cost_cmd;

        let mut literal_carry = 0.0f32;
        self.literal_costs[0] = 0.0;
        for i in 0..self.num_bytes {
            literal_carry += cost_literal[ringbuffer[(position + i) & ringbuffer_mask] as usize];
            self.literal_costs[i + 1] = self.literal_costs[i] + literal_carry;
            literal_carry -= self.literal_costs[i + 1] - self.literal_costs[i];
        }
    }

    fn set_from_literal_costs(&mut self, position: usize, ringbuffer: &[u8], ringbuffer_mask: usize) {
        let mut literal_histograms = [0usize; 3 * 256];
        brotli_estimate_bit_costs_for_literals(
            position,
            self.num_bytes,
            ringbuffer_mask,
            ringbuffer,
            &mut literal_histograms,
            &mut self.literal_costs[1..],
        );
        self.literal_costs[0] = 0.0;
        let mut literal_carry = 0.0f32;
        for i in 0..self.num_bytes {
            literal_carry += self.literal_costs[i + 1];
            self.literal_costs[i + 1] = self.literal_costs[i] + literal_carry;
            literal_carry -= self.literal_costs[i + 1] - self.literal_costs[i];
        }
        for i in 0..BROTLI_NUM_COMMAND_SYMBOLS {
            self.cost_cmd[i] = fast_log2(11 + i) as f32;
        }
        for i in 0..self.distance_histogram_size {
            self.cost_dist[i] = fast_log2(20 + i) as f32;
        }
        self.min_cost_cmd = fast_log2(11) as f32;
    }

    #[inline(always)]
    fn get_command_cost(&self, cmdcode: u16) -> f32 {
        self.cost_cmd[cmdcode as usize]
    }

    #[inline(always)]
    fn get_distance_cost(&self, distcode: usize) -> f32 {
        self.cost_dist[distcode]
    }

    #[inline(always)]
    fn get_literal_costs(&self, from: usize, to: usize) -> f32 {
        self.literal_costs[to] - self.literal_costs[from]
    }

    #[inline(always)]
    fn get_min_cost_cmd(&self) -> f32 {
        self.min_cost_cmd
    }
}

#[inline(always)]
fn update_zopfli_node(
    nodes: &mut [ZopfliNode],
    pos: usize,
    start_pos: usize,
    len: usize,
    len_code: usize,
    dist: usize,
    short_code: usize,
    cost: f32,
) {
    let next = &mut nodes[pos + len];
    next.length = (len | ((len + 9 - len_code) << 25)) as u32;
    next.distance = dist as u32;
    next.dcode_insert_length = ((short_code << 27) | (pos - start_pos)) as u32;
    next.u = ZopfliUnion { cost };
}

#[derive(Debug, Clone, Copy, Default)]
struct PosData {
    pos: usize,
    distance_cache: [i32; 4],
    costdiff: f32,
    cost: f32,
}

struct StartPosQueue {
    q: [PosData; 8],
    idx: usize,
}

impl StartPosQueue {
    fn new() -> Self {
        Self {
            q: [PosData::default(); 8],
            idx: 0,
        }
    }

    #[inline(always)]
    fn size(&self) -> usize {
        self.idx.min(8)
    }

    fn push(&mut self, posdata: &PosData) {
        let mut offset = (!self.idx) & 7;
        self.idx += 1;
        let len = self.size();
        self.q[offset] = *posdata;
        for _ in 1..len {
            if self.q[offset & 7].costdiff > self.q[(offset + 1) & 7].costdiff {
                self.q.swap(offset & 7, (offset + 1) & 7);
            }
            offset += 1;
        }
    }

    #[inline(always)]
    fn at(&self, k: usize) -> &PosData {
        &self.q[(k.wrapping_sub(self.idx)) & 7]
    }
}

fn compute_minimum_copy_length(
    start_cost: f32,
    nodes: &[ZopfliNode],
    num_bytes: usize,
    pos: usize,
) -> usize {
    let mut min_cost = start_cost;
    let mut len = 2usize;
    let mut next_len_bucket = 4usize;
    let mut next_len_offset = 10usize;
    while pos + len <= num_bytes && unsafe { nodes[pos + len].u.cost } <= min_cost {
        len += 1;
        if len == next_len_offset {
            min_cost += 1.0;
            next_len_offset += next_len_bucket;
            next_len_bucket *= 2;
        }
    }
    len
}

fn compute_distance_shortcut(
    block_start: usize,
    pos: usize,
    max_backward_limit: usize,
    gap: usize,
    nodes: &[ZopfliNode],
) -> u32 {
    let c_len = nodes[pos].copy_length() as usize;
    let i_len = (nodes[pos].dcode_insert_length & 0x7FFFFFF) as usize;
    let dist = nodes[pos].copy_distance() as usize;
    if pos == 0 {
        0
    } else if dist + c_len <= block_start + pos + gap
        && dist <= max_backward_limit + gap
        && nodes[pos].distance_code() > 0
    {
        pos as u32
    } else {
        unsafe { nodes[pos - c_len - i_len].u.shortcut }
    }
}

fn compute_distance_cache(
    pos: usize,
    starting_dist_cache: &[i32],
    nodes: &[ZopfliNode],
    dist_cache: &mut [i32; 4],
) {
    let mut idx = 0usize;
    let mut p = unsafe { nodes[pos].u.shortcut } as usize;
    while idx < 4 && p > 0 {
        let i_len = (nodes[p].dcode_insert_length & 0x7FFFFFF) as usize;
        let c_len = nodes[p].copy_length() as usize;
        let dist = nodes[p].copy_distance() as i32;
        dist_cache[idx] = dist;
        idx += 1;
        p = unsafe { nodes[p - c_len - i_len].u.shortcut } as usize;
    }
    let mut s_idx = 0usize;
    while idx < 4 {
        dist_cache[idx] = starting_dist_cache[s_idx];
        idx += 1;
        s_idx += 1;
    }
}

fn evaluate_node(
    block_start: usize,
    pos: usize,
    max_backward_limit: usize,
    gap: usize,
    starting_dist_cache: &[i32],
    model: &ZopfliCostModel,
    queue: &mut StartPosQueue,
    nodes: &mut [ZopfliNode],
) {
    let node_cost = unsafe { nodes[pos].u.cost };
    nodes[pos].u = ZopfliUnion {
        shortcut: compute_distance_shortcut(block_start, pos, max_backward_limit, gap, nodes),
    };
    let lit_cost = model.get_literal_costs(0, pos);
    if node_cost <= lit_cost {
        let mut posdata = PosData {
            pos,
            distance_cache: [0; 4],
            costdiff: node_cost - lit_cost,
            cost: node_cost,
        };
        compute_distance_cache(pos, starting_dist_cache, nodes, &mut posdata.distance_cache);
        queue.push(&posdata);
    }
}

fn update_nodes(
    num_bytes: usize,
    block_start: usize,
    pos: usize,
    ringbuffer: &[u8],
    ringbuffer_mask: usize,
    params: &BrotliEncoderParams,
    max_backward_limit: usize,
    starting_dist_cache: &[i32],
    matches: &[BackwardMatch],
    model: &ZopfliCostModel,
    queue: &mut StartPosQueue,
    nodes: &mut [ZopfliNode],
) -> usize {
    let stream_offset = params.stream_offset;
    let cur_ix = block_start + pos;
    let cur_ix_masked = cur_ix & ringbuffer_mask;
    let max_distance = cur_ix.min(max_backward_limit);
    let dictionary_start = (cur_ix + stream_offset).min(max_backward_limit);
    let max_len = num_bytes - pos;
    let max_zopfli_len = max_zopfli_len(params);
    let max_iters = max_zopfli_candidates(params);
    let addon = &params.dictionary.compound;
    let gap = addon.total_size;

    evaluate_node(
        block_start + stream_offset,
        pos,
        max_backward_limit,
        gap,
        starting_dist_cache,
        model,
        queue,
        nodes,
    );

    let min_len = {
        let posdata = queue.at(0);
        let min_cost = posdata.cost + model.get_min_cost_cmd() + model.get_literal_costs(posdata.pos, pos);
        compute_minimum_copy_length(min_cost, nodes, num_bytes, pos)
    };

    let mut result = 0usize;
    let q_size = queue.size();
    let iters = max_iters.min(q_size);
    for k in 0..iters {
        let posdata = *queue.at(k);
        let start = posdata.pos;
        let inscode = get_insert_length_code(pos - start);
        let start_costdiff = posdata.costdiff;
        let base_cost = start_costdiff + (get_insert_extra(inscode) as f32) + model.get_literal_costs(0, pos);

        let mut best_len = min_len - 1;
        for j in 0..BROTLI_NUM_DISTANCE_SHORT_CODES {
            if best_len >= max_len {
                break;
            }
            let idx = K_DISTANCE_CACHE_INDEX[j];
            let backward = (posdata.distance_cache[idx] as isize + K_DISTANCE_CACHE_OFFSET[j]) as usize;
            let mut prev_ix = cur_ix.wrapping_sub(backward);
            let continuation = ringbuffer[cur_ix_masked + best_len];
            if cur_ix_masked + best_len > ringbuffer_mask {
                break;
            }
            if backward > dictionary_start + gap {
                continue;
            }
            let len;
            if backward <= max_distance {
                if prev_ix >= cur_ix {
                    continue;
                }
                prev_ix &= ringbuffer_mask;
                if prev_ix + best_len > ringbuffer_mask || continuation != ringbuffer[prev_ix + best_len] {
                    continue;
                }
                len = find_match_length_with_limit(
                    &ringbuffer[prev_ix..],
                    &ringbuffer[cur_ix_masked..],
                    max_len,
                );
            } else if backward > dictionary_start {
                let mut d = 0usize;
                let mut offset = dictionary_start + 1 + addon.total_size - 1;
                while offset >= backward + addon.chunk_offsets[d + 1] {
                    d += 1;
                }
                let source = &addon.chunks[d].source;
                offset = offset - addon.chunk_offsets[d] - backward;
                let limit = (addon.chunk_offsets[d + 1] - addon.chunk_offsets[d] - offset).min(max_len);
                if best_len >= limit || continuation != source[offset + best_len] {
                    continue;
                }
                len = find_match_length_with_limit(&source[offset..], &ringbuffer[cur_ix_masked..], limit);
            } else {
                continue;
            }

            let dist_cost = base_cost + model.get_distance_cost(j);
            for l in (best_len + 1)..=len {
                let copycode = get_copy_length_code(l);
                let cmdcode = combine_length_codes(inscode, copycode, j == 0);
                let cost = (if cmdcode < 128 { base_cost } else { dist_cost })
                    + (get_copy_extra(copycode) as f32)
                    + model.get_command_cost(cmdcode);
                if cost < unsafe { nodes[pos + l].u.cost } {
                    update_zopfli_node(nodes, pos, start, l, l, backward, j + 1, cost);
                    result = result.max(l);
                }
                best_len = l;
            }
        }

        if k >= 2 {
            continue;
        }

        let mut len = min_len;
        for m in matches {
            let dist = m.distance as usize;
            let is_dictionary_match = dist > dictionary_start + gap;
            let dist_code = dist + BROTLI_NUM_DISTANCE_SHORT_CODES - 1;
            let (dist_symbol, _) = prefix_encode_copy_distance(
                dist_code,
                params.dist.num_direct_distance_codes as usize,
                params.dist.distance_postfix_bits as usize,
            );
            let distnumextra = dist_symbol >> 10;
            let dist_cost =
                base_cost + (distnumextra as f32) + model.get_distance_cost((dist_symbol & 0x3FF) as usize);

            let max_match_len = m.length();
            if len < max_match_len && (is_dictionary_match || max_match_len > max_zopfli_len) {
                len = max_match_len;
            }
            while len <= max_match_len {
                let len_code = if is_dictionary_match { m.length_code() } else { len };
                let copycode = get_copy_length_code(len_code);
                let cmdcode = combine_length_codes(inscode, copycode, false);
                let cost = dist_cost
                    + (get_copy_extra(copycode) as f32)
                    + model.get_command_cost(cmdcode);
                if cost < unsafe { nodes[pos + len].u.cost } {
                    update_zopfli_node(nodes, pos, start, len, len_code, dist, 0, cost);
                    result = result.max(len);
                }
                len += 1;
            }
        }
    }
    result
}

fn compute_shortest_path_from_nodes(num_bytes: usize, nodes: &mut [ZopfliNode]) -> usize {
    let mut index = num_bytes;
    let mut num_commands = 0usize;
    while (nodes[index].dcode_insert_length & 0x7FFFFFF) == 0 && nodes[index].length == 1 {
        index -= 1;
    }
    nodes[index].u = ZopfliUnion { next: u32::MAX };
    while index != 0 {
        let len = nodes[index].command_length() as usize;
        index -= len;
        nodes[index].u = ZopfliUnion { next: len as u32 };
        num_commands += 1;
    }
    num_commands
}

pub fn brotli_zopfli_create_commands(
    num_bytes: usize,
    block_start: usize,
    nodes: &[ZopfliNode],
    dist_cache: &mut [i32],
    last_insert_len: &mut usize,
    params: &BrotliEncoderParams,
    commands: &mut Vec<Command>,
    num_literals: &mut usize,
) {
    let stream_offset = params.stream_offset;
    let max_backward_limit = brotli_max_backward_limit(params.lgwin as u32);
    let mut pos = 0usize;
    let mut offset = unsafe { nodes[0].u.next };
    let gap = params.dictionary.compound.total_size;
    let mut i = 0usize;
    while offset != u32::MAX {
        let next = &nodes[pos + (offset as usize)];
        let copy_length = next.copy_length() as usize;
        let mut insert_length = (next.dcode_insert_length & 0x7FFFFFF) as usize;
        pos += insert_length;
        offset = unsafe { next.u.next };
        if i == 0 {
            insert_length += *last_insert_len;
            *last_insert_len = 0;
        }
        let distance = next.copy_distance() as usize;
        let len_code = next.length_code() as usize;
        let dictionary_start = (block_start + pos + stream_offset).min(max_backward_limit);
        let is_dictionary = distance > dictionary_start + gap;
        let dist_code = next.distance_code() as usize;
        commands.push(Command::new(
            &params.dist,
            insert_length,
            copy_length,
            (len_code as i32) - (copy_length as i32),
            dist_code,
        ));
        if !is_dictionary && dist_code > 0 {
            dist_cache[3] = dist_cache[2];
            dist_cache[2] = dist_cache[1];
            dist_cache[1] = dist_cache[0];
            dist_cache[0] = distance as i32;
        }
        *num_literals += insert_length;
        pos += copy_length;
        i += 1;
    }
    *last_insert_len += num_bytes - pos;
}

fn zopfli_iterate(
    num_bytes: usize,
    position: usize,
    ringbuffer: &[u8],
    ringbuffer_mask: usize,
    params: &BrotliEncoderParams,
    gap: usize,
    dist_cache: &[i32],
    model: &ZopfliCostModel,
    num_matches: &[u32],
    matches: &[BackwardMatch],
    nodes: &mut [ZopfliNode],
) -> usize {
    let stream_offset = params.stream_offset;
    let max_backward_limit = brotli_max_backward_limit(params.lgwin as u32);
    let max_zopfli_len = max_zopfli_len(params);
    let mut queue = StartPosQueue::new();
    let mut cur_match_pos = 0usize;
    nodes[0].length = 0;
    nodes[0].u = ZopfliUnion { cost: 0.0 };

    let mut i = 0usize;
    while i + 3 < num_bytes {
        let nm = num_matches[i] as usize;
        let mut skip = update_nodes(
            num_bytes,
            position,
            i,
            ringbuffer,
            ringbuffer_mask,
            params,
            max_backward_limit,
            dist_cache,
            &matches[cur_match_pos..cur_match_pos + nm],
            model,
            &mut queue,
            nodes,
        );
        if skip < BROTLI_LONG_COPY_QUICK_STEP {
            skip = 0;
        }
        cur_match_pos += nm;
        if nm == 1 && matches[cur_match_pos - 1].length() > max_zopfli_len {
            skip = skip.max(matches[cur_match_pos - 1].length());
        }
        if skip > 1 {
            skip -= 1;
            while skip != 0 {
                i += 1;
                if i + 3 >= num_bytes {
                    break;
                }
                evaluate_node(
                    position + stream_offset,
                    i,
                    max_backward_limit,
                    gap,
                    dist_cache,
                    model,
                    &mut queue,
                    nodes,
                );
                cur_match_pos += num_matches[i] as usize;
                skip -= 1;
            }
        }
        i += 1;
    }
    compute_shortest_path_from_nodes(num_bytes, nodes)
}

fn merge_matches(dst: &mut [BackwardMatch], src1: &[BackwardMatch], src2: &[BackwardMatch]) {
    let mut i1 = 0usize;
    let mut i2 = 0usize;
    let mut d = 0usize;
    while i1 < src1.len() && i2 < src2.len() {
        let l1 = src1[i1].length();
        let l2 = src2[i2].length();
        if l1 < l2 || (l1 == l2 && src1[i1].distance < src2[i2].distance) {
            dst[d] = src1[i1];
            d += 1;
            i1 += 1;
        } else {
            dst[d] = src2[i2];
            d += 1;
            i2 += 1;
        }
    }
    while i1 < src1.len() {
        dst[d] = src1[i1];
        d += 1;
        i1 += 1;
    }
    while i2 < src2.len() {
        dst[d] = src2[i2];
        d += 1;
        i2 += 1;
    }
}

pub fn brotli_zopfli_compute_shortest_path(
    num_bytes: usize,
    position: usize,
    ringbuffer: &[u8],
    ringbuffer_mask: usize,
    literal_context_lut: ContextLut,
    params: &BrotliEncoderParams,
    dist_cache: &[i32],
    h10: &mut HashToBinaryTree,
    nodes: &mut [ZopfliNode],
) -> usize {
    let stream_offset = params.stream_offset;
    let max_backward_limit = brotli_max_backward_limit(params.lgwin as u32);
    let max_zopfli_len = max_zopfli_len(params);
    let mut queue = StartPosQueue::new();
    let mut matches = vec![BackwardMatch::default(); 2 * (MAX_NUM_MATCHES_H10 + 64)];
    let mut cd_matches_buf = [BackwardMatch::default(); 64];
    let mut lz_matches_buf = [BackwardMatch::default(); MAX_NUM_MATCHES_H10];
    let store_end = if num_bytes >= h10.store_lookahead() {
        position + num_bytes - h10.store_lookahead() + 1
    } else {
        position
    };
    let addon = &params.dictionary.compound;
    let gap = addon.total_size;

    nodes[0].length = 0;
    nodes[0].u = ZopfliUnion { cost: 0.0 };
    let mut model = ZopfliCostModel::new(&params.dist, num_bytes);
    model.set_from_literal_costs(position, ringbuffer, ringbuffer_mask);

    let mut i = 0usize;
    while i + h10.hash_type_length() - 1 < num_bytes {
        let pos = position + i;
        let max_distance = pos.min(max_backward_limit);
        let dictionary_start = (pos + stream_offset).min(max_backward_limit);
        let mut dict_id = 0usize;
        if params.dictionary.contextual.context_based {
            let p1 = if pos >= 1 {
                ringbuffer[(pos - 1) & ringbuffer_mask]
            } else {
                0
            };
            let p2 = if pos >= 2 {
                ringbuffer[(pos - 2) & ringbuffer_mask]
            } else {
                0
            };
            dict_id = params.dictionary.contextual.context_map
                [brotli_context(p1, p2, literal_context_lut) as usize] as usize;
        }
        let mut num_matches = if addon.num_chunks != 0 {
            let lz_n = h10.find_all_matches(
                params.dictionary.contextual.dict_for(dict_id),
                ringbuffer,
                ringbuffer_mask,
                pos,
                num_bytes - i,
                max_distance,
                dictionary_start + gap,
                params,
                &mut lz_matches_buf,
            );
            let cd_n = lookup_all_compound_dictionary_matches(
                addon,
                ringbuffer,
                ringbuffer_mask,
                pos,
                3,
                num_bytes - i,
                dictionary_start,
                params.dist.max_distance,
                &mut cd_matches_buf,
            );
            merge_matches(&mut matches, &cd_matches_buf[..cd_n], &lz_matches_buf[..lz_n]);
            lz_n + cd_n
        } else {
            h10.find_all_matches(
                params.dictionary.contextual.dict_for(dict_id),
                ringbuffer,
                ringbuffer_mask,
                pos,
                num_bytes - i,
                max_distance,
                dictionary_start + gap,
                params,
                &mut matches,
            )
        };
        if num_matches > 0 && matches[num_matches - 1].length() > max_zopfli_len {
            matches[0] = matches[num_matches - 1];
            num_matches = 1;
        }
        let mut skip = update_nodes(
            num_bytes,
            position,
            i,
            ringbuffer,
            ringbuffer_mask,
            params,
            max_backward_limit,
            dist_cache,
            &matches[..num_matches],
            &model,
            &mut queue,
            nodes,
        );
        if skip < BROTLI_LONG_COPY_QUICK_STEP {
            skip = 0;
        }
        if num_matches == 1 && matches[0].length() > max_zopfli_len {
            skip = skip.max(matches[0].length());
        }
        if skip > 1 {
            h10.store_range(ringbuffer, ringbuffer_mask, pos + 1, (pos + skip).min(store_end));
            skip -= 1;
            while skip != 0 {
                i += 1;
                if i + h10.hash_type_length() - 1 >= num_bytes {
                    break;
                }
                evaluate_node(
                    position + stream_offset,
                    i,
                    max_backward_limit,
                    gap,
                    dist_cache,
                    &model,
                    &mut queue,
                    nodes,
                );
                skip -= 1;
            }
        }
        i += 1;
    }
    compute_shortest_path_from_nodes(num_bytes, nodes)
}

pub fn brotli_create_zopfli_backward_references(
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
    let h10 = match &mut hasher.inner {
        HasherInner::H10(h) => h,
        _ => unreachable!("Expected H10 hasher for Zopfli"),
    };
    let mut nodes = vec![ZopfliNode::default(); num_bytes + 1];
    brotli_init_zopfli_nodes(&mut nodes);
    *num_commands += brotli_zopfli_compute_shortest_path(
        num_bytes,
        position,
        ringbuffer,
        ringbuffer_mask,
        literal_context_lut,
        params,
        dist_cache,
        h10,
        &mut nodes,
    );
    brotli_zopfli_create_commands(
        num_bytes,
        position,
        &nodes,
        dist_cache,
        last_insert_len,
        params,
        commands,
        num_literals,
    );
}

pub fn brotli_create_hq_zopfli_backward_references(
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
    let h10 = match &mut hasher.inner {
        HasherInner::H10(h) => h,
        _ => unreachable!("Expected H10 hasher for HQ Zopfli"),
    };
    let stream_offset = params.stream_offset;
    let max_backward_limit = brotli_max_backward_limit(params.lgwin as u32);
    let mut num_matches = vec![0u32; num_bytes];
    let store_end = if num_bytes >= h10.store_lookahead() {
        position + num_bytes - h10.store_lookahead() + 1
    } else {
        position
    };
    let mut matches: Vec<BackwardMatch> = Vec::with_capacity(4 * num_bytes);
    let addon = &params.dictionary.compound;
    let gap = addon.total_size;
    let mut lz_matches_buf = [BackwardMatch::default(); MAX_NUM_MATCHES_H10];
    let mut cd_matches_buf = [BackwardMatch::default(); 64];
    let mut merged_buf = [BackwardMatch::default(); MAX_NUM_MATCHES_H10 + 64];

    let mut i = 0usize;
    while i + h10.hash_type_length() - 1 < num_bytes {
        let pos = position + i;
        let max_distance = pos.min(max_backward_limit);
        let dictionary_start = (pos + stream_offset).min(max_backward_limit);
        let max_length = num_bytes - i;
        let mut dict_id = 0usize;
        if params.dictionary.contextual.context_based {
            let p1 = if pos >= 1 {
                ringbuffer[(pos - 1) & ringbuffer_mask]
            } else {
                0
            };
            let p2 = if pos >= 2 {
                ringbuffer[(pos - 2) & ringbuffer_mask]
            } else {
                0
            };
            dict_id = params.dictionary.contextual.context_map
                [brotli_context(p1, p2, literal_context_lut) as usize] as usize;
        }
        let num_found_matches = if addon.num_chunks != 0 {
            let lz_n = h10.find_all_matches(
                params.dictionary.contextual.dict_for(dict_id),
                ringbuffer,
                ringbuffer_mask,
                pos,
                max_length,
                max_distance,
                dictionary_start + gap,
                params,
                &mut lz_matches_buf,
            );
            let cd_n = lookup_all_compound_dictionary_matches(
                addon,
                ringbuffer,
                ringbuffer_mask,
                pos,
                3,
                max_length,
                dictionary_start,
                params.dist.max_distance,
                &mut cd_matches_buf,
            );
            let total = lz_n + cd_n;
            merge_matches(
                &mut merged_buf[..total],
                &cd_matches_buf[..cd_n],
                &lz_matches_buf[..lz_n],
            );
            total
        } else {
            h10.find_all_matches(
                params.dictionary.contextual.dict_for(dict_id),
                ringbuffer,
                ringbuffer_mask,
                pos,
                max_length,
                max_distance,
                dictionary_start + gap,
                params,
                &mut merged_buf,
            )
        };
        num_matches[i] = num_found_matches as u32;
        if num_found_matches > 0 {
            let match_len = merged_buf[num_found_matches - 1].length();
            if match_len > MAX_ZOPFLI_LEN_QUALITY_11 {
                let skip = match_len - 1;
                matches.push(merged_buf[num_found_matches - 1]);
                num_matches[i] = 1;
                h10.store_range(
                    ringbuffer,
                    ringbuffer_mask,
                    pos + 1,
                    (pos + match_len).min(store_end),
                );
                i += skip;
            } else {
                matches.extend_from_slice(&merged_buf[..num_found_matches]);
            }
        }
        i += 1;
    }

    let orig_num_literals = *num_literals;
    let orig_last_insert_len = *last_insert_len;
    let orig_dist_cache = [dist_cache[0], dist_cache[1], dist_cache[2], dist_cache[3]];
    let orig_num_commands = *num_commands;
    let orig_cmds_vec_len = commands.len();

    let mut nodes = vec![ZopfliNode::default(); num_bytes + 1];
    let mut model = ZopfliCostModel::new(&params.dist, num_bytes);
    for pass in 0..2 {
        brotli_init_zopfli_nodes(&mut nodes);
        if pass == 0 {
            model.set_from_literal_costs(position, ringbuffer, ringbuffer_mask);
        } else {
            model.set_from_commands(
                position,
                ringbuffer,
                ringbuffer_mask,
                &commands[orig_cmds_vec_len..],
                orig_last_insert_len,
            );
        }
        commands.truncate(orig_cmds_vec_len);
        *num_commands = orig_num_commands;
        *num_literals = orig_num_literals;
        *last_insert_len = orig_last_insert_len;
        dist_cache[..4].copy_from_slice(&orig_dist_cache);
        *num_commands += zopfli_iterate(
            num_bytes,
            position,
            ringbuffer,
            ringbuffer_mask,
            params,
            gap,
            dist_cache,
            &model,
            &num_matches,
            &matches,
            &mut nodes,
        );
        brotli_zopfli_create_commands(
            num_bytes,
            position,
            &nodes,
            dist_cache,
            last_insert_len,
            params,
            commands,
            num_literals,
        );
    }
}
