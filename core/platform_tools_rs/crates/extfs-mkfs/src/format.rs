//! High-level Ext2 / Ext3 / Ext4 filesystem formatter and CLI runner (MIT clean-room).

use std::collections::BTreeMap;
use std::fs::{self, OpenOptions};
use std::io::{Seek, SeekFrom, Write};
use std::path::Path;

use android_sparse::SparseImage;

use crate::blocks::*;
use crate::config::*;
use crate::layout::ExtFsLayout;
use crate::spec::*;

struct AllocatedInode {
    ino: u32,
    params: InodeParams,
}

/// Formats an Ext2/Ext3/Ext4 filesystem according to `cfg`.
pub fn format_extfs(mut cfg: ExtMkfsConfig) -> Result<ExtFsLayout, ExtMkfsError> {
    if !cfg.quiet {
        eprint!("{}", version_banner());
    }

    let total_bytes = resolve_volume_size(&cfg)?;
    let mut layout = ExtFsLayout::compute(&mut cfg, total_bytes)?;

    if cfg.dry_run {
        print_dry_run_summary(&layout);
        return Ok(layout);
    }

    let bsz = layout.block_size as usize;
    let sectors_per_blk = layout.block_size / 512;
    let has_extents = (cfg.feature_incompat & EXT4_FEATURE_INCOMPAT_EXTENTS) != 0;
    let use_filetype = (cfg.feature_incompat & EXT2_FEATURE_INCOMPAT_FILETYPE) != 0;
    let uninit_bg = (cfg.feature_ro_compat
        & (EXT4_FEATURE_RO_COMPAT_GDT_CSUM | EXT4_FEATURE_RO_COMPAT_METADATA_CSUM))
        != 0;

    // Map of physical block number -> block payload (each `bsz` bytes).
    let mut data_blocks: BTreeMap<u32, Vec<u8>> = BTreeMap::new();
    // Map of inode number (1-based) -> AllocatedInode.
    let mut inodes: BTreeMap<u32, AllocatedInode> = BTreeMap::new();
    // Per-group highest allocated inode number (1-based local index within group).
    let mut group_used_inodes = vec![0u32; layout.group_count as usize];
    group_used_inodes[0] = EXT2_GOOD_OLD_FIRST_INO; // Inodes 1..11 reserved in Group 0

    // 0. Build Bad Blocks Inode (Inode 1 = EXT2_BAD_INO)
    inodes.insert(
        EXT2_BAD_INO,
        AllocatedInode {
            ino: EXT2_BAD_INO,
            params: InodeParams {
                mode: 0,
                uid: 0,
                gid: 0,
                size: 0,
                atime: cfg.timestamp,
                ctime: cfg.timestamp,
                mtime: cfg.timestamp,
                links_count: 0,
                blocks_512: 0,
                flags: 0,
                i_block: [0u8; 60],
            },
        },
    );

    // 1. Build `lost+found` directory (Inode 11)
    let lf_entries: Vec<(u32, u8, &[u8])> = vec![
        (EXT2_GOOD_OLD_FIRST_INO, EXT2_FT_DIR, b"."),
        (EXT2_ROOT_INO, EXT2_FT_DIR, b".."),
    ];
    let lf_dir_blks = build_directory_blocks(
        &lf_entries,
        layout.block_size,
        layout.lost_found_blocks,
        use_filetype,
    );
    for (idx, blk_data) in lf_dir_blks.into_iter().enumerate() {
        data_blocks.insert(layout.lost_found_start_block + idx as u32, blk_data);
    }
    let lf_iblock = if has_extents {
        build_extent_iblock(&[(
            0,
            layout.lost_found_blocks as u16,
            layout.lost_found_start_block,
        )])
    } else {
        let (ib, _, _) = build_indirect_mapping(
            layout.lost_found_start_block,
            layout.lost_found_blocks,
            layout.block_size,
        );
        ib
    };
    inodes.insert(
        EXT2_GOOD_OLD_FIRST_INO,
        AllocatedInode {
            ino: EXT2_GOOD_OLD_FIRST_INO,
            params: InodeParams {
                mode: S_IFDIR | 0o700,
                uid: cfg.root_uid,
                gid: cfg.root_gid,
                size: u64::from(layout.lost_found_blocks) * u64::from(layout.block_size),
                atime: cfg.timestamp,
                ctime: cfg.timestamp,
                mtime: cfg.timestamp,
                links_count: 2,
                blocks_512: layout.lost_found_blocks * sectors_per_blk,
                flags: if has_extents { EXT4_EXTENTS_FL } else { 0 },
                i_block: lf_iblock,
            },
        },
    );

    // 2. Build JBD2 Journal (Inode 8) if enabled
    let mut jnl_iblock = [0u8; 60];
    if layout.journal_blocks > 0 {
        let jnl_sb_blk = build_jbd2_superblock(&cfg, &layout);
        if has_extents {
            jnl_iblock = build_extent_iblock(&[(
                0,
                layout.journal_blocks as u16,
                layout.journal_start_block,
            )]);
            data_blocks.insert(layout.journal_start_block, jnl_sb_blk);
        } else {
            let (ib, _, ind_blks) = build_indirect_mapping(
                layout.journal_start_block,
                layout.journal_blocks,
                layout.block_size,
            );
            jnl_iblock = ib;
            data_blocks.insert(layout.journal_start_block, jnl_sb_blk);
            for (ind_nr, ind_data) in ind_blks {
                data_blocks.insert(ind_nr, ind_data);
            }
        }
        inodes.insert(
            EXT2_JOURNAL_INO,
            AllocatedInode {
                ino: EXT2_JOURNAL_INO,
                params: InodeParams {
                    mode: S_IFREG | 0o600,
                    uid: 0,
                    gid: 0,
                    size: u64::from(layout.journal_blocks) * u64::from(layout.block_size),
                    atime: cfg.timestamp,
                    ctime: cfg.timestamp,
                    mtime: cfg.timestamp,
                    links_count: 1,
                    blocks_512: layout.journal_total_blocks * sectors_per_blk,
                    flags: if has_extents { EXT4_EXTENTS_FL } else { 0 },
                    i_block: jnl_iblock,
                },
            },
        );
    }

    // 3. Populate root directory (and optional `-d <root_dir>` tree)
    let mut next_ino = EXT2_GOOD_OLD_FIRST_INO + 1;
    let mut root_extra_entries: Vec<(u32, u8, Vec<u8>)> = Vec::new();
    let mut root_subdirs = 1u16; // `lost+found`

    if let Some(root_dir) = cfg.root_dir.clone() {
        let (entries, subdirs) = populate_from_host_dir(
            &root_dir,
            EXT2_ROOT_INO,
            &cfg,
            &mut layout,
            &mut next_ino,
            &mut group_used_inodes,
            &mut inodes,
            &mut data_blocks,
            uninit_bg,
        )?;
        root_extra_entries = entries;
        root_subdirs += subdirs;
    }

    let mut root_entries_refs: Vec<(u32, u8, &[u8])> = vec![
        (EXT2_ROOT_INO, EXT2_FT_DIR, b"."),
        (EXT2_ROOT_INO, EXT2_FT_DIR, b".."),
        (EXT2_GOOD_OLD_FIRST_INO, EXT2_FT_DIR, b"lost+found"),
    ];
    for (ino, ft, name) in &root_extra_entries {
        root_entries_refs.push((*ino, *ft, name.as_slice()));
    }
    let root_dir_blks =
        build_directory_blocks(&root_entries_refs, layout.block_size, 1, use_filetype);
    let root_n_blks = root_dir_blks.len() as u32;

    let root_iblock = if root_n_blks == 1 {
        data_blocks.insert(layout.root_dir_block, root_dir_blks.into_iter().next().unwrap());
        if has_extents {
            build_extent_iblock(&[(0, 1, layout.root_dir_block)])
        } else {
            let (ib, _, _) = build_indirect_mapping(layout.root_dir_block, 1, layout.block_size);
            ib
        }
    } else {
        // Multi-block root directory: allocate contiguous blocks
        let start_b = allocate_contiguous_blocks(&mut layout, root_n_blks)?;
        for (idx, blk_data) in root_dir_blks.into_iter().enumerate() {
            data_blocks.insert(start_b + idx as u32, blk_data);
        }
        if has_extents {
            build_extent_iblock(&[(0, root_n_blks as u16, start_b)])
        } else {
            let (ib, _, ind_blks) =
                build_indirect_mapping(start_b, root_n_blks, layout.block_size);
            for (ind_nr, ind_data) in ind_blks {
                data_blocks.insert(ind_nr, ind_data);
            }
            ib
        }
    };

    inodes.insert(
        EXT2_ROOT_INO,
        AllocatedInode {
            ino: EXT2_ROOT_INO,
            params: InodeParams {
                mode: S_IFDIR | 0o755,
                uid: cfg.root_uid,
                gid: cfg.root_gid,
                size: u64::from(root_n_blks) * u64::from(layout.block_size),
                atime: cfg.timestamp,
                ctime: cfg.timestamp,
                mtime: cfg.timestamp,
                links_count: 2 + root_subdirs,
                blocks_512: root_n_blks * sectors_per_blk,
                flags: if has_extents { EXT4_EXTENTS_FL } else { 0 },
                i_block: root_iblock,
            },
        },
    );

    // Recompute total free counts after any `-d` allocations
    layout.total_free_blocks = layout.groups.iter().map(|g| g.free_blocks).sum();
    layout.total_free_inodes = layout.groups.iter().map(|g| g.free_inodes).sum();

    // 4. Materialize Inode Table blocks that contain allocated inodes
    let inodes_per_blk = layout.block_size / u32::from(layout.inode_size);
    let isz = usize::from(layout.inode_size);
    for alloc_ino in inodes.values() {
        let g_idx = ((alloc_ino.ino - 1) / layout.inodes_per_group) as usize;
        let local_idx = (alloc_ino.ino - 1) % layout.inodes_per_group;
        let blk_off = local_idx / inodes_per_blk;
        let idx_in_blk = (local_idx % inodes_per_blk) as usize;
        let phys_blk = layout.groups[g_idx].inode_table + blk_off;

        let blk_buf = data_blocks
            .entry(phys_blk)
            .or_insert_with(|| vec![0u8; bsz]);
        let ibytes = build_inode_bytes(&cfg, &alloc_ino.params);
        blk_buf[idx_in_blk * isz..(idx_in_blk + 1) * isz].copy_from_slice(&ibytes);
    }

    // Ensure Group 0's first inode table block is always present
    data_blocks
        .entry(layout.groups[0].inode_table)
        .or_insert_with(|| vec![0u8; bsz]);

    // 5. Build per-group Block Bitmaps and Inode Bitmaps (skipping UNINIT bitmaps)
    for (g_idx, g) in layout.groups.iter().enumerate() {
        if (g.bg_flags & EXT2_BG_BLOCK_UNINIT) == 0 {
            let mut bmap = vec![0u8; bsz];
            populate_bitmap(&mut bmap, g.allocated_blocks, g.block_count);
            data_blocks.insert(g.block_bitmap, bmap);
        }

        if (g.bg_flags & EXT2_BG_INODE_UNINIT) == 0 {
            let mut imap = vec![0u8; bsz];
            populate_bitmap(&mut imap, group_used_inodes[g_idx], layout.inodes_per_group);
            data_blocks.insert(g.inode_bitmap, imap);
        }
    }

    // 6. Build Group Descriptor Table & Primary/Backup Superblocks
    let gdt_bytes = build_gdt_blocks(&cfg, &layout);
    let written_blks_approx = data_blocks.len() as u64
        + u64::from(
            layout
                .groups
                .iter()
                .filter(|g| g.has_super)
                .map(|_| 1 + layout.gdt_blocks)
                .sum::<u32>(),
        );
    let kbytes_written = (written_blks_approx * u64::from(layout.block_size)) / 1024;

    for g in &layout.groups {
        if !g.has_super {
            continue;
        }
        let sb_bytes =
            build_superblock_bytes(&cfg, &layout, g.group_nr as u16, &jnl_iblock, kbytes_written);
        let mut sb_blk = vec![0u8; bsz];
        if g.group_nr == 0 && layout.block_size > 1024 {
            sb_blk[1024..2048].copy_from_slice(&sb_bytes);
        } else {
            sb_blk[0..1024].copy_from_slice(&sb_bytes);
        }
        data_blocks.insert(g.start_block, sb_blk);

        for b_idx in 0..layout.gdt_blocks {
            let start = (b_idx as usize) * bsz;
            let end = start + bsz;
            data_blocks.insert(g.start_block + 1 + b_idx, gdt_bytes[start..end].to_vec());
        }
    }

    // 7. Write either Android Sparse Image (`-E android_sparse`) or Raw Image
    if cfg.android_sparse {
        let mut simg = SparseImage::new(
            std::num::NonZeroU32::new(layout.block_size).unwrap(),
            layout.total_blocks,
        )?;
        for (&blk_nr, blk_data) in &data_blocks {
            simg.add_raw_blocks(blk_nr, blk_data)?;
        }
        let mut f = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&cfg.device_path)?;
        simg.write_to(&mut f, false)?;
        f.flush()?;
    } else {
        let mut f = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .truncate(false)
            .open(&cfg.device_path)?;
        let target_len = cfg.offset_bytes
            + u64::from(layout.total_blocks) * u64::from(layout.block_size);
        let cur_len = f.metadata()?.len();
        if cur_len < target_len {
            f.set_len(target_len)?;
        }

        // If lazy_itable_init is false, zero out all inode table blocks explicitly
        if !cfg.lazy_itable_init {
            let zero_blk = vec![0u8; bsz];
            for g in &layout.groups {
                for b in g.inode_table..g.inode_table + g.inode_table_blocks {
                    if !data_blocks.contains_key(&b) {
                        f.seek(SeekFrom::Start(
                            cfg.offset_bytes + u64::from(b) * u64::from(layout.block_size),
                        ))?;
                        f.write_all(&zero_blk)?;
                    }
                }
            }
        }

        for (&blk_nr, blk_data) in &data_blocks {
            f.seek(SeekFrom::Start(
                cfg.offset_bytes + u64::from(blk_nr) * u64::from(layout.block_size),
            ))?;
            f.write_all(blk_data)?;
        }
        f.flush()?;
    }

    if !cfg.quiet {
        println!(
            "Creating filesystem with {} {}k blocks and {} inodes",
            layout.total_blocks,
            layout.block_size / 1024,
            layout.total_inodes
        );
    }

    Ok(layout)
}

fn allocate_contiguous_blocks(
    layout: &mut ExtFsLayout,
    count: u32,
) -> Result<u32, ExtMkfsError> {
    if count == 0 {
        return Ok(0);
    }
    for g in &mut layout.groups {
        if g.free_blocks >= count {
            let start = g.start_block + g.allocated_blocks;
            g.allocated_blocks += count;
            g.free_blocks -= count;
            g.bg_flags &= !EXT2_BG_BLOCK_UNINIT;
            return Ok(start);
        }
    }
    Err(ExtMkfsError::VolumeTooSmall(u64::from(layout.total_blocks)))
}

fn allocate_inode_nr(
    layout: &mut ExtFsLayout,
    next_ino: &mut u32,
    group_used_inodes: &mut [u32],
    is_dir: bool,
    uninit_bg: bool,
) -> Result<u32, ExtMkfsError> {
    let ino = *next_ino;
    if ino > layout.total_inodes {
        return Err(ExtMkfsError::InvalidArgument("out of inodes".into()));
    }
    *next_ino += 1;
    let g_idx = ((ino - 1) / layout.inodes_per_group) as usize;
    let local_used = ((ino - 1) % layout.inodes_per_group) + 1;
    if local_used > group_used_inodes[g_idx] {
        group_used_inodes[g_idx] = local_used;
    }
    let grp = &mut layout.groups[g_idx];
    grp.free_inodes = layout.inodes_per_group - group_used_inodes[g_idx];
    grp.bg_flags &= !EXT2_BG_INODE_UNINIT;
    if uninit_bg {
        grp.itable_unused = grp.free_inodes as u16;
    }
    if is_dir {
        grp.used_dirs += 1;
    }
    Ok(ino)
}

#[allow(clippy::too_many_arguments, clippy::type_complexity)]
fn populate_from_host_dir(
    dir_path: &Path,
    parent_ino: u32,
    cfg: &ExtMkfsConfig,
    layout: &mut ExtFsLayout,
    next_ino: &mut u32,
    group_used_inodes: &mut [u32],
    inodes: &mut BTreeMap<u32, AllocatedInode>,
    data_blocks: &mut BTreeMap<u32, Vec<u8>>,
    uninit_bg: bool,
) -> Result<(Vec<(u32, u8, Vec<u8>)>, u16), ExtMkfsError> {
    let mut rd: Vec<_> = fs::read_dir(dir_path)?.collect::<Result<Vec<_>, _>>()?;
    rd.sort_by_key(|e| e.file_name());

    let bsz = layout.block_size as usize;
    let sectors_per_blk = layout.block_size / 512;
    let has_extents = (cfg.feature_incompat & EXT4_FEATURE_INCOMPAT_EXTENTS) != 0;
    let use_filetype = (cfg.feature_incompat & EXT2_FEATURE_INCOMPAT_FILETYPE) != 0;

    let mut dir_entries: Vec<(u32, u8, Vec<u8>)> = Vec::new();
    let mut subdir_count = 0u16;

    for entry in rd {
        let name_os = entry.file_name();
        let name_str = name_os.to_string_lossy();
        if name_str == "." || name_str == ".." || (parent_ino == EXT2_ROOT_INO && name_str == "lost+found") {
            continue;
        }
        let name_bytes = name_str.as_bytes().to_vec();
        let meta = fs::symlink_metadata(entry.path())?;
        let ftype = meta.file_type();

        if ftype.is_dir() {
            subdir_count += 1;
            let ino = allocate_inode_nr(layout, next_ino, group_used_inodes, true, uninit_bg)?;
            let (child_entries, child_subdirs) = populate_from_host_dir(
                &entry.path(),
                ino,
                cfg,
                layout,
                next_ino,
                group_used_inodes,
                inodes,
                data_blocks,
                uninit_bg,
            )?;

            let mut full_children: Vec<(u32, u8, &[u8])> = vec![
                (ino, EXT2_FT_DIR, b"."),
                (parent_ino, EXT2_FT_DIR, b".."),
            ];
            for (c_ino, c_ft, c_name) in &child_entries {
                full_children.push((*c_ino, *c_ft, c_name.as_slice()));
            }
            let d_blks = build_directory_blocks(&full_children, layout.block_size, 1, use_filetype);
            let n_blks = d_blks.len() as u32;
            let (iblock, total_blks) = if has_extents {
                let start_b = allocate_contiguous_blocks(layout, n_blks)?;
                for (idx, bdata) in d_blks.into_iter().enumerate() {
                    data_blocks.insert(start_b + idx as u32, bdata);
                }
                (build_extent_iblock(&[(0, n_blks as u16, start_b)]), n_blks)
            } else {
                let ind_cnt = crate::layout::count_indirect_blocks(n_blks, layout.block_size);
                let start_b = allocate_contiguous_blocks(layout, n_blks + ind_cnt)?;
                let (ib, tot, ind_blks) =
                    build_indirect_mapping(start_b, n_blks, layout.block_size);
                let ind_map: BTreeMap<u32, Vec<u8>> = ind_blks.into_iter().collect();
                let mut cur_p = start_b;
                for bdata in d_blks {
                    while ind_map.contains_key(&cur_p) {
                        cur_p += 1;
                    }
                    data_blocks.insert(cur_p, bdata);
                    cur_p += 1;
                }
                for (ind_nr, ind_data) in ind_map {
                    data_blocks.insert(ind_nr, ind_data);
                }
                (ib, tot)
            };

            inodes.insert(
                ino,
                AllocatedInode {
                    ino,
                    params: InodeParams {
                        mode: S_IFDIR | 0o755,
                        uid: cfg.root_uid,
                        gid: cfg.root_gid,
                        size: u64::from(n_blks) * u64::from(layout.block_size),
                        atime: cfg.timestamp,
                        ctime: cfg.timestamp,
                        mtime: cfg.timestamp,
                        links_count: 2 + child_subdirs,
                        blocks_512: total_blks * sectors_per_blk,
                        flags: if has_extents { EXT4_EXTENTS_FL } else { 0 },
                        i_block: iblock,
                    },
                },
            );
            dir_entries.push((ino, EXT2_FT_DIR, name_bytes));
        } else if ftype.is_symlink() {
            let ino = allocate_inode_nr(layout, next_ino, group_used_inodes, false, uninit_bg)?;
            let target = fs::read_link(entry.path())?;
            let target_str = target.to_string_lossy();
            let t_bytes = target_str.as_bytes();
            let mut ib = [0u8; 60];
            let (blocks_512, flags) = if t_bytes.len() < 60 {
                ib[..t_bytes.len()].copy_from_slice(t_bytes);
                (0, 0)
            } else {
                let start_b = allocate_contiguous_blocks(layout, 1)?;
                let mut blk = vec![0u8; bsz];
                let copy_len = t_bytes.len().min(bsz);
                blk[..copy_len].copy_from_slice(&t_bytes[..copy_len]);
                data_blocks.insert(start_b, blk);
                if has_extents {
                    ib = build_extent_iblock(&[(0, 1, start_b)]);
                    (sectors_per_blk, EXT4_EXTENTS_FL)
                } else {
                    ib[0..4].copy_from_slice(&start_b.to_le_bytes());
                    (sectors_per_blk, 0)
                }
            };
            inodes.insert(
                ino,
                AllocatedInode {
                    ino,
                    params: InodeParams {
                        mode: S_IFLNK | 0o777,
                        uid: cfg.root_uid,
                        gid: cfg.root_gid,
                        size: t_bytes.len() as u64,
                        atime: cfg.timestamp,
                        ctime: cfg.timestamp,
                        mtime: cfg.timestamp,
                        links_count: 1,
                        blocks_512,
                        flags,
                        i_block: ib,
                    },
                },
            );
            dir_entries.push((ino, EXT2_FT_SYMLINK, name_bytes));
        } else if ftype.is_file() {
            let ino = allocate_inode_nr(layout, next_ino, group_used_inodes, false, uninit_bg)?;
            let content = fs::read(entry.path())?;
            let n_blks = (content.len() as u32).div_ceil(layout.block_size);
            let (iblock, total_blks) = if n_blks == 0 {
                let ib = if has_extents {
                    build_extent_iblock(&[])
                } else {
                    [0u8; 60]
                };
                (ib, 0)
            } else if has_extents {
                let start_b = allocate_contiguous_blocks(layout, n_blks)?;
                for idx in 0..n_blks {
                    let s = (idx as usize) * bsz;
                    let e = ((idx as usize + 1) * bsz).min(content.len());
                    let mut blk = vec![0u8; bsz];
                    blk[..e - s].copy_from_slice(&content[s..e]);
                    data_blocks.insert(start_b + idx, blk);
                }
                (build_extent_iblock(&[(0, n_blks as u16, start_b)]), n_blks)
            } else {
                let ind_cnt = crate::layout::count_indirect_blocks(n_blks, layout.block_size);
                let start_b = allocate_contiguous_blocks(layout, n_blks + ind_cnt)?;
                let (ib, tot, ind_blks) =
                    build_indirect_mapping(start_b, n_blks, layout.block_size);
                let ind_map: BTreeMap<u32, Vec<u8>> = ind_blks.into_iter().collect();
                let mut cur_p = start_b;
                for idx in 0..n_blks {
                    while ind_map.contains_key(&cur_p) {
                        cur_p += 1;
                    }
                    let s = (idx as usize) * bsz;
                    let e = ((idx as usize + 1) * bsz).min(content.len());
                    let mut blk = vec![0u8; bsz];
                    blk[..e - s].copy_from_slice(&content[s..e]);
                    data_blocks.insert(cur_p, blk);
                    cur_p += 1;
                }
                for (ind_nr, ind_data) in ind_map {
                    data_blocks.insert(ind_nr, ind_data);
                }
                (ib, tot)
            };

            inodes.insert(
                ino,
                AllocatedInode {
                    ino,
                    params: InodeParams {
                        mode: S_IFREG | 0o644,
                        uid: cfg.root_uid,
                        gid: cfg.root_gid,
                        size: content.len() as u64,
                        atime: cfg.timestamp,
                        ctime: cfg.timestamp,
                        mtime: cfg.timestamp,
                        links_count: 1,
                        blocks_512: total_blks * sectors_per_blk,
                        flags: if has_extents { EXT4_EXTENTS_FL } else { 0 },
                        i_block: iblock,
                    },
                },
            );
            dir_entries.push((ino, EXT2_FT_REG_FILE, name_bytes));
        }
    }

    Ok((dir_entries, subdir_count))
}

fn resolve_volume_size(cfg: &ExtMkfsConfig) -> Result<u64, ExtMkfsError> {
    if let Some(blks) = cfg.blocks_count_arg {
        return Ok(blks * u64::from(cfg.block_size));
    }
    let meta = fs::metadata(&cfg.device_path)?;
    let len = meta.len();
    if len <= cfg.offset_bytes {
        return Err(ExtMkfsError::VolumeTooSmall(0));
    }
    Ok(len - cfg.offset_bytes)
}

fn print_dry_run_summary(layout: &ExtFsLayout) {
    println!(
        "Filesystem label=\nOS type: Linux\nBlock size={} (log={})\n\
         Fragment size={} (log={})\n{} inodes, {} blocks\n\
         {} blocks reserved for the super user\nFirst data block={}\n\
         {} block groups\n{} blocks per group, {} fragments per group\n\
         {} inodes per group",
        layout.block_size,
        layout.log_block_size,
        layout.block_size,
        layout.log_block_size,
        layout.total_inodes,
        layout.total_blocks,
        layout.reserved_blocks,
        layout.first_data_block,
        layout.group_count,
        layout.blocks_per_group,
        layout.blocks_per_group,
        layout.inodes_per_group
    );
    let backups: Vec<u32> = layout
        .groups
        .iter()
        .filter(|g| g.group_nr > 0 && g.has_super)
        .map(|g| g.start_block)
        .collect();
    if !backups.is_empty() {
        println!("Superblock backups stored on blocks: ");
        let s: Vec<String> = backups.iter().map(u32::to_string).collect();
        println!("\t{}", s.join(", "));
    }
}

/// Runs the CLI entrypoint for `mke2fs`. Returns process exit code.
pub fn run_cli(args: &[String]) -> i32 {
    match parse_cli_args(args) {
        Ok(CliAction::ShowVersion) => {
            eprint!("{}", full_version_banner());
            0
        }
        Ok(CliAction::ShowUsage { exit_code }) => {
            eprint!("{}", version_banner());
            eprint!("{}", usage_string());
            exit_code
        }
        Ok(CliAction::Format(cfg)) => match format_extfs(cfg) {
            Ok(_) => 0,
            Err(e) => {
                eprintln!("mke2fs: {e}");
                1
            }
        },
        Err(e) => {
            eprint!("{}", version_banner());
            eprintln!("mke2fs: {e}");
            eprint!("{}", usage_string());
            1
        }
    }
}
