// SPDX-License-Identifier: MIT

//! In-place EROFS image compactor and metadata relocator.
//! Reclaims wasted hole blocks left behind by direct edits or unlinks without repacking.

use crate::edit::{open_rw, read_inode_file, read_sb_file, refresh_superblock_checksum, set_total_blocks};
use crate::error::{ErofsError, Result};
use crate::format::{
    DataLayout, EROFS_DIRENT_SIZE, EROFS_ISLOT_SIZE, EROFS_NULL_ADDR_32, EROFS_SUPER_OFFSET,
    Inode, S_IFDIR, S_IFMT, SuperBlock, align_up, div_round_up, read_u16_le, read_u64_le,
    write_u32_le, write_u64_le,
};
use crate::reader::ErofsImage;
use crate::zmap::{compressed_blkaddr_slots, compressed_meta_end};
use serde::Serialize;
use std::collections::{HashMap, HashSet};
use std::fs::{self, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;

#[derive(Debug, Clone, Serialize)]
pub struct CompactReport {
    pub before_bytes: u64,
    pub after_bytes: u64,
    pub saved_bytes: u64,
    pub moves: usize,
    pub blocks_moved: u64,
    pub holes_before: usize,
    pub locked_blocks: usize,
    pub total_blocks_before: u64,
    pub total_blocks_after: u64,
    pub meta_blocks_after: u64,
    pub inodes_relocated: usize,
    pub dry_run: bool,
}

#[derive(Debug, Clone)]
struct SpanItem {
    nid: u64,
    layout: DataLayout,
    start: u64,
    cnt: u64,
}

#[derive(Debug, Clone)]
struct SpanGroup {
    start: u64,
    cnt: u64,
    members: Vec<(u64, DataLayout)>,
}

#[derive(Debug, Clone)]
struct MoveOp {
    nid: u64,
    layout: DataLayout,
    src: u64,
    dst: u64,
    copy_cnt: u64,
}

#[derive(Debug, Clone)]
struct MetaItem {
    nid: u64,
    offset: u64,
    size: u64,
    layout: DataLayout,
}

fn meta_extent_exact(img: &ErofsImage, inode: &Inode) -> (u64, u64) {
    let bs = img.sb.block_size as u64;
    let start = inode.offset;
    let end = match inode.layout {
        DataLayout::FlatInline => inode.data_offset + (inode.size % bs),
        DataLayout::CompressedFull | DataLayout::CompressedCompact => {
            let mut read_meta = |off: u64, len: usize| img.source.read_vec(off, len);
            compressed_meta_end(&img.sb, inode, &mut read_meta)
                .unwrap_or_else(|_| align_up(inode.data_offset, 8) + 8)
        }
        _ => inode.data_offset,
    };
    (start, end)
}

fn plan_inode_relocation(img: &ErofsImage, nids: &[u64]) -> (HashMap<u64, u64>, u64, Vec<MetaItem>) {
    let bs = img.sb.block_size as u64;
    let mut items = Vec::with_capacity(nids.len());
    for &nid in nids {
        let Ok(ino) = img.read_inode(nid) else {
            continue;
        };
        let (a, b) = meta_extent_exact(img, &ino);
        items.push(MetaItem {
            nid,
            offset: a,
            size: b.saturating_sub(a),
            layout: ino.layout,
        });
    }
    items.sort_by_key(|x| x.offset);

    // Clip any slightly conservative end estimates so adjacent metadata blobs never overlap.
    let mut clipped = Vec::with_capacity(items.len());
    for i in 0..items.len() {
        let mut it = items[i].clone();
        if i + 1 < items.len() {
            let next_a = items[i + 1].offset;
            if it.offset + it.size > next_a {
                it.size = next_a.saturating_sub(it.offset);
            }
        }
        if it.size > 0 {
            clipped.push(it);
        }
    }

    let base = (img.sb.meta_blkaddr as u64) * bs;
    let first_nid = clipped.iter().map(|i| i.nid).min().unwrap_or(0);
    let mut cursor = base + first_nid * EROFS_ISLOT_SIZE;
    let mut mapping = HashMap::new();

    for it in &clipped {
        let mut new_off = cursor;
        if it.size <= bs && (new_off % bs) + it.size > bs {
            new_off = ((new_off / bs) + 1) * bs;
        }
        let new_nid = (new_off - base) / EROFS_ISLOT_SIZE;
        mapping.insert(it.nid, new_nid);
        cursor = new_off + align_up(it.size, EROFS_ISLOT_SIZE);
    }

    (mapping, cursor, clipped)
}

fn plan_data_moves(
    locked: &HashSet<u64>,
    groups: &[SpanGroup],
    reserved_blocks: u64,
) -> (Vec<MoveOp>, u64) {
    let mut occupied = locked.clone();
    let mut moves = Vec::new();
    let mut cursor = reserved_blocks.max(1);

    let is_free = |occ: &HashSet<u64>, at: u64, cnt: u64| -> bool {
        (0..cnt).all(|k| !locked.contains(&(at + k)) && !occ.contains(&(at + k)))
    };

    for g in groups {
        let mut at = cursor;
        while !is_free(&occupied, at, g.cnt) {
            at += 1;
        }
        for k in 0..g.cnt {
            occupied.insert(at + k);
        }
        if at != g.start {
            for (idx, &(nid, layout)) in g.members.iter().enumerate() {
                moves.push(MoveOp {
                    nid,
                    layout,
                    src: g.start,
                    dst: at,
                    copy_cnt: if idx == 0 { g.cnt } else { 0 },
                });
            }
        }
        cursor = at + g.cnt;
    }

    let new_total = occupied.iter().copied().max().map(|m| m + 1).unwrap_or(1);
    (moves, new_total)
}

fn remap_dirent_block(buf: &mut [u8], mapping: &HashMap<u64, u64>) -> bool {
    if buf.len() < EROFS_DIRENT_SIZE {
        return false;
    }
    let count = (read_u16_le(buf, 8) as usize) / EROFS_DIRENT_SIZE;
    if count == 0 || count * EROFS_DIRENT_SIZE > buf.len() {
        return false;
    }
    let mut dirty = false;
    for i in 0..count {
        let e = i * EROFS_DIRENT_SIZE;
        let old = read_u64_le(buf, e);
        if let Some(&new_nid) = mapping.get(&old) {
            if new_nid != old {
                write_u64_le(buf, e, new_nid);
                dirty = true;
            }
        }
    }
    dirty
}

/// Compact an EROFS image in-place by reclaiming unused blocks and optionally relocating metadata.
pub fn compact_image(
    image_path: &Path,
    dry_run: bool,
    relocate_meta: bool,
) -> Result<CompactReport> {
    if crate::sparse::is_sparse_file(image_path) {
        return Err(ErofsError::Compact(
            "sparse images must be unsparsed before compaction".into(),
        ));
    }

    let img = ErofsImage::open(image_path, 0)?;
    if img.sb.has_metabox() {
        return Err(ErofsError::Compact(
            "images using METABOX are not supported for in-place compaction".into(),
        ));
    }
    if img.sb.has_fragments() {
        return Err(ErofsError::Compact(
            "images using FRAGMENTS/DEDUPE share blocks across inodes; per-inode compaction is unsafe".into(),
        ));
    }

    let bs = img.sb.block_size as u64;
    let before_bytes = fs::metadata(image_path)?.len();
    let nodes = img.walk(true)?;

    let mut seen_nids = HashSet::new();
    let mut all_nids = Vec::new();
    for n in &nodes {
        if seen_nids.insert(n.nid) {
            all_nids.push(n.nid);
        }
    }

    let mut locked: HashSet<u64> = HashSet::new();
    locked.insert(0); // superblock
    if img.sb.xattr_blkaddr > 0 {
        // Lock blocks between xattr_blkaddr and meta_blkaddr (shared xattr region)
        let xstart = img.sb.xattr_blkaddr as u64;
        let xend = if img.sb.meta_blkaddr > img.sb.xattr_blkaddr {
            img.sb.meta_blkaddr as u64
        } else {
            xstart + 1
        };
        for b in xstart..xend {
            locked.insert(b);
        }
    }
    let mut spans: Vec<SpanItem> = Vec::new();
    let mut used_data: HashSet<u64> = HashSet::new();

    for &nid in &all_nids {
        let Ok(ino) = img.read_inode(nid) else {
            continue;
        };
        if ino.layout == DataLayout::ChunkBased {
            return Err(ErofsError::Compact(
                "chunk-based inode (layout 4) is not supported for compaction".into(),
            ));
        }

        let (ma, mb) = meta_extent_exact(&img, &ino);
        for blk in (ma / bs)..div_round_up(mb, bs) {
            locked.insert(blk);
        }

        let pblks = img.inode_data_blocks(&ino)?;
        if !pblks.is_empty() {
            let lo = *pblks.iter().min().unwrap();
            let hi = *pblks.iter().max().unwrap();
            let cnt = hi - lo + 1;
            spans.push(SpanItem {
                nid,
                layout: ino.layout,
                start: lo,
                cnt,
            });
            for k in 0..cnt {
                used_data.insert(lo + k);
            }
        }
    }

    spans.sort_by_key(|s| s.start);
    let mut groups: Vec<SpanGroup> = Vec::new();
    for s in spans {
        let s_end = s.start + s.cnt;
        if let Some(last) = groups.last_mut() {
            if s.start < last.start + last.cnt {
                let new_end = (last.start + last.cnt).max(s_end);
                last.cnt = new_end - last.start;
                last.members.push((s.nid, s.layout));
                continue;
            }
        }
        groups.push(SpanGroup {
            start: s.start,
            cnt: s.cnt,
            members: vec![(s.nid, s.layout)],
        });
    }

    let total_blocks_before = img.sb.blocks;
    let occupied_before: HashSet<u64> = used_data.union(&locked).copied().collect();
    let holes_before = (1..total_blocks_before)
        .filter(|b| !occupied_before.contains(b))
        .count();

    let (mapping, meta_end, meta_items, reserved, n_reloc) = if relocate_meta {
        let (map, mend, items) = plan_inode_relocation(&img, &all_nids);
        let res = div_round_up(mend, bs);
        let reloc = map.iter().filter(|(k, v)| k != v).count();
        (Some(map), mend, items, res, reloc)
    } else {
        (None, 0, Vec::new(), 0, 0)
    };

    let effective_locked: HashSet<u64> = if relocate_meta {
        locked.iter().copied().filter(|&b| b < reserved).collect()
    } else {
        locked.clone()
    };

    let (moves, data_total) = plan_data_moves(&effective_locked, &groups, reserved);
    let new_total = data_total.max(reserved);
    let new_size = new_total * bs;
    let blocks_moved: u64 = moves.iter().map(|m| m.copy_cnt).sum();

    let mut report = CompactReport {
        before_bytes,
        after_bytes: new_size,
        saved_bytes: before_bytes.saturating_sub(new_size),
        moves: moves.len(),
        blocks_moved,
        holes_before,
        locked_blocks: effective_locked.len(),
        total_blocks_before,
        total_blocks_after: new_total,
        meta_blocks_after: reserved,
        inodes_relocated: n_reloc,
        dry_run,
    };

    if dry_run {
        return Ok(report);
    }

    if moves.is_empty() && n_reloc == 0 && new_size >= before_bytes {
        report.after_bytes = before_bytes;
        report.saved_bytes = 0;
        return Ok(report);
    }

    // Stage metadata blobs & compressed slot offsets before mutating disk.
    struct StagedMeta {
        mapping: HashMap<u64, u64>,
        blobs: HashMap<u64, Vec<u8>>,
        items: Vec<MetaItem>,
        new_root: u64,
        dirs_in_data: HashMap<u64, (u64, u64)>,
        slot_rel: HashMap<u64, Vec<(usize, u32)>>,
        has_48bit: bool,
        base: u64,
    }

    let staged = if let (true, Some(map)) = (relocate_meta && n_reloc > 0, mapping) {
        let mut blobs = HashMap::new();
        let mut dirs_in_data = HashMap::new();
        let mut slot_rel = HashMap::new();
        for it in &meta_items {
            let mut blob = img.source.read_exact_vec(it.offset, it.size as usize)?;
            let ino = img.read_inode(it.nid)?;

            if (ino.mode & S_IFMT) == S_IFDIR {
                let dsize = ino.size;
                let full = if it.layout == DataLayout::FlatInline {
                    (dsize / bs) * bs
                } else {
                    div_round_up(dsize, bs) * bs
                };
                if full > 0 && !ino.is_null_start() {
                    dirs_in_data.insert(it.nid, (ino.start_blk, full));
                }
                if it.layout == DataLayout::FlatInline {
                    let tail_len = (dsize - full) as usize;
                    if tail_len > 0 && blob.len() >= tail_len {
                        let head = blob.len() - tail_len;
                        remap_dirent_block(&mut blob[head..], &map);
                    }
                }
            }

            if it.layout.is_compressed() {
                let mut read_meta = |off: u64, len: usize| img.source.read_vec(off, len);
                if let Ok(slots) = compressed_blkaddr_slots(&img.sb, &ino, &mut read_meta) {
                    let rels = slots
                        .into_iter()
                        .filter_map(|(off, val)| {
                            off.checked_sub(it.offset).map(|r| (r as usize, val))
                        })
                        .collect();
                    slot_rel.insert(it.nid, rels);
                }
            }

            blobs.insert(it.nid, blob);
        }

        let new_root = *map.get(&img.sb.root_nid).unwrap_or(&img.sb.root_nid);
        Some(StagedMeta {
            mapping: map,
            blobs,
            items: meta_items,
            new_root,
            dirs_in_data,
            slot_rel,
            has_48bit: img.sb.has_48bit(),
            base: (img.sb.meta_blkaddr as u64) * bs,
        })
    } else {
        None
    };

    // Precompute direct inode patches when not relocating metadata.
    let mut direct_patches: Vec<(u64, u32)> = Vec::new();
    if staged.is_none() {
        for mv in &moves {
            let ino = img.read_inode(mv.nid)?;
            match mv.layout {
                DataLayout::FlatPlain | DataLayout::FlatInline => {
                    direct_patches.push((ino.offset + 16, (mv.dst & 0xFFFF_FFFF) as u32));
                }
                DataLayout::CompressedFull | DataLayout::CompressedCompact => {
                    let mut read_meta = |off: u64, len: usize| img.source.read_vec(off, len);
                    let delta = (mv.dst as i64) - (mv.src as i64);
                    for (off, val) in compressed_blkaddr_slots(&img.sb, &ino, &mut read_meta)? {
                        let patched = (val as i64).wrapping_add(delta) as u32;
                        direct_patches.push((off, patched));
                    }
                }
                _ => {}
            }
        }
    }
    let _ = meta_end;
    drop(img);

    // 1. Copy data blocks safely via staging file so overlapping moves never clobber source data.
    let stage_path = image_path.with_extension("compact-stage");
    let mut f = open_rw(image_path)?;
    {
        let mut stage = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(true)
            .open(&stage_path)?;
        let copies: Vec<&MoveOp> = moves.iter().filter(|m| m.copy_cnt > 0).collect();
        let mut offsets: Vec<(u64, u64, u64)> = Vec::with_capacity(copies.len());
        let mut cur = 0u64;
        const CHUNK: usize = 8 * 1024 * 1024;
        let mut buf = vec![0u8; CHUNK];

        for mv in &copies {
            let total = mv.copy_cnt * bs;
            let mut done = 0u64;
            while done < total {
                let take = (CHUNK as u64).min(total - done) as usize;
                f.seek(SeekFrom::Start(mv.src * bs + done))?;
                f.read_exact(&mut buf[..take])?;
                stage.write_all(&buf[..take])?;
                done += take as u64;
            }
            offsets.push((cur, total, mv.dst));
            cur += total;
        }
        stage.flush()?;

        for (st_off, total, dst) in offsets {
            let mut done = 0u64;
            while done < total {
                let take = (CHUNK as u64).min(total - done) as usize;
                stage.seek(SeekFrom::Start(st_off + done))?;
                stage.read_exact(&mut buf[..take])?;
                f.seek(SeekFrom::Start(dst * bs + done))?;
                f.write_all(&buf[..take])?;
                done += take as u64;
            }
        }
    }
    let _ = fs::remove_file(&stage_path);

    // 2. Patch metadata (either direct patches or relocated staged blobs).
    if let Some(mut st) = staged {
        let mut moved_to = HashMap::new();
        for mv in &moves {
            moved_to.insert(mv.nid, mv.dst);
            if let Some(blob) = st.blobs.get_mut(&mv.nid) {
                match mv.layout {
                    DataLayout::FlatPlain | DataLayout::FlatInline => {
                        if blob.len() >= 20 {
                            write_u32_le(blob, 16, (mv.dst & 0xFFFF_FFFF) as u32);
                        }
                    }
                    DataLayout::CompressedFull | DataLayout::CompressedCompact => {
                        let delta = (mv.dst as i64) - (mv.src as i64);
                        if let Some(rels) = st.slot_rel.get(&mv.nid) {
                            for &(rel, orig) in rels {
                                if rel + 4 <= blob.len() {
                                    let patched = (orig as i64).wrapping_add(delta) as u32;
                                    write_u32_le(blob, rel, patched);
                                }
                            }
                        }
                    }
                    _ => {}
                }
            }
        }

        // Remap NIDs inside directory data blocks
        for (&nid, &(old_start, full_bytes)) in &st.dirs_in_data {
            if old_start == EROFS_NULL_ADDR_32 as u64 || full_bytes == 0 {
                continue;
            }
            let start = *moved_to.get(&nid).unwrap_or(&old_start);
            let nblks = full_bytes / bs;
            for k in 0..nblks {
                let off = (start + k) * bs;
                f.seek(SeekFrom::Start(off))?;
                let mut blk = vec![0u8; bs as usize];
                if f.read_exact(&mut blk).is_err() {
                    break;
                }
                if remap_dirent_block(&mut blk, &st.mapping) {
                    f.seek(SeekFrom::Start(off))?;
                    f.write_all(&blk)?;
                }
            }
        }

        // Write relocated metadata blobs
        for it in &st.items {
            if let Some(blob) = st.blobs.get(&it.nid) {
                let new_nid = st.mapping[&it.nid];
                f.seek(SeekFrom::Start(st.base + new_nid * EROFS_ISLOT_SIZE))?;
                f.write_all(blob)?;
            }
        }

        // Update root_nid in superblock
        if st.has_48bit {
            f.seek(SeekFrom::Start(EROFS_SUPER_OFFSET + 112))?;
            f.write_all(&st.new_root.to_le_bytes())?;
        } else {
            f.seek(SeekFrom::Start(EROFS_SUPER_OFFSET + 14))?;
            f.write_all(&((st.new_root & 0xFFFF) as u16).to_le_bytes())?;
        }
    } else {
        for (off, val) in direct_patches {
            f.seek(SeekFrom::Start(off))?;
            f.write_all(&val.to_le_bytes())?;
        }
    }

    // 3. Update superblock total_blocks, refresh CRC32C, and truncate tail.
    let mut sb: SuperBlock = read_sb_file(&mut f)?;
    set_total_blocks(&mut f, &mut sb, new_total)?;
    refresh_superblock_checksum(&mut f, &mut sb)?;
    f.flush()?;
    let cur_len = f.metadata()?.len();
    if new_size < cur_len {
        f.set_len(new_size)?;
    }
    let _ = f.sync_all();

    let after = f.metadata()?.len();
    let _ = read_inode_file;
    report.after_bytes = after;
    report.saved_bytes = before_bytes.saturating_sub(after);
    Ok(report)
}
