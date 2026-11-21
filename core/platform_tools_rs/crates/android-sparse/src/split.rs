use std::num::NonZeroU32;

use crate::format::{
    ChunkHeader, ChunkType, FileHeader, SparseError, CHUNK_HEADER_SIZE, FILE_HEADER_SIZE,
};

/// A chunk reference inside a [`SplitPart`].
///
/// `input_offset` is the byte offset of the chunk's payload (after its chunk header in a sparse
/// input, or in the raw image for [`split_raw`]), and `data_size` is the number of payload bytes
/// to copy (`0` for `DontCare`, `4` for `Fill`, `blocks * block_size` for `Raw`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SplitChunk {
    pub header: ChunkHeader,
    pub input_offset: u64,
    pub data_size: u64,
}

/// One self-contained sparse image part produced by [`split_sparse`] or [`split_raw`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SplitPart {
    pub header: FileHeader,
    pub chunks: Vec<SplitChunk>,
}

impl SplitPart {
    /// Total serialized size in bytes of this split sparse image.
    pub fn sparse_size(&self) -> u64 {
        FILE_HEADER_SIZE as u64
            + self
                .chunks
                .iter()
                .map(|c| u64::from(c.header.total_size))
                .sum::<u64>()
    }
}

struct SplitBuilder {
    space: u64,
    block_size: NonZeroU32,
    chunks: Vec<SplitChunk>,
    has_payload_chunk: bool,
}

impl SplitBuilder {
    fn new(block_size: NonZeroU32, max_size: u64, blocks_offset: u32) -> Self {
        let mut space = max_size - FILE_HEADER_SIZE as u64;
        let chunks = if blocks_offset == 0 {
            Vec::new()
        } else {
            let header = ChunkHeader::new_dont_care(blocks_offset);
            space = space.saturating_sub(u64::from(header.total_size));
            vec![SplitChunk {
                header,
                input_offset: 0,
                data_size: 0,
            }]
        };
        Self {
            space,
            block_size,
            chunks,
            has_payload_chunk: false,
        }
    }

    fn try_add_chunk(&mut self, chunk: &ChunkHeader, payload_offset: u64) -> bool {
        if self.space >= u64::from(chunk.total_size) {
            self.space -= u64::from(chunk.total_size);
            if chunk.chunk_type != ChunkType::DontCare {
                self.has_payload_chunk = true;
            }
            self.chunks.push(SplitChunk {
                header: chunk.clone(),
                input_offset: payload_offset,
                data_size: chunk.data_size(),
            });
            true
        } else {
            false
        }
    }

    fn add_raw(&mut self, payload_offset: u64, blocks: u32) -> u32 {
        let left = self.space.saturating_sub(CHUNK_HEADER_SIZE as u64);
        let blocks_left = (left / u64::from(self.block_size.get()))
            .try_into()
            .unwrap_or(u32::MAX);
        if blocks_left == 0 {
            return 0;
        }
        let take = blocks.min(blocks_left);
        let (header, used) = ChunkHeader::new_raw_fill(take, self.block_size);
        if used == 0 {
            return 0;
        }
        self.space -= u64::from(header.total_size);
        self.has_payload_chunk = true;
        self.chunks.push(SplitChunk {
            data_size: header.data_size(),
            input_offset: payload_offset,
            header,
        });
        used
    }

    fn finish(self) -> SplitPart {
        let total_blocks = self.chunks.iter().map(|c| c.header.chunk_blocks).sum();
        let total_chunks = self.chunks.len() as u32;
        SplitPart {
            header: FileHeader {
                block_size: self.block_size,
                total_blocks,
                total_chunks,
                image_checksum: 0,
            },
            chunks: self.chunks,
        }
    }
}

fn check_max_size(max_size: u64, block_size: NonZeroU32) -> Result<(), SparseError> {
    let min_size =
        FILE_HEADER_SIZE as u64 + 2 * CHUNK_HEADER_SIZE as u64 + u64::from(block_size.get());
    if max_size < min_size {
        return Err(SparseError::OutputTooSmall);
    }
    Ok(())
}

/// Splits an existing sparse image (described by `header` and `chunks`) into parts each no larger
/// than `max_size` bytes.
pub fn split_sparse(
    header: &FileHeader,
    chunks: &[ChunkHeader],
    max_size: u64,
) -> Result<Vec<SplitPart>, SparseError> {
    check_max_size(max_size, header.block_size)?;
    if ChunkHeader::max_raw_blocks(header.block_size) == 0 {
        return Err(SparseError::BlockSizeTooBig);
    }

    let mut splits: Vec<SplitPart> = Vec::new();
    let mut builder: Option<SplitBuilder> = None;
    let mut block_offset: u32 = 0;
    let mut payload_offset: u64 = (FILE_HEADER_SIZE + CHUNK_HEADER_SIZE) as u64;

    for chunk in chunks {
        match chunk.chunk_type {
            ChunkType::Crc32 => {}
            ChunkType::DontCare => {
                if let Some(b) = builder.as_mut() {
                    if b.has_payload_chunk && !b.try_add_chunk(chunk, 0) {
                        let finished = builder.take().unwrap().finish();
                        splits.push(finished);
                    }
                }
            }
            ChunkType::Fill => {
                if let Some(b) = builder.as_mut() {
                    if !b.try_add_chunk(chunk, payload_offset) {
                        let old = builder.take().unwrap();
                        if old.has_payload_chunk {
                            splits.push(old.finish());
                        }
                        let mut fresh =
                            SplitBuilder::new(header.block_size, max_size, block_offset);
                        if !fresh.try_add_chunk(chunk, payload_offset) {
                            return Err(SparseError::OutputTooSmall);
                        }
                        builder = Some(fresh);
                    }
                } else {
                    let mut fresh = SplitBuilder::new(header.block_size, max_size, block_offset);
                    if !fresh.try_add_chunk(chunk, payload_offset) {
                        return Err(SparseError::OutputTooSmall);
                    }
                    builder = Some(fresh);
                }
            }
            ChunkType::Raw => {
                let mut done_blocks: u32 = 0;
                while done_blocks < chunk.chunk_blocks {
                    if builder.is_none() {
                        builder = Some(SplitBuilder::new(
                            header.block_size,
                            max_size,
                            block_offset + done_blocks,
                        ));
                    }
                    let b = builder.as_mut().unwrap();
                    let cur_off = payload_offset
                        + u64::from(done_blocks) * u64::from(header.block_size.get());
                    let consumed = b.add_raw(cur_off, chunk.chunk_blocks - done_blocks);
                    if consumed == 0 {
                        let old = builder.take().unwrap();
                        if old.has_payload_chunk {
                            splits.push(old.finish());
                        } else {
                            return Err(SparseError::OutputTooSmall);
                        }
                    } else {
                        done_blocks += consumed;
                    }
                }
            }
        }

        block_offset = block_offset
            .checked_add(chunk.chunk_blocks)
            .ok_or(SparseError::InputTooBig)?;
        payload_offset += u64::from(chunk.total_size);
    }

    if let Some(b) = builder {
        if b.has_payload_chunk || splits.is_empty() {
            splits.push(b.finish());
        }
    } else if splits.is_empty() {
        let mut fresh = SplitBuilder::new(header.block_size, max_size, block_offset);
        if block_offset == 0 {
            fresh.chunks.clear();
        }
        splits.push(fresh.finish());
    }

    Ok(splits)
}

/// Splits a raw image of `raw_size` bytes into sparse [`SplitPart`]s each fitting within
/// `max_size` bytes.
pub fn split_raw(
    raw_size: u64,
    block_size: NonZeroU32,
    max_size: u64,
) -> Result<Vec<SplitPart>, SparseError> {
    check_max_size(max_size, block_size)?;
    if ChunkHeader::max_raw_blocks(block_size) == 0 {
        return Err(SparseError::BlockSizeTooBig);
    }

    let raw_blocks: u32 = raw_size
        .div_ceil(u64::from(block_size.get()))
        .try_into()
        .map_err(|_| SparseError::InputTooBig)?;

    let mut splits = Vec::new();
    let mut block_offset: u32 = 0;
    let mut builder = SplitBuilder::new(block_size, max_size, 0);

    while block_offset < raw_blocks {
        let consumed = builder.add_raw(
            u64::from(block_offset) * u64::from(block_size.get()),
            raw_blocks - block_offset,
        );
        if consumed == 0 {
            splits.push(builder.finish());
            builder = SplitBuilder::new(block_size, max_size, block_offset);
        } else {
            block_offset += consumed;
        }
    }
    splits.push(builder.finish());
    Ok(splits)
}
