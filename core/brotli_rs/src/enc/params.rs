#![allow(dead_code)]

use crate::common::constants::*;
use crate::enc::encoder_dict::SharedEncoderDictionary;

pub const BROTLI_DEFAULT_QUALITY: i32 = 11;
pub const BROTLI_DEFAULT_WINDOW: i32 = 22;
pub const BROTLI_DEFAULT_MODE: BrotliEncoderMode = BrotliEncoderMode::Generic;

pub const BROTLI_DEFAULT_BASE64_MODE: BrotliEncoderBase64Mode = BrotliEncoderBase64Mode::Disabled;
pub const BROTLI_DEFAULT_MAX_BASE64_REGIONS: usize = 16;
pub const BROTLI_DEFAULT_MIN_BASE64_REGION_LEN: usize = 2048;
pub const BROTLI_DEFAULT_SIMD_HASHER: BrotliEncoderSimdHasher = BrotliEncoderSimdHasher::Default;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum BrotliEncoderMode {
    Generic = 0,
    Text = 1,
    Font = 2,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum BrotliEncoderBase64Mode {
    Disabled = 0,
    Detection = 1,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum BrotliEncoderSimdHasher {
    Default = 0,
    Enable = 1,
    Disable = 2,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum BrotliEncoderOperation {
    Process = 0,
    Flush = 1,
    Finish = 2,
    EmitMetadata = 3,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum BrotliEncoderParameter {
    Mode = 0,
    Quality = 1,
    Lgwin = 2,
    Lgblock = 3,
    DisableLiteralContextModeling = 4,
    SizeHint = 5,
    LargeWindow = 6,
    Npostfix = 7,
    Ndirect = 8,
    StreamOffset = 9,
    Base64Mode = 10,
    MaxBase64Regions = 11,
    SimdHasher = 12,
    HasherOpt = 13,
    MinBase64RegionLen = 14,
}

#[derive(Debug, Clone, Copy)]
pub struct BrotliHasherParams {
    pub type_: i32,
    pub bucket_bits: i32,
    pub block_bits: i32,
    pub num_last_distances_to_check: i32,
}

impl Default for BrotliHasherParams {
    fn default() -> Self {
        Self {
            type_: 0,
            bucket_bits: 0,
            block_bits: 0,
            num_last_distances_to_check: 0,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct BrotliDistanceParams {
    pub distance_postfix_bits: u32,
    pub num_direct_distance_codes: u32,
    pub alphabet_size_max: u32,
    pub alphabet_size_limit: u32,
    pub max_distance: usize,
}

impl Default for BrotliDistanceParams {
    fn default() -> Self {
        Self {
            distance_postfix_bits: 0,
            num_direct_distance_codes: 0,
            alphabet_size_max: brotli_distance_alphabet_size(0, 0, BROTLI_MAX_DISTANCE_BITS) as u32,
            alphabet_size_limit: brotli_distance_alphabet_size(0, 0, BROTLI_MAX_DISTANCE_BITS) as u32,
            max_distance: BROTLI_MAX_DISTANCE,
        }
    }
}

#[derive(Debug, Clone)]
pub struct BrotliEncoderParams {
    pub mode: BrotliEncoderMode,
    pub quality: i32,
    pub lgwin: i32,
    pub lgblock: i32,
    pub stream_offset: usize,
    pub size_hint: usize,
    pub disable_literal_context_modeling: bool,
    pub large_window: bool,
    pub hasher: BrotliHasherParams,
    pub dist: BrotliDistanceParams,
    pub dictionary: SharedEncoderDictionary,
    pub base64_mode: i32,
    pub max_base64_regions: usize,
    pub min_base64_region_len: usize,
    pub simd_hasher: BrotliEncoderSimdHasher,
    pub hasher_opt: bool,
}

impl Default for BrotliEncoderParams {
    fn default() -> Self {
        Self {
            mode: BROTLI_DEFAULT_MODE,
            quality: BROTLI_DEFAULT_QUALITY,
            lgwin: BROTLI_DEFAULT_WINDOW,
            lgblock: 0,
            stream_offset: 0,
            size_hint: 0,
            disable_literal_context_modeling: false,
            large_window: false,
            hasher: BrotliHasherParams::default(),
            dist: BrotliDistanceParams::default(),
            dictionary: SharedEncoderDictionary::default(),
            base64_mode: BROTLI_DEFAULT_BASE64_MODE as i32,
            max_base64_regions: BROTLI_DEFAULT_MAX_BASE64_REGIONS,
            min_base64_region_len: BROTLI_DEFAULT_MIN_BASE64_REGION_LEN,
            simd_hasher: BROTLI_DEFAULT_SIMD_HASHER,
            hasher_opt: false,
        }
    }
}
