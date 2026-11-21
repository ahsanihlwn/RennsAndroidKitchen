use std::num::NonZeroU32;

use bytes::{Buf, BufMut};
use thiserror::Error;

/// Magic number at the beginning of an Android sparse image header (`0xed26ff3a`).
pub const SPARSE_HEADER_MAGIC: u32 = 0xed26_ff3a;
/// Supported sparse image major format version.
pub const MAJOR_VERSION: u16 = 1;
/// Supported sparse image minor format version.
pub const MINOR_VERSION: u16 = 0;
/// Size in bytes of the fixed sparse file header.
pub const FILE_HEADER_SIZE: usize = 28;
/// Size in bytes of a fixed sparse chunk header.
pub const CHUNK_HEADER_SIZE: usize = 12;
/// Default Android sparse block size (4 KiB).
pub const DEFAULT_BLOCK_SIZE: NonZeroU32 = NonZeroU32::new(4096).unwrap();

/// Errors returned while parsing, writing, or splitting Android sparse images.
#[derive(Debug, Error)]
pub enum SparseError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("invalid sparse header magic: 0x{0:08x}")]
    InvalidMagic(u32),
    #[error("unsupported sparse version: {0}.{1}")]
    UnsupportedVersion(u16, u16),
    #[error("invalid file header size: {0}")]
    InvalidHeaderSize(u16),
    #[error("invalid chunk header size: {0}")]
    InvalidChunkHeaderSize(u16),
    #[error("invalid block size: {0}")]
    InvalidBlockSize(u32),
    #[error("unknown chunk type: 0x{0:04x}")]
    UnknownChunkType(u16),
    #[error("invalid chunk size for {chunk_type:?}: total_size={total_size}, expected={expected}")]
    InvalidChunkSize {
        chunk_type: ChunkType,
        total_size: u32,
        expected: u64,
    },
    #[error("chunk count mismatch: expected {expected}, read {actual}")]
    ChunkCountMismatch { expected: u32, actual: u32 },
    #[error("block count mismatch: header {header_blocks}, actual {actual_blocks}")]
    BlockCountMismatch {
        header_blocks: u32,
        actual_blocks: u32,
    },
    #[error("CRC32 mismatch: expected 0x{expected:08x}, actual 0x{actual:08x}")]
    ChecksumMismatch { expected: u32, actual: u32 },
    #[error("operation out of sparse image bounds")]
    OutOfBounds,
    #[error("data length is not aligned to block size")]
    UnalignedSize,
    #[error("raw data remaining in chunk must be consumed before reading next chunk")]
    UnconsumedChunkData,
    #[error("no active raw chunk data to read")]
    NoActiveRawChunk,
    #[error("output split size is too small")]
    OutputTooSmall,
    #[error("input size is too large to describe in 32-bit block counts")]
    InputTooBig,
    #[error("block size is too big for a chunk")]
    BlockSizeTooBig,
}

/// Global Android sparse file header (28 bytes on disk).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileHeader {
    pub block_size: NonZeroU32,
    pub total_blocks: u32,
    pub total_chunks: u32,
    pub image_checksum: u32,
}

impl FileHeader {
    /// Parses a 28-byte sparse file header requiring exact standard header sizes (`28`, `12`).
    pub fn from_bytes(bytes: &[u8; FILE_HEADER_SIZE]) -> Result<Self, SparseError> {
        let (hdr, file_hdr_sz, chunk_hdr_sz) = Self::from_bytes_ext(bytes)?;
        if usize::from(file_hdr_sz) != FILE_HEADER_SIZE {
            return Err(SparseError::InvalidHeaderSize(file_hdr_sz));
        }
        if usize::from(chunk_hdr_sz) != CHUNK_HEADER_SIZE {
            return Err(SparseError::InvalidChunkHeaderSize(chunk_hdr_sz));
        }
        Ok(hdr)
    }

    /// Parses a 28-byte sparse file header and returns `(header, file_hdr_sz, chunk_hdr_sz)`
    /// allowing extended header sizes (`>= 28` and `>= 12`).
    pub(crate) fn from_bytes_ext(
        bytes: &[u8; FILE_HEADER_SIZE],
    ) -> Result<(Self, u16, u16), SparseError> {
        let mut r = &bytes[..];
        let magic = r.get_u32_le();
        if magic != SPARSE_HEADER_MAGIC {
            return Err(SparseError::InvalidMagic(magic));
        }

        let major = r.get_u16_le();
        let minor = r.get_u16_le();
        if major != MAJOR_VERSION || minor != MINOR_VERSION {
            return Err(SparseError::UnsupportedVersion(major, minor));
        }

        let file_hdr_sz = r.get_u16_le();
        if usize::from(file_hdr_sz) < FILE_HEADER_SIZE {
            return Err(SparseError::InvalidHeaderSize(file_hdr_sz));
        }

        let chunk_hdr_sz = r.get_u16_le();
        if usize::from(chunk_hdr_sz) < CHUNK_HEADER_SIZE {
            return Err(SparseError::InvalidChunkHeaderSize(chunk_hdr_sz));
        }

        let raw_blk_sz = r.get_u32_le();
        let Some(block_size) = NonZeroU32::new(raw_blk_sz) else {
            return Err(SparseError::InvalidBlockSize(raw_blk_sz));
        };
        if block_size.get() % 4 != 0 {
            return Err(SparseError::InvalidBlockSize(raw_blk_sz));
        }

        let total_blocks = r.get_u32_le();
        let total_chunks = r.get_u32_le();
        let image_checksum = r.get_u32_le();

        Ok((
            Self {
                block_size,
                total_blocks,
                total_chunks,
                image_checksum,
            },
            file_hdr_sz,
            chunk_hdr_sz,
        ))
    }

    /// Serializes this file header to its 28-byte little-endian on-disk representation.
    pub fn to_bytes(&self) -> [u8; FILE_HEADER_SIZE] {
        let mut out = [0u8; FILE_HEADER_SIZE];
        let mut w = &mut out[..];
        w.put_u32_le(SPARSE_HEADER_MAGIC);
        w.put_u16_le(MAJOR_VERSION);
        w.put_u16_le(MINOR_VERSION);
        w.put_u16_le(FILE_HEADER_SIZE as u16);
        w.put_u16_le(CHUNK_HEADER_SIZE as u16);
        w.put_u32_le(self.block_size.get());
        w.put_u32_le(self.total_blocks);
        w.put_u32_le(self.total_chunks);
        w.put_u32_le(self.image_checksum);
        out
    }

    /// Returns the total expanded image size in bytes.
    pub fn expanded_size(&self) -> u64 {
        u64::from(self.total_blocks) * u64::from(self.block_size.get())
    }
}

/// Chunk payload encoding type in an Android sparse image.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
#[repr(u16)]
pub enum ChunkType {
    Raw = 0xcac1,
    Fill = 0xcac2,
    DontCare = 0xcac3,
    Crc32 = 0xcac4,
}

impl TryFrom<u16> for ChunkType {
    type Error = SparseError;

    fn try_from(value: u16) -> Result<Self, Self::Error> {
        match value {
            0xcac1 => Ok(Self::Raw),
            0xcac2 => Ok(Self::Fill),
            0xcac3 => Ok(Self::DontCare),
            0xcac4 => Ok(Self::Crc32),
            other => Err(SparseError::UnknownChunkType(other)),
        }
    }
}

/// Android sparse chunk header (12 bytes on disk).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChunkHeader {
    pub chunk_type: ChunkType,
    pub chunk_blocks: u32,
    pub total_size: u32,
}

impl ChunkHeader {
    /// Maximum number of blocks that fit in a single `Raw` chunk without overflowing `total_size: u32`.
    pub fn max_raw_blocks(block_size: NonZeroU32) -> u32 {
        (u32::MAX - CHUNK_HEADER_SIZE as u32) / block_size.get()
    }

    /// Creates a `Raw` chunk header for `blocks` blocks, failing if it overflows a single chunk.
    pub fn new_raw(blocks: u32, block_size: NonZeroU32) -> Result<Self, SparseError> {
        if blocks > Self::max_raw_blocks(block_size) {
            return Err(SparseError::InputTooBig);
        }
        Ok(Self {
            chunk_type: ChunkType::Raw,
            chunk_blocks: blocks,
            total_size: CHUNK_HEADER_SIZE as u32 + blocks * block_size.get(),
        })
    }

    /// Creates a `Raw` chunk header using as many of `blocks` as fit in one chunk,
    /// returning `(header, blocks_used)`.
    pub fn new_raw_fill(blocks: u32, block_size: NonZeroU32) -> (Self, u32) {
        let used = blocks.min(Self::max_raw_blocks(block_size));
        (
            Self {
                chunk_type: ChunkType::Raw,
                chunk_blocks: used,
                total_size: CHUNK_HEADER_SIZE as u32 + used * block_size.get(),
            },
            used,
        )
    }

    /// Creates a `Fill` chunk header for `blocks` blocks.
    pub fn new_fill(blocks: u32) -> Self {
        Self {
            chunk_type: ChunkType::Fill,
            chunk_blocks: blocks,
            total_size: (CHUNK_HEADER_SIZE + 4) as u32,
        }
    }

    /// Creates a `DontCare` chunk header for `blocks` blocks.
    pub fn new_dont_care(blocks: u32) -> Self {
        Self {
            chunk_type: ChunkType::DontCare,
            chunk_blocks: blocks,
            total_size: CHUNK_HEADER_SIZE as u32,
        }
    }

    /// Creates a `Crc32` chunk header.
    pub fn new_crc32() -> Self {
        Self {
            chunk_type: ChunkType::Crc32,
            chunk_blocks: 0,
            total_size: (CHUNK_HEADER_SIZE + 4) as u32,
        }
    }

    /// Parses a 12-byte chunk header.
    pub fn from_bytes(bytes: &[u8; CHUNK_HEADER_SIZE]) -> Result<Self, SparseError> {
        let mut r = &bytes[..];
        let raw_type = r.get_u16_le();
        let chunk_type = ChunkType::try_from(raw_type)?;
        let _reserved = r.get_u16_le();
        let chunk_blocks = r.get_u32_le();
        let total_size = r.get_u32_le();
        Ok(Self {
            chunk_type,
            chunk_blocks,
            total_size,
        })
    }

    /// Serializes this chunk header to its 12-byte little-endian on-disk representation.
    pub fn to_bytes(&self) -> [u8; CHUNK_HEADER_SIZE] {
        let mut out = [0u8; CHUNK_HEADER_SIZE];
        let mut w = &mut out[..];
        w.put_u16_le(self.chunk_type as u16);
        w.put_u16_le(0);
        w.put_u32_le(self.chunk_blocks);
        w.put_u32_le(self.total_size);
        out
    }

    /// Returns the number of payload bytes immediately following this chunk header.
    pub fn data_size(&self) -> u64 {
        u64::from(self.total_size).saturating_sub(CHUNK_HEADER_SIZE as u64)
    }

    /// Returns the expanded output byte count represented by this chunk.
    pub fn output_size(&self, block_size: NonZeroU32) -> u64 {
        u64::from(self.chunk_blocks) * u64::from(block_size.get())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_file_header_parse_and_roundtrip() {
        let data = [
            0x3au8, 0xff, 0x26, 0xed, 0x01, 0x00, 0x00, 0x00, 0x1c, 0x00, 0x0c, 0x00, 0x00, 0x10,
            0x00, 0x00, 0x77, 0x39, 0x14, 0x00, 0xb1, 0x00, 0x00, 0x00, 0xaa, 0x00, 0x00, 0xcc,
        ];
        let hdr = FileHeader::from_bytes(&data).unwrap();
        assert_eq!(
            hdr,
            FileHeader {
                block_size: DEFAULT_BLOCK_SIZE,
                total_blocks: 1_325_431,
                total_chunks: 177,
                image_checksum: 0xcc00_00aa,
            }
        );
        assert_eq!(hdr.to_bytes(), data);
    }

    #[test]
    fn test_chunk_header_parse_and_roundtrip() {
        let data = [
            0xc3u8, 0xca, 0x00, 0x00, 0x1f, 0xf1, 0xaa, 0xbb, 0x0c, 0x00, 0x00, 0x00,
        ];
        let hdr = ChunkHeader::from_bytes(&data).unwrap();
        assert_eq!(
            hdr,
            ChunkHeader {
                chunk_type: ChunkType::DontCare,
                chunk_blocks: 0xbbaa_f11f,
                total_size: CHUNK_HEADER_SIZE as u32,
            }
        );
        assert_eq!(hdr.to_bytes(), data);
    }
}
