//! Static Brotli dictionary words.

use std::borrow::Cow;

pub const BROTLI_MIN_DICTIONARY_WORD_LENGTH: usize = 4;
pub const BROTLI_MAX_DICTIONARY_WORD_LENGTH: usize = 24;

#[derive(Debug, Clone)]
pub struct BrotliDictionary {
    pub size_bits_by_length: [u8; 32],
    pub offsets_by_length: [u32; 32],
    pub data_size: usize,
    pub data: Cow<'static, [u8]>,
}

pub static K_BROTLI_DICTIONARY_DATA: &[u8; 122784] = include_bytes!("dictionary.bin");

pub fn get_default_dictionary() -> BrotliDictionary {
    BrotliDictionary {
        size_bits_by_length: [
            0, 0, 0, 0, 10, 10, 11, 11,
            10, 10, 10, 10, 10, 9, 9, 8,
            7, 7, 8, 7, 7, 6, 6, 5,
            5, 0, 0, 0, 0, 0, 0, 0,
        ],
        offsets_by_length: [
            0, 0, 0, 0, 0, 4096, 9216, 21504,
            35840, 44032, 53248, 63488, 74752, 87040, 93696, 100864,
            104704, 106752, 108928, 113536, 115968, 118528, 119872, 121280,
            122016, 122784, 122784, 122784, 122784, 122784, 122784, 122784,
        ],
        data_size: 122784,
        data: Cow::Borrowed(K_BROTLI_DICTIONARY_DATA.as_slice()),
    }
}

pub static DEFAULT_DICTIONARY: BrotliDictionary = BrotliDictionary {
    size_bits_by_length: [
        0, 0, 0, 0, 10, 10, 11, 11,
        10, 10, 10, 10, 10, 9, 9, 8,
        7, 7, 8, 7, 7, 6, 6, 5,
        5, 0, 0, 0, 0, 0, 0, 0,
    ],
    offsets_by_length: [
        0, 0, 0, 0, 0, 4096, 9216, 21504,
        35840, 44032, 53248, 63488, 74752, 87040, 93696, 100864,
        104704, 106752, 108928, 113536, 115968, 118528, 119872, 121280,
        122016, 122784, 122784, 122784, 122784, 122784, 122784, 122784,
    ],
    data_size: 122784,
    data: Cow::Borrowed(K_BROTLI_DICTIONARY_DATA.as_slice()),
};

#[inline]
pub fn brotli_get_dictionary() -> &'static BrotliDictionary {
    &DEFAULT_DICTIONARY
}
