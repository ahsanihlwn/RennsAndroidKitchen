#![allow(dead_code)]

use std::sync::Arc;

use crate::common::shared_dictionary::{
    SHARED_BROTLI_MAX_COMPOUND_DICTS, SHARED_BROTLI_MAX_RAW_DICT_SIZE,
};

pub const K_PREPARED_DICTIONARY_MAGIC: u32 = 0xDEBCEDE0;
pub const K_SHARED_DICTIONARY_MAGIC: u32 = 0xDEBCEDE1;
pub const K_MANAGED_DICTIONARY_MAGIC: u32 = 0xDEBCEDE2;
pub const K_LEAN_PREPARED_DICTIONARY_MAGIC: u32 = 0xDEBCEDE3;

pub const K_PREPARED_DICTIONARY_HASH_MUL64_LONG: u64 = (0x1FE35A7Bu64 << 32) | 0xD3579BD3u64;

#[derive(Debug, Clone)]
pub struct PreparedDictionary {
    pub magic: u32,
    pub num_items: u32,
    pub source_size: u32,
    pub hash_bits: u32,
    pub bucket_bits: u32,
    pub slot_bits: u32,
    pub slot_offsets: Vec<u32>,
    pub heads: Vec<u16>,
    pub items: Vec<u32>,
    pub source: Arc<[u8]>,
}

impl PreparedDictionary {
    pub fn view(&self) -> PreparedDictionaryView {
        PreparedDictionaryView {
            hash_shift: 64u32 - self.bucket_bits,
            slot_mask: u32::MAX >> (32 - self.slot_bits),
            hash_mask: u64::MAX >> (64 - self.hash_bits),
            source_size: self.source_size,
        }
    }
}

#[derive(Debug, Clone, Copy, Default)]
pub struct PreparedDictionaryView {
    pub source_size: u32,
    pub hash_shift: u32,
    pub slot_mask: u32,
    pub hash_mask: u64,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct PreparedDictionaryProbe {
    pub slot: u32,
    pub cursor: u32,
}

#[derive(Debug, Clone)]
pub struct CompoundDictionary {
    pub num_chunks: usize,
    pub total_size: usize,
    pub chunks: Vec<Arc<PreparedDictionary>>,
    pub chunk_views: Vec<PreparedDictionaryView>,
    pub chunk_offsets: [usize; SHARED_BROTLI_MAX_COMPOUND_DICTS + 1],
}

impl Default for CompoundDictionary {
    fn default() -> Self {
        Self::new()
    }
}

impl CompoundDictionary {
    pub fn new() -> Self {
        Self {
            num_chunks: 0,
            total_size: 0,
            chunks: Vec::new(),
            chunk_views: Vec::new(),
            chunk_offsets: [0; SHARED_BROTLI_MAX_COMPOUND_DICTS + 1],
        }
    }

    pub fn attach(&mut self, dictionary: Arc<PreparedDictionary>) -> bool {
        if self.num_chunks == SHARED_BROTLI_MAX_COMPOUND_DICTS {
            return false;
        }
        let length = dictionary.source_size as usize;
        if length > SHARED_BROTLI_MAX_RAW_DICT_SIZE - self.total_size {
            return false;
        }
        let index = self.num_chunks;
        self.total_size += length;
        self.chunk_offsets[index + 1] = self.total_size;
        self.chunk_views.push(dictionary.view());
        self.chunks.push(dictionary);
        self.num_chunks += 1;
        true
    }
}

fn create_prepared_dictionary_with_params(
    source: &[u8],
    bucket_bits: u32,
    slot_bits: u32,
    hash_bits: u32,
    bucket_limit: u16,
) -> Option<PreparedDictionary> {
    if slot_bits > 16 || slot_bits > bucket_bits || bucket_bits - slot_bits >= 16 {
        return None;
    }
    let source_size = source.len();
    let num_slots = 1usize << slot_bits;
    let num_buckets = 1usize << bucket_bits;
    let hash_shift = 64u32 - bucket_bits;
    let hash_mask = u64::MAX >> (64 - hash_bits);
    let slot_mask = (num_slots as u32) - 1;

    let mut slot_size = vec![0u32; num_slots];
    let mut slot_limit = vec![0u32; num_slots];
    let mut num = vec![0u16; num_buckets];
    let mut bucket_heads = vec![0u32; num_buckets];
    let mut next_bucket = vec![0u32; source_size];

    let mut i = 0usize;
    while i + 7 < source_size {
        let word = u64::from_le_bytes(source[i..i + 8].try_into().unwrap());
        let h = (word & hash_mask).wrapping_mul(K_PREPARED_DICTIONARY_HASH_MUL64_LONG);
        let key = (h >> hash_shift) as usize;
        let mut count = num[key];
        next_bucket[i] = if count == 0 { u32::MAX } else { bucket_heads[key] };
        bucket_heads[key] = i as u32;
        count += 1;
        if count > bucket_limit {
            count = bucket_limit;
        }
        num[key] = count;
        i += 1;
    }

    let mut total_items = 0u32;
    for s in 0..num_slots {
        slot_limit[s] = bucket_limit as u32;
        loop {
            let limit = slot_limit[s];
            let mut count = 0u32;
            let mut overflow = false;
            let mut j = s;
            while j < num_buckets {
                let mut size = num[j] as u32;
                if count >= 0xFFFF {
                    overflow = true;
                    break;
                }
                if size > limit {
                    size = limit;
                }
                count += size;
                j += num_slots;
            }
            if !overflow {
                slot_size[s] = count;
                total_items += count;
                break;
            }
            slot_limit[s] -= 1;
        }
    }

    let mut slot_offsets = vec![0u32; num_slots];
    let mut heads = vec![0u16; num_buckets];
    let mut items = vec![0u32; total_items as usize];

    let mut acc = 0u32;
    for s in 0..num_slots {
        slot_offsets[s] = acc;
        acc += slot_size[s];
        slot_size[s] = 0;
    }

    for b in 0..num_buckets {
        let slot = (b as u32 & slot_mask) as usize;
        let mut count = num[b] as u32;
        if count > slot_limit[slot] {
            count = slot_limit[slot];
        }
        if count == 0 {
            heads[b] = 0xFFFF;
            continue;
        }
        let cursor_rel = slot_size[slot];
        heads[b] = cursor_rel as u16;
        let mut cursor = (cursor_rel + slot_offsets[slot]) as usize;
        slot_size[slot] += count;
        let mut pos = bucket_heads[b];
        for _ in 0..count {
            items[cursor] = pos;
            cursor += 1;
            pos = next_bucket[pos as usize];
        }
        items[cursor - 1] |= 0x80000000;
    }

    Some(PreparedDictionary {
        magic: K_PREPARED_DICTIONARY_MAGIC,
        num_items: total_items,
        source_size: source_size as u32,
        hash_bits,
        bucket_bits,
        slot_bits,
        slot_offsets,
        heads,
        items,
        source: Arc::from(source),
    })
}

pub fn create_prepared_dictionary(source: &[u8]) -> Option<PreparedDictionary> {
    let mut bucket_bits = 17u32;
    let mut slot_bits = 7u32;
    let hash_bits = 40u32;
    let bucket_limit = 32u16;
    let mut volume = 16usize << bucket_bits;
    if source.len() > SHARED_BROTLI_MAX_RAW_DICT_SIZE {
        return None;
    }
    while volume < source.len() && bucket_bits < 22 {
        bucket_bits += 1;
        slot_bits += 1;
        volume <<= 1;
    }
    create_prepared_dictionary_with_params(source, bucket_bits, slot_bits, hash_bits, bucket_limit)
}
