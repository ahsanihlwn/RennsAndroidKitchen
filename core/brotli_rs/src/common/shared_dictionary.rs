//! Shared and compound dictionary definitions and utilities.

use std::borrow::Cow;
use std::sync::Arc;

use super::dictionary::{brotli_get_dictionary, BrotliDictionary};
use super::transform::{
    brotli_get_transforms, BrotliTransforms, BROTLI_NUM_TRANSFORM_TYPES,
    BROTLI_TRANSFORMS_MAX_CUT_OFF,
};

pub const SHARED_BROTLI_MIN_DICTIONARY_WORD_LENGTH: usize = 4;
pub const SHARED_BROTLI_MAX_DICTIONARY_WORD_LENGTH: usize = 31;
pub const SHARED_BROTLI_NUM_DICTIONARY_CONTEXTS: usize = 64;
pub const SHARED_BROTLI_MAX_COMPOUND_DICTS: usize = 15;
pub const SHARED_BROTLI_MAX_RAW_DICT_SIZE: usize = 1usize << (23 + std::mem::size_of::<usize>());

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum BrotliSharedDictionaryType {
    Raw = 0,
    Serialized = 1,
}

#[derive(Debug, Clone)]
pub enum DictRef {
    Builtin,
    Custom(usize),
}

#[derive(Debug, Clone)]
pub struct BrotliSharedDictionary {
    pub prefixes: Vec<Arc<[u8]>>,
    pub context_based: bool,
    pub context_map: [u8; SHARED_BROTLI_NUM_DICTIONARY_CONTEXTS],
    pub num_dictionaries: u8,
    pub words_refs: Vec<DictRef>,
    pub transforms_refs: Vec<DictRef>,
    pub words_instances: Vec<BrotliDictionary>,
    pub transforms_instances: Vec<BrotliTransforms>,
}

impl Default for BrotliSharedDictionary {
    fn default() -> Self {
        Self::new()
    }
}

impl BrotliSharedDictionary {
    pub fn new() -> Self {
        Self {
            prefixes: Vec::new(),
            context_based: false,
            context_map: [0u8; SHARED_BROTLI_NUM_DICTIONARY_CONTEXTS],
            num_dictionaries: 1,
            words_refs: vec![DictRef::Builtin],
            transforms_refs: vec![DictRef::Builtin],
            words_instances: Vec::new(),
            transforms_instances: Vec::new(),
        }
    }

    #[inline]
    pub fn words(&self, dict_id: usize) -> &BrotliDictionary {
        match self.words_refs[dict_id] {
            DictRef::Builtin => brotli_get_dictionary(),
            DictRef::Custom(idx) => &self.words_instances[idx],
        }
    }

    #[inline]
    pub fn transforms(&self, dict_id: usize) -> &BrotliTransforms {
        match self.transforms_refs[dict_id] {
            DictRef::Builtin => brotli_get_transforms(),
            DictRef::Custom(idx) => &self.transforms_instances[idx],
        }
    }

    pub fn attach(&mut self, dict_type: BrotliSharedDictionaryType, data: &[u8]) -> bool {
        match dict_type {
            BrotliSharedDictionaryType::Raw => {
                if self.prefixes.len() >= SHARED_BROTLI_MAX_COMPOUND_DICTS {
                    return false;
                }
                self.prefixes.push(Arc::from(data));
                true
            }
            BrotliSharedDictionaryType::Serialized => decode_shared_dictionary(data, self),
        }
    }
}

const BROTLI_NUM_ENCODED_LENGTHS: usize =
    SHARED_BROTLI_MAX_DICTIONARY_WORD_LENGTH - SHARED_BROTLI_MIN_DICTIONARY_WORD_LENGTH + 1;
const BROTLI_MAX_SIZE_BITS: u8 = 15;

fn read_bool(encoded: &[u8], pos: &mut usize) -> Option<bool> {
    if *pos >= encoded.len() {
        return None;
    }
    let v = encoded[*pos];
    *pos += 1;
    if v > 1 {
        return None;
    }
    Some(v != 0)
}

fn read_u8(encoded: &[u8], pos: &mut usize) -> Option<u8> {
    if *pos >= encoded.len() {
        return None;
    }
    let v = encoded[*pos];
    *pos += 1;
    Some(v)
}

fn read_u16(encoded: &[u8], pos: &mut usize) -> Option<u16> {
    if *pos + 2 > encoded.len() {
        return None;
    }
    let v = u16::from_le_bytes([encoded[*pos], encoded[*pos + 1]]);
    *pos += 2;
    Some(v)
}

fn read_varint32(encoded: &[u8], pos: &mut usize) -> Option<u32> {
    let mut num = 0u32;
    let mut result = 0u32;
    loop {
        if *pos >= encoded.len() {
            return None;
        }
        let b = encoded[*pos];
        *pos += 1;
        if num == 4 && b > 15 {
            return None;
        }
        result |= ((b & 127) as u32) << (num * 7);
        if b < 128 {
            return Some(result);
        }
        num += 1;
    }
}

fn size_bits_to_offsets(size_bits_by_length: &[u8; 32]) -> ([u32; 32], usize) {
    let mut offsets = [0u32; 32];
    let mut pos = 0u32;
    for i in 0..=SHARED_BROTLI_MAX_DICTIONARY_WORD_LENGTH {
        offsets[i] = pos;
        if size_bits_by_length[i] != 0 {
            pos += (i as u32) << size_bits_by_length[i];
        }
    }
    (offsets, pos as usize)
}

fn parse_word_list(encoded: &[u8], pos: &mut usize) -> Option<BrotliDictionary> {
    if *pos + BROTLI_NUM_ENCODED_LENGTHS > encoded.len() {
        return None;
    }
    let mut size_bits_by_length = [0u8; 32];
    size_bits_by_length
        [SHARED_BROTLI_MIN_DICTIONARY_WORD_LENGTH..SHARED_BROTLI_MIN_DICTIONARY_WORD_LENGTH + BROTLI_NUM_ENCODED_LENGTHS]
        .copy_from_slice(&encoded[*pos..*pos + BROTLI_NUM_ENCODED_LENGTHS]);
    for i in SHARED_BROTLI_MIN_DICTIONARY_WORD_LENGTH..=SHARED_BROTLI_MAX_DICTIONARY_WORD_LENGTH {
        if size_bits_by_length[i] > BROTLI_MAX_SIZE_BITS {
            return None;
        }
    }
    *pos += BROTLI_NUM_ENCODED_LENGTHS;
    let (offsets_by_length, data_size) = size_bits_to_offsets(&size_bits_by_length);
    if *pos + data_size > encoded.len() {
        return None;
    }
    let data = encoded[*pos..*pos + data_size].to_vec();
    *pos += data_size;
    Some(BrotliDictionary {
        size_bits_by_length,
        offsets_by_length,
        data_size,
        data: Cow::Owned(data),
    })
}

fn parse_transforms_list(encoded: &[u8], pos: &mut usize) -> Option<BrotliTransforms> {
    if *pos >= encoded.len() {
        return None;
    }
    let prefix_suffix_size = read_u16(encoded, pos)?;
    let data_length = prefix_suffix_size as usize;
    if data_length < 1 || *pos + data_length >= encoded.len() {
        return None;
    }
    let prefix_suffix = encoded[*pos..*pos + data_length].to_vec();
    let mut prefix_suffix_map = Vec::new();
    let mut offset = 0usize;
    loop {
        let stringlet_len = prefix_suffix[offset] as usize;
        prefix_suffix_map.push(offset as u16);
        offset += 1;
        if stringlet_len == 0 {
            if offset == data_length {
                break;
            } else {
                return None;
            }
        }
        if prefix_suffix_map.len() > 255 {
            return None;
        }
        offset += stringlet_len;
        if offset >= data_length {
            return None;
        }
    }
    *pos += data_length;
    let stringlet_cnt = prefix_suffix_map.len();

    let num_transforms = read_u8(encoded, pos)? as usize;
    if *pos + num_transforms * 3 > encoded.len() {
        return None;
    }
    let transforms_data = encoded[*pos..*pos + num_transforms * 3].to_vec();
    *pos += num_transforms * 3;

    let mut has_params = false;
    for i in 0..num_transforms {
        let prefix_id = transforms_data[i * 3] as usize;
        let t = transforms_data[i * 3 + 1];
        let suffix_id = transforms_data[i * 3 + 2] as usize;
        if prefix_id >= stringlet_cnt || t >= BROTLI_NUM_TRANSFORM_TYPES || suffix_id >= stringlet_cnt {
            return None;
        }
        if t == 21 || t == 22 {
            has_params = true;
        }
    }

    let params = if has_params {
        if *pos + num_transforms * 2 > encoded.len() {
            return None;
        }
        let p = encoded[*pos..*pos + num_transforms * 2].to_vec();
        *pos += num_transforms * 2;
        for i in 0..num_transforms {
            let t = transforms_data[i * 3 + 1];
            if t != 21 && t != 22 && (p[i * 2] != 0 || p[i * 2 + 1] != 0) {
                return None;
            }
        }
        Some(Cow::Owned(p))
    } else {
        None
    };

    let mut out = BrotliTransforms {
        prefix_suffix_size,
        prefix_suffix: Cow::Owned(prefix_suffix),
        prefix_suffix_map: Cow::Owned(prefix_suffix_map),
        num_transforms: num_transforms as u32,
        transforms: Cow::Owned(transforms_data),
        params,
        cut_off_transforms: [-1; BROTLI_TRANSFORMS_MAX_CUT_OFF + 1],
    };
    for i in 0..num_transforms {
        let prefix = out.prefix(i);
        let t = out.transform_type(i);
        let suffix = out.suffix(i);
        if (t as usize) <= BROTLI_TRANSFORMS_MAX_CUT_OFF
            && prefix[0] == 0
            && suffix[0] == 0
            && out.cut_off_transforms[t as usize] == -1
        {
            out.cut_off_transforms[t as usize] = i as i16;
        }
    }
    Some(out)
}

fn decode_shared_dictionary(encoded: &[u8], dict: &mut BrotliSharedDictionary) -> bool {
    let has_custom_static_dict =
        !dict.words_instances.is_empty() || !dict.transforms_instances.is_empty();
    if encoded.len() < 2 || encoded[0] != 0x91 || encoded[1] != 0 {
        return false;
    }
    let mut pos = 2usize;
    let Some(chunk_size) = read_varint32(encoded, &mut pos) else {
        return false;
    };
    let mut new_prefix: Option<Arc<[u8]>> = None;
    if chunk_size != 0 {
        if chunk_size > 1_073_741_823 {
            return false;
        }
        if dict.prefixes.len() + 1 > SHARED_BROTLI_MAX_COMPOUND_DICTS {
            return false;
        }
        if pos + (chunk_size as usize) > encoded.len() {
            return false;
        }
        new_prefix = Some(Arc::from(&encoded[pos..pos + (chunk_size as usize)]));
        pos += chunk_size as usize;
    }

    let Some(num_word_lists) = read_u8(encoded, &mut pos) else {
        return false;
    };
    if (num_word_lists as usize) > SHARED_BROTLI_NUM_DICTIONARY_CONTEXTS {
        return false;
    }
    let mut words_instances = Vec::with_capacity(num_word_lists as usize);
    for _ in 0..num_word_lists {
        let Some(w) = parse_word_list(encoded, &mut pos) else {
            return false;
        };
        words_instances.push(w);
    }

    let Some(num_transform_lists) = read_u8(encoded, &mut pos) else {
        return false;
    };
    if (num_transform_lists as usize) > SHARED_BROTLI_NUM_DICTIONARY_CONTEXTS {
        return false;
    }
    let is_custom_static_dict = num_word_lists > 0 || num_transform_lists > 0;
    if has_custom_static_dict && is_custom_static_dict {
        return false;
    }
    let mut transforms_instances = Vec::with_capacity(num_transform_lists as usize);
    for _ in 0..num_transform_lists {
        let Some(t) = parse_transforms_list(encoded, &mut pos) else {
            return false;
        };
        transforms_instances.push(t);
    }

    if is_custom_static_dict {
        let Some(num_dictionaries) = read_u8(encoded, &mut pos) else {
            return false;
        };
        if num_dictionaries == 0
            || (num_dictionaries as usize) > SHARED_BROTLI_NUM_DICTIONARY_CONTEXTS
        {
            return false;
        }
        let mut words_refs = Vec::with_capacity(num_dictionaries as usize);
        let mut transforms_refs = Vec::with_capacity(num_dictionaries as usize);
        for _ in 0..num_dictionaries {
            let Some(words_index) = read_u8(encoded, &mut pos) else {
                return false;
            };
            if words_index > num_word_lists {
                return false;
            }
            let Some(transforms_index) = read_u8(encoded, &mut pos) else {
                return false;
            };
            if transforms_index > num_transform_lists {
                return false;
            }
            words_refs.push(if words_index == num_word_lists {
                DictRef::Builtin
            } else {
                DictRef::Custom(words_index as usize)
            });
            transforms_refs.push(if transforms_index == num_transform_lists {
                DictRef::Builtin
            } else {
                DictRef::Custom(transforms_index as usize)
            });
        }
        let Some(context_based) = read_bool(encoded, &mut pos) else {
            return false;
        };
        let mut context_map = [0u8; SHARED_BROTLI_NUM_DICTIONARY_CONTEXTS];
        if context_based {
            for entry in context_map.iter_mut() {
                let Some(val) = read_u8(encoded, &mut pos) else {
                    return false;
                };
                if val >= num_dictionaries {
                    return false;
                }
                *entry = val;
            }
        }
        dict.words_instances = words_instances;
        dict.transforms_instances = transforms_instances;
        dict.words_refs = words_refs;
        dict.transforms_refs = transforms_refs;
        dict.num_dictionaries = num_dictionaries;
        dict.context_based = context_based;
        dict.context_map = context_map;
    }

    if let Some(p) = new_prefix {
        dict.prefixes.push(p);
    }
    true
}
