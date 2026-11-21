#![allow(dead_code)]

use crate::enc::fast_log::fast_log2;
use crate::enc::utf8_util::{K_MIN_UTF8_RATIO, brotli_is_mostly_utf8};

fn utf8_position(last: usize, c: usize, clamp: usize) -> usize {
    if c < 128 {
        0
    } else if c >= 192 {
        1usize.min(clamp)
    } else if last < 0xE0 {
        0
    } else {
        2usize.min(clamp)
    }
}

fn decide_multi_byte_stats_level(pos: usize, len: usize, mask: usize, data: &[u8]) -> usize {
    let mut counts = [0usize; 3];
    let mut max_utf8 = 1usize;
    let mut last_c = 0usize;
    for i in 0..len {
        let c = data[(pos + i) & mask] as usize;
        counts[utf8_position(last_c, c, 2)] += 1;
        last_c = c;
    }
    if counts[2] < 500 {
        max_utf8 = 1;
    }
    if counts[1] + counts[2] < 25 {
        max_utf8 = 0;
    }
    max_utf8
}

fn estimate_bit_costs_for_literals_utf8(
    pos: usize,
    len: usize,
    mask: usize,
    data: &[u8],
    histogram: &mut [usize],
    cost: &mut [f32],
) {
    let max_utf8 = decide_multi_byte_stats_level(pos, len, mask, data);
    let window_half = 495usize;
    let in_window = window_half.min(len);
    let mut in_window_utf8 = [0usize; 3];
    histogram[..3 * 256].fill(0);

    {
        let mut last_c = 0usize;
        let mut utf8_pos = 0usize;
        for i in 0..in_window {
            let c = data[(pos + i) & mask] as usize;
            histogram[256 * utf8_pos + c] += 1;
            in_window_utf8[utf8_pos] += 1;
            utf8_pos = utf8_position(last_c, c, max_utf8);
            last_c = c;
        }
    }

    for i in 0..len {
        if i >= window_half {
            let c = if i < window_half + 1 {
                0
            } else {
                data[(pos + i - window_half - 1) & mask] as usize
            };
            let last_c = if i < window_half + 2 {
                0
            } else {
                data[(pos + i - window_half - 2) & mask] as usize
            };
            let utf8_pos2 = utf8_position(last_c, c, max_utf8);
            histogram[256 * utf8_pos2 + (data[(pos + i - window_half) & mask] as usize)] -= 1;
            in_window_utf8[utf8_pos2] -= 1;
        }
        if i + window_half < len {
            let c = data[(pos + i + window_half - 1) & mask] as usize;
            let last_c = data[(pos + i + window_half - 2) & mask] as usize;
            let utf8_pos2 = utf8_position(last_c, c, max_utf8);
            histogram[256 * utf8_pos2 + (data[(pos + i + window_half) & mask] as usize)] += 1;
            in_window_utf8[utf8_pos2] += 1;
        }
        {
            let c = if i < 1 { 0 } else { data[(pos + i - 1) & mask] as usize };
            let last_c = if i < 2 { 0 } else { data[(pos + i - 2) & mask] as usize };
            let utf8_pos = utf8_position(last_c, c, max_utf8);
            let masked_pos = (pos + i) & mask;
            let mut histo = histogram[256 * utf8_pos + (data[masked_pos] as usize)];
            const PROLOGUE_LENGTH: usize = 2000;
            const MULTIPLIER: f64 = 0.35 / 2000.0;
            if histo == 0 {
                histo = 1;
            }
            let mut lit_cost = fast_log2(in_window_utf8[utf8_pos]) - fast_log2(histo);
            lit_cost += 0.02905;
            if lit_cost < 1.0 {
                lit_cost *= 0.5;
                lit_cost += 0.5;
            }
            if i < PROLOGUE_LENGTH {
                lit_cost += 0.35 + MULTIPLIER * (i as f64);
            }
            cost[i] = lit_cost as f32;
        }
    }
}

pub fn brotli_estimate_bit_costs_for_literals(
    pos: usize,
    len: usize,
    mask: usize,
    data: &[u8],
    histogram: &mut [usize],
    cost: &mut [f32],
) {
    if brotli_is_mostly_utf8(data, pos, mask, len, K_MIN_UTF8_RATIO) {
        estimate_bit_costs_for_literals_utf8(pos, len, mask, data, histogram, cost);
    } else {
        let window_half = 2000usize;
        let mut in_window = window_half.min(len);
        histogram[..256].fill(0);

        for i in 0..in_window {
            histogram[data[(pos + i) & mask] as usize] += 1;
        }

        for i in 0..len {
            if i >= window_half {
                histogram[data[(pos + i - window_half) & mask] as usize] -= 1;
                in_window -= 1;
            }
            if i + window_half < len {
                histogram[data[(pos + i + window_half) & mask] as usize] += 1;
                in_window += 1;
            }
            let mut histo = histogram[data[(pos + i) & mask] as usize];
            if histo == 0 {
                histo = 1;
            }
            let mut lit_cost = fast_log2(in_window) - fast_log2(histo);
            lit_cost += 0.029;
            if lit_cost < 1.0 {
                lit_cost *= 0.5;
                lit_cost += 0.5;
            }
            cost[i] = lit_cost as f32;
        }
    }
}
