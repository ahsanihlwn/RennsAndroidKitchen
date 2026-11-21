#![allow(dead_code)]

use crate::common::constants::*;
use crate::common::context::{ContextType, brotli_context, brotli_context_lut};
use crate::enc::block_splitter::BlockSplit;
use crate::enc::command::Command;

pub const BROTLI_NUM_HISTOGRAM_DISTANCE_SYMBOLS: usize = 544;

pub trait HistogramTrait: Clone + Default {
    fn data(&self) -> &[u32];
    fn data_mut(&mut self) -> &mut [u32];
    fn total_count(&self) -> usize;
    fn set_total_count(&mut self, count: usize);
    fn bit_cost(&self) -> f64;
    fn set_bit_cost(&mut self, cost: f64);

    #[inline(always)]
    fn clear(&mut self) {
        self.data_mut().fill(0);
        self.set_total_count(0);
        self.set_bit_cost(f64::MAX);
    }

    #[inline(always)]
    fn add(&mut self, val: usize) {
        self.data_mut()[val] += 1;
        let t = self.total_count() + 1;
        self.set_total_count(t);
    }

    #[inline(always)]
    fn add_vector(&mut self, p: &[u8]) {
        let mut n = p.len();
        let mut i = 0usize;
        self.set_total_count(self.total_count() + n);
        let d = self.data_mut();
        while n >= 8 {
            d[p[i] as usize] += 1;
            d[p[i + 1] as usize] += 1;
            d[p[i + 2] as usize] += 1;
            d[p[i + 3] as usize] += 1;
            d[p[i + 4] as usize] += 1;
            d[p[i + 5] as usize] += 1;
            d[p[i + 6] as usize] += 1;
            d[p[i + 7] as usize] += 1;
            i += 8;
            n -= 8;
        }
        while n > 0 {
            d[p[i] as usize] += 1;
            i += 1;
            n -= 1;
        }
    }

    #[inline(always)]
    fn add_histogram(&mut self, other: &Self) {
        let len = self.data().len();
        self.set_total_count(self.total_count() + other.total_count());
        let d = self.data_mut();
        let od = other.data();
        for i in 0..len {
            d[i] += od[i];
        }
    }
}

#[derive(Debug, Clone)]
pub struct Histogram<const N: usize> {
    pub data_: [u32; N],
    pub total_count_: usize,
    pub bit_cost_: f64,
}

impl<const N: usize> Default for Histogram<N> {
    fn default() -> Self {
        Self {
            data_: [0; N],
            total_count_: 0,
            bit_cost_: f64::MAX,
        }
    }
}

impl<const N: usize> Histogram<N> {
    pub fn clear(&mut self) {
        self.data_.fill(0);
        self.total_count_ = 0;
        self.bit_cost_ = f64::MAX;
    }
}

impl<const N: usize> HistogramTrait for Histogram<N> {
    #[inline(always)]
    fn data(&self) -> &[u32] {
        &self.data_
    }
    #[inline(always)]
    fn data_mut(&mut self) -> &mut [u32] {
        &mut self.data_
    }
    #[inline(always)]
    fn total_count(&self) -> usize {
        self.total_count_
    }
    #[inline(always)]
    fn set_total_count(&mut self, count: usize) {
        self.total_count_ = count;
    }
    #[inline(always)]
    fn bit_cost(&self) -> f64 {
        self.bit_cost_
    }
    #[inline(always)]
    fn set_bit_cost(&mut self, cost: f64) {
        self.bit_cost_ = cost;
    }
}

pub type HistogramLiteral = Histogram<BROTLI_NUM_LITERAL_SYMBOLS>;
pub type HistogramCommand = Histogram<BROTLI_NUM_COMMAND_SYMBOLS>;
pub type HistogramDistance = Histogram<BROTLI_NUM_HISTOGRAM_DISTANCE_SYMBOLS>;

pub fn clear_histograms<H: HistogramTrait>(array: &mut [H]) {
    for h in array.iter_mut() {
        h.clear();
    }
}

struct BlockSplitIterator<'a> {
    split: &'a BlockSplit,
    idx: usize,
    type_: usize,
    length: usize,
}

impl<'a> BlockSplitIterator<'a> {
    fn new(split: &'a BlockSplit) -> Self {
        let length = if split.lengths.is_empty() { 0 } else { split.lengths[0] as usize };
        Self {
            split,
            idx: 0,
            type_: 0,
            length,
        }
    }

    #[inline(always)]
    fn next(&mut self) {
        if self.length == 0 {
            self.idx += 1;
            self.type_ = self.split.types[self.idx] as usize;
            self.length = self.split.lengths[self.idx] as usize;
        }
        self.length -= 1;
    }
}

pub fn brotli_build_histograms_with_context(
    cmds: &[Command],
    literal_split: &BlockSplit,
    insert_and_copy_split: &BlockSplit,
    dist_split: &BlockSplit,
    ringbuffer: &[u8],
    start_pos: usize,
    mask: usize,
    mut prev_byte: u8,
    mut prev_byte2: u8,
    context_modes: Option<&[ContextType]>,
    literal_histograms: &mut [HistogramLiteral],
    insert_and_copy_histograms: &mut [HistogramCommand],
    copy_dist_histograms: &mut [HistogramDistance],
) {
    let mut pos = start_pos;
    let mut literal_it = BlockSplitIterator::new(literal_split);
    let mut insert_and_copy_it = BlockSplitIterator::new(insert_and_copy_split);
    let mut dist_it = BlockSplitIterator::new(dist_split);

    for cmd in cmds {
        insert_and_copy_it.next();
        insert_and_copy_histograms[insert_and_copy_it.type_].add(cmd.cmd_prefix_ as usize);
        for _ in 0..cmd.insert_len_ {
            literal_it.next();
            let mut context = literal_it.type_;
            if let Some(modes) = context_modes {
                let lut = brotli_context_lut(modes[context]);
                context = (context << BROTLI_LITERAL_CONTEXT_BITS)
                    + (brotli_context(prev_byte, prev_byte2, lut) as usize);
            }
            let byte = ringbuffer[pos & mask];
            literal_histograms[context].add(byte as usize);
            prev_byte2 = prev_byte;
            prev_byte = byte;
            pos += 1;
        }
        let copy_len = cmd.copy_len() as usize;
        pos += copy_len;
        if copy_len > 0 {
            prev_byte2 = ringbuffer[pos.wrapping_sub(2) & mask];
            prev_byte = ringbuffer[pos.wrapping_sub(1) & mask];
            if cmd.cmd_prefix_ >= 128 {
                dist_it.next();
                let context = (dist_it.type_ << BROTLI_DISTANCE_CONTEXT_BITS)
                    + (cmd.distance_context() as usize);
                copy_dist_histograms[context].add((cmd.dist_prefix_ & 0x3FF) as usize);
            }
        }
    }
}
