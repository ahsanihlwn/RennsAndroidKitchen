#![allow(dead_code)]

use crate::common::constants::*;
use crate::enc::fast_log::fast_log2;
use crate::enc::histogram::HistogramTrait;

pub fn brotli_bits_entropy(population: &[u32]) -> f64 {
    let mut sum = 0usize;
    let mut retval = 0.0f64;
    let mut i = 0usize;
    let size = population.len();
    if (size & 1) != 0 {
        let p = population[0] as usize;
        sum += p;
        retval -= (p as f64) * fast_log2(p);
        i = 1;
    }
    while i < size {
        let p0 = population[i] as usize;
        sum += p0;
        retval -= (p0 as f64) * fast_log2(p0);
        let p1 = population[i + 1] as usize;
        sum += p1;
        retval -= (p1 as f64) * fast_log2(p1);
        i += 2;
    }
    if sum > 0 {
        retval += (sum as f64) * fast_log2(sum);
    }
    if retval < (sum as f64) {
        retval = sum as f64;
    }
    retval
}

pub fn brotli_population_cost<H: HistogramTrait>(histogram: &H) -> f64 {
    const K_ONE_SYMBOL_HISTOGRAM_COST: f64 = 12.0;
    const K_TWO_SYMBOL_HISTOGRAM_COST: f64 = 20.0;
    const K_THREE_SYMBOL_HISTOGRAM_COST: f64 = 28.0;
    const K_FOUR_SYMBOL_HISTOGRAM_COST: f64 = 37.0;

    let data = histogram.data();
    let data_size = data.len();
    let total_count = histogram.total_count();
    if total_count == 0 {
        return K_ONE_SYMBOL_HISTOGRAM_COST;
    }

    let mut count = 0usize;
    let mut s = [0usize; 5];
    for (i, &v) in data.iter().enumerate() {
        if v > 0 {
            s[count] = i;
            count += 1;
            if count > 4 {
                break;
            }
        }
    }

    if count == 1 {
        return K_ONE_SYMBOL_HISTOGRAM_COST;
    }
    if count == 2 {
        return K_TWO_SYMBOL_HISTOGRAM_COST + (total_count as f64);
    }
    if count == 3 {
        let h0 = data[s[0]];
        let h1 = data[s[1]];
        let h2 = data[s[2]];
        let histomax = h0.max(h1.max(h2));
        return K_THREE_SYMBOL_HISTOGRAM_COST + ((2 * (h0 + h1 + h2) - histomax) as f64);
    }
    if count == 4 {
        let mut histo = [data[s[0]], data[s[1]], data[s[2]], data[s[3]]];
        for i in 0..4 {
            for j in (i + 1)..4 {
                if histo[j] > histo[i] {
                    histo.swap(j, i);
                }
            }
        }
        let h23 = histo[2] + histo[3];
        let histomax = h23.max(histo[0]);
        return K_FOUR_SYMBOL_HISTOGRAM_COST
            + ((3 * h23 + 2 * (histo[0] + histo[1]) - histomax) as f64);
    }

    let mut bits = 0.0f64;
    let mut max_depth = 1usize;
    let mut depth_histo = [0u32; BROTLI_CODE_LENGTH_CODES];
    let log2total = fast_log2(total_count);
    let mut i = 0usize;
    while i < data_size {
        if data[i] > 0 {
            let log2p = log2total - fast_log2(data[i] as usize);
            let mut depth = (log2p + 0.5) as usize;
            bits += (data[i] as f64) * log2p;
            if depth > 15 {
                depth = 15;
            }
            if depth > max_depth {
                max_depth = depth;
            }
            depth_histo[depth] += 1;
            i += 1;
        } else {
            let mut reps = 1u32;
            let mut k = i + 1;
            while k < data_size && data[k] == 0 {
                reps += 1;
                k += 1;
            }
            i += reps as usize;
            if i == data_size {
                break;
            }
            if reps < 3 {
                depth_histo[0] += reps;
            } else {
                reps -= 2;
                while reps > 0 {
                    depth_histo[BROTLI_REPEAT_ZERO_CODE_LENGTH as usize] += 1;
                    bits += 3.0;
                    reps >>= 3;
                }
            }
        }
    }
    bits += (18 + 2 * max_depth) as f64;
    bits += brotli_bits_entropy(&depth_histo);
    bits
}
