//! Android sparse and raw image writer.

use std::cell::RefCell;
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::rc::Rc;

use crate::crc32::sparse_crc32;

use super::format::{
    ChunkHeader, SparseError, SparseHeader, CHUNK_TYPE_CRC32, CHUNK_TYPE_DONT_CARE,
    CHUNK_TYPE_FILL, CHUNK_TYPE_RAW, SPARSE_CHUNK_HEADER_SIZE, SPARSE_HEADER_MAGIC,
    SPARSE_HEADER_MAJOR_VER, SPARSE_HEADER_MINOR_VER, SPARSE_HEADER_SIZE,
};
use super::reader::ImageSource;

const MAX_BACKED_BLOCK_SIZE: u64 = 64 << 20; // 64 MiB
const STREAM_BUF_SIZE: usize = 1024 * 1024;

#[derive(Clone)]
pub enum BackedBlockKind {
    Data(Vec<u8>),
    Fill(u32),
    Source {
        source: Rc<RefCell<ImageSource<File>>>,
        offset: u64,
    },
}

#[derive(Clone)]
struct BackedBlock {
    block: u32,
    len: u64,
    kind: BackedBlockKind,
}

/// In-memory sparse file builder supporting raw data, fill blocks, and
/// streamed file/sparse sources with automatic adjacent-block merging.
pub struct SparseFile {
    block_size: u32,
    len: u64,
    blocks: Vec<BackedBlock>,
}

impl SparseFile {
    pub fn new(block_size: u32, len: u64) -> Result<Self, SparseError> {
        if block_size == 0 || block_size % 4 != 0 {
            return Err(SparseError::InvalidFormat(format!(
                "invalid sparse block size {block_size}"
            )));
        }
        Ok(Self {
            block_size,
            len,
            blocks: Vec::new(),
        })
    }

    pub fn block_size(&self) -> u32 {
        self.block_size
    }

    pub fn len(&self) -> u64 {
        self.len
    }

    pub fn add_data(&mut self, data: Vec<u8>, block: u32) {
        if data.is_empty() {
            return;
        }
        let len = data.len() as u64;
        self.insert_block(BackedBlock {
            block,
            len,
            kind: BackedBlockKind::Data(data),
        });
    }

    pub fn add_fill(&mut self, fill_val: u32, len: u64, block: u32) {
        if len == 0 {
            return;
        }
        self.insert_block(BackedBlock {
            block,
            len,
            kind: BackedBlockKind::Fill(fill_val),
        });
    }

    pub fn add_source(
        &mut self,
        source: Rc<RefCell<ImageSource<File>>>,
        offset: u64,
        len: u64,
        block: u32,
    ) {
        if len == 0 {
            return;
        }
        self.insert_block(BackedBlock {
            block,
            len,
            kind: BackedBlockKind::Source { source, offset },
        });
    }

    fn insert_block(&mut self, bb: BackedBlock) {
        // Fast path: sequential append (common case when building super images).
        if let Some(last) = self.blocks.last_mut() {
            if last.block <= bb.block {
                if Self::try_merge(last, &bb, self.block_size as u64) {
                    return;
                }
                if last.block < bb.block {
                    self.blocks.push(bb);
                    return;
                }
            }
        }

        let pos = self.blocks.partition_point(|existing| existing.block < bb.block);
        self.blocks.insert(pos, bb);

        if pos + 1 < self.blocks.len() {
            let next = self.blocks[pos + 1].clone();
            if Self::try_merge(&mut self.blocks[pos], &next, self.block_size as u64) {
                self.blocks.remove(pos + 1);
            }
        }
        if pos > 0 {
            let cur = self.blocks[pos].clone();
            if Self::try_merge(&mut self.blocks[pos - 1], &cur, self.block_size as u64) {
                self.blocks.remove(pos);
            }
        }
    }

    fn try_merge(a: &mut BackedBlock, b: &BackedBlock, block_size: u64) -> bool {
        if a.block >= b.block {
            return false;
        }
        if a.len % block_size != 0 {
            return false;
        }
        let a_blocks = a.len / block_size;
        if a.block as u64 + a_blocks != b.block as u64 {
            return false;
        }
        match (&mut a.kind, &b.kind) {
            (BackedBlockKind::Fill(va), BackedBlockKind::Fill(vb)) if va == vb => {
                a.len += b.len;
                true
            }
            (
                BackedBlockKind::Source {
                    source: sa,
                    offset: oa,
                },
                BackedBlockKind::Source {
                    source: sb,
                    offset: ob,
                },
            ) if Rc::ptr_eq(sa, sb) && *oa + a.len == *ob => {
                a.len += b.len;
                true
            }
            _ => false,
        }
    }

    fn split_large_blocks(&mut self) {
        let max_len = (MAX_BACKED_BLOCK_SIZE / self.block_size as u64) * self.block_size as u64;
        if max_len == 0 {
            return;
        }
        let mut expanded = Vec::with_capacity(self.blocks.len());
        for bb in self.blocks.drain(..) {
            if bb.len <= max_len {
                expanded.push(bb);
                continue;
            }
            let mut rem = bb.len;
            let mut cur_block = bb.block;
            let mut cur_off: u64 = 0;
            while rem > 0 {
                let piece = rem.min(max_len);
                let kind = match &bb.kind {
                    BackedBlockKind::Data(data) => {
                        let start = cur_off as usize;
                        let end = start + piece as usize;
                        BackedBlockKind::Data(data[start..end].to_vec())
                    }
                    BackedBlockKind::Fill(val) => BackedBlockKind::Fill(*val),
                    BackedBlockKind::Source { source, offset } => BackedBlockKind::Source {
                        source: Rc::clone(source),
                        offset: *offset + cur_off,
                    },
                };
                expanded.push(BackedBlock {
                    block: cur_block,
                    len: piece,
                    kind,
                });
                cur_block += (piece / self.block_size as u64) as u32;
                cur_off += piece;
                rem -= piece;
            }
        }
        self.blocks = expanded;
    }

    pub fn count_chunks(&self) -> u32 {
        let blk_sz = self.block_size as u64;
        let mut last_block: u64 = 0;
        let mut chunks: u32 = 0;
        for bb in &self.blocks {
            if bb.block as u64 > last_block {
                chunks += 1;
            }
            chunks += 1;
            last_block = bb.block as u64 + bb.len.div_ceil(blk_sz);
        }
        let total_blocks = self.len.div_ceil(blk_sz);
        if last_block < total_blocks {
            chunks += 1;
        }
        chunks
    }

    /// Write to a `File` in either Android sparse format (`sparsify = true`)
    /// or raw format (`sparsify = false`).
    pub fn write_to_file(
        &mut self,
        file: &mut File,
        sparsify: bool,
        use_crc: bool,
    ) -> Result<(), SparseError> {
        if sparsify {
            self.write_sparse(file, use_crc)
        } else {
            self.write_raw_file(file)
        }
    }

    /// Write Android sparse format to any `Write` stream.
    pub fn write_sparse<W: Write>(
        &mut self,
        out: &mut W,
        use_crc: bool,
    ) -> Result<(), SparseError> {
        self.split_large_blocks();

        let blk_sz = self.block_size as u64;
        let total_blks = self.len.div_ceil(blk_sz);
        if total_blks > u32::MAX as u64 {
            return Err(SparseError::InvalidFormat(
                "image exceeds 32-bit sparse block limit".into(),
            ));
        }

        let mut total_chunks = self.count_chunks();
        if use_crc {
            total_chunks += 1;
        }

        let header = SparseHeader {
            magic: SPARSE_HEADER_MAGIC,
            major_version: SPARSE_HEADER_MAJOR_VER,
            minor_version: SPARSE_HEADER_MINOR_VER,
            file_hdr_sz: SPARSE_HEADER_SIZE as u16,
            chunk_hdr_sz: SPARSE_CHUNK_HEADER_SIZE as u16,
            blk_sz: self.block_size,
            total_blks: total_blks as u32,
            total_chunks,
            image_checksum: 0,
        };
        out.write_all(&header.to_bytes())?;

        let zero_block = vec![0u8; self.block_size as usize];
        let mut copy_buf = vec![0u8; STREAM_BUF_SIZE];
        let mut crc32: u32 = 0;
        let mut last_block: u64 = 0;

        for bb in &self.blocks {
            if bb.block as u64 > last_block {
                let skip_blocks = bb.block as u64 - last_block;
                write_skip_chunk(out, skip_blocks, blk_sz, use_crc, &mut crc32, &zero_block)?;
            }

            let rnd_up_len = bb.len.div_ceil(blk_sz) * blk_sz;
            let chunk_blocks = (rnd_up_len / blk_sz) as u32;
            let zero_pad = (rnd_up_len - bb.len) as usize;

            match &bb.kind {
                BackedBlockKind::Data(data) => {
                    let ch = ChunkHeader {
                        chunk_type: CHUNK_TYPE_RAW,
                        reserved1: 0,
                        chunk_sz: chunk_blocks,
                        total_sz: (SPARSE_CHUNK_HEADER_SIZE as u64 + rnd_up_len) as u32,
                    };
                    out.write_all(&ch.to_bytes())?;
                    out.write_all(data)?;
                    if zero_pad > 0 {
                        out.write_all(&zero_block[..zero_pad])?;
                    }
                    if use_crc {
                        crc32 = sparse_crc32(crc32, data);
                        if zero_pad > 0 {
                            crc32 = sparse_crc32(crc32, &zero_block[..zero_pad]);
                        }
                    }
                }
                BackedBlockKind::Fill(val) => {
                    let ch = ChunkHeader {
                        chunk_type: CHUNK_TYPE_FILL,
                        reserved1: 0,
                        chunk_sz: chunk_blocks,
                        total_sz: (SPARSE_CHUNK_HEADER_SIZE + 4) as u32,
                    };
                    out.write_all(&ch.to_bytes())?;
                    let val_bytes = val.to_le_bytes();
                    out.write_all(&val_bytes)?;
                    if use_crc {
                        let word_count = rnd_up_len / 4;
                        for _ in 0..word_count {
                            crc32 = sparse_crc32(crc32, &val_bytes);
                        }
                    }
                }
                BackedBlockKind::Source { source, offset } => {
                    let ch = ChunkHeader {
                        chunk_type: CHUNK_TYPE_RAW,
                        reserved1: 0,
                        chunk_sz: chunk_blocks,
                        total_sz: (SPARSE_CHUNK_HEADER_SIZE as u64 + rnd_up_len) as u32,
                    };
                    out.write_all(&ch.to_bytes())?;
                    let mut src = source.borrow_mut();
                    src.seek(SeekFrom::Start(*offset))?;
                    let mut rem = bb.len;
                    while rem > 0 {
                        let step = (rem as usize).min(copy_buf.len());
                        src.read_exact(&mut copy_buf[..step])?;
                        out.write_all(&copy_buf[..step])?;
                        if use_crc {
                            crc32 = sparse_crc32(crc32, &copy_buf[..step]);
                        }
                        rem -= step as u64;
                    }
                    if zero_pad > 0 {
                        out.write_all(&zero_block[..zero_pad])?;
                        if use_crc {
                            crc32 = sparse_crc32(crc32, &zero_block[..zero_pad]);
                        }
                    }
                }
            }

            last_block = bb.block as u64 + chunk_blocks as u64;
        }

        if last_block < total_blks {
            let skip_blocks = total_blks - last_block;
            write_skip_chunk(out, skip_blocks, blk_sz, use_crc, &mut crc32, &zero_block)?;
        }

        if use_crc {
            let ch = ChunkHeader {
                chunk_type: CHUNK_TYPE_CRC32,
                reserved1: 0,
                chunk_sz: 0,
                total_sz: (SPARSE_CHUNK_HEADER_SIZE + 4) as u32,
            };
            out.write_all(&ch.to_bytes())?;
            out.write_all(&crc32.to_le_bytes())?;
        }

        Ok(())
    }

    fn write_raw_file(&mut self, file: &mut File) -> Result<(), SparseError> {
        let blk_sz = self.block_size as u64;
        let mut copy_buf = vec![0u8; STREAM_BUF_SIZE];
        let mut fill_buf = vec![0u8; self.block_size as usize];
        let mut last_block: u64 = 0;

        for bb in &self.blocks {
            if bb.block as u64 > last_block {
                let skip_bytes = (bb.block as u64 - last_block) * blk_sz;
                file.seek(SeekFrom::Current(skip_bytes as i64))?;
            }

            let rnd_up_len = bb.len.div_ceil(blk_sz) * blk_sz;
            let pad = rnd_up_len - bb.len;

            match &bb.kind {
                BackedBlockKind::Data(data) => {
                    file.write_all(data)?;
                    if pad > 0 {
                        file.seek(SeekFrom::Current(pad as i64))?;
                    }
                }
                BackedBlockKind::Fill(val) => {
                    let val_bytes = val.to_le_bytes();
                    for slot in fill_buf.chunks_exact_mut(4) {
                        slot.copy_from_slice(&val_bytes);
                    }
                    let mut rem = bb.len;
                    while rem > 0 {
                        let step = (rem as usize).min(fill_buf.len());
                        file.write_all(&fill_buf[..step])?;
                        rem -= step as u64;
                    }
                    if pad > 0 {
                        file.seek(SeekFrom::Current(pad as i64))?;
                    }
                }
                BackedBlockKind::Source { source, offset } => {
                    let mut src = source.borrow_mut();
                    src.seek(SeekFrom::Start(*offset))?;
                    let mut rem = bb.len;
                    while rem > 0 {
                        let step = (rem as usize).min(copy_buf.len());
                        src.read_exact(&mut copy_buf[..step])?;
                        file.write_all(&copy_buf[..step])?;
                        rem -= step as u64;
                    }
                    if pad > 0 {
                        file.seek(SeekFrom::Current(pad as i64))?;
                    }
                }
            }

            last_block = bb.block as u64 + (rnd_up_len / blk_sz);
        }

        file.set_len(self.len)?;
        Ok(())
    }
}

fn write_skip_chunk<W: Write>(
    out: &mut W,
    skip_blocks: u64,
    blk_sz: u64,
    use_crc: bool,
    crc32: &mut u32,
    zero_block: &[u8],
) -> io::Result<()> {
    let ch = ChunkHeader {
        chunk_type: CHUNK_TYPE_DONT_CARE,
        reserved1: 0,
        chunk_sz: skip_blocks as u32,
        total_sz: SPARSE_CHUNK_HEADER_SIZE as u32,
    };
    out.write_all(&ch.to_bytes())?;
    if use_crc {
        let mut rem = skip_blocks * blk_sz;
        while rem > 0 {
            let step = (rem as usize).min(zero_block.len());
            *crc32 = sparse_crc32(*crc32, &zero_block[..step]);
            rem -= step as u64;
        }
    }
    Ok(())
}
