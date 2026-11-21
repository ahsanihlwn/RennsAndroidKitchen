use std::fmt;
use thiserror::Error;

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Error)]
pub enum Error {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Invalid sparse image header: {0}")]
    InvalidSparseHeader(String),

    #[error("Invalid sparse chunk: {0}")]
    InvalidSparseChunk(String),

    #[error("CRC32 chunks are not supported")]
    UnsupportedCrc32Chunk,

    #[error("Unknown chunk type 0x{0:04X} not supported")]
    UnsupportedChunkType(u16),

    #[error("Invalid range set: {0}")]
    InvalidRangeSet(String),

    #[error("Invalid transfer list: {0}")]
    InvalidTransferList(String),

    #[error("Invalid command \"{0}\" in transfer list")]
    InvalidCommand(String),

    #[error("Unexpected EOF while reading {0}")]
    UnexpectedEof(String),

    #[error("Data alignment or size error: {0}")]
    InvalidData(String),

    #[error("Stash size {stashed_bytes} ({stashed_blocks} * {block_size}) exceeds limit {max_allowed} ({cache_size} * {threshold:.2})")]
    StashLimitExceeded {
        stashed_bytes: u64,
        stashed_blocks: u64,
        block_size: u64,
        max_allowed: u64,
        cache_size: u64,
        threshold: f64,
    },

    #[error("External diff command failed: {0}")]
    ExternalDiffFailed(String),

    #[error("Internal invariant failed: {0}")]
    Invariant(String),
}

impl Error {
    pub fn custom<S: fmt::Display>(msg: S) -> Self {
        Self::InvalidData(msg.to_string())
    }
}
