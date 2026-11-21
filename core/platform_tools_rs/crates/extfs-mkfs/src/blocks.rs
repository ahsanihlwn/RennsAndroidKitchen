//! On-disk Ext2 / Ext3 / Ext4 block and structure builders (MIT clean-room).

use crate::config::ExtMkfsConfig;
use crate::crc::gdt_crc16;
use crate::layout::ExtFsLayout;
use crate::spec::*;

/// Sets bits `0..used_bits` and padding bits `max_valid_bits..(buf.len() * 8)` in an Ext2/3/4 bitmap.
pub fn populate_bitmap(buf: &mut [u8], used_bits: u32, max_valid_bits: u32) {
    buf.fill(0);
    for b in 0..used_bits {
        buf[(b >> 3) as usize] |= 1u8 << (b & 7);
    }
    let total_bits = (buf.len() * 8) as u32;
    for b in max_valid_bits..total_bits {
        buf[(b >> 3) as usize] |= 1u8 << (b & 7);
    }
}

/// Builds a 60-byte `i_block` extent tree for up to 4 contiguous block ranges.
pub fn build_extent_iblock(extents: &[(u32, u16, u32)]) -> [u8; 60] {
    let mut ib = [0u8; 60];
    let entries = extents.len().min(4) as u16;
    ib[0..2].copy_from_slice(&EXT4_EXT_MAGIC.to_le_bytes());
    ib[2..4].copy_from_slice(&entries.to_le_bytes());
    ib[4..6].copy_from_slice(&4u16.to_le_bytes()); // eh_max = 4
    ib[6..8].copy_from_slice(&0u16.to_le_bytes()); // eh_depth = 0
    ib[8..12].copy_from_slice(&0u32.to_le_bytes()); // eh_generation = 0

    for (idx, &(logical_blk, len, phys_blk)) in extents.iter().take(4).enumerate() {
        let off = 12 + idx * 12;
        ib[off..off + 4].copy_from_slice(&logical_blk.to_le_bytes());
        ib[off + 4..off + 6].copy_from_slice(&len.to_le_bytes());
        ib[off + 6..off + 8].copy_from_slice(&0u16.to_le_bytes()); // ee_start_hi
        ib[off + 8..off + 12].copy_from_slice(&phys_blk.to_le_bytes());
    }
    ib
}

/// Allocates direct, single-indirect, and double-indirect blocks for a contiguous range
/// starting at `start_phys_blk` with `num_data_blocks` data blocks (when extents are disabled).
///
/// Returns `(i_block_bytes, total_blocks_consumed, Vec<(indirect_blk_nr, indirect_blk_data)>)`.
#[allow(clippy::type_complexity)]
pub fn build_indirect_mapping(
    start_phys_blk: u32,
    num_data_blocks: u32,
    block_size: u32,
) -> ([u8; 60], u32, Vec<(u32, Vec<u8>)>) {
    let mut ib = [0u8; 60];
    let mut indirect_blocks: Vec<(u32, Vec<u8>)> = Vec::new();
    let ptrs_per_blk = block_size / 4;
    let mut cur_phys = start_phys_blk;

    let mut single_ind_blk = 0u32;
    let mut single_ind_buf = Vec::new();

    let mut double_ind_blk = 0u32;
    let mut double_ind_buf = Vec::new();
    let mut cur_l2_blk = 0u32;
    let mut cur_l2_buf = Vec::new();

    for logical in 0..num_data_blocks {
        if logical < 12 {
            let b = cur_phys;
            cur_phys += 1;
            let off = (logical as usize) * 4;
            ib[off..off + 4].copy_from_slice(&b.to_le_bytes());
        } else if logical - 12 < ptrs_per_blk {
            let idx = (logical - 12) as usize;
            if idx == 0 {
                single_ind_blk = cur_phys;
                cur_phys += 1;
                single_ind_buf = vec![0u8; block_size as usize];
                ib[12 * 4..13 * 4].copy_from_slice(&single_ind_blk.to_le_bytes());
            }
            let b = cur_phys;
            cur_phys += 1;
            single_ind_buf[idx * 4..idx * 4 + 4].copy_from_slice(&b.to_le_bytes());
        } else {
            let rem = logical - 12 - ptrs_per_blk;
            let l1_idx = (rem / ptrs_per_blk) as usize;
            let l2_idx = (rem % ptrs_per_blk) as usize;
            if rem == 0 {
                double_ind_blk = cur_phys;
                cur_phys += 1;
                double_ind_buf = vec![0u8; block_size as usize];
                ib[13 * 4..14 * 4].copy_from_slice(&double_ind_blk.to_le_bytes());
            }
            if l2_idx == 0 {
                if cur_l2_blk != 0 {
                    indirect_blocks.push((cur_l2_blk, std::mem::take(&mut cur_l2_buf)));
                }
                cur_l2_blk = cur_phys;
                cur_phys += 1;
                cur_l2_buf = vec![0u8; block_size as usize];
                double_ind_buf[l1_idx * 4..l1_idx * 4 + 4]
                    .copy_from_slice(&cur_l2_blk.to_le_bytes());
            }
            let b = cur_phys;
            cur_phys += 1;
            cur_l2_buf[l2_idx * 4..l2_idx * 4 + 4].copy_from_slice(&b.to_le_bytes());
        }
    }

    if single_ind_blk != 0 {
        indirect_blocks.push((single_ind_blk, single_ind_buf));
    }
    if cur_l2_blk != 0 {
        indirect_blocks.push((cur_l2_blk, cur_l2_buf));
    }
    if double_ind_blk != 0 {
        indirect_blocks.push((double_ind_blk, double_ind_buf));
    }

    let total_consumed = cur_phys - start_phys_blk;
    (ib, total_consumed, indirect_blocks)
}

/// Parameters for encoding an Ext2/3/4 inode.
pub struct InodeParams {
    pub mode: u16,
    pub uid: u32,
    pub gid: u32,
    pub size: u64,
    pub atime: u32,
    pub ctime: u32,
    pub mtime: u32,
    pub links_count: u16,
    pub blocks_512: u32,
    pub flags: u32,
    pub i_block: [u8; 60],
}

/// Encodes an inode of size `cfg.inode_size` (`128..=1024`).
pub fn build_inode_bytes(cfg: &ExtMkfsConfig, p: &InodeParams) -> Vec<u8> {
    let mut buf = vec![0u8; usize::from(cfg.inode_size)];
    buf[0..2].copy_from_slice(&p.mode.to_le_bytes());
    buf[2..4].copy_from_slice(&(p.uid as u16).to_le_bytes());
    buf[4..8].copy_from_slice(&(p.size as u32).to_le_bytes());
    buf[8..12].copy_from_slice(&p.atime.to_le_bytes());
    buf[12..16].copy_from_slice(&p.ctime.to_le_bytes());
    buf[16..20].copy_from_slice(&p.mtime.to_le_bytes());
    buf[20..24].copy_from_slice(&0u32.to_le_bytes()); // i_dtime
    buf[24..26].copy_from_slice(&(p.gid as u16).to_le_bytes());
    buf[26..28].copy_from_slice(&p.links_count.to_le_bytes());
    buf[28..32].copy_from_slice(&p.blocks_512.to_le_bytes());
    buf[32..36].copy_from_slice(&p.flags.to_le_bytes());
    buf[40..100].copy_from_slice(&p.i_block);
    if (p.mode & S_IFMT) == S_IFREG {
        buf[108..112].copy_from_slice(&((p.size >> 32) as u32).to_le_bytes());
    }
    buf[120..122].copy_from_slice(&((p.uid >> 16) as u16).to_le_bytes());
    buf[122..124].copy_from_slice(&((p.gid >> 16) as u16).to_le_bytes());

    if p.mode != 0 && cfg.inode_size > 128 {
        let extra_isize: u16 = 32;
        buf[128..130].copy_from_slice(&extra_isize.to_le_bytes());
        buf[144..148].copy_from_slice(&cfg.timestamp.to_le_bytes()); // i_crtime
    }
    buf
}

/// Builds a directory block (or multi-block directory) from `(inode, file_type, name)` entries.
pub fn build_directory_blocks(
    entries: &[(u32, u8, &[u8])],
    block_size: u32,
    min_blocks: u32,
    use_filetype: bool,
) -> Vec<Vec<u8>> {
    let bsz = block_size as usize;
    let mut blocks: Vec<Vec<u8>> = Vec::new();
    let mut cur_blk = vec![0u8; bsz];
    let mut cur_off = 0usize;
    let mut prev_off: Option<usize> = None;

    for &(ino, ft, name) in entries {
        let name_len = name.len().min(255);
        let needed = (8 + name_len + 3) & !3;
        if cur_off + needed > bsz {
            if let Some(poff) = prev_off {
                let stretched = (bsz - poff) as u16;
                cur_blk[poff + 4..poff + 6].copy_from_slice(&stretched.to_le_bytes());
            }
            blocks.push(cur_blk);
            cur_blk = vec![0u8; bsz];
            cur_off = 0;
        }
        cur_blk[cur_off..cur_off + 4].copy_from_slice(&ino.to_le_bytes());
        cur_blk[cur_off + 4..cur_off + 6].copy_from_slice(&(needed as u16).to_le_bytes());
        cur_blk[cur_off + 6] = name_len as u8;
        cur_blk[cur_off + 7] = if use_filetype { ft } else { 0 };
        cur_blk[cur_off + 8..cur_off + 8 + name_len].copy_from_slice(&name[..name_len]);
        prev_off = Some(cur_off);
        cur_off += needed;
    }

    if let Some(poff) = prev_off {
        let stretched = (bsz - poff) as u16;
        cur_blk[poff + 4..poff + 6].copy_from_slice(&stretched.to_le_bytes());
        blocks.push(cur_blk);
    }

    while (blocks.len() as u32) < min_blocks {
        let mut empty_blk = vec![0u8; bsz];
        empty_blk[4..6].copy_from_slice(&(block_size as u16).to_le_bytes());
        blocks.push(empty_blk);
    }

    blocks
}

/// Builds the JBD2 Journal Superblock v2 block (`block_size` bytes).
pub fn build_jbd2_superblock(cfg: &ExtMkfsConfig, layout: &ExtFsLayout) -> Vec<u8> {
    let mut blk = vec![0u8; layout.block_size as usize];
    // JBD2 fields are big-endian!
    blk[0..4].copy_from_slice(&JBD2_MAGIC_NUMBER.to_be_bytes());
    blk[4..8].copy_from_slice(&JBD2_SUPERBLOCK_V2.to_be_bytes());
    blk[8..12].copy_from_slice(&0u32.to_be_bytes()); // h_sequence
    blk[12..16].copy_from_slice(&layout.block_size.to_be_bytes());
    blk[16..20].copy_from_slice(&layout.journal_blocks.to_be_bytes());
    blk[20..24].copy_from_slice(&1u32.to_be_bytes()); // s_first = 1
    blk[24..28].copy_from_slice(&1u32.to_be_bytes()); // s_sequence = 1
    blk[28..32].copy_from_slice(&0u32.to_be_bytes()); // s_start = 0
    blk[48..64].copy_from_slice(&cfg.uuid);
    blk[64..68].copy_from_slice(&1u32.to_be_bytes()); // s_nr_users = 1
    blk
}

/// Builds the 1024-byte Ext2/3/4 Superblock for `block_group_nr`.
pub fn build_superblock_bytes(
    cfg: &ExtMkfsConfig,
    layout: &ExtFsLayout,
    block_group_nr: u16,
    jnl_iblock: &[u8; 60],
    kbytes_written: u64,
) -> [u8; 1024] {
    let mut sb = [0u8; 1024];

    sb[0..4].copy_from_slice(&layout.total_inodes.to_le_bytes());
    sb[4..8].copy_from_slice(&layout.total_blocks.to_le_bytes());
    sb[8..12].copy_from_slice(&layout.reserved_blocks.to_le_bytes());
    sb[12..16].copy_from_slice(&layout.total_free_blocks.to_le_bytes());
    sb[16..20].copy_from_slice(&layout.total_free_inodes.to_le_bytes());
    sb[20..24].copy_from_slice(&layout.first_data_block.to_le_bytes());
    sb[24..28].copy_from_slice(&layout.log_block_size.to_le_bytes());
    sb[28..32].copy_from_slice(&layout.log_block_size.to_le_bytes());
    sb[32..36].copy_from_slice(&layout.blocks_per_group.to_le_bytes());
    sb[36..40].copy_from_slice(&layout.blocks_per_group.to_le_bytes());
    sb[40..44].copy_from_slice(&layout.inodes_per_group.to_le_bytes());
    sb[44..48].copy_from_slice(&0u32.to_le_bytes()); // s_mtime = 0
    sb[48..52].copy_from_slice(&cfg.timestamp.to_le_bytes()); // s_wtime
    sb[52..54].copy_from_slice(&0u16.to_le_bytes()); // s_mnt_count = 0
    sb[54..56].copy_from_slice(&(-1i16).to_le_bytes()); // s_max_mnt_count = -1
    sb[56..58].copy_from_slice(&EXT4_SUPER_MAGIC.to_le_bytes());
    let state = if block_group_nr == 0 { EXT2_VALID_FS } else { 0 };
    sb[58..60].copy_from_slice(&state.to_le_bytes());
    sb[60..62].copy_from_slice(&cfg.errors_behavior.to_le_bytes());
    sb[62..64].copy_from_slice(&0u16.to_le_bytes()); // s_minor_rev_level
    sb[64..68].copy_from_slice(&cfg.timestamp.to_le_bytes()); // s_lastcheck
    sb[68..72].copy_from_slice(&0u32.to_le_bytes()); // s_checkinterval
    sb[72..76].copy_from_slice(&cfg.creator_os.to_le_bytes());
    sb[76..80].copy_from_slice(&cfg.rev_level.to_le_bytes());
    sb[80..82].copy_from_slice(&0u16.to_le_bytes()); // s_def_resuid
    sb[82..84].copy_from_slice(&0u16.to_le_bytes()); // s_def_resgid
    sb[84..88].copy_from_slice(&EXT2_GOOD_OLD_FIRST_INO.to_le_bytes());
    sb[88..90].copy_from_slice(&layout.inode_size.to_le_bytes());
    sb[90..92].copy_from_slice(&block_group_nr.to_le_bytes());
    sb[92..96].copy_from_slice(&cfg.feature_compat.to_le_bytes());
    sb[96..100].copy_from_slice(&cfg.feature_incompat.to_le_bytes());
    sb[100..104].copy_from_slice(&cfg.feature_ro_compat.to_le_bytes());
    sb[104..120].copy_from_slice(&cfg.uuid);

    let lbl_bytes = cfg.label.as_bytes();
    let lbl_len = lbl_bytes.len().min(16);
    sb[120..120 + lbl_len].copy_from_slice(&lbl_bytes[..lbl_len]);

    let mnt_bytes = cfg.last_mounted.as_bytes();
    let mnt_len = mnt_bytes.len().min(64);
    sb[136..136 + mnt_len].copy_from_slice(&mnt_bytes[..mnt_len]);

    sb[206..208].copy_from_slice(&layout.reserved_gdt_blocks.to_le_bytes());

    if layout.journal_blocks > 0 {
        sb[224..228].copy_from_slice(&EXT2_JOURNAL_INO.to_le_bytes());
        sb[253] = EXT3_JNL_BACKUP_BLOCKS;
        sb[268..328].copy_from_slice(jnl_iblock);
        let jnl_size_bytes = u64::from(layout.journal_blocks) * u64::from(layout.block_size);
        sb[328..332].copy_from_slice(&((jnl_size_bytes >> 32) as u32).to_le_bytes());
        sb[332..336].copy_from_slice(&(jnl_size_bytes as u32).to_le_bytes());
    }

    sb[236..252].copy_from_slice(&cfg.hash_seed);
    sb[252] = DX_HASH_HALF_MD4;
    if (cfg.feature_incompat & EXT4_FEATURE_INCOMPAT_64BIT) != 0 {
        sb[254..256].copy_from_slice(&layout.desc_size.to_le_bytes());
    }
    sb[256..260].copy_from_slice(&cfg.default_mntopts.to_le_bytes());
    sb[264..268].copy_from_slice(&cfg.timestamp.to_le_bytes()); // s_mkfs_time

    if layout.inode_size > 128 {
        sb[348..350].copy_from_slice(&32u16.to_le_bytes());
        sb[350..352].copy_from_slice(&32u16.to_le_bytes());
    }

    sb[352..356].copy_from_slice(&cfg.super_flags.to_le_bytes());
    sb[356..358].copy_from_slice(&cfg.stride.to_le_bytes());
    sb[360..364].copy_from_slice(&cfg.stripe_width.to_le_bytes());
    if (cfg.feature_incompat & EXT4_FEATURE_INCOMPAT_FLEX_BG) != 0 {
        let log_flex = cfg.flex_bg_size.unwrap_or(16).trailing_zeros() as u8;
        sb[372] = log_flex;
    }
    if (cfg.feature_ro_compat & EXT4_FEATURE_RO_COMPAT_METADATA_CSUM) != 0 {
        sb[375] = EXT4_CRC32C_CHKSUM;
    }
    if block_group_nr == 0 {
        sb[376..384].copy_from_slice(&kbytes_written.to_le_bytes());
    } else {
        sb[376..384].copy_from_slice(&1u64.to_le_bytes());
    }
    sb[584..588].copy_from_slice(&layout.overhead_clusters.to_le_bytes());
    sb[636..638].copy_from_slice(&cfg.encoding.to_le_bytes());
    sb[638..640].copy_from_slice(&cfg.encoding_flags.to_le_bytes());

    sb
}

/// Builds the Group Descriptor Table blocks (`layout.gdt_blocks * layout.block_size` bytes).
pub fn build_gdt_blocks(cfg: &ExtMkfsConfig, layout: &ExtFsLayout) -> Vec<u8> {
    let total_bytes = (layout.gdt_blocks * layout.block_size) as usize;
    let mut gdt = vec![0u8; total_bytes];
    let dsz = usize::from(layout.desc_size);
    let has_gdt_csum = (cfg.feature_ro_compat & EXT4_FEATURE_RO_COMPAT_GDT_CSUM) != 0;

    for g in &layout.groups {
        let off = (g.group_nr as usize) * dsz;
        let desc = &mut gdt[off..off + dsz];
        desc[0..4].copy_from_slice(&g.block_bitmap.to_le_bytes());
        desc[4..8].copy_from_slice(&g.inode_bitmap.to_le_bytes());
        desc[8..12].copy_from_slice(&g.inode_table.to_le_bytes());
        desc[12..14].copy_from_slice(&(g.free_blocks as u16).to_le_bytes());
        desc[14..16].copy_from_slice(&(g.free_inodes as u16).to_le_bytes());
        desc[16..18].copy_from_slice(&g.used_dirs.to_le_bytes());
        desc[18..20].copy_from_slice(&g.bg_flags.to_le_bytes());
        desc[28..30].copy_from_slice(&g.itable_unused.to_le_bytes());
        if dsz >= 64 {
            desc[44..46].copy_from_slice(&((g.free_blocks >> 16) as u16).to_le_bytes());
            desc[46..48].copy_from_slice(&((g.free_inodes >> 16) as u16).to_le_bytes());
            desc[56..58].copy_from_slice(&((g.free_inodes >> 16) as u16).to_le_bytes());
        }
        if has_gdt_csum {
            let csum = gdt_crc16(&cfg.uuid, g.group_nr, desc);
            desc[30..32].copy_from_slice(&csum.to_le_bytes());
        }
    }
    gdt
}
