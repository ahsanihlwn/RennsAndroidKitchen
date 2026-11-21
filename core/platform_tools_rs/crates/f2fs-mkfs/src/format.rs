use std::collections::BTreeMap;
use std::fs::{File, OpenOptions};
use std::io::{BufWriter, Seek, SeekFrom, Write};
use std::num::NonZeroU32;

use android_sparse::SparseImage;

use crate::blocks::{
    build_checkpoint_block, build_compact_summary_block, build_dir_dentry_block,
    build_lost_found_inode_block, build_nat_bits_block, build_nat_block0,
    build_node_summary_blocks, build_quota_data_blocks, build_quota_inode_block,
    build_root_inode_block, build_superblock_page,
};
use crate::config::{CliAction, MkfsConfig, MkfsError, parse_cli_args, usage_string};
use crate::layout::F2fsLayout;
use crate::spec::{
    BLOCKS_PER_SEG, DETERMINISTIC_CP_VER, F2FS_BLKSIZE, F2FS_FEATURE_CASEFOLD, F2FS_FT_DIR,
    F2FS_ROOT_INO,
};

/// Summary returned after formatting an F2FS volume.
#[derive(Debug, Clone)]
pub struct FormatResult {
    pub layout: F2fsLayout,
    pub cp_ver: u64,
}

fn determine_volume_bytes(cfg: &MkfsConfig) -> Result<u64, MkfsError> {
    if let Some(sp_bytes) = cfg.sparse_size {
        return Ok(sp_bytes);
    }
    if let Some(secs) = cfg.total_sectors {
        return secs
            .checked_mul(u64::from(cfg.sector_size))
            .ok_or(MkfsError::VolumeTooSmall);
    }
    let meta = std::fs::metadata(&cfg.device_path)?;
    Ok(meta.len())
}

fn generate_cp_ver(cfg: &MkfsConfig) -> u64 {
    if cfg.fixed_seed {
        return DETERMINISTIC_CP_VER;
    }
    let mut rnd = [0u8; 4];
    if getrandom::fill(&mut rnd).is_ok() {
        u64::from((u32::from_le_bytes(rnd) & 0x7fff_ffff).max(1))
    } else {
        DETERMINISTIC_CP_VER
    }
}

/// Collects all non-zero 4 KiB blocks (`block_index -> [u8; 4096]`) for the formatted F2FS volume.
pub fn build_f2fs_blocks(
    cfg: &MkfsConfig,
    layout: &F2fsLayout,
    cp_ver: u64,
) -> BTreeMap<u32, [u8; 4096]> {
    let mut blocks: BTreeMap<u32, [u8; 4096]> = BTreeMap::new();

    // 1. Primary and backup superblock (blocks 0 and 1).
    let sb_page = build_superblock_page(cfg, layout);
    blocks.insert(0, sb_page);
    blocks.insert(1, sb_page);

    // 2. Checkpoint pack 0 (cp_blkaddr..cp_blkaddr+6+cp_payload) and optional nat_bits at cp_blkaddr+511.
    let (cp0, cp0_crc) = build_checkpoint_block(cfg, layout, cp_ver, true);
    let cp0_start = layout.cp_blkaddr;
    blocks.insert(cp0_start, cp0);

    let sum_start = cp0_start + 1 + layout.cp_payload;
    blocks.insert(sum_start, build_compact_summary_block(layout));
    let node_sums = build_node_summary_blocks(layout);
    blocks.insert(sum_start + 1, node_sums[0]);
    blocks.insert(sum_start + 2, node_sums[1]);
    blocks.insert(sum_start + 3, node_sums[2]);
    blocks.insert(sum_start + 4, cp0); // CP pack 0 footer

    if cfg.nat_bits {
        blocks.insert(
            cp0_start + BLOCKS_PER_SEG - 1,
            build_nat_bits_block(layout, cp_ver, cp0_crc),
        );
    }

    // 3. Checkpoint pack 1 (cp_blkaddr + 512).
    let (cp1, _) = build_checkpoint_block(cfg, layout, cp_ver, false);
    let cp1_start = layout.cp_blkaddr + BLOCKS_PER_SEG;
    blocks.insert(cp1_start, cp1);
    blocks.insert(cp1_start + 5 + layout.cp_payload, cp1);

    // 4. NAT block 0.
    blocks.insert(layout.nat_blkaddr, build_nat_block0());

    // 5. Root inode + root directory dentry block.
    blocks.insert(layout.root_node_blkaddr, build_root_inode_block(cfg, layout));
    let root_dentry = if let Some((lf_ino, _, _)) = layout.lost_found {
        build_dir_dentry_block(
            F2FS_ROOT_INO,
            F2FS_ROOT_INO,
            &[(b"lost+found", lf_ino, F2FS_FT_DIR)],
        )
    } else {
        build_dir_dentry_block(F2FS_ROOT_INO, F2FS_ROOT_INO, &[])
    };
    blocks.insert(layout.root_data_blkaddr, root_dentry);

    // 6. Quota inodes and VFS quota v2r1 data blocks.
    let accounted_inodes: u64 = if layout.lost_found.is_some() { 2 } else { 1 };
    let accounted_space: u64 = accounted_inodes * 4096;
    for &(qtype, q_ino, q_nblk, q_dblk0) in &layout.active_quotas {
        blocks.insert(
            q_nblk,
            build_quota_inode_block(cfg, q_ino, q_nblk, q_dblk0),
        );
        let owner_id = match qtype {
            0 => cfg.root_uid,
            1 => cfg.root_gid,
            _ => 0,
        };
        let (qb0, qb1) =
            build_quota_data_blocks(qtype, owner_id, accounted_inodes, accounted_space);
        blocks.insert(q_dblk0, qb0);
        blocks.insert(q_dblk0 + 1, qb1);
    }

    // 7. Optional lost+found inode and dentry block.
    if let Some((lf_ino, lf_nblk, lf_dblk)) = layout.lost_found {
        blocks.insert(
            lf_nblk,
            build_lost_found_inode_block(cfg, lf_ino, lf_nblk, lf_dblk),
        );
        blocks.insert(
            lf_dblk,
            build_dir_dentry_block(lf_ino, F2FS_ROOT_INO, &[]),
        );
    }

    blocks
}

/// Formats `cfg.device_path` as an F2FS volume (either raw or Android sparse image).
pub fn format_f2fs(cfg: &MkfsConfig) -> Result<FormatResult, MkfsError> {
    let total_bytes = determine_volume_bytes(cfg)?;
    let layout = F2fsLayout::compute(cfg, total_bytes)?;
    let cp_ver = generate_cp_ver(cfg);

    if !cfg.quiet {
        println!("\n\tF2FS-tools: mkfs.f2fs Ver: 1.16.0 (2023-04-11)\n");
        println!("Info: Debug level = {}", cfg.debug_level);
        println!(
            "Info: Trim is {}",
            if cfg.trim { "enabled" } else { "disabled" }
        );
        if (cfg.features & F2FS_FEATURE_CASEFOLD) != 0 {
            println!("Info: Enable utf8 with casefolding");
        }
        if cfg.prj_quota {
            println!("Info: Enable Project quota");
        }
        println!("Info: Segments per section = {}", cfg.segs_per_sec);
        println!("Info: Sections per zone = {}", cfg.secs_per_zone);
        println!("Info: sector size = {}", cfg.sector_size);
        println!(
            "Info: total sectors = {} ({} MB)",
            layout.total_sectors,
            layout.total_bytes / (1024 * 1024)
        );
        println!("Info: block size = {}", cfg.block_size);
        println!(
            "Info: zone aligned segment0 blkaddr: {}",
            layout.segment0_blkaddr
        );
        for &(qtype, q_ino, _, _) in &layout.active_quotas {
            println!("Info: add quota type = {qtype} => {q_ino}");
        }
        println!("Info: format version with\n  \"{}\"", cfg.version_string);
        println!("Info: Overprovision ratio = {:.3}%", layout.overprov_ratio_pct);
        println!(
            "Info: Overprovision segments = {} (GC reserved = {})",
            layout.overprov_segment_count, layout.rsvd_segment_count
        );
    }

    let blocks = build_f2fs_blocks(cfg, &layout, cp_ver);

    if cfg.sparse_size.is_some() {
        let total_blks =
            u32::try_from(layout.total_blocks).map_err(|_| MkfsError::VolumeTooSmall)?;
        let bs = NonZeroU32::new(F2FS_BLKSIZE).unwrap();
        let mut simg = SparseImage::new(bs, total_blks)?;

        // Match libsparse zeroed metadata areas for SIT segment 0, NAT segment 0, and SSA.
        simg.add_fill_blocks(layout.sit_blkaddr, BLOCKS_PER_SEG, 0)?;
        simg.add_fill_blocks(layout.nat_blkaddr, BLOCKS_PER_SEG, 0)?;

        for (&blk_idx, data) in &blocks {
            simg.add_raw_blocks(blk_idx, data)?;
        }

        let file = File::create(&cfg.device_path)?;
        let mut writer = BufWriter::new(file);
        simg.write_to_with_options(&mut writer, false, false)?;
        writer.flush()?;
    } else {
        let mut file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(false)
            .open(&cfg.device_path)?;
        file.set_len(layout.total_bytes)?;

        // Zero-fill active metadata regions (SB, CP, SIT, NAT, SSA) before writing populated blocks.
        let zero_seg = vec![0u8; (BLOCKS_PER_SEG * F2FS_BLKSIZE) as usize];
        file.seek(SeekFrom::Start(0))?;
        file.write_all(&zero_seg[..2 * F2FS_BLKSIZE as usize])?;

        let meta_start_byte = u64::from(layout.cp_blkaddr) * u64::from(F2FS_BLKSIZE);
        let meta_segs = layout.segment_count_ckpt
            + layout.segment_count_sit
            + layout.segment_count_nat
            + layout.segment_count_ssa;
        file.seek(SeekFrom::Start(meta_start_byte))?;
        for _ in 0..meta_segs {
            file.write_all(&zero_seg)?;
        }

        for (&blk_idx, data) in &blocks {
            let off = u64::from(blk_idx) * u64::from(F2FS_BLKSIZE);
            file.seek(SeekFrom::Start(off))?;
            file.write_all(data)?;
        }
        file.flush()?;
    }

    if !cfg.quiet {
        println!("Info: format successful");
    }

    Ok(FormatResult { layout, cp_ver })
}

/// Shared CLI entry point for `make_f2fs` (`casefold_default = false`) and
/// `make_f2fs_casefold` (`casefold_default = true`).
pub fn run_cli(args: &[String], casefold_default: bool) -> i32 {
    match parse_cli_args(&args[1..], casefold_default) {
        Ok(CliAction::ShowVersion) => {
            println!("mkfs.f2fs 1.16.0 (2023-04-11)");
            0
        }
        Ok(CliAction::ShowHelp) => {
            eprintln!("{}", usage_string());
            0
        }
        Ok(CliAction::Format(cfg)) => match format_f2fs(&cfg) {
            Ok(_) => 0,
            Err(err) => {
                eprintln!("\t{err}");
                255
            }
        },
        Err(err) => {
            eprintln!("\t{err}\n");
            eprintln!("{}", usage_string());
            255
        }
    }
}
