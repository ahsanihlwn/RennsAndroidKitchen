// SPDX-License-Identifier: MIT

use thiserror::Error;

/// Unified error type for all `erofs-rs` operations.
#[derive(Debug, Error)]
pub enum ErofsError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("invalid EROFS image: {0}")]
    InvalidFormat(String),

    #[error("corrupted EROFS data: {0}")]
    Corrupted(String),

    #[error("unsupported feature or layout: {0}")]
    Unsupported(String),

    #[error("path not found: {0}")]
    NotFound(String),

    #[error("invalid path: {0}")]
    InvalidPath(String),

    #[error("compression/decompression failed: {0}")]
    Compression(String),

    #[error("edit error: {0}")]
    Edit(String),

    #[error("compaction error: {0}")]
    Compact(String),

    #[error("invalid argument: {0}")]
    InvalidArg(String),
}

pub type Result<T> = std::result::Result<T, ErofsError>;
