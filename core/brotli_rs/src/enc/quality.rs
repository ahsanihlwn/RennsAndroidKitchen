#![allow(dead_code)]

use crate::common::constants::*;
use crate::enc::params::{BrotliEncoderParams, BrotliEncoderSimdHasher, BrotliHasherParams};

pub const FAST_ONE_PASS_COMPRESSION_QUALITY: i32 = 0;
pub const FAST_TWO_PASS_COMPRESSION_QUALITY: i32 = 1;
pub const ZOPFLIFICATION_QUALITY: i32 = 10;
pub const HQ_ZOPFLIFICATION_QUALITY: i32 = 11;

pub const MAX_QUALITY_FOR_STATIC_ENTROPY_CODES: i32 = 2;
pub const MIN_QUALITY_FOR_BLOCK_SPLIT: i32 = 4;
pub const MIN_QUALITY_FOR_NONZERO_DISTANCE_PARAMS: i32 = 4;
pub const MIN_QUALITY_FOR_OPTIMIZE_HISTOGRAMS: i32 = 4;
pub const MIN_QUALITY_FOR_EXTENSIVE_REFERENCE_SEARCH: i32 = 5;
pub const MIN_QUALITY_FOR_CONTEXT_MODELING: i32 = 5;
pub const MIN_QUALITY_FOR_HQ_CONTEXT_MODELING: i32 = 7;
pub const MIN_QUALITY_FOR_HQ_BLOCK_SPLITTING: i32 = 10;

pub const MAX_NUM_DELAYED_SYMBOLS: usize = 0x2FFF;

#[inline(always)]
pub fn max_hash_table_size(quality: i32) -> usize {
    if quality == FAST_ONE_PASS_COMPRESSION_QUALITY {
        1 << 15
    } else {
        1 << 17
    }
}

pub const MAX_ZOPFLI_LEN_QUALITY_10: usize = 150;
pub const MAX_ZOPFLI_LEN_QUALITY_11: usize = 325;

pub const BROTLI_LONG_COPY_QUICK_STEP: usize = 16384;

#[inline(always)]
pub fn max_zopfli_len(params: &BrotliEncoderParams) -> usize {
    if params.quality <= 10 {
        MAX_ZOPFLI_LEN_QUALITY_10
    } else {
        MAX_ZOPFLI_LEN_QUALITY_11
    }
}

#[inline(always)]
pub fn max_zopfli_candidates(params: &BrotliEncoderParams) -> usize {
    if params.quality <= 10 {
        1
    } else {
        5
    }
}

#[inline(always)]
pub fn sanitize_params(params: &mut BrotliEncoderParams) {
    params.quality = params.quality.clamp(BROTLI_MIN_QUALITY, BROTLI_MAX_QUALITY);
    if params.quality <= MAX_QUALITY_FOR_STATIC_ENTROPY_CODES {
        params.large_window = false;
    }
    if params.lgwin < BROTLI_MIN_WINDOW_BITS {
        params.lgwin = BROTLI_MIN_WINDOW_BITS;
    } else {
        let max_lgwin = if params.large_window {
            BROTLI_LARGE_MAX_WINDOW_BITS
        } else {
            BROTLI_MAX_WINDOW_BITS
        };
        if params.lgwin > max_lgwin {
            params.lgwin = max_lgwin;
        }
    }
}

#[inline(always)]
pub fn compute_lg_block(params: &BrotliEncoderParams) -> i32 {
    let mut lgblock = params.lgblock;
    if params.quality == FAST_ONE_PASS_COMPRESSION_QUALITY
        || params.quality == FAST_TWO_PASS_COMPRESSION_QUALITY
    {
        lgblock = params.lgwin;
    } else if params.quality < MIN_QUALITY_FOR_BLOCK_SPLIT {
        lgblock = 14;
    } else if lgblock == 0 {
        lgblock = 16;
        if params.quality >= 9 && params.lgwin > lgblock {
            lgblock = 18.min(params.lgwin);
        }
    } else {
        lgblock = lgblock.clamp(BROTLI_MIN_INPUT_BLOCK_BITS, BROTLI_MAX_INPUT_BLOCK_BITS);
    }
    lgblock
}

#[inline(always)]
pub fn compute_rb_bits(params: &BrotliEncoderParams) -> i32 {
    1 + params.lgwin.max(params.lgblock)
}

#[inline(always)]
pub fn max_metablock_size(params: &BrotliEncoderParams) -> usize {
    let bits = compute_rb_bits(params).min(BROTLI_MAX_INPUT_BLOCK_BITS);
    1usize << bits
}

#[inline(always)]
pub fn literal_spree_length_for_sparse_search(params: &BrotliEncoderParams) -> usize {
    if params.quality < 9 { 64 } else { 512 }
}

#[cfg(target_arch = "x86_64")]
const BROTLI_MAX_RECOMMENDED_SIMD_QUALITY: i32 = 7;
#[cfg(not(target_arch = "x86_64"))]
const BROTLI_MAX_RECOMMENDED_SIMD_QUALITY: i32 = 6;

const BROTLI_MAX_SIMD_QUALITY: i32 = 7;

#[inline(always)]
fn should_use_simd_hasher(params: &BrotliEncoderParams) -> bool {
    if params.simd_hasher == BrotliEncoderSimdHasher::Disable {
        return false;
    }
    let mut max_quality = BROTLI_MAX_RECOMMENDED_SIMD_QUALITY;
    if params.simd_hasher == BrotliEncoderSimdHasher::Enable {
        max_quality = BROTLI_MAX_SIMD_QUALITY;
    }
    params.quality <= max_quality
}

#[inline(always)]
pub fn choose_hasher(params: &BrotliEncoderParams, hparams: &mut BrotliHasherParams) {
    if params.quality > 9 {
        hparams.type_ = 10;
    } else if params.quality == 4 && params.size_hint >= (1 << 20) {
        hparams.type_ = 54;
    } else if params.quality < 5 {
        hparams.type_ = params.quality;
    } else if params.lgwin <= 16 {
        hparams.type_ = if params.quality < 7 {
            40
        } else if params.quality < 9 {
            41
        } else {
            42
        };
    } else if params.size_hint >= (1 << 20) && params.lgwin >= 19 {
        hparams.type_ = if should_use_simd_hasher(params) { 68 } else { 6 };
        hparams.block_bits = params.quality - 1;
        hparams.bucket_bits = 15;
        hparams.num_last_distances_to_check = if params.quality < 7 {
            4
        } else if params.quality < 9 {
            10
        } else {
            16
        };
    } else {
        hparams.type_ = if should_use_simd_hasher(params) {
            if params.hasher_opt { 59 } else { 58 }
        } else {
            5
        };
        hparams.block_bits = params.quality - 1;
        hparams.bucket_bits = if params.quality < 7 { 14 } else { 15 };
        hparams.num_last_distances_to_check = if params.quality < 7 {
            4
        } else if params.quality < 9 {
            10
        } else {
            16
        };
    }

    if params.lgwin > 24 {
        if hparams.type_ == 3 {
            hparams.type_ = 35;
        }
        if hparams.type_ == 54 {
            hparams.type_ = 55;
        }
        if hparams.type_ == 6 || hparams.type_ == 68 {
            hparams.type_ = 65;
        }
    }
}
