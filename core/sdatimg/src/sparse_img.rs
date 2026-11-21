use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::Path;

use indexmap::IndexMap;
use sha1::{Digest, Sha1};

use crate::blockimgdiff::{BLOCK_SIZE, Image, hex_encode};
use crate::error::{Error, Result};
use crate::rangelib::RangeSet;

pub const SPARSE_HEADER_MAGIC: u32 = 0xED26FF3A;
pub const SPARSE_HEADER_SIZE: u16 = 28;
pub const CHUNK_HEADER_SIZE: u16 = 12;
pub const CHUNK_TYPE_RAW: u16 = 0xCAC1;
pub const CHUNK_TYPE_FILL: u16 = 0xCAC2;
pub const CHUNK_TYPE_DONT_CARE: u16 = 0xCAC3;
pub const CHUNK_TYPE_CRC32: u16 = 0xCAC4;

/// Maximum integer entries per `__NONZERO-*` group (2 entries per block = 512 blocks/group)
/// to bound per-transfer memory usage during OTA application.
const MAX_NONZERO_LIST_ENTRIES: usize = 1024;

#[derive(Debug, Clone)]
enum ChunkData {
    Raw { file_offset: u64 },
    Fill { fill_data: [u8; 4] },
}

#[derive(Debug, Clone)]
struct OffsetEntry {
    chunk_start: u64,
    chunk_len: u64,
    data: ChunkData,
}

/// Checks if a file begins with the Android sparse image magic (`0xED26FF3A` LE).
pub fn is_sparse_image(path: &Path) -> Result<bool> {
    let mut f = File::open(path)?;
    let mut buf = [0u8; 4];
    match f.read_exact(&mut buf) {
        Ok(()) => Ok(u32::from_le_bytes(buf) == SPARSE_HEADER_MAGIC),
        Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => Ok(false),
        Err(e) => Err(Error::Io(e)),
    }
}

/// Reader and block mapper for Android sparse images (`0xED26FF3A`).
pub struct SparseImage {
    file: File,
    pub blocksize: u64,
    pub total_blocks: u64,
    pub total_chunks: u32,
    pub care_map: RangeSet,
    pub extended: RangeSet,
    pub clobbered_blocks: RangeSet,
    offset_map: Vec<OffsetEntry>,
    offset_index: Vec<u64>,
    pub file_map: IndexMap<String, RangeSet>,
}

impl SparseImage {
    pub fn open(
        simg_path: &Path,
        file_map_path: Option<&Path>,
        clobbered_blocks: Option<&str>,
        build_map: bool,
        quiet: bool,
    ) -> Result<Self> {
        Self::open_with_mode(simg_path, file_map_path, clobbered_blocks, build_map, false, quiet)
    }

    pub fn open_rw(
        simg_path: &Path,
        file_map_path: Option<&Path>,
        clobbered_blocks: Option<&str>,
        build_map: bool,
        quiet: bool,
    ) -> Result<Self> {
        Self::open_with_mode(simg_path, file_map_path, clobbered_blocks, build_map, true, quiet)
    }

    fn open_with_mode(
        simg_path: &Path,
        file_map_path: Option<&Path>,
        clobbered_blocks: Option<&str>,
        build_map: bool,
        writable: bool,
        quiet: bool,
    ) -> Result<Self> {
        let mut f = if writable {
            OpenOptions::new().read(true).write(true).open(simg_path)?
        } else {
            File::open(simg_path)?
        };

        let mut header_bin = [0u8; 28];
        f.read_exact(&mut header_bin).map_err(|e| {
            if e.kind() == std::io::ErrorKind::UnexpectedEof {
                Error::InvalidSparseHeader("file too short for 28-byte sparse header".to_string())
            } else {
                Error::Io(e)
            }
        })?;

        let magic = u32::from_le_bytes(header_bin[0..4].try_into().unwrap());
        let major_version = u16::from_le_bytes(header_bin[4..6].try_into().unwrap());
        let minor_version = u16::from_le_bytes(header_bin[6..8].try_into().unwrap());
        let file_hdr_sz = u16::from_le_bytes(header_bin[8..10].try_into().unwrap());
        let chunk_hdr_sz = u16::from_le_bytes(header_bin[10..12].try_into().unwrap());
        let blk_sz = u32::from_le_bytes(header_bin[12..16].try_into().unwrap()) as u64;
        let total_blks = u32::from_le_bytes(header_bin[16..20].try_into().unwrap()) as u64;
        let total_chunks = u32::from_le_bytes(header_bin[20..24].try_into().unwrap());

        if magic != SPARSE_HEADER_MAGIC {
            return Err(Error::InvalidSparseHeader(format!(
                "expected sparse magic 0x{SPARSE_HEADER_MAGIC:08X}, found 0x{magic:08X}"
            )));
        }
        if major_version != 1 || minor_version != 0 {
            return Err(Error::InvalidSparseHeader(format!(
                "unsupported sparse image version {major_version}.{minor_version} (expected 1.0)"
            )));
        }
        if file_hdr_sz != SPARSE_HEADER_SIZE {
            return Err(Error::InvalidSparseHeader(format!(
                "unexpected file header size {file_hdr_sz} (expected 28)"
            )));
        }
        if chunk_hdr_sz != CHUNK_HEADER_SIZE {
            return Err(Error::InvalidSparseHeader(format!(
                "unexpected chunk header size {chunk_hdr_sz} (expected 12)"
            )));
        }
        if blk_sz == 0 || !blk_sz.is_multiple_of(4) {
            return Err(Error::InvalidSparseHeader(format!(
                "invalid block size {blk_sz}"
            )));
        }

        if !quiet {
            println!("Sparse image: {total_blks} blocks ({blk_sz} bytes/block) across {total_chunks} chunks");
        }

        let clobbered = match clobbered_blocks {
            Some(s) if !s.trim().is_empty() => RangeSet::parse(s)?,
            _ => RangeSet::new(),
        };

        if !build_map {
            return Ok(Self {
                file: f,
                blocksize: blk_sz,
                total_blocks: total_blks,
                total_chunks,
                care_map: RangeSet::new(),
                extended: RangeSet::new(),
                clobbered_blocks: clobbered,
                offset_map: Vec::new(),
                offset_index: Vec::new(),
                file_map: IndexMap::new(),
            });
        }

        let mut pos: u64 = 0;
        let mut care_data: Vec<u64> = Vec::new();
        let mut offset_map: Vec<OffsetEntry> = Vec::with_capacity(total_chunks as usize);

        for _ in 0..total_chunks {
            let mut chunk_hdr = [0u8; 12];
            f.read_exact(&mut chunk_hdr).map_err(|e| {
                if e.kind() == std::io::ErrorKind::UnexpectedEof {
                    Error::InvalidSparseChunk("unexpected EOF reading chunk header".to_string())
                } else {
                    Error::Io(e)
                }
            })?;

            let chunk_type = u16::from_le_bytes(chunk_hdr[0..2].try_into().unwrap());
            let chunk_sz = u32::from_le_bytes(chunk_hdr[4..8].try_into().unwrap()) as u64;
            let total_sz = u32::from_le_bytes(chunk_hdr[8..12].try_into().unwrap()) as u64;

            if total_sz < 12 {
                return Err(Error::InvalidSparseChunk(format!(
                    "chunk total_sz ({total_sz}) < 12"
                )));
            }
            let data_sz = total_sz - 12;

            match chunk_type {
                CHUNK_TYPE_RAW => {
                    let expected = chunk_sz.checked_mul(blk_sz).ok_or_else(|| {
                        Error::InvalidSparseChunk("raw chunk size overflow".to_string())
                    })?;
                    if data_sz != expected {
                        return Err(Error::InvalidSparseChunk(format!(
                            "Raw chunk input size ({data_sz}) does not match output size ({expected})"
                        )));
                    }
                    let file_offset = f.stream_position()?;
                    care_data.push(pos);
                    care_data.push(pos + chunk_sz);
                    offset_map.push(OffsetEntry {
                        chunk_start: pos,
                        chunk_len: chunk_sz,
                        data: ChunkData::Raw { file_offset },
                    });
                    pos += chunk_sz;
                    f.seek(SeekFrom::Current(data_sz as i64))?;
                }
                CHUNK_TYPE_FILL => {
                    if data_sz != 4 {
                        return Err(Error::InvalidSparseChunk(format!(
                            "Fill chunk input size is not 4 ({data_sz})"
                        )));
                    }
                    let mut fill_data = [0u8; 4];
                    f.read_exact(&mut fill_data)?;
                    care_data.push(pos);
                    care_data.push(pos + chunk_sz);
                    offset_map.push(OffsetEntry {
                        chunk_start: pos,
                        chunk_len: chunk_sz,
                        data: ChunkData::Fill { fill_data },
                    });
                    pos += chunk_sz;
                }
                CHUNK_TYPE_DONT_CARE => {
                    if data_sz != 0 {
                        return Err(Error::InvalidSparseChunk(format!(
                            "Don't care chunk input size is non-zero ({data_sz})"
                        )));
                    }
                    pos += chunk_sz;
                }
                CHUNK_TYPE_CRC32 => {
                    return Err(Error::UnsupportedCrc32Chunk);
                }
                other => {
                    return Err(Error::UnsupportedChunkType(other));
                }
            }
        }

        let care_map = RangeSet::from_data(care_data)?;
        let offset_index: Vec<u64> = offset_map.iter().map(|e| e.chunk_start).collect();

        // Explicitly zero up to 512 read-ahead blocks adjacent to care_map for dm-verity safety
        let all_blocks = RangeSet::from_range(0, total_blks);
        let extended = care_map
            .extend(512)
            .intersect(&all_blocks)
            .subtract(&care_map);

        let mut img = Self {
            file: f,
            blocksize: blk_sz,
            total_blocks: total_blks,
            total_chunks,
            care_map,
            extended,
            clobbered_blocks: clobbered,
            offset_map,
            offset_index,
            file_map: IndexMap::new(),
        };

        if let Some(map_path) = file_map_path {
            let clob = img.clobbered_blocks.clone();
            img.load_file_block_map(Some(map_path), &clob)?;
        } else {
            img.file_map.insert("__DATA".to_string(), img.care_map.clone());
        }

        Ok(img)
    }

    pub fn append_fill_chunk(&mut self, data: u32, blocks: u32) -> Result<()> {
        self.file.seek(SeekFrom::End(0))?;
        let mut rec = [0u8; 16];
        rec[0..2].copy_from_slice(&CHUNK_TYPE_FILL.to_le_bytes());
        rec[2..4].copy_from_slice(&0u16.to_le_bytes());
        rec[4..8].copy_from_slice(&blocks.to_le_bytes());
        rec[8..12].copy_from_slice(&16u32.to_le_bytes());
        rec[12..16].copy_from_slice(&data.to_le_bytes());
        self.file.write_all(&rec)?;

        self.total_blocks += blocks as u64;
        self.total_chunks += 1;

        self.file.seek(SeekFrom::Start(16))?;
        let mut hdr_patch = [0u8; 8];
        hdr_patch[0..4].copy_from_slice(&(self.total_blocks as u32).to_le_bytes());
        hdr_patch[4..8].copy_from_slice(&self.total_chunks.to_le_bytes());
        self.file.write_all(&hdr_patch)?;
        Ok(())
    }

    pub fn load_file_block_map(
        &mut self,
        file_map_path: Option<&Path>,
        clobbered_blocks: &RangeSet,
    ) -> Result<()> {
        let mut remaining = self.care_map.clone();
        let mut out: IndexMap<String, RangeSet> = IndexMap::new();

        if let Some(path) = file_map_path {
            let f = File::open(path)?;
            let reader = BufReader::new(f);
            for line_res in reader.lines() {
                let line = line_res?;
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                let mut parts = trimmed.splitn(2, char::is_whitespace);
                let name = parts.next().unwrap();
                let ranges_str = parts.next().unwrap_or("").trim();
                let ranges = RangeSet::parse(ranges_str)?;
                if ranges.size() != ranges.intersect(&remaining).size() {
                    return Err(Error::Invariant(format!(
                        "file_map entry '{name}' has blocks outside remaining care_map"
                    )));
                }
                if clobbered_blocks.overlaps(&ranges) {
                    return Err(Error::Invariant(format!(
                        "file_map entry '{name}' overlaps clobbered_blocks"
                    )));
                }
                remaining = remaining.subtract(&ranges);
                out.insert(name.to_string(), ranges);
            }
        }

        remaining = remaining.subtract(clobbered_blocks);

        let mut zero_blocks: Vec<u64> = Vec::new();
        let mut nonzero_blocks: Vec<u64> = Vec::new();
        let mut nonzero_groups: Vec<Vec<u64>> = Vec::new();

        let blk_sz = self.blocksize as usize;
        let max_buf_blocks = (4 * 1024 * 1024 / blk_sz).max(1);
        let mut buf = vec![0u8; max_buf_blocks * blk_sz];

        for (s, e) in &remaining {
            let mut cur = s;
            while cur < e {
                let idx = self.find_chunk_index(cur)?;
                let entry = &self.offset_map[idx];
                let chunk_end = entry.chunk_start + entry.chunk_len;
                let end_in_chunk = e.min(chunk_end);

                match entry.data {
                    ChunkData::Fill { fill_data } => {
                        let is_zero = fill_data == [0, 0, 0, 0];
                        for b in cur..end_in_chunk {
                            if is_zero {
                                zero_blocks.push(b);
                                zero_blocks.push(b + 1);
                            } else {
                                nonzero_blocks.push(b);
                                nonzero_blocks.push(b + 1);
                                if nonzero_blocks.len() >= MAX_NONZERO_LIST_ENTRIES {
                                    nonzero_groups.push(std::mem::take(&mut nonzero_blocks));
                                }
                            }
                        }
                    }
                    ChunkData::Raw { file_offset } => {
                        let start_off = file_offset + (cur - entry.chunk_start) * self.blocksize;
                        self.file.seek(SeekFrom::Start(start_off))?;

                        let mut b = cur;
                        while b < end_in_chunk {
                            let batch_blocks =
                                ((end_in_chunk - b) as usize).min(max_buf_blocks);
                            let byte_len = batch_blocks * blk_sz;
                            self.file.read_exact(&mut buf[..byte_len])?;
                            for k in 0..batch_blocks {
                                let slice = &buf[k * blk_sz..(k + 1) * blk_sz];
                                let blk_idx = b + (k as u64);
                                if slice.iter().all(|&byte| byte == 0) {
                                    zero_blocks.push(blk_idx);
                                    zero_blocks.push(blk_idx + 1);
                                } else {
                                    nonzero_blocks.push(blk_idx);
                                    nonzero_blocks.push(blk_idx + 1);
                                    if nonzero_blocks.len() >= MAX_NONZERO_LIST_ENTRIES {
                                        nonzero_groups.push(std::mem::take(&mut nonzero_blocks));
                                    }
                                }
                            }
                            b += batch_blocks as u64;
                        }
                    }
                }

                cur = end_in_chunk;
            }
        }

        if !nonzero_blocks.is_empty() {
            nonzero_groups.push(nonzero_blocks);
        }

        if !zero_blocks.is_empty() {
            out.insert("__ZERO".to_string(), RangeSet::from_data(zero_blocks)?);
        }
        for (i, blocks) in nonzero_groups.into_iter().enumerate() {
            out.insert(format!("__NONZERO-{i}"), RangeSet::from_data(blocks)?);
        }
        if !clobbered_blocks.is_empty() {
            out.insert("__COPY".to_string(), clobbered_blocks.clone());
        }

        self.clobbered_blocks = clobbered_blocks.clone();
        self.file_map = out;
        Ok(())
    }

    pub fn reset_file_map(&mut self) {
        self.file_map.clear();
        self.file_map
            .insert("__DATA".to_string(), self.care_map.clone());
    }

    fn find_chunk_index(&self, block: u64) -> Result<usize> {
        let pos = self.offset_index.partition_point(|&start| start <= block);
        if pos == 0 {
            return Err(Error::InvalidData(format!(
                "block {block} is not mapped in sparse image"
            )));
        }
        let idx = pos - 1;
        let entry = &self.offset_map[idx];
        if block >= entry.chunk_start + entry.chunk_len {
            return Err(Error::InvalidData(format!(
                "block {block} falls in DONT_CARE gap after chunk starting at {}",
                entry.chunk_start
            )));
        }
        Ok(idx)
    }

    /// Convert this sparse image into a raw image file (`simg2img`).
    pub fn to_raw_image(&mut self, output_path: &Path) -> Result<()> {
        let out_file = File::create(output_path)?;
        let mut writer = BufWriter::with_capacity(4 * 1024 * 1024, out_file);
        let total_bytes = self.total_blocks * self.blocksize;

        let blk_sz = self.blocksize as usize;
        let max_buf_blocks = (4 * 1024 * 1024 / blk_sz).max(1);
        let mut buf = vec![0u8; max_buf_blocks * blk_sz];
        let mut fill_buf = Vec::new();

        let entries = self.offset_map.clone();
        for entry in entries {
            let offset_bytes = entry.chunk_start * self.blocksize;
            writer.seek(SeekFrom::Start(offset_bytes))?;
            match entry.data {
                ChunkData::Raw { file_offset } => {
                    self.file.seek(SeekFrom::Start(file_offset))?;
                    let mut rem = entry.chunk_len as usize;
                    while rem > 0 {
                        let take = rem.min(max_buf_blocks);
                        let bytes = take * blk_sz;
                        self.file.read_exact(&mut buf[..bytes])?;
                        writer.write_all(&buf[..bytes])?;
                        rem -= take;
                    }
                }
                ChunkData::Fill { fill_data } => {
                    let pattern_per_block = blk_sz / 4;
                    let chunk_blocks = entry.chunk_len as usize;
                    let mut rem = chunk_blocks;
                    while rem > 0 {
                        let take = rem.min(max_buf_blocks);
                        fill_buf.clear();
                        fill_buf.reserve(take * blk_sz);
                        for _ in 0..(take * pattern_per_block) {
                            fill_buf.extend_from_slice(&fill_data);
                        }
                        writer.write_all(&fill_buf)?;
                        rem -= take;
                    }
                }
            }
        }

        writer.flush()?;
        let inner = writer.into_inner().map_err(|e| e.into_error())?;
        if inner.metadata()?.len() < total_bytes {
            inner.set_len(total_bytes)?;
        }
        Ok(())
    }
}

impl Image for SparseImage {
    fn blocksize(&self) -> u64 {
        self.blocksize
    }

    fn total_blocks(&self) -> u64 {
        self.total_blocks
    }

    fn care_map(&self) -> &RangeSet {
        &self.care_map
    }

    fn clobbered_blocks(&self) -> &RangeSet {
        &self.clobbered_blocks
    }

    fn extended(&self) -> &RangeSet {
        &self.extended
    }

    fn file_map(&self) -> &IndexMap<String, RangeSet> {
        &self.file_map
    }

    fn read_range_set(&mut self, ranges: &RangeSet) -> Result<Vec<Vec<u8>>> {
        let mut pieces = Vec::new();
        let blk_sz = self.blocksize as usize;

        for (s, e) in ranges {
            let mut to_read = e - s;
            let mut idx = self.find_chunk_index(s)?;
            {
                let entry = &self.offset_map[idx];
                let remain = entry.chunk_len - (s - entry.chunk_start);
                let this_read = remain.min(to_read);
                match entry.data {
                    ChunkData::Raw { file_offset } => {
                        let p = file_offset + (s - entry.chunk_start) * self.blocksize;
                        self.file.seek(SeekFrom::Start(p))?;
                        let mut buf = vec![0u8; (this_read as usize) * blk_sz];
                        self.file.read_exact(&mut buf)?;
                        pieces.push(buf);
                    }
                    ChunkData::Fill { fill_data } => {
                        let repeats = (this_read as usize) * (blk_sz / 4);
                        let mut buf = Vec::with_capacity(repeats * 4);
                        for _ in 0..repeats {
                            buf.extend_from_slice(&fill_data);
                        }
                        pieces.push(buf);
                    }
                }
                to_read -= this_read;
            }

            while to_read > 0 {
                idx += 1;
                let entry = self.offset_map.get(idx).ok_or_else(|| {
                    Error::InvalidData("range spans past end of sparse offset_map".to_string())
                })?;
                let this_read = entry.chunk_len.min(to_read);
                match entry.data {
                    ChunkData::Raw { file_offset } => {
                        self.file.seek(SeekFrom::Start(file_offset))?;
                        let mut buf = vec![0u8; (this_read as usize) * blk_sz];
                        self.file.read_exact(&mut buf)?;
                        pieces.push(buf);
                    }
                    ChunkData::Fill { fill_data } => {
                        let repeats = (this_read as usize) * (blk_sz / 4);
                        let mut buf = Vec::with_capacity(repeats * 4);
                        for _ in 0..repeats {
                            buf.extend_from_slice(&fill_data);
                        }
                        pieces.push(buf);
                    }
                }
                to_read -= this_read;
            }
        }

        Ok(pieces)
    }

    fn stream_range_set(
        &mut self,
        ranges: &RangeSet,
        cb: &mut dyn FnMut(&[u8]) -> Result<()>,
    ) -> Result<()> {
        let blk_sz = self.blocksize as usize;
        let max_buf_blocks = (4 * 1024 * 1024 / blk_sz).max(1);
        let mut buf = vec![0u8; max_buf_blocks * blk_sz];
        let mut fill_buf = Vec::new();

        for (s, e) in ranges {
            let mut cur = s;
            while cur < e {
                let idx = self.find_chunk_index(cur)?;
                let entry = self.offset_map[idx].clone();
                let chunk_end = entry.chunk_start + entry.chunk_len;
                let end_in_chunk = e.min(chunk_end);

                match entry.data {
                    ChunkData::Raw { file_offset } => {
                        let p = file_offset + (cur - entry.chunk_start) * self.blocksize;
                        self.file.seek(SeekFrom::Start(p))?;
                        let mut rem = (end_in_chunk - cur) as usize;
                        while rem > 0 {
                            let take = rem.min(max_buf_blocks);
                            let bytes = take * blk_sz;
                            self.file.read_exact(&mut buf[..bytes])?;
                            cb(&buf[..bytes])?;
                            rem -= take;
                        }
                    }
                    ChunkData::Fill { fill_data } => {
                        let mut rem = (end_in_chunk - cur) as usize;
                        while rem > 0 {
                            let take = rem.min(max_buf_blocks);
                            let repeats = take * (blk_sz / 4);
                            fill_buf.clear();
                            fill_buf.reserve(repeats * 4);
                            for _ in 0..repeats {
                                fill_buf.extend_from_slice(&fill_data);
                            }
                            cb(&fill_buf)?;
                            rem -= take;
                        }
                    }
                }

                cur = end_in_chunk;
            }
        }

        Ok(())
    }

    fn total_sha1(&mut self, include_clobbered_blocks: bool) -> Result<String> {
        let ranges = if include_clobbered_blocks {
            self.care_map.clone()
        } else {
            self.care_map.subtract(&self.clobbered_blocks)
        };
        let mut hasher = Sha1::new();
        self.stream_range_set(&ranges, &mut |piece| {
            hasher.update(piece);
            Ok(())
        })?;
        Ok(hex_encode(&hasher.finalize()))
    }
}

/// Streaming raw 4096-byte block image wrapper implementing `Image`.
/// Allows `img2sdat` to operate directly on raw images (such as those created by `sdat2img`).
pub struct RawImage {
    file: File,
    pub blocksize: u64,
    pub total_blocks: u64,
    pub care_map: RangeSet,
    pub extended: RangeSet,
    pub clobbered_blocks: RangeSet,
    pub file_map: IndexMap<String, RangeSet>,
}

impl RawImage {
    pub fn open(
        raw_path: &Path,
        file_map_path: Option<&Path>,
        clobbered_blocks: Option<&str>,
        quiet: bool,
    ) -> Result<Self> {
        let f = File::open(raw_path)?;
        let len = f.metadata()?.len();
        if len == 0 || len % BLOCK_SIZE != 0 {
            return Err(Error::InvalidData(format!(
                "Raw image size ({len} bytes) must be a non-zero multiple of {BLOCK_SIZE} bytes"
            )));
        }

        let total_blocks = len / BLOCK_SIZE;
        let care_map = RangeSet::from_range(0, total_blocks);
        let clobbered = match clobbered_blocks {
            Some(s) if !s.trim().is_empty() => RangeSet::parse(s)?.intersect(&care_map),
            _ => RangeSet::new(),
        };

        if !quiet {
            println!("Total of {total_blocks} {BLOCK_SIZE}-byte blocks in raw image.");
        }

        let mut img = Self {
            file: f,
            blocksize: BLOCK_SIZE,
            total_blocks,
            care_map,
            extended: RangeSet::new(),
            clobbered_blocks: clobbered.clone(),
            file_map: IndexMap::new(),
        };

        img.load_file_block_map(file_map_path, &clobbered)?;
        Ok(img)
    }

    fn load_file_block_map(
        &mut self,
        file_map_path: Option<&Path>,
        clobbered_blocks: &RangeSet,
    ) -> Result<()> {
        let mut remaining = self.care_map.clone();
        let mut out: IndexMap<String, RangeSet> = IndexMap::new();

        if let Some(path) = file_map_path {
            let f = File::open(path)?;
            let reader = BufReader::new(f);
            for line_res in reader.lines() {
                let line = line_res?;
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                let mut parts = trimmed.splitn(2, char::is_whitespace);
                let name = parts.next().unwrap();
                let ranges_str = parts.next().unwrap_or("").trim();
                let ranges = RangeSet::parse(ranges_str)?;
                if ranges.size() != ranges.intersect(&remaining).size() {
                    return Err(Error::Invariant(format!(
                        "file_map entry '{name}' has blocks outside remaining care_map"
                    )));
                }
                if clobbered_blocks.overlaps(&ranges) {
                    return Err(Error::Invariant(format!(
                        "file_map entry '{name}' overlaps clobbered_blocks"
                    )));
                }
                remaining = remaining.subtract(&ranges);
                out.insert(name.to_string(), ranges);
            }
        }

        remaining = remaining.subtract(clobbered_blocks);

        let mut zero_blocks: Vec<u64> = Vec::new();
        let mut nonzero_blocks: Vec<u64> = Vec::new();
        let mut nonzero_groups: Vec<Vec<u64>> = Vec::new();

        let blk_sz = self.blocksize as usize;
        let max_buf_blocks = (4 * 1024 * 1024 / blk_sz).max(1);
        let mut buf = vec![0u8; max_buf_blocks * blk_sz];

        for (s, e) in &remaining {
            self.file.seek(SeekFrom::Start(s * self.blocksize))?;
            let mut b = s;
            while b < e {
                let batch = ((e - b) as usize).min(max_buf_blocks);
                let byte_len = batch * blk_sz;
                self.file.read_exact(&mut buf[..byte_len])?;
                for k in 0..batch {
                    let slice = &buf[k * blk_sz..(k + 1) * blk_sz];
                    let blk_idx = b + (k as u64);
                    if slice.iter().all(|&byte| byte == 0) {
                        zero_blocks.push(blk_idx);
                        zero_blocks.push(blk_idx + 1);
                    } else {
                        nonzero_blocks.push(blk_idx);
                        nonzero_blocks.push(blk_idx + 1);
                        if nonzero_blocks.len() >= MAX_NONZERO_LIST_ENTRIES {
                            nonzero_groups.push(std::mem::take(&mut nonzero_blocks));
                        }
                    }
                }
                b += batch as u64;
            }
        }

        if !nonzero_blocks.is_empty() {
            nonzero_groups.push(nonzero_blocks);
        }

        if !zero_blocks.is_empty() {
            out.insert("__ZERO".to_string(), RangeSet::from_data(zero_blocks)?);
        }
        for (i, blocks) in nonzero_groups.into_iter().enumerate() {
            out.insert(format!("__NONZERO-{i}"), RangeSet::from_data(blocks)?);
        }
        if !clobbered_blocks.is_empty() {
            out.insert("__COPY".to_string(), clobbered_blocks.clone());
        }

        self.clobbered_blocks = clobbered_blocks.clone();
        self.file_map = out;
        Ok(())
    }
}

impl Image for RawImage {
    fn blocksize(&self) -> u64 {
        self.blocksize
    }

    fn total_blocks(&self) -> u64 {
        self.total_blocks
    }

    fn care_map(&self) -> &RangeSet {
        &self.care_map
    }

    fn clobbered_blocks(&self) -> &RangeSet {
        &self.clobbered_blocks
    }

    fn extended(&self) -> &RangeSet {
        &self.extended
    }

    fn file_map(&self) -> &IndexMap<String, RangeSet> {
        &self.file_map
    }

    fn read_range_set(&mut self, ranges: &RangeSet) -> Result<Vec<Vec<u8>>> {
        let mut pieces = Vec::new();
        let blk_sz = self.blocksize as usize;
        for (s, e) in ranges {
            self.file.seek(SeekFrom::Start(s * self.blocksize))?;
            let mut buf = vec![0u8; ((e - s) as usize) * blk_sz];
            self.file.read_exact(&mut buf)?;
            pieces.push(buf);
        }
        Ok(pieces)
    }

    fn stream_range_set(
        &mut self,
        ranges: &RangeSet,
        cb: &mut dyn FnMut(&[u8]) -> Result<()>,
    ) -> Result<()> {
        let blk_sz = self.blocksize as usize;
        let max_buf_blocks = (4 * 1024 * 1024 / blk_sz).max(1);
        let mut buf = vec![0u8; max_buf_blocks * blk_sz];

        for (s, e) in ranges {
            self.file.seek(SeekFrom::Start(s * self.blocksize))?;
            let mut rem = (e - s) as usize;
            while rem > 0 {
                let take = rem.min(max_buf_blocks);
                let bytes = take * blk_sz;
                self.file.read_exact(&mut buf[..bytes])?;
                cb(&buf[..bytes])?;
                rem -= take;
            }
        }
        Ok(())
    }

    fn total_sha1(&mut self, include_clobbered_blocks: bool) -> Result<String> {
        let ranges = if include_clobbered_blocks {
            self.care_map.clone()
        } else {
            self.care_map.subtract(&self.clobbered_blocks)
        };
        let mut hasher = Sha1::new();
        self.stream_range_set(&ranges, &mut |piece| {
            hasher.update(piece);
            Ok(())
        })?;
        Ok(hex_encode(&hasher.finalize()))
    }
}

/// Convert a raw 4096-byte-block image file into an Android sparse image (`img2simg`).
///
/// Zero blocks are encoded as `DONT_CARE` (`0xCAC3`), and contiguous non-zero
/// blocks (split at max 16384 blocks = 64 MiB per chunk) are encoded as `RAW` (`0xCAC1`).
pub fn raw_to_sparse(raw_path: &Path, sparse_path: &Path) -> Result<()> {
    let mut raw_file = File::open(raw_path)?;
    let len = raw_file.metadata()?.len();
    if len == 0 || len % BLOCK_SIZE != 0 {
        return Err(Error::InvalidData(format!(
            "Raw image size ({len} bytes) must be a non-zero multiple of {BLOCK_SIZE} bytes"
        )));
    }
    let total_blocks = len / BLOCK_SIZE;
    if total_blocks > u32::MAX as u64 {
        return Err(Error::InvalidData(
            "Image exceeds u32::MAX blocks for sparse format".to_string(),
        ));
    }

    let out_file = File::create(sparse_path)?;
    let mut writer = BufWriter::with_capacity(4 * 1024 * 1024, out_file);
    // Reserve 28 bytes for sparse header
    writer.write_all(&[0u8; 28])?;

    let blk_sz = BLOCK_SIZE as usize;
    let mut block_buf = vec![0u8; blk_sz];

    #[derive(Clone, Copy, PartialEq, Eq)]
    enum RunKind {
        DontCare,
        Raw,
    }

    const MAX_CHUNK_BLOCKS: u32 = 16384; // 64 MiB per RAW chunk

    let mut total_chunks: u32 = 0;
    let mut current_kind: Option<RunKind> = None;
    let mut run_blocks: u32 = 0;
    let mut raw_bytes: Vec<u8> = Vec::new();

    let flush_chunk = |writer: &mut BufWriter<File>,
                       kind: RunKind,
                       blocks: u32,
                       raw_data: &mut Vec<u8>,
                       total_chunks: &mut u32|
     -> Result<()> {
        if blocks == 0 {
            return Ok(());
        }
        let mut hdr = [0u8; 12];
        match kind {
            RunKind::DontCare => {
                hdr[0..2].copy_from_slice(&CHUNK_TYPE_DONT_CARE.to_le_bytes());
                hdr[4..8].copy_from_slice(&blocks.to_le_bytes());
                hdr[8..12].copy_from_slice(&12u32.to_le_bytes());
                writer.write_all(&hdr)?;
            }
            RunKind::Raw => {
                let total_sz = 12u32 + (blocks * BLOCK_SIZE as u32);
                hdr[0..2].copy_from_slice(&CHUNK_TYPE_RAW.to_le_bytes());
                hdr[4..8].copy_from_slice(&blocks.to_le_bytes());
                hdr[8..12].copy_from_slice(&total_sz.to_le_bytes());
                writer.write_all(&hdr)?;
                writer.write_all(raw_data)?;
                raw_data.clear();
            }
        }
        *total_chunks += 1;
        Ok(())
    };

    for _ in 0..total_blocks {
        raw_file.read_exact(&mut block_buf)?;
        let is_zero = block_buf.iter().all(|&b| b == 0);
        let kind = if is_zero {
            RunKind::DontCare
        } else {
            RunKind::Raw
        };

        if current_kind != Some(kind) || (kind == RunKind::Raw && run_blocks >= MAX_CHUNK_BLOCKS) {
            if let Some(prev) = current_kind {
                flush_chunk(&mut writer, prev, run_blocks, &mut raw_bytes, &mut total_chunks)?;
            }
            current_kind = Some(kind);
            run_blocks = 0;
        }

        if kind == RunKind::Raw {
            raw_bytes.extend_from_slice(&block_buf);
        }
        run_blocks += 1;
    }

    if let Some(prev) = current_kind {
        flush_chunk(&mut writer, prev, run_blocks, &mut raw_bytes, &mut total_chunks)?;
    }

    writer.flush()?;
    let mut inner = writer.into_inner().map_err(|e| e.into_error())?;
    inner.seek(SeekFrom::Start(0))?;

    let mut header = [0u8; 28];
    header[0..4].copy_from_slice(&SPARSE_HEADER_MAGIC.to_le_bytes());
    header[4..6].copy_from_slice(&1u16.to_le_bytes());
    header[6..8].copy_from_slice(&0u16.to_le_bytes());
    header[8..10].copy_from_slice(&SPARSE_HEADER_SIZE.to_le_bytes());
    header[10..12].copy_from_slice(&CHUNK_HEADER_SIZE.to_le_bytes());
    header[12..16].copy_from_slice(&(BLOCK_SIZE as u32).to_le_bytes());
    header[16..20].copy_from_slice(&(total_blocks as u32).to_le_bytes());
    header[20..24].copy_from_slice(&total_chunks.to_le_bytes());
    header[24..28].copy_from_slice(&0u32.to_le_bytes());
    inner.write_all(&header)?;
    inner.flush()?;
    Ok(())
}
