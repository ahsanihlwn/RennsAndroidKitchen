//! Binary format definitions for Android sparse images.

use std::fmt;

pub const SPARSE_HEADER_MAGIC: u32 = 0xed26_ff3a;
pub const SPARSE_HEADER_MAJOR_VER: u16 = 1;
pub const SPARSE_HEADER_MINOR_VER: u16 = 0;
pub const SPARSE_HEADER_SIZE: usize = 28;
pub const SPARSE_CHUNK_HEADER_SIZE: usize = 12;

pub const CHUNK_TYPE_RAW: u16 = 0xCAC1;
pub const CHUNK_TYPE_FILL: u16 = 0xCAC2;
pub const CHUNK_TYPE_DONT_CARE: u16 = 0xCAC3;
pub const CHUNK_TYPE_CRC32: u16 = 0xCAC4;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SparseHeader {
    pub magic: u32,
    pub major_version: u16,
    pub minor_version: u16,
    pub file_hdr_sz: u16,
    pub chunk_hdr_sz: u16,
    pub blk_sz: u32,
    pub total_blks: u32,
    pub total_chunks: u32,
    pub image_checksum: u32,
}

impl SparseHeader {
    pub fn from_bytes(buf: &[u8; SPARSE_HEADER_SIZE]) -> Self {
        Self {
            magic: u32::from_le_bytes([buf[0], buf[1], buf[2], buf[3]]),
            major_version: u16::from_le_bytes([buf[4], buf[5]]),
            minor_version: u16::from_le_bytes([buf[6], buf[7]]),
            file_hdr_sz: u16::from_le_bytes([buf[8], buf[9]]),
            chunk_hdr_sz: u16::from_le_bytes([buf[10], buf[11]]),
            blk_sz: u32::from_le_bytes([buf[12], buf[13], buf[14], buf[15]]),
            total_blks: u32::from_le_bytes([buf[16], buf[17], buf[18], buf[19]]),
            total_chunks: u32::from_le_bytes([buf[20], buf[21], buf[22], buf[23]]),
            image_checksum: u32::from_le_bytes([buf[24], buf[25], buf[26], buf[27]]),
        }
    }

    pub fn to_bytes(&self) -> [u8; SPARSE_HEADER_SIZE] {
        let mut buf = [0u8; SPARSE_HEADER_SIZE];
        buf[0..4].copy_from_slice(&self.magic.to_le_bytes());
        buf[4..6].copy_from_slice(&self.major_version.to_le_bytes());
        buf[6..8].copy_from_slice(&self.minor_version.to_le_bytes());
        buf[8..10].copy_from_slice(&self.file_hdr_sz.to_le_bytes());
        buf[10..12].copy_from_slice(&self.chunk_hdr_sz.to_le_bytes());
        buf[12..16].copy_from_slice(&self.blk_sz.to_le_bytes());
        buf[16..20].copy_from_slice(&self.total_blks.to_le_bytes());
        buf[20..24].copy_from_slice(&self.total_chunks.to_le_bytes());
        buf[24..28].copy_from_slice(&self.image_checksum.to_le_bytes());
        buf
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ChunkHeader {
    pub chunk_type: u16,
    pub reserved1: u16,
    pub chunk_sz: u32,
    pub total_sz: u32,
}

impl ChunkHeader {
    pub fn from_bytes(buf: &[u8; SPARSE_CHUNK_HEADER_SIZE]) -> Self {
        Self {
            chunk_type: u16::from_le_bytes([buf[0], buf[1]]),
            reserved1: u16::from_le_bytes([buf[2], buf[3]]),
            chunk_sz: u32::from_le_bytes([buf[4], buf[5], buf[6], buf[7]]),
            total_sz: u32::from_le_bytes([buf[8], buf[9], buf[10], buf[11]]),
        }
    }

    pub fn to_bytes(&self) -> [u8; SPARSE_CHUNK_HEADER_SIZE] {
        let mut buf = [0u8; SPARSE_CHUNK_HEADER_SIZE];
        buf[0..2].copy_from_slice(&self.chunk_type.to_le_bytes());
        buf[2..4].copy_from_slice(&self.reserved1.to_le_bytes());
        buf[4..8].copy_from_slice(&self.chunk_sz.to_le_bytes());
        buf[8..12].copy_from_slice(&self.total_sz.to_le_bytes());
        buf
    }
}

#[derive(Debug)]
pub enum SparseError {
    Io(std::io::Error),
    InvalidFormat(String),
}

impl fmt::Display for SparseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            SparseError::Io(e) => write!(f, "I/O error: {e}"),
            SparseError::InvalidFormat(msg) => write!(f, "Invalid sparse image: {msg}"),
        }
    }
}

impl std::error::Error for SparseError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            SparseError::Io(e) => Some(e),
            SparseError::InvalidFormat(_) => None,
        }
    }
}

impl From<std::io::Error> for SparseError {
    fn from(err: std::io::Error) -> Self {
        SparseError::Io(err)
    }
}
