use std::collections::BTreeMap;
use std::io::{Read, Write};
use std::num::NonZeroU32;

use crate::crc32::{sparse_crc32, sparse_crc32_fill, sparse_crc32_zeros};
use crate::format::{ChunkHeader, FileHeader, SparseError};

#[derive(Clone, Debug, PartialEq, Eq)]
enum BlockRegion {
    Raw(Vec<u8>),
    Fill { blocks: u32, value: u32 },
}

impl BlockRegion {
    fn blocks(&self, block_size: u32) -> u32 {
        match self {
            Self::Raw(data) => (data.len() / block_size as usize) as u32,
            Self::Fill { blocks, .. } => *blocks,
        }
    }

    fn slice_blocks(&self, offset_blocks: u32, len_blocks: u32, block_size: u32) -> Self {
        match self {
            Self::Raw(data) => {
                let start = offset_blocks as usize * block_size as usize;
                let end = start + len_blocks as usize * block_size as usize;
                Self::Raw(data[start..end].to_vec())
            }
            Self::Fill { value, .. } => Self::Fill {
                blocks: len_blocks,
                value: *value,
            },
        }
    }
}

enum EmitSegment<'a> {
    Raw {
        total_bytes: usize,
        slices: Vec<&'a [u8]>,
    },
    Fill {
        blocks: u32,
        value: u32,
    },
    DontCare(u32),
}

/// Builder/in-memory representation for constructing Android sparse images from
/// block-aligned writes, fills, and don't-care regions.
#[derive(Clone, Debug)]
pub struct SparseImage {
    block_size: NonZeroU32,
    total_blocks: u32,
    regions: BTreeMap<u32, BlockRegion>,
}

impl SparseImage {
    /// Creates an empty sparse image of `total_blocks` blocks (all implicitly `DontCare`).
    pub fn new(block_size: NonZeroU32, total_blocks: u32) -> Result<Self, SparseError> {
        if block_size.get() % 4 != 0 {
            return Err(SparseError::InvalidBlockSize(block_size.get()));
        }
        Ok(Self {
            block_size,
            total_blocks,
            regions: BTreeMap::new(),
        })
    }

    pub fn block_size(&self) -> NonZeroU32 {
        self.block_size
    }

    pub fn total_blocks(&self) -> u32 {
        self.total_blocks
    }

    fn clear_range(&mut self, start: u32, end: u32) {
        if start >= end {
            return;
        }
        let bs = self.block_size.get();

        // Check any single region starting strictly before `start` that overlaps `start`.
        if let Some((&prev_start, _)) = self.regions.range(..start).next_back() {
            let prev_len = self.regions[&prev_start].blocks(bs);
            let prev_end = prev_start + prev_len;
            if prev_end > start {
                let prev = self.regions.remove(&prev_start).unwrap();
                let prefix_len = start - prev_start;
                self.regions
                    .insert(prev_start, prev.slice_blocks(0, prefix_len, bs));
                if prev_end > end {
                    let suffix_offset = end - prev_start;
                    let suffix_len = prev_end - end;
                    self.regions
                        .insert(end, prev.slice_blocks(suffix_offset, suffix_len, bs));
                }
            }
        }

        // Remove/trim any regions whose start falls inside `[start, end)`.
        let overlapping: Vec<u32> = self.regions.range(start..end).map(|(&k, _)| k).collect();
        for k in overlapping {
            let reg = self.regions.remove(&k).unwrap();
            let reg_end = k + reg.blocks(bs);
            if reg_end > end {
                let suffix_offset = end - k;
                let suffix_len = reg_end - end;
                self.regions
                    .insert(end, reg.slice_blocks(suffix_offset, suffix_len, bs));
            }
        }
    }

    /// Writes `data` (must be a multiple of `block_size`) starting at `start_block`.
    pub fn add_raw_blocks(&mut self, start_block: u32, data: &[u8]) -> Result<(), SparseError> {
        let bs = self.block_size.get() as usize;
        if data.len() % bs != 0 {
            return Err(SparseError::UnalignedSize);
        }
        let num_blocks = u32::try_from(data.len() / bs).map_err(|_| SparseError::InputTooBig)?;
        if num_blocks == 0 {
            return Ok(());
        }
        let end_block = start_block
            .checked_add(num_blocks)
            .ok_or(SparseError::OutOfBounds)?;
        if end_block > self.total_blocks {
            return Err(SparseError::OutOfBounds);
        }

        self.clear_range(start_block, end_block);
        self.regions
            .insert(start_block, BlockRegion::Raw(data.to_vec()));
        Ok(())
    }

    /// Marks `num_blocks` starting at `start_block` as filled with repeated 32-bit `fill_val`.
    pub fn add_fill_blocks(
        &mut self,
        start_block: u32,
        num_blocks: u32,
        fill_val: u32,
    ) -> Result<(), SparseError> {
        if num_blocks == 0 {
            return Ok(());
        }
        let end_block = start_block
            .checked_add(num_blocks)
            .ok_or(SparseError::OutOfBounds)?;
        if end_block > self.total_blocks {
            return Err(SparseError::OutOfBounds);
        }
        self.clear_range(start_block, end_block);
        self.regions.insert(
            start_block,
            BlockRegion::Fill {
                blocks: num_blocks,
                value: fill_val,
            },
        );
        Ok(())
    }

    /// Marks `num_blocks` starting at `start_block` as `DontCare`.
    pub fn add_dont_care(&mut self, start_block: u32, num_blocks: u32) -> Result<(), SparseError> {
        if num_blocks == 0 {
            return Ok(());
        }
        let end_block = start_block
            .checked_add(num_blocks)
            .ok_or(SparseError::OutOfBounds)?;
        if end_block > self.total_blocks {
            return Err(SparseError::OutOfBounds);
        }
        self.clear_range(start_block, end_block);
        Ok(())
    }

    /// Builds a `SparseImage` by reading `total_size` bytes from `reader`, converting
    /// uniform 32-bit blocks (including zero blocks) into `Fill` chunks.
    pub fn from_raw<R: Read>(
        reader: &mut R,
        block_size: NonZeroU32,
        total_size: u64,
    ) -> Result<Self, SparseError> {
        Self::from_raw_with_options(reader, block_size, total_size, false)
    }

    /// Builds a `SparseImage` from a raw stream, optionally emitting `DontCare` instead of
    /// `Fill(0)` for all-zero blocks.
    pub fn from_raw_with_options<R: Read>(
        reader: &mut R,
        block_size: NonZeroU32,
        total_size: u64,
        zero_is_dont_care: bool,
    ) -> Result<Self, SparseError> {
        let bs = u64::from(block_size.get());
        if total_size % bs != 0 {
            return Err(SparseError::UnalignedSize);
        }
        let total_blocks = u32::try_from(total_size / bs).map_err(|_| SparseError::InputTooBig)?;
        let mut image = Self::new(block_size, total_blocks)?;
        let mut buf = vec![0u8; bs as usize];

        for blk in 0..total_blocks {
            reader.read_exact(&mut buf)?;
            if let Some(fill_val) = detect_uniform_u32(&buf) {
                if fill_val != 0 || !zero_is_dont_care {
                    image.add_fill_blocks(blk, 1, fill_val)?;
                }
            } else {
                image.add_raw_blocks(blk, &buf)?;
            }
        }
        Ok(image)
    }

    /// Writes the sparse image to `writer`, coalescing adjacent chunks and optionally appending
    /// a `ChunkType::Crc32` chunk.
    pub fn write_to<W: Write>(
        &self,
        writer: &mut W,
        include_crc: bool,
    ) -> Result<FileHeader, SparseError> {
        self.write_to_with_options(writer, include_crc, true)
    }

    /// Writes the sparse image to `writer` with configurable uniform-block Fill detection.
    pub fn write_to_with_options<W: Write>(
        &self,
        writer: &mut W,
        include_crc: bool,
        coalesce_fill: bool,
    ) -> Result<FileHeader, SparseError> {
        let segments = self.build_segments(coalesce_fill);
        let max_raw = ChunkHeader::max_raw_blocks(self.block_size);
        let bs = self.block_size.get() as usize;

        let mut chunk_count: u32 = 0;
        for seg in &segments {
            match seg {
                EmitSegment::Raw { total_bytes, .. } => {
                    let blocks = (*total_bytes / bs) as u32;
                    let parts = blocks.div_ceil(max_raw);
                    chunk_count = chunk_count
                        .checked_add(parts)
                        .ok_or(SparseError::InputTooBig)?;
                }
                EmitSegment::Fill { .. } | EmitSegment::DontCare(_) => {
                    chunk_count = chunk_count
                        .checked_add(1)
                        .ok_or(SparseError::InputTooBig)?;
                }
            }
        }
        if include_crc {
            chunk_count = chunk_count
                .checked_add(1)
                .ok_or(SparseError::InputTooBig)?;
        }

        let header = FileHeader {
            block_size: self.block_size,
            total_blocks: self.total_blocks,
            total_chunks: chunk_count,
            image_checksum: 0,
        };
        writer.write_all(&header.to_bytes())?;

        let mut running_crc = 0u32;
        for seg in segments {
            match seg {
                EmitSegment::Raw {
                    total_bytes,
                    slices,
                } => {
                    running_crc = write_raw_slices(
                        writer,
                        &slices,
                        total_bytes,
                        self.block_size,
                        max_raw,
                        include_crc,
                        running_crc,
                    )?;
                }
                EmitSegment::Fill { blocks, value } => {
                    let ch = ChunkHeader::new_fill(blocks);
                    writer.write_all(&ch.to_bytes())?;
                    writer.write_all(&value.to_le_bytes())?;
                    if include_crc {
                        running_crc =
                            sparse_crc32_fill(running_crc, value, ch.output_size(self.block_size));
                    }
                }
                EmitSegment::DontCare(blocks) => {
                    let ch = ChunkHeader::new_dont_care(blocks);
                    writer.write_all(&ch.to_bytes())?;
                    if include_crc {
                        running_crc =
                            sparse_crc32_zeros(running_crc, ch.output_size(self.block_size));
                    }
                }
            }
        }

        if include_crc {
            let ch = ChunkHeader::new_crc32();
            writer.write_all(&ch.to_bytes())?;
            writer.write_all(&running_crc.to_le_bytes())?;
        }

        Ok(header)
    }

    fn build_segments(&self, coalesce_fill: bool) -> Vec<EmitSegment<'_>> {
        let bs = self.block_size.get() as usize;
        let mut raw_pieces: Vec<EmitSegment<'_>> = Vec::new();
        let mut cur_block: u32 = 0;

        for (&start_block, region) in &self.regions {
            if start_block > cur_block {
                raw_pieces.push(EmitSegment::DontCare(start_block - cur_block));
                cur_block = start_block;
            }
            match region {
                BlockRegion::Fill { blocks, value } => {
                    raw_pieces.push(EmitSegment::Fill {
                        blocks: *blocks,
                        value: *value,
                    });
                    cur_block += *blocks;
                }
                BlockRegion::Raw(data) => {
                    if !coalesce_fill {
                        raw_pieces.push(EmitSegment::Raw {
                            total_bytes: data.len(),
                            slices: vec![data.as_slice()],
                        });
                    } else {
                        let mut raw_run_start: Option<usize> = None;
                        for (idx, blk_bytes) in data.chunks_exact(bs).enumerate() {
                            let byte_off = idx * bs;
                            if let Some(fill_val) = detect_uniform_u32(blk_bytes) {
                                if let Some(rs) = raw_run_start.take() {
                                    let slice = &data[rs..byte_off];
                                    raw_pieces.push(EmitSegment::Raw {
                                        total_bytes: slice.len(),
                                        slices: vec![slice],
                                    });
                                }
                                raw_pieces.push(EmitSegment::Fill {
                                    blocks: 1,
                                    value: fill_val,
                                });
                            } else if raw_run_start.is_none() {
                                raw_run_start = Some(byte_off);
                            }
                        }
                        if let Some(rs) = raw_run_start {
                            let slice = &data[rs..];
                            raw_pieces.push(EmitSegment::Raw {
                                total_bytes: slice.len(),
                                slices: vec![slice],
                            });
                        }
                    }
                    cur_block += (data.len() / bs) as u32;
                }
            }
        }

        if cur_block < self.total_blocks {
            raw_pieces.push(EmitSegment::DontCare(self.total_blocks - cur_block));
        }

        let mut out: Vec<EmitSegment<'_>> = Vec::with_capacity(raw_pieces.len());
        for seg in raw_pieces {
            match (out.last_mut(), seg) {
                (Some(EmitSegment::DontCare(prev)), EmitSegment::DontCare(n)) => {
                    *prev += n;
                }
                (
                    Some(EmitSegment::Fill {
                        blocks: pb,
                        value: pv,
                    }),
                    EmitSegment::Fill { blocks, value },
                ) if *pv == value => {
                    *pb += blocks;
                }
                (
                    Some(EmitSegment::Raw {
                        total_bytes: ptb,
                        slices: ps,
                    }),
                    EmitSegment::Raw {
                        total_bytes,
                        slices,
                    },
                ) => {
                    *ptb += total_bytes;
                    ps.extend(slices);
                }
                (_, s) => out.push(s),
            }
        }
        out
    }
}

fn write_raw_slices<W: Write>(
    writer: &mut W,
    slices: &[&[u8]],
    total_bytes: usize,
    block_size: NonZeroU32,
    max_raw: u32,
    include_crc: bool,
    mut running_crc: u32,
) -> Result<u32, SparseError> {
    let bs = block_size.get() as usize;
    let max_chunk_bytes = max_raw as usize * bs;
    let mut remaining = total_bytes;
    let mut slice_idx = 0usize;
    let mut slice_off = 0usize;

    while remaining > 0 {
        let mut chunk_bytes = remaining.min(max_chunk_bytes);
        let blocks = (chunk_bytes / bs) as u32;
        let ch = ChunkHeader::new_raw(blocks, block_size)?;
        writer.write_all(&ch.to_bytes())?;
        remaining -= chunk_bytes;

        while chunk_bytes > 0 {
            let cur = &slices[slice_idx][slice_off..];
            let take = cur.len().min(chunk_bytes);
            let piece = &cur[..take];
            writer.write_all(piece)?;
            if include_crc {
                running_crc = sparse_crc32(running_crc, piece);
            }
            chunk_bytes -= take;
            slice_off += take;
            if slice_off == slices[slice_idx].len() {
                slice_idx += 1;
                slice_off = 0;
            }
        }
    }
    Ok(running_crc)
}

fn detect_uniform_u32(block: &[u8]) -> Option<u32> {
    let mut chunks = block.chunks_exact(4);
    let first = u32::from_le_bytes(chunks.next()?.try_into().ok()?);
    for c in chunks {
        if u32::from_le_bytes(c.try_into().ok()?) != first {
            return None;
        }
    }
    Some(first)
}
