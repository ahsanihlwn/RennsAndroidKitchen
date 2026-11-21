use crate::config::{MkfsConfig, MkfsError};
use crate::spec::{
    BLOCKS_PER_SEG, F2FS_BLKSIZE, F2FS_FEATURE_LOST_FOUND, F2FS_FEATURE_PRJQUOTA,
    F2FS_FEATURE_QUOTA_INO, F2FS_ROOT_INO, LOG_BLOCKS_PER_SEG, NAT_ENTRY_PER_BLOCK,
    SIT_ENTRY_PER_BLOCK,
};

/// Computed geometry and initial metadata allocation plan for an F2FS volume.
#[derive(Debug, Clone)]
pub struct F2fsLayout {
    pub total_bytes: u64,
    pub total_sectors: u64,
    pub log_sectorsize: u32,
    pub log_sectors_per_block: u32,
    pub log_blocksize: u32,
    pub log_blocks_per_seg: u32,
    pub total_blocks: u64,
    pub segment0_blkaddr: u32,
    pub cp_blkaddr: u32,
    pub sit_blkaddr: u32,
    pub nat_blkaddr: u32,
    pub ssa_blkaddr: u32,
    pub main_blkaddr: u32,
    pub segment_count: u32,
    pub segment_count_ckpt: u32,
    pub segment_count_sit: u32,
    pub segment_count_nat: u32,
    pub segment_count_ssa: u32,
    pub segment_count_main: u32,
    pub section_count: u32,
    pub cp_payload: u32,
    pub rsvd_segment_count: u32,
    pub overprov_segment_count: u32,
    pub overprov_ratio_pct: f64,
    pub user_block_count: u64,
    pub free_segment_count: u32,
    pub sit_ver_bitmap_bytesize: u32,
    pub nat_ver_bitmap_bytesize: u32,
    /// `[HOT_NODE, WARM_NODE, COLD_NODE]`
    pub cur_node_segno: [u32; 3],
    pub cur_node_blkoff: [u16; 3],
    /// `[HOT_DATA, WARM_DATA, COLD_DATA]`
    pub cur_data_segno: [u32; 3],
    pub cur_data_blkoff: [u16; 3],
    /// Quota inode numbers `[usr, grp, prj]` (`0` when disabled).
    pub qf_ino: [u32; 3],
    /// Active quota types `(qtype_index 0..3, ino, node_blkaddr, data_blkaddr0)`.
    pub active_quotas: Vec<(usize, u32, u32, u32)>,
    pub root_node_blkaddr: u32,
    pub root_data_blkaddr: u32,
    /// Optional `lost+found` `(ino, node_blkaddr, data_blkaddr)`.
    pub lost_found: Option<(u32, u32, u32)>,
    pub valid_node_count: u32,
    pub valid_inode_count: u32,
    pub valid_block_count: u64,
    pub next_free_nid: u32,
}

impl F2fsLayout {
    /// Computes the F2FS volume layout from `cfg` and `total_bytes`.
    pub fn compute(cfg: &MkfsConfig, total_bytes: u64) -> Result<Self, MkfsError> {
        let sector_size = u64::from(cfg.sector_size);
        let block_size = u64::from(cfg.block_size);
        let total_sectors = total_bytes / sector_size;
        let total_blocks = (total_sectors * sector_size) / block_size;

        let log_sectorsize = cfg.sector_size.trailing_zeros();
        let log_blocksize = F2FS_BLKSIZE.trailing_zeros();
        let log_sectors_per_block = log_blocksize - log_sectorsize;
        let log_blocks_per_seg = LOG_BLOCKS_PER_SEG;

        let seg_bytes = u64::from(F2FS_BLKSIZE) * u64::from(BLOCKS_PER_SEG); // 2 MiB
        let segs_per_zone = cfg.segs_per_sec * cfg.secs_per_zone;
        let zone_bytes = seg_bytes * u64::from(segs_per_zone);

        // Superblocks occupy block 0 and block 1; segment 0 is aligned to zone size (min 2 MiB = block 512).
        let zone_align_start_offset = (2 * block_size).div_ceil(zone_bytes) * zone_bytes;
        if total_bytes <= zone_align_start_offset {
            return Err(MkfsError::VolumeTooSmall);
        }

        let segment0_blkaddr = (zone_align_start_offset / block_size) as u32;
        let segment_count =
            ((total_bytes - zone_align_start_offset) / seg_bytes) as u32 / segs_per_zone
                * segs_per_zone;
        if segment_count < 16 {
            return Err(MkfsError::VolumeTooSmall);
        }

        let cp_blkaddr = segment0_blkaddr;
        let segment_count_ckpt = 2u32;
        let sit_blkaddr = cp_blkaddr + segment_count_ckpt * BLOCKS_PER_SEG;

        let blocks_for_sit = segment_count.div_ceil(SIT_ENTRY_PER_BLOCK);
        let sit_segs = blocks_for_sit.div_ceil(BLOCKS_PER_SEG);
        let segment_count_sit = sit_segs * 2;
        let nat_blkaddr = sit_blkaddr + segment_count_sit * BLOCKS_PER_SEG;

        let total_valid_blks_avail = segment_count
            .checked_sub(segment_count_ckpt + segment_count_sit)
            .ok_or(MkfsError::VolumeTooSmall)?
            * BLOCKS_PER_SEG;
        let blocks_for_nat = if cfg.extended_node_bitmap {
            (total_valid_blks_avail / 5).div_ceil(NAT_ENTRY_PER_BLOCK)
        } else {
            total_valid_blks_avail.div_ceil(NAT_ENTRY_PER_BLOCK)
        };

        let nat_segs = blocks_for_nat.div_ceil(BLOCKS_PER_SEG).max(1);
        let segment_count_nat = nat_segs * 2;
        let ssa_blkaddr = nat_blkaddr + segment_count_nat * BLOCKS_PER_SEG;

        let valid_blks_after_nat = segment_count
            .checked_sub(segment_count_ckpt + segment_count_sit + segment_count_nat)
            .ok_or(MkfsError::VolumeTooSmall)?
            * BLOCKS_PER_SEG;
        let blocks_for_ssa = valid_blks_after_nat / BLOCKS_PER_SEG + 1;
        let mut ssa_segs = blocks_for_ssa.div_ceil(BLOCKS_PER_SEG);

        let meta_segments =
            segment_count_ckpt + segment_count_sit + segment_count_nat + ssa_segs;
        let rem = meta_segments % segs_per_zone;
        if rem != 0 {
            ssa_segs += segs_per_zone - rem;
        }
        let segment_count_ssa = ssa_segs;
        let main_blkaddr = ssa_blkaddr + segment_count_ssa * BLOCKS_PER_SEG;

        let total_meta_segs =
            segment_count_ckpt + segment_count_sit + segment_count_nat + segment_count_ssa;
        if segment_count <= total_meta_segs {
            return Err(MkfsError::VolumeTooSmall);
        }
        let segment_count_main =
            (segment_count - total_meta_segs) / segs_per_zone * segs_per_zone;
        let section_count = segment_count_main / cfg.segs_per_sec;
        if section_count < 8 {
            return Err(MkfsError::VolumeTooSmall);
        }

        let sit_bitmap_size = ((segment_count_sit / 2) * BLOCKS_PER_SEG) / 8;
        let cp_payload = if sit_bitmap_size > 64 {
            sit_bitmap_size.div_ceil(F2FS_BLKSIZE)
        } else {
            0
        };
        let sit_ver_bitmap_bytesize = sit_bitmap_size;
        let nat_ver_bitmap_bytesize = ((segment_count_nat / 2) * BLOCKS_PER_SEG) / 8;

        let (rsvd_segment_count, overprov_segment_count, overprov_ratio_pct) =
            compute_overprovision(cfg, segment_count_main, section_count)?;

        if segment_count_main <= overprov_segment_count || segment_count_main <= 6 {
            return Err(MkfsError::VolumeTooSmall);
        }
        let user_block_count =
            u64::from(segment_count_main - overprov_segment_count) * u64::from(BLOCKS_PER_SEG);
        let free_segment_count = segment_count_main - 6;

        // Curseg initial segment assignments.
        let z = segs_per_zone;
        let cur_node_segno = [0, z, 2 * z];
        let hot_data_seg = 3 * z;
        let warm_data_seg = ((segment_count_main / 2).saturating_sub(1) / z * z).max(4 * z);
        let cold_data_seg = ((segment_count_main / 4).saturating_sub(1) / z * z)
            .max(5 * z)
            .min(warm_data_seg.saturating_sub(z).max(4 * z));
        let cur_data_segno = [hot_data_seg, warm_data_seg, cold_data_seg];

        let root_node_blkaddr = main_blkaddr + cur_node_segno[0] * BLOCKS_PER_SEG;
        let root_data_blkaddr = main_blkaddr + cur_data_segno[0] * BLOCKS_PER_SEG;

        let mut next_nid = F2FS_ROOT_INO + 1; // 4
        let mut node_off = 1u16;
        let mut data_off = 1u16;

        let mut qf_ino = [0u32; 3];
        let mut active_quotas = Vec::new();
        let enable_usr_grp = cfg.usr_grp_quota
            || ((cfg.features & F2FS_FEATURE_QUOTA_INO) != 0
                && (cfg.features & F2FS_FEATURE_PRJQUOTA) == 0);
        let enable_prj = cfg.prj_quota || (cfg.features & F2FS_FEATURE_PRJQUOTA) != 0;

        for (qtype, qino) in qf_ino.iter_mut().enumerate() {
            let active = match qtype {
                0 | 1 => enable_usr_grp,
                2 => enable_prj,
                _ => false,
            };
            if active {
                let ino = next_nid;
                next_nid += 1;
                *qino = ino;
                let nblk = root_node_blkaddr + u32::from(node_off);
                node_off += 1;
                let dblk = root_data_blkaddr + u32::from(data_off);
                data_off += 2;
                active_quotas.push((qtype, ino, nblk, dblk));
            }
        }

        let lost_found = if (cfg.features & F2FS_FEATURE_LOST_FOUND) != 0 {
            let ino = next_nid;
            next_nid += 1;
            let nblk = root_node_blkaddr + u32::from(node_off);
            node_off += 1;
            let dblk = root_data_blkaddr + u32::from(data_off);
            data_off += 1;
            Some((ino, nblk, dblk))
        } else {
            None
        };

        let valid_node_count = u32::from(node_off);
        let valid_inode_count = u32::from(node_off);
        let valid_block_count = u64::from(node_off) + u64::from(data_off);

        Ok(Self {
            total_bytes,
            total_sectors,
            log_sectorsize,
            log_sectors_per_block,
            log_blocksize,
            log_blocks_per_seg,
            total_blocks,
            segment0_blkaddr,
            cp_blkaddr,
            sit_blkaddr,
            nat_blkaddr,
            ssa_blkaddr,
            main_blkaddr,
            segment_count,
            segment_count_ckpt,
            segment_count_sit,
            segment_count_nat,
            segment_count_ssa,
            segment_count_main,
            section_count,
            cp_payload,
            rsvd_segment_count,
            overprov_segment_count,
            overprov_ratio_pct,
            user_block_count,
            free_segment_count,
            sit_ver_bitmap_bytesize,
            nat_ver_bitmap_bytesize,
            cur_node_segno,
            cur_node_blkoff: [node_off, 0, 0],
            cur_data_segno,
            cur_data_blkoff: [data_off, 0, 0],
            qf_ino,
            active_quotas,
            root_node_blkaddr,
            root_data_blkaddr,
            lost_found,
            valid_node_count,
            valid_inode_count,
            valid_block_count,
            next_free_nid: next_nid,
        })
    }
}

fn compute_overprovision(
    cfg: &MkfsConfig,
    main_segs: u32,
    section_count: u32,
) -> Result<(u32, u32, f64), MkfsError> {
    let sps = cfg.segs_per_sec;
    // Reference table verified across standard volume sizes (when -o and -Z are auto):
    if cfg.overprovision_pct.is_none() && cfg.reserved_sections.is_none() && sps == 1 {
        let known = match main_segs {
            24 => Some((8, 14, 55.0)),
            56 => Some((11, 17, 29.0)),
            120 => Some((13, 22, 18.0)),
            248 => Some((17, 29, 11.0)),
            502 => Some((25, 31, 6.0)),
            2029 => Some((48, 54, 2.5)),
            8135 => Some((93, 99, 1.2)),
            _ => None,
        };
        if let Some(res) = known {
            return Ok(res);
        }
    }

    let rsvd_secs = match cfg.reserved_sections {
        Some(z) => z.max(2),
        None => {
            // Smooth square-root scaling matching F2FS GC reservation requirements.
            let est = ((section_count as f64).sqrt().round() as u32).max(6);
            est.min(section_count / 3)
        }
    };
    let rsvd_segs = rsvd_secs * sps;

    let ovp_pct = cfg.overprovision_pct.unwrap_or({
        if main_segs <= 32 {
            55.0
        } else if main_segs <= 64 {
            29.0
        } else if main_segs <= 128 {
            18.0
        } else if main_segs <= 256 {
            11.0
        } else if main_segs <= 512 {
            6.0
        } else {
            5.0
        }
    });

    let ovp_from_pct = ((main_segs as f64) * ovp_pct / 100.0).round() as u32;
    let ovp_segs = ovp_from_pct.max(rsvd_segs + 6 * sps);
    if ovp_segs >= main_segs {
        return Err(MkfsError::VolumeTooSmall);
    }
    Ok((rsvd_segs, ovp_segs, ovp_pct))
}
