use std::cell::RefCell;
use std::rc::Rc;

use crate::ext4::types::{
    div_round_up_u32, read_u32_le, write_u32_le, EXT4_BG_INODE_UNINIT, EXT4_FEATURE_RO_COMPAT_SPARSE_SUPER,
};
use crate::sparse::{SharedBuf, SparseFile};

#[derive(Clone, Debug)]
pub struct Region {
    pub block: u32,
    pub len: u32,
    pub bg: usize,
}

pub struct BlockGroup {
    pub first_block: u32,
    pub header_blocks: u32,
    pub has_superblock: bool,
    pub bitmaps: SharedBuf, // 2 * block_size: [0..bs] = block_bitmap, [bs..2*bs] = inode_bitmap
    pub inode_table: Option<SharedBuf>,
    pub free_blocks: u32,
    pub first_free_block: u32,
    pub free_inodes: u32,
    pub first_free_inode: u32,
    pub flags: u16,
    pub used_dirs: u16,
    block_size: u32,
}

impl BlockGroup {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        bg_idx: u32,
        first_data_block: u32,
        blocks_per_group: u32,
        inodes_per_group: u32,
        inode_table_blocks: u32,
        bg_desc_blocks: u32,
        bg_desc_reserve_blocks: u32,
        len_blocks: u64,
        block_size: u32,
        has_superblock: bool,
        sparse_file: &mut SparseFile,
    ) -> Result<Self, String> {
        let mut header_blocks = 2 + inode_table_blocks;
        if has_superblock {
            header_blocks += 1 + bg_desc_blocks + bg_desc_reserve_blocks;
        }

        let bitmaps = Rc::new(RefCell::new(vec![0u8; 2 * block_size as usize]));
        let first_block = first_data_block + bg_idx * blocks_per_group;

        let mut bitmap_block = first_block;
        if has_superblock {
            bitmap_block += 1 + bg_desc_blocks + bg_desc_reserve_blocks;
        }
        sparse_file.add_shared_data(Rc::clone(&bitmaps), 2 * block_size, bitmap_block);

        let mut bg = Self {
            first_block,
            header_blocks,
            has_superblock,
            bitmaps,
            inode_table: None,
            free_blocks: blocks_per_group,
            first_free_block: 0,
            free_inodes: inodes_per_group,
            first_free_inode: 1,
            flags: EXT4_BG_INODE_UNINIT,
            used_dirs: 0,
            block_size,
        };

        bg.reserve_blocks(0, header_blocks).map_err(|_| {
            format!(
                "failed to reserve {} blocks in block group {}",
                header_blocks, bg_idx
            )
        })?;

        if (first_block as u64 + blocks_per_group as u64) > len_blocks {
            let overrun = (first_block as u64 + blocks_per_group as u64 - len_blocks) as u32;
            bg.reserve_blocks(blocks_per_group - overrun, overrun)
                .map_err(|_| {
                    format!(
                        "failed to reserve {} overrun blocks in block group {}",
                        overrun, bg_idx
                    )
                })?;
        }

        Ok(bg)
    }

    pub fn reserve_blocks(&mut self, start: u32, num: u32) -> Result<(), String> {
        if num > self.free_blocks {
            return Err("not enough free blocks".to_string());
        }

        {
            let mut bm = self.bitmaps.borrow_mut();
            let block_bm = &mut bm[..self.block_size as usize];

            let mut i = 0u32;
            let mut block = start;

            while i < num && (block % 8) != 0 {
                let byte = (block / 8) as usize;
                let mask = 1u8 << (block % 8);
                if (block_bm[byte] & mask) != 0 {
                    return Err("attempted to reserve already reserved block".to_string());
                }
                block_bm[byte] |= mask;
                i += 1;
                block += 1;
            }

            let aligned_end = num & !7;
            while i + 8 <= aligned_end {
                let byte = (block / 8) as usize;
                if block_bm[byte] != 0 {
                    return Err("attempted to reserve already reserved block".to_string());
                }
                block_bm[byte] = 0xFF;
                i += 8;
                block += 8;
            }

            while i < num {
                let byte = (block / 8) as usize;
                let mask = 1u8 << (block % 8);
                if (block_bm[byte] & mask) != 0 {
                    return Err("attempted to reserve already reserved block".to_string());
                }
                block_bm[byte] |= mask;
                i += 1;
                block += 1;
            }
        }

        self.free_blocks -= num;
        if start == self.first_free_block {
            self.first_free_block = start + num;
        }
        Ok(())
    }

    pub fn free_blocks_tail(&mut self, num_blocks: u32) {
        let mut bm = self.bitmaps.borrow_mut();
        let block_bm = &mut bm[..self.block_size as usize];
        let mut block = self.first_free_block - 1;
        for _ in 0..num_blocks {
            let byte = (block / 8) as usize;
            block_bm[byte] &= !(1u8 << (block % 8));
            block = block.wrapping_sub(1);
        }
        self.free_blocks += num_blocks;
        self.first_free_block -= num_blocks;
    }

    pub fn ensure_inode_table(
        &mut self,
        inode_table_blocks: u32,
        bg_desc_blocks: u32,
        bg_desc_reserve_blocks: u32,
        sparse_file: &mut SparseFile,
    ) -> SharedBuf {
        if let Some(ref tbl) = self.inode_table {
            return Rc::clone(tbl);
        }

        let mut block = self.first_block + 2;
        if self.has_superblock {
            block += bg_desc_blocks + bg_desc_reserve_blocks + 1;
        }

        let size = inode_table_blocks as usize * self.block_size as usize;
        let tbl = Rc::new(RefCell::new(vec![0u8; size]));
        sparse_file.add_shared_data(Rc::clone(&tbl), size as u32, block);
        self.inode_table = Some(Rc::clone(&tbl));
        self.flags &= !EXT4_BG_INODE_UNINIT;
        tbl
    }

    pub fn reserve_inodes(&mut self, num: u32) -> Option<u32> {
        if self.free_inodes < num {
            return None;
        }
        {
            let mut bm = self.bitmaps.borrow_mut();
            let inode_bm = &mut bm[self.block_size as usize..];
            for i in 0..num {
                let inode = (self.first_free_inode + i - 1) as usize;
                inode_bm[inode / 8] |= 1u8 << (inode % 8);
            }
        }
        let first = self.first_free_inode;
        self.first_free_inode += num;
        self.free_inodes -= num;
        Some(first)
    }
}

pub struct BlockAllocation {
    pub regions: Vec<Region>,
    pub oob_regions: Vec<Region>,
    pub list_iter_idx: usize,
    pub list_partial: u32,
    pub oob_iter_idx: usize,
    pub oob_partial: u32,
    pub filename: Option<String>,
}

impl BlockAllocation {
    pub fn new() -> Self {
        Self {
            regions: Vec::new(),
            oob_regions: Vec::new(),
            list_iter_idx: 0,
            list_partial: 0,
            oob_iter_idx: 0,
            oob_partial: 0,
            filename: None,
        }
    }

    pub fn append_region(&mut self, block: u32, len: u32, bg: usize) {
        self.regions.push(Region { block, len, bg });
    }

    pub fn num_regions(&self) -> usize {
        self.regions.len()
    }

    pub fn total_len(&self) -> u32 {
        self.regions.iter().map(|r| r.len).sum()
    }

    pub fn rewind(&mut self) {
        self.list_iter_idx = 0;
        self.list_partial = 0;
    }

    pub fn current_region(&self) -> Option<(u32, u32)> {
        self.regions
            .get(self.list_iter_idx)
            .map(|r| (r.block, r.len - self.list_partial))
    }

    pub fn next_region(&mut self) {
        if self.list_iter_idx < self.regions.len() {
            self.list_iter_idx += 1;
        }
        self.list_partial = 0;
    }

    pub fn at_end(&self) -> bool {
        self.list_iter_idx >= self.regions.len()
    }

    pub fn get_block(&self, mut block: u32) -> Option<u32> {
        block += self.list_partial;
        for r in &self.regions[self.list_iter_idx..] {
            if block < r.len {
                return Some(r.block + block);
            }
            block -= r.len;
        }
        None
    }

    pub fn get_oob_block(&self, mut block: u32) -> Option<u32> {
        block += self.oob_partial;
        for r in &self.oob_regions[self.oob_iter_idx..] {
            if block < r.len {
                return Some(r.block + block);
            }
            block -= r.len;
        }
        None
    }

    pub fn advance_blocks(&mut self, mut blocks: u32) -> Result<(), ()> {
        while self.list_iter_idx < self.regions.len() && blocks > 0 {
            let rlen = self.regions[self.list_iter_idx].len;
            if rlen > self.list_partial + blocks {
                self.list_partial += blocks;
                return Ok(());
            }
            blocks -= rlen - self.list_partial;
            self.list_partial = 0;
            self.list_iter_idx += 1;
        }
        if blocks > 0 {
            Err(())
        } else {
            Ok(())
        }
    }

    pub fn advance_oob_blocks(&mut self, mut blocks: u32) -> Result<(), ()> {
        while self.oob_iter_idx < self.oob_regions.len() && blocks > 0 {
            let rlen = self.oob_regions[self.oob_iter_idx].len;
            if rlen > self.oob_partial + blocks {
                self.oob_partial += blocks;
                return Ok(());
            }
            blocks -= rlen - self.oob_partial;
            self.oob_partial = 0;
            self.oob_iter_idx += 1;
        }
        if blocks > 0 {
            Err(())
        } else {
            Ok(())
        }
    }

    fn split_at_offset(&mut self, mut len: u32) -> Result<usize, ()> {
        let mut idx = self.list_iter_idx;
        while idx < self.regions.len() && len >= self.regions[idx].len {
            len -= self.regions[idx].len;
            idx += 1;
        }
        if idx >= self.regions.len() && len > 0 {
            return Err(());
        }
        if len > 0 {
            let cur = &self.regions[idx];
            let second = Region {
                bg: cur.bg,
                block: cur.block + len,
                len: cur.len - len,
            };
            self.regions[idx].len = len;
            self.regions.insert(idx + 1, second);
            Ok(idx + 1)
        } else {
            Ok(idx)
        }
    }

    /// Splits off `blocks` from the current `list_iter_idx`/`list_partial` position
    /// and moves them into `oob_regions`.
    pub fn reserve_oob_blocks(&mut self, blocks: u32) -> Result<(), ()> {
        if self.list_partial > 0 {
            let new_idx = self.split_at_offset(self.list_partial)?;
            self.list_iter_idx = new_idx;
            self.list_partial = 0;
        }
        let start_idx = self.list_iter_idx;
        let end_idx = self.split_at_offset(blocks)?;
        if end_idx > start_idx {
            let drained: Vec<Region> = self.regions.drain(start_idx..end_idx).collect();
            self.oob_regions.extend(drained);
            self.list_iter_idx = start_idx;
        }
        self.list_partial = 0;
        Ok(())
    }

    pub fn reduce_allocation(&mut self, mut len: u32, bgs: &mut [BlockGroup]) {
        while len > 0 {
            let last_idx = self.regions.len() - 1;
            if self.regions[last_idx].len > len {
                let bg_idx = self.regions[last_idx].bg;
                bgs[bg_idx].free_blocks_tail(len);
                self.regions[last_idx].len -= len;
                len = 0;
            } else {
                let last = self.regions.pop().unwrap();
                bgs[last.bg].free_blocks_tail(last.len);
                len -= last.len;
                if self.regions.is_empty() {
                    self.list_iter_idx = 0;
                    self.list_partial = 0;
                }
            }
        }
    }
}

pub fn allocate_blocks_from_bg(bgs: &mut [BlockGroup], len: u32, bg_num: usize) -> Option<u32> {
    if bgs[bg_num].free_blocks < len {
        return None;
    }
    let block = bgs[bg_num].first_free_block;
    if bgs[bg_num].reserve_blocks(block, len).is_err() {
        return None;
    }
    Some(bgs[bg_num].first_block + block)
}

pub fn allocate_single_block(bgs: &mut [BlockGroup]) -> Option<u32> {
    for bg_num in 0..bgs.len() {
        if let Some(blk) = allocate_blocks_from_bg(bgs, 1, bg_num) {
            return Some(blk);
        }
    }
    None
}

pub fn allocate_best_fit_regions(bgs: &mut [BlockGroup], mut len: u32) -> Result<Vec<Region>, String> {
    let mut regions = Vec::new();
    while len > 0 {
        let mut found_bg = 0usize;
        let mut found_bg_len = 0u32;

        for (i, bg) in bgs.iter().enumerate() {
            let bg_len = bg.free_blocks;
            if (len <= bg_len && (found_bg_len == 0 || bg_len < found_bg_len))
                || (len > found_bg_len && bg_len > found_bg_len)
            {
                found_bg = i;
                found_bg_len = bg_len;
            }
        }

        if found_bg_len == 0 {
            return Err(format!("failed to allocate {} blocks, out of space?", len));
        }

        let alloc_len = len.min(found_bg_len);
        let block = allocate_blocks_from_bg(bgs, alloc_len, found_bg).ok_or_else(|| {
            format!(
                "failed to allocate {} blocks in block group {}",
                alloc_len, found_bg
            )
        })?;
        regions.push(Region {
            block,
            len: alloc_len,
            bg: found_bg,
        });
        len -= alloc_len;
    }
    Ok(regions)
}

pub fn allocate_blocks(bgs: &mut [BlockGroup], len: u32) -> Result<BlockAllocation, String> {
    let regions = allocate_best_fit_regions(bgs, len)?;
    let mut alloc = BlockAllocation::new();
    alloc.regions = regions;
    Ok(alloc)
}

pub fn is_power_of(mut a: u32, b: u32) -> bool {
    while a > b {
        if a % b != 0 {
            return false;
        }
        a /= b;
    }
    a == b
}

pub fn bg_has_super_block(bg: u32, feat_ro_compat: u16) -> bool {
    if (feat_ro_compat & EXT4_FEATURE_RO_COMPAT_SPARSE_SUPER) == 0 {
        return true;
    }
    if bg == 0 || bg == 1 {
        return true;
    }
    is_power_of(bg, 3) || is_power_of(bg, 5) || is_power_of(bg, 7)
}

#[allow(dead_code)]
fn _unused_helpers() {
    let _ = div_round_up_u32;
    let _ = read_u32_le;
    let _ = write_u32_le;
}
