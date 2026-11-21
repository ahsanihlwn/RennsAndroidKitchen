pub mod reader;
pub mod writer;

use std::cell::RefCell;
use std::fs::File;
use std::path::PathBuf;
use std::rc::Rc;

pub const SPARSE_HEADER_MAGIC: u32 = 0xED26_FF3A;
pub const SPARSE_HEADER_MAJOR_VER: u16 = 1;
pub const SPARSE_HEADER_MINOR_VER: u16 = 0;
pub const SPARSE_HEADER_LEN: u16 = 28;
pub const CHUNK_HEADER_LEN: u16 = 12;

pub const CHUNK_TYPE_RAW: u16 = 0xCAC1;
pub const CHUNK_TYPE_FILL: u16 = 0xCAC2;
pub const CHUNK_TYPE_DONT_CARE: u16 = 0xCAC3;
pub const CHUNK_TYPE_CRC32: u16 = 0xCAC4;

pub type SharedBuf = Rc<RefCell<Vec<u8>>>;

#[derive(Clone)]
pub enum BackedKind {
    Data { buf: SharedBuf, start: usize },
    File { path: PathBuf, offset: u64 },
    OpenFile { file: Rc<RefCell<File>>, offset: u64 },
    Fill(u32),
}

#[derive(Clone)]
pub struct BackedBlock {
    pub block: u32,
    pub len: u32,
    pub kind: BackedKind,
}

pub struct SparseFile {
    pub block_size: u32,
    pub len: i64,
    pub verbose: bool,
    pub blocks: Vec<BackedBlock>,
    sorted: bool,
}

impl SparseFile {
    pub fn new(block_size: u32, len: i64) -> Self {
        Self {
            block_size,
            len,
            verbose: false,
            blocks: Vec::new(),
            sorted: true,
        }
    }

    pub fn add_shared_data(&mut self, data: SharedBuf, len: u32, block: u32) {
        self.add_shared_data_slice(data, 0, len, block);
    }

    pub fn add_shared_data_slice(&mut self, buf: SharedBuf, start: usize, len: u32, block: u32) {
        self.push_block(BackedBlock {
            block,
            len,
            kind: BackedKind::Data { buf, start },
        });
    }

    pub fn add_owned_data(&mut self, data: Vec<u8>, block: u32) {
        let len = data.len() as u32;
        self.add_shared_data(Rc::new(RefCell::new(data)), len, block);
    }

    pub fn add_file(&mut self, path: PathBuf, offset: u64, len: u32, block: u32) {
        self.push_block(BackedBlock {
            block,
            len,
            kind: BackedKind::File { path, offset },
        });
    }

    pub fn add_open_file(&mut self, file: Rc<RefCell<File>>, offset: u64, len: u32, block: u32) {
        self.push_block(BackedBlock {
            block,
            len,
            kind: BackedKind::OpenFile { file, offset },
        });
    }

    pub fn add_fill(&mut self, fill_val: u32, len: u32, block: u32) {
        self.push_block(BackedBlock {
            block,
            len,
            kind: BackedKind::Fill(fill_val),
        });
    }

    #[inline]
    fn push_block(&mut self, bb: BackedBlock) {
        if let Some(last) = self.blocks.last() {
            if bb.block < last.block {
                self.sorted = false;
            } else if Self::can_merge(last, &bb, self.block_size) {
                let last_mut = self.blocks.last_mut().unwrap();
                last_mut.len += bb.len;
                return;
            }
        }
        self.blocks.push(bb);
    }

    fn can_merge(a: &BackedBlock, b: &BackedBlock, block_size: u32) -> bool {
        let block_len = a.len / block_size;
        if a.block + block_len != b.block {
            return false;
        }
        match (&a.kind, &b.kind) {
            (BackedKind::Fill(v1), BackedKind::Fill(v2)) => v1 == v2,
            (
                BackedKind::File { path: p1, offset: o1 },
                BackedKind::File { path: p2, offset: o2 },
            ) => p1 == p2 && o1 + a.len as u64 == *o2,
            (
                BackedKind::OpenFile { file: f1, offset: o1 },
                BackedKind::OpenFile { file: f2, offset: o2 },
            ) => Rc::ptr_eq(f1, f2) && o1 + a.len as u64 == *o2,
            _ => false,
        }
    }

    /// Sort blocks by start block and merge compatible adjacent blocks.
    pub fn finalize(&mut self) {
        if !self.sorted {
            self.blocks.sort_by_key(|b| b.block);
            self.sorted = true;
        }
        if self.blocks.len() <= 1 {
            return;
        }
        let block_size = self.block_size;
        let mut write_idx = 0usize;
        for read_idx in 1..self.blocks.len() {
            let can_merge = Self::can_merge(&self.blocks[write_idx], &self.blocks[read_idx], block_size);
            if can_merge {
                let add_len = self.blocks[read_idx].len;
                self.blocks[write_idx].len += add_len;
            } else {
                write_idx += 1;
                if write_idx != read_idx {
                    self.blocks.swap(write_idx, read_idx);
                }
            }
        }
        self.blocks.truncate(write_idx + 1);
    }

    pub fn count_chunks(&self) -> u32 {
        let mut last_block = 0u32;
        let mut chunks = 0u32;
        let bs = self.block_size;
        for bb in &self.blocks {
            if bb.block > last_block {
                chunks += 1;
            }
            chunks += 1;
            last_block = bb.block + (bb.len + bs - 1) / bs;
        }
        let total_blocks = ((self.len as u64 + bs as u64 - 1) / bs as u64) as u32;
        if last_block < total_blocks {
            chunks += 1;
        }
        chunks
    }
}
