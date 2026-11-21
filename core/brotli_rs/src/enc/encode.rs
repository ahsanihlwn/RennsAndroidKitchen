//! Brotli streaming and one-shot compression implementation.

#![allow(dead_code)]

use crate::common::constants::*;
use crate::common::context::{ContextType, brotli_context, brotli_context_lut};
use crate::common::version::BROTLI_VERSION;
use crate::enc::backward_references::brotli_create_backward_references;
use crate::enc::backward_references_hq::{
    brotli_create_hq_zopfli_backward_references, brotli_create_zopfli_backward_references,
};
use crate::enc::bit_cost::brotli_bits_entropy;
use crate::enc::brotli_bit_stream::{
    brotli_store_meta_block, brotli_store_meta_block_fast, brotli_store_meta_block_trivial,
    brotli_store_uncompressed_meta_block,
};
use crate::enc::command::{Command, get_length_code};
use crate::enc::compound_dictionary::{
    K_LEAN_PREPARED_DICTIONARY_MAGIC, K_PREPARED_DICTIONARY_MAGIC,
    K_SHARED_DICTIONARY_MAGIC, PreparedDictionary, create_prepared_dictionary,
};
use crate::enc::compress_fragment::{BrotliOnePassArena, brotli_compress_fragment_fast};
use crate::enc::compress_fragment_two_pass::{
    BrotliTwoPassArena, K_COMPRESS_FRAGMENT_TWO_PASS_BLOCK_SIZE, brotli_compress_fragment_two_pass,
};
use crate::enc::encoder_dict::SharedEncoderDictionary;
use crate::enc::fast_log::{fast_log2, log2_floor_non_zero};
use crate::enc::hash::Hasher;
use crate::enc::histogram::{HistogramCommand, HistogramDistance, HistogramLiteral};
use crate::enc::metablock::{
    BROTLI_MAX_STATIC_CONTEXTS, Base64Region, MetaBlockSplit, brotli_build_meta_block,
    brotli_build_meta_block_greedy, brotli_init_distance_params, brotli_optimize_histograms,
};
use crate::enc::params::{
    BrotliEncoderMode, BrotliEncoderOperation, BrotliEncoderParameter, BrotliEncoderParams,
    BrotliEncoderSimdHasher,
};
use crate::enc::quality::{
    FAST_ONE_PASS_COMPRESSION_QUALITY, FAST_TWO_PASS_COMPRESSION_QUALITY,
    HQ_ZOPFLIFICATION_QUALITY, MAX_NUM_DELAYED_SYMBOLS, MAX_QUALITY_FOR_STATIC_ENTROPY_CODES,
    MIN_QUALITY_FOR_BLOCK_SPLIT, MIN_QUALITY_FOR_CONTEXT_MODELING,
    MIN_QUALITY_FOR_HQ_BLOCK_SPLITTING, MIN_QUALITY_FOR_HQ_CONTEXT_MODELING,
    MIN_QUALITY_FOR_NONZERO_DISTANCE_PARAMS, MIN_QUALITY_FOR_OPTIMIZE_HISTOGRAMS,
    ZOPFLIFICATION_QUALITY, choose_hasher, compute_lg_block, compute_rb_bits, max_hash_table_size,
    max_metablock_size, sanitize_params,
};
use crate::enc::ringbuffer::RingBuffer;
use crate::enc::utf8_util::{K_MIN_UTF8_RATIO, brotli_is_mostly_utf8};
use crate::enc::write_bits::brotli_write_bits;
use std::sync::Arc;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
pub enum BrotliEncoderStreamState {
    Processing = 0,
    FlushRequested = 1,
    Finished = 2,
    MetadataHead = 3,
    MetadataBody = 4,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i8)]
pub enum BrotliEncoderFlintState {
    Needs2Bytes = 2,
    Needs1Byte = 1,
    WaitingForProcessing = 0,
    WaitingForFlushing = -1,
    Done = -2,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum OutputSource {
    None,
    TinyBuf,
    Storage,
}

pub struct BrotliEncoderState {
    pub params: BrotliEncoderParams,

    input_pos_: u64,
    ringbuffer_: RingBuffer,
    commands_: Vec<Command>,
    num_commands_: usize,
    num_literals_: usize,
    last_insert_len_: usize,
    last_flush_pos_: u64,
    last_processed_pos_: u64,
    dist_cache_: [i32; BROTLI_NUM_DISTANCE_SHORT_CODES],
    saved_dist_cache_: [i32; 4],
    last_bytes_: u16,
    last_bytes_bits_: u8,
    flint_: i8,
    prev_byte_: u8,
    prev_byte2_: u8,
    storage_: Vec<u8>,

    hasher_: Hasher,

    small_table_: [i32; 1 << 10],
    large_table_: Vec<i32>,

    one_pass_arena_: Option<Box<BrotliOnePassArena>>,
    two_pass_arena_: Option<Box<BrotliTwoPassArena>>,

    command_buf_: Vec<u32>,
    literal_buf_: Vec<u8>,

    total_in_: u64,
    out_source_: OutputSource,
    out_pos_: usize,
    available_out_: usize,
    total_out_: u64,
    tiny_buf_: [u8; 16],
    remaining_metadata_bytes_: u32,
    stream_state_: BrotliEncoderStreamState,

    is_last_block_emitted_: bool,
    is_initialized_: bool,
}

impl Default for BrotliEncoderState {
    fn default() -> Self {
        Self::new()
    }
}

impl BrotliEncoderState {
    pub fn new() -> Self {
        let mut s = Self {
            params: BrotliEncoderParams::default(),
            input_pos_: 0,
            ringbuffer_: RingBuffer::new(),
            commands_: Vec::new(),
            num_commands_: 0,
            num_literals_: 0,
            last_insert_len_: 0,
            last_flush_pos_: 0,
            last_processed_pos_: 0,
            dist_cache_: [4, 11, 15, 16, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            saved_dist_cache_: [4, 11, 15, 16],
            last_bytes_: 0,
            last_bytes_bits_: 0,
            flint_: BrotliEncoderFlintState::Done as i8,
            prev_byte_: 0,
            prev_byte2_: 0,
            storage_: Vec::new(),
            hasher_: Hasher::new(),
            small_table_: [0; 1 << 10],
            large_table_: Vec::new(),
            one_pass_arena_: None,
            two_pass_arena_: None,
            command_buf_: Vec::new(),
            literal_buf_: Vec::new(),
            total_in_: 0,
            out_source_: OutputSource::None,
            out_pos_: 0,
            available_out_: 0,
            total_out_: 0,
            tiny_buf_: [0; 16],
            remaining_metadata_bytes_: 0,
            stream_state_: BrotliEncoderStreamState::Processing,
            is_last_block_emitted_: false,
            is_initialized_: false,
        };
        init_params(&mut s.params);
        s
    }

    #[inline(always)]
    fn input_block_size(&self) -> usize {
        1usize << self.params.lgblock
    }

    #[inline(always)]
    fn unprocessed_input_size(&self) -> u64 {
        self.input_pos_ - self.last_processed_pos_
    }

    #[inline(always)]
    fn remaining_input_block_size(&self) -> usize {
        let delta = self.unprocessed_input_size();
        let block_size = self.input_block_size();
        if delta >= block_size as u64 {
            0
        } else {
            block_size - delta as usize
        }
    }

    pub fn set_parameter(&mut self, p: BrotliEncoderParameter, value: u32) -> bool {
        if self.is_initialized_ {
            return false;
        }
        match p {
            BrotliEncoderParameter::Mode => {
                self.params.mode = match value {
                    0 => BrotliEncoderMode::Generic,
                    1 => BrotliEncoderMode::Text,
                    2 => BrotliEncoderMode::Font,
                    _ => return false,
                };
                true
            }
            BrotliEncoderParameter::Quality => {
                self.params.quality = value as i32;
                true
            }
            BrotliEncoderParameter::Lgwin => {
                self.params.lgwin = value as i32;
                true
            }
            BrotliEncoderParameter::Lgblock => {
                self.params.lgblock = value as i32;
                true
            }
            BrotliEncoderParameter::DisableLiteralContextModeling => {
                if value != 0 && value != 1 {
                    return false;
                }
                self.params.disable_literal_context_modeling = value != 0;
                true
            }
            BrotliEncoderParameter::SizeHint => {
                self.params.size_hint = value as usize;
                true
            }
            BrotliEncoderParameter::LargeWindow => {
                self.params.large_window = value != 0;
                true
            }
            BrotliEncoderParameter::Npostfix => {
                self.params.dist.distance_postfix_bits = value;
                true
            }
            BrotliEncoderParameter::Ndirect => {
                self.params.dist.num_direct_distance_codes = value;
                true
            }
            BrotliEncoderParameter::StreamOffset => {
                if value > (1u32 << 30) {
                    return false;
                }
                self.params.stream_offset = value as usize;
                true
            }
            BrotliEncoderParameter::Base64Mode => {
                self.params.base64_mode = (value & 1) as i32;
                true
            }
            BrotliEncoderParameter::MaxBase64Regions => {
                self.params.max_base64_regions = value as usize;
                true
            }
            BrotliEncoderParameter::MinBase64RegionLen => {
                self.params.min_base64_region_len = value as usize;
                true
            }
            BrotliEncoderParameter::SimdHasher => {
                self.params.simd_hasher = match value {
                    0 => BrotliEncoderSimdHasher::Default,
                    1 => BrotliEncoderSimdHasher::Enable,
                    2 => BrotliEncoderSimdHasher::Disable,
                    _ => return false,
                };
                true
            }
            BrotliEncoderParameter::HasherOpt => {
                if value != 0 && value != 1 {
                    return false;
                }
                self.params.hasher_opt = value != 0;
                true
            }
        }
    }

    pub fn attach_prepared_dictionary(&mut self, dictionary: Arc<PreparedDictionary>) -> bool {
        if self.is_initialized_ {
            return false;
        }
        if dictionary.magic != K_PREPARED_DICTIONARY_MAGIC
            && dictionary.magic != K_LEAN_PREPARED_DICTIONARY_MAGIC
        {
            return false;
        }
        self.params.dictionary.compound.attach(dictionary)
    }

    pub fn attach_shared_dictionary(&mut self, attached: &SharedEncoderDictionary) -> bool {
        if self.is_initialized_ {
            return false;
        }
        if attached.magic != K_SHARED_DICTIONARY_MAGIC {
            return false;
        }
        let current = &mut self.params.dictionary;
        let was_default = !current.contextual.context_based
            && current.contextual.num_dictionaries == 1;
        let new_default = !attached.contextual.context_based
            && attached.contextual.num_dictionaries == 1;

        current.max_quality = current.max_quality.min(attached.max_quality);
        for chunk in &attached.compound.chunks {
            if !current.compound.attach(Arc::clone(chunk)) {
                return false;
            }
        }
        if !new_default {
            if !was_default {
                return false;
            }
            current.contextual = attached.contextual.clone();
        }
        true
    }

    fn ensure_storage(&mut self, size: usize) {
        if self.storage_.len() < size {
            self.storage_.resize(size, 0);
        }
    }

    fn copy_input_to_ringbuffer(&mut self, input_buffer: &[u8]) {
        self.ringbuffer_.write(input_buffer);
        self.input_pos_ += input_buffer.len() as u64;
        if self.ringbuffer_.pos_ <= self.ringbuffer_.mask_ {
            let pos = self.ringbuffer_.pos_ as usize;
            let buf = self.ringbuffer_.buffer_mut();
            if pos + 7 <= buf.len() {
                buf[pos..pos + 7].fill(0);
            }
        }
    }

    fn update_last_processed_pos(&mut self) -> bool {
        let wrapped_last = wrap_position(self.last_processed_pos_);
        let wrapped_in = wrap_position(self.input_pos_);
        self.last_processed_pos_ = self.input_pos_;
        wrapped_in < wrapped_last
    }

    fn extend_last_command(&mut self, bytes: &mut u32, wrapped_last_processed_pos: &mut u32) {
        let last_cmd_idx = self.num_commands_ - 1;
        let data = self.ringbuffer_.buffer();
        let mask = self.ringbuffer_.mask_;
        let max_backward_distance = (1u64 << self.params.lgwin) - BROTLI_WINDOW_GAP as u64;
        let last_copy_len = (self.commands_[last_cmd_idx].copy_len_ & 0x1FFFFFF) as u64;
        let last_processed_pos = self.last_processed_pos_ - last_copy_len;
        let max_distance = last_processed_pos.min(max_backward_distance);
        let cmd_dist = self.dist_cache_[0] as u64;
        let distance_code = self.commands_[last_cmd_idx].restore_distance_code(&self.params.dist);
        let dict = &self.params.dictionary.compound;
        let compound_dictionary_size = dict.total_size;

        if distance_code < BROTLI_NUM_DISTANCE_SHORT_CODES as u32
            || (distance_code - (BROTLI_NUM_DISTANCE_SHORT_CODES as u32 - 1)) as u64 == cmd_dist
        {
            if cmd_dist <= max_distance {
                let d32 = cmd_dist as u32;
                while *bytes != 0
                    && data[(*wrapped_last_processed_pos & mask) as usize]
                        == data[(((*wrapped_last_processed_pos).wrapping_sub(d32)) & mask) as usize]
                {
                    self.commands_[last_cmd_idx].copy_len_ += 1;
                    *bytes -= 1;
                    *wrapped_last_processed_pos = wrapped_last_processed_pos.wrapping_add(1);
                }
            } else if (cmd_dist - max_distance - 1) < compound_dictionary_size as u64
                && last_copy_len < (cmd_dist - max_distance)
            {
                let address = compound_dictionary_size - (cmd_dist - max_distance) as usize
                    + last_copy_len as usize;
                let mut br_index = 0usize;
                while address >= dict.chunk_offsets[br_index + 1] {
                    br_index += 1;
                }
                let mut br_offset = address - dict.chunk_offsets[br_index];
                let mut chunk = &dict.chunks[br_index].source;
                let mut chunk_length =
                    dict.chunk_offsets[br_index + 1] - dict.chunk_offsets[br_index];
                while *bytes != 0
                    && data[(*wrapped_last_processed_pos & mask) as usize] == chunk[br_offset]
                {
                    self.commands_[last_cmd_idx].copy_len_ += 1;
                    *bytes -= 1;
                    *wrapped_last_processed_pos = wrapped_last_processed_pos.wrapping_add(1);
                    br_offset += 1;
                    if br_offset == chunk_length {
                        br_index += 1;
                        br_offset = 0;
                        if br_index != dict.num_chunks {
                            chunk = &dict.chunks[br_index].source;
                            chunk_length =
                                dict.chunk_offsets[br_index + 1] - dict.chunk_offsets[br_index];
                        } else {
                            break;
                        }
                    }
                }
            }
            let cmd = &mut self.commands_[last_cmd_idx];
            let copy_len = ((cmd.copy_len_ & 0x1FFFFFF) as usize) + ((cmd.copy_len_ >> 25) as usize);
            cmd.cmd_prefix_ = get_length_code(
                cmd.insert_len_ as usize,
                copy_len,
                (cmd.dist_prefix_ & 0x3FF) == 0,
            );
        }
    }

    fn ensure_initialized(&mut self) -> bool {
        if self.is_initialized_ {
            return true;
        }
        self.last_bytes_bits_ = 0;
        self.last_bytes_ = 0;
        self.flint_ = BrotliEncoderFlintState::Done as i8;
        self.remaining_metadata_bytes_ = u32::MAX;

        sanitize_params(&mut self.params);
        self.params.lgblock = compute_lg_block(&self.params);
        choose_distance_params(&mut self.params);

        if self.params.stream_offset != 0 {
            self.flint_ = BrotliEncoderFlintState::Needs2Bytes as i8;
            self.dist_cache_[0] = -16;
            self.dist_cache_[1] = -16;
            self.dist_cache_[2] = -16;
            self.dist_cache_[3] = -16;
            self.saved_dist_cache_.copy_from_slice(&self.dist_cache_[..4]);
        }

        self.ringbuffer_.setup(&self.params);

        {
            let mut lgwin = self.params.lgwin;
            if self.params.quality == FAST_ONE_PASS_COMPRESSION_QUALITY
                || self.params.quality == FAST_TWO_PASS_COMPRESSION_QUALITY
            {
                lgwin = lgwin.max(18);
            }
            if self.params.stream_offset == 0 {
                encode_window_bits(
                    lgwin,
                    self.params.large_window,
                    &mut self.last_bytes_,
                    &mut self.last_bytes_bits_,
                );
            } else {
                self.params.stream_offset = self
                    .params
                    .stream_offset
                    .min(brotli_max_backward_limit(lgwin as u32));
            }
        }

        if self.params.quality == FAST_ONE_PASS_COMPRESSION_QUALITY {
            let mut arena = Box::new(BrotliOnePassArena::new());
            init_command_prefix_codes(&mut arena);
            self.one_pass_arena_ = Some(arena);
        } else if self.params.quality == FAST_TWO_PASS_COMPRESSION_QUALITY {
            self.two_pass_arena_ = Some(Box::new(BrotliTwoPassArena::new()));
        }

        self.is_initialized_ = true;
        true
    }

    fn encode_data(&mut self, is_last: bool, force_flush: bool) -> bool {
        let delta = self.unprocessed_input_size();
        let mut bytes = delta as u32;
        let mut wrapped_last_processed_pos = wrap_position(self.last_processed_pos_);
        let fast_compress = self.params.quality == FAST_ONE_PASS_COMPRESSION_QUALITY
            || self.params.quality == FAST_TWO_PASS_COMPRESSION_QUALITY;

        if delta == 0 {
            if self.ringbuffer_.data().is_empty() {
                if is_last {
                    debug_assert!(self.last_bytes_bits_ <= 14);
                    self.last_bytes_ |= 3u16 << self.last_bytes_bits_;
                    self.last_bytes_bits_ += 2;
                    self.tiny_buf_[0] = self.last_bytes_ as u8;
                    self.tiny_buf_[1] = (self.last_bytes_ >> 8) as u8;
                    self.out_source_ = OutputSource::TinyBuf;
                    self.out_pos_ = 0;
                    self.available_out_ = (self.last_bytes_bits_ as usize + 7) >> 3;
                    return true;
                } else {
                    self.out_source_ = OutputSource::None;
                    self.out_pos_ = 0;
                    self.available_out_ = 0;
                    return true;
                }
            } else if !is_last && (!force_flush || fast_compress) {
                self.out_source_ = OutputSource::None;
                self.out_pos_ = 0;
                self.available_out_ = 0;
                return true;
            }
        }
        debug_assert!(!self.ringbuffer_.data().is_empty());

        if self.params.quality > self.params.dictionary.max_quality {
            return false;
        }
        if self.is_last_block_emitted_ {
            return false;
        }
        if is_last {
            self.is_last_block_emitted_ = true;
        }

        if delta > self.input_block_size() as u64 {
            return false;
        }
        if self.params.quality == FAST_TWO_PASS_COMPRESSION_QUALITY && self.command_buf_.is_empty()
        {
            self.command_buf_
                .resize(K_COMPRESS_FRAGMENT_TWO_PASS_BLOCK_SIZE, 0);
            self.literal_buf_
                .resize(K_COMPRESS_FRAGMENT_TWO_PASS_BLOCK_SIZE, 0);
        }

        if fast_compress {
            let mut storage_ix = self.last_bytes_bits_ as usize;
            let bytes_usize = bytes as usize;
            self.ensure_storage(2 * bytes_usize + 503);
            self.storage_[0] = self.last_bytes_ as u8;
            self.storage_[1] = (self.last_bytes_ >> 8) as u8;

            let mask = self.ringbuffer_.mask_ as usize;
            let data = self.ringbuffer_.buffer();
            let input_slice = &data[(wrapped_last_processed_pos as usize) & mask..];

            let max_table_size = max_hash_table_size(self.params.quality);
            let mut htsize = hash_table_size(max_table_size, bytes_usize);
            if self.params.quality == FAST_ONE_PASS_COMPRESSION_QUALITY && (htsize & 0xAAAAA) == 0 {
                htsize <<= 1;
            }
            let use_small = htsize <= self.small_table_.len();
            if use_small {
                self.small_table_[..htsize].fill(0);
            } else {
                if htsize > self.large_table_.len() {
                    self.large_table_.resize(htsize, 0);
                }
                self.large_table_[..htsize].fill(0);
            }
            let table: &mut [i32] = if use_small {
                &mut self.small_table_[..htsize]
            } else {
                &mut self.large_table_[..htsize]
            };

            if self.params.quality == FAST_ONE_PASS_COMPRESSION_QUALITY {
                let arena = self.one_pass_arena_.as_mut().unwrap();
                brotli_compress_fragment_fast(
                    arena,
                    input_slice,
                    bytes_usize,
                    is_last,
                    table,
                    htsize,
                    &mut storage_ix,
                    &mut self.storage_,
                );
            } else {
                let arena = self.two_pass_arena_.as_mut().unwrap();
                brotli_compress_fragment_two_pass(
                    arena,
                    input_slice,
                    bytes_usize,
                    is_last,
                    &mut self.command_buf_,
                    &mut self.literal_buf_,
                    table,
                    htsize,
                    &mut storage_ix,
                    &mut self.storage_,
                );
            }
            self.last_bytes_ = self.storage_[storage_ix >> 3] as u16;
            self.last_bytes_bits_ = (storage_ix & 7) as u8;
            self.update_last_processed_pos();
            self.out_source_ = OutputSource::Storage;
            self.out_pos_ = 0;
            self.available_out_ = storage_ix >> 3;
            return true;
        }

        {
            let newsize = self.num_commands_ + (bytes as usize) / 2 + 1;
            if newsize > self.commands_.capacity() {
                let extra = (bytes as usize) / 4 + 16;
                self.commands_.reserve(newsize + extra - self.commands_.len());
            }
            if self.commands_.len() > self.num_commands_ {
                self.commands_.truncate(self.num_commands_);
            }
        }

        let mask = self.ringbuffer_.mask_ as usize;
        {
            let data = self.ringbuffer_.buffer();
            self.hasher_.init_or_stitch_to_previous_block(
                &mut self.params,
                data,
                mask,
                wrapped_last_processed_pos as usize,
                bytes as usize,
                is_last,
            );
        }

        let literal_context_mode = choose_context_mode(
            &self.params,
            self.ringbuffer_.buffer(),
            wrap_position(self.last_flush_pos_) as usize,
            mask,
            (self.input_pos_ - self.last_flush_pos_) as usize,
        );
        let literal_context_lut = brotli_context_lut(literal_context_mode);

        if self.num_commands_ > 0 && self.last_insert_len_ == 0 {
            self.extend_last_command(&mut bytes, &mut wrapped_last_processed_pos);
        }

        {
            let data = self.ringbuffer_.buffer();
            if self.params.quality == ZOPFLIFICATION_QUALITY {
                debug_assert_eq!(self.params.hasher.type_, 10);
                brotli_create_zopfli_backward_references(
                    bytes as usize,
                    wrapped_last_processed_pos as usize,
                    data,
                    mask,
                    literal_context_lut,
                    &self.params,
                    &mut self.hasher_,
                    &mut self.dist_cache_,
                    &mut self.last_insert_len_,
                    &mut self.commands_,
                    &mut self.num_commands_,
                    &mut self.num_literals_,
                );
            } else if self.params.quality == HQ_ZOPFLIFICATION_QUALITY {
                debug_assert_eq!(self.params.hasher.type_, 10);
                brotli_create_hq_zopfli_backward_references(
                    bytes as usize,
                    wrapped_last_processed_pos as usize,
                    data,
                    mask,
                    literal_context_lut,
                    &self.params,
                    &mut self.hasher_,
                    &mut self.dist_cache_,
                    &mut self.last_insert_len_,
                    &mut self.commands_,
                    &mut self.num_commands_,
                    &mut self.num_literals_,
                );
            } else {
                brotli_create_backward_references(
                    bytes as usize,
                    wrapped_last_processed_pos as usize,
                    data,
                    mask,
                    literal_context_lut,
                    &self.params,
                    &mut self.hasher_,
                    &mut self.dist_cache_,
                    &mut self.last_insert_len_,
                    &mut self.commands_,
                    &mut self.num_commands_,
                    &mut self.num_literals_,
                );
            }
        }

        {
            let max_length = max_metablock_size(&self.params);
            let max_literals = max_length / 8;
            let max_commands = max_length / 8;
            let processed_bytes = (self.input_pos_ - self.last_flush_pos_) as usize;
            let next_input_fits_metablock = processed_bytes + self.input_block_size() <= max_length;
            let should_flush = self.params.quality < MIN_QUALITY_FOR_BLOCK_SPLIT
                && self.num_literals_ + self.num_commands_ >= MAX_NUM_DELAYED_SYMBOLS;
            if !is_last
                && !force_flush
                && !should_flush
                && next_input_fits_metablock
                && self.num_literals_ < max_literals
                && self.num_commands_ < max_commands
            {
                if self.update_last_processed_pos() {
                    self.hasher_.reset();
                }
                self.out_source_ = OutputSource::None;
                self.out_pos_ = 0;
                self.available_out_ = 0;
                return true;
            }
        }

        if self.last_insert_len_ > 0 {
            self.commands_.push(Command::new_insert(self.last_insert_len_));
            self.num_commands_ += 1;
            self.num_literals_ += self.last_insert_len_;
            self.last_insert_len_ = 0;
        }

        if !is_last && self.input_pos_ == self.last_flush_pos_ {
            self.out_source_ = OutputSource::None;
            self.out_pos_ = 0;
            self.available_out_ = 0;
            return true;
        }

        debug_assert!(self.input_pos_ >= self.last_flush_pos_);
        debug_assert!(self.input_pos_ > self.last_flush_pos_ || is_last);
        debug_assert!(self.input_pos_ - self.last_flush_pos_ <= (1u64 << 24));

        let metablock_size = (self.input_pos_ - self.last_flush_pos_) as usize;
        self.ensure_storage(2 * metablock_size + 503);
        let mut storage_ix = self.last_bytes_bits_ as usize;
        self.storage_[0] = self.last_bytes_ as u8;
        self.storage_[1] = (self.last_bytes_ >> 8) as u8;

        {
            let data = self.ringbuffer_.buffer();
            let b64_regions = &self.hasher_.common.base64_regions;
            write_meta_block_internal(
                data,
                mask,
                self.last_flush_pos_,
                metablock_size,
                is_last,
                b64_regions,
                literal_context_mode,
                &self.params,
                self.prev_byte_,
                self.prev_byte2_,
                self.num_literals_,
                &mut self.commands_[..self.num_commands_],
                &self.saved_dist_cache_,
                &mut self.dist_cache_,
                &mut storage_ix,
                &mut self.storage_,
            );
        }

        self.hasher_.common.base64_regions.clear();
        self.last_bytes_ = self.storage_[storage_ix >> 3] as u16;
        self.last_bytes_bits_ = (storage_ix & 7) as u8;
        self.last_flush_pos_ = self.input_pos_;
        if self.update_last_processed_pos() {
            self.hasher_.reset();
        }
        {
            let data = self.ringbuffer_.buffer();
            if self.last_flush_pos_ > 0 {
                self.prev_byte_ = data[((self.last_flush_pos_ as u32).wrapping_sub(1) & (mask as u32)) as usize];
            }
            if self.last_flush_pos_ > 1 {
                self.prev_byte2_ = data[((self.last_flush_pos_ as u32).wrapping_sub(2) & (mask as u32)) as usize];
            }
        }
        self.commands_.clear();
        self.num_commands_ = 0;
        self.num_literals_ = 0;
        self.saved_dist_cache_.copy_from_slice(&self.dist_cache_[..4]);
        self.out_source_ = OutputSource::Storage;
        self.out_pos_ = 0;
        self.available_out_ = storage_ix >> 3;
        true
    }

    fn write_metadata_header(&mut self, block_size: usize) -> usize {
        let mut storage_ix = self.last_bytes_bits_ as usize;
        self.tiny_buf_[0] = self.last_bytes_ as u8;
        self.tiny_buf_[1] = (self.last_bytes_ >> 8) as u8;
        self.last_bytes_ = 0;
        self.last_bytes_bits_ = 0;

        brotli_write_bits(1, 0, &mut storage_ix, &mut self.tiny_buf_);
        brotli_write_bits(2, 3, &mut storage_ix, &mut self.tiny_buf_);
        brotli_write_bits(1, 0, &mut storage_ix, &mut self.tiny_buf_);
        if block_size == 0 {
            brotli_write_bits(2, 0, &mut storage_ix, &mut self.tiny_buf_);
        } else {
            let nbits = if block_size == 1 {
                1
            } else {
                log2_floor_non_zero(block_size - 1) + 1
            };
            let nbytes = (nbits + 7) / 8;
            brotli_write_bits(2, nbytes as u64, &mut storage_ix, &mut self.tiny_buf_);
            brotli_write_bits(
                8 * (nbytes as usize),
                (block_size - 1) as u64,
                &mut storage_ix,
                &mut self.tiny_buf_,
            );
        }
        (storage_ix + 7) >> 3
    }

    fn inject_byte_padding_block(&mut self) {
        let mut seal = self.last_bytes_ as u32;
        let mut seal_bits = self.last_bytes_bits_ as usize;
        self.last_bytes_ = 0;
        self.last_bytes_bits_ = 0;
        seal |= 0x6u32 << seal_bits;
        seal_bits += 6;

        let added = (seal_bits + 7) >> 3;
        match self.out_source_ {
            OutputSource::Storage => {
                let offset = self.out_pos_ + self.available_out_;
                if self.storage_.len() < offset + 4 {
                    self.storage_.resize(offset + 4, 0);
                }
                self.storage_[offset] = seal as u8;
                if seal_bits > 8 {
                    self.storage_[offset + 1] = (seal >> 8) as u8;
                }
                if seal_bits > 16 {
                    self.storage_[offset + 2] = (seal >> 16) as u8;
                }
            }
            OutputSource::TinyBuf => {
                let offset = self.out_pos_ + self.available_out_;
                self.tiny_buf_[offset] = seal as u8;
                if seal_bits > 8 {
                    self.tiny_buf_[offset + 1] = (seal >> 8) as u8;
                }
                if seal_bits > 16 {
                    self.tiny_buf_[offset + 2] = (seal >> 16) as u8;
                }
            }
            OutputSource::None => {
                self.out_source_ = OutputSource::TinyBuf;
                self.out_pos_ = 0;
                self.available_out_ = 0;
                self.tiny_buf_[0] = seal as u8;
                if seal_bits > 8 {
                    self.tiny_buf_[1] = (seal >> 8) as u8;
                }
                if seal_bits > 16 {
                    self.tiny_buf_[2] = (seal >> 16) as u8;
                }
            }
        }
        self.available_out_ += added;
    }

    #[inline(always)]
    fn set_total_out(&self, total_out: Option<&mut usize>) {
        if let Some(t) = total_out {
            *t = if self.total_out_ < (usize::MAX as u64) {
                self.total_out_ as usize
            } else {
                usize::MAX
            };
        }
    }

    fn inject_flush_or_push_output(
        &mut self,
        output: &mut [u8],
        out_pos: &mut usize,
        total_out: Option<&mut usize>,
    ) -> bool {
        if self.stream_state_ == BrotliEncoderStreamState::FlushRequested && self.last_bytes_bits_ != 0
        {
            self.inject_byte_padding_block();
            return true;
        }

        let avail_out = output.len().saturating_sub(*out_pos);
        if self.available_out_ != 0 && avail_out != 0 {
            let copy_size = self.available_out_.min(avail_out);
            let src = match self.out_source_ {
                OutputSource::TinyBuf => &self.tiny_buf_[self.out_pos_..self.out_pos_ + copy_size],
                OutputSource::Storage => &self.storage_[self.out_pos_..self.out_pos_ + copy_size],
                OutputSource::None => unreachable!(),
            };
            output[*out_pos..*out_pos + copy_size].copy_from_slice(src);
            *out_pos += copy_size;
            self.out_pos_ += copy_size;
            self.available_out_ -= copy_size;
            self.total_out_ += copy_size as u64;
            self.set_total_out(total_out);
            return true;
        }
        false
    }

    fn check_flush_complete(&mut self) {
        if self.stream_state_ == BrotliEncoderStreamState::FlushRequested && self.available_out_ == 0
        {
            self.stream_state_ = BrotliEncoderStreamState::Processing;
            self.out_source_ = OutputSource::None;
            self.out_pos_ = 0;
        }
    }

    fn compress_stream_fast(
        &mut self,
        op: BrotliEncoderOperation,
        input: &[u8],
        in_pos: &mut usize,
        output: &mut [u8],
        out_pos: &mut usize,
        mut total_out: Option<&mut usize>,
    ) -> bool {
        let block_size_limit = 1usize << self.params.lgwin;
        let avail_in_initial = input.len().saturating_sub(*in_pos);
        let buf_size = K_COMPRESS_FRAGMENT_TWO_PASS_BLOCK_SIZE
            .min(avail_in_initial.min(block_size_limit));
        let mut tmp_command_buf = Vec::new();
        let mut tmp_literal_buf = Vec::new();

        if self.params.quality != FAST_ONE_PASS_COMPRESSION_QUALITY
            && self.params.quality != FAST_TWO_PASS_COMPRESSION_QUALITY
        {
            return false;
        }
        if self.params.quality == FAST_TWO_PASS_COMPRESSION_QUALITY {
            if self.command_buf_.is_empty() && buf_size == K_COMPRESS_FRAGMENT_TWO_PASS_BLOCK_SIZE {
                self.command_buf_
                    .resize(K_COMPRESS_FRAGMENT_TWO_PASS_BLOCK_SIZE, 0);
                self.literal_buf_
                    .resize(K_COMPRESS_FRAGMENT_TWO_PASS_BLOCK_SIZE, 0);
            }
            if self.command_buf_.is_empty() {
                tmp_command_buf.resize(buf_size, 0);
                tmp_literal_buf.resize(buf_size, 0);
            }
        }

        loop {
            if self.inject_flush_or_push_output(output, out_pos, total_out.as_deref_mut()) {
                continue;
            }

            let avail_in = input.len().saturating_sub(*in_pos);
            if self.available_out_ == 0
                && self.stream_state_ == BrotliEncoderStreamState::Processing
                && (avail_in != 0 || op != BrotliEncoderOperation::Process)
            {
                let block_size = block_size_limit.min(avail_in);
                let is_last = (avail_in == block_size) && (op == BrotliEncoderOperation::Finish);
                let force_flush = (avail_in == block_size) && (op == BrotliEncoderOperation::Flush);
                let max_out_size = 2 * block_size + 503;

                if force_flush && block_size == 0 {
                    self.stream_state_ = BrotliEncoderStreamState::FlushRequested;
                    continue;
                }

                let avail_out = output.len().saturating_sub(*out_pos);
                let inplace = max_out_size <= avail_out;
                if !inplace {
                    self.ensure_storage(max_out_size);
                }

                let mut storage_ix = self.last_bytes_bits_ as usize;
                let max_table_size = max_hash_table_size(self.params.quality);
                let mut htsize = hash_table_size(max_table_size, block_size);
                if self.params.quality == FAST_ONE_PASS_COMPRESSION_QUALITY && (htsize & 0xAAAAA) == 0 {
                    htsize <<= 1;
                }
                let use_small = htsize <= self.small_table_.len();
                if use_small {
                    self.small_table_[..htsize].fill(0);
                } else {
                    if htsize > self.large_table_.len() {
                        self.large_table_.resize(htsize, 0);
                    }
                    self.large_table_[..htsize].fill(0);
                }
                let table: &mut [i32] = if use_small {
                    &mut self.small_table_[..htsize]
                } else {
                    &mut self.large_table_[..htsize]
                };

                let storage: &mut [u8] = if inplace {
                    &mut output[*out_pos..]
                } else {
                    &mut self.storage_
                };
                storage[0] = self.last_bytes_ as u8;
                storage[1] = (self.last_bytes_ >> 8) as u8;

                let in_slice = &input[*in_pos..*in_pos + block_size];
                if self.params.quality == FAST_ONE_PASS_COMPRESSION_QUALITY {
                    let arena = self.one_pass_arena_.as_mut().unwrap();
                    brotli_compress_fragment_fast(
                        arena,
                        in_slice,
                        block_size,
                        is_last,
                        table,
                        htsize,
                        &mut storage_ix,
                        storage,
                    );
                } else {
                    let arena = self.two_pass_arena_.as_mut().unwrap();
                    let (cmd_buf, lit_buf) = if !self.command_buf_.is_empty() {
                        (&mut self.command_buf_[..], &mut self.literal_buf_[..])
                    } else {
                        (&mut tmp_command_buf[..], &mut tmp_literal_buf[..])
                    };
                    brotli_compress_fragment_two_pass(
                        arena,
                        in_slice,
                        block_size,
                        is_last,
                        cmd_buf,
                        lit_buf,
                        table,
                        htsize,
                        &mut storage_ix,
                        storage,
                    );
                }

                if block_size != 0 {
                    *in_pos += block_size;
                    self.total_in_ += block_size as u64;
                }

                let out_bytes = storage_ix >> 3;
                self.last_bytes_ = storage[storage_ix >> 3] as u16;
                self.last_bytes_bits_ = (storage_ix & 7) as u8;

                if inplace {
                    *out_pos += out_bytes;
                    self.total_out_ += out_bytes as u64;
                    self.set_total_out(total_out.as_deref_mut());
                } else {
                    self.out_source_ = OutputSource::Storage;
                    self.out_pos_ = 0;
                    self.available_out_ = out_bytes;
                }

                if force_flush {
                    self.stream_state_ = BrotliEncoderStreamState::FlushRequested;
                }
                if is_last {
                    self.stream_state_ = BrotliEncoderStreamState::Finished;
                }
                continue;
            }
            break;
        }
        self.check_flush_complete();
        true
    }

    fn process_metadata(
        &mut self,
        input: &[u8],
        in_pos: &mut usize,
        output: &mut [u8],
        out_pos: &mut usize,
        mut total_out: Option<&mut usize>,
    ) -> bool {
        let avail_in = input.len().saturating_sub(*in_pos);
        if avail_in > (1usize << 24) {
            return false;
        }
        if self.stream_state_ == BrotliEncoderStreamState::Processing {
            self.remaining_metadata_bytes_ = avail_in as u32;
            self.stream_state_ = BrotliEncoderStreamState::MetadataHead;
        }
        if self.stream_state_ != BrotliEncoderStreamState::MetadataHead
            && self.stream_state_ != BrotliEncoderStreamState::MetadataBody
        {
            return false;
        }

        loop {
            if self.inject_flush_or_push_output(output, out_pos, total_out.as_deref_mut()) {
                continue;
            }
            if self.available_out_ != 0 {
                break;
            }

            if self.input_pos_ != self.last_flush_pos_ {
                if !self.encode_data(false, true) {
                    return false;
                }
                continue;
            }

            if self.stream_state_ == BrotliEncoderStreamState::MetadataHead {
                let rem = self.remaining_metadata_bytes_ as usize;
                let header_len = self.write_metadata_header(rem);
                self.out_source_ = OutputSource::TinyBuf;
                self.out_pos_ = 0;
                self.available_out_ = header_len;
                self.stream_state_ = BrotliEncoderStreamState::MetadataBody;
                continue;
            } else {
                if self.remaining_metadata_bytes_ == 0 {
                    self.remaining_metadata_bytes_ = u32::MAX;
                    self.stream_state_ = BrotliEncoderStreamState::Processing;
                    break;
                }
                let avail_out = output.len().saturating_sub(*out_pos);
                if avail_out != 0 {
                    let copy = (self.remaining_metadata_bytes_ as usize).min(avail_out);
                    output[*out_pos..*out_pos + copy]
                        .copy_from_slice(&input[*in_pos..*in_pos + copy]);
                    *in_pos += copy;
                    self.total_in_ += copy as u64;
                    self.remaining_metadata_bytes_ -= copy as u32;
                    *out_pos += copy;
                } else {
                    let copy = (self.remaining_metadata_bytes_ as usize).min(16);
                    self.tiny_buf_[..copy].copy_from_slice(&input[*in_pos..*in_pos + copy]);
                    *in_pos += copy;
                    self.total_in_ += copy as u64;
                    self.remaining_metadata_bytes_ -= copy as u32;
                    self.out_source_ = OutputSource::TinyBuf;
                    self.out_pos_ = 0;
                    self.available_out_ = copy;
                }
                continue;
            }
        }
        true
    }

    fn update_size_hint(&mut self, available_in: usize) {
        if self.params.size_hint == 0 {
            let delta = self.unprocessed_input_size();
            let tail = available_in as u64;
            let limit = 1u64 << 30;
            let total = if delta >= limit || tail >= limit || delta + tail >= limit {
                limit as usize
            } else {
                (delta + tail) as usize
            };
            self.params.size_hint = total;
        }
    }

    pub fn compress_stream(
        &mut self,
        op: BrotliEncoderOperation,
        input: &[u8],
        in_pos: &mut usize,
        output: &mut [u8],
        out_pos: &mut usize,
        mut total_out: Option<&mut usize>,
    ) -> bool {
        if !self.ensure_initialized() {
            return false;
        }

        if self.remaining_metadata_bytes_ != u32::MAX {
            let avail_in = input.len().saturating_sub(*in_pos);
            if avail_in != self.remaining_metadata_bytes_ as usize {
                return false;
            }
            if op != BrotliEncoderOperation::EmitMetadata {
                return false;
            }
        }

        if op == BrotliEncoderOperation::EmitMetadata {
            self.update_size_hint(0);
            return self.process_metadata(input, in_pos, output, out_pos, total_out);
        }

        if self.stream_state_ == BrotliEncoderStreamState::MetadataHead
            || self.stream_state_ == BrotliEncoderStreamState::MetadataBody
        {
            return false;
        }

        if self.stream_state_ != BrotliEncoderStreamState::Processing && *in_pos < input.len() {
            return false;
        }

        if self.params.quality == FAST_ONE_PASS_COMPRESSION_QUALITY
            || self.params.quality == FAST_TWO_PASS_COMPRESSION_QUALITY
        {
            return self.compress_stream_fast(op, input, in_pos, output, out_pos, total_out);
        }

        loop {
            let mut remaining_block_size = self.remaining_input_block_size();
            if self.flint_ >= 0 && remaining_block_size > self.flint_ as usize {
                remaining_block_size = self.flint_ as usize;
            }

            let avail_in = input.len().saturating_sub(*in_pos);
            if remaining_block_size != 0 && avail_in != 0 {
                let copy_input_size = remaining_block_size.min(avail_in);
                self.copy_input_to_ringbuffer(&input[*in_pos..*in_pos + copy_input_size]);
                *in_pos += copy_input_size;
                self.total_in_ += copy_input_size as u64;
                if self.flint_ > 0 {
                    self.flint_ -= copy_input_size as i8;
                }
                continue;
            }

            if self.inject_flush_or_push_output(output, out_pos, total_out.as_deref_mut()) {
                if self.flint_ == BrotliEncoderFlintState::WaitingForFlushing as i8 {
                    self.check_flush_complete();
                    if self.stream_state_ == BrotliEncoderStreamState::Processing {
                        self.flint_ = BrotliEncoderFlintState::Done as i8;
                    }
                }
                continue;
            }

            if self.available_out_ == 0
                && self.stream_state_ == BrotliEncoderStreamState::Processing
                && (remaining_block_size == 0 || op != BrotliEncoderOperation::Process)
            {
                let avail_in = input.len().saturating_sub(*in_pos);
                let is_last = (avail_in == 0) && op == BrotliEncoderOperation::Finish;
                let mut force_flush = (avail_in == 0) && op == BrotliEncoderOperation::Flush;
                if !is_last && self.flint_ == 0 {
                    self.flint_ = BrotliEncoderFlintState::WaitingForFlushing as i8;
                    force_flush = true;
                }
                self.update_size_hint(avail_in);
                if !self.encode_data(is_last, force_flush) {
                    return false;
                }
                if force_flush {
                    self.stream_state_ = BrotliEncoderStreamState::FlushRequested;
                }
                if is_last {
                    self.stream_state_ = BrotliEncoderStreamState::Finished;
                }
                continue;
            }
            break;
        }
        self.check_flush_complete();
        true
    }

    pub fn is_finished(&self) -> bool {
        self.stream_state_ == BrotliEncoderStreamState::Finished && !self.has_more_output()
    }

    pub fn has_more_output(&self) -> bool {
        self.available_out_ != 0
    }

    pub fn take_output(&mut self, max_size: Option<usize>) -> &[u8] {
        let mut consumed_size = self.available_out_;
        if let Some(limit) = max_size {
            if limit != 0 {
                consumed_size = consumed_size.min(limit);
            }
        }
        if consumed_size == 0 {
            return &[];
        }
        let old_pos = self.out_pos_;
        self.out_pos_ += consumed_size;
        self.available_out_ -= consumed_size;
        self.total_out_ += consumed_size as u64;
        if self.stream_state_ == BrotliEncoderStreamState::FlushRequested
            && self.available_out_ == 0
        {
            self.stream_state_ = BrotliEncoderStreamState::Processing;
        }
        match self.out_source_ {
            OutputSource::TinyBuf => &self.tiny_buf_[old_pos..old_pos + consumed_size],
            OutputSource::Storage => &self.storage_[old_pos..old_pos + consumed_size],
            OutputSource::None => &[],
        }
    }

    pub fn total_in(&self) -> u64 {
        self.total_in_
    }

    pub fn total_out(&self) -> u64 {
        self.total_out_
    }
}

fn init_params(params: &mut BrotliEncoderParams) {
    *params = BrotliEncoderParams::default();
}

#[inline(always)]
fn wrap_position(position: u64) -> u32 {
    let mut result = position as u32;
    let gb = position >> 30;
    if gb > 2 {
        result = (result & ((1u32 << 30) - 1)) | ((((gb - 1) & 1) as u32 + 1) << 30);
    }
    result
}

fn hash_table_size(max_table_size: usize, input_size: usize) -> usize {
    let mut htsize = 256usize;
    while htsize < max_table_size && htsize < input_size {
        htsize <<= 1;
    }
    htsize
}

fn encode_window_bits(lgwin: i32, large_window: bool, last_bytes: &mut u16, last_bytes_bits: &mut u8) {
    if large_window {
        *last_bytes = (((lgwin & 0x3F) << 8) | 0x11) as u16;
        *last_bytes_bits = 14;
    } else if lgwin == 16 {
        *last_bytes = 0;
        *last_bytes_bits = 1;
    } else if lgwin == 17 {
        *last_bytes = 1;
        *last_bytes_bits = 7;
    } else if lgwin > 17 {
        *last_bytes = (((lgwin - 17) << 1) | 0x01) as u16;
        *last_bytes_bits = 4;
    } else {
        *last_bytes = (((lgwin - 8) << 4) | 0x01) as u16;
        *last_bytes_bits = 7;
    }
}

fn init_command_prefix_codes(s: &mut BrotliOnePassArena) {
    const K_DEFAULT_COMMAND_DEPTHS: [u8; 128] = [
        0, 4, 4, 5, 6, 6, 7, 7, 7, 7, 7, 8, 8, 8, 8, 8,
        0, 0, 0, 4, 4, 4, 4, 4, 5, 5, 6, 6, 6, 6, 7, 7,
        7, 7, 10, 10, 10, 10, 10, 10, 0, 4, 4, 5, 5, 5, 6, 6,
        7, 8, 8, 9, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10,
        5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        6, 6, 6, 6, 6, 6, 5, 5, 5, 5, 5, 5, 4, 4, 4, 4,
        4, 4, 4, 5, 5, 5, 5, 5, 5, 6, 6, 7, 7, 7, 8, 10,
        12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 12, 0, 0, 0, 0,
    ];
    const K_DEFAULT_COMMAND_BITS: [u16; 128] = [
        0,   0,   8,   9,   3,  35,   7,   71,
        39, 103,  23,  47, 175, 111, 239,   31,
        0,   0,   0,   4,  12,   2,  10,    6,
        13,  29,  11,  43,  27,  59,  87,   55,
        15,  79, 319, 831, 191, 703, 447,  959,
        0,  14,   1,  25,   5,  21,  19,   51,
        119, 159,  95, 223, 479, 991,  63,  575,
        127, 639, 383, 895, 255, 767, 511, 1023,
        14, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
        27, 59, 7, 39, 23, 55, 30, 1, 17, 9, 25, 5, 0, 8, 4, 12,
        2, 10, 6, 21, 13, 29, 3, 19, 11, 15, 47, 31, 95, 63, 127, 255,
        767, 2815, 1791, 3839, 511, 2559, 1535, 3583, 1023, 3071, 2047, 4095, 0, 0, 0, 0,
    ];
    const K_DEFAULT_COMMAND_CODE: [u8; 57] = [
        0xff, 0x77, 0xd5, 0xbf, 0xe7, 0xde, 0xea, 0x9e, 0x51, 0x5d, 0xde, 0xc6,
        0x70, 0x57, 0xbc, 0x58, 0x58, 0x58, 0xd8, 0xd8, 0x58, 0xd5, 0xcb, 0x8c,
        0xea, 0xe0, 0xc3, 0x87, 0x1f, 0x83, 0xc1, 0x60, 0x1c, 0x67, 0xb2, 0xaa,
        0x06, 0x83, 0xc1, 0x60, 0x30, 0x18, 0xcc, 0xa1, 0xce, 0x88, 0x54, 0x94,
        0x46, 0xe1, 0xb0, 0xd0, 0x4e, 0xb2, 0xf7, 0x04, 0x00,
    ];
    const K_DEFAULT_COMMAND_CODE_NUM_BITS: usize = 448;
    s.cmd_depth = K_DEFAULT_COMMAND_DEPTHS;
    s.cmd_bits = K_DEFAULT_COMMAND_BITS;
    s.cmd_code[..K_DEFAULT_COMMAND_CODE.len()].copy_from_slice(&K_DEFAULT_COMMAND_CODE);
    s.cmd_code_numbits = K_DEFAULT_COMMAND_CODE_NUM_BITS;
}

fn estimate_entropy(population: &[u32]) -> f64 {
    let mut total = 0usize;
    let mut result = 0.0f64;
    for &p in population {
        total += p as usize;
        result += (p as f64) * fast_log2(p as usize);
    }
    (total as f64) * fast_log2(total) - result
}

const K_STATIC_CONTEXT_MAP_CONTINUATION: [u32; 64] = [
    1, 1, 2, 2, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
];

const K_STATIC_CONTEXT_MAP_SIMPLE_UTF8: [u32; 64] = [
    0, 0, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
];

const K_STATIC_CONTEXT_MAP_COMPLEX_UTF8: [u32; 64] = [
    11, 11, 12, 12,
    0, 0, 0, 0,
    1, 1, 9, 9,
    2, 2, 2, 2,
    1, 1, 1, 1,
    8, 3, 3, 3,
    1, 1, 1, 1,
    2, 2, 2, 2,
    8, 4, 4, 4,
    8, 7, 4, 4,
    8, 0, 0, 0,
    3, 3, 3, 3,
    5, 5, 10, 5,
    5, 5, 10, 5,
    6, 6, 6, 6,
    6, 6, 6, 6,
];

fn choose_context_map(
    quality: i32,
    bigram_histo: &[u32; 9],
    num_literal_contexts: &mut usize,
    literal_context_map: &mut Option<&'static [u32]>,
) {
    let mut monogram_histo = [0u32; 3];
    let mut two_prefix_histo = [0u32; 6];
    for i in 0..9 {
        monogram_histo[i % 3] += bigram_histo[i];
        two_prefix_histo[i % 6] += bigram_histo[i];
    }
    let mut entropy = [0.0f64; 4];
    entropy[1] = estimate_entropy(&monogram_histo);
    entropy[2] = estimate_entropy(&two_prefix_histo[0..3]) + estimate_entropy(&two_prefix_histo[3..6]);
    for i in 0..3 {
        entropy[3] += estimate_entropy(&bigram_histo[3 * i..3 * i + 3]);
    }
    let total = monogram_histo[0] + monogram_histo[1] + monogram_histo[2];
    debug_assert!(total != 0);
    entropy[0] = 1.0 / (total as f64);
    entropy[1] *= entropy[0];
    entropy[2] *= entropy[0];
    entropy[3] *= entropy[0];

    if quality < MIN_QUALITY_FOR_HQ_CONTEXT_MODELING {
        entropy[3] = entropy[1] * 10.0;
    }
    if entropy[1] - entropy[2] < 0.2 && entropy[1] - entropy[3] < 0.2 {
        *num_literal_contexts = 1;
    } else if entropy[2] - entropy[3] < 0.02 {
        *num_literal_contexts = 2;
        *literal_context_map = Some(&K_STATIC_CONTEXT_MAP_SIMPLE_UTF8);
    } else {
        *num_literal_contexts = 3;
        *literal_context_map = Some(&K_STATIC_CONTEXT_MAP_CONTINUATION);
    }
}

fn should_use_complex_static_context_map(
    input: &[u8],
    mut start_pos: usize,
    length: usize,
    mask: usize,
    _quality: i32,
    size_hint: usize,
    num_literal_contexts: &mut usize,
    literal_context_map: &mut Option<&'static [u32]>,
    arena: &mut [u32],
) -> bool {
    if size_hint < (1 << 20) {
        return false;
    }
    let end_pos = start_pos + length;
    arena[..32 * (BROTLI_MAX_STATIC_CONTEXTS + 1)].fill(0);
    let mut total = 0u32;
    let utf8_lut = brotli_context_lut(ContextType::Utf8);
    while start_pos + 64 <= end_pos {
        let stride_end_pos = start_pos + 64;
        let mut prev2 = input[start_pos & mask];
        let mut prev1 = input[(start_pos + 1) & mask];
        for pos in (start_pos + 2)..stride_end_pos {
            let literal = input[pos & mask];
            let context = K_STATIC_CONTEXT_MAP_COMPLEX_UTF8
                [brotli_context(prev1, prev2, utf8_lut) as usize] as usize;
            total += 1;
            arena[(literal >> 3) as usize] += 1;
            arena[32 + (context << 5) + ((literal >> 3) as usize)] += 1;
            prev2 = prev1;
            prev1 = literal;
        }
        start_pos += 4096;
    }
    let mut entropy = [0.0f64; 3];
    entropy[1] = estimate_entropy(&arena[..32]);
    for i in 0..BROTLI_MAX_STATIC_CONTEXTS {
        let base = 32 + (i << 5);
        entropy[2] += estimate_entropy(&arena[base..base + 32]);
    }
    entropy[0] = 1.0 / (total as f64);
    entropy[1] *= entropy[0];
    entropy[2] *= entropy[0];
    if entropy[2] > 3.0 || entropy[1] - entropy[2] < 0.2 {
        false
    } else {
        *num_literal_contexts = BROTLI_MAX_STATIC_CONTEXTS;
        *literal_context_map = Some(&K_STATIC_CONTEXT_MAP_COMPLEX_UTF8);
        true
    }
}

fn decide_over_literal_context_modeling(
    input: &[u8],
    mut start_pos: usize,
    length: usize,
    mask: usize,
    quality: i32,
    size_hint: usize,
    num_literal_contexts: &mut usize,
    literal_context_map: &mut Option<&'static [u32]>,
    arena: &mut [u32],
) {
    if quality < MIN_QUALITY_FOR_CONTEXT_MODELING || length < 64 {
        return;
    }
    if should_use_complex_static_context_map(
        input,
        start_pos,
        length,
        mask,
        quality,
        size_hint,
        num_literal_contexts,
        literal_context_map,
        arena,
    ) {
        return;
    }
    const LUT: [usize; 4] = [0, 0, 1, 2];
    let end_pos = start_pos + length;
    let mut bigram_prefix_histo = [0u32; 9];
    while start_pos + 64 <= end_pos {
        let stride_end_pos = start_pos + 64;
        let mut prev = LUT[(input[start_pos & mask] >> 6) as usize] * 3;
        for pos in (start_pos + 1)..stride_end_pos {
            let literal = input[pos & mask];
            bigram_prefix_histo[prev + LUT[(literal >> 6) as usize]] += 1;
            prev = LUT[(literal >> 6) as usize] * 3;
        }
        start_pos += 4096;
    }
    choose_context_map(
        quality,
        &bigram_prefix_histo,
        num_literal_contexts,
        literal_context_map,
    );
}

fn should_compress(
    data: &[u8],
    mask: usize,
    last_flush_pos: u64,
    bytes: usize,
    num_literals: usize,
    num_commands: usize,
) -> bool {
    if bytes <= 2 {
        return false;
    }
    if num_commands < (bytes >> 8) + 2 && (num_literals as f64) > 0.99 * (bytes as f64) {
        let mut literal_histo = [0u32; 256];
        const K_SAMPLE_RATE: usize = 13;
        const K_INV_SAMPLE_RATE: f64 = 1.0 / 13.0;
        const K_MIN_ENTROPY: f64 = 7.92;
        let bit_cost_threshold = (bytes as f64) * K_MIN_ENTROPY * K_INV_SAMPLE_RATE;
        let t = (bytes + K_SAMPLE_RATE - 1) / K_SAMPLE_RATE;
        let mut pos = last_flush_pos as u32;
        for _ in 0..t {
            literal_histo[data[(pos as usize) & mask] as usize] += 1;
            pos = pos.wrapping_add(K_SAMPLE_RATE as u32);
        }
        if brotli_bits_entropy(&literal_histo) > bit_cost_threshold {
            return false;
        }
    }
    true
}

fn choose_context_mode(
    params: &BrotliEncoderParams,
    data: &[u8],
    pos: usize,
    mask: usize,
    length: usize,
) -> ContextType {
    if params.quality >= MIN_QUALITY_FOR_HQ_BLOCK_SPLITTING
        && !brotli_is_mostly_utf8(data, pos, mask, length, K_MIN_UTF8_RATIO)
    {
        return ContextType::Signed;
    }
    ContextType::Utf8
}

#[allow(clippy::too_many_arguments)]
fn write_meta_block_internal(
    data: &[u8],
    mask: usize,
    last_flush_pos: u64,
    bytes: usize,
    is_last: bool,
    base64_regions: &[Base64Region],
    literal_context_mode: ContextType,
    params: &BrotliEncoderParams,
    prev_byte: u8,
    prev_byte2: u8,
    num_literals: usize,
    commands: &mut [Command],
    saved_dist_cache: &[i32; 4],
    dist_cache: &mut [i32; BROTLI_NUM_DISTANCE_SHORT_CODES],
    storage_ix: &mut usize,
    storage: &mut [u8],
) {
    let wrapped_last_flush_pos = wrap_position(last_flush_pos) as usize;
    let literal_context_lut = brotli_context_lut(literal_context_mode);
    let mut block_params = params.clone();

    if bytes == 0 {
        brotli_write_bits(2, 3, storage_ix, storage);
        *storage_ix = (*storage_ix + 7) & !7;
        return;
    }

    if !should_compress(data, mask, last_flush_pos, bytes, num_literals, commands.len()) {
        dist_cache[..4].copy_from_slice(saved_dist_cache);
        brotli_store_uncompressed_meta_block(
            is_last,
            data,
            wrapped_last_flush_pos,
            mask,
            bytes,
            storage_ix,
            storage,
        );
        return;
    }

    debug_assert!(*storage_ix <= 14);
    let last_bytes = ((storage[1] as u16) << 8) | (storage[0] as u16);
    let last_bytes_bits = *storage_ix as u8;

    if params.quality <= MAX_QUALITY_FOR_STATIC_ENTROPY_CODES {
        brotli_store_meta_block_fast(
            data,
            wrapped_last_flush_pos,
            bytes,
            mask,
            is_last,
            params,
            commands,
            storage_ix,
            storage,
        );
    } else if params.quality < MIN_QUALITY_FOR_BLOCK_SPLIT {
        brotli_store_meta_block_trivial(
            data,
            wrapped_last_flush_pos,
            bytes,
            mask,
            is_last,
            params,
            commands,
            storage_ix,
            storage,
        );
    } else {
        let mut mb = MetaBlockSplit::new();
        if params.quality < MIN_QUALITY_FOR_HQ_BLOCK_SPLITTING {
            let mut num_literal_contexts = 1usize;
            let mut literal_context_map: Option<&'static [u32]> = None;
            if !params.disable_literal_context_modeling {
                let mut arena = vec![0u32; 32 * (BROTLI_MAX_STATIC_CONTEXTS + 1)];
                decide_over_literal_context_modeling(
                    data,
                    wrapped_last_flush_pos,
                    bytes,
                    mask,
                    params.quality,
                    params.size_hint,
                    &mut num_literal_contexts,
                    &mut literal_context_map,
                    &mut arena,
                );
            }
            const SINGLE_CTX: [u32; 64] = [0; 64];
            let map_slice = literal_context_map.unwrap_or(&SINGLE_CTX);
            brotli_build_meta_block_greedy(
                data,
                wrapped_last_flush_pos,
                mask,
                base64_regions,
                prev_byte,
                prev_byte2,
                literal_context_lut,
                num_literal_contexts,
                map_slice,
                commands,
                &mut mb,
            );
        } else {
            brotli_build_meta_block(
                data,
                wrapped_last_flush_pos,
                mask,
                base64_regions,
                &mut block_params,
                prev_byte,
                prev_byte2,
                commands,
                literal_context_mode,
                &mut mb,
            );
        }
        if params.quality >= MIN_QUALITY_FOR_OPTIMIZE_HISTOGRAMS {
            brotli_optimize_histograms(block_params.dist.alphabet_size_limit, &mut mb);
        }
        brotli_store_meta_block(
            data,
            wrapped_last_flush_pos,
            bytes,
            mask,
            prev_byte,
            prev_byte2,
            is_last,
            &block_params,
            literal_context_mode,
            commands,
            &mb,
            storage_ix,
            storage,
        );
    }

    if bytes + 4 < (*storage_ix >> 3) {
        dist_cache[..4].copy_from_slice(saved_dist_cache);
        storage[0] = last_bytes as u8;
        storage[1] = (last_bytes >> 8) as u8;
        *storage_ix = last_bytes_bits as usize;
        brotli_store_uncompressed_meta_block(
            is_last,
            data,
            wrapped_last_flush_pos,
            mask,
            bytes,
            storage_ix,
            storage,
        );
    }
}

fn choose_distance_params(params: &mut BrotliEncoderParams) {
    let mut distance_postfix_bits = 0u32;
    let mut num_direct_distance_codes = 0u32;
    if params.quality >= MIN_QUALITY_FOR_NONZERO_DISTANCE_PARAMS {
        if params.mode == BrotliEncoderMode::Font {
            distance_postfix_bits = 1;
            num_direct_distance_codes = 12;
        } else {
            distance_postfix_bits = params.dist.distance_postfix_bits;
            num_direct_distance_codes = params.dist.num_direct_distance_codes;
        }
        let ndirect_msb = (num_direct_distance_codes >> distance_postfix_bits) & 0x0F;
        if distance_postfix_bits > BROTLI_MAX_NPOSTFIX as u32
            || num_direct_distance_codes > BROTLI_MAX_NDIRECT as u32
            || (ndirect_msb << distance_postfix_bits) != num_direct_distance_codes
        {
            distance_postfix_bits = 0;
            num_direct_distance_codes = 0;
        }
    }
    brotli_init_distance_params(
        &mut params.dist,
        distance_postfix_bits,
        num_direct_distance_codes,
        params.large_window,
    );
}

#[inline]
pub fn brotli_encoder_version() -> u32 {
    BROTLI_VERSION
}

pub fn brotli_encoder_max_compressed_size(input_size: usize) -> usize {
    if input_size == 0 {
        return 2;
    }
    let num_large_blocks = input_size >> 14;
    let overhead = 2usize
        .saturating_add(4usize.saturating_mul(num_large_blocks))
        .saturating_add(4);
    input_size.checked_add(overhead).unwrap_or(0)
}

fn make_uncompressed_stream(input: &[u8], output: &mut [u8]) -> usize {
    if input.is_empty() {
        output[0] = 6;
        return 1;
    }
    let mut size = input.len();
    let mut result = 0usize;
    let mut offset = 0usize;
    output[result] = 0x21;
    result += 1;
    output[result] = 0x03;
    result += 1;
    while size > 0 {
        let mut nibbles = 0u32;
        let chunk_size = if size > (1usize << 24) {
            1usize << 24
        } else {
            size
        };
        if chunk_size > (1usize << 16) {
            nibbles = if chunk_size > (1usize << 20) { 2 } else { 1 };
        }
        let bits = (nibbles << 1)
            | (((chunk_size as u32) - 1) << 3)
            | (1u32 << (19 + 4 * nibbles));
        output[result] = bits as u8;
        result += 1;
        output[result] = (bits >> 8) as u8;
        result += 1;
        output[result] = (bits >> 16) as u8;
        result += 1;
        if nibbles == 2 {
            output[result] = (bits >> 24) as u8;
            result += 1;
        }
        output[result..result + chunk_size].copy_from_slice(&input[offset..offset + chunk_size]);
        result += chunk_size;
        offset += chunk_size;
        size -= chunk_size;
    }
    output[result] = 3;
    result + 1
}

/// One-shot memory-to-memory compression into a caller-supplied buffer.
pub fn brotli_encoder_compress_into(
    quality: i32,
    lgwin: i32,
    mode: BrotliEncoderMode,
    input: &[u8],
    output: &mut [u8],
) -> Result<usize, ()> {
    if output.is_empty() {
        return Err(());
    }
    if input.is_empty() {
        output[0] = 6;
        return Ok(1);
    }
    let max_out_size = brotli_encoder_max_compressed_size(input.len());
    let mut s = BrotliEncoderState::new();
    s.set_parameter(BrotliEncoderParameter::Quality, quality as u32);
    s.set_parameter(BrotliEncoderParameter::Lgwin, lgwin as u32);
    s.set_parameter(BrotliEncoderParameter::Mode, mode as u32);
    s.set_parameter(BrotliEncoderParameter::SizeHint, input.len() as u32);
    if lgwin > BROTLI_MAX_WINDOW_BITS {
        s.set_parameter(BrotliEncoderParameter::LargeWindow, 1);
    }

    let mut in_pos = 0usize;
    let mut out_pos = 0usize;
    let mut total_out = 0usize;
    let ok = s.compress_stream(
        BrotliEncoderOperation::Finish,
        input,
        &mut in_pos,
        output,
        &mut out_pos,
        Some(&mut total_out),
    ) && s.is_finished();

    if ok && (max_out_size == 0 || total_out <= max_out_size) {
        return Ok(total_out);
    }
    if max_out_size != 0 && output.len() >= max_out_size {
        return Ok(make_uncompressed_stream(input, output));
    }
    Err(())
}

/// One-shot compression returning an owned `Vec<u8>`.
pub fn brotli_encoder_compress(
    quality: i32,
    lgwin: i32,
    mode: BrotliEncoderMode,
    input: &[u8],
) -> Result<Vec<u8>, ()> {
    let mut params = BrotliEncoderParams::default();
    params.quality = quality;
    params.lgwin = lgwin;
    params.mode = mode;
    params.size_hint = input.len();
    if lgwin > BROTLI_MAX_WINDOW_BITS {
        params.large_window = true;
    }
    brotli_encoder_compress_with_params(&params, input)
}

/// One-shot compression with full `BrotliEncoderParams`.
pub fn brotli_encoder_compress_with_params(
    params: &BrotliEncoderParams,
    input: &[u8],
) -> Result<Vec<u8>, ()> {
    if input.is_empty() && params.stream_offset == 0 {
        return Ok(vec![6]);
    }
    let mut s = BrotliEncoderState::new();
    s.params = params.clone();
    if s.params.size_hint == 0 {
        s.params.size_hint = input.len();
    }
    if s.params.lgwin > BROTLI_MAX_WINDOW_BITS {
        s.params.large_window = true;
    }

    let mut in_pos = 0usize;
    let mut out = Vec::with_capacity(input.len() / 2 + 64);
    let mut scratch = vec![0u8; 65536];
    loop {
        let mut out_pos = 0usize;
        if !s.compress_stream(
            BrotliEncoderOperation::Finish,
            input,
            &mut in_pos,
            &mut scratch,
            &mut out_pos,
            None,
        ) {
            return Err(());
        }
        if out_pos > 0 {
            out.extend_from_slice(&scratch[..out_pos]);
        }
        if s.is_finished() {
            return Ok(out);
        }
    }
}

pub fn brotli_encoder_prepare_raw_dictionary(data: &[u8]) -> Option<Arc<PreparedDictionary>> {
    create_prepared_dictionary(data).map(Arc::new)
}

pub fn brotli_encoder_estimate_peak_memory_usage(
    quality: i32,
    lgwin: i32,
    input_size: usize,
) -> usize {
    let mut params = BrotliEncoderParams::default();
    params.quality = quality;
    params.lgwin = lgwin;
    params.size_hint = input_size;
    params.large_window = lgwin > BROTLI_MAX_WINDOW_BITS;
    sanitize_params(&mut params);
    params.lgblock = compute_lg_block(&params);
    let mut hparams = params.hasher;
    choose_hasher(&params, &mut hparams);
    params.hasher = hparams;

    if params.quality == FAST_ONE_PASS_COMPRESSION_QUALITY
        || params.quality == FAST_TWO_PASS_COMPRESSION_QUALITY
    {
        let mut state_size = std::mem::size_of::<BrotliEncoderState>();
        let block_size = input_size.min(1usize << params.lgwin);
        let ht_size = hash_table_size(max_hash_table_size(params.quality), block_size);
        let hash_size = if ht_size < (1 << 10) {
            0
        } else {
            ht_size * std::mem::size_of::<i32>()
        };
        let cmdbuf_size = if params.quality == FAST_TWO_PASS_COMPRESSION_QUALITY {
            5 * block_size.min(1 << 17)
        } else {
            0
        };
        if params.quality == FAST_ONE_PASS_COMPRESSION_QUALITY {
            state_size += std::mem::size_of::<BrotliOnePassArena>();
        } else {
            state_size += std::mem::size_of::<BrotliTwoPassArena>();
        }
        hash_size + cmdbuf_size + state_size
    } else {
        let short_rb = 1usize << params.lgblock;
        let rb_bits = compute_rb_bits(&params);
        let rb_size = if input_size < short_rb {
            input_size
        } else {
            (1usize << rb_bits) + short_rb
        };
        let metablock_size = input_size.min(max_metablock_size(&params));
        let inputblock_size = input_size.min(1usize << params.lgblock);
        let mut cmdbuf_size = metablock_size * 2 + inputblock_size * 6;
        let outbuf_size = metablock_size * 2 + 503;
        if params.quality < MIN_QUALITY_FOR_BLOCK_SPLIT {
            cmdbuf_size = cmdbuf_size.min(
                MAX_NUM_DELAYED_SYMBOLS * std::mem::size_of::<Command>() + inputblock_size * 12,
            );
        }
        let histogram_size = if params.quality >= MIN_QUALITY_FOR_HQ_BLOCK_SPLITTING {
            200 << 20
        } else if params.quality >= MIN_QUALITY_FOR_BLOCK_SPLIT {
            let n = (metablock_size / 6144).min(256);
            n * (std::mem::size_of::<HistogramLiteral>()
                + std::mem::size_of::<HistogramCommand>()
                + std::mem::size_of::<HistogramDistance>())
        } else {
            0
        };
        rb_size + cmdbuf_size + outbuf_size + histogram_size
    }
}
