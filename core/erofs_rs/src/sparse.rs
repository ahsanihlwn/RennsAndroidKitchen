// SPDX-License-Identifier: MIT

//! Android Sparse image reader (`0xED26FF3A`) and filesystem auto-detection.

use crate::error::{ErofsError, Result};
use crate::format::{EROFS_SUPER_MAGIC, read_u16_le, read_u32_le};
use serde::Serialize;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

pub const SPARSE_MAGIC: u32 = 0xED26_FF3A;
pub const EXT_SUPER_MAGIC: u16 = 0xEF53;

const CHUNK_TYPE_RAW: u16 = 0xCAC1;
const CHUNK_TYPE_FILL: u16 = 0xCAC2;
const CHUNK_TYPE_DONT_CARE: u16 = 0xCAC3;
const CHUNK_TYPE_CRC32: u16 = 0xCAC4;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum FsKind {
    Erofs,
    Ext,
    Unknown,
}

impl FsKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Erofs => "EROFS",
            Self::Ext => "EXT",
            Self::Unknown => "UNKNOWN",
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct DetectInfo {
    pub fs: FsKind,
    pub is_sparse: bool,
    pub erofs_magic_offset: Option<u64>,
    pub virtual_size: u64,
    pub file_size: u64,
}

#[derive(Debug, Clone)]
enum ChunkPayload {
    Raw { file_offset: u64 },
    Fill { pattern: [u8; 4] },
    DontCare,
}

#[derive(Debug, Clone)]
struct SparseChunk {
    out_start: u64,
    out_end: u64,
    payload: ChunkPayload,
}

/// Random-access reader for Android Sparse images.
#[derive(Debug)]
pub struct SparseImage {
    file: File,
    pub block_size: u32,
    pub total_blocks: u32,
    pub total_size: u64,
    chunks: Vec<SparseChunk>,
}

impl SparseImage {
    pub fn open(path: &Path) -> Result<Self> {
        let mut file = File::open(path)?;
        let mut hdr = [0u8; 28];
        file.read_exact(&mut hdr)?;

        let magic = read_u32_le(&hdr, 0);
        if magic != SPARSE_MAGIC {
            return Err(ErofsError::InvalidFormat("not an Android sparse image".into()));
        }
        let major = read_u16_le(&hdr, 4);
        if major != 1 {
            return Err(ErofsError::Unsupported(format!(
                "unsupported sparse major version {major}"
            )));
        }
        let file_hdr_sz = read_u16_le(&hdr, 8) as u64;
        let chunk_hdr_sz = read_u16_le(&hdr, 10) as u64;
        let block_size = read_u32_le(&hdr, 12);
        let total_blocks = read_u32_le(&hdr, 16);
        let total_chunks = read_u32_le(&hdr, 20);

        if file_hdr_sz < 28 || chunk_hdr_sz < 12 || block_size == 0 {
            return Err(ErofsError::InvalidFormat("invalid sparse header fields".into()));
        }

        let mut file_pos = file_hdr_sz;
        let mut out_pos = 0u64;
        let mut chunks = Vec::with_capacity(total_chunks as usize);
        let mut chdr = [0u8; 12];

        for _ in 0..total_chunks {
            file.seek(SeekFrom::Start(file_pos))?;
            file.read_exact(&mut chdr)?;
            let chunk_type = read_u16_le(&chdr, 0);
            let chunk_sz = read_u32_le(&chdr, 4) as u64;
            let total_sz = read_u32_le(&chdr, 8) as u64;
            if total_sz < chunk_hdr_sz {
                return Err(ErofsError::Corrupted("sparse chunk total_sz < header size".into()));
            }
            let data_off = file_pos + chunk_hdr_sz;
            let data_len = total_sz - chunk_hdr_sz;
            let out_len = chunk_sz.saturating_mul(block_size as u64);

            match chunk_type {
                CHUNK_TYPE_RAW => {
                    if data_len < out_len {
                        return Err(ErofsError::Corrupted("truncated RAW sparse chunk".into()));
                    }
                    chunks.push(SparseChunk {
                        out_start: out_pos,
                        out_end: out_pos + out_len,
                        payload: ChunkPayload::Raw {
                            file_offset: data_off,
                        },
                    });
                    out_pos += out_len;
                }
                CHUNK_TYPE_FILL => {
                    if data_len < 4 {
                        return Err(ErofsError::Corrupted("truncated FILL sparse chunk".into()));
                    }
                    file.seek(SeekFrom::Start(data_off))?;
                    let mut pat = [0u8; 4];
                    file.read_exact(&mut pat)?;
                    chunks.push(SparseChunk {
                        out_start: out_pos,
                        out_end: out_pos + out_len,
                        payload: ChunkPayload::Fill { pattern: pat },
                    });
                    out_pos += out_len;
                }
                CHUNK_TYPE_DONT_CARE => {
                    chunks.push(SparseChunk {
                        out_start: out_pos,
                        out_end: out_pos + out_len,
                        payload: ChunkPayload::DontCare,
                    });
                    out_pos += out_len;
                }
                CHUNK_TYPE_CRC32 => {
                    // Metadata-only chunk, consumes no virtual blocks.
                }
                other => {
                    return Err(ErofsError::Unsupported(format!(
                        "unknown sparse chunk type 0x{other:04x}"
                    )));
                }
            }

            file_pos += total_sz;
        }

        let declared = (total_blocks as u64) * (block_size as u64);
        let total_size = declared.max(out_pos);

        Ok(Self {
            file,
            block_size,
            total_blocks,
            total_size,
            chunks,
        })
    }

    pub fn read_at(&self, mut offset: u64, mut buf: &mut [u8]) -> Result<usize> {
        if offset >= self.total_size || buf.is_empty() {
            return Ok(0);
        }
        let max_len = ((self.total_size - offset) as usize).min(buf.len());
        buf = &mut buf[..max_len];
        let mut total_read = 0usize;

        while !buf.is_empty() && offset < self.total_size {
            let idx = self
                .chunks
                .partition_point(|c| c.out_end <= offset);
            if idx >= self.chunks.len() {
                buf.fill(0);
                total_read += buf.len();
                break;
            }
            let chunk = &self.chunks[idx];
            if offset < chunk.out_start {
                let gap = ((chunk.out_start - offset) as usize).min(buf.len());
                buf[..gap].fill(0);
                buf = &mut buf[gap..];
                offset += gap as u64;
                total_read += gap;
                continue;
            }

            let in_chunk = offset - chunk.out_start;
            let avail = (chunk.out_end - offset) as usize;
            let take = avail.min(buf.len());

            match chunk.payload {
                ChunkPayload::Raw { file_offset } => {
                    let mut f = self.file.try_clone()?;
                    f.seek(SeekFrom::Start(file_offset + in_chunk))?;
                    f.read_exact(&mut buf[..take])?;
                }
                ChunkPayload::Fill { pattern } => {
                    let base = (in_chunk as usize) & 3;
                    for (i, b) in buf[..take].iter_mut().enumerate() {
                        *b = pattern[(base + i) & 3];
                    }
                }
                ChunkPayload::DontCare => {
                    buf[..take].fill(0);
                }
            }

            buf = &mut buf[take..];
            offset += take as u64;
            total_read += take;
        }

        Ok(total_read)
    }
}

pub fn is_sparse_file(path: &Path) -> bool {
    let Ok(mut f) = File::open(path) else {
        return false;
    };
    let mut magic = [0u8; 4];
    if f.read_exact(&mut magic).is_err() {
        return false;
    }
    u32::from_le_bytes(magic) == SPARSE_MAGIC
}

fn detect_fs_bytes(data: &[u8]) -> (FsKind, Option<u64>) {
    for &off in &[1024usize, 0, 512, 4096] {
        if data.len() >= off + 4 && read_u32_le(data, off) == EROFS_SUPER_MAGIC {
            return (FsKind::Erofs, Some(off as u64));
        }
    }
    for &sb_start in &[1024usize, 0] {
        if data.len() >= sb_start + 0x3A && read_u16_le(data, sb_start + 0x38) == EXT_SUPER_MAGIC {
            return (FsKind::Ext, None);
        }
    }
    (FsKind::Unknown, None)
}

/// Detect filesystem type (EROFS / EXT / UNKNOWN) and whether the image is Android Sparse.
pub fn detect_fs(path: &Path, offset: u64) -> Result<DetectInfo> {
    let meta = std::fs::metadata(path)?;
    let file_size = meta.len();
    let sparse = offset == 0 && is_sparse_file(path);

    let mut head = vec![0u8; 65536];
    let (read_len, virtual_size) = if sparse {
        let img = SparseImage::open(path)?;
        let n = img.read_at(0, &mut head)?;
        (n, img.total_size)
    } else {
        let mut f = File::open(path)?;
        if offset > 0 {
            f.seek(SeekFrom::Start(offset))?;
        }
        let n = f.read(&mut head)?;
        (n, file_size.saturating_sub(offset))
    };

    let (fs, erofs_magic_offset) = detect_fs_bytes(&head[..read_len]);
    Ok(DetectInfo {
        fs,
        is_sparse: sparse,
        erofs_magic_offset,
        virtual_size,
        file_size,
    })
}
