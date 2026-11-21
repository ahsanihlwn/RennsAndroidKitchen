//! Common constants used across the Brotli decoder and encoder.

pub const BROTLI_CONTEXT_MAP_MAX_RLE: usize = 16;

pub const BROTLI_MAX_NUMBER_OF_BLOCK_TYPES: usize = 256;

pub const BROTLI_NUM_LITERAL_SYMBOLS: usize = 256;
pub const BROTLI_NUM_COMMAND_SYMBOLS: usize = 704;
pub const BROTLI_NUM_BLOCK_LEN_SYMBOLS: usize = 26;
pub const BROTLI_MAX_CONTEXT_MAP_SYMBOLS: usize =
    BROTLI_MAX_NUMBER_OF_BLOCK_TYPES + BROTLI_CONTEXT_MAP_MAX_RLE;
pub const BROTLI_MAX_BLOCK_TYPE_SYMBOLS: usize = BROTLI_MAX_NUMBER_OF_BLOCK_TYPES + 2;

pub const BROTLI_REPEAT_PREVIOUS_CODE_LENGTH: u32 = 16;
pub const BROTLI_REPEAT_ZERO_CODE_LENGTH: u32 = 17;
pub const BROTLI_CODE_LENGTH_CODES: usize = (BROTLI_REPEAT_ZERO_CODE_LENGTH as usize) + 1;
pub const BROTLI_INITIAL_REPEATED_CODE_LENGTH: u32 = 8;

pub const BROTLI_LARGE_MAX_DISTANCE_BITS: u32 = 62;
pub const BROTLI_LARGE_MIN_WBITS: u32 = 10;
pub const BROTLI_LARGE_MAX_WBITS: u32 = 30;

pub const BROTLI_NUM_DISTANCE_SHORT_CODES: usize = 16;
pub const BROTLI_MAX_NPOSTFIX: u32 = 3;
pub const BROTLI_MAX_NDIRECT: u32 = 120;
pub const BROTLI_MAX_DISTANCE_BITS: u32 = 24;

#[inline]
pub const fn brotli_distance_alphabet_size(
    npostfix: u32,
    ndirect: u32,
    maxnbits: u32,
) -> u32 {
    (BROTLI_NUM_DISTANCE_SHORT_CODES as u32) + ndirect + (maxnbits << (npostfix + 1))
}

pub const BROTLI_NUM_DISTANCE_SYMBOLS: u32 = brotli_distance_alphabet_size(
    BROTLI_MAX_NPOSTFIX,
    BROTLI_MAX_NDIRECT,
    BROTLI_LARGE_MAX_DISTANCE_BITS,
);

pub const BROTLI_MAX_DISTANCE: usize = 0x3FFFFFC;
pub const BROTLI_MAX_ALLOWED_DISTANCE: usize = 0x7FFFFFFC;

pub const BROTLI_NUM_INS_COPY_CODES: usize = 24;

pub const BROTLI_LITERAL_CONTEXT_BITS: usize = 6;
pub const BROTLI_DISTANCE_CONTEXT_BITS: usize = 2;

pub const BROTLI_WINDOW_GAP: usize = 16;

#[inline]
pub const fn brotli_max_backward_limit(w: u32) -> usize {
    (1usize << w) - BROTLI_WINDOW_GAP
}

pub const BROTLI_MIN_WINDOW_BITS: i32 = 10;
pub const BROTLI_MAX_WINDOW_BITS: i32 = 24;
pub const BROTLI_LARGE_MAX_WINDOW_BITS: i32 = 30;
pub const BROTLI_MIN_INPUT_BLOCK_BITS: i32 = 16;
pub const BROTLI_MAX_INPUT_BLOCK_BITS: i32 = 24;
pub const BROTLI_MIN_QUALITY: i32 = 0;
pub const BROTLI_MAX_QUALITY: i32 = 11;

pub const BROTLI_DEFAULT_QUALITY: i32 = 11;
pub const BROTLI_DEFAULT_WINDOW: i32 = 22;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BrotliDistanceCodeLimit {
    pub max_alphabet_size: u32,
    pub max_distance: u32,
}

pub fn brotli_calculate_distance_code_limit(
    max_distance: u32,
    npostfix: u32,
    ndirect: u32,
) -> BrotliDistanceCodeLimit {
    if max_distance <= ndirect {
        return BrotliDistanceCodeLimit {
            max_alphabet_size: max_distance + BROTLI_NUM_DISTANCE_SHORT_CODES as u32,
            max_distance,
        };
    }
    let forbidden_distance = max_distance + 1;
    let mut offset = forbidden_distance - ndirect - 1;
    let mut ndistbits: u32 = 0;
    let postfix = (1u32 << npostfix) - 1;
    offset = (offset >> npostfix) + 4;
    let mut tmp = offset / 2;
    while tmp != 0 {
        ndistbits += 1;
        tmp >>= 1;
    }
    ndistbits -= 1;
    let half = (offset >> ndistbits) & 1;
    let mut group = ((ndistbits - 1) << 1) | half;
    if group == 0 {
        return BrotliDistanceCodeLimit {
            max_alphabet_size: ndirect + BROTLI_NUM_DISTANCE_SHORT_CODES as u32,
            max_distance: ndirect,
        };
    }
    group -= 1;
    ndistbits = (group >> 1) + 1;
    let extra = (1u32 << ndistbits) - 1;
    let mut start = (1u32 << (ndistbits + 1)) - 4;
    start += (group & 1) << ndistbits;
    BrotliDistanceCodeLimit {
        max_alphabet_size: ((group << npostfix) | postfix)
            + ndirect
            + (BROTLI_NUM_DISTANCE_SHORT_CODES as u32)
            + 1,
        max_distance: ((start + extra) << npostfix) + postfix + ndirect + 1,
    }
}

#[derive(Debug, Clone, Copy)]
pub struct BrotliPrefixCodeRange {
    pub offset: u16,
    pub nbits: u8,
}

pub static K_BROTLI_PREFIX_CODE_RANGES: [BrotliPrefixCodeRange; BROTLI_NUM_BLOCK_LEN_SYMBOLS] = [
    BrotliPrefixCodeRange { offset: 1, nbits: 2 },
    BrotliPrefixCodeRange { offset: 5, nbits: 2 },
    BrotliPrefixCodeRange { offset: 9, nbits: 2 },
    BrotliPrefixCodeRange { offset: 13, nbits: 2 },
    BrotliPrefixCodeRange { offset: 17, nbits: 3 },
    BrotliPrefixCodeRange { offset: 25, nbits: 3 },
    BrotliPrefixCodeRange { offset: 33, nbits: 3 },
    BrotliPrefixCodeRange { offset: 41, nbits: 3 },
    BrotliPrefixCodeRange { offset: 49, nbits: 4 },
    BrotliPrefixCodeRange { offset: 65, nbits: 4 },
    BrotliPrefixCodeRange { offset: 81, nbits: 4 },
    BrotliPrefixCodeRange { offset: 97, nbits: 4 },
    BrotliPrefixCodeRange { offset: 113, nbits: 5 },
    BrotliPrefixCodeRange { offset: 145, nbits: 5 },
    BrotliPrefixCodeRange { offset: 177, nbits: 5 },
    BrotliPrefixCodeRange { offset: 209, nbits: 5 },
    BrotliPrefixCodeRange { offset: 241, nbits: 6 },
    BrotliPrefixCodeRange { offset: 305, nbits: 6 },
    BrotliPrefixCodeRange { offset: 369, nbits: 7 },
    BrotliPrefixCodeRange { offset: 497, nbits: 8 },
    BrotliPrefixCodeRange { offset: 753, nbits: 9 },
    BrotliPrefixCodeRange { offset: 1265, nbits: 10 },
    BrotliPrefixCodeRange { offset: 2289, nbits: 11 },
    BrotliPrefixCodeRange { offset: 4337, nbits: 12 },
    BrotliPrefixCodeRange { offset: 8433, nbits: 13 },
    BrotliPrefixCodeRange { offset: 16625, nbits: 24 },
];
