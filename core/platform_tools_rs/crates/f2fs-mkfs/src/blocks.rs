use crate::config::MkfsConfig;
use crate::crc32::{f2fs_crc32_raw, f2fs_dentry_hash, f2fs_inode_checksum};
use crate::layout::F2fsLayout;
use crate::spec::{
    BLOCKS_PER_SEG, CP_COMPACT_SUM_FLAG, CP_NAT_BITS_FLAG, CP_TRIMMED_FLAG, CP_UMOUNT_FLAG,
    CURSEG_COLD_DATA, CURSEG_COLD_NODE, CURSEG_HOT_DATA, CURSEG_HOT_NODE, CURSEG_WARM_DATA,
    CURSEG_WARM_NODE, F2FS_EXTRA_ATTR_FL, F2FS_FEATURE_COMPRESSION, F2FS_FEATURE_EXTRA_ATTR,
    F2FS_FEATURE_INODE_CHKSUM, F2FS_FEATURE_INODE_CRTIME, F2FS_FEATURE_PRJQUOTA,
    F2FS_FEATURE_SB_CHKSUM, F2FS_FT_DIR, F2FS_IMMUTABLE_FL, F2FS_MAJOR_VERSION,
    F2FS_MAX_EXTENSION, F2FS_META_INO, F2FS_MINOR_VERSION, F2FS_NOATIME_FL, F2FS_NODE_INO,
    F2FS_ROOT_INO, F2FS_SUPER_MAGIC, F2FS_SUPER_OFFSET, F2FS_SUPER_SIZE, SUM_TYPE_NODE,
    V2_INITQMAGICS, V2_INITQVERSION, V2_MAX_DQ_TIME, V2_MAX_IQ_TIME,
};

/// Computes `i_extra_isize` in bytes based on active superblock features.
pub fn compute_extra_isize(features: u32) -> u16 {
    if (features & F2FS_FEATURE_EXTRA_ATTR) == 0 {
        0
    } else if (features & F2FS_FEATURE_COMPRESSION) != 0 {
        36
    } else if (features & F2FS_FEATURE_INODE_CRTIME) != 0 {
        24
    } else if (features & F2FS_FEATURE_INODE_CHKSUM) != 0 {
        12
    } else if (features & F2FS_FEATURE_PRJQUOTA) != 0 {
        8
    } else {
        4
    }
}

/// Builds the 4096-byte page containing the F2FS superblock at byte offset `1024..4096`
/// (written identically to block 0 and block 1).
pub fn build_superblock_page(cfg: &MkfsConfig, layout: &F2fsLayout) -> [u8; 4096] {
    let mut page = [0u8; 4096];
    let sb = &mut page[F2FS_SUPER_OFFSET..F2FS_SUPER_OFFSET + F2FS_SUPER_SIZE];

    let has_sb_csum = (cfg.features & F2FS_FEATURE_SB_CHKSUM) != 0;
    let csum_offset: u32 = if has_sb_csum { 3068 } else { 0 };

    sb[0..4].copy_from_slice(&F2FS_SUPER_MAGIC.to_le_bytes());
    sb[4..6].copy_from_slice(&F2FS_MAJOR_VERSION.to_le_bytes());
    sb[6..8].copy_from_slice(&F2FS_MINOR_VERSION.to_le_bytes());
    sb[8..12].copy_from_slice(&layout.log_sectorsize.to_le_bytes());
    sb[12..16].copy_from_slice(&layout.log_sectors_per_block.to_le_bytes());
    sb[16..20].copy_from_slice(&layout.log_blocksize.to_le_bytes());
    sb[20..24].copy_from_slice(&layout.log_blocks_per_seg.to_le_bytes());
    sb[24..28].copy_from_slice(&cfg.segs_per_sec.to_le_bytes());
    sb[28..32].copy_from_slice(&cfg.secs_per_zone.to_le_bytes());
    sb[32..36].copy_from_slice(&csum_offset.to_le_bytes());
    sb[36..44].copy_from_slice(&layout.total_blocks.to_le_bytes());
    sb[44..48].copy_from_slice(&layout.section_count.to_le_bytes());
    sb[48..52].copy_from_slice(&layout.segment_count.to_le_bytes());
    sb[52..56].copy_from_slice(&layout.segment_count_ckpt.to_le_bytes());
    sb[56..60].copy_from_slice(&layout.segment_count_sit.to_le_bytes());
    sb[60..64].copy_from_slice(&layout.segment_count_nat.to_le_bytes());
    sb[64..68].copy_from_slice(&layout.segment_count_ssa.to_le_bytes());
    sb[68..72].copy_from_slice(&layout.segment_count_main.to_le_bytes());
    sb[72..76].copy_from_slice(&layout.segment0_blkaddr.to_le_bytes());
    sb[76..80].copy_from_slice(&layout.cp_blkaddr.to_le_bytes());
    sb[80..84].copy_from_slice(&layout.sit_blkaddr.to_le_bytes());
    sb[84..88].copy_from_slice(&layout.nat_blkaddr.to_le_bytes());
    sb[88..92].copy_from_slice(&layout.ssa_blkaddr.to_le_bytes());
    sb[92..96].copy_from_slice(&layout.main_blkaddr.to_le_bytes());
    sb[96..100].copy_from_slice(&F2FS_ROOT_INO.to_le_bytes());
    sb[100..104].copy_from_slice(&F2FS_NODE_INO.to_le_bytes());
    sb[104..108].copy_from_slice(&F2FS_META_INO.to_le_bytes());
    sb[108..124].copy_from_slice(&cfg.uuid);

    // UTF-16LE volume name at 124..1148 (max 512 u16 code units).
    for (idx, u) in cfg.label.encode_utf16().take(511).enumerate() {
        let off = 124 + idx * 2;
        sb[off..off + 2].copy_from_slice(&u.to_le_bytes());
    }

    // Cold and hot extensions in extension_list[64][8] at 1152..1664.
    let cold_cnt = cfg.cold_extensions.len().min(F2FS_MAX_EXTENSION);
    let hot_cnt = cfg
        .hot_extensions
        .len()
        .min(F2FS_MAX_EXTENSION - cold_cnt);
    sb[1148..1152].copy_from_slice(&(cold_cnt as u32).to_le_bytes());
    for (idx, ext) in cfg.cold_extensions.iter().take(cold_cnt).enumerate() {
        let slot = &mut sb[1152 + idx * 8..1152 + (idx + 1) * 8];
        let b = ext.as_bytes();
        let n = b.len().min(7);
        slot[..n].copy_from_slice(&b[..n]);
    }
    for (idx, ext) in cfg.hot_extensions.iter().take(hot_cnt).enumerate() {
        let slot_idx = cold_cnt + idx;
        let slot = &mut sb[1152 + slot_idx * 8..1152 + (slot_idx + 1) * 8];
        let b = ext.as_bytes();
        let n = b.len().min(7);
        slot[..n].copy_from_slice(&b[..n]);
    }

    sb[1664..1668].copy_from_slice(&layout.cp_payload.to_le_bytes());
    let ver_bytes = cfg.version_string.as_bytes();
    let ver_len = ver_bytes.len().min(255);
    sb[1668..1668 + ver_len].copy_from_slice(&ver_bytes[..ver_len]);
    sb[1924..1924 + ver_len].copy_from_slice(&ver_bytes[..ver_len]);

    sb[2180..2184].copy_from_slice(&cfg.features.to_le_bytes());
    // qf_ino[3] at 2745..2757
    for (i, &qino) in layout.qf_ino.iter().enumerate() {
        let off = 2745 + i * 4;
        sb[off..off + 4].copy_from_slice(&qino.to_le_bytes());
    }
    sb[2757] = hot_cnt as u8;
    sb[2758..2760].copy_from_slice(&cfg.encoding.to_le_bytes());
    sb[2760..2762].copy_from_slice(&cfg.encoding_flags.to_le_bytes());

    if has_sb_csum {
        let crc = f2fs_crc32_raw(F2FS_SUPER_MAGIC, &sb[..3068]);
        sb[3068..3072].copy_from_slice(&crc.to_le_bytes());
    }

    page
}

/// Builds a 4096-byte F2FS checkpoint header/footer block (`cp_ver` for pack 0, `0` for pack 1).
pub fn build_checkpoint_block(
    cfg: &MkfsConfig,
    layout: &F2fsLayout,
    cp_ver: u64,
    is_pack0: bool,
) -> ([u8; 4096], u32) {
    let mut cp = [0u8; 4096];
    let ver = if is_pack0 { cp_ver } else { 0 };
    cp[0..8].copy_from_slice(&ver.to_le_bytes());
    cp[8..16].copy_from_slice(&layout.user_block_count.to_le_bytes());
    cp[16..24].copy_from_slice(&layout.valid_block_count.to_le_bytes());
    cp[24..28].copy_from_slice(&layout.rsvd_segment_count.to_le_bytes());
    cp[28..32].copy_from_slice(&layout.overprov_segment_count.to_le_bytes());
    cp[32..36].copy_from_slice(&layout.free_segment_count.to_le_bytes());

    // cur_node_segno[8] at 36..68
    for i in 0..8 {
        let v = if i < 3 {
            layout.cur_node_segno[i]
        } else {
            0xffff_ffff
        };
        cp[36 + i * 4..40 + i * 4].copy_from_slice(&v.to_le_bytes());
    }
    // cur_node_blkoff[8] at 68..84
    for i in 0..3 {
        cp[68 + i * 2..70 + i * 2].copy_from_slice(&layout.cur_node_blkoff[i].to_le_bytes());
    }
    // cur_data_segno[8] at 84..116
    for i in 0..8 {
        let v = if i < 3 {
            layout.cur_data_segno[i]
        } else {
            0xffff_ffff
        };
        cp[84 + i * 4..88 + i * 4].copy_from_slice(&v.to_le_bytes());
    }
    // cur_data_blkoff[8] at 116..132
    for i in 0..3 {
        cp[116 + i * 2..118 + i * 2]
            .copy_from_slice(&layout.cur_data_blkoff[i].to_le_bytes());
    }

    let mut flags = CP_UMOUNT_FLAG | CP_COMPACT_SUM_FLAG;
    if cfg.nat_bits {
        flags |= CP_NAT_BITS_FLAG;
    }
    if cfg.trim {
        flags |= CP_TRIMMED_FLAG;
    }
    let cp_pack_total = 6 + layout.cp_payload;
    let cp_pack_start_sum = 1 + layout.cp_payload;
    let csum_offset = 4092u32;

    cp[132..136].copy_from_slice(&flags.to_le_bytes());
    cp[136..140].copy_from_slice(&cp_pack_total.to_le_bytes());
    cp[140..144].copy_from_slice(&cp_pack_start_sum.to_le_bytes());
    cp[144..148].copy_from_slice(&layout.valid_node_count.to_le_bytes());
    cp[148..152].copy_from_slice(&layout.valid_inode_count.to_le_bytes());
    cp[152..156].copy_from_slice(&layout.next_free_nid.to_le_bytes());
    cp[156..160].copy_from_slice(&layout.sit_ver_bitmap_bytesize.to_le_bytes());
    cp[160..164].copy_from_slice(&layout.nat_ver_bitmap_bytesize.to_le_bytes());
    cp[164..168].copy_from_slice(&csum_offset.to_le_bytes());

    let crc = f2fs_crc32_raw(F2FS_SUPER_MAGIC, &cp[..4092]);
    cp[4092..4096].copy_from_slice(&crc.to_le_bytes());
    (cp, crc)
}

fn write_nat_journal_entry(buf: &mut [u8], idx: usize, nid: u32, ino: u32, blkaddr: u32) {
    let off = 2 + idx * 13;
    buf[off..off + 4].copy_from_slice(&nid.to_le_bytes());
    buf[off + 4] = 0; // version
    buf[off + 5..off + 9].copy_from_slice(&ino.to_le_bytes());
    buf[off + 9..off + 13].copy_from_slice(&blkaddr.to_le_bytes());
}

fn write_sit_journal_entry(
    buf: &mut [u8],
    idx: usize,
    segno: u32,
    segtype: u16,
    valid_blocks: u16,
) {
    let off = 509 + idx * 78;
    buf[off..off + 4].copy_from_slice(&segno.to_le_bytes());
    let vblocks = (segtype << 10) | (valid_blocks & 0x03ff);
    buf[off + 4..off + 6].copy_from_slice(&vblocks.to_le_bytes());
    let vmap = &mut buf[off + 6..off + 70];
    for b in 0..valid_blocks {
        vmap[(b >> 3) as usize] |= 0x80 >> (b & 7);
    }
}

fn write_summary_entry(buf: &mut [u8], off: usize, nid: u32, version: u8, ofs_in_node: u16) {
    buf[off..off + 4].copy_from_slice(&nid.to_le_bytes());
    buf[off + 4] = version;
    buf[off + 5..off + 7].copy_from_slice(&ofs_in_node.to_le_bytes());
}

/// Builds the compact summary block (`blk cp + 1 + cp_payload`) containing the NAT journal,
/// SIT journal, and compact data summary entries.
pub fn build_compact_summary_block(layout: &F2fsLayout) -> [u8; 4096] {
    let mut blk = [0u8; 4096];

    // 1. NAT journal at 0..507
    let mut nat_idx = 0usize;
    write_nat_journal_entry(
        &mut blk,
        nat_idx,
        F2FS_ROOT_INO,
        F2FS_ROOT_INO,
        layout.root_node_blkaddr,
    );
    nat_idx += 1;

    for &(_, q_ino, q_nblk, _) in &layout.active_quotas {
        write_nat_journal_entry(&mut blk, nat_idx, q_ino, q_ino, q_nblk);
        nat_idx += 1;
    }
    if let Some((lf_ino, lf_nblk, _)) = layout.lost_found {
        write_nat_journal_entry(&mut blk, nat_idx, lf_ino, lf_ino, lf_nblk);
        nat_idx += 1;
    }
    blk[0..2].copy_from_slice(&(nat_idx as u16).to_le_bytes());

    // 2. SIT journal at 507..1014 (6 active cursegs)
    blk[507..509].copy_from_slice(&6u16.to_le_bytes());
    write_sit_journal_entry(
        &mut blk,
        0,
        layout.cur_data_segno[0],
        CURSEG_HOT_DATA,
        layout.cur_data_blkoff[0],
    );
    write_sit_journal_entry(
        &mut blk,
        1,
        layout.cur_data_segno[1],
        CURSEG_WARM_DATA,
        layout.cur_data_blkoff[1],
    );
    write_sit_journal_entry(
        &mut blk,
        2,
        layout.cur_data_segno[2],
        CURSEG_COLD_DATA,
        layout.cur_data_blkoff[2],
    );
    write_sit_journal_entry(
        &mut blk,
        3,
        layout.cur_node_segno[0],
        CURSEG_HOT_NODE,
        layout.cur_node_blkoff[0],
    );
    write_sit_journal_entry(
        &mut blk,
        4,
        layout.cur_node_segno[1],
        CURSEG_WARM_NODE,
        layout.cur_node_blkoff[1],
    );
    write_sit_journal_entry(
        &mut blk,
        5,
        layout.cur_node_segno[2],
        CURSEG_COLD_NODE,
        layout.cur_node_blkoff[2],
    );

    // 3. Compact HOT_DATA summaries starting at offset 1014 (7 bytes each)
    let mut sum_off = 1014usize;
    write_summary_entry(&mut blk, sum_off, F2FS_ROOT_INO, 0, 0);
    sum_off += 7;
    for &(_, q_ino, _, _) in &layout.active_quotas {
        write_summary_entry(&mut blk, sum_off, q_ino, 0, 0);
        sum_off += 7;
        write_summary_entry(&mut blk, sum_off, q_ino, 0, 1);
        sum_off += 7;
    }
    if let Some((lf_ino, _, _)) = layout.lost_found {
        write_summary_entry(&mut blk, sum_off, lf_ino, 0, 0);
    }

    blk
}

/// Builds the 3 node curseg summary blocks (`HOT_NODE`, `WARM_NODE`, `COLD_NODE`).
pub fn build_node_summary_blocks(layout: &F2fsLayout) -> [[u8; 4096]; 3] {
    let mut out = [[0u8; 4096]; 3];

    let mut idx = 0usize;
    write_summary_entry(&mut out[0], idx * 7, F2FS_ROOT_INO, 0, 0);
    idx += 1;
    for &(_, q_ino, _, _) in &layout.active_quotas {
        write_summary_entry(&mut out[0], idx * 7, q_ino, 0, 0);
        idx += 1;
    }
    if let Some((lf_ino, _, _)) = layout.lost_found {
        write_summary_entry(&mut out[0], idx * 7, lf_ino, 0, 0);
    }

    for b in &mut out {
        b[4091] = SUM_TYPE_NODE;
    }
    out
}

/// Builds the `nat_bits` block placed at the end of checkpoint segment 0 (`cp_blkaddr + 511`).
pub fn build_nat_bits_block(layout: &F2fsLayout, cp_ver: u64, cp_crc: u32) -> [u8; 4096] {
    let mut blk = [0u8; 4096];
    blk[0..4].copy_from_slice(&(cp_ver as u32).to_le_bytes());
    blk[4..8].copy_from_slice(&cp_crc.to_le_bytes());

    let nat_blocks = (layout.segment_count_nat / 2) * BLOCKS_PER_SEG;
    let nat_bits_bytes = (nat_blocks / 8) as usize;
    let empty_start = 8 + nat_bits_bytes;
    let empty_end = (empty_start + nat_bits_bytes).min(4096);
    if empty_start < empty_end {
        blk[empty_start..empty_end].fill(0xff);
        // NAT block 0 is non-empty (holds reserved NIDs), so clear bit 0.
        blk[empty_start] = 0xfe;
    }
    blk
}

/// Builds NAT block 0 (`nat_blkaddr`) with entries for `F2FS_NODE_INO` (1) and `F2FS_META_INO` (2).
pub fn build_nat_block0() -> [u8; 4096] {
    let mut blk = [0u8; 4096];
    // Each f2fs_nat_entry is 9 bytes: version(1) + ino(4) + block_addr(4).
    for ino in [F2FS_NODE_INO, F2FS_META_INO] {
        let off = ino as usize * 9;
        blk[off] = 0;
        blk[off + 1..off + 5].copy_from_slice(&ino.to_le_bytes());
        blk[off + 5..off + 9].copy_from_slice(&1u32.to_le_bytes());
    }
    blk
}

#[allow(clippy::too_many_arguments)]
fn build_inode_common(
    cfg: &MkfsConfig,
    ino: u32,
    this_node_blk: u32,
    mode: u16,
    links: u32,
    size_bytes: u64,
    blocks_used: u64,
    current_depth: u32,
    i_flags: u32,
    pino: u32,
    name: &[u8],
    data_addrs: &[u32],
) -> [u8; 4096] {
    let mut blk = [0u8; 4096];
    let extra_isize = compute_extra_isize(cfg.features);
    let i_inline = if extra_isize > 0 {
        F2FS_EXTRA_ATTR_FL
    } else {
        0
    };

    blk[0..2].copy_from_slice(&mode.to_le_bytes());
    blk[2] = 0; // i_advise
    blk[3] = i_inline;
    blk[4..8].copy_from_slice(&cfg.root_uid.to_le_bytes());
    blk[8..12].copy_from_slice(&cfg.root_gid.to_le_bytes());
    blk[12..16].copy_from_slice(&links.to_le_bytes());
    blk[16..24].copy_from_slice(&size_bytes.to_le_bytes());
    blk[24..32].copy_from_slice(&blocks_used.to_le_bytes());
    blk[32..40].copy_from_slice(&cfg.timestamp.to_le_bytes());
    blk[40..48].copy_from_slice(&cfg.timestamp.to_le_bytes());
    blk[48..56].copy_from_slice(&cfg.timestamp.to_le_bytes());
    blk[72..76].copy_from_slice(&current_depth.to_le_bytes());
    blk[80..84].copy_from_slice(&i_flags.to_le_bytes());
    blk[84..88].copy_from_slice(&pino.to_le_bytes());
    if !name.is_empty() {
        let n = name.len().min(255);
        blk[88..92].copy_from_slice(&(n as u32).to_le_bytes());
        blk[92..92 + n].copy_from_slice(&name[..n]);
    }

    if extra_isize > 0 {
        blk[360..362].copy_from_slice(&extra_isize.to_le_bytes());
        if (cfg.features & F2FS_FEATURE_INODE_CRTIME) != 0 && extra_isize >= 24 {
            blk[372..380].copy_from_slice(&cfg.timestamp.to_le_bytes());
        }
    }

    let addr_base = 360 + usize::from(extra_isize);
    for (idx, &addr) in data_addrs.iter().enumerate() {
        let off = addr_base + idx * 4;
        blk[off..off + 4].copy_from_slice(&addr.to_le_bytes());
    }

    // Node footer at 4072..4096
    let next_blk = this_node_blk + 1;
    blk[4072..4076].copy_from_slice(&ino.to_le_bytes());
    blk[4076..4080].copy_from_slice(&ino.to_le_bytes());
    blk[4080..4084].copy_from_slice(&0u32.to_le_bytes());
    blk[4084..4092].copy_from_slice(&1u64.to_le_bytes());
    blk[4092..4096].copy_from_slice(&next_blk.to_le_bytes());

    if (cfg.features & F2FS_FEATURE_INODE_CHKSUM) != 0 && extra_isize >= 12 {
        let csum = f2fs_inode_checksum(&cfg.uuid, ino, 0, &blk);
        blk[368..372].copy_from_slice(&csum.to_le_bytes());
    }

    blk
}

/// Builds the root directory inode block (`nid = 3`).
pub fn build_root_inode_block(cfg: &MkfsConfig, layout: &F2fsLayout) -> [u8; 4096] {
    let links = if layout.lost_found.is_some() { 3 } else { 2 };
    build_inode_common(
        cfg,
        F2FS_ROOT_INO,
        layout.root_node_blkaddr,
        0o040755,
        links,
        4096,
        2,
        1,
        0,
        0,
        b"",
        &[layout.root_data_blkaddr],
    )
}

/// Builds a quota file inode block (`nid = q_ino`, 2 data blocks, 6144 bytes).
pub fn build_quota_inode_block(
    cfg: &MkfsConfig,
    q_ino: u32,
    node_blk: u32,
    data_blk0: u32,
) -> [u8; 4096] {
    build_inode_common(
        cfg,
        q_ino,
        node_blk,
        0o100600,
        1,
        6144,
        3,
        0,
        F2FS_IMMUTABLE_FL | F2FS_NOATIME_FL,
        0,
        b"",
        &[data_blk0, data_blk0 + 1],
    )
}

/// Builds the `lost+found` directory inode block (`drwx------`, `0o040700`).
pub fn build_lost_found_inode_block(
    cfg: &MkfsConfig,
    lf_ino: u32,
    node_blk: u32,
    data_blk: u32,
) -> [u8; 4096] {
    build_inode_common(
        cfg,
        lf_ino,
        node_blk,
        0o040700,
        2,
        4096,
        2,
        1,
        0,
        F2FS_ROOT_INO,
        b"lost+found",
        &[data_blk],
    )
}

/// Builds an `f2fs_dentry_block` for a directory containing `.`, `..`, and optional `extra_entries`.
pub fn build_dir_dentry_block(
    self_ino: u32,
    parent_ino: u32,
    extra_entries: &[(&[u8], u32, u8)],
) -> [u8; 4096] {
    let mut blk = [0u8; 4096];
    let mut all_entries: Vec<(&[u8], u32, u8)> = Vec::with_capacity(2 + extra_entries.len());
    all_entries.push((b".", self_ino, F2FS_FT_DIR));
    all_entries.push((b"..", parent_ino, F2FS_FT_DIR));
    all_entries.extend_from_slice(extra_entries);

    let mut slot = 0usize;
    for (name, ino, ftype) in all_entries {
        let slots = name.len().div_ceil(8).max(1);
        for s in slot..slot + slots {
            blk[s >> 3] |= 1 << (s & 7);
        }

        let dentry_off = 30 + slot * 11;
        let hash = f2fs_dentry_hash(name);
        blk[dentry_off..dentry_off + 4].copy_from_slice(&hash.to_le_bytes());
        blk[dentry_off + 4..dentry_off + 8].copy_from_slice(&ino.to_le_bytes());
        blk[dentry_off + 8..dentry_off + 10].copy_from_slice(&(name.len() as u16).to_le_bytes());
        blk[dentry_off + 10] = ftype;

        let fname_off = 2384 + slot * 8;
        blk[fname_off..fname_off + name.len()].copy_from_slice(name);
        slot += slots;
    }

    blk
}

/// Builds the 2 data blocks (8192 bytes) of a Linux VFS quota v2r1 (`vfsv1`) file.
pub fn build_quota_data_blocks(
    qtype: usize,
    owner_id: u32,
    cur_inodes: u64,
    cur_space_bytes: u64,
) -> ([u8; 4096], [u8; 4096]) {
    let mut blk0 = [0u8; 4096];
    let mut blk1 = [0u8; 4096];

    // Block 0: v2_disk_dqheader (8B) + v2_disk_dqinfo (24B) + qtree levels 0..2 at 1024, 2048, 3072
    blk0[0..4].copy_from_slice(&V2_INITQMAGICS[qtype].to_le_bytes());
    blk0[4..8].copy_from_slice(&V2_INITQVERSION.to_le_bytes());
    blk0[8..12].copy_from_slice(&V2_MAX_DQ_TIME.to_le_bytes());
    blk0[12..16].copy_from_slice(&V2_MAX_IQ_TIME.to_le_bytes());
    blk0[16..20].copy_from_slice(&0u32.to_le_bytes()); // dqi_flags
    blk0[20..24].copy_from_slice(&6u32.to_le_bytes()); // dqi_blocks
    blk0[24..28].copy_from_slice(&0u32.to_le_bytes()); // dqi_free_blk
    blk0[28..32].copy_from_slice(&5u32.to_le_bytes()); // dqi_free_entry

    blk0[1024..1028].copy_from_slice(&2u32.to_le_bytes());
    blk0[2048..2052].copy_from_slice(&3u32.to_le_bytes());
    blk0[3072..3076].copy_from_slice(&4u32.to_le_bytes());

    // Block 1: qtree level 3 at offset 0 -> block 5, leaf block 5 at offset 1024
    blk1[0..4].copy_from_slice(&5u32.to_le_bytes());
    // qt_disk_dqdbheader: next_free(4)=0, prev_free(4)=0, entries(2)=1
    blk1[1032..1034].copy_from_slice(&1u16.to_le_bytes());
    // v2r1_disk_dqblk at offset 1040 (72 bytes)
    blk1[1040..1044].copy_from_slice(&owner_id.to_le_bytes());
    blk1[1064..1072].copy_from_slice(&cur_inodes.to_le_bytes());
    blk1[1088..1096].copy_from_slice(&cur_space_bytes.to_le_bytes());

    (blk0, blk1)
}
