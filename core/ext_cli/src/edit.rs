use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs::{File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use crate::extfs::{
    u16_le, u32_le, ExtFileNode, ImageReader, Volume, COMPAT_HAS_JOURNAL, EXT4_EXTENTS_FL,
    EXT4_INDEX_FL, EXT4_INLINE_DATA_FL, EXT4_ROOT_INO, FT_DIR, INCOMPAT_64BIT, INCOMPAT_EXTENTS,
    RO_COMPAT_GDT_CSUM, RO_COMPAT_METADATA_CSUM, S_IFDIR, S_IFLNK, S_IFMT, S_IFREG,
};

const SB_OFFSET: u64 = 1024;
const SB_FREE_BLOCKS_LO: usize = 0x0C;
const SB_FREE_INODES: usize = 0x10;
const SB_FREE_BLOCKS_HI: usize = 0x158;

const BG_BLOCK_BITMAP_LO: usize = 0x00;
const BG_INODE_BITMAP_LO: usize = 0x04;
const BG_FREE_BLOCKS_LO: usize = 0x0C;
const BG_FREE_INODES_LO: usize = 0x0E;
const BG_USED_DIRS_LO: usize = 0x10;
const BG_FLAGS: usize = 0x12;
const BG_CHECKSUM: usize = 0x1E;
const BG_BLOCK_BITMAP_HI: usize = 0x20;
const BG_INODE_BITMAP_HI: usize = 0x24;
const BG_FREE_BLOCKS_HI: usize = 0x2C;
const BG_FREE_INODES_HI: usize = 0x2E;
const BG_USED_DIRS_HI: usize = 0x30;

const EXT4_BG_BLOCK_UNINIT: u16 = 0x2;

const I_MODE: usize = 0x00;
const I_SIZE_LO: usize = 0x04;
const I_DTIME: usize = 0x14;
const I_LINKS_COUNT: usize = 0x1A;
const I_BLOCKS_LO: usize = 0x1C;
const I_BLOCK: usize = 0x28;
const I_FILE_ACL_LO: usize = 0x68;
const I_SIZE_HI: usize = 0x6C;

#[derive(Debug, Clone)]
pub struct ExtWritePlan {
    pub mode: String,
    pub old_size: u64,
    pub new_size: u64,
    pub grow_bytes: u64,
}

#[derive(Debug, Clone)]
pub struct ExtWriteResult {
    pub path: String,
    pub mode: String,
    pub old_size: u64,
    pub new_size: u64,
    pub blocks_freed: u64,
}

#[derive(Debug, Clone)]
pub struct ExtUnlinkResult {
    pub removed: String,
    pub was_dir: bool,
    pub entries_removed: usize,
    pub blocks_freed: u64,
    pub mode: String,
}

fn crc16(mut crc: u16, data: &[u8]) -> u16 {
    for &b in data {
        crc ^= b as u16;
        for _ in 0..8 {
            if (crc & 1) != 0 {
                crc = (crc >> 1) ^ 0xA001;
            } else {
                crc >>= 1;
            }
        }
    }
    crc
}

fn pack_u16_le(buf: &mut [u8], off: usize, val: u16) {
    buf[off..off + 2].copy_from_slice(&val.to_le_bytes());
}

fn pack_u32_le(buf: &mut [u8], off: usize, val: u32) {
    buf[off..off + 4].copy_from_slice(&val.to_le_bytes());
}

fn normalize_internal(internal_path: &str) -> String {
    internal_path
        .trim_matches('/')
        .replace('\\', "/")
        .trim_matches('/')
        .to_string()
}

fn open_rw(path: &Path) -> Result<File, String> {
    if ImageReader::is_sparse_file(path).map_err(|e| e.to_string())? {
        return Err("write in-place tidak didukung untuk sparse".to_string());
    }
    OpenOptions::new()
        .read(true)
        .write(true)
        .open(path)
        .map_err(|e| format!("Gagal membuka image untuk write: {e}"))
}

fn resolve_target_node(vol: &mut Volume, internal_path: &str) -> Result<ExtFileNode, String> {
    let norm = normalize_internal(internal_path);
    if norm.is_empty() {
        return vol.read_root_node().map_err(|e| e.to_string());
    }
    vol.lookup_path(&norm).map_err(|e| {
        if e.kind() == io::ErrorKind::NotFound {
            format!("Path not found: {norm}")
        } else {
            e.to_string()
        }
    })
}

fn file_physical_blocks(vol: &mut Volume, inode_idx: u32) -> Result<(crate::extfs::InodeData, Vec<u64>), String> {
    let inode = vol.read_inode(inode_idx, 0).map_err(|e| e.to_string())?;
    let bs = vol.block_size;
    let nblocks = if inode.i_size == 0 {
        0usize
    } else {
        ((inode.i_size + bs - 1) / bs) as usize
    };
    let mut phys = vec![0u64; nblocks];

    let has_extents = (vol.superblock.s_feature_incompat & INCOMPAT_EXTENTS) != 0;
    if has_extents && (inode.i_flags & EXT4_EXTENTS_FL) != 0 {
        let runs = vol.collect_extents(&inode).map_err(|e| e.to_string())?;
        for run in runs {
            if run.unwritten || run.disk_block == 0 {
                continue;
            }
            for k in 0..(run.len as u64) {
                let lb = (run.file_block + k) as usize;
                if lb < nblocks {
                    phys[lb] = run.disk_block + k;
                }
            }
        }
    } else {
        for fb in 0..nblocks {
            phys[fb] = vol
                .resolve_indirect_block(&inode, fb as u64)
                .map_err(|e| e.to_string())?;
        }
    }
    Ok((inode, phys))
}

pub fn plan_path_write(path: &Path, internal_path: &str, new_len: u64) -> Result<ExtWritePlan, String> {
    let norm = normalize_internal(internal_path);
    if norm.is_empty() {
        return Err(format!("Is a directory: {internal_path}"));
    }
    let file = open_rw(path)?;
    let mut vol = Volume::open(file).map_err(|e| e.to_string())?;
    let node = resolve_target_node(&mut vol, &norm)?;
    if node.is_dir || node.is_link {
        return Err(format!("Is a directory: {norm}"));
    }
    let (inode, phys) = file_physical_blocks(&mut vol, node.ino)?;
    if (inode.i_flags & EXT4_INLINE_DATA_FL) != 0 {
        return Err("file inline_data — belum didukung untuk write".to_string());
    }
    if (inode.i_mode & S_IFMT) != S_IFREG {
        return Err("bukan file reguler".to_string());
    }
    let bs = vol.block_size;
    let capacity = (phys.len() as u64) * bs;
    if new_len > capacity {
        return Err(format!(
            "konten baru {new_len}B > kapasitas blok teralokasi {capacity}B ({} blok)",
            phys.len()
        ));
    }
    let needed = if new_len == 0 {
        0usize
    } else {
        ((new_len + bs - 1) / bs) as usize
    };
    if phys.iter().take(needed).any(|&b| b == 0) {
        return Err("file punya hole di rentang tulis (sparse)".to_string());
    }
    let has_metadata_csum = (vol.superblock.s_feature_ro_compat & RO_COMPAT_METADATA_CSUM) != 0;
    if has_metadata_csum && new_len != inode.i_size {
        return Err(
            "image pakai metadata_csum: ukuran konten harus sama atau image perlu direpack"
                .to_string(),
        );
    }
    Ok(ExtWritePlan {
        mode: "inplace_ext".to_string(),
        old_size: inode.i_size,
        new_size: new_len,
        grow_bytes: 0,
    })
}

struct ExtEditor {
    vol: Volume,
    has_gdt_csum: bool,
    has_journal: bool,
    has_64bit: bool,
    has_metadata_csum: bool,
    gdt_block: u64,
    desc_size: usize,
    uuid: [u8; 16],
}

impl ExtEditor {
    fn open(path: &Path) -> Result<Self, String> {
        let file = open_rw(path)?;
        let vol = Volume::open(file).map_err(|e| e.to_string())?;
        let sb = &vol.superblock;
        let has_metadata_csum = (sb.s_feature_ro_compat & RO_COMPAT_METADATA_CSUM) != 0;
        let has_gdt_csum = (sb.s_feature_ro_compat & RO_COMPAT_GDT_CSUM) != 0;
        let has_journal = (sb.s_feature_compat & COMPAT_HAS_JOURNAL) != 0;
        let has_64bit = (sb.s_feature_incompat & INCOMPAT_64BIT) != 0;
        let gdt_block = sb.s_first_data_block as u64 + 1;
        let desc_size = sb.s_desc_size as usize;
        let uuid = sb.s_uuid;
        Ok(Self {
            vol,
            has_gdt_csum,
            has_journal,
            has_64bit,
            has_metadata_csum,
            gdt_block,
            desc_size,
            uuid,
        })
    }

    fn require_structural_writable(&self) -> Result<(), String> {
        if self.has_metadata_csum {
            return Err(
                "image pakai metadata_csum — operasi struktural butuh recompute crc32c (belum didukung)"
                    .to_string(),
            );
        }
        if self.has_journal {
            return Err(
                "image punya journal aktif — tulis struktural bisa di-rollback saat mount (belum didukung)"
                    .to_string(),
            );
        }
        Ok(())
    }

    fn raw_file_mut(&mut self) -> Result<&mut File, String> {
        match &mut self.vol.reader {
            ImageReader::Raw(f) => Ok(f),
            _ => Err("write in-place tidak didukung untuk sparse / memory reader".to_string()),
        }
    }

    fn read_at_raw(&mut self, offset: u64, buf: &mut [u8]) -> Result<(), String> {
        let f = self.raw_file_mut()?;
        f.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
        f.read_exact(buf).map_err(|e| e.to_string())
    }

    fn write_at_raw(&mut self, offset: u64, data: &[u8]) -> Result<(), String> {
        let f = self.raw_file_mut()?;
        f.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
        f.write_all(data).map_err(|e| e.to_string())
    }

    fn flush(&mut self) -> Result<(), String> {
        let f = self.raw_file_mut()?;
        f.flush().map_err(|e| e.to_string())?;
        let _ = f.sync_all();
        Ok(())
    }

    fn sb_u32(&mut self, off: usize) -> Result<u32, String> {
        let mut b = [0u8; 4];
        self.read_at_raw(SB_OFFSET + off as u64, &mut b)?;
        Ok(u32::from_le_bytes(b))
    }

    fn sb_set_u32(&mut self, off: usize, val: u32) -> Result<(), String> {
        self.write_at_raw(SB_OFFSET + off as u64, &val.to_le_bytes())
    }

    fn free_blocks(&mut self) -> Result<u64, String> {
        let lo = self.sb_u32(SB_FREE_BLOCKS_LO)? as u64;
        let hi = if self.has_64bit {
            self.sb_u32(SB_FREE_BLOCKS_HI)? as u64
        } else {
            0
        };
        Ok((hi << 32) | lo)
    }

    fn set_free_blocks(&mut self, n: u64) -> Result<(), String> {
        self.sb_set_u32(SB_FREE_BLOCKS_LO, (n & 0xFFFF_FFFF) as u32)?;
        if self.has_64bit {
            self.sb_set_u32(SB_FREE_BLOCKS_HI, ((n >> 32) & 0xFFFF_FFFF) as u32)?;
        }
        Ok(())
    }

    fn gd_offset(&self, group: usize) -> u64 {
        self.gdt_block * self.vol.block_size + (group as u64) * (self.desc_size as u64)
    }

    fn gd_read(&mut self, group: usize) -> Result<Vec<u8>, String> {
        let off = self.gd_offset(group);
        let mut buf = vec![0u8; self.desc_size];
        self.read_at_raw(off, &mut buf)?;
        Ok(buf)
    }

    fn gd_write(&mut self, group: usize, d: &mut [u8]) -> Result<(), String> {
        if self.has_gdt_csum {
            pack_u16_le(d, BG_CHECKSUM, 0);
            let mut c = crc16(0xFFFF, &self.uuid);
            c = crc16(c, &(group as u32).to_le_bytes());
            c = crc16(c, &d[..BG_CHECKSUM]);
            if self.desc_size > 0x20 {
                c = crc16(c, &d[0x20..self.desc_size]);
            }
            pack_u16_le(d, BG_CHECKSUM, c);
        }
        let off = self.gd_offset(group);
        self.write_at_raw(off, d)
    }

    fn gd_field(&self, d: &[u8], off_lo: usize, off_hi: Option<usize>, width: usize) -> u64 {
        let mut v = if width == 2 {
            u16_le(d, off_lo) as u64
        } else {
            u32_le(d, off_lo) as u64
        };
        if let Some(hi_off) = off_hi {
            if self.has_64bit && self.desc_size >= hi_off + width {
                let hi = if width == 2 {
                    u16_le(d, hi_off) as u64
                } else {
                    u32_le(d, hi_off) as u64
                };
                v |= hi << (width * 8);
            }
        }
        v
    }

    fn gd_set_field(&self, d: &mut [u8], off_lo: usize, val: u64, off_hi: Option<usize>) {
        pack_u16_le(d, off_lo, (val & 0xFFFF) as u16);
        if let Some(hi_off) = off_hi {
            if self.has_64bit && self.desc_size >= hi_off + 2 {
                pack_u16_le(d, hi_off, ((val >> 16) & 0xFFFF) as u16);
            }
        }
    }

    fn bitmap_clear(&mut self, bitmap_block: u64, index: u64) -> Result<bool, String> {
        let byte_off = bitmap_block * self.vol.block_size + (index >> 3);
        let mut b = [0u8; 1];
        if self.read_at_raw(byte_off, &mut b).is_err() {
            return Ok(false);
        }
        let mask = 1u8 << (index & 7);
        if (b[0] & mask) == 0 {
            return Ok(false);
        }
        b[0] &= !mask;
        self.write_at_raw(byte_off, &b)?;
        Ok(true)
    }

    fn read_inode_raw(&mut self, ino: u32) -> Result<(u64, Vec<u8>), String> {
        let inode = self.vol.read_inode(ino, 0).map_err(|e| e.to_string())?;
        Ok((inode.offset, inode.raw))
    }

    fn free_block_list(&mut self, blocks: &BTreeSet<u64>) -> Result<u64, String> {
        let first_data = self.vol.superblock.s_first_data_block as u64;
        let bpg = self.vol.superblock.s_blocks_per_group as u64;
        if bpg == 0 {
            return Ok(0);
        }
        let mut per_group: HashMap<usize, Vec<u64>> = HashMap::new();
        for &b in blocks {
            if b < first_data || b == 0 {
                continue;
            }
            let rel = b - first_data;
            let g = (rel / bpg) as usize;
            let idx = rel % bpg;
            per_group.entry(g).or_default().push(idx);
        }

        let num_groups = self.vol.group_descriptors.len();
        let mut freed = 0u64;
        for (g, idxs) in per_group {
            if g >= num_groups {
                continue;
            }
            let mut d = self.gd_read(g)?;
            let bitmap_blk = self.gd_field(&d, BG_BLOCK_BITMAP_LO, Some(BG_BLOCK_BITMAP_HI), 4);
            let mut n = 0u64;
            for idx in idxs {
                if self.bitmap_clear(bitmap_blk, idx)? {
                    n += 1;
                }
            }
            if n == 0 {
                continue;
            }
            let free_b = self.gd_field(&d, BG_FREE_BLOCKS_LO, Some(BG_FREE_BLOCKS_HI), 2);
            self.gd_set_field(&mut d, BG_FREE_BLOCKS_LO, free_b + n, Some(BG_FREE_BLOCKS_HI));
            let flags = u16_le(&d, BG_FLAGS);
            pack_u16_le(&mut d, BG_FLAGS, flags & !EXT4_BG_BLOCK_UNINIT);
            self.gd_write(g, &mut d)?;
            freed += n;
        }
        if freed > 0 {
            let cur = self.free_blocks()?;
            self.set_free_blocks(cur + freed)?;
        }
        Ok(freed)
    }

    fn free_inode(&mut self, ino: u32, is_dir: bool) -> Result<bool, String> {
        let ipg = self.vol.superblock.s_inodes_per_group;
        if ipg == 0 || ino == 0 {
            return Ok(false);
        }
        let g = ((ino - 1) / ipg) as usize;
        let idx = ((ino - 1) % ipg) as u64;
        if g >= self.vol.group_descriptors.len() {
            return Ok(false);
        }
        let mut d = self.gd_read(g)?;
        let bitmap_blk = self.gd_field(&d, BG_INODE_BITMAP_LO, Some(BG_INODE_BITMAP_HI), 4);
        if !self.bitmap_clear(bitmap_blk, idx)? {
            return Ok(false);
        }
        let free_i = self.gd_field(&d, BG_FREE_INODES_LO, Some(BG_FREE_INODES_HI), 2);
        self.gd_set_field(&mut d, BG_FREE_INODES_LO, free_i + 1, Some(BG_FREE_INODES_HI));
        if is_dir {
            let used = self.gd_field(&d, BG_USED_DIRS_LO, Some(BG_USED_DIRS_HI), 2);
            if used > 0 {
                self.gd_set_field(&mut d, BG_USED_DIRS_LO, used - 1, Some(BG_USED_DIRS_HI));
            }
        }
        self.gd_write(g, &mut d)?;
        let cur_free = self.sb_u32(SB_FREE_INODES)?;
        self.sb_set_u32(SB_FREE_INODES, cur_free + 1)?;
        Ok(true)
    }

    fn mark_inode_deleted(&mut self, ino: u32) -> Result<(), String> {
        let (off, mut raw) = self.read_inode_raw(ino)?;
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs() as u32)
            .unwrap_or(0);
        pack_u16_le(&mut raw, I_LINKS_COUNT, 0);
        pack_u32_le(&mut raw, I_DTIME, now);
        pack_u32_le(&mut raw, I_BLOCKS_LO, 0);
        pack_u32_le(&mut raw, I_SIZE_LO, 0);
        if raw.len() >= I_SIZE_HI + 4 {
            pack_u32_le(&mut raw, I_SIZE_HI, 0);
        }
        self.write_at_raw(off, &raw)
    }

    fn adjust_parent_links(&mut self, parent_ino: u32, delta: i32) -> Result<u16, String> {
        let (off, mut raw) = self.read_inode_raw(parent_ino)?;
        let links = u16_le(&raw, I_LINKS_COUNT) as i32;
        let new_links = (links + delta).max(0) as u16;
        pack_u16_le(&mut raw, I_LINKS_COUNT, new_links);
        self.write_at_raw(off, &raw)?;
        Ok(new_links)
    }

    fn read_ptr_block(&mut self, blk: u64, per: usize) -> Result<Vec<u32>, String> {
        let bs = self.vol.block_size as usize;
        let mut data = vec![0u8; bs];
        if self.read_at_raw(blk * self.vol.block_size, &mut data).is_err() {
            return Ok(Vec::new());
        }
        Ok((0..per).map(|i| u32_le(&data, i * 4)).collect())
    }

    fn collect_extent_blocks(&mut self, node: &[u8], blocks: &mut BTreeSet<u64>) -> Result<(), String> {
        if node.len() < 12 || u16_le(node, 0) != 0xF30A {
            return Ok(());
        }
        let entries = u16_le(node, 2) as usize;
        let depth = u16_le(node, 6);
        let bs = self.vol.block_size;
        if depth == 0 {
            for i in 0..entries {
                let b = 12 + i * 12;
                if b + 12 > node.len() {
                    break;
                }
                let len = (u16_le(node, b + 4) & 0x7FFF) as u64;
                let hi = u16_le(node, b + 6) as u64;
                let lo = u32_le(node, b + 8) as u64;
                let phys = (hi << 32) | lo;
                for k in 0..len {
                    blocks.insert(phys + k);
                }
            }
        } else {
            for i in 0..entries {
                let b = 12 + i * 12;
                if b + 12 > node.len() {
                    break;
                }
                let lo = u32_le(node, b + 4) as u64;
                let hi = u16_le(node, b + 8) as u64;
                let leaf = (hi << 32) | lo;
                if leaf == 0 {
                    continue;
                }
                blocks.insert(leaf);
                let mut child = vec![0u8; bs as usize];
                self.read_at_raw(leaf * bs, &mut child)?;
                self.collect_extent_blocks(&child, blocks)?;
            }
        }
        Ok(())
    }

    fn inode_blocks(&mut self, ino: u32) -> Result<BTreeSet<u64>, String> {
        let inode = self.vol.read_inode(ino, 0).map_err(|e| e.to_string())?;
        let mut blocks = BTreeSet::new();
        if (inode.i_flags & EXT4_INLINE_DATA_FL) != 0 {
            return Ok(blocks);
        }
        let has_extents = (self.vol.superblock.s_feature_incompat & INCOMPAT_EXTENTS) != 0;
        if has_extents && (inode.i_flags & EXT4_EXTENTS_FL) != 0 {
            let ib = inode.i_block_raw;
            self.collect_extent_blocks(&ib, &mut blocks)?;
        } else {
            let per = (self.vol.block_size / 4) as usize;
            for &p in &inode.i_block[..12] {
                if p != 0 {
                    blocks.insert(p as u64);
                }
            }
            if inode.i_block[12] != 0 {
                let p = inode.i_block[12] as u64;
                blocks.insert(p);
                for c in self.read_ptr_block(p, per)? {
                    if c != 0 {
                        blocks.insert(c as u64);
                    }
                }
            }
            if inode.i_block[13] != 0 {
                let p1_blk = inode.i_block[13] as u64;
                blocks.insert(p1_blk);
                for p1 in self.read_ptr_block(p1_blk, per)? {
                    if p1 == 0 {
                        continue;
                    }
                    blocks.insert(p1 as u64);
                    for p2 in self.read_ptr_block(p1 as u64, per)? {
                        if p2 != 0 {
                            blocks.insert(p2 as u64);
                        }
                    }
                }
            }
            if inode.i_block[14] != 0 {
                let p1_blk = inode.i_block[14] as u64;
                blocks.insert(p1_blk);
                for p1 in self.read_ptr_block(p1_blk, per)? {
                    if p1 == 0 {
                        continue;
                    }
                    blocks.insert(p1 as u64);
                    for p2 in self.read_ptr_block(p1 as u64, per)? {
                        if p2 == 0 {
                            continue;
                        }
                        blocks.insert(p2 as u64);
                        for p3 in self.read_ptr_block(p2 as u64, per)? {
                            if p3 != 0 {
                                blocks.insert(p3 as u64);
                            }
                        }
                    }
                }
            }
        }
        let file_acl = u32_le(&inode.raw, I_FILE_ACL_LO) as u64;
        if file_acl != 0 {
            blocks.insert(file_acl);
        }
        Ok(blocks)
    }

    fn shrink_to(&mut self, ino: u32, new_size: u64, old_size: u64) -> Result<u64, String> {
        if new_size >= old_size {
            return Ok(0);
        }
        let inode = self.vol.read_inode(ino, 0).map_err(|e| e.to_string())?;
        if (inode.i_flags & EXT4_INLINE_DATA_FL) != 0 {
            return Ok(0);
        }
        let bs = self.vol.block_size;
        let keep = if new_size == 0 {
            0u64
        } else {
            (new_size + bs - 1) / bs
        };
        let has_extents = (self.vol.superblock.s_feature_incompat & INCOMPAT_EXTENTS) != 0;
        let freed = if has_extents && (inode.i_flags & EXT4_EXTENTS_FL) != 0 {
            self.shrink_extents(ino, keep)?
        } else {
            self.shrink_classic(ino, keep)?
        };
        if freed > 0 {
            let (off, mut raw) = self.read_inode_raw(ino)?;
            let iblocks = u32_le(&raw, I_BLOCKS_LO) as u64;
            let per_block = bs / 512;
            let updated = iblocks.saturating_sub(freed * per_block) as u32;
            pack_u32_le(&mut raw, I_BLOCKS_LO, updated);
            self.write_at_raw(off, &raw)?;
        }
        Ok(freed)
    }

    fn shrink_extents(&mut self, ino: u32, keep: u64) -> Result<u64, String> {
        let (off, mut raw) = self.read_inode_raw(ino)?;
        let mut node = [0u8; 60];
        node.copy_from_slice(&raw[I_BLOCK..I_BLOCK + 60]);
        if u16_le(&node, 0) != 0xF30A || u16_le(&node, 6) != 0 {
            return Ok(0);
        }
        let entries = u16_le(&node, 2) as usize;
        let mut free_blocks = BTreeSet::new();
        let mut kept: Vec<[u8; 12]> = Vec::new();
        for i in 0..entries {
            let b = 12 + i * 12;
            if b + 12 > node.len() {
                break;
            }
            let e_blk = u32_le(&node, b) as u64;
            let e_len = (u16_le(&node, b + 4) & 0x7FFF) as u64;
            let hi = u16_le(&node, b + 6) as u64;
            let lo = u32_le(&node, b + 8) as u64;
            let phys = (hi << 32) | lo;

            if e_blk >= keep {
                for k in 0..e_len {
                    free_blocks.insert(phys + k);
                }
                continue;
            }
            let mut ent = [0u8; 12];
            ent.copy_from_slice(&node[b..b + 12]);
            if e_blk + e_len > keep {
                let cut = keep - e_blk;
                for k in cut..e_len {
                    free_blocks.insert(phys + k);
                }
                pack_u16_le(&mut ent, 4, cut as u16);
            }
            if (u16_le(&ent, 4) & 0x7FFF) > 0 {
                kept.push(ent);
            }
        }
        if free_blocks.is_empty() {
            return Ok(0);
        }
        for (i, ent) in kept.iter().enumerate() {
            node[12 + i * 12..24 + i * 12].copy_from_slice(ent);
        }
        for i in kept.len()..entries {
            node[12 + i * 12..24 + i * 12].fill(0);
        }
        pack_u16_le(&mut node, 2, kept.len() as u16);
        raw[I_BLOCK..I_BLOCK + 60].copy_from_slice(&node);
        self.write_at_raw(off, &raw)?;
        self.free_block_list(&free_blocks)
    }

    fn shrink_classic(&mut self, ino: u32, keep: u64) -> Result<u64, String> {
        let (off, mut raw) = self.read_inode_raw(ino)?;
        let mut ptrs = [0u32; 15];
        for (i, p) in ptrs.iter_mut().enumerate() {
            *p = u32_le(&raw, I_BLOCK + i * 4);
        }
        if ptrs[12] != 0 || ptrs[13] != 0 || ptrs[14] != 0 {
            return Ok(0);
        }
        let mut free_blocks = BTreeSet::new();
        for i in (keep as usize)..12 {
            if ptrs[i] != 0 {
                free_blocks.insert(ptrs[i] as u64);
                ptrs[i] = 0;
            }
        }
        if free_blocks.is_empty() {
            return Ok(0);
        }
        for (i, &p) in ptrs.iter().enumerate() {
            pack_u32_le(&mut raw, I_BLOCK + i * 4, p);
        }
        self.write_at_raw(off, &raw)?;
        self.free_block_list(&free_blocks)
    }

    fn dir_blocks(&mut self, dir_ino: u32) -> Result<Vec<u64>, String> {
        let inode = self.vol.read_inode(dir_ino, FT_DIR).map_err(|e| e.to_string())?;
        if (inode.i_mode & S_IFMT) != S_IFDIR {
            return Err("bukan direktori".to_string());
        }
        if (inode.i_flags & EXT4_INLINE_DATA_FL) != 0 {
            return Err("direktori inline_data belum didukung".to_string());
        }
        if (inode.i_flags & EXT4_INDEX_FL) != 0 {
            return Err(
                "direktori pakai htree (dir_index) — hapus butuh rebuild index (belum didukung)"
                    .to_string(),
            );
        }
        let (_, blocks) = file_physical_blocks(&mut self.vol, dir_ino)?;
        Ok(blocks)
    }

    fn unlink_dirent(&mut self, dir_ino: u32, name: &str) -> Result<(u32, u8), String> {
        let target_bytes = name.as_bytes();
        let bs = self.vol.block_size;
        let blocks = self.dir_blocks(dir_ino)?;
        for blk in blocks {
            if blk == 0 {
                continue;
            }
            let mut data = vec![0u8; bs as usize];
            self.read_at_raw(blk * bs, &mut data)?;
            let mut pos = 0usize;
            let mut prev: Option<usize> = None;
            while pos + 8 <= data.len() {
                let e_ino = u32_le(&data, pos);
                let rec_len = u16_le(&data, pos + 4) as usize;
                let name_len = data[pos + 6] as usize;
                let ftype = data[pos + 7];
                if rec_len < 8 || pos + rec_len > data.len() {
                    break;
                }
                if e_ino != 0 && pos + 8 + name_len <= data.len() && &data[pos + 8..pos + 8 + name_len] == target_bytes {
                    if let Some(p) = prev {
                        let prev_len = u16_le(&data, p + 4) as usize;
                        pack_u16_le(&mut data, p + 4, (prev_len + rec_len) as u16);
                    } else {
                        pack_u32_le(&mut data, pos, 0);
                    }
                    self.write_at_raw(blk * bs, &data)?;
                    return Ok((e_ino, ftype));
                }
                prev = Some(pos);
                pos += rec_len;
            }
        }
        Err(format!("Path not found: {name}"))
    }

    fn delete_inode(&mut self, ino: u32) -> Result<u64, String> {
        let inode = self.vol.read_inode(ino, 0).map_err(|e| e.to_string())?;
        let is_dir = (inode.i_mode & S_IFMT) == S_IFDIR;
        let is_fast_link = (inode.i_mode & S_IFMT) == S_IFLNK
            && inode.i_size < 60
            && (inode.i_flags & EXT4_EXTENTS_FL) == 0;
        let blocks = if is_fast_link {
            let mut set = BTreeSet::new();
            let file_acl = u32_le(&inode.raw, I_FILE_ACL_LO) as u64;
            if file_acl != 0 {
                set.insert(file_acl);
            }
            set
        } else {
            self.inode_blocks(ino)?
        };
        let freed = self.free_block_list(&blocks)?;
        self.mark_inode_deleted(ino)?;
        self.free_inode(ino, is_dir)?;
        Ok(freed)
    }

    fn remove_entry(&mut self, parent_ino: u32, name: &str, child_ino: Option<u32>) -> Result<u64, String> {
        let (mut ino, _ftype) = self.unlink_dirent(parent_ino, name)?;
        if let Some(expected) = child_ino {
            if ino != expected {
                ino = expected;
            }
        }
        let (off, mut raw) = self.read_inode_raw(ino)?;
        let mode = u16_le(&raw, I_MODE);
        let is_dir = (mode & S_IFMT) == S_IFDIR;
        let links = u16_le(&raw, I_LINKS_COUNT);
        let freed = if is_dir {
            let f = self.delete_inode(ino)?;
            self.adjust_parent_links(parent_ino, -1)?;
            f
        } else if links > 1 {
            pack_u16_le(&mut raw, I_LINKS_COUNT, links - 1);
            self.write_at_raw(off, &raw)?;
            0
        } else {
            self.delete_inode(ino)?
        };
        Ok(freed)
    }
}

pub fn write_path_data(path: &Path, internal_path: &str, new_data: &[u8]) -> Result<ExtWriteResult, String> {
    let norm = normalize_internal(internal_path);
    if norm.is_empty() {
        return Err(format!("Is a directory: {internal_path}"));
    }
    let mut ed = ExtEditor::open(path)?;
    let node = resolve_target_node(&mut ed.vol, &norm)?;
    if node.is_dir || node.is_link {
        return Err(format!("Is a directory: {norm}"));
    }
    let (inode, phys) = file_physical_blocks(&mut ed.vol, node.ino)?;
    if (inode.i_flags & EXT4_INLINE_DATA_FL) != 0 {
        return Err("file inline_data — belum didukung untuk write".to_string());
    }
    if (inode.i_mode & S_IFMT) != S_IFREG {
        return Err("bukan file reguler".to_string());
    }
    let bs = ed.vol.block_size as usize;
    let new_size = new_data.len() as u64;
    let old_size = inode.i_size;
    let capacity = (phys.len() as u64) * (bs as u64);
    if new_size > capacity {
        return Err(format!(
            "konten baru {new_size}B > kapasitas blok teralokasi {capacity}B ({} blok). Edit ini butuh realokasi (repack), bukan in-place.",
            phys.len()
        ));
    }
    if ed.has_metadata_csum && new_size != old_size {
        return Err(
            "image pakai metadata_csum: hanya penggantian dengan ukuran yang sama yang aman; perubahan ukuran butuh recompute checksum/repack"
                .to_string(),
        );
    }
    let needed = if new_size == 0 {
        0usize
    } else {
        ((new_size as usize) + bs - 1) / bs
    };
    if phys.iter().take(needed).any(|&b| b == 0) {
        return Err("file punya hole di rentang tulis (sparse) — in-place tak aman".to_string());
    }

    let mut block_buf = vec![0u8; bs];
    for (i, &pblk) in phys.iter().enumerate() {
        let start = i * bs;
        if start >= new_data.len() {
            break;
        }
        let end = (start + bs).min(new_data.len());
        let slice = &new_data[start..end];
        block_buf[..slice.len()].copy_from_slice(slice);
        if slice.len() < bs {
            block_buf[slice.len()..].fill(0);
        }
        ed.write_at_raw(pblk * (bs as u64), &block_buf)?;
    }

    if new_size != old_size {
        let (ioff, mut raw) = ed.read_inode_raw(node.ino)?;
        pack_u32_le(&mut raw, I_SIZE_LO, (new_size & 0xFFFF_FFFF) as u32);
        if raw.len() >= I_SIZE_HI + 4 {
            pack_u32_le(&mut raw, I_SIZE_HI, ((new_size >> 32) & 0xFFFF_FFFF) as u32);
        }
        ed.write_at_raw(ioff, &raw)?;
    }

    let mut blocks_freed = 0u64;
    if new_size < old_size && !ed.has_metadata_csum && !ed.has_journal {
        blocks_freed = ed.shrink_to(node.ino, new_size, old_size)?;
    }
    ed.flush()?;

    Ok(ExtWriteResult {
        path: norm,
        mode: "inplace_ext".to_string(),
        old_size,
        new_size,
        blocks_freed,
    })
}

pub fn write_path_mode(path: &Path, internal_path: &str, perm_octal: u16) -> Result<u16, String> {
    let norm = normalize_internal(internal_path);
    let mut ed = ExtEditor::open(path)?;
    if ed.has_metadata_csum {
        return Err("image pakai metadata_csum — edit mode butuh recompute crc (belum didukung)".to_string());
    }
    let node = resolve_target_node(&mut ed.vol, &norm)?;
    let (ioff, mut raw) = ed.read_inode_raw(node.ino)?;
    let old_mode = u16_le(&raw, I_MODE);
    let new_mode = (old_mode & !0o7777) | (perm_octal & 0o7777);
    pack_u16_le(&mut raw, I_MODE, new_mode);
    ed.write_at_raw(ioff, &raw)?;
    ed.flush()?;
    Ok(new_mode)
}

fn find_selinux_slot(buf: &[u8], base: usize, names_value_base: usize) -> Option<(usize, usize, usize)> {
    let mut pos = base;
    while pos + 4 <= buf.len() {
        let name_len = buf[pos] as usize;
        let name_index = buf[pos + 1];
        if name_len == 0 && name_index == 0 {
            break;
        }
        if pos + 16 + name_len > buf.len() {
            break;
        }
        let value_offs = u16_le(buf, pos + 2) as usize;
        let value_inum = u32_le(buf, pos + 4);
        let value_size = u32_le(buf, pos + 8) as usize;
        let name = &buf[pos + 16..pos + 16 + name_len];
        if name_index == 6 && name == b"selinux" && value_inum == 0 {
            return Some((pos + 8, names_value_base + value_offs, value_size));
        }
        pos += 16 + name_len;
        pos = (pos + 3) & !3;
    }
    None
}

pub fn write_path_selinux(path: &Path, internal_path: &str, new_ctx: &str) -> Result<String, String> {
    let norm = normalize_internal(internal_path);
    let mut ed = ExtEditor::open(path)?;
    if ed.has_metadata_csum {
        return Err("image pakai metadata_csum — edit selinux butuh recompute crc (belum didukung)".to_string());
    }
    let node = resolve_target_node(&mut ed.vol, &norm)?;
    let (ioff, raw) = ed.read_inode_raw(node.ino)?;
    let new_bytes = new_ctx.as_bytes();

    let extra = if raw.len() >= 0x82 {
        u16_le(&raw, 0x80) as usize
    } else {
        0
    };
    if ed.vol.superblock.s_inode_size > 128 && extra > 0 {
        let xstart = 128 + extra;
        if xstart + 4 <= raw.len() && u32_le(&raw, xstart) == 0xEA02_0000 {
            if let Some((size_off, val_abs, old_size)) = find_selinux_slot(&raw, xstart + 4, xstart + 4) {
                if new_bytes.len() > old_size {
                    return Err(format!(
                        "konteks SELinux baru ({}B) > slot lama ({old_size}B). Edit ini butuh realokasi xattr (repack), bukan in-place.",
                        new_bytes.len()
                    ));
                }
                let mut padded = vec![0u8; old_size];
                padded[..new_bytes.len()].copy_from_slice(new_bytes);
                ed.write_at_raw(ioff + val_abs as u64, &padded)?;
                ed.write_at_raw(ioff + size_off as u64, &(new_bytes.len() as u32).to_le_bytes())?;
                ed.flush()?;
                return Ok(new_ctx.to_string());
            }
        }
    }

    let file_acl = u32_le(&raw, I_FILE_ACL_LO) as u64;
    if file_acl != 0 {
        let boff = file_acl * ed.vol.block_size;
        let mut blk = vec![0u8; ed.vol.block_size as usize];
        ed.read_at_raw(boff, &mut blk)?;
        if blk.len() >= 32 && u32_le(&blk, 0) == 0xEA02_0000 {
            if let Some((size_off, val_abs, old_size)) = find_selinux_slot(&blk, 32, 0) {
                if new_bytes.len() > old_size {
                    return Err(format!(
                        "konteks SELinux baru ({}B) > slot lama ({old_size}B). Edit ini butuh realokasi xattr (repack), bukan in-place.",
                        new_bytes.len()
                    ));
                }
                let mut padded = vec![0u8; old_size];
                padded[..new_bytes.len()].copy_from_slice(new_bytes);
                ed.write_at_raw(boff + val_abs as u64, &padded)?;
                ed.write_at_raw(boff + size_off as u64, &(new_bytes.len() as u32).to_le_bytes())?;
                ed.flush()?;
                return Ok(new_ctx.to_string());
            }
        }
    }

    Err("entry security.selinux tidak ditemukan di inode ini".to_string())
}

pub fn unlink_path(path: &Path, internal_path: &str, recursive: bool) -> Result<ExtUnlinkResult, String> {
    let norm = normalize_internal(internal_path);
    if norm.is_empty() {
        return Err("root image tak bisa dihapus".to_string());
    }
    let mut ed = ExtEditor::open(path)?;
    ed.require_structural_writable()?;

    let nodes = ed.vol.walk_nodes().map_err(|e| e.to_string())?;
    let mut by_path: HashMap<String, ExtFileNode> = HashMap::with_capacity(nodes.len());
    for n in &nodes {
        by_path.insert(n.path.clone(), n.clone());
    }
    let target = by_path
        .get(&norm)
        .cloned()
        .ok_or_else(|| format!("Path not found: {norm}"))?;

    let prefix = format!("{norm}/");
    let mut victims: Vec<ExtFileNode> = vec![target.clone()];
    if target.is_dir {
        for n in &nodes {
            if n.path.starts_with(&prefix) {
                victims.push(n.clone());
            }
        }
        if victims.len() > 1 && !recursive {
            return Err(format!("folder tidak kosong: {norm}"));
        }
    }

    // Bottom-up (deepest first)
    victims.sort_by(|a, b| {
        let da = a.path.matches('/').count();
        let db = b.path.matches('/').count();
        db.cmp(&da).then_with(|| b.path.len().cmp(&a.path.len()))
    });

    let mut freed = 0u64;
    let mut seen_removed = HashSet::new();
    for n in &victims {
        if !seen_removed.insert(n.path.clone()) {
            continue;
        }
        let (parent_path, name) = match n.path.rsplit_once('/') {
            Some((p, nm)) => (p, nm),
            None => ("", n.path.as_str()),
        };
        let parent_ino = by_path
            .get(parent_path)
            .map(|p| p.ino)
            .unwrap_or(EXT4_ROOT_INO);
        freed += ed.remove_entry(parent_ino, name, Some(n.ino))?;
    }
    ed.flush()?;

    Ok(ExtUnlinkResult {
        removed: norm,
        was_dir: target.is_dir,
        entries_removed: victims.len(),
        blocks_freed: freed,
        mode: "ext_unlink".to_string(),
    })
}
