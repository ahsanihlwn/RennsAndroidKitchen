//! Brotli streaming and one-shot decompression implementation.

use crate::common::{
    brotli_calculate_distance_code_limit, brotli_context, brotli_context_lut,
    brotli_distance_alphabet_size, brotli_transform_dictionary_word,
    BrotliSharedDictionaryType, BROTLI_CODE_LENGTH_CODES, BROTLI_DISTANCE_CONTEXT_BITS,
    BROTLI_LARGE_MAX_DISTANCE_BITS, BROTLI_LARGE_MAX_WBITS, BROTLI_LARGE_MIN_WBITS,
    BROTLI_LITERAL_CONTEXT_BITS, BROTLI_MAX_ALLOWED_DISTANCE, BROTLI_MAX_DISTANCE_BITS,
    BROTLI_NUM_BLOCK_LEN_SYMBOLS, BROTLI_NUM_COMMAND_SYMBOLS, BROTLI_NUM_DISTANCE_SHORT_CODES,
    BROTLI_NUM_LITERAL_SYMBOLS, BROTLI_REPEAT_PREVIOUS_CODE_LENGTH, BROTLI_VERSION,
    BROTLI_WINDOW_GAP, K_BROTLI_PREFIX_CODE_RANGES, SHARED_BROTLI_MAX_COMPOUND_DICTS,
    SHARED_BROTLI_MAX_DICTIONARY_WORD_LENGTH, SHARED_BROTLI_MAX_RAW_DICT_SIZE,
    SHARED_BROTLI_MIN_DICTIONARY_WORD_LENGTH,
};

use super::bit_reader::{bit_mask, BrotliBitReader};
use super::huffman::{
    brotli_build_code_lengths_huffman_table, brotli_build_huffman_table,
    brotli_build_simple_huffman_table, HuffmanCode, BROTLI_HUFFMAN_MAX_CODE_LENGTH,
    BROTLI_HUFFMAN_MAX_CODE_LENGTH_CODE_LENGTH, BROTLI_HUFFMAN_MAX_SIZE_258,
    BROTLI_HUFFMAN_MAX_SIZE_26,
};
use super::prefix::K_CMD_LUT;
use super::state::{
    brotli_decoder_huffman_tree_group_init, BrotliDecoderCompoundDictionary,
    BrotliDecoderErrorCode, BrotliDecoderParameter, BrotliDecoderResult, BrotliDecoderState,
    BrotliRunningContextMapState, BrotliRunningDecodeUint8State, BrotliRunningHuffmanState,
    BrotliRunningMetablockHeaderState, BrotliRunningReadBlockLengthState, BrotliRunningState,
    BrotliRunningTreeGroupState, BrotliRunningUncompressedState, HuffmanTarget, MetadataChunkFn,
    MetadataStartFn, TreeGroupKind,
};

const HUFFMAN_TABLE_BITS: u32 = 8;
const HUFFMAN_TABLE_MASK: usize = 0xFF;
const K_RING_BUFFER_WRITE_AHEAD_SLACK: usize = 542;

static K_CODE_LENGTH_CODE_ORDER: [u8; BROTLI_CODE_LENGTH_CODES] = [
    1, 2, 3, 4, 0, 5, 17, 6, 16, 7, 8, 9, 10, 11, 12, 13, 14, 15,
];

static K_CODE_LENGTH_PREFIX_LENGTH: [u8; 16] =
    [2, 2, 2, 3, 2, 2, 2, 4, 2, 2, 2, 3, 2, 2, 2, 4];

static K_CODE_LENGTH_PREFIX_VALUE: [u8; 16] =
    [0, 4, 3, 2, 0, 4, 3, 1, 0, 4, 3, 2, 0, 4, 3, 5];

#[inline]
pub fn brotli_decoder_version() -> u32 {
    BROTLI_VERSION
}

#[inline]
pub fn brotli_decoder_error_string(c: BrotliDecoderErrorCode) -> &'static str {
    c.as_str()
}

#[inline(always)]
fn log2_floor(mut x: u32) -> u32 {
    let mut result = 0u32;
    while x != 0 {
        x >>= 1;
        result += 1;
    }
    result
}

#[inline(always)]
fn decode_symbol(bits: u64, table: &[HuffmanCode], br: &mut BrotliBitReader) -> u32 {
    let mut idx = (bits as usize) & HUFFMAN_TABLE_MASK;
    let entry = table[idx];
    if (entry.bits as u32) > HUFFMAN_TABLE_BITS {
        let nbits = (entry.bits as u32) - HUFFMAN_TABLE_BITS;
        br.drop_bits(HUFFMAN_TABLE_BITS);
        idx += (entry.value as usize) + (((bits >> HUFFMAN_TABLE_BITS) & bit_mask(nbits)) as usize);
        let sub = table[idx];
        br.drop_bits(sub.bits as u32);
        return sub.value as u32;
    }
    br.drop_bits(entry.bits as u32);
    entry.value as u32
}

#[inline(always)]
fn read_symbol(table: &[HuffmanCode], br: &mut BrotliBitReader, input: &[u8]) -> u32 {
    let bits = br.get_16_bits_unmasked(input);
    decode_symbol(bits, table, br)
}

#[inline(never)]
fn safe_decode_symbol(
    table: &[HuffmanCode],
    br: &mut BrotliBitReader,
    result: &mut u32,
) -> bool {
    let mut available_bits = br.get_available_bits();
    if available_bits == 0 {
        if table[0].bits == 0 {
            *result = table[0].value as u32;
            return true;
        }
        return false;
    }
    let val = br.get_bits_unmasked();
    let idx = (val as usize) & HUFFMAN_TABLE_MASK;
    let entry = table[idx];
    if (entry.bits as u32) <= HUFFMAN_TABLE_BITS {
        if (entry.bits as u32) <= available_bits {
            br.drop_bits(entry.bits as u32);
            *result = entry.value as u32;
            return true;
        } else {
            return false;
        }
    }
    if available_bits <= HUFFMAN_TABLE_BITS {
        return false;
    }
    let sub_val =
        ((val & bit_mask(entry.bits as u32)) >> HUFFMAN_TABLE_BITS) as usize;
    available_bits -= HUFFMAN_TABLE_BITS;
    let sub = table[idx + (entry.value as usize) + sub_val];
    if available_bits < (sub.bits as u32) {
        return false;
    }
    br.drop_bits(HUFFMAN_TABLE_BITS + (sub.bits as u32));
    *result = sub.value as u32;
    true
}

#[inline(always)]
fn safe_read_symbol(
    table: &[HuffmanCode],
    br: &mut BrotliBitReader,
    input: &[u8],
    result: &mut u32,
) -> bool {
    let mut val = 0u64;
    if br.safe_get_bits(input, 15, &mut val) {
        *result = decode_symbol(val, table, br);
        return true;
    }
    safe_decode_symbol(table, br, result)
}

#[inline(always)]
fn preload_symbol(
    safe: bool,
    table: &[HuffmanCode],
    br: &mut BrotliBitReader,
    input: &[u8],
    bits: &mut u32,
    value: &mut u32,
) {
    if safe {
        return;
    }
    let idx = br.get_bits(input, HUFFMAN_TABLE_BITS) as usize;
    let entry = table[idx];
    *bits = entry.bits as u32;
    *value = entry.value as u32;
}

#[inline(always)]
fn read_preloaded_symbol(
    table: &[HuffmanCode],
    br: &mut BrotliBitReader,
    input: &[u8],
    bits: &mut u32,
    value: &mut u32,
) -> u32 {
    let mut result = *value;
    if *bits > HUFFMAN_TABLE_BITS {
        let val = br.get_16_bits_unmasked(input);
        let mask = bit_mask(*bits - HUFFMAN_TABLE_BITS);
        br.drop_bits(HUFFMAN_TABLE_BITS);
        let idx = ((val as usize) & HUFFMAN_TABLE_MASK)
            + (*value as usize)
            + (((val >> HUFFMAN_TABLE_BITS) & mask) as usize);
        let ext = table[idx];
        br.drop_bits(ext.bits as u32);
        result = ext.value as u32;
    } else {
        br.drop_bits(*bits);
    }
    preload_symbol(false, table, br, input, bits, value);
    result
}

#[inline(always)]
fn copy_preloaded_symbols_to_u8(
    table: &[HuffmanCode],
    br: &mut BrotliBitReader,
    input: &[u8],
    bits: &mut u32,
    value: &mut u32,
    ringbuffer: &mut [u8],
    mut pos: usize,
    limit: usize,
) -> usize {
    const MAXIMAL_OVERREAD: isize = 4;
    let mut pos_limit = limit as isize;
    let mut new_lim = (br.guard_in as isize) - (br.next_in as isize);
    new_lim *= 8;
    new_lim /= 15;
    if (new_lim - MAXIMAL_OVERREAD) <= (limit as isize) {
        pos_limit = new_lim - MAXIMAL_OVERREAD;
    }
    if pos_limit < 0 {
        pos_limit = 0;
    }
    let mut copies = pos_limit as usize;
    let end_pos = pos + copies;
    while pos < end_pos {
        debug_assert!(br.check_input_amount());
        ringbuffer[pos] = read_preloaded_symbol(table, br, input, bits, value) as u8;
        pos += 1;
    }
    while br.check_input_amount() && copies < limit {
        ringbuffer[pos] = read_preloaded_symbol(table, br, input, bits, value) as u8;
        pos += 1;
        copies += 1;
    }
    copies
}

fn decode_window_bits(s: &mut BrotliDecoderState) -> BrotliDecoderErrorCode {
    let large_window = s.large_window;
    s.large_window = false;
    let mut n = s.br.take_bits(1);
    if n == 0 {
        s.window_bits = 16;
        return BrotliDecoderErrorCode::Success;
    }
    n = s.br.take_bits(3);
    if n != 0 {
        s.window_bits = (17 + n as u32) & 63;
        return BrotliDecoderErrorCode::Success;
    }
    n = s.br.take_bits(3);
    if n == 1 {
        if large_window {
            n = s.br.take_bits(1);
            if n == 1 {
                return BrotliDecoderErrorCode::ErrorFormatWindowBits;
            }
            s.large_window = true;
            return BrotliDecoderErrorCode::Success;
        } else {
            return BrotliDecoderErrorCode::ErrorFormatWindowBits;
        }
    }
    if n != 0 {
        s.window_bits = (8 + n as u32) & 63;
        return BrotliDecoderErrorCode::Success;
    }
    s.window_bits = 17;
    BrotliDecoderErrorCode::Success
}

#[inline(never)]
fn decode_var_len_uint8(
    s: &mut BrotliDecoderState,
    input: &[u8],
    value: &mut u32,
) -> BrotliDecoderErrorCode {
    let mut bits = 0u64;
    match s.substate_decode_uint8 {
        BrotliRunningDecodeUint8State::None => {
            if !s.br.safe_read_bits(input, 1, &mut bits) {
                return BrotliDecoderErrorCode::NeedsMoreInput;
            }
            if bits == 0 {
                *value = 0;
                return BrotliDecoderErrorCode::Success;
            }
            // Fall through to Short
            if !s.br.safe_read_bits(input, 3, &mut bits) {
                s.substate_decode_uint8 = BrotliRunningDecodeUint8State::Short;
                return BrotliDecoderErrorCode::NeedsMoreInput;
            }
            if bits == 0 {
                *value = 1;
                s.substate_decode_uint8 = BrotliRunningDecodeUint8State::None;
                return BrotliDecoderErrorCode::Success;
            }
            *value = bits as u32;
            if !s.br.safe_read_bits(input, *value, &mut bits) {
                s.substate_decode_uint8 = BrotliRunningDecodeUint8State::Long;
                return BrotliDecoderErrorCode::NeedsMoreInput;
            }
            *value = (1u32 << *value) + (bits as u32);
            s.substate_decode_uint8 = BrotliRunningDecodeUint8State::None;
            BrotliDecoderErrorCode::Success
        }
        BrotliRunningDecodeUint8State::Short => {
            if !s.br.safe_read_bits(input, 3, &mut bits) {
                return BrotliDecoderErrorCode::NeedsMoreInput;
            }
            if bits == 0 {
                *value = 1;
                s.substate_decode_uint8 = BrotliRunningDecodeUint8State::None;
                return BrotliDecoderErrorCode::Success;
            }
            *value = bits as u32;
            if !s.br.safe_read_bits(input, *value, &mut bits) {
                s.substate_decode_uint8 = BrotliRunningDecodeUint8State::Long;
                return BrotliDecoderErrorCode::NeedsMoreInput;
            }
            *value = (1u32 << *value) + (bits as u32);
            s.substate_decode_uint8 = BrotliRunningDecodeUint8State::None;
            BrotliDecoderErrorCode::Success
        }
        BrotliRunningDecodeUint8State::Long => {
            if !s.br.safe_read_bits(input, *value, &mut bits) {
                return BrotliDecoderErrorCode::NeedsMoreInput;
            }
            *value = (1u32 << *value) + (bits as u32);
            s.substate_decode_uint8 = BrotliRunningDecodeUint8State::None;
            BrotliDecoderErrorCode::Success
        }
    }
}

#[inline(never)]
fn decode_meta_block_length(
    s: &mut BrotliDecoderState,
    input: &[u8],
) -> BrotliDecoderErrorCode {
    let mut bits = 0u64;
    loop {
        match s.substate_metablock_header {
            BrotliRunningMetablockHeaderState::None => {
                if !s.br.safe_read_bits(input, 1, &mut bits) {
                    return BrotliDecoderErrorCode::NeedsMoreInput;
                }
                s.is_last_metablock = bits != 0;
                s.meta_block_remaining_len = 0;
                s.is_uncompressed = false;
                s.is_metadata = false;
                if !s.is_last_metablock {
                    s.substate_metablock_header = BrotliRunningMetablockHeaderState::Nibbles;
                    continue;
                }
                s.substate_metablock_header = BrotliRunningMetablockHeaderState::Empty;
            }
            BrotliRunningMetablockHeaderState::Empty => {
                if !s.br.safe_read_bits(input, 1, &mut bits) {
                    return BrotliDecoderErrorCode::NeedsMoreInput;
                }
                if bits != 0 {
                    s.substate_metablock_header = BrotliRunningMetablockHeaderState::None;
                    return BrotliDecoderErrorCode::Success;
                }
                s.substate_metablock_header = BrotliRunningMetablockHeaderState::Nibbles;
            }
            BrotliRunningMetablockHeaderState::Nibbles => {
                if !s.br.safe_read_bits(input, 2, &mut bits) {
                    return BrotliDecoderErrorCode::NeedsMoreInput;
                }
                s.size_nibbles = (bits + 4) as u8;
                s.loop_counter = 0;
                if bits == 3 {
                    s.is_metadata = true;
                    s.substate_metablock_header = BrotliRunningMetablockHeaderState::Reserved;
                    continue;
                }
                s.substate_metablock_header = BrotliRunningMetablockHeaderState::Size;
            }
            BrotliRunningMetablockHeaderState::Size => {
                let mut i = s.loop_counter;
                while i < s.size_nibbles as i32 {
                    if !s.br.safe_read_bits(input, 4, &mut bits) {
                        s.loop_counter = i;
                        return BrotliDecoderErrorCode::NeedsMoreInput;
                    }
                    if i + 1 == s.size_nibbles as i32 && s.size_nibbles > 4 && bits == 0 {
                        return BrotliDecoderErrorCode::ErrorFormatExuberantNibble;
                    }
                    s.meta_block_remaining_len |= (bits as i32) << (i * 4);
                    i += 1;
                }
                s.substate_metablock_header = BrotliRunningMetablockHeaderState::Uncompressed;
            }
            BrotliRunningMetablockHeaderState::Uncompressed => {
                if !s.is_last_metablock {
                    if !s.br.safe_read_bits(input, 1, &mut bits) {
                        return BrotliDecoderErrorCode::NeedsMoreInput;
                    }
                    s.is_uncompressed = bits != 0;
                }
                s.meta_block_remaining_len += 1;
                s.substate_metablock_header = BrotliRunningMetablockHeaderState::None;
                return BrotliDecoderErrorCode::Success;
            }
            BrotliRunningMetablockHeaderState::Reserved => {
                if !s.br.safe_read_bits(input, 1, &mut bits) {
                    return BrotliDecoderErrorCode::NeedsMoreInput;
                }
                if bits != 0 {
                    return BrotliDecoderErrorCode::ErrorFormatReserved;
                }
                s.substate_metablock_header = BrotliRunningMetablockHeaderState::Bytes;
            }
            BrotliRunningMetablockHeaderState::Bytes => {
                if !s.br.safe_read_bits(input, 2, &mut bits) {
                    return BrotliDecoderErrorCode::NeedsMoreInput;
                }
                if bits == 0 {
                    s.substate_metablock_header = BrotliRunningMetablockHeaderState::None;
                    return BrotliDecoderErrorCode::Success;
                }
                s.size_nibbles = bits as u8;
                s.substate_metablock_header = BrotliRunningMetablockHeaderState::Metadata;
            }
            BrotliRunningMetablockHeaderState::Metadata => {
                let mut i = s.loop_counter;
                while i < s.size_nibbles as i32 {
                    if !s.br.safe_read_bits(input, 8, &mut bits) {
                        s.loop_counter = i;
                        return BrotliDecoderErrorCode::NeedsMoreInput;
                    }
                    if i + 1 == s.size_nibbles as i32 && s.size_nibbles > 1 && bits == 0 {
                        return BrotliDecoderErrorCode::ErrorFormatExuberantMetaNibble;
                    }
                    s.meta_block_remaining_len |= (bits as i32) << (i * 8);
                    i += 1;
                }
                s.meta_block_remaining_len += 1;
                s.substate_metablock_header = BrotliRunningMetablockHeaderState::None;
                return BrotliDecoderErrorCode::Success;
            }
        }
    }
}

fn read_simple_huffman_symbols(
    alphabet_size_max: u32,
    alphabet_size_limit: u32,
    s: &mut BrotliDecoderState,
    input: &[u8],
) -> BrotliDecoderErrorCode {
    let max_bits = log2_floor(alphabet_size_max - 1);
    let mut i = s.header.sub_loop_counter;
    let num_symbols = s.header.symbol;
    while i <= num_symbols {
        let mut v = 0u64;
        if !s.br.safe_read_bits(input, max_bits, &mut v) {
            s.header.sub_loop_counter = i;
            s.header.substate_huffman = BrotliRunningHuffmanState::SimpleRead;
            return BrotliDecoderErrorCode::NeedsMoreInput;
        }
        if (v as u32) >= alphabet_size_limit {
            return BrotliDecoderErrorCode::ErrorFormatSimpleHuffmanAlphabet;
        }
        s.header.symbols_lists_array[i as usize] = v as u16;
        i += 1;
    }

    for a in 0..num_symbols as usize {
        for b in (a + 1)..=(num_symbols as usize) {
            if s.header.symbols_lists_array[a] == s.header.symbols_lists_array[b] {
                return BrotliDecoderErrorCode::ErrorFormatSimpleHuffmanSame;
            }
        }
    }

    BrotliDecoderErrorCode::Success
}

const SYM_LIST_BASE: isize = (BROTLI_HUFFMAN_MAX_CODE_LENGTH + 1) as isize;

#[inline(always)]
fn process_single_code_length(
    code_len: u32,
    symbol: &mut u32,
    repeat: &mut u32,
    space: &mut u32,
    prev_code_len: &mut u32,
    symbols_lists_array: &mut [u16],
    code_length_histo: &mut [u16],
    next_symbol: &mut [i32],
) {
    *repeat = 0;
    if code_len != 0 {
        let cl = code_len as usize;
        let idx = (SYM_LIST_BASE + next_symbol[cl] as isize) as usize;
        symbols_lists_array[idx] = *symbol as u16;
        next_symbol[cl] = *symbol as i32;
        *prev_code_len = code_len;
        *space -= 32768u32 >> code_len;
        code_length_histo[cl] += 1;
    }
    *symbol += 1;
}

#[inline(always)]
fn process_repeated_code_length(
    code_len: u32,
    mut repeat_delta: u32,
    alphabet_size: u32,
    symbol: &mut u32,
    repeat: &mut u32,
    space: &mut u32,
    prev_code_len: &mut u32,
    repeat_code_len: &mut u32,
    symbols_lists_array: &mut [u16],
    code_length_histo: &mut [u16],
    next_symbol: &mut [i32],
) {
    let extra_bits: u32;
    let new_len: u32;
    if code_len == BROTLI_REPEAT_PREVIOUS_CODE_LENGTH {
        new_len = *prev_code_len;
        extra_bits = 2;
    } else {
        new_len = 0;
        extra_bits = 3;
    }
    if *repeat_code_len != new_len {
        *repeat = 0;
        *repeat_code_len = new_len;
    }
    let old_repeat = *repeat;
    if *repeat > 0 {
        *repeat -= 2;
        *repeat <<= extra_bits;
    }
    *repeat += repeat_delta + 3;
    repeat_delta = *repeat - old_repeat;
    if *symbol + repeat_delta > alphabet_size {
        *symbol = alphabet_size;
        *space = 0xFFFFF;
        return;
    }
    if *repeat_code_len != 0 {
        let rcl = *repeat_code_len as usize;
        let last = *symbol + repeat_delta;
        let mut next = next_symbol[rcl];
        loop {
            symbols_lists_array[(SYM_LIST_BASE + next as isize) as usize] = *symbol as u16;
            next = *symbol as i32;
            *symbol += 1;
            if *symbol == last {
                break;
            }
        }
        next_symbol[rcl] = next;
        *space -= repeat_delta << (15 - *repeat_code_len);
        code_length_histo[rcl] = (code_length_histo[rcl] as u32 + repeat_delta) as u16;
    } else {
        *symbol += repeat_delta;
    }
}

fn read_symbol_code_lengths(
    alphabet_size: u32,
    s: &mut BrotliDecoderState,
    input: &[u8],
) -> BrotliDecoderErrorCode {
    let mut symbol = s.header.symbol;
    let mut repeat = s.header.repeat;
    let mut space = s.header.space;
    let mut prev_code_len = s.header.prev_code_len;
    let mut repeat_code_len = s.header.repeat_code_len;
    if !s.br.warmup(input) {
        return BrotliDecoderErrorCode::NeedsMoreInput;
    }
    while symbol < alphabet_size && space > 0 {
        if !s.br.check_input_amount() {
            s.header.symbol = symbol;
            s.header.repeat = repeat;
            s.header.prev_code_len = prev_code_len;
            s.header.repeat_code_len = repeat_code_len;
            s.header.space = space;
            return BrotliDecoderErrorCode::NeedsMoreInput;
        }
        s.br.fill_bit_window_16(input);
        let entry = s.header.table
            [(s.br.get_bits_unmasked() & bit_mask(BROTLI_HUFFMAN_MAX_CODE_LENGTH_CODE_LENGTH as u32)) as usize];
        s.br.drop_bits(entry.bits as u32);
        let code_len = entry.value as u32;
        if code_len < BROTLI_REPEAT_PREVIOUS_CODE_LENGTH {
            process_single_code_length(
                code_len,
                &mut symbol,
                &mut repeat,
                &mut space,
                &mut prev_code_len,
                &mut s.header.symbols_lists_array,
                &mut s.header.code_length_histo,
                &mut s.header.next_symbol,
            );
        } else {
            let extra_bits = if code_len == BROTLI_REPEAT_PREVIOUS_CODE_LENGTH {
                2
            } else {
                3
            };
            let repeat_delta = (s.br.get_bits_unmasked() & bit_mask(extra_bits)) as u32;
            s.br.drop_bits(extra_bits);
            process_repeated_code_length(
                code_len,
                repeat_delta,
                alphabet_size,
                &mut symbol,
                &mut repeat,
                &mut space,
                &mut prev_code_len,
                &mut repeat_code_len,
                &mut s.header.symbols_lists_array,
                &mut s.header.code_length_histo,
                &mut s.header.next_symbol,
            );
        }
    }
    s.header.space = space;
    BrotliDecoderErrorCode::Success
}

fn safe_read_symbol_code_lengths(
    alphabet_size: u32,
    s: &mut BrotliDecoderState,
    input: &[u8],
) -> BrotliDecoderErrorCode {
    let mut get_byte = false;
    let h = &mut s.header;
    while h.symbol < alphabet_size && h.space > 0 {
        if get_byte && !s.br.pull_byte(input) {
            return BrotliDecoderErrorCode::NeedsMoreInput;
        }
        get_byte = false;
        let available_bits = s.br.get_available_bits();
        let bits = if available_bits != 0 {
            s.br.get_bits_unmasked()
        } else {
            0
        };
        let entry = h.table
            [(bits & bit_mask(BROTLI_HUFFMAN_MAX_CODE_LENGTH_CODE_LENGTH as u32)) as usize];
        if (entry.bits as u32) > available_bits {
            get_byte = true;
            continue;
        }
        let code_len = entry.value as u32;
        if code_len < BROTLI_REPEAT_PREVIOUS_CODE_LENGTH {
            s.br.drop_bits(entry.bits as u32);
            process_single_code_length(
                code_len,
                &mut h.symbol,
                &mut h.repeat,
                &mut h.space,
                &mut h.prev_code_len,
                &mut h.symbols_lists_array,
                &mut h.code_length_histo,
                &mut h.next_symbol,
            );
        } else {
            let extra_bits = code_len - 14;
            let repeat_delta = ((bits >> (entry.bits as u32)) & bit_mask(extra_bits)) as u32;
            if available_bits < (entry.bits as u32) + extra_bits {
                get_byte = true;
                continue;
            }
            s.br.drop_bits((entry.bits as u32) + extra_bits);
            process_repeated_code_length(
                code_len,
                repeat_delta,
                alphabet_size,
                &mut h.symbol,
                &mut h.repeat,
                &mut h.space,
                &mut h.prev_code_len,
                &mut h.repeat_code_len,
                &mut h.symbols_lists_array,
                &mut h.code_length_histo,
                &mut h.next_symbol,
            );
        }
    }
    BrotliDecoderErrorCode::Success
}

fn read_code_length_code_lengths(
    s: &mut BrotliDecoderState,
    input: &[u8],
) -> BrotliDecoderErrorCode {
    let h = &mut s.header;
    let mut num_codes = h.repeat;
    let mut space = h.space;
    let mut i = h.sub_loop_counter as usize;
    while i < BROTLI_CODE_LENGTH_CODES {
        let code_len_idx = K_CODE_LENGTH_CODE_ORDER[i] as usize;
        let mut ix = 0u64;
        if !s.br.safe_get_bits(input, 4, &mut ix) {
            let available_bits = s.br.get_available_bits();
            ix = if available_bits != 0 {
                s.br.get_bits_unmasked() & 0xF
            } else {
                0
            };
            if (K_CODE_LENGTH_PREFIX_LENGTH[ix as usize] as u32) > available_bits {
                h.sub_loop_counter = i as u32;
                h.repeat = num_codes;
                h.space = space;
                h.substate_huffman = BrotliRunningHuffmanState::Complex;
                return BrotliDecoderErrorCode::NeedsMoreInput;
            }
        }
        let v = K_CODE_LENGTH_PREFIX_VALUE[ix as usize];
        s.br.drop_bits(K_CODE_LENGTH_PREFIX_LENGTH[ix as usize] as u32);
        h.code_length_code_lengths[code_len_idx] = v;
        if v != 0 {
            space = space.wrapping_sub(32u32 >> v);
            num_codes += 1;
            h.code_length_histo[v as usize] += 1;
            if space.wrapping_sub(1) >= 32 {
                break;
            }
        }
        i += 1;
    }
    if !(num_codes == 1 || space == 0) {
        return BrotliDecoderErrorCode::ErrorFormatClSpace;
    }
    BrotliDecoderErrorCode::Success
}

fn get_target_table<'a>(
    s: &'a mut BrotliDecoderState,
    target: HuffmanTarget,
    offset: usize,
) -> &'a mut [HuffmanCode] {
    match target {
        HuffmanTarget::BlockType(tree_idx) => {
            &mut s.block_type_trees[tree_idx * BROTLI_HUFFMAN_MAX_SIZE_258..]
        }
        HuffmanTarget::BlockLen(tree_idx) => {
            &mut s.block_len_trees[tree_idx * BROTLI_HUFFMAN_MAX_SIZE_26..]
        }
        HuffmanTarget::ContextMap => &mut s.header.context_map_table[..],
        HuffmanTarget::TreeGroup(TreeGroupKind::Literal) => {
            &mut s.literal_hgroup.codes[offset..]
        }
        HuffmanTarget::TreeGroup(TreeGroupKind::InsertCopy) => {
            &mut s.insert_copy_hgroup.codes[offset..]
        }
        HuffmanTarget::TreeGroup(TreeGroupKind::Distance) => {
            &mut s.distance_hgroup.codes[offset..]
        }
    }
}

fn read_huffman_code(
    alphabet_size_max: u32,
    alphabet_size_limit: u32,
    target: HuffmanTarget,
    target_offset: usize,
    opt_table_size: Option<&mut u32>,
    s: &mut BrotliDecoderState,
    input: &[u8],
) -> BrotliDecoderErrorCode {
    let mut opt_table_size = opt_table_size;
    loop {
        match s.header.substate_huffman {
            BrotliRunningHuffmanState::None => {
                let mut val = 0u64;
                if !s.br.safe_read_bits(input, 2, &mut val) {
                    return BrotliDecoderErrorCode::NeedsMoreInput;
                }
                s.header.sub_loop_counter = val as u32;
                if s.header.sub_loop_counter != 1 {
                    s.header.space = 32;
                    s.header.repeat = 0;
                    for slot in s.header.code_length_histo
                        [..=BROTLI_HUFFMAN_MAX_CODE_LENGTH_CODE_LENGTH]
                        .iter_mut()
                    {
                        *slot = 0;
                    }
                    s.header.code_length_code_lengths = [0; BROTLI_CODE_LENGTH_CODES];
                    s.header.substate_huffman = BrotliRunningHuffmanState::Complex;
                    continue;
                }
                s.header.substate_huffman = BrotliRunningHuffmanState::SimpleSize;
            }
            BrotliRunningHuffmanState::SimpleSize => {
                let mut val = 0u64;
                if !s.br.safe_read_bits(input, 2, &mut val) {
                    s.header.substate_huffman = BrotliRunningHuffmanState::SimpleSize;
                    return BrotliDecoderErrorCode::NeedsMoreInput;
                }
                s.header.symbol = val as u32;
                s.header.sub_loop_counter = 0;
                s.header.substate_huffman = BrotliRunningHuffmanState::SimpleRead;
            }
            BrotliRunningHuffmanState::SimpleRead => {
                let res = read_simple_huffman_symbols(
                    alphabet_size_max,
                    alphabet_size_limit,
                    s,
                    input,
                );
                if res != BrotliDecoderErrorCode::Success {
                    return res;
                }
                s.header.substate_huffman = BrotliRunningHuffmanState::SimpleBuild;
            }
            BrotliRunningHuffmanState::SimpleBuild => {
                if s.header.symbol == 3 {
                    let mut bits = 0u64;
                    if !s.br.safe_read_bits(input, 1, &mut bits) {
                        s.header.substate_huffman = BrotliRunningHuffmanState::SimpleBuild;
                        return BrotliDecoderErrorCode::NeedsMoreInput;
                    }
                    s.header.symbol += bits as u32;
                }
                let num_symbols = s.header.symbol;
                let mut syms = [0u16; 4];
                syms.copy_from_slice(&s.header.symbols_lists_array[..4]);
                let table = get_target_table(s, target, target_offset);
                let table_size = brotli_build_simple_huffman_table(
                    table,
                    HUFFMAN_TABLE_BITS as usize,
                    &mut syms,
                    num_symbols,
                );
                s.header.symbols_lists_array[..4].copy_from_slice(&syms);
                if let Some(sz) = opt_table_size.as_deref_mut() {
                    *sz = table_size;
                }
                s.header.substate_huffman = BrotliRunningHuffmanState::None;
                return BrotliDecoderErrorCode::Success;
            }
            BrotliRunningHuffmanState::Complex => {
                let res = read_code_length_code_lengths(s, input);
                if res != BrotliDecoderErrorCode::Success {
                    return res;
                }
                brotli_build_code_lengths_huffman_table(
                    &mut s.header.table,
                    &s.header.code_length_code_lengths,
                    &s.header.code_length_histo,
                );
                s.header.code_length_histo = [0; 16];
                for i in 0..=BROTLI_HUFFMAN_MAX_CODE_LENGTH {
                    s.header.next_symbol[i] =
                        (i as i32) - ((BROTLI_HUFFMAN_MAX_CODE_LENGTH + 1) as i32);
                    let idx = (SYM_LIST_BASE + s.header.next_symbol[i] as isize) as usize;
                    s.header.symbols_lists_array[idx] = 0xFFFF;
                }
                s.header.symbol = 0;
                s.header.prev_code_len = crate::common::BROTLI_INITIAL_REPEATED_CODE_LENGTH;
                s.header.repeat = 0;
                s.header.repeat_code_len = 0;
                s.header.space = 32768;
                s.header.substate_huffman = BrotliRunningHuffmanState::LengthSymbols;
            }
            BrotliRunningHuffmanState::LengthSymbols => {
                let mut res = read_symbol_code_lengths(alphabet_size_limit, s, input);
                if res == BrotliDecoderErrorCode::NeedsMoreInput {
                    res = safe_read_symbol_code_lengths(alphabet_size_limit, s, input);
                }
                if res != BrotliDecoderErrorCode::Success {
                    return res;
                }
                if s.header.space != 0 {
                    return BrotliDecoderErrorCode::ErrorFormatHuffmanSpace;
                }
                let mut syms = s.header.symbols_lists_array;
                let mut histo = s.header.code_length_histo;
                let table = get_target_table(s, target, target_offset);
                let table_size = brotli_build_huffman_table(
                    table,
                    HUFFMAN_TABLE_BITS as usize,
                    &syms,
                    &mut histo,
                );
                let _ = &mut syms;
                s.header.code_length_histo = histo;
                if let Some(sz) = opt_table_size.as_deref_mut() {
                    *sz = table_size;
                }
                s.header.substate_huffman = BrotliRunningHuffmanState::None;
                return BrotliDecoderErrorCode::Success;
            }
        }
    }
}

#[inline(always)]
fn read_block_length(
    table: &[HuffmanCode],
    br: &mut BrotliBitReader,
    input: &[u8],
) -> u32 {
    let code = read_symbol(table, br, input) as usize;
    let range = K_BROTLI_PREFIX_CODE_RANGES[code];
    (range.offset as u32) + (br.read_bits_24(input, range.nbits as u32) as u32)
}

#[inline(always)]
fn safe_read_block_length(
    s: &mut BrotliDecoderState,
    tree_idx: usize,
    result: &mut u32,
    input: &[u8],
) -> bool {
    let table = &s.block_len_trees[tree_idx * BROTLI_HUFFMAN_MAX_SIZE_26..];
    let index = if s.substate_read_block_length == BrotliRunningReadBlockLengthState::None {
        let mut idx = 0u32;
        if !safe_read_symbol(table, &mut s.br, input, &mut idx) {
            return false;
        }
        idx
    } else {
        s.block_length_index
    };
    let range = K_BROTLI_PREFIX_CODE_RANGES[index as usize];
    let mut bits = 0u64;
    if !s.br.safe_read_bits(input, range.nbits as u32, &mut bits) {
        s.block_length_index = index;
        s.substate_read_block_length = BrotliRunningReadBlockLengthState::Suffix;
        return false;
    }
    *result = (range.offset as u32) + (bits as u32);
    s.substate_read_block_length = BrotliRunningReadBlockLengthState::None;
    true
}

#[inline(never)]
fn inverse_move_to_front_transform(v: &mut [u8], state: &mut BrotliDecoderState) {
    let upper_bound = state.mtf_upper_bound as usize;
    // mtf[0..4] is the -1..-4 slack (we place the 4-byte pattern starting at mtf[4..]).
    let mtf = &mut state.mtf;
    let mut pattern: [u8; 4] = [0, 1, 2, 3];
    let mut i = 0usize;
    loop {
        let base = 4 + i * 4;
        mtf[base..base + 4].copy_from_slice(&pattern);
        pattern[0] = pattern[0].wrapping_add(4);
        pattern[1] = pattern[1].wrapping_add(4);
        pattern[2] = pattern[2].wrapping_add(4);
        pattern[3] = pattern[3].wrapping_add(4);
        i += 1;
        if i > upper_bound {
            break;
        }
    }

    let mut new_upper = 0u32;
    for byte in v.iter_mut() {
        let mut index = *byte as usize;
        let value = mtf[4 + index];
        new_upper |= *byte as u32;
        *byte = value;
        mtf[3] = value;
        while index > 0 {
            mtf[4 + index] = mtf[4 + index - 1];
            index -= 1;
        }
        mtf[4] = mtf[3];
    }
    state.mtf_upper_bound = new_upper >> 2;
}

fn huffman_tree_group_decode(
    kind: TreeGroupKind,
    s: &mut BrotliDecoderState,
    input: &[u8],
) -> BrotliDecoderErrorCode {
    if s.header.substate_tree_group != BrotliRunningTreeGroupState::Loop {
        s.header.next_code_offset = 0;
        s.header.htree_index = 0;
        s.header.substate_tree_group = BrotliRunningTreeGroupState::Loop;
    }
    let (num_htrees, alphabet_size_max, alphabet_size_limit) = match kind {
        TreeGroupKind::Literal => (
            s.literal_hgroup.num_htrees as usize,
            s.literal_hgroup.alphabet_size_max as u32,
            s.literal_hgroup.alphabet_size_limit as u32,
        ),
        TreeGroupKind::InsertCopy => (
            s.insert_copy_hgroup.num_htrees as usize,
            s.insert_copy_hgroup.alphabet_size_max as u32,
            s.insert_copy_hgroup.alphabet_size_limit as u32,
        ),
        TreeGroupKind::Distance => (
            s.distance_hgroup.num_htrees as usize,
            s.distance_hgroup.alphabet_size_max as u32,
            s.distance_hgroup.alphabet_size_limit as u32,
        ),
    };
    while s.header.htree_index < num_htrees {
        let mut table_size = 0u32;
        let offset = s.header.next_code_offset;
        let res = read_huffman_code(
            alphabet_size_max,
            alphabet_size_limit,
            HuffmanTarget::TreeGroup(kind),
            offset,
            Some(&mut table_size),
            s,
            input,
        );
        if res != BrotliDecoderErrorCode::Success {
            return res;
        }
        let htree_idx = s.header.htree_index;
        match kind {
            TreeGroupKind::Literal => s.literal_hgroup.htree_offsets[htree_idx] = offset,
            TreeGroupKind::InsertCopy => s.insert_copy_hgroup.htree_offsets[htree_idx] = offset,
            TreeGroupKind::Distance => s.distance_hgroup.htree_offsets[htree_idx] = offset,
        }
        s.header.next_code_offset += table_size as usize;
        s.header.htree_index += 1;
    }
    s.header.substate_tree_group = BrotliRunningTreeGroupState::None;
    BrotliDecoderErrorCode::Success
}

fn decode_context_map(
    context_map_size: u32,
    is_dist: bool,
    s: &mut BrotliDecoderState,
    input: &[u8],
) -> BrotliDecoderErrorCode {
    loop {
        match s.header.substate_context_map {
            BrotliRunningContextMapState::None => {
                let mut ntrees = if is_dist {
                    s.num_dist_htrees
                } else {
                    s.num_literal_htrees
                };
                let res = decode_var_len_uint8(s, input, &mut ntrees);
                if is_dist {
                    s.num_dist_htrees = ntrees;
                } else {
                    s.num_literal_htrees = ntrees;
                }
                if res != BrotliDecoderErrorCode::Success {
                    return res;
                }
                ntrees += 1;
                if is_dist {
                    s.num_dist_htrees = ntrees;
                    s.dist_context_map = vec![0u8; context_map_size as usize];
                } else {
                    s.num_literal_htrees = ntrees;
                    s.context_map = vec![0u8; context_map_size as usize];
                }
                s.header.context_index = 0;
                if ntrees <= 1 {
                    return BrotliDecoderErrorCode::Success;
                }
                s.header.substate_context_map = BrotliRunningContextMapState::ReadPrefix;
            }
            BrotliRunningContextMapState::ReadPrefix => {
                let mut bits = 0u64;
                if !s.br.safe_get_bits(input, 5, &mut bits) {
                    return BrotliDecoderErrorCode::NeedsMoreInput;
                }
                if (bits & 1) != 0 {
                    s.header.max_run_length_prefix = ((bits >> 1) + 1) as u32;
                    s.br.drop_bits(5);
                } else {
                    s.header.max_run_length_prefix = 0;
                    s.br.drop_bits(1);
                }
                s.header.substate_context_map = BrotliRunningContextMapState::Huffman;
            }
            BrotliRunningContextMapState::Huffman => {
                let ntrees = if is_dist {
                    s.num_dist_htrees
                } else {
                    s.num_literal_htrees
                };
                let alphabet_size = ntrees + s.header.max_run_length_prefix;
                let res = read_huffman_code(
                    alphabet_size,
                    alphabet_size,
                    HuffmanTarget::ContextMap,
                    0,
                    None,
                    s,
                    input,
                );
                if res != BrotliDecoderErrorCode::Success {
                    return res;
                }
                s.header.code = 0xFFFF;
                s.header.substate_context_map = BrotliRunningContextMapState::Decode;
            }
            BrotliRunningContextMapState::Decode => {
                let mut context_index = s.header.context_index;
                let max_run_length_prefix = s.header.max_run_length_prefix;
                let mut code = s.header.code;
                let mut skip_preamble = code != 0xFFFF;
                while context_index < context_map_size || skip_preamble {
                    if !skip_preamble {
                        if !safe_read_symbol(
                            &s.header.context_map_table,
                            &mut s.br,
                            input,
                            &mut code,
                        ) {
                            s.header.code = 0xFFFF;
                            s.header.context_index = context_index;
                            return BrotliDecoderErrorCode::NeedsMoreInput;
                        }
                        if code == 0 {
                            let map = if is_dist {
                                &mut s.dist_context_map
                            } else {
                                &mut s.context_map
                            };
                            map[context_index as usize] = 0;
                            context_index += 1;
                            continue;
                        }
                        if code > max_run_length_prefix {
                            let map = if is_dist {
                                &mut s.dist_context_map
                            } else {
                                &mut s.context_map
                            };
                            map[context_index as usize] = (code - max_run_length_prefix) as u8;
                            context_index += 1;
                            continue;
                        }
                    } else {
                        skip_preamble = false;
                    }
                    let mut reps = 0u64;
                    if !s.br.safe_read_bits(input, code, &mut reps) {
                        s.header.code = code;
                        s.header.context_index = context_index;
                        return BrotliDecoderErrorCode::NeedsMoreInput;
                    }
                    let reps = (reps as u32) + (1u32 << code);
                    if context_index + reps > context_map_size {
                        return BrotliDecoderErrorCode::ErrorFormatContextMapRepeat;
                    }
                    let map = if is_dist {
                        &mut s.dist_context_map
                    } else {
                        &mut s.context_map
                    };
                    for k in 0..reps {
                        map[(context_index + k) as usize] = 0;
                    }
                    context_index += reps;
                }
                s.header.substate_context_map = BrotliRunningContextMapState::Transform;
            }
            BrotliRunningContextMapState::Transform => {
                let mut bits = 0u64;
                if !s.br.safe_read_bits(input, 1, &mut bits) {
                    return BrotliDecoderErrorCode::NeedsMoreInput;
                }
                if bits != 0 {
                    let mut map = if is_dist {
                        std::mem::take(&mut s.dist_context_map)
                    } else {
                        std::mem::take(&mut s.context_map)
                    };
                    inverse_move_to_front_transform(&mut map[..context_map_size as usize], s);
                    if is_dist {
                        s.dist_context_map = map;
                    } else {
                        s.context_map = map;
                    }
                }
                s.header.substate_context_map = BrotliRunningContextMapState::None;
                return BrotliDecoderErrorCode::Success;
            }
        }
    }
}

#[inline(always)]
fn decode_block_type_and_length(
    safe: bool,
    s: &mut BrotliDecoderState,
    tree_type: usize,
    input: &[u8],
) -> BrotliDecoderErrorCode {
    let max_block_type = s.num_block_types[tree_type];
    if max_block_type <= 1 {
        return BrotliDecoderErrorCode::ErrorFormatBlockSwitch;
    }

    let mut block_type: u32;
    if !safe {
        let type_tree = &s.block_type_trees[tree_type * BROTLI_HUFFMAN_MAX_SIZE_258..];
        let len_tree = &s.block_len_trees[tree_type * BROTLI_HUFFMAN_MAX_SIZE_26..];
        block_type = read_symbol(type_tree, &mut s.br, input);
        s.block_length[tree_type] = read_block_length(len_tree, &mut s.br, input);
    } else {
        let memento = s.br.save_state();
        let mut bt = 0u32;
        let ok = {
            let type_tree = &s.block_type_trees[tree_type * BROTLI_HUFFMAN_MAX_SIZE_258..];
            safe_read_symbol(type_tree, &mut s.br, input, &mut bt)
        };
        if !ok {
            return BrotliDecoderErrorCode::NeedsMoreInput;
        }
        block_type = bt;
        let mut bl = s.block_length[tree_type];
        if !safe_read_block_length(s, tree_type, &mut bl, input) {
            s.substate_read_block_length = BrotliRunningReadBlockLengthState::None;
            s.br.restore_state(&memento);
            return BrotliDecoderErrorCode::NeedsMoreInput;
        }
        s.block_length[tree_type] = bl;
    }

    let rb = &mut s.block_type_rb[tree_type * 2..tree_type * 2 + 2];
    if block_type == 1 {
        block_type = rb[1] + 1;
    } else if block_type == 0 {
        block_type = rb[0];
    } else {
        block_type -= 2;
    }
    if block_type >= max_block_type {
        block_type -= max_block_type;
    }
    rb[0] = rb[1];
    rb[1] = block_type;
    BrotliDecoderErrorCode::Success
}

#[inline]
fn detect_trivial_literal_block_types(s: &mut BrotliDecoderState) {
    s.trivial_literal_contexts = [0; 8];
    for i in 0..(s.num_block_types[0] as usize) {
        let offset = i << BROTLI_LITERAL_CONTEXT_BITS;
        let sample = s.context_map[offset];
        let mut error = 0u8;
        for j in 0..(1usize << BROTLI_LITERAL_CONTEXT_BITS) {
            error |= s.context_map[offset + j] ^ sample;
        }
        if error == 0 {
            s.trivial_literal_contexts[i >> 5] |= 1u32 << (i & 31);
        }
    }
}

#[inline]
fn prepare_literal_decoding(s: &mut BrotliDecoderState) {
    let block_type = s.block_type_rb[1] as usize;
    let context_offset = block_type << BROTLI_LITERAL_CONTEXT_BITS;
    s.context_map_slice_offset = context_offset;
    let trivial = s.trivial_literal_contexts[block_type >> 5];
    s.trivial_literal_context = (trivial >> (block_type & 31)) & 1;
    let htree_id = s.context_map[context_offset] as usize;
    s.literal_htree_idx = s.literal_hgroup.htree_offsets[htree_id];
    let context_mode = s.context_modes[block_type] & 3;
    s.context_lookup = Some(brotli_context_lut(crate::common::ContextType::from_u8(
        context_mode,
    )));
}

#[inline]
fn decode_literal_block_switch(
    safe: bool,
    s: &mut BrotliDecoderState,
    input: &[u8],
) -> BrotliDecoderErrorCode {
    let res = decode_block_type_and_length(safe, s, 0, input);
    if res != BrotliDecoderErrorCode::Success {
        return res;
    }
    prepare_literal_decoding(s);
    BrotliDecoderErrorCode::Success
}

#[inline]
fn decode_command_block_switch(
    safe: bool,
    s: &mut BrotliDecoderState,
    input: &[u8],
) -> BrotliDecoderErrorCode {
    let res = decode_block_type_and_length(safe, s, 1, input);
    if res != BrotliDecoderErrorCode::Success {
        return res;
    }
    s.htree_command_idx = s.insert_copy_hgroup.htree_offsets[s.block_type_rb[3] as usize];
    BrotliDecoderErrorCode::Success
}

#[inline]
fn decode_distance_block_switch(
    safe: bool,
    s: &mut BrotliDecoderState,
    input: &[u8],
) -> BrotliDecoderErrorCode {
    let res = decode_block_type_and_length(safe, s, 2, input);
    if res != BrotliDecoderErrorCode::Success {
        return res;
    }
    s.dist_context_map_slice_offset = (s.block_type_rb[5] as usize) << BROTLI_DISTANCE_CONTEXT_BITS;
    s.dist_htree_index =
        s.dist_context_map[s.dist_context_map_slice_offset + (s.distance_context as usize)];
    BrotliDecoderErrorCode::Success
}

#[inline]
fn unwritten_bytes(s: &BrotliDecoderState, wrap: bool) -> usize {
    let pos = if wrap && s.pos > s.ringbuffer_size {
        s.ringbuffer_size as usize
    } else {
        s.pos as usize
    };
    let partial_pos_rb = (s.rb_roundtrips * (s.ringbuffer_size as usize)) + pos;
    partial_pos_rb - s.partial_pos_out
}

#[inline(never)]
fn write_ring_buffer(
    s: &mut BrotliDecoderState,
    output: Option<(&mut [u8], &mut usize)>,
    force: bool,
) -> BrotliDecoderErrorCode {
    let start = s.partial_pos_out & (s.ringbuffer_mask as usize);
    let to_write = unwritten_bytes(s, true);
    let num_written = if let Some((out_buf, out_pos)) = output {
        let avail = out_buf.len() - *out_pos;
        let n = avail.min(to_write);
        if s.meta_block_remaining_len < 0 {
            return BrotliDecoderErrorCode::ErrorFormatBlockLength1;
        }
        if n > 0 {
            out_buf[*out_pos..*out_pos + n].copy_from_slice(&s.ringbuffer[start..start + n]);
            *out_pos += n;
        }
        n
    } else {
        if s.meta_block_remaining_len < 0 {
            return BrotliDecoderErrorCode::ErrorFormatBlockLength1;
        }
        0
    };
    s.partial_pos_out += num_written;
    if num_written < to_write {
        if s.ringbuffer_size == (1 << s.window_bits) || force {
            return BrotliDecoderErrorCode::NeedsMoreOutput;
        } else {
            return BrotliDecoderErrorCode::Success;
        }
    }
    if s.ringbuffer_size == (1 << s.window_bits) && s.pos >= s.ringbuffer_size {
        s.pos -= s.ringbuffer_size;
        s.rb_roundtrips += 1;
        s.should_wrap_ringbuffer = s.pos != 0;
    }
    BrotliDecoderErrorCode::Success
}

#[inline(never)]
fn wrap_ring_buffer(s: &mut BrotliDecoderState) {
    if s.should_wrap_ringbuffer {
        let rb_size = s.ringbuffer_size as usize;
        let count = s.pos as usize;
        s.ringbuffer.copy_within(rb_size..rb_size + count, 0);
        s.should_wrap_ringbuffer = false;
    }
}

#[inline(never)]
fn brotli_ensure_ring_buffer(s: &mut BrotliDecoderState) -> bool {
    if s.ringbuffer_size == s.new_ringbuffer_size {
        return true;
    }
    let new_len = (s.new_ringbuffer_size as usize) + K_RING_BUFFER_WRITE_AHEAD_SLACK;
    s.ringbuffer.resize(new_len, 0);
    s.ringbuffer[s.new_ringbuffer_size as usize - 2] = 0;
    s.ringbuffer[s.new_ringbuffer_size as usize - 1] = 0;

    s.ringbuffer_size = s.new_ringbuffer_size;
    s.ringbuffer_mask = s.new_ringbuffer_size - 1;
    true
}

fn skip_metadata_block(s: &mut BrotliDecoderState, input: &[u8]) -> BrotliDecoderErrorCode {
    if s.meta_block_remaining_len == 0 {
        return BrotliDecoderErrorCode::Success;
    }
    debug_assert!((s.br.get_available_bits() & 7) == 0);

    if s.br.get_available_bits() >= 8 {
        let mut buffer = [0u8; 8];
        let mut nbytes = (s.br.get_available_bits() >> 3) as i32;
        if nbytes > s.meta_block_remaining_len {
            nbytes = s.meta_block_remaining_len;
        }
        s.br.copy_bytes(input, &mut buffer[..nbytes as usize]);
        if let Some(ref mut cb) = s.metadata_chunk_func {
            cb(&buffer[..nbytes as usize]);
        }
        s.meta_block_remaining_len -= nbytes;
        if s.meta_block_remaining_len == 0 {
            return BrotliDecoderErrorCode::Success;
        }
    }

    let mut nbytes = s.br.get_remaining_bytes() as i32;
    if nbytes > s.meta_block_remaining_len {
        nbytes = s.meta_block_remaining_len;
    }
    if nbytes > 0 {
        let start = s.br.next_in;
        let end = start + (nbytes as usize);
        if let Some(ref mut cb) = s.metadata_chunk_func {
            cb(&input[start..end]);
        }
        s.br.drop_bytes(nbytes as usize);
        s.meta_block_remaining_len -= nbytes;
        if s.meta_block_remaining_len == 0 {
            return BrotliDecoderErrorCode::Success;
        }
    }

    BrotliDecoderErrorCode::NeedsMoreInput
}

fn copy_uncompressed_block_to_output(
    s: &mut BrotliDecoderState,
    input: &[u8],
    mut output: Option<(&mut [u8], &mut usize)>,
) -> BrotliDecoderErrorCode {
    if !brotli_ensure_ring_buffer(s) {
        return BrotliDecoderErrorCode::ErrorAllocRingBuffer1;
    }
    loop {
        match s.substate_uncompressed {
            BrotliRunningUncompressedState::None => {
                let mut nbytes = s.br.get_remaining_bytes() as i32;
                if nbytes > s.meta_block_remaining_len {
                    nbytes = s.meta_block_remaining_len;
                }
                if s.pos + nbytes > s.ringbuffer_size {
                    nbytes = s.ringbuffer_size - s.pos;
                }
                let pos = s.pos as usize;
                s.br.copy_bytes(input, &mut s.ringbuffer[pos..pos + (nbytes as usize)]);
                s.pos += nbytes;
                s.meta_block_remaining_len -= nbytes;
                if s.pos < (1 << s.window_bits) {
                    if s.meta_block_remaining_len == 0 {
                        return BrotliDecoderErrorCode::Success;
                    }
                    return BrotliDecoderErrorCode::NeedsMoreInput;
                }
                s.substate_uncompressed = BrotliRunningUncompressedState::Write;
            }
            BrotliRunningUncompressedState::Write => {
                let out_reborrow = output.as_mut().map(|(b, p)| (&mut **b, &mut **p));
                let res = write_ring_buffer(s, out_reborrow, false);
                if res != BrotliDecoderErrorCode::Success {
                    return res;
                }
                if s.ringbuffer_size == (1 << s.window_bits) {
                    s.max_distance = s.max_backward_distance;
                }
                s.substate_uncompressed = BrotliRunningUncompressedState::None;
            }
        }
    }
}

fn attach_compound_dictionary(
    state: &mut BrotliDecoderState,
    data: std::sync::Arc<[u8]>,
) -> bool {
    let size = data.len();
    if size == 0 {
        return true;
    }
    if size > SHARED_BROTLI_MAX_RAW_DICT_SIZE {
        return false;
    }
    if state.state != BrotliRunningState::Uninited {
        return false;
    }
    let addon = state
        .compound_dictionary
        .get_or_insert_with(BrotliDecoderCompoundDictionary::default);
    if (addon.num_chunks as usize) == SHARED_BROTLI_MAX_COMPOUND_DICTS {
        return false;
    }
    if size > SHARED_BROTLI_MAX_RAW_DICT_SIZE - (addon.total_size as usize) {
        return false;
    }
    let idx = addon.num_chunks as usize;
    addon.chunks[idx] = Some(data);
    addon.num_chunks += 1;
    addon.total_size += size as u32;
    addon.chunk_offsets[addon.num_chunks as usize] = addon.total_size;
    true
}

fn ensure_compound_dictionary_initialized(addon: &mut BrotliDecoderCompoundDictionary) {
    if addon.block_bits != 255 {
        return;
    }
    let mut block_bits = 8u32;
    let maximal_address = addon.total_size - 1;
    while (maximal_address >> block_bits) != 0 {
        block_bits += 1;
    }
    block_bits -= 8;
    addon.block_bits = block_bits as u8;
    let mut cursor = 0u32;
    let mut index = 0usize;
    while cursor <= maximal_address {
        while addon.chunk_offsets[index + 1] < cursor {
            index += 1;
        }
        addon.block_map[(cursor >> block_bits) as usize] = index as u8;
        cursor = cursor.saturating_add(1u32 << block_bits);
        if cursor == 0 {
            break;
        }
    }
}

fn initialize_compound_dictionary_copy(
    s: &mut BrotliDecoderState,
    address: u32,
    length: u32,
) -> bool {
    let addon = s.compound_dictionary.as_mut().unwrap();
    ensure_compound_dictionary_initialized(addon);
    let mut index = addon.block_map[(address >> addon.block_bits) as usize] as usize;
    while address >= addon.chunk_offsets[index + 1] {
        index += 1;
    }
    if length > addon.total_size - address {
        return false;
    }
    s.dist_rb[(s.dist_rb_idx & 3) as usize] = s.distance_code;
    s.dist_rb_idx += 1;
    s.meta_block_remaining_len -= length as i32;
    addon.br_index = index as u16;
    addon.br_offset = address - addon.chunk_offsets[index];
    addon.br_length = length;
    addon.br_copied = 0;
    true
}

#[inline]
fn get_compound_dictionary_size(s: &BrotliDecoderState) -> u32 {
    s.compound_dictionary
        .as_ref()
        .map_or(0, |a| a.total_size)
}

fn copy_from_compound_dictionary(s: &mut BrotliDecoderState, mut pos: i32) -> i32 {
    let addon = s.compound_dictionary.as_mut().unwrap();
    let orig_pos = pos;
    while addon.br_length != addon.br_copied {
        let idx = addon.br_index as usize;
        let chunk = addon.chunks[idx].as_ref().unwrap();
        let space = (s.ringbuffer_size - pos) as u32;
        let rem_chunk_length =
            (addon.chunk_offsets[idx + 1] - addon.chunk_offsets[idx]) - addon.br_offset;
        let mut length = addon.br_length - addon.br_copied;
        if length > rem_chunk_length {
            length = rem_chunk_length;
        }
        if length > space {
            length = space;
        }
        let dst = pos as usize;
        let src = addon.br_offset as usize;
        let len = length as usize;
        s.ringbuffer[dst..dst + len].copy_from_slice(&chunk[src..src + len]);
        pos += length as i32;
        addon.br_offset += length;
        addon.br_copied += length;
        if length == rem_chunk_length {
            addon.br_index += 1;
            addon.br_offset = 0;
        }
        if pos == s.ringbuffer_size {
            break;
        }
    }
    pos - orig_pos
}

#[inline(never)]
fn brotli_calculate_ring_buffer_size(s: &mut BrotliDecoderState) {
    let window_size = 1i32 << s.window_bits;
    let mut new_ringbuffer_size = window_size;
    let mut min_size = if s.ringbuffer_size != 0 {
        s.ringbuffer_size
    } else {
        1024
    };
    if s.ringbuffer_size == window_size {
        return;
    }
    if s.is_metadata {
        return;
    }
    let output_size = if s.ringbuffer.is_empty() {
        0
    } else {
        s.pos
    } + s.meta_block_remaining_len;
    if min_size < output_size {
        min_size = output_size;
    }
    if s.canny_ringbuffer_allocation {
        while (new_ringbuffer_size >> 1) >= min_size {
            new_ringbuffer_size >>= 1;
        }
    }
    s.new_ringbuffer_size = new_ringbuffer_size;
}

fn read_context_modes(s: &mut BrotliDecoderState, input: &[u8]) -> BrotliDecoderErrorCode {
    let mut i = s.loop_counter;
    while i < s.num_block_types[0] as i32 {
        let mut bits = 0u64;
        if !s.br.safe_read_bits(input, 2, &mut bits) {
            s.loop_counter = i;
            return BrotliDecoderErrorCode::NeedsMoreInput;
        }
        s.context_modes[i as usize] = bits as u8;
        i += 1;
    }
    BrotliDecoderErrorCode::Success
}

#[inline(always)]
fn take_distance_from_ring_buffer(s: &mut BrotliDecoderState) {
    let offset = s.distance_code - 3;
    if s.distance_code <= 3 {
        s.distance_context = 1 >> s.distance_code;
        s.distance_code = s.dist_rb[((s.dist_rb_idx - offset) & 3) as usize];
        s.dist_rb_idx -= s.distance_context;
    } else {
        let mut index_delta = 3;
        let base = if s.distance_code < 10 {
            s.distance_code - 4
        } else {
            index_delta = 2;
            s.distance_code - 10
        };
        let delta = ((0x605142i32 >> (4 * base)) & 0xF) - 3;
        s.distance_code = s.dist_rb[((s.dist_rb_idx + index_delta) & 3) as usize] + delta;
        if s.distance_code <= 0 {
            s.distance_code = 0x7FFFFFFF;
        }
    }
}

fn calculate_distance_lut(s: &mut BrotliDecoderState) {
    let npostfix = s.distance_postfix_bits;
    let ndirect = s.num_direct_distance_codes;
    let alphabet_size_limit = s.distance_hgroup.alphabet_size_limit as u32;
    let postfix = 1u32 << npostfix;
    let mut bits = 1u32;
    let mut half = 0u32;
    let mut i = BROTLI_NUM_DISTANCE_SHORT_CODES;

    for j in 0..ndirect {
        s.body.dist_extra_bits[i] = 0;
        s.body.dist_offset[i] = j + 1;
        i += 1;
    }

    while (i as u32) < alphabet_size_limit {
        let base = ndirect + ((((2 + half) << bits) - 4) << npostfix) + 1;
        for j in 0..postfix {
            s.body.dist_extra_bits[i] = bits as u8;
            s.body.dist_offset[i] = base + j;
            i += 1;
        }
        bits += half;
        half ^= 1;
    }
}

#[inline(always)]
fn read_distance_internal(
    safe: bool,
    s: &mut BrotliDecoderState,
    input: &[u8],
) -> bool {
    let dist_tree =
        &s.distance_hgroup.codes[s.distance_hgroup.htree_offsets[s.dist_htree_index as usize]..];
    let code: u32;
    let mut memento = s.br.save_state();
    if !safe {
        code = read_symbol(dist_tree, &mut s.br, input);
    } else {
        memento = s.br.save_state();
        let mut c = 0u32;
        if !safe_read_symbol(dist_tree, &mut s.br, input, &mut c) {
            return false;
        }
        code = c;
    }
    s.block_length[2] -= 1;
    s.distance_context = 0;
    if (code & !0xFu32) == 0 {
        s.distance_code = code as i32;
        take_distance_from_ring_buffer(s);
        return true;
    }
    let extra_bits = s.body.dist_extra_bits[code as usize] as u32;
    let bits: u64;
    if !safe {
        bits = if extra_bits != 0 {
            s.br.read_bits_32(input, extra_bits)
        } else {
            0
        };
    } else {
        let mut b = 0u64;
        let ok = if extra_bits != 0 {
            s.br.safe_read_bits_32(input, extra_bits, &mut b)
        } else {
            true
        };
        if !ok {
            s.block_length[2] += 1;
            s.br.restore_state(&memento);
            return false;
        }
        bits = b;
    }
    s.distance_code =
        (s.body.dist_offset[code as usize] + ((bits as u32) << s.distance_postfix_bits)) as i32;
    true
}

#[inline(always)]
fn read_command_internal(
    safe: bool,
    s: &mut BrotliDecoderState,
    input: &[u8],
    insert_length: &mut i32,
) -> bool {
    let cmd_tree = &s.insert_copy_hgroup.codes[s.htree_command_idx..];
    let cmd_code: u32;
    let mut memento = s.br.save_state();
    if !safe {
        cmd_code = read_symbol(cmd_tree, &mut s.br, input);
    } else {
        memento = s.br.save_state();
        let mut c = 0u32;
        if !safe_read_symbol(cmd_tree, &mut s.br, input, &mut c) {
            return false;
        }
        cmd_code = c;
    }
    let v = K_CMD_LUT[cmd_code as usize];
    s.distance_code = v.distance_code as i32;
    s.distance_context = v.context as i32;
    s.dist_htree_index =
        s.dist_context_map[s.dist_context_map_slice_offset + (s.distance_context as usize)];
    *insert_length = v.insert_len_offset as i32;
    let mut insert_len_extra = 0u64;
    let mut copy_length = 0u64;
    if !safe {
        if v.insert_len_extra_bits != 0 {
            insert_len_extra = s.br.read_bits_24(input, v.insert_len_extra_bits as u32);
        }
        if v.copy_len_extra_bits != 0 {
            copy_length = s.br.read_bits_24(input, v.copy_len_extra_bits as u32);
        }
    } else {
        let ok1 = if v.insert_len_extra_bits != 0 {
            s.br.safe_read_bits(input, v.insert_len_extra_bits as u32, &mut insert_len_extra)
        } else {
            true
        };
        let ok2 = ok1
            && if v.copy_len_extra_bits != 0 {
                s.br.safe_read_bits(input, v.copy_len_extra_bits as u32, &mut copy_length)
            } else {
                true
            };
        if !ok2 {
            s.br.restore_state(&memento);
            return false;
        }
    }
    s.copy_length = (copy_length as i32) + (v.copy_len_offset as i32);
    s.block_length[1] -= 1;
    *insert_length += insert_len_extra as i32;
    true
}

#[inline(always)]
fn check_input_amount(safe: bool, br: &BrotliBitReader) -> bool {
    if safe {
        true
    } else {
        br.check_input_amount()
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum CmdPhase {
    Begin,
    Inner,
    PostDecodeLiterals,
    PostWrapCopy,
}

fn process_commands_internal(
    safe: bool,
    s: &mut BrotliDecoderState,
    input: &[u8],
) -> BrotliDecoderErrorCode {
    let mut pos = s.pos;
    let mut i = s.loop_counter;
    let compound_dictionary_size = get_compound_dictionary_size(s);

    if !check_input_amount(safe, &s.br) {
        s.pos = pos;
        s.loop_counter = i;
        return BrotliDecoderErrorCode::NeedsMoreInput;
    }
    if !safe {
        let _ = s.br.warmup(input);
    }

    let mut phase = match s.state {
        BrotliRunningState::CommandBegin => CmdPhase::Begin,
        BrotliRunningState::CommandInner => CmdPhase::Inner,
        BrotliRunningState::CommandPostDecodeLiterals => CmdPhase::PostDecodeLiterals,
        BrotliRunningState::CommandPostWrapCopy => CmdPhase::PostWrapCopy,
        _ => return BrotliDecoderErrorCode::ErrorUnreachable,
    };

    'outer: loop {
        match phase {
            CmdPhase::Begin => {
                if safe {
                    s.state = BrotliRunningState::CommandBegin;
                }
                if !check_input_amount(safe, &s.br) {
                    s.state = BrotliRunningState::CommandBegin;
                    s.pos = pos;
                    s.loop_counter = i;
                    return BrotliDecoderErrorCode::NeedsMoreInput;
                }
                if s.block_length[1] == 0 {
                    let status = decode_command_block_switch(safe, s, input);
                    if status != BrotliDecoderErrorCode::Success {
                        s.pos = pos;
                        s.loop_counter = i;
                        return status;
                    }
                    continue 'outer;
                }
                if !read_command_internal(safe, s, input, &mut i) {
                    s.pos = pos;
                    s.loop_counter = i;
                    return BrotliDecoderErrorCode::NeedsMoreInput;
                }
                if i == 0 {
                    phase = CmdPhase::PostDecodeLiterals;
                    continue 'outer;
                }
                s.meta_block_remaining_len -= i;
                phase = CmdPhase::Inner;
            }
            CmdPhase::Inner => {
                if safe {
                    s.state = BrotliRunningState::CommandInner;
                }
                'lit_loop: loop {
                    if s.trivial_literal_context != 0 {
                        let mut bits = 0u32;
                        let mut value = 0u32;
                        preload_symbol(
                            safe,
                            &s.literal_hgroup.codes[s.literal_htree_idx..],
                            &mut s.br,
                            input,
                            &mut bits,
                            &mut value,
                        );
                        if !safe {
                            let mut num_steps = i - 1;
                            if num_steps > 0 && (num_steps as u32) > s.block_length[0] {
                                num_steps = s.block_length[0] as i32;
                            }
                            if s.ringbuffer_size >= pos && (s.ringbuffer_size - pos) <= num_steps {
                                num_steps = s.ringbuffer_size - pos - 1;
                            }
                            if num_steps < 0 {
                                num_steps = 0;
                            }
                            let copied = copy_preloaded_symbols_to_u8(
                                &s.literal_hgroup.codes[s.literal_htree_idx..],
                                &mut s.br,
                                input,
                                &mut bits,
                                &mut value,
                                &mut s.ringbuffer,
                                pos as usize,
                                num_steps as usize,
                            ) as i32;
                            pos += copied;
                            s.block_length[0] -= copied as u32;
                            i -= copied;
                            loop {
                                if !check_input_amount(safe, &s.br) {
                                    s.state = BrotliRunningState::CommandInner;
                                    s.pos = pos;
                                    s.loop_counter = i;
                                    return BrotliDecoderErrorCode::NeedsMoreInput;
                                }
                                if s.block_length[0] == 0 {
                                    let status = decode_literal_block_switch(safe, s, input);
                                    if status != BrotliDecoderErrorCode::Success {
                                        s.pos = pos;
                                        s.loop_counter = i;
                                        return status;
                                    }
                                    continue 'lit_loop;
                                }
                                let sym = read_preloaded_symbol(
                                    &s.literal_hgroup.codes[s.literal_htree_idx..],
                                    &mut s.br,
                                    input,
                                    &mut bits,
                                    &mut value,
                                );
                                s.ringbuffer[pos as usize] = sym as u8;
                                s.block_length[0] -= 1;
                                pos += 1;
                                if pos == s.ringbuffer_size {
                                    s.state = BrotliRunningState::CommandInnerWrite;
                                    i -= 1;
                                    s.pos = pos;
                                    s.loop_counter = i;
                                    return BrotliDecoderErrorCode::Success;
                                }
                                i -= 1;
                                if i == 0 {
                                    break;
                                }
                            }
                        } else {
                            loop {
                                if s.block_length[0] == 0 {
                                    let status = decode_literal_block_switch(safe, s, input);
                                    if status != BrotliDecoderErrorCode::Success {
                                        s.pos = pos;
                                        s.loop_counter = i;
                                        return status;
                                    }
                                    continue 'lit_loop;
                                }
                                let mut literal = 0u32;
                                if !safe_read_symbol(
                                    &s.literal_hgroup.codes[s.literal_htree_idx..],
                                    &mut s.br,
                                    input,
                                    &mut literal,
                                ) {
                                    s.pos = pos;
                                    s.loop_counter = i;
                                    return BrotliDecoderErrorCode::NeedsMoreInput;
                                }
                                s.ringbuffer[pos as usize] = literal as u8;
                                s.block_length[0] -= 1;
                                pos += 1;
                                if pos == s.ringbuffer_size {
                                    s.state = BrotliRunningState::CommandInnerWrite;
                                    i -= 1;
                                    s.pos = pos;
                                    s.loop_counter = i;
                                    return BrotliDecoderErrorCode::Success;
                                }
                                i -= 1;
                                if i == 0 {
                                    break;
                                }
                            }
                        }
                    } else {
                        let mut p1 = s.ringbuffer[((pos - 1) & s.ringbuffer_mask) as usize];
                        let mut p2 = s.ringbuffer[((pos - 2) & s.ringbuffer_mask) as usize];
                        loop {
                            if !check_input_amount(safe, &s.br) {
                                s.state = BrotliRunningState::CommandInner;
                                s.pos = pos;
                                s.loop_counter = i;
                                return BrotliDecoderErrorCode::NeedsMoreInput;
                            }
                            if s.block_length[0] == 0 {
                                let status = decode_literal_block_switch(safe, s, input);
                                if status != BrotliDecoderErrorCode::Success {
                                    s.pos = pos;
                                    s.loop_counter = i;
                                    return status;
                                }
                                continue 'lit_loop;
                            }
                            let lut = s.context_lookup.unwrap();
                            let context = brotli_context(p1, p2, lut) as usize;
                            let htree_id =
                                s.context_map[s.context_map_slice_offset + context] as usize;
                            let hc_offset = s.literal_hgroup.htree_offsets[htree_id];
                            let hc = &s.literal_hgroup.codes[hc_offset..];
                            p2 = p1;
                            if !safe {
                                p1 = read_symbol(hc, &mut s.br, input) as u8;
                            } else {
                                let mut literal = 0u32;
                                if !safe_read_symbol(hc, &mut s.br, input, &mut literal) {
                                    s.pos = pos;
                                    s.loop_counter = i;
                                    return BrotliDecoderErrorCode::NeedsMoreInput;
                                }
                                p1 = literal as u8;
                            }
                            s.ringbuffer[pos as usize] = p1;
                            s.block_length[0] -= 1;
                            pos += 1;
                            if pos == s.ringbuffer_size {
                                s.state = BrotliRunningState::CommandInnerWrite;
                                i -= 1;
                                s.pos = pos;
                                s.loop_counter = i;
                                return BrotliDecoderErrorCode::Success;
                            }
                            i -= 1;
                            if i == 0 {
                                break;
                            }
                        }
                    }
                    break 'lit_loop;
                }

                if s.meta_block_remaining_len <= 0 {
                    s.state = BrotliRunningState::MetablockDone;
                    s.pos = pos;
                    s.loop_counter = i;
                    return BrotliDecoderErrorCode::Success;
                }
                phase = CmdPhase::PostDecodeLiterals;
            }
            CmdPhase::PostDecodeLiterals => {
                if safe {
                    s.state = BrotliRunningState::CommandPostDecodeLiterals;
                }
                if s.distance_code >= 0 {
                    s.distance_context = if s.distance_code != 0 { 0 } else { 1 };
                    s.dist_rb_idx -= 1;
                    s.distance_code = s.dist_rb[(s.dist_rb_idx & 3) as usize];
                } else {
                    if s.block_length[2] == 0 {
                        let status = decode_distance_block_switch(safe, s, input);
                        if status != BrotliDecoderErrorCode::Success {
                            s.pos = pos;
                            s.loop_counter = i;
                            return status;
                        }
                    }
                    if !read_distance_internal(safe, s, input) {
                        s.pos = pos;
                        s.loop_counter = i;
                        return BrotliDecoderErrorCode::NeedsMoreInput;
                    }
                }
                if s.max_distance != s.max_backward_distance {
                    s.max_distance = pos.min(s.max_backward_distance);
                }
                i = s.copy_length;
                if s.distance_code > s.max_distance {
                    if (s.distance_code as u32) > (BROTLI_MAX_ALLOWED_DISTANCE as u32) {
                        s.pos = pos;
                        s.loop_counter = i;
                        return BrotliDecoderErrorCode::ErrorFormatDistance;
                    }
                    if ((s.distance_code - s.max_distance) as u32).wrapping_sub(1)
                        < compound_dictionary_size
                    {
                        let address = compound_dictionary_size
                            - ((s.distance_code - s.max_distance) as u32);
                        if !initialize_compound_dictionary_copy(s, address, i as u32) {
                            s.pos = pos;
                            s.loop_counter = i;
                            return BrotliDecoderErrorCode::ErrorCompoundDictionary;
                        }
                        pos += copy_from_compound_dictionary(s, pos);
                        if pos >= s.ringbuffer_size {
                            s.state = BrotliRunningState::CommandPostWrite1;
                            s.pos = pos;
                            s.loop_counter = i;
                            return BrotliDecoderErrorCode::Success;
                        }
                    } else if (i as usize) >= SHARED_BROTLI_MIN_DICTIONARY_WORD_LENGTH
                        && (i as usize) <= SHARED_BROTLI_MAX_DICTIONARY_WORD_LENGTH
                    {
                        let p1 = s.ringbuffer[((pos - 1) & s.ringbuffer_mask) as usize];
                        let p2 = s.ringbuffer[((pos - 2) & s.ringbuffer_mask) as usize];
                        let mut dict_id = if s.dictionary.context_based {
                            let lut = s.context_lookup.unwrap();
                            s.dictionary.context_map[brotli_context(p1, p2, lut) as usize] as usize
                        } else {
                            0usize
                        };
                        let len_idx = i as usize;
                        let mut address = s.distance_code
                            - s.max_distance
                            - 1
                            - (compound_dictionary_size as i32);
                        let mut shift = s.dictionary.words(dict_id).size_bits_by_length[len_idx] as u32;
                        let mut mask = bit_mask(shift) as i32;
                        let mut word_idx = address & mask;
                        let mut transform_idx = address >> shift;
                        let mut offset =
                            (s.dictionary.words(dict_id).offsets_by_length[len_idx] as i32)
                                + word_idx * i;
                        s.dist_rb_idx += s.distance_context;

                        let num_transforms =
                            s.dictionary.transforms(dict_id).num_transforms as i32;
                        if (transform_idx >= num_transforms || shift == 0)
                            && s.dictionary.num_dictionaries > 1
                        {
                            let mut dist_remaining =
                                address - (((1i32 << shift) & !1i32) * num_transforms);
                            for dict_id2 in 0..(s.dictionary.num_dictionaries as usize) {
                                let shift2 =
                                    s.dictionary.words(dict_id2).size_bits_by_length[len_idx] as u32;
                                if dict_id2 != dict_id && shift2 != 0 {
                                    let num_t2 =
                                        s.dictionary.transforms(dict_id2).num_transforms as i32;
                                    let num = ((1i32 << shift2) & !1i32) * num_t2;
                                    if dist_remaining < num {
                                        dict_id = dict_id2;
                                        address = dist_remaining;
                                        shift = shift2;
                                        mask = bit_mask(shift) as i32;
                                        word_idx = address & mask;
                                        transform_idx = address >> shift;
                                        offset = (s.dictionary.words(dict_id).offsets_by_length
                                            [len_idx]
                                            as i32)
                                            + word_idx * i;
                                        break;
                                    }
                                    dist_remaining -= num;
                                }
                            }
                        }

                        let words = s.dictionary.words(dict_id);
                        let transforms = s.dictionary.transforms(dict_id);
                        if words.size_bits_by_length[len_idx] == 0 {
                            s.pos = pos;
                            s.loop_counter = i;
                            return BrotliDecoderErrorCode::ErrorFormatDictionary;
                        }
                        if words.data.is_empty() {
                            s.pos = pos;
                            s.loop_counter = i;
                            return BrotliDecoderErrorCode::ErrorDictionaryNotSet;
                        }
                        if transform_idx < (transforms.num_transforms as i32) {
                            let word = &words.data[(offset as usize)..(offset as usize) + len_idx];
                            let mut len = i;
                            if transform_idx == (transforms.cut_off_transforms[0] as i32) {
                                let dst = pos as usize;
                                s.ringbuffer[dst..dst + (len as usize)].copy_from_slice(word);
                            } else {
                                let dst = pos as usize;
                                let transformed_len = brotli_transform_dictionary_word(
                                    &mut s.ringbuffer[dst..],
                                    word,
                                    len,
                                    transforms,
                                    transform_idx as usize,
                                );
                                len = transformed_len as i32;
                                if len == 0 && s.distance_code <= 120 {
                                    s.pos = pos;
                                    s.loop_counter = i;
                                    return BrotliDecoderErrorCode::ErrorFormatTransform;
                                }
                            }
                            pos += len;
                            s.meta_block_remaining_len -= len;
                            if pos >= s.ringbuffer_size {
                                s.state = BrotliRunningState::CommandPostWrite1;
                                s.pos = pos;
                                s.loop_counter = i;
                                return BrotliDecoderErrorCode::Success;
                            }
                        } else {
                            s.pos = pos;
                            s.loop_counter = i;
                            return BrotliDecoderErrorCode::ErrorFormatTransform;
                        }
                    } else {
                        s.pos = pos;
                        s.loop_counter = i;
                        return BrotliDecoderErrorCode::ErrorFormatDictionary;
                    }
                } else {
                    let src_start = ((pos - s.distance_code) & s.ringbuffer_mask) as usize;
                    let dst_pos = pos as usize;
                    let dst_end = dst_pos + (i as usize);
                    let src_end = src_start + (i as usize);
                    s.dist_rb[(s.dist_rb_idx & 3) as usize] = s.distance_code;
                    s.dist_rb_idx += 1;
                    s.meta_block_remaining_len -= i;

                    s.ringbuffer.copy_within(src_start..src_start + 16, dst_pos);
                    if src_end > dst_pos && dst_end > src_start {
                        phase = CmdPhase::PostWrapCopy;
                        continue 'outer;
                    }
                    if dst_end >= (s.ringbuffer_size as usize)
                        || src_end >= (s.ringbuffer_size as usize)
                    {
                        phase = CmdPhase::PostWrapCopy;
                        continue 'outer;
                    }
                    pos += i;
                    if i > 16 {
                        if i > 32 {
                            s.ringbuffer
                                .copy_within(src_start + 16..src_start + (i as usize), dst_pos + 16);
                        } else {
                            s.ringbuffer
                                .copy_within(src_start + 16..src_start + 32, dst_pos + 16);
                        }
                    }
                }

                if s.meta_block_remaining_len <= 0 {
                    s.state = BrotliRunningState::MetablockDone;
                    s.pos = pos;
                    s.loop_counter = i;
                    return BrotliDecoderErrorCode::Success;
                }
                phase = CmdPhase::Begin;
            }
            CmdPhase::PostWrapCopy => {
                let mut wrap_guard = s.ringbuffer_size - pos;
                loop {
                    i -= 1;
                    if i < 0 {
                        break;
                    }
                    let src_idx = ((pos - s.distance_code) & s.ringbuffer_mask) as usize;
                    s.ringbuffer[pos as usize] = s.ringbuffer[src_idx];
                    pos += 1;
                    wrap_guard -= 1;
                    if wrap_guard == 0 {
                        s.state = BrotliRunningState::CommandPostWrite2;
                        s.pos = pos;
                        s.loop_counter = i;
                        return BrotliDecoderErrorCode::Success;
                    }
                }
                if s.meta_block_remaining_len <= 0 {
                    s.state = BrotliRunningState::MetablockDone;
                    s.pos = pos;
                    s.loop_counter = i;
                    return BrotliDecoderErrorCode::Success;
                }
                phase = CmdPhase::Begin;
            }
        }
    }
}

impl BrotliDecoderState {
    pub fn set_parameter(&mut self, param: BrotliDecoderParameter, value: u32) -> bool {
        if self.state != BrotliRunningState::Uninited {
            return false;
        }
        match param {
            BrotliDecoderParameter::DisableRingBufferReallocation => {
                self.canny_ringbuffer_allocation = value == 0;
                true
            }
            BrotliDecoderParameter::LargeWindow => {
                self.large_window = value != 0;
                true
            }
        }
    }

    pub fn attach_dictionary(
        &mut self,
        dict_type: BrotliSharedDictionaryType,
        data: &[u8],
    ) -> bool {
        if self.state != BrotliRunningState::Uninited {
            return false;
        }
        let num_prefix_before = self.dictionary.prefixes.len();
        if !self.dictionary.attach(dict_type, data) {
            return false;
        }
        let prefixes: Vec<_> = self.dictionary.prefixes[num_prefix_before..].to_vec();
        for p in prefixes {
            if !attach_compound_dictionary(self, p) {
                return false;
            }
        }
        true
    }

    pub fn set_metadata_callbacks(
        &mut self,
        start_func: Option<MetadataStartFn>,
        chunk_func: Option<MetadataChunkFn>,
    ) {
        self.metadata_start_func = start_func;
        self.metadata_chunk_func = chunk_func;
    }

    pub fn has_more_output(&self) -> bool {
        if (self.error_code as i32) < 0 {
            return false;
        }
        !self.ringbuffer.is_empty() && unwritten_bytes(self, false) != 0
    }

    pub fn take_output(&mut self, max_size: usize) -> Vec<u8> {
        let cap = if max_size == 0 { 1usize << 24 } else { max_size };
        if self.ringbuffer.is_empty() || (self.error_code as i32) < 0 {
            return Vec::new();
        }
        wrap_ring_buffer(self);
        let to_write = unwritten_bytes(self, true).min(cap);
        let mut out = vec![0u8; to_write];
        let mut out_pos = 0usize;
        let status = write_ring_buffer(self, Some((&mut out, &mut out_pos)), true);
        if status == BrotliDecoderErrorCode::Success
            || status == BrotliDecoderErrorCode::NeedsMoreOutput
        {
            out.truncate(out_pos);
            out
        } else {
            if (status as i32) < 0 {
                self.save_error_code(status, 0);
            }
            Vec::new()
        }
    }

    pub fn is_used(&self) -> bool {
        self.state != BrotliRunningState::Uninited || self.br.get_available_bits() != 0
    }

    pub fn is_finished(&self) -> bool {
        self.state == BrotliRunningState::Done && !self.has_more_output()
    }

    pub fn error_code(&self) -> BrotliDecoderErrorCode {
        self.error_code
    }

    fn save_error_code(
        &mut self,
        e: BrotliDecoderErrorCode,
        consumed_input: usize,
    ) -> BrotliDecoderResult {
        self.error_code = e;
        self.used_input += consumed_input as u64;
        if self.buffer_length != 0 && self.br.next_in == self.br.last_in {
            self.buffer_length = 0;
        }
        match e {
            BrotliDecoderErrorCode::Success => BrotliDecoderResult::Success,
            BrotliDecoderErrorCode::NeedsMoreInput => BrotliDecoderResult::NeedsMoreInput,
            BrotliDecoderErrorCode::NeedsMoreOutput => BrotliDecoderResult::NeedsMoreOutput,
            _ => BrotliDecoderResult::Error,
        }
    }

    pub fn decompress_stream(
        &mut self,
        input: &[u8],
        in_pos: &mut usize,
        output: &mut [u8],
        out_pos: &mut usize,
        total_out: Option<&mut usize>,
    ) -> BrotliDecoderResult {
        let mut result = BrotliDecoderErrorCode::Success;
        let input_start = *in_pos;
        if (self.error_code as i32) < 0 {
            return BrotliDecoderResult::Error;
        }

        let mut using_internal_buf = self.buffer_length != 0;
        let mut local_buf = self.buffer;
        if !using_internal_buf {
            self.br.set_input(*in_pos, input.len() - *in_pos);
        } else {
            result = BrotliDecoderErrorCode::NeedsMoreInput;
            self.br.set_input(0, self.buffer_length);
        }

        loop {
            if result != BrotliDecoderErrorCode::Success {
                if result == BrotliDecoderErrorCode::NeedsMoreInput {
                    if !self.ringbuffer.is_empty() {
                        let out_arg = if *out_pos < output.len() {
                            Some((&mut *output, &mut *out_pos))
                        } else {
                            None
                        };
                        let intermediate = write_ring_buffer(self, out_arg, true);
                        if (intermediate as i32) < 0 {
                            result = intermediate;
                            break;
                        }
                    }
                    if using_internal_buf {
                        if self.br.next_in == self.br.last_in {
                            self.buffer_length = 0;
                            using_internal_buf = false;
                            result = BrotliDecoderErrorCode::Success;
                            self.br.set_input(*in_pos, input.len() - *in_pos);
                            continue;
                        } else if *in_pos < input.len() {
                            result = BrotliDecoderErrorCode::Success;
                            debug_assert!(self.buffer_length < 8);
                            local_buf[self.buffer_length] = input[*in_pos];
                            self.buffer[self.buffer_length] = input[*in_pos];
                            self.buffer_length += 1;
                            self.br.set_input(0, self.buffer_length);
                            *in_pos += 1;
                            continue;
                        }
                        break;
                    } else {
                        *in_pos = self.br.next_in;
                        let avail = self.br.get_avail_in();
                        for _ in 0..avail {
                            self.buffer[self.buffer_length] = input[*in_pos];
                            local_buf[self.buffer_length] = self.buffer[self.buffer_length];
                            self.buffer_length += 1;
                            *in_pos += 1;
                        }
                        break;
                    }
                }

                if using_internal_buf {
                    self.buffer_length = 0;
                } else {
                    self.br.unload();
                    *in_pos = self.br.next_in;
                }
                break;
            }

            let cur_in: &[u8] = if using_internal_buf {
                &local_buf[..self.buffer_length]
            } else {
                input
            };

            match self.state {
                BrotliRunningState::Uninited => {
                    if !self.br.warmup(cur_in) {
                        result = BrotliDecoderErrorCode::NeedsMoreInput;
                        continue;
                    }
                    result = decode_window_bits(self);
                    if result != BrotliDecoderErrorCode::Success {
                        continue;
                    }
                    if self.large_window {
                        self.state = BrotliRunningState::LargeWindowBits;
                        continue;
                    }
                    self.state = BrotliRunningState::Initialize;
                }
                BrotliRunningState::LargeWindowBits => {
                    let mut bits = 0u64;
                    if !self.br.safe_read_bits(cur_in, 6, &mut bits) {
                        result = BrotliDecoderErrorCode::NeedsMoreInput;
                        continue;
                    }
                    self.window_bits = (bits as u32) & 63;
                    if self.window_bits < BROTLI_LARGE_MIN_WBITS
                        || self.window_bits > BROTLI_LARGE_MAX_WBITS
                    {
                        result = BrotliDecoderErrorCode::ErrorFormatWindowBits;
                        continue;
                    }
                    self.state = BrotliRunningState::Initialize;
                }
                BrotliRunningState::Initialize => {
                    self.max_backward_distance = (1i32 << self.window_bits) - (BROTLI_WINDOW_GAP as i32);
                    self.init_block_trees();
                    self.state = BrotliRunningState::MetablockBegin;
                }
                BrotliRunningState::MetablockBegin => {
                    self.metablock_begin();
                    self.state = BrotliRunningState::MetablockHeader;
                }
                BrotliRunningState::MetablockHeader => {
                    result = decode_meta_block_length(self, cur_in);
                    if result != BrotliDecoderErrorCode::Success {
                        continue;
                    }
                    if self.is_metadata || self.is_uncompressed {
                        if !self.br.jump_to_byte_boundary() {
                            result = BrotliDecoderErrorCode::ErrorFormatPadding1;
                            continue;
                        }
                    }
                    if self.is_metadata {
                        self.state = BrotliRunningState::Metadata;
                        let meta_len = self.meta_block_remaining_len as usize;
                        if let Some(ref mut cb) = self.metadata_start_func {
                            cb(meta_len);
                        }
                        continue;
                    }
                    if self.meta_block_remaining_len == 0 {
                        self.state = BrotliRunningState::MetablockDone;
                        continue;
                    }
                    brotli_calculate_ring_buffer_size(self);
                    if self.is_uncompressed {
                        self.state = BrotliRunningState::Uncompressed;
                        continue;
                    }
                    self.state = BrotliRunningState::BeforeCompressedMetablockHeader;
                }
                BrotliRunningState::BeforeCompressedMetablockHeader => {
                    self.loop_counter = 0;
                    self.header.sub_loop_counter = 0;
                    self.header.substate_huffman = BrotliRunningHuffmanState::None;
                    self.header.substate_tree_group = BrotliRunningTreeGroupState::None;
                    self.header.substate_context_map = BrotliRunningContextMapState::None;
                    self.state = BrotliRunningState::HuffmanCode0;
                }
                BrotliRunningState::HuffmanCode0 => {
                    if self.loop_counter >= 3 {
                        self.state = BrotliRunningState::MetablockHeader2;
                        continue;
                    }
                    let idx = self.loop_counter as usize;
                    let mut val = self.num_block_types[idx];
                    result = decode_var_len_uint8(self, cur_in, &mut val);
                    self.num_block_types[idx] = val;
                    if result != BrotliDecoderErrorCode::Success {
                        continue;
                    }
                    self.num_block_types[idx] += 1;
                    if self.num_block_types[idx] < 2 {
                        self.loop_counter += 1;
                        continue;
                    }
                    self.state = BrotliRunningState::HuffmanCode1;
                }
                BrotliRunningState::HuffmanCode1 => {
                    let idx = self.loop_counter as usize;
                    let alphabet_size = self.num_block_types[idx] + 2;
                    result = read_huffman_code(
                        alphabet_size,
                        alphabet_size,
                        HuffmanTarget::BlockType(idx),
                        0,
                        None,
                        self,
                        cur_in,
                    );
                    if result != BrotliDecoderErrorCode::Success {
                        continue;
                    }
                    self.state = BrotliRunningState::HuffmanCode2;
                }
                BrotliRunningState::HuffmanCode2 => {
                    let idx = self.loop_counter as usize;
                    let alphabet_size = BROTLI_NUM_BLOCK_LEN_SYMBOLS as u32;
                    result = read_huffman_code(
                        alphabet_size,
                        alphabet_size,
                        HuffmanTarget::BlockLen(idx),
                        0,
                        None,
                        self,
                        cur_in,
                    );
                    if result != BrotliDecoderErrorCode::Success {
                        continue;
                    }
                    self.state = BrotliRunningState::HuffmanCode3;
                }
                BrotliRunningState::HuffmanCode3 => {
                    let idx = self.loop_counter as usize;
                    let mut bl = self.block_length[idx];
                    let ok = safe_read_block_length(self, idx, &mut bl, cur_in);
                    self.block_length[idx] = bl;
                    if !ok {
                        result = BrotliDecoderErrorCode::NeedsMoreInput;
                        continue;
                    }
                    self.loop_counter += 1;
                    self.state = BrotliRunningState::HuffmanCode0;
                }
                BrotliRunningState::Uncompressed => {
                    let out_arg = if *out_pos < output.len() {
                        Some((&mut *output, &mut *out_pos))
                    } else {
                        None
                    };
                    result = copy_uncompressed_block_to_output(self, cur_in, out_arg);
                    if result != BrotliDecoderErrorCode::Success {
                        continue;
                    }
                    self.state = BrotliRunningState::MetablockDone;
                }
                BrotliRunningState::Metadata => {
                    result = skip_metadata_block(self, cur_in);
                    if result != BrotliDecoderErrorCode::Success {
                        continue;
                    }
                    self.state = BrotliRunningState::MetablockDone;
                }
                BrotliRunningState::MetablockHeader2 => {
                    let mut bits = 0u64;
                    if !self.br.safe_read_bits(cur_in, 6, &mut bits) {
                        result = BrotliDecoderErrorCode::NeedsMoreInput;
                        continue;
                    }
                    self.distance_postfix_bits = (bits as u32) & 3;
                    bits >>= 2;
                    self.num_direct_distance_codes = (bits as u32) << self.distance_postfix_bits;
                    self.context_modes = vec![0u8; self.num_block_types[0] as usize];
                    self.loop_counter = 0;
                    self.state = BrotliRunningState::ContextModes;
                }
                BrotliRunningState::ContextModes => {
                    result = read_context_modes(self, cur_in);
                    if result != BrotliDecoderErrorCode::Success {
                        continue;
                    }
                    self.state = BrotliRunningState::ContextMap1;
                }
                BrotliRunningState::ContextMap1 => {
                    let map_size = self.num_block_types[0] << BROTLI_LITERAL_CONTEXT_BITS;
                    result = decode_context_map(map_size, false, self, cur_in);
                    if result != BrotliDecoderErrorCode::Success {
                        continue;
                    }
                    detect_trivial_literal_block_types(self);
                    self.state = BrotliRunningState::ContextMap2;
                }
                BrotliRunningState::ContextMap2 => {
                    let npostfix = self.distance_postfix_bits;
                    let ndirect = self.num_direct_distance_codes;
                    let mut distance_alphabet_size_max = brotli_distance_alphabet_size(
                        npostfix,
                        ndirect,
                        BROTLI_MAX_DISTANCE_BITS,
                    );
                    let mut distance_alphabet_size_limit = distance_alphabet_size_max;
                    if self.large_window {
                        let limit = brotli_calculate_distance_code_limit(
                            BROTLI_MAX_ALLOWED_DISTANCE as u32,
                            npostfix,
                            ndirect,
                        );
                        distance_alphabet_size_max = brotli_distance_alphabet_size(
                            npostfix,
                            ndirect,
                            BROTLI_LARGE_MAX_DISTANCE_BITS,
                        );
                        distance_alphabet_size_limit = limit.max_alphabet_size;
                    }
                    let map_size = self.num_block_types[2] << BROTLI_DISTANCE_CONTEXT_BITS;
                    result = decode_context_map(map_size, true, self, cur_in);
                    if result != BrotliDecoderErrorCode::Success {
                        continue;
                    }
                    let mut lit_group = std::mem::take(&mut self.literal_hgroup);
                    brotli_decoder_huffman_tree_group_init(
                        &mut lit_group,
                        BROTLI_NUM_LITERAL_SYMBOLS as u32,
                        BROTLI_NUM_LITERAL_SYMBOLS as u32,
                        self.num_literal_htrees,
                    );
                    self.literal_hgroup = lit_group;

                    let mut cmd_group = std::mem::take(&mut self.insert_copy_hgroup);
                    brotli_decoder_huffman_tree_group_init(
                        &mut cmd_group,
                        BROTLI_NUM_COMMAND_SYMBOLS as u32,
                        BROTLI_NUM_COMMAND_SYMBOLS as u32,
                        self.num_block_types[1],
                    );
                    self.insert_copy_hgroup = cmd_group;

                    let mut dist_group = std::mem::take(&mut self.distance_hgroup);
                    brotli_decoder_huffman_tree_group_init(
                        &mut dist_group,
                        distance_alphabet_size_max,
                        distance_alphabet_size_limit,
                        self.num_dist_htrees,
                    );
                    self.distance_hgroup = dist_group;

                    self.loop_counter = 0;
                    self.state = BrotliRunningState::TreeGroup;
                }
                BrotliRunningState::TreeGroup => {
                    let kind = match self.loop_counter {
                        0 => TreeGroupKind::Literal,
                        1 => TreeGroupKind::InsertCopy,
                        2 => TreeGroupKind::Distance,
                        _ => {
                            result = BrotliDecoderErrorCode::ErrorUnreachable;
                            continue;
                        }
                    };
                    result = huffman_tree_group_decode(kind, self, cur_in);
                    if result != BrotliDecoderErrorCode::Success {
                        continue;
                    }
                    self.loop_counter += 1;
                    if self.loop_counter < 3 {
                        continue;
                    }
                    self.state = BrotliRunningState::BeforeCompressedMetablockBody;
                }
                BrotliRunningState::BeforeCompressedMetablockBody => {
                    prepare_literal_decoding(self);
                    self.dist_context_map_slice_offset = 0;
                    self.htree_command_idx = self.insert_copy_hgroup.htree_offsets[0];
                    if !brotli_ensure_ring_buffer(self) {
                        result = BrotliDecoderErrorCode::ErrorAllocRingBuffer2;
                        continue;
                    }
                    calculate_distance_lut(self);
                    self.state = BrotliRunningState::CommandBegin;
                }
                BrotliRunningState::CommandBegin
                | BrotliRunningState::CommandInner
                | BrotliRunningState::CommandPostDecodeLiterals
                | BrotliRunningState::CommandPostWrapCopy => {
                    result = process_commands_internal(false, self, cur_in);
                    if result == BrotliDecoderErrorCode::NeedsMoreInput {
                        result = process_commands_internal(true, self, cur_in);
                    }
                }
                BrotliRunningState::CommandInnerWrite
                | BrotliRunningState::CommandPostWrite1
                | BrotliRunningState::CommandPostWrite2 => {
                    let out_arg = if *out_pos < output.len() {
                        Some((&mut *output, &mut *out_pos))
                    } else {
                        None
                    };
                    result = write_ring_buffer(self, out_arg, false);
                    if result != BrotliDecoderErrorCode::Success {
                        continue;
                    }
                    wrap_ring_buffer(self);
                    if self.ringbuffer_size == (1 << self.window_bits) {
                        self.max_distance = self.max_backward_distance;
                    }
                    if self.state == BrotliRunningState::CommandPostWrite1 {
                        let has_more_compound = self
                            .compound_dictionary
                            .as_ref()
                            .is_some_and(|a| a.br_length != a.br_copied);
                        if has_more_compound {
                            let pos = self.pos;
                            self.pos += copy_from_compound_dictionary(self, pos);
                            if self.pos >= self.ringbuffer_size {
                                continue;
                            }
                        }
                        if self.meta_block_remaining_len == 0 {
                            self.state = BrotliRunningState::MetablockDone;
                        } else {
                            self.state = BrotliRunningState::CommandBegin;
                        }
                    } else if self.state == BrotliRunningState::CommandPostWrite2 {
                        self.state = BrotliRunningState::CommandPostWrapCopy;
                    } else {
                        if self.loop_counter == 0 {
                            if self.meta_block_remaining_len == 0 {
                                self.state = BrotliRunningState::MetablockDone;
                            } else {
                                self.state = BrotliRunningState::CommandPostDecodeLiterals;
                            }
                            continue;
                        }
                        self.state = BrotliRunningState::CommandInner;
                    }
                }
                BrotliRunningState::MetablockDone => {
                    if self.meta_block_remaining_len < 0 {
                        result = BrotliDecoderErrorCode::ErrorFormatBlockLength2;
                        continue;
                    }
                    self.cleanup_after_metablock();
                    if !self.is_last_metablock {
                        self.state = BrotliRunningState::MetablockBegin;
                        continue;
                    }
                    if !self.br.jump_to_byte_boundary() {
                        result = BrotliDecoderErrorCode::ErrorFormatPadding2;
                        continue;
                    }
                    if !using_internal_buf {
                        self.br.unload();
                        *in_pos = self.br.next_in;
                    }
                    self.state = BrotliRunningState::Done;
                }
                BrotliRunningState::Done => {
                    if !self.ringbuffer.is_empty() {
                        let out_arg = if *out_pos < output.len() {
                            Some((&mut *output, &mut *out_pos))
                        } else {
                            None
                        };
                        result = write_ring_buffer(self, out_arg, true);
                        if result != BrotliDecoderErrorCode::Success {
                            continue;
                        }
                    }
                    if let Some(t) = total_out {
                        *t = self.partial_pos_out;
                    }
                    return self.save_error_code(result, *in_pos - input_start);
                }
            }
        }

        if let Some(t) = total_out {
            *t = self.partial_pos_out;
        }
        self.save_error_code(result, *in_pos - input_start)
    }
}

/// One-shot memory-to-memory decompression into a caller-supplied buffer.
pub fn brotli_decoder_decompress_into(
    encoded: &[u8],
    decoded: &mut [u8],
) -> Result<usize, BrotliDecoderErrorCode> {
    let mut s = BrotliDecoderState::new();
    let mut in_pos = 0usize;
    let mut out_pos = 0usize;
    let mut total_out = 0usize;
    let res = s.decompress_stream(encoded, &mut in_pos, decoded, &mut out_pos, Some(&mut total_out));
    if res != BrotliDecoderResult::Success {
        Err(if (s.error_code as i32) < 0 {
            s.error_code
        } else {
            BrotliDecoderErrorCode::ErrorInvalidArguments
        })
    } else {
        Ok(total_out)
    }
}

/// One-shot decompression returning an owned `Vec<u8>`.
pub fn brotli_decoder_decompress(encoded: &[u8]) -> Result<Vec<u8>, BrotliDecoderErrorCode> {
    let mut s = BrotliDecoderState::new();
    let mut in_pos = 0usize;
    let mut out = Vec::new();
    let mut scratch = [0u8; 65536];
    loop {
        let mut out_pos = 0usize;
        let res = s.decompress_stream(encoded, &mut in_pos, &mut scratch, &mut out_pos, None);
        if out_pos > 0 {
            out.extend_from_slice(&scratch[..out_pos]);
        }
        match res {
            BrotliDecoderResult::Success => return Ok(out),
            BrotliDecoderResult::NeedsMoreOutput => continue,
            BrotliDecoderResult::NeedsMoreInput => {
                return Err(BrotliDecoderErrorCode::NeedsMoreInput);
            }
            BrotliDecoderResult::Error => return Err(s.error_code),
        }
    }
}
