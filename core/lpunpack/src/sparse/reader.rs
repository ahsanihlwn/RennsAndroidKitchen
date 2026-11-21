//! Streaming reader for Android sparse and raw images.

use std::io::{self, Read, Seek, SeekFrom};

use crate::crc32::sparse_crc32;

use super::format::{
    ChunkHeader, SparseError, SparseHeader, CHUNK_TYPE_CRC32, CHUNK_TYPE_DONT_CARE,
    CHUNK_TYPE_FILL, CHUNK_TYPE_RAW, SPARSE_CHUNK_HEADER_SIZE, SPARSE_HEADER_MAGIC,
    SPARSE_HEADER_MAJOR_VER, SPARSE_HEADER_SIZE,
};

const COPY_BUF_SIZE: usize = 1024 * 1024;

#[derive(Debug, Clone)]
enum ChunkKind {
    Raw { file_offset: u64 },
    Fill([u8; 4]),
    DontCare,
}

#[derive(Debug, Clone)]
struct ChunkMapping {
    logical_start: u64,
    len: u64,
    kind: ChunkKind,
}

/// Random-access `Read + Seek` reader over an Android sparse image without
/// unpacking the full image to disk.
pub struct SparseReader<R> {
    inner: R,
    header: SparseHeader,
    chunks: Vec<ChunkMapping>,
    total_size: u64,
    pos: u64,
    inner_pos: Option<u64>,
}

impl<R: Read + Seek> SparseReader<R> {
    /// Check whether the stream starts with the Android sparse magic header.
    /// Restores the stream position before returning.
    pub fn is_sparse(reader: &mut R) -> io::Result<bool> {
        let saved = reader.stream_position()?;
        reader.seek(SeekFrom::Start(0))?;
        let mut magic = [0u8; 4];
        let is_match = match reader.read_exact(&mut magic) {
            Ok(()) => u32::from_le_bytes(magic) == SPARSE_HEADER_MAGIC,
            Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => false,
            Err(e) => {
                let _ = reader.seek(SeekFrom::Start(saved));
                return Err(e);
            }
        };
        reader.seek(SeekFrom::Start(saved))?;
        Ok(is_match)
    }

    /// Parse the sparse image chunk index, optionally verifying embedded CRC32 chunks.
    pub fn open(mut inner: R, verify_crc: bool) -> Result<Self, SparseError> {
        inner.seek(SeekFrom::Start(0))?;
        let mut hdr_buf = [0u8; SPARSE_HEADER_SIZE];
        inner.read_exact(&mut hdr_buf)?;
        let header = SparseHeader::from_bytes(&hdr_buf);

        if header.magic != SPARSE_HEADER_MAGIC {
            return Err(SparseError::InvalidFormat("bad sparse magic".into()));
        }
        if header.major_version != SPARSE_HEADER_MAJOR_VER {
            return Err(SparseError::InvalidFormat(format!(
                "unsupported major version {}",
                header.major_version
            )));
        }
        if (header.file_hdr_sz as usize) < SPARSE_HEADER_SIZE {
            return Err(SparseError::InvalidFormat("file header too small".into()));
        }
        if (header.chunk_hdr_sz as usize) < SPARSE_CHUNK_HEADER_SIZE {
            return Err(SparseError::InvalidFormat("chunk header too small".into()));
        }
        if header.blk_sz == 0 || header.blk_sz % 4 != 0 {
            return Err(SparseError::InvalidFormat(format!(
                "invalid block size {}",
                header.blk_sz
            )));
        }

        let extra_hdr = header.file_hdr_sz as i64 - SPARSE_HEADER_SIZE as i64;
        if extra_hdr > 0 {
            inner.seek(SeekFrom::Current(extra_hdr))?;
        }

        let blk_sz = header.blk_sz as u64;
        let mut chunks = Vec::with_capacity(header.total_chunks as usize);
        let mut cur_block: u64 = 0;
        let mut crc32: u32 = 0;
        let mut scratch = if verify_crc {
            vec![0u8; COPY_BUF_SIZE]
        } else {
            Vec::new()
        };

        for idx in 0..header.total_chunks {
            let mut ch_buf = [0u8; SPARSE_CHUNK_HEADER_SIZE];
            inner.read_exact(&mut ch_buf)?;
            let ch = ChunkHeader::from_bytes(&ch_buf);

            let extra_ch = header.chunk_hdr_sz as i64 - SPARSE_CHUNK_HEADER_SIZE as i64;
            if extra_ch > 0 {
                inner.seek(SeekFrom::Current(extra_ch))?;
            }

            if ch.total_sz < header.chunk_hdr_sz as u32 {
                return Err(SparseError::InvalidFormat(format!(
                    "chunk {idx}: total_sz smaller than chunk header"
                )));
            }
            let chunk_data_size = (ch.total_sz - header.chunk_hdr_sz as u32) as u64;
            let logical_start = cur_block
                .checked_mul(blk_sz)
                .ok_or_else(|| SparseError::InvalidFormat("logical offset overflow".into()))?;
            let chunk_bytes = (ch.chunk_sz as u64)
                .checked_mul(blk_sz)
                .ok_or_else(|| SparseError::InvalidFormat("chunk byte length overflow".into()))?;

            match ch.chunk_type {
                CHUNK_TYPE_RAW => {
                    if chunk_data_size != chunk_bytes {
                        return Err(SparseError::InvalidFormat(format!(
                            "raw chunk {idx}: data size {chunk_data_size} != expected {chunk_bytes}"
                        )));
                    }
                    let file_offset = inner.stream_position()?;
                    if verify_crc {
                        let mut rem = chunk_data_size;
                        while rem > 0 {
                            let step = (rem as usize).min(scratch.len());
                            inner.read_exact(&mut scratch[..step])?;
                            crc32 = sparse_crc32(crc32, &scratch[..step]);
                            rem -= step as u64;
                        }
                    } else if chunk_data_size > 0 {
                        inner.seek(SeekFrom::Current(chunk_data_size as i64))?;
                    }
                    if chunk_bytes > 0 {
                        chunks.push(ChunkMapping {
                            logical_start,
                            len: chunk_bytes,
                            kind: ChunkKind::Raw { file_offset },
                        });
                    }
                    cur_block = cur_block
                        .checked_add(ch.chunk_sz as u64)
                        .ok_or_else(|| SparseError::InvalidFormat("block count overflow".into()))?;
                }
                CHUNK_TYPE_FILL => {
                    if chunk_data_size != 4 {
                        return Err(SparseError::InvalidFormat(format!(
                            "fill chunk {idx}: expected 4 data bytes, got {chunk_data_size}"
                        )));
                    }
                    let mut fill_bytes = [0u8; 4];
                    inner.read_exact(&mut fill_bytes)?;
                    if verify_crc {
                        for chunk in scratch.chunks_exact_mut(4) {
                            chunk.copy_from_slice(&fill_bytes);
                        }
                        let mut rem = chunk_bytes;
                        while rem > 0 {
                            let step = (rem as usize).min(scratch.len());
                            crc32 = sparse_crc32(crc32, &scratch[..step]);
                            rem -= step as u64;
                        }
                    }
                    if chunk_bytes > 0 {
                        chunks.push(ChunkMapping {
                            logical_start,
                            len: chunk_bytes,
                            kind: ChunkKind::Fill(fill_bytes),
                        });
                    }
                    cur_block = cur_block
                        .checked_add(ch.chunk_sz as u64)
                        .ok_or_else(|| SparseError::InvalidFormat("block count overflow".into()))?;
                }
                CHUNK_TYPE_DONT_CARE => {
                    if chunk_data_size != 0 {
                        return Err(SparseError::InvalidFormat(format!(
                            "don't-care chunk {idx}: expected 0 data bytes, got {chunk_data_size}"
                        )));
                    }
                    if verify_crc {
                        scratch.fill(0);
                        let mut rem = chunk_bytes;
                        while rem > 0 {
                            let step = (rem as usize).min(scratch.len());
                            crc32 = sparse_crc32(crc32, &scratch[..step]);
                            rem -= step as u64;
                        }
                    }
                    if chunk_bytes > 0 {
                        chunks.push(ChunkMapping {
                            logical_start,
                            len: chunk_bytes,
                            kind: ChunkKind::DontCare,
                        });
                    }
                    cur_block = cur_block
                        .checked_add(ch.chunk_sz as u64)
                        .ok_or_else(|| SparseError::InvalidFormat("block count overflow".into()))?;
                }
                CHUNK_TYPE_CRC32 => {
                    if chunk_data_size != 4 {
                        return Err(SparseError::InvalidFormat(format!(
                            "crc32 chunk {idx}: expected 4 data bytes, got {chunk_data_size}"
                        )));
                    }
                    let mut crc_bytes = [0u8; 4];
                    inner.read_exact(&mut crc_bytes)?;
                    if verify_crc {
                        let expected = u32::from_le_bytes(crc_bytes);
                        if expected != crc32 {
                            return Err(SparseError::InvalidFormat(format!(
                                "CRC32 mismatch at chunk {idx}: expected {expected:#010x}, computed {crc32:#010x}"
                            )));
                        }
                    }
                }
                other => {
                    return Err(SparseError::InvalidFormat(format!(
                        "unknown chunk type {other:#06x} at chunk {idx}"
                    )));
                }
            }
        }

        if cur_block != header.total_blks as u64 {
            return Err(SparseError::InvalidFormat(format!(
                "block count mismatch: decoded {cur_block}, header expects {}",
                header.total_blks
            )));
        }

        let total_size = (header.total_blks as u64) * blk_sz;
        let inner_pos = inner.stream_position().ok();
        Ok(Self {
            inner,
            header,
            chunks,
            total_size,
            pos: 0,
            inner_pos,
        })
    }

    pub fn header(&self) -> &SparseHeader {
        &self.header
    }

    pub fn logical_size(&self) -> u64 {
        self.total_size
    }

    /// Unpack the sparse image sequentially into `writer`.
    pub fn write_raw_to<W: io::Write + Seek>(&mut self, writer: &mut W) -> io::Result<()> {
        let mut buf = vec![0u8; COPY_BUF_SIZE];
        let mut hole: u64 = 0;

        for chunk in &self.chunks {
            match chunk.kind {
                ChunkKind::DontCare => {
                    hole += chunk.len;
                }
                ChunkKind::Fill(fill) => {
                    if fill == [0, 0, 0, 0] {
                        hole += chunk.len;
                        continue;
                    }
                    if hole > 0 {
                        writer.seek(SeekFrom::Current(hole as i64))?;
                        hole = 0;
                    }
                    for slot in buf.chunks_exact_mut(4) {
                        slot.copy_from_slice(&fill);
                    }
                    let mut rem = chunk.len;
                    while rem > 0 {
                        let step = (rem as usize).min(buf.len());
                        writer.write_all(&buf[..step])?;
                        rem -= step as u64;
                    }
                }
                ChunkKind::Raw { file_offset } => {
                    if hole > 0 {
                        writer.seek(SeekFrom::Current(hole as i64))?;
                        hole = 0;
                    }
                    self.inner.seek(SeekFrom::Start(file_offset))?;
                    let mut rem = chunk.len;
                    while rem > 0 {
                        let step = (rem as usize).min(buf.len());
                        self.inner.read_exact(&mut buf[..step])?;
                        writer.write_all(&buf[..step])?;
                        rem -= step as u64;
                    }
                }
            }
        }

        if hole > 0 {
            writer.seek(SeekFrom::Current(hole as i64 - 1))?;
            writer.write_all(&[0])?;
        }
        Ok(())
    }
}

impl<R: Read + Seek> Read for SparseReader<R> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if buf.is_empty() || self.pos >= self.total_size {
            return Ok(0);
        }

        let idx = self
            .chunks
            .partition_point(|c| c.logical_start + c.len <= self.pos);
        if idx >= self.chunks.len() {
            let avail = (self.total_size - self.pos) as usize;
            let n = buf.len().min(avail);
            buf[..n].fill(0);
            self.pos += n as u64;
            return Ok(n);
        }

        let chunk = &self.chunks[idx];
        if self.pos < chunk.logical_start {
            let gap = (chunk.logical_start - self.pos) as usize;
            let n = buf.len().min(gap);
            buf[..n].fill(0);
            self.pos += n as u64;
            return Ok(n);
        }

        let offset_in_chunk = self.pos - chunk.logical_start;
        let rem_in_chunk = (chunk.len - offset_in_chunk) as usize;
        let to_read = buf.len().min(rem_in_chunk);

        match chunk.kind {
            ChunkKind::Raw { file_offset } => {
                let target = file_offset + offset_in_chunk;
                if self.inner_pos != Some(target) {
                    self.inner.seek(SeekFrom::Start(target))?;
                }
                let n = self.inner.read(&mut buf[..to_read])?;
                self.inner_pos = Some(target + n as u64);
                self.pos += n as u64;
                Ok(n)
            }
            ChunkKind::Fill(fill) => {
                let start_phase = (offset_in_chunk % 4) as usize;
                for (i, byte) in buf[..to_read].iter_mut().enumerate() {
                    *byte = fill[(start_phase + i) % 4];
                }
                self.pos += to_read as u64;
                Ok(to_read)
            }
            ChunkKind::DontCare => {
                buf[..to_read].fill(0);
                self.pos += to_read as u64;
                Ok(to_read)
            }
        }
    }
}

impl<R: Read + Seek> Seek for SparseReader<R> {
    fn seek(&mut self, pos: SeekFrom) -> io::Result<u64> {
        let next: i64 = match pos {
            SeekFrom::Start(off) => off as i64,
            SeekFrom::Current(delta) => (self.pos as i64)
                .checked_add(delta)
                .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "seek overflow"))?,
            SeekFrom::End(delta) => (self.total_size as i64)
                .checked_add(delta)
                .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "seek overflow"))?,
        };
        if next < 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid seek to negative position",
            ));
        }
        self.pos = next as u64;
        Ok(self.pos)
    }
}

/// Transparent reader over either a raw image or an Android sparse image.
pub enum ImageSource<R> {
    Raw { reader: R, size: u64 },
    Sparse(SparseReader<R>),
}

impl<R: Read + Seek> ImageSource<R> {
    pub fn open(mut reader: R) -> Result<Self, SparseError> {
        if SparseReader::is_sparse(&mut reader)? {
            let sparse = SparseReader::open(reader, false)?;
            Ok(ImageSource::Sparse(sparse))
        } else {
            let size = reader.seek(SeekFrom::End(0))?;
            reader.seek(SeekFrom::Start(0))?;
            Ok(ImageSource::Raw { reader, size })
        }
    }

    pub fn is_sparse(&self) -> bool {
        matches!(self, ImageSource::Sparse(_))
    }

    pub fn logical_size(&self) -> u64 {
        match self {
            ImageSource::Raw { size, .. } => *size,
            ImageSource::Sparse(s) => s.logical_size(),
        }
    }
}

impl<R: Read + Seek> Read for ImageSource<R> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        match self {
            ImageSource::Raw { reader, .. } => reader.read(buf),
            ImageSource::Sparse(s) => s.read(buf),
        }
    }
}

impl<R: Read + Seek> Seek for ImageSource<R> {
    fn seek(&mut self, pos: SeekFrom) -> io::Result<u64> {
        match self {
            ImageSource::Raw { reader, .. } => reader.seek(pos),
            ImageSource::Sparse(s) => s.seek(pos),
        }
    }
}
