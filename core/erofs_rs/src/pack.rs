// SPDX-License-Identifier: MIT

//! Clean-room EROFS image builder (`pack`): creates standard Linux-kernel-compatible
//! EROFS images from local directories with optional LZ4/LZ4HC/DEFLATE/ZSTD/LZMA compression,
//! shared xattr deduplication, and Android `fs_config` / `file_contexts` support.

use crate::android_cfg::{FileContextsMatcher, FsConfigTable};
use crate::compress::{CompressionAlg, compress_fixed_output};
use crate::crc32c::erofs_superblock_crc;
use crate::error::{ErofsError, Result};
use crate::format::{
    COMPR_ALG_DEFLATE, COMPR_ALG_LZ4, COMPR_ALG_LZMA, COMPR_ALG_ZSTD, DataLayout,
    EROFS_ISLOT_SIZE, EROFS_NULL_ADDR_32, EROFS_SUPER_BASE_SIZE, EROFS_SUPER_MAGIC,
    EROFS_SUPER_OFFSET, FEATURE_COMPAT_MTIME, FEATURE_COMPAT_SB_CHKSUM,
    FEATURE_COMPAT_XATTR_FILTER, FEATURE_INCOMPAT_BIG_PCLUSTER, FEATURE_INCOMPAT_COMPR_CFGS,
    FEATURE_INCOMPAT_LZ4_0PADDING, FT_DIR, FT_REG_FILE, FT_SYMLINK, INODE_DATALAYOUT_SHIFT,
    INODE_VERSION_COMPACT, INODE_VERSION_EXTENDED, S_IFDIR, S_IFLNK, S_IFREG, SuperBlock,
    XATTR_INDEX_SECURITY, Z_ADVISE_BIG_PCLUSTER_1, Z_ADVISE_BIG_PCLUSTER_2, Z_LCLUSTER_HEAD1,
    Z_LCLUSTER_NONHEAD, Z_LCLUSTER_PLAIN, Z_LI_D0_CBLKCNT, align_up, align_up_usize, div_round_up,
    format_uuid, parse_uuid, write_u16_le, write_u32_le, write_u64_le,
};
use crate::reader::build_dirent_blocks;
use crate::xattr::{
    encode_raw_xattr_entry, encode_vfs_capability_v2, split_xattr_name, xattr_name_filter_bit,
};
use rayon::prelude::*;
use regex::Regex;
use serde::Serialize;
use std::collections::{BTreeMap, HashMap};
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum InodeFormatMode {
    Auto,
    Compact,
    Extended,
}

impl InodeFormatMode {
    pub fn parse(s: &str) -> Result<Self> {
        match s.to_ascii_lowercase().as_str() {
            "auto" => Ok(Self::Auto),
            "compact" => Ok(Self::Compact),
            "extended" => Ok(Self::Extended),
            other => Err(ErofsError::InvalidArg(format!(
                "invalid inode format '{other}' (expected auto|compact|extended)"
            ))),
        }
    }
}

#[derive(Debug, Clone)]
pub struct PackOptions {
    pub source_dir: PathBuf,
    pub output_image: PathBuf,
    pub block_size: u32,
    pub algorithm: CompressionAlg,
    pub level: Option<i32>,
    pub pcluster_size: u32,
    pub inline_data: bool,
    pub inode_format: InodeFormatMode,
    pub volume_label: String,
    pub uuid: Option<String>,
    pub timestamp: Option<u64>,
    pub mount_point: String,
    pub fs_config: Option<PathBuf>,
    pub file_contexts: Option<PathBuf>,
    pub force_uid: Option<u32>,
    pub force_gid: Option<u32>,
    pub uid_offset: u32,
    pub gid_offset: u32,
    pub all_root: bool,
    pub enable_crc: bool,
    pub align_blocks: u64,
    pub exclude_regex: Vec<String>,
    pub threads: usize,
}

impl Default for PackOptions {
    fn default() -> Self {
        Self {
            source_dir: PathBuf::new(),
            output_image: PathBuf::new(),
            block_size: 4096,
            algorithm: CompressionAlg::None,
            level: None,
            pcluster_size: 4096,
            inline_data: true,
            inode_format: InodeFormatMode::Auto,
            volume_label: String::new(),
            uuid: None,
            timestamp: None,
            mount_point: "/".to_string(),
            fs_config: None,
            file_contexts: None,
            force_uid: None,
            force_gid: None,
            uid_offset: 0,
            gid_offset: 0,
            all_root: false,
            enable_crc: true,
            align_blocks: 1,
            exclude_regex: Vec::new(),
            threads: 0,
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct PackReport {
    pub output_image: String,
    pub block_size: u32,
    pub total_blocks: u64,
    pub image_bytes: u64,
    pub inodes: usize,
    pub files: usize,
    pub dirs: usize,
    pub symlinks: usize,
    pub compressed_files: usize,
    pub shared_xattrs: usize,
    pub algorithm: String,
    pub uuid: String,
    pub timestamp: u64,
    pub crc32c: Option<u32>,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct RawXattr {
    name_index: u8,
    name: Vec<u8>,
    value: Vec<u8>,
}

#[derive(Debug, Clone)]
enum NodeKind {
    Dir { children: Vec<(Vec<u8>, usize)> },
    File { host_path: PathBuf },
    Symlink { target: Vec<u8> },
}

#[derive(Debug)]
struct BuildNode {
    id: usize,
    parent_id: usize,
    rel_path: String,
    kind: NodeKind,
    mode: u16,
    uid: u32,
    gid: u32,
    mtime: u64,
    size: u64,
    nlink: u32,
    xattrs: Vec<RawXattr>,
}

#[derive(Debug, Clone)]
struct CompressedChunk {
    raw_len: usize,
    is_compressed: bool,
    blocks_data: Vec<u8>, // multiple of block_size
    cblks: usize,
}

#[derive(Debug)]
enum PreparedPayload {
    Raw {
        data: Vec<u8>,
    },
    Compressed {
        chunks: Vec<CompressedChunk>,
        total_size: u64,
    },
}

/// Build an EROFS image from a local source directory.
fn emit_monotonic<F: Fn(u8, &str)>(last: &Mutex<u8>, cb: &F, pct: u8, stage: &str) {
    let clamped = pct.min(100);
    let mut guard = last.lock().unwrap();
    if clamped > *guard || clamped == 0 {
        *guard = clamped.max(*guard);
        cb(clamped, stage);
    }
}

pub fn pack_directory(opts: &PackOptions) -> Result<PackReport> {
    pack_directory_with_progress(opts, |_, _| {})
}

pub fn pack_directory_with_progress<F>(opts: &PackOptions, on_progress: F) -> Result<PackReport>
where
    F: Fn(u8, &str) + Sync,
{
    let last_pct = Mutex::new(0u8);
    emit_monotonic(&last_pct, &on_progress, 2, "Scanning directory");
    if !opts.source_dir.is_dir() {
        return Err(ErofsError::InvalidArg(format!(
            "source directory does not exist: {}",
            opts.source_dir.display()
        )));
    }
    if !opts.block_size.is_power_of_two() || !(512..=65536).contains(&opts.block_size) {
        return Err(ErofsError::InvalidArg(format!(
            "invalid block size {} (must be power of two in 512..=65536)",
            opts.block_size
        )));
    }
    let bs = opts.block_size as usize;
    let blkszbits = opts.block_size.trailing_zeros() as u8;
    let pcluster_size = (opts.pcluster_size as usize).max(bs);
    if pcluster_size % bs != 0 || pcluster_size > 1024 * 1024 {
        return Err(ErofsError::InvalidArg(format!(
            "pcluster size ({pcluster_size}) must be a multiple of block_size ({bs}) and <= 1 MiB"
        )));
    }

    let fs_cfg = match &opts.fs_config {
        Some(p) => Some(FsConfigTable::load(p)?),
        None => None,
    };
    let file_ctx = match &opts.file_contexts {
        Some(p) => Some(FileContextsMatcher::load(p)?),
        None => None,
    };
    let mut excludes = Vec::new();
    for pat in &opts.exclude_regex {
        excludes.push(
            Regex::new(pat)
                .map_err(|e| ErofsError::InvalidArg(format!("invalid exclude regex '{pat}': {e}")))?,
        );
    }

    let mut effective_mp = opts.mount_point.clone();
    if effective_mp.trim_matches('/').is_empty() {
        if let Some(dir_name) = opts.source_dir.file_name().and_then(|s| s.to_str()) {
            let has_pfx = fs_cfg
                .as_ref()
                .is_some_and(|c| c.has_partition_prefix(dir_name))
                || file_ctx
                    .as_ref()
                    .is_some_and(|f| f.has_partition_prefix(dir_name));
            if has_pfx {
                effective_mp = format!("/{dir_name}");
            }
        }
    }

    let now_epoch = opts.timestamp.unwrap_or_else(|| {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0)
    });

    let uuid = resolve_uuid(opts.uuid.as_deref())?;

    // 1. Scan source directory into in-memory tree.
    let mut nodes: Vec<BuildNode> = Vec::new();
    let mut hardlink_map: HashMap<(u64, u64), usize> = HashMap::new();
    let root_id = 0usize;
    let root_mtime = opts.timestamp.unwrap_or_else(|| {
        fs::symlink_metadata(&opts.source_dir)
            .ok()
            .and_then(|m| m.modified().ok())
            .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
            .map(|d| d.as_secs())
            .unwrap_or(now_epoch)
    });
    nodes.push(BuildNode {
        id: root_id,
        parent_id: root_id,
        rel_path: "/".to_string(),
        kind: NodeKind::Dir {
            children: Vec::new(),
        },
        mode: S_IFDIR | 0o755,
        uid: 0,
        gid: 0,
        mtime: root_mtime,
        size: 0,
        nlink: 2,
        xattrs: Vec::new(),
    });
    apply_android_attrs(
        &mut nodes[root_id],
        opts,
        &effective_mp,
        fs_cfg.as_ref(),
        file_ctx.as_ref(),
        'd',
    );

    scan_dir_recursive(
        &opts.source_dir,
        root_id,
        "",
        opts,
        &effective_mp,
        fs_cfg.as_ref(),
        file_ctx.as_ref(),
        &excludes,
        now_epoch,
        &mut hardlink_map,
        &mut nodes,
    )?;

    let (sb_epoch, sb_build_time) = match opts.timestamp {
        Some(ts) => (ts, 0u32),
        None => {
            let min_mtime = nodes.iter().map(|n| n.mtime).min().unwrap_or(now_epoch);
            let delta = now_epoch.saturating_sub(min_mtime).min(u32::MAX as u64) as u32;
            (min_mtime, delta)
        }
    };

    // Fix directory nlink counts (= 2 + number of subdirectory children)
    let dir_sub_counts: Vec<(usize, u32)> = nodes
        .iter()
        .filter_map(|n| match &n.kind {
            NodeKind::Dir { children } => {
                let subdirs = children
                    .iter()
                    .filter(|&&(_, cid)| matches!(nodes[cid].kind, NodeKind::Dir { .. }))
                    .count() as u32;
                Some((n.id, 2 + subdirs))
            }
            _ => None,
        })
        .collect();
    for (id, nl) in dir_sub_counts {
        nodes[id].nlink = nl;
    }

    // 2. Build shared xattr deduplication table (any xattr appearing >= 2 times becomes shared).
    emit_monotonic(&last_pct, &on_progress, 12, "Deduplicating metadata");
    let mut xattr_freq: HashMap<RawXattr, usize> = HashMap::new();
    for n in &nodes {
        for xa in &n.xattrs {
            *xattr_freq.entry(xa.clone()).or_insert(0) += 1;
        }
    }
    let mut shared_entries: Vec<RawXattr> = xattr_freq
        .into_iter()
        .filter(|(_, cnt)| *cnt >= 2)
        .map(|(k, _)| k)
        .collect();
    shared_entries.sort_by(|a, b| {
        a.name_index
            .cmp(&b.name_index)
            .then_with(|| a.name.cmp(&b.name))
            .then_with(|| a.value.cmp(&b.value))
    });

    let mut shared_xattr_blob = Vec::new();
    let mut shared_xattr_id: HashMap<RawXattr, u32> = HashMap::new();
    for xa in &shared_entries {
        let idx = (shared_xattr_blob.len() / 4) as u32;
        let encoded = encode_raw_xattr_entry(xa.name_index, &xa.name, &xa.value)?;
        shared_xattr_blob.extend_from_slice(&encoded);
        shared_xattr_id.insert(xa.clone(), idx);
    }

    let xattr_blocks = div_round_up(shared_xattr_blob.len() as u64, bs as u64);
    let xattr_blkaddr = if xattr_blocks > 0 { 1u32 } else { 0u32 };
    let meta_blkaddr = (1 + xattr_blocks) as u32;

    // 3. Prepare file payloads (parallel compression for regular files!).
    let file_ids: Vec<usize> = nodes
        .iter()
        .filter(|n| matches!(n.kind, NodeKind::File { .. }))
        .map(|n| n.id)
        .collect();
    let total_files = file_ids.len().max(1);
    let done_files = AtomicUsize::new(0);
    emit_monotonic(&last_pct, &on_progress, 16, "Compressing EROFS files");

    let compress_one = |&id: &usize| -> Result<(usize, PreparedPayload)> {
        let host_path = match &nodes[id].kind {
            NodeKind::File { host_path } => host_path,
            _ => unreachable!(),
        };
        let data = fs::read(host_path)?;
        let res = if opts.algorithm == CompressionAlg::None || data.len() <= bs {
            (id, PreparedPayload::Raw { data })
        } else {
            // Try compressing into pclusters
            let chunks = compress_file_into_pclusters(
                &data,
                opts.algorithm,
                opts.level,
                pcluster_size,
                bs,
            )?;
            let total_comp_blks: usize = chunks.iter().map(|c| c.cblks).sum();
            let raw_blks = (data.len() + bs - 1) / bs;
            if total_comp_blks < raw_blks && chunks.iter().any(|c| c.is_compressed) {
                (
                    id,
                    PreparedPayload::Compressed {
                        chunks,
                        total_size: data.len() as u64,
                    },
                )
            } else {
                (id, PreparedPayload::Raw { data })
            }
        };
        let done = done_files.fetch_add(1, Ordering::Relaxed) + 1;
        let pct = 16 + ((done * 56) / total_files).min(56) as u8;
        emit_monotonic(&last_pct, &on_progress, pct, "Compressing EROFS files");
        Ok(res)
    };

    let prepared_vec: Vec<(usize, PreparedPayload)> = if opts.threads == 1 {
        file_ids.iter().map(compress_one).collect::<Result<Vec<_>>>()?
    } else if opts.threads > 1 {
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(opts.threads)
            .build()
            .map_err(|e| ErofsError::Io(std::io::Error::other(e.to_string())))?;
        pool.install(|| file_ids.par_iter().map(compress_one).collect::<Result<Vec<_>>>())?
    } else {
        file_ids
            .par_iter()
            .map(compress_one)
            .collect::<Result<Vec<_>>>()?
    };

    let mut payloads: HashMap<usize, PreparedPayload> = prepared_vec.into_iter().collect();

    // Update regular file sizes & symlink payloads
    let mut compressed_files = 0usize;
    let mut use_big_pcluster = false;
    for n in &mut nodes {
        match &n.kind {
            NodeKind::File { .. } => {
                if let Some(p) = payloads.get(&n.id) {
                    match p {
                        PreparedPayload::Raw { data } => n.size = data.len() as u64,
                        PreparedPayload::Compressed { chunks, total_size } => {
                            n.size = *total_size;
                            compressed_files += 1;
                            if chunks.iter().any(|c| c.is_compressed && c.cblks > 1) {
                                use_big_pcluster = true;
                            }
                        }
                    }
                }
            }
            NodeKind::Symlink { target } => {
                n.size = target.len() as u64;
                payloads.insert(
                    n.id,
                    PreparedPayload::Raw {
                        data: target.clone(),
                    },
                );
            }
            NodeKind::Dir { .. } => {}
        }
    }

    // 4. Precompute xattr ibody blobs and whether each inode is compact or extended.
    emit_monotonic(&last_pct, &on_progress, 74, "Building EROFS metadata");
    let mut xattr_ibodies: Vec<(Vec<u8>, u16)> = Vec::with_capacity(nodes.len());
    let mut is_extended_vec: Vec<bool> = Vec::with_capacity(nodes.len());
    for n in &nodes {
        let (ibody, icount) = build_xattr_ibody(&n.xattrs, &shared_xattr_id)?;
        xattr_ibodies.push((ibody, icount));
        let ext = match opts.inode_format {
            InodeFormatMode::Extended => true,
            InodeFormatMode::Compact => false,
            InodeFormatMode::Auto => {
                n.size > u32::MAX as u64
                    || n.uid > u16::MAX as u32
                    || n.gid > u16::MAX as u32
                    || n.nlink > u16::MAX as u32
                    || n.mtime < sb_epoch
                    || n.mtime.saturating_sub(sb_epoch) > u32::MAX as u64
            }
        };
        is_extended_vec.push(ext);
    }

    // Compute exact directory binary sizes (independent of NID values because every dirent is 12B + name).
    for id in 0..nodes.len() {
        if let NodeKind::Dir { children } = &nodes[id].kind {
            let mut dummy_entries = Vec::with_capacity(children.len() + 2);
            dummy_entries.push((0u64, b".".to_vec(), FT_DIR));
            dummy_entries.push((0u64, b"..".to_vec(), FT_DIR));
            for (child_name, cid) in children {
                let ft = match nodes[*cid].kind {
                    NodeKind::Dir { .. } => FT_DIR,
                    NodeKind::File { .. } => FT_REG_FILE,
                    NodeKind::Symlink { .. } => FT_SYMLINK,
                };
                dummy_entries.push((0u64, child_name.clone(), ft));
            }
            let dir_bytes = build_dirent_blocks(&dummy_entries, bs)?;
            nodes[id].size = dir_bytes.len() as u64;
        }
    }

    // 5. Assign NIDs & metadata layout (single pass, knowing exact header + xattr + inline/zmap size).
    let mut nids: Vec<u64> = vec![0; nodes.len()];
    let mut layouts: Vec<DataLayout> = vec![DataLayout::FlatPlain; nodes.len()];
    let mut meta_offsets: Vec<u64> = vec![0; nodes.len()]; // relative to meta_base
    let mut meta_cursor: u64 = 0;

    for id in 0..nodes.len() {
        let hdr_sz = if is_extended_vec[id] { 64u64 } else { 32u64 };
        let xattr_sz = xattr_ibodies[id].0.len() as u64;
        let is_compressed = matches!(
            payloads.get(&id),
            Some(PreparedPayload::Compressed { .. })
        );

        // Keep fixed inode header (and xattrs if <= block_size) inside a single metadata block.
        if (meta_cursor % (bs as u64)) + hdr_sz > bs as u64 {
            meta_cursor = align_up(meta_cursor, bs as u64);
        }
        if hdr_sz + xattr_sz <= bs as u64
            && (meta_cursor % (bs as u64)) + hdr_sz + xattr_sz > bs as u64
        {
            meta_cursor = align_up(meta_cursor, bs as u64);
        }

        let (layout, tail_meta_sz) = if is_compressed {
            let lclusters = div_round_up(nodes[id].size, bs as u64);
            let n_packs = div_round_up(lclusters, 2); // 4B compact packs hold 2 lclusters each (8 bytes)
            let zmap_off = align_up(hdr_sz + xattr_sz, 8) - (hdr_sz + xattr_sz);
            let zmap_sz = zmap_off + 8 + n_packs * 8;
            (DataLayout::CompressedCompact, zmap_sz)
        } else {
            let tail = nodes[id].size % (bs as u64);
            let allow_inline = opts.inline_data || !matches!(nodes[id].kind, NodeKind::File { .. });
            let total_with_tail = hdr_sz + xattr_sz + tail;
            if allow_inline && tail > 0 && total_with_tail <= bs as u64 {
                // Ensure the entire inline inode fits within a single metadata block.
                if (meta_cursor % (bs as u64)) + total_with_tail > bs as u64 {
                    meta_cursor = align_up(meta_cursor, bs as u64);
                }
                (DataLayout::FlatInline, tail)
            } else {
                (DataLayout::FlatPlain, 0)
            }
        };

        let off = meta_cursor;
        meta_offsets[id] = off;
        nids[id] = off / EROFS_ISLOT_SIZE;
        layouts[id] = layout;
        meta_cursor = align_up(off + hdr_sz + xattr_sz + tail_meta_sz, EROFS_ISLOT_SIZE);
    }

    // Build final directory binary payloads now that all NIDs are known!
    for id in 0..nodes.len() {
        if let NodeKind::Dir { children } = &nodes[id].kind {
            let mut entries = Vec::with_capacity(children.len() + 2);
            entries.push((nids[id], b".".to_vec(), FT_DIR));
            entries.push((nids[nodes[id].parent_id], b"..".to_vec(), FT_DIR));
            for (child_name, cid) in children {
                let ft = match nodes[*cid].kind {
                    NodeKind::Dir { .. } => FT_DIR,
                    NodeKind::File { .. } => FT_REG_FILE,
                    NodeKind::Symlink { .. } => FT_SYMLINK,
                };
                entries.push((nids[*cid], child_name.clone(), ft));
            }
            let dir_bytes = build_dirent_blocks(&entries, bs)?;
            debug_assert_eq!(dir_bytes.len() as u64, nodes[id].size);
            payloads.insert(id, PreparedPayload::Raw { data: dir_bytes });
        }
    }

    // 6. Allocate data blocks sequentially starting right after metadata area.
    let meta_blocks = div_round_up(meta_cursor, bs as u64);
    let data_start_blk = (meta_blkaddr as u64) + meta_blocks;
    let mut next_data_blk = data_start_blk;
    let mut data_region: Vec<u8> = Vec::new();
    let mut meta_region = vec![0u8; (meta_blocks as usize) * bs];

    let max_pcluster_blks = (pcluster_size / bs) as u16;
    let compr_alg_id = opts.algorithm.on_disk_id().unwrap_or(COMPR_ALG_LZ4);

    for id in 0..nodes.len() {
        let n = &nodes[id];
        let off = meta_offsets[id] as usize;
        let is_ext = is_extended_vec[id];
        let hdr_sz = if is_ext { 64usize } else { 32usize };
        let (ref xattr_bytes, xattr_icount) = xattr_ibodies[id];
        let layout = layouts[id];

        let mut start_blk_field: u32 = EROFS_NULL_ADDR_32;

        match payloads.get(&id) {
            Some(PreparedPayload::Raw { data }) => {
                match layout {
                    DataLayout::FlatPlain => {
                        if !data.is_empty() {
                            start_blk_field = next_data_blk as u32;
                            let nblks = div_round_up(data.len() as u64, bs as u64);
                            data_region.extend_from_slice(data);
                            let rem = (nblks as usize) * bs - data.len();
                            if rem > 0 {
                                data_region.resize(data_region.len() + rem, 0);
                            }
                            next_data_blk += nblks;
                        }
                    }
                    DataLayout::FlatInline => {
                        let full_blks = data.len() / bs;
                        let full_bytes = full_blks * bs;
                        if full_blks > 0 {
                            start_blk_field = next_data_blk as u32;
                            data_region.extend_from_slice(&data[..full_bytes]);
                            next_data_blk += full_blks as u64;
                        }
                        let tail = &data[full_bytes..];
                        let tail_pos = off + hdr_sz + xattr_bytes.len();
                        meta_region[tail_pos..tail_pos + tail.len()].copy_from_slice(tail);
                    }
                    _ => unreachable!(),
                }
            }
            Some(PreparedPayload::Compressed { chunks, total_size }) => {
                let first_blk = next_data_blk as u32;
                let mut total_cblks = 0u32;
                for ch in chunks {
                    data_region.extend_from_slice(&ch.blocks_data);
                    total_cblks += ch.cblks as u32;
                    next_data_blk += ch.cblks as u64;
                }
                // For compressed inodes, i_u.blocks_lo stores total compressed blocks count.
                start_blk_field = total_cblks;

                let z_off = align_up_usize(off + hdr_sz + xattr_bytes.len(), 8);
                let zmap_bytes = encode_compact_4b_zmap(
                    chunks,
                    *total_size,
                    bs,
                    first_blk,
                    compr_alg_id,
                    use_big_pcluster,
                )?;
                meta_region[z_off..z_off + zmap_bytes.len()].copy_from_slice(&zmap_bytes);
            }
            None => {}
        }

        // Write inode header
        let format_word: u16 = (if is_ext {
            INODE_VERSION_EXTENDED
        } else {
            INODE_VERSION_COMPACT
        }) | ((layout as u16) << INODE_DATALAYOUT_SHIFT);

        write_u16_le(&mut meta_region, off, format_word);
        write_u16_le(&mut meta_region, off + 2, xattr_icount);
        write_u16_le(&mut meta_region, off + 4, n.mode);

        if is_ext {
            write_u16_le(&mut meta_region, off + 6, 0); // startblk_hi = 0
            write_u64_le(&mut meta_region, off + 8, n.size);
            write_u32_le(&mut meta_region, off + 16, start_blk_field);
            write_u32_le(&mut meta_region, off + 20, (id + 1) as u32);
            write_u32_le(&mut meta_region, off + 24, n.uid);
            write_u32_le(&mut meta_region, off + 28, n.gid);
            write_u64_le(&mut meta_region, off + 32, n.mtime);
            write_u32_le(&mut meta_region, off + 40, 0);
            write_u32_le(&mut meta_region, off + 44, n.nlink);
        } else {
            write_u16_le(&mut meta_region, off + 6, n.nlink.min(u16::MAX as u32) as u16);
            write_u32_le(&mut meta_region, off + 8, n.size as u32);
            write_u32_le(&mut meta_region, off + 12, n.mtime.saturating_sub(sb_epoch) as u32);
            write_u32_le(&mut meta_region, off + 16, start_blk_field);
            write_u32_le(&mut meta_region, off + 20, (id + 1) as u32);
            write_u16_le(&mut meta_region, off + 24, n.uid as u16);
            write_u16_le(&mut meta_region, off + 26, n.gid as u16);
            write_u32_le(&mut meta_region, off + 28, 0);
        }

        // Write xattr ibody
        if !xattr_bytes.is_empty() {
            meta_region[off + hdr_sz..off + hdr_sz + xattr_bytes.len()]
                .copy_from_slice(xattr_bytes);
        }
    }

    // 7. Build SuperBlock (+ optional COMPR_CFGS) in Block 0.
    let align_blks = opts.align_blocks.max(1);
    let total_blocks = align_up(next_data_blk.max(1), align_blks);

    let mut feature_compat = FEATURE_COMPAT_MTIME | FEATURE_COMPAT_XATTR_FILTER;
    if opts.enable_crc {
        feature_compat |= FEATURE_COMPAT_SB_CHKSUM;
    }

    let need_compr_cfgs = compressed_files > 0
        && (use_big_pcluster
            || !matches!(opts.algorithm, CompressionAlg::Lz4 | CompressionAlg::Lz4Hc));

    let mut feature_incompat = 0u32;
    let mut available_compr_algs = 0u16;
    let mut lz4_max_distance = 0u16;
    if compressed_files > 0 {
        feature_incompat |= FEATURE_INCOMPAT_LZ4_0PADDING;
        if need_compr_cfgs {
            feature_incompat |= FEATURE_INCOMPAT_COMPR_CFGS;
            if use_big_pcluster {
                feature_incompat |= FEATURE_INCOMPAT_BIG_PCLUSTER;
            }
            available_compr_algs = 1u16 << compr_alg_id;
        } else {
            lz4_max_distance = 65535;
        }
    }

    let sb = SuperBlock {
        magic: EROFS_SUPER_MAGIC,
        checksum: 0,
        feature_compat,
        blkszbits,
        block_size: opts.block_size,
        sb_extslots: 0,
        sb_size: EROFS_SUPER_BASE_SIZE,
        root_nid: nids[root_id],
        inos: nodes.len() as u64,
        epoch: sb_epoch,
        fixed_nsec: 0,
        blocks: total_blocks,
        meta_blkaddr,
        xattr_blkaddr,
        uuid,
        volume_name: opts.volume_label.clone(),
        feature_incompat,
        available_compr_algs,
        lz4_max_distance,
        extra_devices: 0,
        devt_slotoff: 0,
        dirblkbits: 0,
        xattr_prefix_count: 0,
        xattr_prefix_start: 0,
        packed_nid: 0,
        xattr_filter_reserved: 0,
        ishare_xattr_prefix_id: 0,
        build_time: sb_build_time,
        metabox_nid: 0,
    };

    let mut block0 = vec![0u8; bs.max((EROFS_SUPER_OFFSET as usize) + 256)];
    let sb_bytes = sb.serialize();
    let sb_off = EROFS_SUPER_OFFSET as usize;
    block0[sb_off..sb_off + sb_bytes.len()].copy_from_slice(&sb_bytes);

    if need_compr_cfgs {
        let cfg_off = sb_off + sb_bytes.len();
        let cfg_blob = encode_compr_cfg(compr_alg_id, max_pcluster_blks);
        block0[cfg_off..cfg_off + cfg_blob.len()].copy_from_slice(&cfg_blob);
    }

    let crc32c = if opts.enable_crc {
        let crc_len = if bs > sb_off { bs - sb_off } else { bs };
        let crc = erofs_superblock_crc(&block0[sb_off..sb_off + crc_len]);
        write_u32_le(&mut block0, sb_off + 4, crc);
        Some(crc)
    } else {
        None
    };

    // 8. Write final image file cleanly.
    emit_monotonic(&last_pct, &on_progress, 84, "Writing EROFS image");
    if let Some(par) = opts.output_image.parent() {
        if !par.as_os_str().is_empty() {
            fs::create_dir_all(par)?;
        }
    }
    let mut out_file = File::create(&opts.output_image)?;
    out_file.write_all(&block0[..bs])?;

    if xattr_blocks > 0 {
        let mut xblk = shared_xattr_blob;
        xblk.resize((xattr_blocks as usize) * bs, 0);
        out_file.write_all(&xblk)?;
    }

    let total_payload = (meta_region.len() + data_region.len()).max(1);
    let mut written_payload = 0usize;
    let write_chunk_sz = 2 * 1024 * 1024;
    for chunk in meta_region.chunks(write_chunk_sz) {
        out_file.write_all(chunk)?;
        written_payload += chunk.len();
        let pct = 84 + ((written_payload * 15) / total_payload).min(15) as u8;
        emit_monotonic(&last_pct, &on_progress, pct, "Writing EROFS image");
    }
    for chunk in data_region.chunks(write_chunk_sz) {
        out_file.write_all(chunk)?;
        written_payload += chunk.len();
        let pct = 84 + ((written_payload * 15) / total_payload).min(15) as u8;
        emit_monotonic(&last_pct, &on_progress, pct, "Writing EROFS image");
    }

    let written_bytes = (bs as u64) + xattr_blocks * (bs as u64) + (meta_region.len() as u64) + (data_region.len() as u64);
    let target_bytes = total_blocks * (bs as u64);
    if target_bytes > written_bytes {
        out_file.write_all(&vec![0u8; (target_bytes - written_bytes) as usize])?;
    }
    out_file.flush()?;
    emit_monotonic(&last_pct, &on_progress, 100, "Done");

    let files = nodes
        .iter()
        .filter(|n| matches!(n.kind, NodeKind::File { .. }))
        .count();
    let dirs = nodes
        .iter()
        .filter(|n| matches!(n.kind, NodeKind::Dir { .. }))
        .count();
    let symlinks = nodes
        .iter()
        .filter(|n| matches!(n.kind, NodeKind::Symlink { .. }))
        .count();

    Ok(PackReport {
        output_image: opts.output_image.display().to_string(),
        block_size: opts.block_size,
        total_blocks,
        image_bytes: target_bytes,
        inodes: nodes.len(),
        files,
        dirs,
        symlinks,
        compressed_files,
        shared_xattrs: shared_entries.len(),
        algorithm: opts.algorithm.name().to_string(),
        uuid: format_uuid(&uuid),
        timestamp: sb_epoch.saturating_add(sb_build_time as u64),
        crc32c,
    })
}

#[allow(clippy::too_many_arguments)]
fn scan_dir_recursive(
    host_dir: &Path,
    parent_id: usize,
    rel_prefix: &str,
    opts: &PackOptions,
    effective_mp: &str,
    fs_cfg: Option<&FsConfigTable>,
    file_ctx: Option<&FileContextsMatcher>,
    excludes: &[Regex],
    default_epoch: u64,
    hardlink_map: &mut HashMap<(u64, u64), usize>,
    nodes: &mut Vec<BuildNode>,
) -> Result<()> {
    // Read and sort entries deterministically by raw UTF-8 name.
    let mut entries: BTreeMap<String, PathBuf> = BTreeMap::new();
    for ent in fs::read_dir(host_dir)? {
        let ent = ent?;
        let fname = ent.file_name().to_string_lossy().into_owned();
        entries.insert(fname, ent.path());
    }

    let mut child_ids: Vec<(Vec<u8>, usize)> = Vec::new();
    for (fname, path) in entries {
        let rel = if rel_prefix.is_empty() {
            fname.clone()
        } else {
            format!("{rel_prefix}/{fname}")
        };
        if excludes.iter().any(|re| re.is_match(&rel)) {
            continue;
        }
        let meta = fs::symlink_metadata(&path)?;
        let ft = meta.file_type();
        let mtime = opts.timestamp.unwrap_or_else(|| {
            meta.modified()
                .ok()
                .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
                .map(|d| d.as_secs())
                .unwrap_or(default_epoch)
        });

        let (kind, default_mode, mode_ch, hardlink_key) = if ft.is_symlink() {
            let target = fs::read_link(&path)?
                .to_string_lossy()
                .replace('\\', "/")
                .into_bytes();
            (NodeKind::Symlink { target }, S_IFLNK | 0o777, 'l', None)
        } else if ft.is_dir() {
            (
                NodeKind::Dir {
                    children: Vec::new(),
                },
                S_IFDIR | 0o755,
                'd',
                None,
            )
        } else if ft.is_file() {
            if let Some(target) = try_read_cygwin_symlink(&path, &meta) {
                (NodeKind::Symlink { target }, S_IFLNK | 0o777, 'l', None)
            } else {
                let hkey = host_hardlink_key(&path, &meta);
                if let Some(k) = hkey {
                    if let Some(&existing_id) = hardlink_map.get(&k) {
                        nodes[existing_id].nlink += 1;
                        child_ids.push((fname.into_bytes(), existing_id));
                        continue;
                    }
                }
                #[cfg(unix)]
                let perm = {
                    use std::os::unix::fs::PermissionsExt;
                    (meta.permissions().mode() as u16) & 0o7777
                };
                #[cfg(not(unix))]
                let perm = 0o644u16;
                (NodeKind::File { host_path: path.clone() }, S_IFREG | perm, '-', hkey)
            }
        } else {
            continue;
        };

        let id = nodes.len();
        if let Some(k) = hardlink_key {
            hardlink_map.insert(k, id);
        }
        let fname_bytes = fname.into_bytes();
        let mut node = BuildNode {
            id,
            parent_id,
            rel_path: format!("/{rel}"),
            kind,
            mode: default_mode,
            uid: 0,
            gid: 0,
            mtime,
            size: if ft.is_file() { meta.len() } else { 0 },
            nlink: 1,
            xattrs: Vec::new(),
        };
        apply_android_attrs(&mut node, opts, effective_mp, fs_cfg, file_ctx, mode_ch);
        nodes.push(node);
        child_ids.push((fname_bytes, id));

        if ft.is_dir() {
            scan_dir_recursive(
                &path,
                id,
                &rel,
                opts,
                effective_mp,
                fs_cfg,
                file_ctx,
                excludes,
                default_epoch,
                hardlink_map,
                nodes,
            )?;
        }
    }

    if let NodeKind::Dir { children } = &mut nodes[parent_id].kind {
        *children = child_ids;
    }
    Ok(())
}

fn try_read_cygwin_symlink(path: &Path, meta: &fs::Metadata) -> Option<Vec<u8>> {
    let len = meta.len();
    if !(12..=8192).contains(&len) {
        return None;
    }
    let raw = fs::read(path).ok()?;
    if raw.starts_with(b"!<symlink>\xFF\xFE") && raw.len() >= 14 {
        let body = &raw[12..];
        let mut u16s = Vec::with_capacity(body.len() / 2);
        for chunk in body.chunks_exact(2) {
            let code = u16::from_le_bytes([chunk[0], chunk[1]]);
            if code == 0 {
                break;
            }
            u16s.push(code);
        }
        let target = String::from_utf16(&u16s).ok()?;
        return Some(target.into_bytes());
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_SYSTEM: u32 = 0x0000_0004;
        if (meta.file_attributes() & FILE_ATTRIBUTE_SYSTEM) != 0
            && raw.starts_with(b"!<symlink>")
            && raw.len() > 11
            && raw.last() == Some(&0)
        {
            let body = &raw[10..];
            let end = body.iter().position(|&b| b == 0).unwrap_or(body.len());
            return Some(body[..end].to_vec());
        }
    }
    None
}

#[cfg(unix)]
fn host_hardlink_key(_path: &Path, meta: &fs::Metadata) -> Option<(u64, u64)> {
    use std::os::unix::fs::MetadataExt;
    if meta.nlink() > 1 {
        Some((meta.dev(), meta.ino()))
    } else {
        None
    }
}

#[cfg(windows)]
fn host_hardlink_key(path: &Path, _meta: &fs::Metadata) -> Option<(u64, u64)> {
    use core::ffi::c_void;
    use std::os::windows::io::AsRawHandle;

    #[repr(C)]
    struct FileTime {
        dw_low_date_time: u32,
        dw_high_date_time: u32,
    }

    #[repr(C)]
    struct ByHandleFileInformation {
        dw_file_attributes: u32,
        ft_creation_time: FileTime,
        ft_last_access_time: FileTime,
        ft_last_write_time: FileTime,
        dw_volume_serial_number: u32,
        n_file_size_high: u32,
        n_file_size_low: u32,
        n_number_of_links: u32,
        n_file_index_high: u32,
        n_file_index_low: u32,
    }

    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn GetFileInformationByHandle(
            file: *mut c_void,
            info: *mut ByHandleFileInformation,
        ) -> i32;
    }

    let file = File::open(path).ok()?;
    let mut info = core::mem::MaybeUninit::<ByHandleFileInformation>::zeroed();
    let ok = unsafe { GetFileInformationByHandle(file.as_raw_handle(), info.as_mut_ptr()) };
    if ok == 0 {
        return None;
    }
    let info = unsafe { info.assume_init() };
    if info.n_number_of_links > 1 {
        let idx = ((info.n_file_index_high as u64) << 32) | (info.n_file_index_low as u64);
        Some((info.dw_volume_serial_number as u64, idx))
    } else {
        None
    }
}

fn apply_android_attrs(
    node: &mut BuildNode,
    opts: &PackOptions,
    mount_point: &str,
    fs_cfg: Option<&FsConfigTable>,
    file_ctx: Option<&FileContextsMatcher>,
    mode_char: char,
) {
    if let Some(cfg) = fs_cfg {
        if let Some(e) = cfg.lookup(&node.rel_path, mount_point) {
            node.uid = e.uid;
            node.gid = e.gid;
            node.mode = (node.mode & 0o170000) | (e.mode & 0o7777);
            if e.capabilities != 0 {
                let cap_val = encode_vfs_capability_v2(e.capabilities);
                node.xattrs.push(RawXattr {
                    name_index: XATTR_INDEX_SECURITY,
                    name: b"capability".to_vec(),
                    value: cap_val.to_vec(),
                });
            }
        }
    }

    if let Some(ctx_matcher) = file_ctx {
        if let Some(label) = ctx_matcher.lookup(&node.rel_path, mode_char, mount_point) {
            if let Some((idx, suffix)) = split_xattr_name("security.selinux") {
                node.xattrs.push(RawXattr {
                    name_index: idx,
                    name: suffix.as_bytes().to_vec(),
                    value: label.as_bytes().to_vec(),
                });
            }
        }
    }

    if opts.all_root {
        node.uid = 0;
        node.gid = 0;
    } else {
        if let Some(u) = opts.force_uid {
            node.uid = u;
        }
        if let Some(g) = opts.force_gid {
            node.gid = g;
        }
        node.uid = node.uid.saturating_add(opts.uid_offset);
        node.gid = node.gid.saturating_add(opts.gid_offset);
    }
}

fn build_xattr_ibody(
    xattrs: &[RawXattr],
    shared_ids: &HashMap<RawXattr, u32>,
) -> Result<(Vec<u8>, u16)> {
    if xattrs.is_empty() {
        return Ok((Vec::new(), 0));
    }
    let mut shared = Vec::new();
    let mut inline = Vec::new();
    let mut name_filter: u32 = u32::MAX; // 1 = absent, 0 = present

    for xa in xattrs {
        name_filter &= !xattr_name_filter_bit(xa.name_index, &xa.name);
        if let Some(&sid) = shared_ids.get(xa) {
            shared.push(sid);
        } else {
            inline.push(xa);
        }
    }

    let mut body = Vec::new();
    // erofs_xattr_ibody_header (12 bytes)
    body.extend_from_slice(&name_filter.to_le_bytes());
    body.push(shared.len() as u8);
    body.extend_from_slice(&[0u8; 7]);

    for sid in &shared {
        body.extend_from_slice(&sid.to_le_bytes());
    }
    for xa in inline {
        let enc = encode_raw_xattr_entry(xa.name_index, &xa.name, &xa.value)?;
        body.extend_from_slice(&enc);
    }

    let aligned = align_up_usize(body.len(), 4);
    body.resize(aligned, 0);
    let icount = ((body.len() - 12) / 4 + 1) as u16;
    Ok((body, icount))
}

fn compress_file_into_pclusters(
    data: &[u8],
    alg: CompressionAlg,
    level: Option<i32>,
    pcluster_size: usize,
    bs: usize,
) -> Result<Vec<CompressedChunk>> {
    let mut chunks = Vec::new();
    let mut pos = 0usize;
    while pos < data.len() {
        let rem = &data[pos..];
        if rem.len() <= bs {
            // Tail fits in a single uncompressed block
            let mut blk = vec![0u8; bs];
            blk[..rem.len()].copy_from_slice(rem);
            chunks.push(CompressedChunk {
                raw_len: rem.len(),
                is_compressed: false,
                blocks_data: blk,
                cblks: 1,
            });
            pos += rem.len();
            continue;
        }
        if let Some((consumed, padded)) =
            compress_fixed_output(alg, level, rem, pcluster_size, bs, true)?
        {
            let cblks = padded.len() / bs;
            chunks.push(CompressedChunk {
                raw_len: consumed,
                is_compressed: true,
                blocks_data: padded,
                cblks,
            });
            pos += consumed;
        } else {
            // Store 1 block uncompressed (keeps 4B compact index accounting 100% exact).
            let take = rem.len().min(bs);
            let mut blk = vec![0u8; bs];
            blk[..take].copy_from_slice(&rem[..take]);
            chunks.push(CompressedChunk {
                raw_len: take,
                is_compressed: false,
                blocks_data: blk,
                cblks: 1,
            });
            pos += take;
        }
    }
    Ok(chunks)
}

/// Encode `z_erofs_map_header` + 4B compact lcluster index packs (`vcnt = 2`, 8 bytes/pack).
fn encode_compact_4b_zmap(
    chunks: &[CompressedChunk],
    total_size: u64,
    bs: usize,
    first_blk: u32,
    alg_id: u8,
    big_pcluster: bool,
) -> Result<Vec<u8>> {
    let total_lcn = div_round_up(total_size, bs as u64) as usize;

    #[derive(Clone, Copy)]
    struct LcInfo {
        ltype: u8,
        clusterofs: u16,
        d0: u16,
        d1: u16,
        pblk: u32,
        cblks: u16,
    }

    let mut lcs = vec![
        LcInfo {
            ltype: Z_LCLUSTER_NONHEAD,
            clusterofs: 0,
            d0: 0,
            d1: 0,
            pblk: first_blk,
            cblks: 0,
        };
        total_lcn
    ];

    // Record every chunk's HEAD lcluster at its starting logical cluster.
    let mut cur_la = 0usize;
    let mut cur_blk = first_blk;
    let mut head_positions: Vec<(usize, u16, u32, u8, u16)> = Vec::new();
    for ch in chunks {
        let lcn = cur_la / bs;
        let ofs = (cur_la % bs) as u16;
        let ltype = if ch.is_compressed {
            Z_LCLUSTER_HEAD1
        } else {
            Z_LCLUSTER_PLAIN
        };
        head_positions.push((lcn, ofs, cur_blk, ltype, ch.cblks as u16));
        cur_la += ch.raw_len;
        cur_blk += ch.cblks as u32;
    }

    // Fill HEAD entries
    for &(lcn, ofs, pblk, ltype, cblks) in &head_positions {
        lcs[lcn] = LcInfo {
            ltype,
            clusterofs: ofs,
            d0: 0,
            d1: 0,
            pblk,
            cblks,
        };
    }

    // Fill NONHEAD entries between heads with exact delta0 and delta1
    for idx in 0..head_positions.len() {
        let (h_lcn, _, h_pblk, _, h_cblks) = head_positions[idx];
        let next_h_lcn = if idx + 1 < head_positions.len() {
            head_positions[idx + 1].0
        } else {
            total_lcn
        };
        for lcn in (h_lcn + 1)..next_h_lcn {
            let d0 = (lcn - h_lcn) as u16;
            let d1 = (next_h_lcn - lcn) as u16;
            lcs[lcn] = LcInfo {
                ltype: Z_LCLUSTER_NONHEAD,
                clusterofs: 0,
                d0,
                d1,
                pblk: h_pblk,
                cblks: if d0 == 1 { h_cblks } else { 0 },
            };
        }
    }

    let lclusterbits = bs.trailing_zeros();
    let lobits = lclusterbits.max(12);
    let lo_mask = ((1u32 << lobits) - 1) as u16;

    let mut out = Vec::new();
    // z_erofs_map_header (8 bytes)
    let advise: u16 = if big_pcluster {
        Z_ADVISE_BIG_PCLUSTER_1 | Z_ADVISE_BIG_PCLUSTER_2
    } else {
        0
    };
    out.extend_from_slice(&0u32.to_le_bytes()); // h_reserved / h_idata_size = 0
    out.extend_from_slice(&advise.to_le_bytes());
    out.push(alg_id & 0x0F); // h_algorithmtype (head1)
    out.push(0u8); // h_clusterbits = 0 (lcluster == block_size)

    // Emit 4B packs (vcnt = 2, 8 bytes per pack: u16 e0, u16 e1, u32 base_blk)
    let mut p = 0usize;
    let mut last_blk = first_blk;
    while p < total_lcn {
        let e0 = lcs[p];
        let has_e1 = p + 1 < total_lcn;
        let e1 = if has_e1 {
            lcs[p + 1]
        } else {
            LcInfo {
                ltype: Z_LCLUSTER_PLAIN,
                clusterofs: 0,
                d0: 0,
                d1: 0,
                pblk: last_blk,
                cblks: 1,
            }
        };

        let encode_slot = |lc: LcInfo, is_last_in_pack: bool| -> u16 {
            let lo = if lc.ltype == Z_LCLUSTER_NONHEAD {
                if big_pcluster && lc.d0 == 1 && lc.cblks > 0 {
                    Z_LI_D0_CBLKCNT | (lc.cblks & 0x07FF)
                } else if is_last_in_pack {
                    lc.d1 & lo_mask
                } else {
                    lc.d0 & lo_mask
                }
            } else {
                lc.clusterofs & lo_mask
            };
            lo | ((lc.ltype as u16) << lobits)
        };

        let w0 = encode_slot(e0, false);
        let w1 = if has_e1 { encode_slot(e1, true) } else { 0 };

        // Compute base_blk so decoder recovers exact pblk for any HEAD in slot 0 or slot 1.
        let base_blk: u32 = if !big_pcluster {
            if e0.ltype != Z_LCLUSTER_NONHEAD {
                last_blk = e0.pblk;
                e0.pblk.wrapping_sub(1)
            } else if has_e1 && e1.ltype != Z_LCLUSTER_NONHEAD {
                last_blk = e1.pblk;
                e1.pblk.wrapping_sub(1)
            } else {
                last_blk
            }
        } else if e0.ltype != Z_LCLUSTER_NONHEAD {
            last_blk = e0.pblk;
            e0.pblk
        } else if has_e1 && e1.ltype != Z_LCLUSTER_NONHEAD {
            last_blk = e1.pblk;
            // In big_pcluster, if slot 0 is NONHEAD with CBLKCNT, decoder adds cblks to base_blk.
            if e0.d0 == 1 && e0.cblks > 0 {
                e1.pblk.wrapping_sub(e0.cblks as u32)
            } else {
                e1.pblk
            }
        } else {
            last_blk
        };

        out.extend_from_slice(&w0.to_le_bytes());
        out.extend_from_slice(&w1.to_le_bytes());
        out.extend_from_slice(&base_blk.to_le_bytes());
        p += 2;
    }

    Ok(out)
}

fn encode_compr_cfg(alg_id: u8, max_pcluster_blks: u16) -> Vec<u8> {
    let mut out = Vec::new();
    match alg_id {
        COMPR_ALG_LZ4 => {
            out.extend_from_slice(&14u16.to_le_bytes());
            out.extend_from_slice(&65535u16.to_le_bytes()); // max_distance
            out.extend_from_slice(&max_pcluster_blks.to_le_bytes());
            out.extend_from_slice(&[0u8; 10]);
        }
        COMPR_ALG_LZMA => {
            out.extend_from_slice(&14u16.to_le_bytes());
            out.extend_from_slice(&(1024 * 1024u32).to_le_bytes()); // dict_size 1 MiB
            out.extend_from_slice(&0u16.to_le_bytes()); // format
            out.extend_from_slice(&[0u8; 8]);
        }
        COMPR_ALG_DEFLATE => {
            out.extend_from_slice(&6u16.to_le_bytes());
            out.push(15u8); // windowbits
            out.extend_from_slice(&[0u8; 5]);
        }
        COMPR_ALG_ZSTD => {
            out.extend_from_slice(&6u16.to_le_bytes());
            out.push(0u8); // format
            out.push(10u8); // windowlog delta (10 + 10 = 20 -> 1 MiB)
            out.extend_from_slice(&[0u8; 4]);
        }
        _ => {}
    }
    out
}

fn resolve_uuid(spec: Option<&str>) -> Result<[u8; 16]> {
    match spec {
        None | Some("random") => {
            let now = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0);
            let pid = std::process::id() as u128;
            let mut state = now ^ (pid << 64) ^ 0x9E37_79B9_7F4A_7C15_D1B5_4A32_D192_ED03u128;
            // Simple SplitMix64-style mixing for deterministic-free v4 UUID without extra crates.
            let mix = |s: u64| -> u64 {
                let mut z = s.wrapping_add(0x9E37_79B9_7F4A_7C15);
                z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
                z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
                z ^ (z >> 31)
            };
            let hi = mix(state as u64);
            state >>= 64;
            let lo = mix(state as u64 ^ hi);
            let mut u = [0u8; 16];
            u[..8].copy_from_slice(&hi.to_le_bytes());
            u[8..].copy_from_slice(&lo.to_le_bytes());
            u[6] = (u[6] & 0x0F) | 0x40; // UUID v4
            u[8] = (u[8] & 0x3F) | 0x80; // RFC 4122 variant
            Ok(u)
        }
        Some("clear") => Ok([0u8; 16]),
        Some(s) => parse_uuid(s),
    }
}
