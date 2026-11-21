//! Ext2 / Ext3 / Ext4 block group and filesystem geometry calculator (MIT clean-room).

use crate::config::{apply_fs_type_profile, ExtMkfsConfig, ExtMkfsError};
use crate::spec::*;

/// Layout of a single block group.
#[derive(Debug, Clone)]
pub struct BlockGroupLayout {
    pub group_nr: u32,
    pub start_block: u32,
    pub block_count: u32,
    pub has_super: bool,
    pub super_and_gdt_blocks: u32,
    pub block_bitmap: u32,
    pub inode_bitmap: u32,
    pub inode_table: u32,
    pub inode_table_blocks: u32,
    pub used_meta_blocks: u32,
    pub allocated_blocks: u32,
    pub free_blocks: u32,
    pub free_inodes: u32,
    pub used_dirs: u16,
    pub bg_flags: u16,
    pub itable_unused: u16,
}

/// Complete computed geometry for an Ext2/Ext3/Ext4 volume.
#[derive(Debug, Clone)]
pub struct ExtFsLayout {
    pub total_blocks: u32,
    pub block_size: u32,
    pub log_block_size: u32,
    pub first_data_block: u32,
    pub blocks_per_group: u32,
    pub inodes_per_group: u32,
    pub inode_size: u16,
    pub inode_table_blocks: u32,
    pub group_count: u32,
    pub total_inodes: u32,
    pub reserved_blocks: u32,
    pub desc_size: u16,
    pub gdt_blocks: u32,
    pub reserved_gdt_blocks: u16,
    pub lost_found_blocks: u32,
    pub root_dir_block: u32,
    pub lost_found_start_block: u32,
    pub journal_blocks: u32,
    pub journal_indirect_blocks: u32,
    pub journal_total_blocks: u32,
    pub journal_start_block: u32,
    pub journal_group: u32,
    pub overhead_clusters: u32,
    pub total_free_blocks: u32,
    pub total_free_inodes: u32,
    pub groups: Vec<BlockGroupLayout>,
}

fn is_power_of(mut n: u32, base: u32) -> bool {
    if n == 0 {
        return false;
    }
    while n % base == 0 {
        n /= base;
    }
    n == 1
}

/// Returns `true` if block group `group_nr` stores a primary or backup superblock + GDT.
pub fn group_has_super(
    group_nr: u32,
    group_count: u32,
    feature_compat: u32,
    feature_ro_compat: u32,
) -> bool {
    if group_nr == 0 {
        return true;
    }
    if (feature_compat & EXT4_FEATURE_COMPAT_SPARSE_SUPER2) != 0 {
        return group_nr == 1 || group_nr + 1 == group_count;
    }
    if (feature_ro_compat & EXT2_FEATURE_RO_COMPAT_SPARSE_SUPER) != 0 {
        return group_nr <= 1
            || is_power_of(group_nr, 3)
            || is_power_of(group_nr, 5)
            || is_power_of(group_nr, 7);
    }
    true
}

/// Counts how many indirect/double-indirect pointer blocks are needed for `data_blocks`
/// when `EXT4_FEATURE_INCOMPAT_EXTENTS` is NOT enabled (Ext2 / Ext3).
pub fn count_indirect_blocks(data_blocks: u32, block_size: u32) -> u32 {
    if data_blocks <= 12 {
        return 0;
    }
    let ptrs = block_size / 4;
    if data_blocks <= 12 + ptrs {
        return 1;
    }
    let rem = data_blocks - 12 - ptrs;
    1 + 1 + rem.div_ceil(ptrs)
}

/// Computes the default JBD2 journal length in filesystem blocks.
pub fn default_journal_blocks(total_blocks: u32, block_size: u32) -> u32 {
    if total_blocks < 2048 {
        return 0;
    }
    // Scale thresholds matching e2fsprogs `ext2fs_default_journal_size`
    let factor = 4096 / block_size;
    if total_blocks < 32768 * factor {
        1024
    } else if total_blocks < 256 * 1024 * factor {
        4096 / factor.max(1)
    } else if total_blocks < 512 * 1024 * factor {
        8192 / factor.max(1)
    } else if total_blocks < 1024 * 1024 * factor {
        16384 / factor.max(1)
    } else {
        32768
    }
}

impl ExtFsLayout {
    /// Finalizes `cfg` (including automatic size-based usage profile if `-T` was not set)
    /// and computes the complete filesystem layout.
    pub fn compute(cfg: &mut ExtMkfsConfig, total_bytes: u64) -> Result<Self, ExtMkfsError> {
        let mib = total_bytes / (1024 * 1024);
        if cfg.usage_type.is_none() {
            if mib < 3 {
                apply_fs_type_profile(cfg, "floppy");
            } else if mib < 512 {
                apply_fs_type_profile(cfg, "small");
            }
        }

        let block_size = cfg.block_size;
        let log_block_size = block_size.trailing_zeros() - 10;
        let total_blocks_u64 = total_bytes / u64::from(block_size);
        if total_blocks_u64 < 64 {
            return Err(ExtMkfsError::VolumeTooSmall(total_blocks_u64));
        }
        let mut total_blocks = total_blocks_u64.min(u64::from(u32::MAX)) as u32;

        let first_data_block = if block_size == 1024 { 1 } else { 0 };
        let max_bpg = 8 * block_size;
        let blocks_per_group = cfg
            .blocks_per_group
            .unwrap_or(max_bpg)
            .min(max_bpg)
            & !7;

        let mut group_count =
            (total_blocks - first_data_block).div_ceil(blocks_per_group).max(1);

        let desc_size: u16 = if (cfg.feature_incompat & EXT4_FEATURE_INCOMPAT_64BIT) != 0 {
            64
        } else {
            32
        };

        // Compute preliminary inode count to check if last group has enough room for metadata.
        let compute_ipg = |t_blks: u32, g_cnt: u32| -> (u32, u32) {
            let t_bytes = u64::from(t_blks) * u64::from(block_size);
            let wanted = cfg.num_inodes.unwrap_or_else(|| {
                t_bytes.div_ceil(u64::from(cfg.inode_ratio)).max(11) as u32
            });
            let raw_ipg = wanted.div_ceil(g_cnt).max(16);
            let inodes_per_blk = block_size / u32::from(cfg.inode_size);
            let aligned = raw_ipg.div_ceil(inodes_per_blk) * inodes_per_blk;
            let ipg = ((aligned + 7) & !7).min(8 * block_size);
            let it_blks = (ipg * u32::from(cfg.inode_size)) / block_size;
            (ipg, it_blks)
        };

        let (mut inodes_per_group, mut inode_table_blocks) = compute_ipg(total_blocks, group_count);
        let mut gdt_blocks = (group_count * u32::from(desc_size)).div_ceil(block_size);

        if group_count > 1 {
            let last_start = first_data_block + (group_count - 1) * blocks_per_group;
            let last_blks = total_blocks - last_start;
            let last_has_sb = group_has_super(
                group_count - 1,
                group_count,
                cfg.feature_compat,
                cfg.feature_ro_compat,
            );
            let needed = (if last_has_sb { 1 + gdt_blocks } else { 0 }) + 2 + inode_table_blocks + 50;
            if last_blks < needed {
                total_blocks = last_start;
                group_count -= 1;
                (inodes_per_group, inode_table_blocks) = compute_ipg(total_blocks, group_count);
                gdt_blocks = (group_count * u32::from(desc_size)).div_ceil(block_size);
            }
        }

        let total_inodes = inodes_per_group * group_count;
        let reserved_blocks = ((f64::from(total_blocks) * cfg.reserved_ratio) / 100.0) as u32;

        // `lost+found` takes up to 16 KiB, capped at 12 direct blocks (EXT2_NDIR_BLOCKS).
        let lost_found_blocks = (16384 / block_size).clamp(1, 12);

        let has_journal = (cfg.feature_compat & EXT3_FEATURE_COMPAT_HAS_JOURNAL) != 0;
        let has_extents = (cfg.feature_incompat & EXT4_FEATURE_INCOMPAT_EXTENTS) != 0;
        let journal_blocks = if has_journal {
            match cfg.journal_blocks {
                Some(mb) => ((mb * 1024 * 1024) / block_size).clamp(1024, total_blocks / 2),
                None => default_journal_blocks(total_blocks, block_size),
            }
        } else {
            0
        };
        let journal_indirect_blocks = if journal_blocks > 0 && !has_extents {
            count_indirect_blocks(journal_blocks, block_size)
        } else {
            0
        };
        let journal_total_blocks = journal_blocks + journal_indirect_blocks;

        let uninit_bg = (cfg.feature_ro_compat
            & (EXT4_FEATURE_RO_COMPAT_GDT_CSUM | EXT4_FEATURE_RO_COMPAT_METADATA_CSUM))
            != 0;

        let mut groups = Vec::with_capacity(group_count as usize);
        for g in 0..group_count {
            let start_block = first_data_block + g * blocks_per_group;
            let end_block = (start_block + blocks_per_group).min(total_blocks);
            let block_count = end_block - start_block;
            let has_super =
                group_has_super(g, group_count, cfg.feature_compat, cfg.feature_ro_compat);
            let super_and_gdt = if has_super { 1 + gdt_blocks } else { 0 };
            let block_bitmap = start_block + super_and_gdt;
            let inode_bitmap = block_bitmap + 1;
            let inode_table = inode_bitmap + 1;
            let used_meta_blocks = super_and_gdt + 2 + inode_table_blocks;

            let (free_inodes, used_dirs, bg_flags, itable_unused) = if g == 0 {
                // Inodes 1..11 allocated in Group 0 (11 inodes used, 2 directories: `/` and `lost+found`)
                let fi = inodes_per_group - EXT2_GOOD_OLD_FIRST_INO;
                let fl = if uninit_bg && !cfg.lazy_itable_init {
                    EXT2_BG_INODE_ZEROED
                } else {
                    0
                };
                let iu = if uninit_bg { fi as u16 } else { 0 };
                (fi, 2u16, fl, iu)
            } else {
                let fi = inodes_per_group;
                let fl = if uninit_bg {
                    let mut f = EXT2_BG_INODE_UNINIT;
                    if g + 1 < group_count {
                        f |= EXT2_BG_BLOCK_UNINIT;
                    }
                    if !cfg.lazy_itable_init {
                        f |= EXT2_BG_INODE_ZEROED;
                    }
                    f
                } else {
                    0
                };
                let iu = if uninit_bg { fi as u16 } else { 0 };
                (fi, 0u16, fl, iu)
            };

            groups.push(BlockGroupLayout {
                group_nr: g,
                start_block,
                block_count,
                has_super,
                super_and_gdt_blocks: super_and_gdt,
                block_bitmap,
                inode_bitmap,
                inode_table,
                inode_table_blocks,
                used_meta_blocks,
                allocated_blocks: used_meta_blocks,
                free_blocks: block_count - used_meta_blocks,
                free_inodes,
                used_dirs,
                bg_flags,
                itable_unused,
            });
        }

        // Root dir (`1` block) and `lost+found` (`lost_found_blocks` blocks) are allocated in Group 0
        let root_dir_block = groups[0].start_block + groups[0].used_meta_blocks;
        let lost_found_start_block = root_dir_block + 1;
        groups[0].allocated_blocks += 1 + lost_found_blocks;
        groups[0].free_blocks -= 1 + lost_found_blocks;

        // Allocate contiguous journal blocks: e2fsprogs places the journal in the middle group
        // when group_count > 1 (or Group 0 when group_count == 1) if it fits!
        let mut journal_group = 0u32;
        let mut journal_start_block = 0u32;
        if journal_total_blocks > 0 {
            let preferred_group = if group_count > 1 { group_count / 2 } else { 0 };
            let chosen_g = if groups[preferred_group as usize].free_blocks >= journal_total_blocks {
                preferred_group
            } else {
                groups
                    .iter()
                    .position(|g| g.free_blocks >= journal_total_blocks)
                    .unwrap_or(0) as u32
            };
            journal_group = chosen_g;
            let grp = &mut groups[chosen_g as usize];
            journal_start_block = grp.start_block + grp.allocated_blocks;
            grp.allocated_blocks += journal_total_blocks;
            grp.free_blocks -= journal_total_blocks;
            // A group holding the journal has initialized block bitmap!
            grp.bg_flags &= !EXT2_BG_BLOCK_UNINIT;
        }

        // Overhead clusters = first_data_block + sum(group.used_meta_blocks) + journal_blocks
        let meta_sum: u32 = groups.iter().map(|g| g.used_meta_blocks).sum();
        let overhead_clusters = first_data_block + meta_sum + journal_blocks;
        let total_free_blocks: u32 = groups.iter().map(|g| g.free_blocks).sum();
        let total_free_inodes: u32 = groups.iter().map(|g| g.free_inodes).sum();

        Ok(Self {
            total_blocks,
            block_size,
            log_block_size,
            first_data_block,
            blocks_per_group,
            inodes_per_group,
            inode_size: cfg.inode_size,
            inode_table_blocks,
            group_count,
            total_inodes,
            reserved_blocks,
            desc_size,
            gdt_blocks,
            reserved_gdt_blocks: 0,
            lost_found_blocks,
            root_dir_block,
            lost_found_start_block,
            journal_blocks,
            journal_indirect_blocks,
            journal_total_blocks,
            journal_start_block,
            journal_group,
            overhead_clusters,
            total_free_blocks,
            total_free_inodes,
            groups,
        })
    }
}
