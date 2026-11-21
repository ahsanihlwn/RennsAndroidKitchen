#![allow(dead_code)]

use crate::common::constants::BROTLI_MAX_QUALITY;
use crate::common::dictionary::{BrotliDictionary, brotli_get_dictionary};
use crate::common::shared_dictionary::{
    SHARED_BROTLI_NUM_DICTIONARY_CONTEXTS,
};
use crate::common::transform::brotli_get_transforms;
use crate::enc::compound_dictionary::{CompoundDictionary, K_SHARED_DICTIONARY_MAGIC};
use crate::enc::dictionary_hash::{
    K_STATIC_DICTIONARY_HASH_LENGTHS, K_STATIC_DICTIONARY_HASH_WORDS,
};
use crate::enc::static_dict_lut::{
    DictWord, K_STATIC_DICTIONARY_BUCKETS, K_STATIC_DICTIONARY_WORDS,
};

pub const K_CUTOFF_TRANSFORMS_COUNT: u32 = 10;
pub const K_CUTOFF_TRANSFORMS: u64 = (0x071B520Au64 << 32) | 0xDA2D3200u64;

#[derive(Debug, Clone)]
pub struct BrotliEncoderDictionary {
    pub words: &'static BrotliDictionary,
    pub num_transforms: u32,
    pub cutoff_transforms_count: u32,
    pub cutoff_transforms: u64,
    pub hash_table_words: &'static [u16],
    pub hash_table_lengths: &'static [u8],
    pub buckets: &'static [u16],
    pub dict_words: &'static [DictWord],
}

impl Default for BrotliEncoderDictionary {
    fn default() -> Self {
        Self::new()
    }
}

impl BrotliEncoderDictionary {
    pub fn new() -> Self {
        Self {
            words: brotli_get_dictionary(),
            num_transforms: brotli_get_transforms().num_transforms as u32,
            cutoff_transforms_count: K_CUTOFF_TRANSFORMS_COUNT,
            cutoff_transforms: K_CUTOFF_TRANSFORMS,
            hash_table_words: &K_STATIC_DICTIONARY_HASH_WORDS,
            hash_table_lengths: &K_STATIC_DICTIONARY_HASH_LENGTHS,
            buckets: &K_STATIC_DICTIONARY_BUCKETS,
            dict_words: &K_STATIC_DICTIONARY_WORDS,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ContextualEncoderDictionary {
    pub context_based: bool,
    pub num_dictionaries: u8,
    pub context_map: [u8; SHARED_BROTLI_NUM_DICTIONARY_CONTEXTS],
    pub instance: BrotliEncoderDictionary,
}

impl Default for ContextualEncoderDictionary {
    fn default() -> Self {
        Self {
            context_based: false,
            num_dictionaries: 1,
            context_map: [0; SHARED_BROTLI_NUM_DICTIONARY_CONTEXTS],
            instance: BrotliEncoderDictionary::new(),
        }
    }
}

impl ContextualEncoderDictionary {
    #[inline(always)]
    pub fn dict_for(&self, _dict_id: usize) -> &BrotliEncoderDictionary {
        &self.instance
    }
}

#[derive(Debug, Clone)]
pub struct SharedEncoderDictionary {
    pub magic: u32,
    pub compound: CompoundDictionary,
    pub contextual: ContextualEncoderDictionary,
    pub max_quality: i32,
}

impl Default for SharedEncoderDictionary {
    fn default() -> Self {
        Self::new()
    }
}

impl SharedEncoderDictionary {
    pub fn new() -> Self {
        Self {
            magic: K_SHARED_DICTIONARY_MAGIC,
            compound: CompoundDictionary::new(),
            contextual: ContextualEncoderDictionary::default(),
            max_quality: BROTLI_MAX_QUALITY,
        }
    }
}
