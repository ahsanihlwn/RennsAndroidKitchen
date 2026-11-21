// SPDX-License-Identifier: MIT

//! High-level read-only EROFS image parser, directory walker, file reader, and metadata inspector.

use crate::avb::{AvbFooter, read_avb_footer};
use crate::compress::{DecompressRequest, decompress_pcluster};
use crate::crc32c::erofs_superblock_crc;
use crate::error::{ErofsError, Result};
use crate::format::{
    CHUNK_FORMAT_48BIT, CHUNK_FORMAT_BLKBITS_MASK, CHUNK_FORMAT_INDEXES, COMPR_ALG_DEFLATE,
    COMPR_ALG_LZ4, COMPR_ALG_LZMA, COMPR_ALG_ZSTD, DataLayout, DirEntry, EROFS_DIRENT_SIZE,
    EROFS_NID_METABOX_BIT, EROFS_NULL_ADDR_32, EROFS_NULL_ADDR_48, EROFS_SUPER_MAX_SIZE,
    EROFS_SUPER_OFFSET, FT_DIR, FT_SYMLINK, Inode, S_IFDIR, S_IFLNK, S_IFMT, SuperBlock,
    align_up, div_round_up, ftype_label, mode_to_string, read_u16_le, read_u32_le, read_u64_le,
};
use crate::io::ImageSource;
use crate::xattr::{
    LongXattrPrefix, XATTR_NAME_CAPABILITY, XATTR_NAME_SELINUX, XattrEntry,
    decode_raw_xattr_entry, parse_vfs_capability, resolve_xattr_name,
};
use crate::zmap::map_all_extents;
use serde::Serialize;
use std::collections::HashSet;
use std::fs::File;
use std::path::Path;
use std::sync::OnceLock;

/// Compression config details parsed after the superblock when `COMPR_CFGS` is enabled.
#[derive(Debug, Clone, Default, Serialize)]
pub struct CompressionConfigs {
    pub lz4_max_distance: Option<u16>,
    pub lz4_max_pclusterblks: Option<u16>,
    pub lzma_dict_size: Option<u32>,
    pub deflate_windowbits: Option<u8>,
    pub zstd_windowlog: Option<u8>,
}

/// Complete high-level entry emitted by `walk()` and `list`.
#[derive(Debug, Clone, Serialize)]
pub struct FileNode {
    pub path: String,
    pub nid: u64,
    pub ino: u32,
    pub is_dir: bool,
    pub is_link: bool,
    pub file_type: String,
    pub layout: String,
    pub size: u64,
    pub mode: u16,
    pub mode_octal: String,
    pub perm: String,
    pub uid: u32,
    pub gid: u32,
    pub nlink: u32,
    pub mtime: u64,
    pub target: Option<String>,
    pub selinux: Option<String>,
    pub capabilities: Option<u64>,
}

/// Superblock & container summary emitted by `info`.
#[derive(Debug, Clone, Serialize)]
pub struct ImageSummary {
    pub block_size: u32,
    pub total_blocks: u64,
    pub image_size: u64,
    pub inos: u64,
    pub root_nid: u64,
    pub meta_blkaddr: u32,
    pub xattr_blkaddr: u32,
    pub packed_nid: u64,
    pub metabox_nid: u64,
    pub uuid: String,
    pub volume_name: String,
    pub build_time: u64,
    pub compat_features: Vec<String>,
    pub incompat_features: Vec<String>,
    pub compression_algorithms: Vec<String>,
    pub compr_configs: CompressionConfigs,
    pub is_sparse: bool,
    pub crc_present: bool,
    pub crc_stored: Option<u32>,
    pub crc_computed: Option<u32>,
    pub crc_valid: Option<bool>,
    pub avb_footer: Option<AvbFooter>,
    pub has_xattr_inode_digest: bool,
    pub ishare_xattr_name: Option<String>,
}

/// Thread-safe read-only EROFS image handle.
pub struct ErofsImage {
    pub source: ImageSource,
    pub sb: SuperBlock,
    pub compr_cfgs: CompressionConfigs,
    pub long_prefixes: Vec<LongXattrPrefix>,
    pub avb_footer: Option<AvbFooter>,
    pub crc_computed: Option<u32>,
    metabox_blob: OnceLock<Vec<u8>>,
    packed_blob: OnceLock<Vec<u8>>,
}

impl ErofsImage {
    pub fn open(path: &Path, offset: u64) -> Result<Self> {
        let source = ImageSource::open(path, offset)?;
        let avb_footer = if !source.is_sparse() && offset == 0 {
            File::open(path)
                .ok()
                .and_then(|mut f| read_avb_footer(&mut f).ok().flatten())
        } else {
            None
        };
        Self::init_from_source(source, avb_footer)
    }

    /// Open an EROFS image directly from an in-memory buffer.
    pub fn open_bytes(data: Vec<u8>) -> Result<Self> {
        let source = ImageSource::from_bytes(data);
        Self::init_from_source(source, None)
    }

    fn init_from_source(source: ImageSource, avb_footer: Option<AvbFooter>) -> Result<Self> {
        let sb_raw = source.read_vec(EROFS_SUPER_OFFSET, EROFS_SUPER_MAX_SIZE)?;
        let sb = SuperBlock::parse(&sb_raw)?;

        // Compute CRC32C if enabled
        let crc_computed = if sb.has_sb_chksum() {
            let bs = sb.block_size as u64;
            let len = if bs > EROFS_SUPER_OFFSET {
                (bs - EROFS_SUPER_OFFSET) as usize
            } else {
                bs as usize
            };
            let mut region = source.read_vec(EROFS_SUPER_OFFSET, len)?;
            if region.len() < len {
                region.resize(len, 0);
            }
            Some(erofs_superblock_crc(&region))
        } else {
            None
        };

        let compr_cfgs = parse_compr_cfgs(&source, &sb)?;

        let mut img = Self {
            source,
            sb,
            compr_cfgs,
            long_prefixes: Vec::new(),
            avb_footer,
            crc_computed,
            metabox_blob: OnceLock::new(),
            packed_blob: OnceLock::new(),
        };

        img.long_prefixes = img.load_long_xattr_prefixes()?;
        Ok(img)
    }

    pub fn summary(&self) -> ImageSummary {
        let crc_present = self.sb.has_sb_chksum();
        let crc_stored = crc_present.then_some(self.sb.checksum);
        let crc_valid = match (crc_stored, self.crc_computed) {
            (Some(a), Some(b)) => Some(a == b),
            _ => None,
        };
        let ishare_name = self.ishare_xattr_name();

        ImageSummary {
            block_size: self.sb.block_size,
            total_blocks: self.sb.blocks,
            image_size: self.source.len(),
            inos: self.sb.inos,
            root_nid: self.sb.root_nid,
            meta_blkaddr: self.sb.meta_blkaddr,
            xattr_blkaddr: self.sb.xattr_blkaddr,
            packed_nid: self.sb.packed_nid,
            metabox_nid: self.sb.metabox_nid,
            uuid: self.sb.uuid_string(),
            volume_name: self.sb.volume_name.clone(),
            build_time: self.sb.build_timestamp(),
            compat_features: self
                .sb
                .compat_feature_names()
                .into_iter()
                .map(String::from)
                .collect(),
            incompat_features: self
                .sb
                .incompat_feature_names()
                .into_iter()
                .map(String::from)
                .collect(),
            compression_algorithms: self
                .sb
                .algorithm_names()
                .into_iter()
                .map(String::from)
                .collect(),
            compr_configs: self.compr_cfgs.clone(),
            is_sparse: self.source.is_sparse(),
            crc_present,
            crc_stored,
            crc_computed: self.crc_computed,
            crc_valid,
            avb_footer: self.avb_footer.clone(),
            has_xattr_inode_digest: ishare_name.is_some(),
            ishare_xattr_name: ishare_name,
        }
    }

    /// Return the full xattr name used for `ishare` per-inode SHA-256 digests, if enabled.
    pub fn ishare_xattr_name(&self) -> Option<String> {
        if !self.sb.has_ishare_xattrs() {
            return None;
        }
        let idx = self.sb.ishare_xattr_prefix_id as usize;
        let lp = self.long_prefixes.get(idx)?;
        let base = crate::xattr::base_prefix_str(lp.base_index)?;
        Some(format!("{base}{}", lp.infix))
    }

    fn get_metabox_blob(&self) -> Result<&[u8]> {
        if !self.sb.has_metabox() || self.sb.metabox_nid == 0 {
            return Err(ErofsError::InvalidFormat(
                "metabox nid referenced but METABOX feature is inactive".into(),
            ));
        }
        if let Some(b) = self.metabox_blob.get() {
            return Ok(b.as_slice());
        }
        let inode = self.read_inode(self.sb.metabox_nid)?;
        let data = self.read_inode_bytes(&inode)?;
        let _ = self.metabox_blob.set(data);
        Ok(self.metabox_blob.get().unwrap().as_slice())
    }

    fn get_packed_blob(&self) -> Result<&[u8]> {
        if self.sb.packed_nid == 0 {
            return Err(ErofsError::InvalidFormat(
                "fragment referenced but packed_nid is 0".into(),
            ));
        }
        if let Some(b) = self.packed_blob.get() {
            return Ok(b.as_slice());
        }
        let inode = self.read_inode(self.sb.packed_nid)?;
        let data = self.read_inode_bytes(&inode)?;
        let _ = self.packed_blob.set(data);
        Ok(self.packed_blob.get().unwrap().as_slice())
    }

    fn read_meta_space(&self, in_metabox: bool, off: u64, len: usize) -> Result<Vec<u8>> {
        if in_metabox {
            let blob = self.get_metabox_blob()?;
            let start = off as usize;
            if start >= blob.len() {
                return Ok(Vec::new());
            }
            let end = (start + len).min(blob.len());
            Ok(blob[start..end].to_vec())
        } else {
            self.source.read_vec(off, len)
        }
    }

    fn load_long_xattr_prefixes(&self) -> Result<Vec<LongXattrPrefix>> {
        if !self.sb.has_xattr_prefixes() || self.sb.xattr_prefix_count == 0 {
            return Ok(Vec::new());
        }
        let count = self.sb.xattr_prefix_count as usize;
        let use_plain = self.sb.has_plain_xattr_pfx()
            || (!self.sb.has_metabox() && self.sb.packed_nid == 0);

        let mut pos = (self.sb.xattr_prefix_start as u64) * 4;
        let mut prefixes = Vec::with_capacity(count);

        for _ in 0..count {
            let len_buf = if use_plain {
                let abs = (self.sb.meta_blkaddr as u64) * (self.sb.block_size as u64) + pos;
                self.source.read_exact_vec(abs, 2)?
            } else if self.sb.has_metabox() && self.sb.metabox_nid != 0 {
                let blob = self.get_metabox_blob()?;
                let p = pos as usize;
                if p + 2 > blob.len() {
                    break;
                }
                blob[p..p + 2].to_vec()
            } else {
                let blob = self.get_packed_blob()?;
                let p = pos as usize;
                if p + 2 > blob.len() {
                    break;
                }
                blob[p..p + 2].to_vec()
            };

            let item_len = read_u16_le(&len_buf, 0) as usize;
            pos += 2;
            if item_len == 0 {
                break;
            }
            let payload = if use_plain {
                let abs = (self.sb.meta_blkaddr as u64) * (self.sb.block_size as u64) + pos;
                self.source.read_exact_vec(abs, item_len)?
            } else if self.sb.has_metabox() && self.sb.metabox_nid != 0 {
                let blob = self.get_metabox_blob()?;
                let p = pos as usize;
                blob[p..(p + item_len).min(blob.len())].to_vec()
            } else {
                let blob = self.get_packed_blob()?;
                let p = pos as usize;
                blob[p..(p + item_len).min(blob.len())].to_vec()
            };
            pos = align_up(pos + item_len as u64, 4);

            if !payload.is_empty() {
                let base_index = payload[0];
                let infix = String::from_utf8_lossy(&payload[1..]).into_owned();
                prefixes.push(LongXattrPrefix { base_index, infix });
            }
        }
        Ok(prefixes)
    }

    pub fn read_inode(&self, nid: u64) -> Result<Inode> {
        let in_metabox = (nid & EROFS_NID_METABOX_BIT) != 0;
        let off = if in_metabox {
            (nid & crate::format::EROFS_NID_MASK) * crate::format::EROFS_ISLOT_SIZE
        } else {
            self.sb.nid_to_offset(nid)
        };
        let hdr = self.read_meta_space(in_metabox, off, 64)?;
        Inode::parse(&self.sb, nid, &hdr)
    }

    /// Read all extended attributes (both shared and inline) belonging to `inode`.
    pub fn read_xattrs(&self, inode: &Inode) -> Result<Vec<XattrEntry>> {
        if inode.xattr_icount == 0 || inode.xattr_size < 12 {
            return Ok(Vec::new());
        }
        let area = self.read_meta_space(
            inode.in_metabox,
            inode.xattr_offset,
            inode.xattr_size as usize,
        )?;
        if area.len() < 12 {
            return Ok(Vec::new());
        }

        let shared_count = area[4] as usize;
        let mut out = Vec::new();

        // 1. Shared xattrs
        let shared_in_metabox = self.sb.has_shared_ea_in_metabox();
        for i in 0..shared_count {
            let pos = 12 + i * 4;
            if pos + 4 > area.len() {
                break;
            }
            let idx = read_u32_le(&area, pos) as u64;
            let raw_entry = if shared_in_metabox {
                let blob = self.get_metabox_blob()?;
                let off = (idx * 4) as usize;
                if off + 4 > blob.len() {
                    continue;
                }
                let nlen = blob[off] as usize;
                let vlen = read_u16_le(blob, off + 2) as usize;
                let end = (off + 4 + nlen + vlen).min(blob.len());
                blob[off..end].to_vec()
            } else {
                let off =
                    (self.sb.xattr_blkaddr as u64) * (self.sb.block_size as u64) + idx * 4;
                let hdr = self.source.read_vec(off, 4)?;
                if hdr.len() < 4 {
                    continue;
                }
                let nlen = hdr[0] as usize;
                let vlen = read_u16_le(&hdr, 2) as usize;
                self.source.read_vec(off, 4 + nlen + vlen)?
            };

            if let Some((nidx, name, val, _)) = decode_raw_xattr_entry(&raw_entry, 0) {
                if let Some(full) = resolve_xattr_name(nidx, name, &self.long_prefixes) {
                    out.push(XattrEntry {
                        name: full,
                        value: val.to_vec(),
                        shared: true,
                    });
                }
            }
        }

        // 2. Inline xattrs
        let mut pos = 12 + shared_count * 4;
        while pos + 4 <= area.len() {
            let Some((nidx, name, val, next)) = decode_raw_xattr_entry(&area, pos) else {
                break;
            };
            if let Some(full) = resolve_xattr_name(nidx, name, &self.long_prefixes) {
                out.push(XattrEntry {
                    name: full,
                    value: val.to_vec(),
                    shared: false,
                });
            }
            if next <= pos {
                break;
            }
            pos = next;
        }

        Ok(out)
    }

    /// Read raw file/directory/symlink payload bytes for `inode`.
    pub fn read_inode_bytes(&self, inode: &Inode) -> Result<Vec<u8>> {
        let size = inode.size as usize;
        if size == 0 {
            return Ok(Vec::new());
        }
        let bs = self.sb.block_size as u64;

        match inode.layout {
            DataLayout::FlatPlain => {
                if inode.is_null_start() {
                    return Ok(vec![0u8; size]);
                }
                let mut data = self.source.read_vec(inode.start_blk * bs, size)?;
                if data.len() < size {
                    data.resize(size, 0);
                }
                Ok(data)
            }
            DataLayout::FlatInline => {
                let full_bytes = ((inode.size / bs) * bs) as usize;
                let tail_bytes = size - full_bytes;
                let mut out = Vec::with_capacity(size);
                if full_bytes > 0 {
                    if inode.is_null_start() {
                        out.resize(full_bytes, 0);
                    } else {
                        let mut head = self.source.read_vec(inode.start_blk * bs, full_bytes)?;
                        head.resize(full_bytes, 0);
                        out.extend_from_slice(&head);
                    }
                }
                if tail_bytes > 0 {
                    let mut tail =
                        self.read_meta_space(inode.in_metabox, inode.data_offset, tail_bytes)?;
                    tail.resize(tail_bytes, 0);
                    out.extend_from_slice(&tail);
                }
                Ok(out)
            }
            DataLayout::ChunkBased => self.read_chunked(inode),
            DataLayout::CompressedFull | DataLayout::CompressedCompact => {
                self.read_compressed(inode)
            }
        }
    }

    fn read_chunked(&self, inode: &Inode) -> Result<Vec<u8>> {
        let fmt = inode.chunk_format;
        if (fmt & !0x007F) != 0 {
            return Err(ErofsError::Unsupported(format!(
                "unsupported chunk format flags 0x{fmt:04x}"
            )));
        }
        let chunkbits = (self.sb.blkszbits as u32) + ((fmt & CHUNK_FORMAT_BLKBITS_MASK) as u32);
        if chunkbits > 31 {
            return Err(ErofsError::Unsupported("chunk size > 2 GiB".into()));
        }
        let chunk_size = 1usize << chunkbits;
        let count = div_round_up(inode.size, chunk_size as u64) as usize;
        let has_indexes = (fmt & CHUNK_FORMAT_INDEXES) != 0;
        let unit = if has_indexes { 8u64 } else { 4u64 };
        let base = align_up(inode.data_offset, unit);
        let mask = if (fmt & CHUNK_FORMAT_48BIT) != 0 {
            EROFS_NULL_ADDR_48
        } else {
            EROFS_NULL_ADDR_32 as u64
        };
        let bs = self.sb.block_size as u64;

        let table = self.read_meta_space(inode.in_metabox, base, count * (unit as usize))?;
        if table.len() < count * (unit as usize) {
            return Err(ErofsError::Corrupted("truncated chunk index table".into()));
        }

        let total = inode.size as usize;
        let mut out = Vec::with_capacity(total);
        for i in 0..count {
            let off = i * (unit as usize);
            let start = if has_indexes {
                let hi = read_u16_le(&table, off) as u64;
                let dev_id = read_u16_le(&table, off + 2);
                let lo = read_u32_le(&table, off + 4) as u64;
                if dev_id != 0 {
                    return Err(ErofsError::Unsupported(format!(
                        "multi-device chunk (device_id={dev_id}) requires external blob device"
                    )));
                }
                ((hi << 32) | lo) & mask
            } else {
                read_u32_le(&table, off) as u64
            };
            let want = chunk_size.min(total - out.len());
            if start == mask {
                out.resize(out.len() + want, 0);
            } else {
                let mut part = self.source.read_vec(start * bs, want)?;
                part.resize(want, 0);
                out.extend_from_slice(&part);
            }
        }
        Ok(out)
    }

    fn read_compressed(&self, inode: &Inode) -> Result<Vec<u8>> {
        // Pre-buffer the inode's entire zmap index area in ONE read to avoid per-cluster syscalls.
        let bs = self.sb.block_size as u64;
        let max_lcl = div_round_up(inode.size, bs);
        let zmap_start = align_up(inode.data_offset, 8);
        let est_zmap_len: usize = match inode.layout {
            DataLayout::CompressedCompact => {
                // At most 4B per lcluster + alignment padding + 8B header + 64B slack + possible inline tail
                (16 + (max_lcl as usize) * 4 + 64 + (bs as usize)).min(64 * 1024 * 1024)
            }
            DataLayout::CompressedFull => {
                // 8B per lcluster (or up to 32B per extent) + 16B header
                (32 + (max_lcl as usize) * 8 + 64).min(64 * 1024 * 1024)
            }
            _ => 0,
        };
        let zmap_buf = self.read_meta_space(inode.in_metabox, zmap_start, est_zmap_len)?;
        let zmap_end = zmap_start + (zmap_buf.len() as u64);

        let mut read_meta = |off: u64, len: usize| -> Result<Vec<u8>> {
            if off >= zmap_start && off + (len as u64) <= zmap_end {
                let s = (off - zmap_start) as usize;
                Ok(zmap_buf[s..s + len].to_vec())
            } else {
                self.read_meta_space(inode.in_metabox, off, len)
            }
        };
        let (_hdr, extents) = map_all_extents(&self.sb, inode, &mut read_meta)?;

        let total = inode.size as usize;
        let mut out = vec![0u8; total];
        let bs_usize = self.sb.block_size as usize;

        // Batch contiguous on-disk pclusters into large reads (up to 4 MiB) to eliminate syscall overhead.
        const MAX_BATCH: usize = 4 * 1024 * 1024;
        let mut idx = 0usize;
        while idx < extents.len() {
            let ext = &extents[idx];
            if ext.la as usize >= total {
                break;
            }
            if ext.is_fragment || ext.is_inline {
                let la = ext.la as usize;
                let copy_len = (ext.llen as usize).min(total - la);
                if ext.is_fragment {
                    let packed = self.get_packed_blob()?;
                    let p_start = ext.pa as usize;
                    let p_end = (p_start + copy_len).min(packed.len());
                    if p_start >= packed.len() || p_end - p_start < copy_len {
                        return Err(ErofsError::Corrupted(format!(
                            "fragment out of bounds in packed inode (off={p_start}, len={copy_len}, packed_len={})",
                            packed.len()
                        )));
                    }
                    out[la..la + copy_len].copy_from_slice(&packed[p_start..p_end]);
                } else {
                    let raw_in = read_meta(ext.pa, ext.plen)?;
                    let decoded = decompress_pcluster(&DecompressRequest {
                        alg: ext.alg,
                        input: &raw_in,
                        decoded_len: ext.llen as usize,
                        decoded_skip: 0,
                        partial: ext.partial,
                        block_size: bs_usize,
                    })?;
                    out[la..la + copy_len].copy_from_slice(&decoded[..copy_len]);
                }
                idx += 1;
                continue;
            }

            // Collect a contiguous physical batch starting at `idx`
            let batch_start_pa = ext.pa;
            let mut batch_end_pa = ext.pa + (ext.plen as u64);
            let mut batch_end_idx = idx + 1;
            while batch_end_idx < extents.len() {
                let nxt = &extents[batch_end_idx];
                if nxt.is_fragment || nxt.is_inline || nxt.la as usize >= total {
                    break;
                }
                if nxt.pa < batch_start_pa {
                    break;
                }
                let nxt_end = nxt.pa + (nxt.plen as u64);
                // Allow tiny gaps (up to 64 KiB) or contiguous runs within MAX_BATCH
                if nxt.pa <= batch_end_pa + 65536 && nxt_end.saturating_sub(batch_start_pa) <= MAX_BATCH as u64 {
                    batch_end_pa = batch_end_pa.max(nxt_end);
                    batch_end_idx += 1;
                } else {
                    break;
                }
            }

            let batch_len = (batch_end_pa - batch_start_pa) as usize;
            let batch_buf = self.source.read_exact_vec(batch_start_pa, batch_len)?;

            for e in &extents[idx..batch_end_idx] {
                let la = e.la as usize;
                let copy_len = (e.llen as usize).min(total - la);
                let rel = (e.pa - batch_start_pa) as usize;
                let raw_in = &batch_buf[rel..rel + e.plen];
                let decoded = decompress_pcluster(&DecompressRequest {
                    alg: e.alg,
                    input: raw_in,
                    decoded_len: e.llen as usize,
                    decoded_skip: 0,
                    partial: e.partial,
                    block_size: bs_usize,
                })?;
                out[la..la + copy_len].copy_from_slice(&decoded[..copy_len]);
            }

            idx = batch_end_idx;
        }

        Ok(out)
    }

    /// Read directory entries for directory `inode` (excluding `.` and `..` when `include_dots` is false).
    pub fn read_dir(&self, inode: &Inode, include_dots: bool) -> Result<Vec<DirEntry>> {
        if !inode.is_dir() {
            return Err(ErofsError::InvalidFormat(format!(
                "nid={} is not a directory",
                inode.nid
            )));
        }
        let data = self.read_inode_bytes(inode)?;
        parse_dirent_blocks(&data, self.sb.dir_block_size(), include_dots)
    }

    pub fn read_symlink(&self, inode: &Inode) -> Result<String> {
        let data = self.read_inode_bytes(inode)?;
        let end = data.iter().position(|&b| b == 0).unwrap_or(data.len());
        Ok(String::from_utf8_lossy(&data[..end]).into_owned())
    }

    /// Build a `FileNode` summary for `(path, inode)`.
    pub fn build_node(&self, path: String, inode: &Inode) -> Result<FileNode> {
        let xattrs = self.read_xattrs(inode).unwrap_or_default();
        let selinux = xattrs
            .iter()
            .find(|x| x.name == XATTR_NAME_SELINUX)
            .map(|x| x.value_utf8_trimmed());
        let capabilities = xattrs
            .iter()
            .find(|x| x.name == XATTR_NAME_CAPABILITY)
            .and_then(|x| parse_vfs_capability(&x.value));

        let is_dir = inode.is_dir();
        let is_link = inode.is_symlink();
        let target = if is_link {
            Some(self.read_symlink(inode).unwrap_or_default())
        } else {
            None
        };

        Ok(FileNode {
            path,
            nid: inode.nid,
            ino: inode.ino,
            is_dir,
            is_link,
            file_type: ftype_label(inode.file_type()).to_string(),
            layout: inode.layout.as_str().to_string(),
            size: inode.size,
            mode: inode.mode,
            mode_octal: format!("{:04o}", inode.mode & 0o7777),
            perm: mode_to_string(inode.mode),
            uid: inode.uid,
            gid: inode.gid,
            nlink: inode.nlink,
            mtime: inode.mtime,
            target,
            selinux,
            capabilities,
        })
    }

    /// Fast direct path lookup (walks only path components instead of entire filesystem).
    pub fn lookup(&self, internal_path: &str) -> Result<Inode> {
        let clean = internal_path.replace('\\', "/");
        let clean = clean.trim_matches('/');
        let mut cur = self.read_inode(self.sb.root_nid)?;
        if clean.is_empty() {
            return Ok(cur);
        }
        for part in clean.split('/').filter(|p| !p.is_empty() && *p != ".") {
            if !cur.is_dir() {
                return Err(ErofsError::NotFound(internal_path.into()));
            }
            let entries = self.read_dir(&cur, false)?;
            let found = entries
                .into_iter()
                .find(|e| e.name == part)
                .ok_or_else(|| ErofsError::NotFound(internal_path.into()))?;
            cur = self.read_inode(found.nid)?;
        }
        Ok(cur)
    }

    /// Walk the entire filesystem (optionally including the root `"/"` node).
    pub fn walk(&self, include_root: bool) -> Result<Vec<FileNode>> {
        let mut nodes = Vec::new();
        let mut visited = HashSet::new();
        let root_inode = self.read_inode(self.sb.root_nid)?;
        if include_root {
            nodes.push(self.build_node("/".into(), &root_inode)?);
        }
        self.walk_recursive(&root_inode, "", &mut visited, &mut nodes)?;
        Ok(nodes)
    }

    /// Walk starting at a specific internal path (file or directory).
    pub fn walk_path(&self, internal_path: &str, recursive: bool) -> Result<Vec<FileNode>> {
        let clean = internal_path.replace('\\', "/");
        let clean = clean.trim_matches('/');
        let target = self.lookup(clean)?;
        let display_path = if clean.is_empty() {
            "/".to_string()
        } else {
            format!("/{clean}")
        };

        let mut nodes = vec![self.build_node(display_path, &target)?];
        if recursive && target.is_dir() {
            let mut visited = HashSet::new();
            self.walk_recursive(&target, clean, &mut visited, &mut nodes)?;
        }
        Ok(nodes)
    }

    fn walk_recursive(
        &self,
        dir_inode: &Inode,
        prefix: &str,
        visited: &mut HashSet<u64>,
        out: &mut Vec<FileNode>,
    ) -> Result<()> {
        if !visited.insert(dir_inode.nid) {
            return Ok(());
        }
        let entries = self.read_dir(dir_inode, false)?;
        for ent in entries {
            // Skip special packed/metabox internal inodes if somehow referenced
            if (self.sb.packed_nid != 0 && ent.nid == self.sb.packed_nid)
                || (self.sb.metabox_nid != 0 && ent.nid == self.sb.metabox_nid)
            {
                continue;
            }
            let rel = if prefix.is_empty() {
                ent.name.clone()
            } else {
                format!("{prefix}/{}", ent.name)
            };
            let full_path = format!("/{rel}");
            let child = self.read_inode(ent.nid)?;
            let mut node = self.build_node(full_path, &child)?;
            if ent.file_type == FT_DIR {
                node.is_dir = true;
            }
            if ent.file_type == FT_SYMLINK {
                node.is_link = true;
            }
            let is_dir = (child.mode & S_IFMT) == S_IFDIR || ent.file_type == FT_DIR;
            out.push(node);
            if is_dir && (child.mode & S_IFMT) != S_IFLNK {
                self.walk_recursive(&child, &rel, visited, out)?;
            }
        }
        Ok(())
    }

    /// Read a file by internal path.
    pub fn read_path(&self, internal_path: &str) -> Result<(Vec<u8>, FileNode)> {
        let inode = self.lookup(internal_path)?;
        if inode.is_dir() {
            return Err(ErofsError::InvalidPath(format!(
                "'{internal_path}' is a directory"
            )));
        }
        let data = self.read_inode_bytes(&inode)?;
        let clean = internal_path.replace('\\', "/");
        let clean = clean.trim_matches('/');
        let node = self.build_node(
            if clean.is_empty() {
                "/".into()
            } else {
                format!("/{clean}")
            },
            &inode,
        )?;
        Ok((data, node))
    }

    /// List physical data blocks used by `inode` (for `usage` and `compact`).
    pub fn inode_data_blocks(&self, inode: &Inode) -> Result<Vec<u64>> {
        let bs = self.sb.block_size as u64;
        if inode.size == 0 {
            return Ok(Vec::new());
        }
        match inode.layout {
            DataLayout::FlatPlain => {
                if inode.is_null_start() {
                    return Ok(Vec::new());
                }
                let n = div_round_up(inode.size, bs);
                Ok((inode.start_blk..inode.start_blk + n).collect())
            }
            DataLayout::FlatInline => {
                if inode.is_null_start() {
                    return Ok(Vec::new());
                }
                let full = inode.size / bs;
                Ok((inode.start_blk..inode.start_blk + full).collect())
            }
            DataLayout::CompressedFull | DataLayout::CompressedCompact => {
                let max_lcl = div_round_up(inode.size, bs);
                let zmap_start = align_up(inode.data_offset, 8);
                let est_len = match inode.layout {
                    DataLayout::CompressedCompact => {
                        (16 + (max_lcl as usize) * 4 + 64 + (bs as usize)).min(64 * 1024 * 1024)
                    }
                    _ => (32 + (max_lcl as usize) * 8 + 64).min(64 * 1024 * 1024),
                };
                let zmap_buf = self.read_meta_space(inode.in_metabox, zmap_start, est_len)?;
                let zmap_end = zmap_start + (zmap_buf.len() as u64);
                let mut read_meta = |off: u64, len: usize| -> Result<Vec<u8>> {
                    if off >= zmap_start && off + (len as u64) <= zmap_end {
                        let s = (off - zmap_start) as usize;
                        Ok(zmap_buf[s..s + len].to_vec())
                    } else {
                        self.read_meta_space(inode.in_metabox, off, len)
                    }
                };
                let (_hdr, extents) = map_all_extents(&self.sb, inode, &mut read_meta)?;
                let mut blks = HashSet::new();
                for ext in extents {
                    if ext.is_inline || ext.is_fragment {
                        continue;
                    }
                    let start = ext.pa / bs;
                    let cnt = div_round_up(ext.plen as u64, bs);
                    for k in 0..cnt {
                        blks.insert(start + k);
                    }
                }
                let mut v: Vec<u64> = blks.into_iter().collect();
                v.sort_unstable();
                Ok(v)
            }
            DataLayout::ChunkBased => {
                let fmt = inode.chunk_format;
                let chunkbits =
                    (self.sb.blkszbits as u32) + ((fmt & CHUNK_FORMAT_BLKBITS_MASK) as u32);
                let chunk_size = 1u64 << chunkbits;
                let blocks_per_chunk = div_round_up(chunk_size, bs);
                let count = div_round_up(inode.size, chunk_size) as usize;
                let has_indexes = (fmt & CHUNK_FORMAT_INDEXES) != 0;
                let unit = if has_indexes { 8u64 } else { 4u64 };
                let base = align_up(inode.data_offset, unit);
                let mask = if (fmt & CHUNK_FORMAT_48BIT) != 0 {
                    EROFS_NULL_ADDR_48
                } else {
                    EROFS_NULL_ADDR_32 as u64
                };
                let table = self.read_meta_space(inode.in_metabox, base, count * (unit as usize))?;
                let mut blks = HashSet::new();
                for i in 0..count {
                    let off = i * (unit as usize);
                    if off + (unit as usize) > table.len() {
                        break;
                    }
                    let start = if has_indexes {
                        let hi = read_u16_le(&table, off) as u64;
                        let dev_id = read_u16_le(&table, off + 2);
                        let lo = read_u32_le(&table, off + 4) as u64;
                        if dev_id != 0 {
                            continue;
                        }
                        ((hi << 32) | lo) & mask
                    } else {
                        read_u32_le(&table, off) as u64
                    };
                    if start == mask {
                        continue;
                    }
                    for k in 0..blocks_per_chunk {
                        blks.insert(start + k);
                    }
                }
                let mut v: Vec<u64> = blks.into_iter().collect();
                v.sort_unstable();
                Ok(v)
            }
        }
    }
}

/// Parse EROFS directory blocks into `DirEntry` records.
pub fn parse_dirent_blocks(
    data: &[u8],
    dir_block_size: usize,
    include_dots: bool,
) -> Result<Vec<DirEntry>> {
    let mut out = Vec::new();
    let mut pos = 0usize;
    while pos < data.len() {
        let end = (pos + dir_block_size).min(data.len());
        let blk = &data[pos..end];
        if blk.len() < EROFS_DIRENT_SIZE {
            break;
        }
        let first_nameoff = read_u16_le(blk, 8) as usize;
        let count = first_nameoff / EROFS_DIRENT_SIZE;
        if count == 0 || count * EROFS_DIRENT_SIZE > blk.len() {
            break;
        }
        for i in 0..count {
            let e = i * EROFS_DIRENT_SIZE;
            let nid = read_u64_le(blk, e);
            let nameoff = (read_u16_le(blk, e + 8) as usize).min(blk.len());
            let file_type = blk[e + 10];
            let next_nameoff = if i + 1 < count {
                (read_u16_le(blk, (i + 1) * EROFS_DIRENT_SIZE + 8) as usize).min(blk.len())
            } else {
                blk.len()
            };
            if nameoff > next_nameoff {
                continue;
            }
            let slice = &blk[nameoff..next_nameoff];
            let nul = slice.iter().position(|&b| b == 0).unwrap_or(slice.len());
            let raw_name = slice[..nul].to_vec();
            if raw_name.is_empty() {
                continue;
            }
            let name = String::from_utf8_lossy(&raw_name).into_owned();
            if !include_dots && (name == "." || name == "..") {
                continue;
            }
            out.push(DirEntry {
                nid,
                name,
                raw_name,
                file_type,
            });
        }
        pos += dir_block_size;
    }
    Ok(out)
}

/// Build binary EROFS directory blocks from `(nid, raw_name, file_type)` tuples.
/// Entries are sorted byte-wise ascending as required by the EROFS binary-search lookup spec.
pub fn build_dirent_blocks(
    entries: &[(u64, Vec<u8>, u8)],
    dir_block_size: usize,
) -> Result<Vec<u8>> {
    if entries.is_empty() {
        return Err(ErofsError::InvalidFormat(
            "directory cannot be completely empty".into(),
        ));
    }
    let mut sorted = entries.to_vec();
    sorted.sort_by(|a, b| a.1.cmp(&b.1));

    for w in sorted.windows(2) {
        if w[0].1 == w[1].1 {
            return Err(ErofsError::Edit(format!(
                "duplicate directory entry '{}'",
                String::from_utf8_lossy(&w[0].1)
            )));
        }
    }

    let mut blocks: Vec<Vec<&(u64, Vec<u8>, u8)>> = Vec::new();
    let mut cur = Vec::new();
    let mut used = 0usize;
    for ent in &sorted {
        let need = EROFS_DIRENT_SIZE + ent.1.len();
        if need > dir_block_size {
            return Err(ErofsError::Edit(format!(
                "directory entry name too long: '{}'",
                String::from_utf8_lossy(&ent.1)
            )));
        }
        if !cur.is_empty() && used + need > dir_block_size {
            blocks.push(cur);
            cur = Vec::new();
            used = 0;
        }
        cur.push(ent);
        used += need;
    }
    if !cur.is_empty() {
        blocks.push(cur);
    }

    let mut out = Vec::new();
    for (bi, blk) in blocks.iter().enumerate() {
        let mut buf = vec![0u8; dir_block_size];
        let mut nameoff = blk.len() * EROFS_DIRENT_SIZE;
        for (i, (nid, name, ftype)) in blk.iter().enumerate() {
            let e = i * EROFS_DIRENT_SIZE;
            buf[e..e + 8].copy_from_slice(&nid.to_le_bytes());
            buf[e + 8..e + 10].copy_from_slice(&(nameoff as u16).to_le_bytes());
            buf[e + 10] = *ftype;
            buf[e + 11] = 0;
            buf[nameoff..nameoff + name.len()].copy_from_slice(name);
            nameoff += name.len();
        }
        if bi + 1 == blocks.len() {
            out.extend_from_slice(&buf[..nameoff]);
        } else {
            out.extend_from_slice(&buf);
        }
    }
    Ok(out)
}

fn parse_compr_cfgs(source: &ImageSource, sb: &SuperBlock) -> Result<CompressionConfigs> {
    let mut cfgs = CompressionConfigs::default();
    if !sb.has_compr_cfgs() {
        if sb.lz4_max_distance != 0 || sb.has_lz4_0padding() {
            cfgs.lz4_max_distance = Some(sb.lz4_max_distance);
            cfgs.lz4_max_pclusterblks = Some(1);
        }
        return Ok(cfgs);
    }

    let mut off = EROFS_SUPER_OFFSET + (sb.sb_size as u64);
    let mut algs = sb.available_compr_algs;
    let mut alg = 0u8;
    while algs != 0 {
        if (algs & 1) != 0 {
            let len_buf = source.read_vec(off, 2)?;
            if len_buf.len() < 2 {
                break;
            }
            let size = read_u16_le(&len_buf, 0) as usize;
            let data = source.read_vec(off + 2, size)?;
            off = align_up(off + 2 + (size as u64), 4);
            match alg {
                COMPR_ALG_LZ4 if data.len() >= 4 => {
                    cfgs.lz4_max_distance = Some(read_u16_le(&data, 0));
                    let pblk = read_u16_le(&data, 2);
                    cfgs.lz4_max_pclusterblks = Some(if pblk == 0 { 1 } else { pblk });
                }
                COMPR_ALG_LZMA if data.len() >= 4 => {
                    cfgs.lzma_dict_size = Some(read_u32_le(&data, 0));
                }
                COMPR_ALG_DEFLATE if !data.is_empty() => {
                    cfgs.deflate_windowbits = Some(data[0]);
                }
                COMPR_ALG_ZSTD if data.len() >= 2 => {
                    cfgs.zstd_windowlog = Some(data[1].saturating_add(10));
                }
                _ => {}
            }
        }
        algs >>= 1;
        alg += 1;
    }
    Ok(cfgs)
}

/// Heuristic check whether byte payload looks like human-readable text.
pub fn is_text_bytes(data: &[u8]) -> bool {
    if data.is_empty() {
        return true;
    }
    if data.starts_with(&[0xEF, 0xBB, 0xBF])
        || data.starts_with(&[0xFF, 0xFE])
        || data.starts_with(&[0xFE, 0xFF])
    {
        return true;
    }
    let sample = &data[..data.len().min(8192)];
    if sample.contains(&0) {
        return false;
    }
    let nontext = sample
        .iter()
        .filter(|&&b| b < 0x20 && !matches!(b, 0x08 | 0x09 | 0x0A | 0x0C | 0x0D))
        .count();
    (nontext as f64) / (sample.len() as f64) <= 0.30
}
