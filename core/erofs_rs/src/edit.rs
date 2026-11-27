// SPDX-License-Identifier: MIT

//! Direct in-place & append EROFS image editor (without unpacking):
//! write file data, change permissions, edit inline SELinux context, unlink entries,
//! and compute detailed space usage reports.

use crate::avb::{AvbFooter, read_avb_footer, strip_avb_footer};
use crate::crc32c::erofs_superblock_crc;
use crate::error::{ErofsError, Result};
use crate::format::{
    DataLayout, EROFS_NULL_ADDR_32, EROFS_NULL_ADDR_48, EROFS_SUPER_MAX_SIZE, EROFS_SUPER_OFFSET,
    INODE_DATALAYOUT_MASK, INODE_DATALAYOUT_SHIFT, INODE_NLINK_1_BIT, Inode, S_IFDIR, S_IFMT,
    S_IFREG, SuperBlock, XATTR_INDEX_SECURITY, div_round_up, read_u16_le,
};
use crate::reader::{ErofsImage, build_dirent_blocks, parse_dirent_blocks};
use crate::sparse::is_sparse_file;
use crate::zmap::compressed_meta_end;
use serde::Serialize;
use std::collections::HashSet;
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;

const SB_CHECKSUM_OFF: u64 = 4;
const SB_RB_OFF: u64 = 14;
const SB_BLOCKS_LO_OFF: u64 = 36;

const I_FORMAT_OFF: u64 = 0;
const I_MODE_OFF: u64 = 4;
const I_NB_OFF: u64 = 6;
const I_SIZE_OFF: u64 = 8;
const I_U_OFF: u64 = 16;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum WriteMode {
    InplacePlain,
    InplaceInline,
    Append,
}

#[derive(Debug, Clone, Serialize)]
pub struct WritePlan {
    pub mode: WriteMode,
    pub old_size: u64,
    pub new_size: u64,
    pub grow_bytes: u64,
    pub detail: String,
    pub avb_strip: Option<AvbFooter>,
}

#[derive(Debug, Clone, Serialize)]
pub struct WriteResult {
    pub path: String,
    pub mode: WriteMode,
    pub old_size: u64,
    pub new_size: u64,
    pub grew_bytes: u64,
    pub detail: String,
    pub checksum: Option<u32>,
    pub avb_stripped: Option<AvbFooter>,
}

#[derive(Debug, Clone, Serialize)]
pub struct UnlinkResult {
    pub removed: String,
    pub removed_nid: u64,
    pub was_dir: bool,
    pub entries_before: usize,
    pub entries_after: usize,
    pub mode: WriteMode,
    pub grew_bytes: u64,
    pub checksum: Option<u32>,
}

#[derive(Debug, Clone, Serialize)]
pub struct UsageReport {
    pub fs: String,
    pub block_size: u32,
    pub file_size: u64,
    pub total_blocks: u64,
    pub data_blocks: u64,
    pub data_bytes: u64,
    pub meta_blocks: u64,
    pub meta_bytes: u64,
    pub wasted_blocks: u64,
    pub wasted_bytes: u64,
    pub reclaimable_bytes: u64,
    pub logical_bytes: u64,
    pub files: usize,
    pub dirs: usize,
    pub symlinks: usize,
    pub is_sparse: bool,
}

pub(crate) fn open_rw(path: &Path) -> Result<File> {
    if is_sparse_file(path) {
        return Err(ErofsError::Edit(
            "direct in-place editing is not supported on Android Sparse images (unsparse first)"
                .into(),
        ));
    }
    Ok(OpenOptions::new().read(true).write(true).open(path)?)
}

pub fn read_sb_file(f: &mut File) -> Result<SuperBlock> {
    f.seek(SeekFrom::Start(EROFS_SUPER_OFFSET))?;
    let mut raw = vec![0u8; EROFS_SUPER_MAX_SIZE];
    let n = f.read(&mut raw)?;
    raw.truncate(n);
    SuperBlock::parse(&raw)
}

pub(crate) fn read_inode_file(f: &mut File, sb: &SuperBlock, nid: u64) -> Result<Inode> {
    if (nid & crate::format::EROFS_NID_METABOX_BIT) != 0 {
        return Err(ErofsError::Edit(
            "inode resides inside METABOX; direct in-place edit is not supported".into(),
        ));
    }
    let off = sb.nid_to_offset(nid);
    f.seek(SeekFrom::Start(off))?;
    let mut hdr = vec![0u8; 64];
    let n = f.read(&mut hdr)?;
    hdr.truncate(n);
    Inode::parse(sb, nid, &hdr)
}

pub(crate) fn set_total_blocks(f: &mut File, sb: &mut SuperBlock, nblocks: u64) -> Result<()> {
    if (nblocks >> 32) != 0 && !sb.has_48bit() {
        return Err(ErofsError::Edit(
            "block count exceeds 32 bits on non-48BIT image".into(),
        ));
    }
    f.seek(SeekFrom::Start(EROFS_SUPER_OFFSET + SB_BLOCKS_LO_OFF))?;
    f.write_all(&((nblocks & 0xFFFF_FFFF) as u32).to_le_bytes())?;
    if sb.has_48bit() {
        f.seek(SeekFrom::Start(EROFS_SUPER_OFFSET + SB_RB_OFF))?;
        f.write_all(&(((nblocks >> 32) & 0xFFFF) as u16).to_le_bytes())?;
    }
    sb.blocks = nblocks;
    Ok(())
}

pub(crate) fn refresh_superblock_checksum(f: &mut File, sb: &mut SuperBlock) -> Result<Option<u32>> {
    if !sb.has_sb_chksum() {
        return Ok(None);
    }
    let bs = sb.block_size as u64;
    let len = if bs > EROFS_SUPER_OFFSET {
        (bs - EROFS_SUPER_OFFSET) as usize
    } else {
        bs as usize
    };
    f.seek(SeekFrom::Start(EROFS_SUPER_OFFSET))?;
    let mut buf = vec![0u8; len];
    let n = f.read(&mut buf)?;
    if n < len {
        buf[n..].fill(0);
    }
    let crc = erofs_superblock_crc(&buf);
    f.seek(SeekFrom::Start(EROFS_SUPER_OFFSET + SB_CHECKSUM_OFF))?;
    f.write_all(&crc.to_le_bytes())?;
    sb.checksum = crc;
    Ok(Some(crc))
}

pub(crate) fn write_inode_fields(
    f: &mut File,
    inode: &mut Inode,
    layout: Option<DataLayout>,
    size: Option<u64>,
    start_blk: Option<u64>,
) -> Result<()> {
    let off = inode.offset;

    if let Some(lay) = layout {
        let mut fmt = inode.format;
        fmt = (fmt & !(INODE_DATALAYOUT_MASK << INODE_DATALAYOUT_SHIFT))
            | (((lay as u16) & INODE_DATALAYOUT_MASK) << INODE_DATALAYOUT_SHIFT);
        f.seek(SeekFrom::Start(off + I_FORMAT_OFF))?;
        f.write_all(&fmt.to_le_bytes())?;
        inode.format = fmt;
        inode.layout = lay;
    }

    if let Some(sz) = size {
        f.seek(SeekFrom::Start(off + I_SIZE_OFF))?;
        if inode.is_extended {
            f.write_all(&sz.to_le_bytes())?;
        } else {
            if sz > u32::MAX as u64 {
                return Err(ErofsError::Edit(
                    "file size > 4 GiB does not fit in compact inode".into(),
                ));
            }
            f.write_all(&(sz as u32).to_le_bytes())?;
        }
        inode.size = sz;
    }

    if let Some(sbk) = start_blk {
        if sbk > EROFS_NULL_ADDR_48 {
            return Err(ErofsError::Edit(
                "start block exceeds 48-bit EROFS limit".into(),
            ));
        }
        let hi = ((sbk >> 32) & 0xFFFF) as u16;
        if hi != 0 {
            if inode.is_extended {
                f.seek(SeekFrom::Start(off + I_NB_OFF))?;
                f.write_all(&hi.to_le_bytes())?;
            } else {
                if (inode.format & INODE_NLINK_1_BIT) == 0 {
                    if inode.nb_raw != 1 {
                        return Err(ErofsError::Edit(
                            "compact inode with hardlink (nlink != 1) cannot store 48-bit startblk"
                                .into(),
                        ));
                    }
                    let fmt = inode.format | INODE_NLINK_1_BIT;
                    f.seek(SeekFrom::Start(off + I_FORMAT_OFF))?;
                    f.write_all(&fmt.to_le_bytes())?;
                    inode.format = fmt;
                }
                f.seek(SeekFrom::Start(off + I_NB_OFF))?;
                f.write_all(&hi.to_le_bytes())?;
            }
        } else if inode.is_extended || (inode.format & INODE_NLINK_1_BIT) != 0 {
            f.seek(SeekFrom::Start(off + I_NB_OFF))?;
            f.write_all(&0u16.to_le_bytes())?;
        }

        f.seek(SeekFrom::Start(off + I_U_OFF))?;
        f.write_all(&((sbk & 0xFFFF_FFFF) as u32).to_le_bytes())?;
        inode.u_raw = (sbk & 0xFFFF_FFFF) as u32;
        inode.start_blk = sbk;
    }

    Ok(())
}

/// Plan the optimal write strategy (`inplace_plain`, `inplace_inline`, or `append`) for an inode.
pub fn plan_inode_write(
    f: &mut File,
    sb: &SuperBlock,
    inode: &Inode,
    new_len: u64,
    strip_avb: bool,
) -> Result<WritePlan> {
    let bs = sb.block_size as u64;

    if inode.layout == DataLayout::ChunkBased {
        return Err(ErofsError::Edit(
            "chunk-based inode (layout 4) is not supported for direct write".into(),
        ));
    }

    // 1. In-place FLAT_PLAIN
    if inode.layout == DataLayout::FlatPlain {
        let cap = div_round_up(inode.size, bs) * bs;
        if new_len <= cap && (!inode.is_null_start() || new_len == 0) {
            return Ok(WritePlan {
                mode: WriteMode::InplacePlain,
                old_size: inode.size,
                new_size: new_len,
                grow_bytes: 0,
                detail: format!("in-place {new_len}/{cap}B at block {}", inode.start_blk),
                avb_strip: None,
            });
        }
    }

    // 2. In-place FLAT_INLINE
    if inode.layout == DataLayout::FlatInline {
        let old_full = inode.size / bs;
        let old_tail = inode.size % bs;
        let new_full = new_len / bs;
        let new_tail = new_len % bs;
        if new_full == old_full && new_tail <= old_tail && (old_full == 0 || !inode.is_null_start())
        {
            return Ok(WritePlan {
                mode: WriteMode::InplaceInline,
                old_size: inode.size,
                new_size: new_len,
                grow_bytes: 0,
                detail: format!(
                    "in-place inline: {new_full} full block(s) + tail {new_tail}/{old_tail}B"
                ),
                avb_strip: None,
            });
        }
    }

    // 3. Append at end of image
    if sb.has_fragments() {
        return Err(ErofsError::Edit(
            "image uses FRAGMENTS/DEDUPE; append path is disabled for safety".into(),
        ));
    }
    if sb.has_metabox() {
        return Err(ErofsError::Edit(
            "image uses METABOX; append path is disabled".into(),
        ));
    }

    let avb_info = read_avb_footer(f)?;
    if avb_info.is_some() && !strip_avb {
        return Err(ErofsError::Edit(
            "image has an AVB footer; appending would write past vbmeta (pass --strip-avb or keep size within existing blocks)".into(),
        ));
    }

    let nblocks = div_round_up(new_len, bs);
    let grow = nblocks * bs;
    let mut detail = format!(
        "append {nblocks} block(s) ({grow}B) at end of image and repoint inode to FLAT_PLAIN"
    );
    if let Some(ref avb) = avb_info {
        detail.push_str(&format!(
            " (AVB footer will be stripped: {}B -> raw)",
            avb.original_image_size
        ));
    }

    Ok(WritePlan {
        mode: WriteMode::Append,
        old_size: inode.size,
        new_size: new_len,
        grow_bytes: grow,
        detail,
        avb_strip: avb_info,
    })
}

fn do_inplace_plain(f: &mut File, sb: &SuperBlock, inode: &mut Inode, data: &[u8]) -> Result<()> {
    let bs = sb.block_size as u64;
    if !data.is_empty() {
        f.seek(SeekFrom::Start(inode.start_blk * bs))?;
        f.write_all(data)?;
        let rem = (bs - (data.len() as u64 % bs)) % bs;
        if rem > 0 {
            f.write_all(&vec![0u8; rem as usize])?;
        }
    }
    write_inode_fields(f, inode, None, Some(data.len() as u64), None)
}

fn do_inplace_inline(f: &mut File, sb: &SuperBlock, inode: &mut Inode, data: &[u8]) -> Result<()> {
    let bs = sb.block_size as usize;
    let old_tail = (inode.size as usize) % bs;
    let new_full = data.len() / bs;
    let new_tail = data.len() % bs;

    if new_full > 0 {
        f.seek(SeekFrom::Start(inode.start_blk * (bs as u64)))?;
        f.write_all(&data[..new_full * bs])?;
    }
    f.seek(SeekFrom::Start(inode.data_offset))?;
    f.write_all(&data[new_full * bs..])?;
    if old_tail > new_tail {
        f.write_all(&vec![0u8; old_tail - new_tail])?;
    }
    write_inode_fields(f, inode, None, Some(data.len() as u64), None)
}

fn do_append(f: &mut File, sb: &mut SuperBlock, inode: &mut Inode, data: &[u8]) -> Result<()> {
    let bs = sb.block_size as u64;
    let end = f.seek(SeekFrom::End(0))?;
    let mut start_blk = div_round_up(end, bs);
    if start_blk < sb.blocks {
        start_blk = sb.blocks;
    }
    let start_off = start_blk * bs;
    if start_off > end {
        f.seek(SeekFrom::Start(end))?;
        f.write_all(&vec![0u8; (start_off - end) as usize])?;
    }

    f.seek(SeekFrom::Start(start_off))?;
    f.write_all(data)?;
    let rem = (bs - (data.len() as u64 % bs)) % bs;
    if rem > 0 {
        f.write_all(&vec![0u8; rem as usize])?;
    }

    let nblocks = div_round_up(data.len() as u64, bs);
    let null_blk = if inode.addr_mask == EROFS_NULL_ADDR_48 {
        EROFS_NULL_ADDR_48
    } else {
        EROFS_NULL_ADDR_32 as u64
    };
    let target_blk = if nblocks > 0 { start_blk } else { null_blk };

    write_inode_fields(
        f,
        inode,
        Some(DataLayout::FlatPlain),
        Some(data.len() as u64),
        Some(target_blk),
    )?;

    let new_total = start_blk + nblocks;
    if new_total > sb.blocks {
        set_total_blocks(f, sb, new_total)?;
    }
    Ok(())
}

/// Plan writing `new_len` bytes to `internal_path` inside `image_path` without modifying disk.
pub fn plan_path_write(
    image_path: &Path,
    internal_path: &str,
    new_len: u64,
    strip_avb: bool,
) -> Result<WritePlan> {
    let img = ErofsImage::open(image_path, 0)?;
    let target = img.lookup(internal_path)?;
    if !target.is_reg() {
        return Err(ErofsError::Edit(format!(
            "'{internal_path}' is not a regular file"
        )));
    }
    let mut f = open_rw(image_path)?;
    let sb = read_sb_file(&mut f)?;
    let inode = read_inode_file(&mut f, &sb, target.nid)?;
    plan_inode_write(&mut f, &sb, &inode, new_len, strip_avb)
}

/// Directly overwrite a regular file inside a raw EROFS image without unpacking.
pub fn write_path_data(
    image_path: &Path,
    internal_path: &str,
    data: &[u8],
    allow_grow: bool,
    strip_avb: bool,
) -> Result<WriteResult> {
    let img = ErofsImage::open(image_path, 0)?;
    let target = img.lookup(internal_path)?;
    drop(img);

    if (target.mode & S_IFMT) != S_IFREG {
        return Err(ErofsError::Edit(format!(
            "'{internal_path}' is not a regular file"
        )));
    }

    let mut f = open_rw(image_path)?;
    let mut sb = read_sb_file(&mut f)?;
    let mut inode = read_inode_file(&mut f, &sb, target.nid)?;
    let plan = plan_inode_write(&mut f, &sb, &inode, data.len() as u64, strip_avb)?;

    if plan.mode == WriteMode::Append && !allow_grow {
        return Err(ErofsError::Edit(
            "new content does not fit in-place and growing the image is disabled".into(),
        ));
    }

    let old_size = inode.size;
    let mut avb_stripped = None;
    match plan.mode {
        WriteMode::InplacePlain => do_inplace_plain(&mut f, &sb, &mut inode, data)?,
        WriteMode::InplaceInline => do_inplace_inline(&mut f, &sb, &mut inode, data)?,
        WriteMode::Append => {
            if plan.avb_strip.is_some() {
                avb_stripped = Some(strip_avb_footer(&mut f, &sb)?);
            }
            do_append(&mut f, &mut sb, &mut inode, data)?;
        }
    }

    let checksum = refresh_superblock_checksum(&mut f, &mut sb)?;
    f.flush()?;
    let _ = f.sync_all();

    Ok(WriteResult {
        path: internal_path.to_string(),
        mode: plan.mode,
        old_size,
        new_size: data.len() as u64,
        grew_bytes: plan.grow_bytes,
        detail: plan.detail,
        checksum,
        avb_stripped,
    })
}

/// Directly modify Unix permission bits (`0o0000..=0o7777`) of an inode in-place.
pub fn write_path_mode(image_path: &Path, internal_path: &str, perm_octal: u16) -> Result<u16> {
    let img = ErofsImage::open(image_path, 0)?;
    let target = img.lookup(internal_path)?;
    drop(img);

    let mut f = open_rw(image_path)?;
    let mut sb = read_sb_file(&mut f)?;
    let inode = read_inode_file(&mut f, &sb, target.nid)?;
    let new_mode = (inode.mode & !0o7777) | (perm_octal & 0o7777);

    f.seek(SeekFrom::Start(inode.offset + I_MODE_OFF))?;
    f.write_all(&new_mode.to_le_bytes())?;
    refresh_superblock_checksum(&mut f, &mut sb)?;
    f.flush()?;
    let _ = f.sync_all();
    Ok(new_mode)
}

/// Directly modify an inline `security.selinux` xattr value in-place (within existing slot size).
pub fn write_path_selinux(image_path: &Path, internal_path: &str, new_ctx: &str) -> Result<String> {
    let img = ErofsImage::open(image_path, 0)?;
    let target = img.lookup(internal_path)?;
    drop(img);

    let mut f = open_rw(image_path)?;
    let mut sb = read_sb_file(&mut f)?;
    let inode = read_inode_file(&mut f, &sb, target.nid)?;
    if inode.xattr_icount == 0 || inode.xattr_size < 12 {
        return Err(ErofsError::Edit("inode has no xattrs".into()));
    }

    f.seek(SeekFrom::Start(inode.xattr_offset))?;
    let mut area = vec![0u8; inode.xattr_size as usize];
    f.read_exact(&mut area)?;

    let shared_count = area[4] as usize;
    let mut pos = 12 + shared_count * 4;
    let mut found = None;
    while pos + 4 <= area.len() {
        let name_len = area[pos] as usize;
        let name_index = area[pos + 1];
        let val_size = read_u16_le(&area, pos + 2) as usize;
        if name_len == 0 && name_index == 0 && val_size == 0 {
            break;
        }
        let vstart = pos + 4 + name_len;
        if vstart + val_size > area.len() {
            break;
        }
        if name_index == XATTR_INDEX_SECURITY && &area[pos + 4..vstart] == b"selinux" {
            found = Some((pos, vstart, val_size));
            break;
        }
        pos = (vstart + val_size + 3) & !3;
    }

    let Some((entry_pos, vstart, old_val_size)) = found else {
        if shared_count > 0 {
            return Err(ErofsError::Edit(
                "security.selinux is stored in the shared xattr area (shared across inodes); changing it requires repack".into(),
            ));
        }
        return Err(ErofsError::Edit(
            "inline security.selinux entry not found".into(),
        ));
    };

    let new_bytes = new_ctx.as_bytes();
    if new_bytes.len() > old_val_size {
        return Err(ErofsError::Edit(format!(
            "new SELinux context ({}B) exceeds existing inline slot ({old_val_size}B); repack required",
            new_bytes.len()
        )));
    }
    let old_next = (vstart + old_val_size + 3) & !3;
    let new_next = (vstart + new_bytes.len() + 3) & !3;
    if new_next != old_next
        && old_next + 4 <= area.len()
        && (area[old_next] != 0
            || area[old_next + 1] != 0
            || read_u16_le(&area, old_next + 2) != 0)
    {
        return Err(ErofsError::Edit(
            "cannot shrink inline security.selinux when another inline xattr follows (would misalign trailing xattrs); repack required"
                .into(),
        ));
    }

    let mut padded = vec![0u8; old_val_size];
    padded[..new_bytes.len()].copy_from_slice(new_bytes);

    f.seek(SeekFrom::Start(inode.xattr_offset + vstart as u64))?;
    f.write_all(&padded)?;
    f.seek(SeekFrom::Start(inode.xattr_offset + entry_pos as u64 + 2))?;
    f.write_all(&(new_bytes.len() as u16).to_le_bytes())?;

    refresh_superblock_checksum(&mut f, &mut sb)?;
    f.flush()?;
    let _ = f.sync_all();
    Ok(new_ctx.to_string())
}

/// Unlink a file or directory entry directly from its parent directory without unpacking.
pub fn unlink_path(
    image_path: &Path,
    internal_path: &str,
    recursive: bool,
    strip_avb: bool,
) -> Result<UnlinkResult> {
    let clean = internal_path.replace('\\', "/");
    let clean = clean.trim_matches('/');
    if clean.is_empty() {
        return Err(ErofsError::Edit("cannot remove filesystem root".into()));
    }

    let img = ErofsImage::open(image_path, 0)?;
    let target = img.lookup(clean)?;
    let (parent_path, name) = match clean.rsplit_once('/') {
        Some((p, n)) => (p, n),
        None => ("", clean),
    };
    if name == "." || name == ".." {
        return Err(ErofsError::Edit("cannot remove '.' or '..'".into()));
    }
    let parent = img.lookup(parent_path)?;

    if target.is_dir() && !recursive {
        let kids = img.read_dir(&target, false)?;
        if !kids.is_empty() {
            return Err(ErofsError::Edit(format!(
                "directory '{clean}' is not empty (pass --recursive to unlink)"
            )));
        }
    }

    let parent_raw = img.read_inode_bytes(&parent)?;
    let dir_bs = img.sb.dir_block_size();
    let entries = parse_dirent_blocks(&parent_raw, dir_bs, true)?;
    drop(img);

    let name_bytes = name.as_bytes();
    let mut kept = Vec::with_capacity(entries.len());
    let mut removed_nid = None;
    for e in &entries {
        if e.raw_name == name_bytes {
            removed_nid = Some(e.nid);
        } else {
            kept.push((e.nid, e.raw_name.clone(), e.file_type));
        }
    }
    let removed_nid = removed_nid.ok_or_else(|| ErofsError::NotFound(clean.to_string()))?;

    let new_dir_data = build_dirent_blocks(&kept, dir_bs)?;

    let mut f = open_rw(image_path)?;
    let mut sb = read_sb_file(&mut f)?;
    let mut parent_ino = read_inode_file(&mut f, &sb, parent.nid)?;
    if (parent_ino.mode & S_IFMT) != S_IFDIR {
        return Err(ErofsError::Edit("parent is not a directory".into()));
    }

    let plan = plan_inode_write(&mut f, &sb, &parent_ino, new_dir_data.len() as u64, strip_avb)?;
    match plan.mode {
        WriteMode::InplacePlain => do_inplace_plain(&mut f, &sb, &mut parent_ino, &new_dir_data)?,
        WriteMode::InplaceInline => {
            do_inplace_inline(&mut f, &sb, &mut parent_ino, &new_dir_data)?
        }
        WriteMode::Append => {
            if plan.avb_strip.is_some() {
                let _ = strip_avb_footer(&mut f, &sb)?;
            }
            do_append(&mut f, &mut sb, &mut parent_ino, &new_dir_data)?;
        }
    }

    let checksum = refresh_superblock_checksum(&mut f, &mut sb)?;
    f.flush()?;
    let _ = f.sync_all();

    Ok(UnlinkResult {
        removed: clean.to_string(),
        removed_nid,
        was_dir: target.is_dir(),
        entries_before: entries.len(),
        entries_after: kept.len(),
        mode: plan.mode,
        grew_bytes: plan.grow_bytes,
        checksum,
    })
}

/// Compute exact space usage (used data blocks, metadata blocks, wasted/reclaimable blocks).
pub fn usage_report(image_path: &Path, offset: u64) -> Result<UsageReport> {
    let img = ErofsImage::open(image_path, offset)?;
    let bs = img.sb.block_size as u64;
    let nodes = img.walk(true)?;

    let mut data_used: HashSet<u64> = HashSet::new();
    let mut meta_used: HashSet<u64> = HashSet::new();
    // Superblock always occupies block 0; also count shared xattr region if separate
    meta_used.insert(0);
    if img.sb.xattr_blkaddr > 0 {
        let xstart = img.sb.xattr_blkaddr as u64;
        let xend = if img.sb.meta_blkaddr > img.sb.xattr_blkaddr {
            img.sb.meta_blkaddr as u64
        } else {
            xstart + 1
        };
        for b in xstart..xend {
            meta_used.insert(b);
        }
    }

    let mut files = 0usize;
    let mut dirs = 0usize;
    let mut symlinks = 0usize;
    let mut logical_bytes = 0u64;

    for n in &nodes {
        if n.path != "/" {
            if n.is_dir {
                dirs += 1;
            } else if n.is_link {
                symlinks += 1;
            } else {
                files += 1;
                logical_bytes += n.size;
            }
        }
        let Ok(inode) = img.read_inode(n.nid) else {
            continue;
        };
        if let Ok(pblocks) = img.inode_data_blocks(&inode) {
            data_used.extend(pblocks);
        }
        if !inode.in_metabox {
            let start = inode.offset;
            let end = match inode.layout {
                DataLayout::FlatInline => inode.data_offset + (inode.size % bs),
                DataLayout::CompressedFull | DataLayout::CompressedCompact => {
                    let mut read_meta = |off: u64, len: usize| img.source.read_vec(off, len);
                    compressed_meta_end(&img.sb, &inode, &mut read_meta)
                        .unwrap_or(inode.data_offset + 8)
                }
                _ => inode.data_offset,
            };
            let first_blk = start / bs;
            let last_blk = div_round_up(end, bs);
            for b in first_blk..last_blk {
                meta_used.insert(b);
            }
        }
    }

    let file_size = std::fs::metadata(image_path)?.len();
    let total_blocks = if img.sb.blocks > 0 {
        img.sb.blocks
    } else {
        img.source.len() / bs
    };

    let occupied: HashSet<u64> = data_used.union(&meta_used).copied().collect();
    let data_blocks = data_used.len() as u64;
    let meta_only_blocks = meta_used.difference(&data_used).count() as u64;
    let wasted_blocks = total_blocks.saturating_sub(occupied.len() as u64);

    Ok(UsageReport {
        fs: "EROFS".into(),
        block_size: img.sb.block_size,
        file_size,
        total_blocks,
        data_blocks,
        data_bytes: data_blocks * bs,
        meta_blocks: meta_only_blocks,
        meta_bytes: meta_only_blocks * bs,
        wasted_blocks,
        wasted_bytes: wasted_blocks * bs,
        reclaimable_bytes: wasted_blocks * bs,
        logical_bytes,
        files,
        dirs,
        symlinks,
        is_sparse: img.source.is_sparse(),
    })
}
