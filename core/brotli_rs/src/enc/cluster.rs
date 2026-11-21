#![allow(dead_code)]

use crate::enc::bit_cost::brotli_population_cost;
use crate::enc::fast_log::fast_log2;
use crate::enc::histogram::HistogramTrait;

#[derive(Debug, Clone, Copy, Default)]
pub struct HistogramPair {
    pub idx1: u32,
    pub idx2: u32,
    pub cost_combo: f64,
    pub cost_diff: f64,
}

#[inline(always)]
fn histogram_pair_is_less(p1: &HistogramPair, p2: &HistogramPair) -> bool {
    if p1.cost_diff != p2.cost_diff {
        p1.cost_diff > p2.cost_diff
    } else {
        (p1.idx2 - p1.idx1) > (p2.idx2 - p2.idx1)
    }
}

#[inline(always)]
fn cluster_cost_diff(size_a: usize, size_b: usize) -> f64 {
    let size_c = size_a + size_b;
    (size_a as f64) * fast_log2(size_a) + (size_b as f64) * fast_log2(size_b)
        - (size_c as f64) * fast_log2(size_c)
}

pub fn brotli_compare_and_push_to_queue<H: HistogramTrait>(
    out: &[H],
    tmp: &mut H,
    cluster_size: &[u32],
    mut idx1: u32,
    mut idx2: u32,
    max_num_pairs: usize,
    pairs: &mut [HistogramPair],
    num_pairs: &mut usize,
) {
    if idx1 == idx2 {
        return;
    }
    if idx2 < idx1 {
        std::mem::swap(&mut idx1, &mut idx2);
    }
    let mut is_good_pair = false;
    let mut p = HistogramPair {
        idx1,
        idx2,
        cost_combo: 0.0,
        cost_diff: 0.5 * cluster_cost_diff(cluster_size[idx1 as usize] as usize, cluster_size[idx2 as usize] as usize)
            - out[idx1 as usize].bit_cost()
            - out[idx2 as usize].bit_cost(),
    };
    if out[idx1 as usize].total_count() == 0 {
        p.cost_combo = out[idx2 as usize].bit_cost();
        is_good_pair = true;
    } else if out[idx2 as usize].total_count() == 0 {
        p.cost_combo = out[idx1 as usize].bit_cost();
        is_good_pair = true;
    } else {
        let threshold = if *num_pairs == 0 {
            1e99
        } else {
            pairs[0].cost_diff.max(0.0)
        };
        *tmp = out[idx1 as usize].clone();
        tmp.add_histogram(&out[idx2 as usize]);
        let cost_combo = brotli_population_cost(tmp);
        if cost_combo < threshold - p.cost_diff {
            p.cost_combo = cost_combo;
            is_good_pair = true;
        }
    }
    if is_good_pair {
        p.cost_diff += p.cost_combo;
        if *num_pairs > 0 && histogram_pair_is_less(&pairs[0], &p) {
            if *num_pairs < max_num_pairs {
                pairs[*num_pairs] = pairs[0];
                *num_pairs += 1;
            }
            pairs[0] = p;
        } else if *num_pairs < max_num_pairs {
            pairs[*num_pairs] = p;
            *num_pairs += 1;
        }
    }
}

pub fn brotli_histogram_combine<H: HistogramTrait>(
    out: &mut [H],
    tmp: &mut H,
    cluster_size: &mut [u32],
    symbols: &mut [u32],
    clusters: &mut [u32],
    pairs: &mut [HistogramPair],
    mut num_clusters: usize,
    symbols_size: usize,
    max_clusters: usize,
    max_num_pairs: usize,
) -> usize {
    let mut cost_diff_threshold = 0.0f64;
    let mut min_cluster_size = 1usize;
    let mut num_pairs = 0usize;

    for idx1 in 0..num_clusters {
        for idx2 in (idx1 + 1)..num_clusters {
            brotli_compare_and_push_to_queue(
                out,
                tmp,
                cluster_size,
                clusters[idx1],
                clusters[idx2],
                max_num_pairs,
                pairs,
                &mut num_pairs,
            );
        }
    }

    while num_clusters > min_cluster_size {
        if pairs[0].cost_diff >= cost_diff_threshold {
            cost_diff_threshold = 1e99;
            min_cluster_size = max_clusters;
            continue;
        }
        let best_idx1 = pairs[0].idx1;
        let best_idx2 = pairs[0].idx2;
        let rhs = out[best_idx2 as usize].clone();
        out[best_idx1 as usize].add_histogram(&rhs);
        out[best_idx1 as usize].set_bit_cost(pairs[0].cost_combo);
        cluster_size[best_idx1 as usize] += cluster_size[best_idx2 as usize];
        for s in &mut symbols[..symbols_size] {
            if *s == best_idx2 {
                *s = best_idx1;
            }
        }
        for i in 0..num_clusters {
            if clusters[i] == best_idx2 {
                clusters.copy_within((i + 1)..num_clusters, i);
                break;
            }
        }
        num_clusters -= 1;

        let mut copy_to_idx = 0usize;
        for i in 0..num_pairs {
            let p = pairs[i];
            if p.idx1 == best_idx1
                || p.idx2 == best_idx1
                || p.idx1 == best_idx2
                || p.idx2 == best_idx2
            {
                continue;
            }
            if histogram_pair_is_less(&pairs[0], &p) {
                let front = pairs[0];
                pairs[0] = p;
                pairs[copy_to_idx] = front;
            } else {
                pairs[copy_to_idx] = p;
            }
            copy_to_idx += 1;
        }
        num_pairs = copy_to_idx;

        for i in 0..num_clusters {
            brotli_compare_and_push_to_queue(
                out,
                tmp,
                cluster_size,
                best_idx1,
                clusters[i],
                max_num_pairs,
                pairs,
                &mut num_pairs,
            );
        }
    }
    num_clusters
}

#[inline(always)]
pub fn brotli_histogram_bit_cost_distance<H: HistogramTrait>(
    histogram: &H,
    candidate: &H,
    tmp: &mut H,
) -> f64 {
    if histogram.total_count() == 0 {
        0.0
    } else {
        *tmp = histogram.clone();
        tmp.add_histogram(candidate);
        brotli_population_cost(tmp) - candidate.bit_cost()
    }
}

pub fn brotli_histogram_remap<H: HistogramTrait>(
    in_histos: &[H],
    in_size: usize,
    clusters: &[u32],
    num_clusters: usize,
    out: &mut [H],
    tmp: &mut H,
    symbols: &mut [u32],
) {
    for i in 0..in_size {
        let mut best_out = if i == 0 { symbols[0] } else { symbols[i - 1] };
        let mut best_bits =
            brotli_histogram_bit_cost_distance(&in_histos[i], &out[best_out as usize], tmp);
        for j in 0..num_clusters {
            let cur_bits =
                brotli_histogram_bit_cost_distance(&in_histos[i], &out[clusters[j] as usize], tmp);
            if cur_bits < best_bits {
                best_bits = cur_bits;
                best_out = clusters[j];
            }
        }
        symbols[i] = best_out;
    }

    for &c in &clusters[..num_clusters] {
        out[c as usize].clear();
    }
    for i in 0..in_size {
        out[symbols[i] as usize].add_histogram(&in_histos[i]);
    }
}

pub fn brotli_histogram_reindex<H: HistogramTrait>(
    out: &mut [H],
    symbols: &mut [u32],
    length: usize,
) -> usize {
    const K_INVALID_INDEX: u32 = u32::MAX;
    let mut new_index = vec![K_INVALID_INDEX; length];
    let mut next_index = 0u32;
    for &s in &symbols[..length] {
        if new_index[s as usize] == K_INVALID_INDEX {
            new_index[s as usize] = next_index;
            next_index += 1;
        }
    }
    let mut tmp = vec![H::default(); next_index as usize];
    let mut next_idx = 0u32;
    for s in &mut symbols[..length] {
        if new_index[*s as usize] == next_idx {
            tmp[next_idx as usize] = out[*s as usize].clone();
            next_idx += 1;
        }
        *s = new_index[*s as usize];
    }
    for i in 0..(next_idx as usize) {
        out[i] = tmp[i].clone();
    }
    next_idx as usize
}

pub fn brotli_cluster_histograms<H: HistogramTrait>(
    in_histos: &[H],
    max_histograms: usize,
    out: &mut Vec<H>,
    out_size: &mut usize,
    histogram_symbols: &mut [u32],
) {
    let in_size = in_histos.len();
    out.resize(in_size, H::default());
    let mut cluster_size = vec![1u32; in_size];
    let mut clusters = vec![0u32; in_size];
    let mut num_clusters = 0usize;
    const MAX_INPUT_HISTOGRAMS: usize = 64;
    let mut pairs_capacity = MAX_INPUT_HISTOGRAMS * MAX_INPUT_HISTOGRAMS / 2;
    let mut pairs = vec![HistogramPair::default(); pairs_capacity + 1];
    let mut tmp = H::default();

    for i in 0..in_size {
        out[i] = in_histos[i].clone();
        out[i].set_bit_cost(brotli_population_cost(&in_histos[i]));
        histogram_symbols[i] = i as u32;
    }

    let mut i = 0usize;
    while i < in_size {
        let num_to_combine = (in_size - i).min(MAX_INPUT_HISTOGRAMS);
        for j in 0..num_to_combine {
            clusters[num_clusters + j] = (i + j) as u32;
        }
        let num_new_clusters = brotli_histogram_combine(
            out,
            &mut tmp,
            &mut cluster_size,
            &mut histogram_symbols[i..],
            &mut clusters[num_clusters..],
            &mut pairs,
            num_to_combine,
            num_to_combine,
            max_histograms,
            pairs_capacity,
        );
        num_clusters += num_new_clusters;
        i += MAX_INPUT_HISTOGRAMS;
    }

    let max_num_pairs = (64 * num_clusters).min((num_clusters / 2) * num_clusters);
    if pairs_capacity < max_num_pairs + 1 {
        pairs_capacity = max_num_pairs + 1;
        pairs.resize(pairs_capacity, HistogramPair::default());
    }
    num_clusters = brotli_histogram_combine(
        out,
        &mut tmp,
        &mut cluster_size,
        histogram_symbols,
        &mut clusters,
        &mut pairs,
        num_clusters,
        in_size,
        max_histograms,
        max_num_pairs,
    );

    brotli_histogram_remap(
        in_histos,
        in_size,
        &clusters,
        num_clusters,
        out,
        &mut tmp,
        histogram_symbols,
    );
    *out_size = brotli_histogram_reindex(out, histogram_symbols, in_size);
    out.truncate(*out_size);
}
