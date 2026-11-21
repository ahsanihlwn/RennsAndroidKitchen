use std::io::{Cursor, Read, Seek, SeekFrom};

use android_sparse::{is_sparse_image, unsparse};

use crate::blocks::compute_extra_isize;
use crate::config::MkfsError;
use crate::crc32::{f2fs_crc32_raw, f2fs_dentry_hash, f2fs_inode_checksum};
use crate::spec::{
    BLOCKS_PER_SEG, CP_COMPACT_SUM_FLAG, CP_NAT_BITS_FLAG, CP_UMOUNT_FLAG, F2FS_BLKSIZE,
    F2FS_FEATURE_INODE_CHKSUM, F2FS_FEATURE_LOST_FOUND, F2FS_FEATURE_SB_CHKSUM,
    F2FS_MAJOR_VERSION, F2FS_MINOR_VERSION, F2FS_ROOT_INO, F2FS_SUPER_MAGIC, F2FS_SUPER_OFFSET,
    F2FS_SUPER_SIZE, V2_INITQMAGICS, V2_INITQVERSION,
};

/// Decoded on-disk metadata summary extracted from an F2FS image by [`verify_f2fs_image`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifiedF2fsInfo {
    pub block_count: u64,
    pub section_count: u32,
    pub segment_count: u32,
    pub segment_count_ckpt: u32,
    pub segment_count_sit: u32,
    pub segment_count_nat: u32,
    pub segment_count_ssa: u32,
    pub segment_count_main: u32,
    pub segment0_blkaddr: u32,
    pub cp_blkaddr: u32,
    pub sit_blkaddr: u32,
    pub nat_blkaddr: u32,
    pub ssa_blkaddr: u32,
    pub main_blkaddr: u32,
    pub feature: u32,
    pub qf_ino: [u32; 3],
    pub encoding: u16,
    pub encoding_flags: u16,
    pub ckpt_flags: u32,
    pub user_block_count: u64,
    pub valid_block_count: u64,
    pub rsvd_segment_count: u32,
    pub overprov_segment_count: u32,
    pub free_segment_count: u32,
    pub valid_node_count: u32,
    pub valid_inode_count: u32,
    pub next_free_nid: u32,
    pub cur_node_segno: [u32; 3],
    pub cur_node_blkoff: [u16; 3],
    pub cur_data_segno: [u32; 3],
    pub cur_data_blkoff: [u16; 3],
}

fn read_u16(b: &[u8], off: usize) -> u16 {
    u16::from_le_bytes([b[off], b[off + 1]])
}

fn read_u32(b: &[u8], off: usize) -> u32 {
    u32::from_le_bytes([b[off], b[off + 1], b[off + 2], b[off + 3]])
}

fn read_u64(b: &[u8], off: usize) -> u64 {
    u64::from_le_bytes(b[off..off + 8].try_into().unwrap())
}

fn read_block<R: Read + Seek>(r: &mut R, blk_idx: u32) -> Result<[u8; 4096], MkfsError> {
    let mut blk = [0u8; 4096];
    r.seek(SeekFrom::Start(u64::from(blk_idx) * u64::from(F2FS_BLKSIZE)))?;
    r.read_exact(&mut blk)?;
    Ok(blk)
}

/// Validates the on-disk structural integrity and checksums of a raw or Android-sparse F2FS image.
pub fn verify_f2fs_image<R: Read + Seek>(mut reader: R) -> Result<VerifiedF2fsInfo, MkfsError> {
    if is_sparse_image(&mut reader)? {
        let mut raw = Cursor::new(Vec::new());
        unsparse(reader, &mut raw, true)?;
        raw.seek(SeekFrom::Start(0))?;
        return verify_raw_f2fs(&mut raw);
    }
    verify_raw_f2fs(&mut reader)
}

fn verify_raw_f2fs<R: Read + Seek>(r: &mut R) -> Result<VerifiedF2fsInfo, MkfsError> {
    let blk0 = read_block(r, 0)?;
    let blk1 = read_block(r, 1)?;
    let sb = &blk0[F2FS_SUPER_OFFSET..F2FS_SUPER_OFFSET + F2FS_SUPER_SIZE];
    let sb_backup = &blk1[F2FS_SUPER_OFFSET..F2FS_SUPER_OFFSET + F2FS_SUPER_SIZE];
    if sb != sb_backup {
        return Err(MkfsError::VerificationFailed(
            "primary and backup superblocks differ".into(),
        ));
    }

    let magic = read_u32(sb, 0);
    if magic != F2FS_SUPER_MAGIC {
        return Err(MkfsError::VerificationFailed(format!(
            "bad superblock magic {magic:#x}"
        )));
    }
    let maj = read_u16(sb, 4);
    let minv = read_u16(sb, 6);
    if maj != F2FS_MAJOR_VERSION || minv != F2FS_MINOR_VERSION {
        return Err(MkfsError::VerificationFailed(format!(
            "unexpected F2FS version {maj}.{minv}"
        )));
    }

    let csum_off = read_u32(sb, 32);
    let block_count = read_u64(sb, 36);
    let section_count = read_u32(sb, 44);
    let segment_count = read_u32(sb, 48);
    let segment_count_ckpt = read_u32(sb, 52);
    let segment_count_sit = read_u32(sb, 56);
    let segment_count_nat = read_u32(sb, 60);
    let segment_count_ssa = read_u32(sb, 64);
    let segment_count_main = read_u32(sb, 68);
    let segment0_blkaddr = read_u32(sb, 72);
    let cp_blkaddr = read_u32(sb, 76);
    let sit_blkaddr = read_u32(sb, 80);
    let nat_blkaddr = read_u32(sb, 84);
    let ssa_blkaddr = read_u32(sb, 88);
    let main_blkaddr = read_u32(sb, 92);
    let root_ino = read_u32(sb, 96);
    if root_ino != F2FS_ROOT_INO {
        return Err(MkfsError::VerificationFailed(format!(
            "unexpected root_ino {root_ino}"
        )));
    }
    let uuid: [u8; 16] = sb[108..124].try_into().unwrap();
    let cp_payload = read_u32(sb, 1664);
    let feature = read_u32(sb, 2180);
    let qf_ino = [read_u32(sb, 2745), read_u32(sb, 2749), read_u32(sb, 2753)];
    let encoding = read_u16(sb, 2758);
    let encoding_flags = read_u16(sb, 2760);

    if (feature & F2FS_FEATURE_SB_CHKSUM) != 0 {
        if csum_off != 3068 {
            return Err(MkfsError::VerificationFailed(format!(
                "unexpected SB checksum_offset {csum_off}"
            )));
        }
        let stored = read_u32(sb, 3068);
        let calc = f2fs_crc32_raw(F2FS_SUPER_MAGIC, &sb[..3068]);
        if stored != calc {
            return Err(MkfsError::VerificationFailed(format!(
                "SB CRC mismatch: stored={stored:#x}, calc={calc:#x}"
            )));
        }
    }

    // Verify Checkpoint Pack 0 & 1.
    let cp0 = read_block(r, cp_blkaddr)?;
    let cp_tot = read_u32(&cp0, 136);
    if cp_tot != 6 + cp_payload {
        return Err(MkfsError::VerificationFailed(format!(
            "unexpected cp_pack_total_block_count {cp_tot}"
        )));
    }
    let cp0_footer = read_block(r, cp_blkaddr + cp_tot - 1)?;
    if cp0 != cp0_footer {
        return Err(MkfsError::VerificationFailed(
            "CP pack 0 header and footer differ".into(),
        ));
    }
    let cp0_csum_off = read_u32(&cp0, 164) as usize;
    let cp0_crc_stored = read_u32(&cp0, cp0_csum_off);
    let cp0_crc_calc = f2fs_crc32_raw(F2FS_SUPER_MAGIC, &cp0[..cp0_csum_off]);
    if cp0_crc_stored != cp0_crc_calc {
        return Err(MkfsError::VerificationFailed(format!(
            "CP0 CRC mismatch: stored={cp0_crc_stored:#x}, calc={cp0_crc_calc:#x}"
        )));
    }

    let cp1 = read_block(r, cp_blkaddr + BLOCKS_PER_SEG)?;
    let cp1_footer = read_block(r, cp_blkaddr + BLOCKS_PER_SEG + cp_tot - 1)?;
    if cp1 != cp1_footer {
        return Err(MkfsError::VerificationFailed(
            "CP pack 1 header and footer differ".into(),
        ));
    }
    let cp1_crc_stored = read_u32(&cp1, cp0_csum_off);
    let cp1_crc_calc = f2fs_crc32_raw(F2FS_SUPER_MAGIC, &cp1[..cp0_csum_off]);
    if cp1_crc_stored != cp1_crc_calc {
        return Err(MkfsError::VerificationFailed(
            "CP1 CRC mismatch".into(),
        ));
    }

    let user_block_count = read_u64(&cp0, 8);
    let valid_block_count = read_u64(&cp0, 16);
    let rsvd_segment_count = read_u32(&cp0, 24);
    let overprov_segment_count = read_u32(&cp0, 28);
    let free_segment_count = read_u32(&cp0, 32);
    let cur_node_segno = [read_u32(&cp0, 36), read_u32(&cp0, 40), read_u32(&cp0, 44)];
    let cur_node_blkoff = [read_u16(&cp0, 68), read_u16(&cp0, 70), read_u16(&cp0, 72)];
    let cur_data_segno = [read_u32(&cp0, 84), read_u32(&cp0, 88), read_u32(&cp0, 92)];
    let cur_data_blkoff = [
        read_u16(&cp0, 116),
        read_u16(&cp0, 118),
        read_u16(&cp0, 120),
    ];
    let ckpt_flags = read_u32(&cp0, 132);
    let valid_node_count = read_u32(&cp0, 144);
    let valid_inode_count = read_u32(&cp0, 148);
    let next_free_nid = read_u32(&cp0, 152);

    if (ckpt_flags & (CP_UMOUNT_FLAG | CP_COMPACT_SUM_FLAG))
        != (CP_UMOUNT_FLAG | CP_COMPACT_SUM_FLAG)
    {
        return Err(MkfsError::VerificationFailed(format!(
            "missing UMOUNT or COMPACT_SUM in ckpt_flags {ckpt_flags:#x}"
        )));
    }

    if (ckpt_flags & CP_NAT_BITS_FLAG) != 0 {
        let nb = read_block(r, cp_blkaddr + BLOCKS_PER_SEG - 1)?;
        if read_u32(&nb, 4) != cp0_crc_stored {
            return Err(MkfsError::VerificationFailed(
                "nat_bits cp_crc mismatch".into(),
            ));
        }
    }

    // Verify compact summary NAT journal and every allocated inode.
    let sum_blk = read_block(r, cp_blkaddr + 1 + cp_payload)?;
    let n_nats = usize::from(read_u16(&sum_blk, 0));
    if n_nats != valid_inode_count as usize {
        return Err(MkfsError::VerificationFailed(format!(
            "NAT journal count {n_nats} != valid_inode_count {valid_inode_count}"
        )));
    }

    let extra_isize = usize::from(compute_extra_isize(feature));
    for idx in 0..n_nats {
        let off = 2 + idx * 13;
        let nid = read_u32(&sum_blk, off);
        let ino = read_u32(&sum_blk, off + 5);
        let blkaddr = read_u32(&sum_blk, off + 9);
        if nid != ino {
            return Err(MkfsError::VerificationFailed(
                "inode NAT entry nid != ino".into(),
            ));
        }
        let node_blk = read_block(r, blkaddr)?;
        let footer_nid = read_u32(&node_blk, 4072);
        let footer_ino = read_u32(&node_blk, 4076);
        if footer_nid != nid || footer_ino != ino {
            return Err(MkfsError::VerificationFailed(format!(
                "inode {ino} footer mismatch ({footer_nid}, {footer_ino})"
            )));
        }
        if (feature & F2FS_FEATURE_INODE_CHKSUM) != 0 {
            let stored_icrc = read_u32(&node_blk, 368);
            let gen = read_u32(&node_blk, 68);
            let calc_icrc = f2fs_inode_checksum(&uuid, ino, gen, &node_blk);
            if stored_icrc != calc_icrc {
                return Err(MkfsError::VerificationFailed(format!(
                    "inode {ino} checksum mismatch: stored={stored_icrc:#x}, calc={calc_icrc:#x}"
                )));
            }
        }

        // If root inode, verify root dentry block.
        if ino == F2FS_ROOT_INO {
            let dblk_addr = read_u32(&node_blk, 360 + extra_isize);
            let dblk = read_block(r, dblk_addr)?;
            // Entry 0 = ".", Entry 1 = ".."
            if &dblk[2384..2385] != b"." || &dblk[2392..2394] != b".." {
                return Err(MkfsError::VerificationFailed(
                    "root dentry missing '.' or '..'".into(),
                ));
            }
            if (feature & F2FS_FEATURE_LOST_FOUND) != 0 {
                let lf_hash = read_u32(&dblk, 30 + 2 * 11);
                if lf_hash != f2fs_dentry_hash(b"lost+found")
                    || &dblk[2400..2410] != b"lost+found"
                {
                    return Err(MkfsError::VerificationFailed(
                        "root dentry bad lost+found entry".into(),
                    ));
                }
            }
        }

        // If quota inode, verify VFS quota v2r1 header magic & version.
        for (qtype, &q_ino) in qf_ino.iter().enumerate() {
            if q_ino != 0 && ino == q_ino {
                let qblk0_addr = read_u32(&node_blk, 360 + extra_isize);
                let qblk0 = read_block(r, qblk0_addr)?;
                let qmagic = read_u32(&qblk0, 0);
                let qver = read_u32(&qblk0, 4);
                if qmagic != V2_INITQMAGICS[qtype] || qver != V2_INITQVERSION {
                    return Err(MkfsError::VerificationFailed(format!(
                        "quota type {qtype} bad magic/version ({qmagic:#x}, {qver})"
                    )));
                }
            }
        }
    }

    Ok(VerifiedF2fsInfo {
        block_count,
        section_count,
        segment_count,
        segment_count_ckpt,
        segment_count_sit,
        segment_count_nat,
        segment_count_ssa,
        segment_count_main,
        segment0_blkaddr,
        cp_blkaddr,
        sit_blkaddr,
        nat_blkaddr,
        ssa_blkaddr,
        main_blkaddr,
        feature,
        qf_ino,
        encoding,
        encoding_flags,
        ckpt_flags,
        user_block_count,
        valid_block_count,
        rsvd_segment_count,
        overprov_segment_count,
        free_segment_count,
        valid_node_count,
        valid_inode_count,
        next_free_nid,
        cur_node_segno,
        cur_node_blkoff,
        cur_data_segno,
        cur_data_blkoff,
    })
}
