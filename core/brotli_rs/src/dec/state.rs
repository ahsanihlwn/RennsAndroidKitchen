//! Brotli streaming decoder state and configuration types.

use std::sync::Arc;

use crate::common::{
    BrotliSharedDictionary, ContextLut, BROTLI_CODE_LENGTH_CODES, BROTLI_NUM_COMMAND_SYMBOLS,
};

use super::bit_reader::BrotliBitReader;
use super::huffman::{
    HuffmanCode, HuffmanTreeGroup, BROTLI_HUFFMAN_MAX_CODE_LENGTH, BROTLI_HUFFMAN_MAX_SIZE_258,
    BROTLI_HUFFMAN_MAX_SIZE_26, BROTLI_HUFFMAN_MAX_SIZE_272,
};

pub const BROTLI_BLOCK_SIZE_CAP: u32 = 1u32 << 24;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrotliDecoderResult {
    Error = 0,
    Success = 1,
    NeedsMoreInput = 2,
    NeedsMoreOutput = 3,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
pub enum BrotliDecoderErrorCode {
    NoError = 0,
    Success = 1,
    NeedsMoreInput = 2,
    NeedsMoreOutput = 3,
    ErrorFormatExuberantNibble = -1,
    ErrorFormatReserved = -2,
    ErrorFormatExuberantMetaNibble = -3,
    ErrorFormatSimpleHuffmanAlphabet = -4,
    ErrorFormatSimpleHuffmanSame = -5,
    ErrorFormatClSpace = -6,
    ErrorFormatHuffmanSpace = -7,
    ErrorFormatContextMapRepeat = -8,
    ErrorFormatBlockLength1 = -9,
    ErrorFormatBlockLength2 = -10,
    ErrorFormatTransform = -11,
    ErrorFormatDictionary = -12,
    ErrorFormatWindowBits = -13,
    ErrorFormatPadding1 = -14,
    ErrorFormatPadding2 = -15,
    ErrorFormatDistance = -16,
    ErrorFormatBlockSwitch = -17,
    ErrorCompoundDictionary = -18,
    ErrorDictionaryNotSet = -19,
    ErrorInvalidArguments = -20,
    ErrorAllocContextModes = -21,
    ErrorAllocTreeGroups = -22,
    ErrorAllocContextMap = -25,
    ErrorAllocRingBuffer1 = -26,
    ErrorAllocRingBuffer2 = -27,
    ErrorAllocBlockTypeTrees = -30,
    ErrorUnreachable = -31,
}

impl BrotliDecoderErrorCode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::NoError => "_NO_ERROR",
            Self::Success => "_SUCCESS",
            Self::NeedsMoreInput => "_NEEDS_MORE_INPUT",
            Self::NeedsMoreOutput => "_NEEDS_MORE_OUTPUT",
            Self::ErrorFormatExuberantNibble => "_ERROR_FORMAT_EXUBERANT_NIBBLE",
            Self::ErrorFormatReserved => "_ERROR_FORMAT_RESERVED",
            Self::ErrorFormatExuberantMetaNibble => "_ERROR_FORMAT_EXUBERANT_META_NIBBLE",
            Self::ErrorFormatSimpleHuffmanAlphabet => "_ERROR_FORMAT_SIMPLE_HUFFMAN_ALPHABET",
            Self::ErrorFormatSimpleHuffmanSame => "_ERROR_FORMAT_SIMPLE_HUFFMAN_SAME",
            Self::ErrorFormatClSpace => "_ERROR_FORMAT_CL_SPACE",
            Self::ErrorFormatHuffmanSpace => "_ERROR_FORMAT_HUFFMAN_SPACE",
            Self::ErrorFormatContextMapRepeat => "_ERROR_FORMAT_CONTEXT_MAP_REPEAT",
            Self::ErrorFormatBlockLength1 => "_ERROR_FORMAT_BLOCK_LENGTH_1",
            Self::ErrorFormatBlockLength2 => "_ERROR_FORMAT_BLOCK_LENGTH_2",
            Self::ErrorFormatTransform => "_ERROR_FORMAT_TRANSFORM",
            Self::ErrorFormatDictionary => "_ERROR_FORMAT_DICTIONARY",
            Self::ErrorFormatWindowBits => "_ERROR_FORMAT_WINDOW_BITS",
            Self::ErrorFormatPadding1 => "_ERROR_FORMAT_PADDING_1",
            Self::ErrorFormatPadding2 => "_ERROR_FORMAT_PADDING_2",
            Self::ErrorFormatDistance => "_ERROR_FORMAT_DISTANCE",
            Self::ErrorFormatBlockSwitch => "_ERROR_FORMAT_BLOCK_SWITCH",
            Self::ErrorCompoundDictionary => "_ERROR_COMPOUND_DICTIONARY",
            Self::ErrorDictionaryNotSet => "_ERROR_DICTIONARY_NOT_SET",
            Self::ErrorInvalidArguments => "_ERROR_INVALID_ARGUMENTS",
            Self::ErrorAllocContextModes => "_ERROR_ALLOC_CONTEXT_MODES",
            Self::ErrorAllocTreeGroups => "_ERROR_ALLOC_TREE_GROUPS",
            Self::ErrorAllocContextMap => "_ERROR_ALLOC_CONTEXT_MAP",
            Self::ErrorAllocRingBuffer1 => "_ERROR_ALLOC_RING_BUFFER_1",
            Self::ErrorAllocRingBuffer2 => "_ERROR_ALLOC_RING_BUFFER_2",
            Self::ErrorAllocBlockTypeTrees => "_ERROR_ALLOC_BLOCK_TYPE_TREES",
            Self::ErrorUnreachable => "_ERROR_UNREACHABLE",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u32)]
pub enum BrotliDecoderParameter {
    DisableRingBufferReallocation = 0,
    LargeWindow = 1,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrotliRunningState {
    Uninited,
    LargeWindowBits,
    Initialize,
    MetablockBegin,
    MetablockHeader,
    MetablockHeader2,
    ContextModes,
    CommandBegin,
    CommandInner,
    CommandPostDecodeLiterals,
    CommandPostWrapCopy,
    Uncompressed,
    Metadata,
    CommandInnerWrite,
    MetablockDone,
    CommandPostWrite1,
    CommandPostWrite2,
    BeforeCompressedMetablockHeader,
    HuffmanCode0,
    HuffmanCode1,
    HuffmanCode2,
    HuffmanCode3,
    ContextMap1,
    ContextMap2,
    TreeGroup,
    BeforeCompressedMetablockBody,
    Done,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrotliRunningMetablockHeaderState {
    None,
    Empty,
    Nibbles,
    Size,
    Uncompressed,
    Reserved,
    Bytes,
    Metadata,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrotliRunningUncompressedState {
    None,
    Write,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrotliRunningTreeGroupState {
    None,
    Loop,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrotliRunningContextMapState {
    None,
    ReadPrefix,
    Huffman,
    Decode,
    Transform,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrotliRunningHuffmanState {
    None,
    SimpleSize,
    SimpleRead,
    SimpleBuild,
    Complex,
    LengthSymbols,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrotliRunningDecodeUint8State {
    None,
    Short,
    Long,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BrotliRunningReadBlockLengthState {
    None,
    Suffix,
}

#[derive(Debug, Clone)]
pub struct BrotliDecoderCompoundDictionary {
    pub num_chunks: u8,
    pub block_bits: u8,
    pub br_index: u16,
    pub total_size: u32,
    pub br_offset: u32,
    pub br_length: u32,
    pub br_copied: u32,
    pub chunks: [Option<Arc<[u8]>>; 16],
    pub chunk_offsets: [u32; 17],
    pub block_map: [u8; 256],
}

impl Default for BrotliDecoderCompoundDictionary {
    fn default() -> Self {
        Self {
            num_chunks: 0,
            block_bits: 255,
            br_index: 0,
            total_size: 0,
            br_offset: 0,
            br_length: 0,
            br_copied: 0,
            chunks: std::array::from_fn(|_| None),
            chunk_offsets: [0; 17],
            block_map: [0; 256],
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TreeGroupKind {
    Literal,
    InsertCopy,
    Distance,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HuffmanTarget {
    BlockType(usize),
    BlockLen(usize),
    ContextMap,
    TreeGroup(TreeGroupKind),
}

pub struct BrotliMetablockHeaderArena {
    pub substate_tree_group: BrotliRunningTreeGroupState,
    pub substate_context_map: BrotliRunningContextMapState,
    pub substate_huffman: BrotliRunningHuffmanState,

    pub sub_loop_counter: u32,
    pub repeat_code_len: u32,
    pub prev_code_len: u32,

    pub symbol: u32,
    pub repeat: u32,
    pub space: u32,

    pub table: [HuffmanCode; 32],
    pub symbols_lists_array:
        [u16; BROTLI_HUFFMAN_MAX_CODE_LENGTH + 1 + BROTLI_NUM_COMMAND_SYMBOLS],
    pub next_symbol: [i32; 32],
    pub code_length_code_lengths: [u8; BROTLI_CODE_LENGTH_CODES],
    pub code_length_histo: [u16; 16],

    pub htree_index: usize,
    pub next_code_offset: usize,

    pub context_index: u32,
    pub max_run_length_prefix: u32,
    pub code: u32,
    pub context_map_table: [HuffmanCode; BROTLI_HUFFMAN_MAX_SIZE_272],
}

impl Default for BrotliMetablockHeaderArena {
    fn default() -> Self {
        Self {
            substate_tree_group: BrotliRunningTreeGroupState::None,
            substate_context_map: BrotliRunningContextMapState::None,
            substate_huffman: BrotliRunningHuffmanState::None,
            sub_loop_counter: 0,
            repeat_code_len: 0,
            prev_code_len: 0,
            symbol: 0,
            repeat: 0,
            space: 0,
            table: [HuffmanCode::default(); 32],
            symbols_lists_array: [0; BROTLI_HUFFMAN_MAX_CODE_LENGTH
                + 1
                + BROTLI_NUM_COMMAND_SYMBOLS],
            next_symbol: [0; 32],
            code_length_code_lengths: [0; BROTLI_CODE_LENGTH_CODES],
            code_length_histo: [0; 16],
            htree_index: 0,
            next_code_offset: 0,
            context_index: 0,
            max_run_length_prefix: 0,
            code: 0,
            context_map_table: [HuffmanCode::default(); BROTLI_HUFFMAN_MAX_SIZE_272],
        }
    }
}

pub struct BrotliMetablockBodyArena {
    pub dist_extra_bits: [u8; 544],
    pub dist_offset: [u32; 544],
}

impl Default for BrotliMetablockBodyArena {
    fn default() -> Self {
        Self {
            dist_extra_bits: [0; 544],
            dist_offset: [0; 544],
        }
    }
}

pub type MetadataStartFn = Box<dyn FnMut(usize) + Send>;
pub type MetadataChunkFn = Box<dyn FnMut(&[u8]) + Send>;

pub struct BrotliDecoderState {
    pub state: BrotliRunningState,
    pub loop_counter: i32,
    pub br: BrotliBitReader,

    pub buffer: [u8; 8],
    pub buffer_length: usize,

    pub pos: i32,
    pub max_backward_distance: i32,
    pub max_distance: i32,
    pub ringbuffer_size: i32,
    pub ringbuffer_mask: i32,
    pub dist_rb_idx: i32,
    pub dist_rb: [i32; 4],
    pub error_code: BrotliDecoderErrorCode,
    pub meta_block_remaining_len: i32,

    pub ringbuffer: Vec<u8>,
    pub htree_command_idx: usize,
    pub context_lookup: Option<ContextLut>,
    pub context_map_slice_offset: usize,
    pub dist_context_map_slice_offset: usize,

    pub literal_hgroup: HuffmanTreeGroup,
    pub insert_copy_hgroup: HuffmanTreeGroup,
    pub distance_hgroup: HuffmanTreeGroup,
    pub block_type_trees: Vec<HuffmanCode>,
    pub block_len_trees: Vec<HuffmanCode>,

    pub trivial_literal_context: u32,
    pub distance_context: i32,
    pub block_length: [u32; 3],
    pub block_length_index: u32,
    pub num_block_types: [u32; 3],
    pub block_type_rb: [u32; 6],
    pub distance_postfix_bits: u32,
    pub num_direct_distance_codes: u32,
    pub num_dist_htrees: u32,
    pub dist_context_map: Vec<u8>,
    pub literal_htree_idx: usize,

    pub rb_roundtrips: usize,
    pub partial_pos_out: usize,

    pub mtf_upper_bound: u32,
    pub mtf: [u8; (64 + 1) * 4],

    pub copy_length: i32,
    pub distance_code: i32,
    pub dist_htree_index: u8,

    pub metadata_start_func: Option<MetadataStartFn>,
    pub metadata_chunk_func: Option<MetadataChunkFn>,

    pub used_input: u64,

    pub substate_metablock_header: BrotliRunningMetablockHeaderState,
    pub substate_uncompressed: BrotliRunningUncompressedState,
    pub substate_decode_uint8: BrotliRunningDecodeUint8State,
    pub substate_read_block_length: BrotliRunningReadBlockLengthState,

    pub new_ringbuffer_size: i32,

    pub is_last_metablock: bool,
    pub is_uncompressed: bool,
    pub is_metadata: bool,
    pub should_wrap_ringbuffer: bool,
    pub canny_ringbuffer_allocation: bool,
    pub large_window: bool,
    pub window_bits: u32,
    pub size_nibbles: u8,

    pub num_literal_htrees: u32,
    pub context_map: Vec<u8>,
    pub context_modes: Vec<u8>,

    pub dictionary: BrotliSharedDictionary,
    pub compound_dictionary: Option<BrotliDecoderCompoundDictionary>,

    pub trivial_literal_contexts: [u32; 8],

    pub header: BrotliMetablockHeaderArena,
    pub body: BrotliMetablockBodyArena,
}

impl Default for BrotliDecoderState {
    fn default() -> Self {
        Self::new()
    }
}

impl BrotliDecoderState {
    pub fn new() -> Self {
        let mut br = BrotliBitReader::default();
        br.init();
        Self {
            state: BrotliRunningState::Uninited,
            loop_counter: 0,
            br,
            buffer: [0; 8],
            buffer_length: 0,
            pos: 0,
            max_backward_distance: 0,
            max_distance: 0,
            ringbuffer_size: 0,
            ringbuffer_mask: 0,
            dist_rb_idx: 0,
            dist_rb: [16, 15, 11, 4],
            error_code: BrotliDecoderErrorCode::NoError,
            meta_block_remaining_len: 0,
            ringbuffer: Vec::new(),
            htree_command_idx: 0,
            context_lookup: None,
            context_map_slice_offset: 0,
            dist_context_map_slice_offset: 0,
            literal_hgroup: HuffmanTreeGroup::default(),
            insert_copy_hgroup: HuffmanTreeGroup::default(),
            distance_hgroup: HuffmanTreeGroup::default(),
            block_type_trees: Vec::new(),
            block_len_trees: Vec::new(),
            trivial_literal_context: 0,
            distance_context: 0,
            block_length: [BROTLI_BLOCK_SIZE_CAP; 3],
            block_length_index: 0,
            num_block_types: [1; 3],
            block_type_rb: [1, 0, 1, 0, 1, 0],
            distance_postfix_bits: 0,
            num_direct_distance_codes: 0,
            num_dist_htrees: 0,
            dist_context_map: Vec::new(),
            literal_htree_idx: 0,
            rb_roundtrips: 0,
            partial_pos_out: 0,
            mtf_upper_bound: 63,
            mtf: [0; (64 + 1) * 4],
            copy_length: 0,
            distance_code: 0,
            dist_htree_index: 0,
            metadata_start_func: None,
            metadata_chunk_func: None,
            used_input: 0,
            substate_metablock_header: BrotliRunningMetablockHeaderState::None,
            substate_uncompressed: BrotliRunningUncompressedState::None,
            substate_decode_uint8: BrotliRunningDecodeUint8State::None,
            substate_read_block_length: BrotliRunningReadBlockLengthState::None,
            new_ringbuffer_size: 0,
            is_last_metablock: false,
            is_uncompressed: false,
            is_metadata: false,
            should_wrap_ringbuffer: false,
            canny_ringbuffer_allocation: true,
            large_window: false,
            window_bits: 0,
            size_nibbles: 0,
            num_literal_htrees: 0,
            context_map: Vec::new(),
            context_modes: Vec::new(),
            dictionary: BrotliSharedDictionary::new(),
            compound_dictionary: None,
            trivial_literal_contexts: [0; 8],
            header: BrotliMetablockHeaderArena::default(),
            body: BrotliMetablockBodyArena::default(),
        }
    }

    pub fn metablock_begin(&mut self) {
        self.meta_block_remaining_len = 0;
        self.block_length = [BROTLI_BLOCK_SIZE_CAP; 3];
        self.num_block_types = [1; 3];
        self.block_type_rb = [1, 0, 1, 0, 1, 0];
        self.context_map.clear();
        self.context_modes.clear();
        self.dist_context_map.clear();
        self.context_map_slice_offset = 0;
        self.literal_htree_idx = 0;
        self.dist_context_map_slice_offset = 0;
        self.dist_htree_index = 0;
        self.context_lookup = None;
        self.literal_hgroup = HuffmanTreeGroup::default();
        self.insert_copy_hgroup = HuffmanTreeGroup::default();
        self.distance_hgroup = HuffmanTreeGroup::default();
    }

    pub fn cleanup_after_metablock(&mut self) {
        self.context_modes.clear();
        self.context_map.clear();
        self.dist_context_map.clear();
        self.literal_hgroup.htree_offsets.clear();
        self.literal_hgroup.codes.clear();
        self.insert_copy_hgroup.htree_offsets.clear();
        self.insert_copy_hgroup.codes.clear();
        self.distance_hgroup.htree_offsets.clear();
        self.distance_hgroup.codes.clear();
    }

    pub fn init_block_trees(&mut self) {
        self.block_type_trees = vec![HuffmanCode::default(); 3 * BROTLI_HUFFMAN_MAX_SIZE_258];
        self.block_len_trees = vec![HuffmanCode::default(); 3 * BROTLI_HUFFMAN_MAX_SIZE_26];
    }
}

pub fn brotli_decoder_huffman_tree_group_init(
    group: &mut HuffmanTreeGroup,
    alphabet_size_max: u32,
    alphabet_size_limit: u32,
    ntrees: u32,
) {
    let max_table_size = (alphabet_size_limit as usize) + 376;
    group.alphabet_size_max = alphabet_size_max as u16;
    group.alphabet_size_limit = alphabet_size_limit as u16;
    group.num_htrees = ntrees as u16;
    group.htree_offsets = vec![0usize; ntrees as usize];
    group.codes = vec![HuffmanCode::default(); (ntrees as usize) * max_table_size];
}
