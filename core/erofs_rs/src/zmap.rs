// SPDX-License-Identifier: MIT

//! EROFS compressed extent mapper (`COMPRESSED_FULL`, `COMPRESSED_COMPACT`, 48-bit extents,
//! big pcluster, ztailpacking, fragments, and blkaddr slot enumeration for compaction).

use crate::compress::DecodeAlg;
use crate::error::{ErofsError, Result};
use crate::format::{
    DataLayout, Inode, SuperBlock, Z_ADVISE_BIG_PCLUSTER_1, Z_ADVISE_BIG_PCLUSTER_2,
    Z_ADVISE_COMPACTED_2B, Z_ADVISE_EXTENTS, Z_ADVISE_EXTRECSZ_BIT, Z_ADVISE_EXTRECSZ_MASK,
    Z_ADVISE_FRAGMENT_PCLUSTER, Z_ADVISE_INLINE_PCLUSTER, Z_ADVISE_INTERLACED_PCLUSTER,
    Z_EXTENT_PLEN_FMT_BIT, Z_EXTENT_PLEN_MASK, Z_EXTENT_PLEN_PARTIAL, Z_FRAGMENT_INODE_BIT,
    Z_LCLUSTER_HEAD1, Z_LCLUSTER_HEAD2, Z_LCLUSTER_NONHEAD, Z_LCLUSTER_PLAIN, Z_LI_D0_CBLKCNT,
    Z_LI_PARTIAL_REF, align_up, div_round_up, read_u16_le, read_u32_le,
};

/// Parsed `z_erofs_map_header` for a compressed inode.
#[derive(Debug, Clone)]
pub struct ZHeader {
    pub header_offset: u64,
    pub index_base: u64,
    pub advise: u16,
    pub alg_head1: u8,
    pub alg_head2: u8,
    pub lclusterbits: u32,
    pub idata_size: u16,
    pub fragment_off: u32,
    pub is_fragment_inode: bool,
    pub is_extents_mode: bool,
    pub extent_count: u64,
}

/// Mapped physical extent for a logical range of a compressed file.
#[derive(Debug, Clone)]
pub struct ZMappedExtent {
    pub la: u64,
    pub llen: u64,
    pub pa: u64,
    pub plen: usize,
    pub alg: DecodeAlg,
    pub partial: bool,
    pub is_inline: bool,
    pub is_fragment: bool,
}

impl ZHeader {
    pub fn parse<F>(sb: &SuperBlock, inode: &Inode, read_meta: &mut F) -> Result<Self>
    where
        F: FnMut(u64, usize) -> Result<Vec<u8>>,
    {
        let header_offset = align_up(inode.data_offset, 8);
        let raw = read_meta(header_offset, 8)?;
        if raw.len() < 8 {
            return Err(ErofsError::Corrupted(format!(
                "truncated z_erofs_map_header for nid={}",
                inode.nid
            )));
        }

        let field0 = read_u32_le(&raw, 0);
        let advise = read_u16_le(&raw, 4);
        let alg_byte = raw[6];
        let cluster_byte = raw[7];

        // Whole-file fragment inode check (bit 7 of h_clusterbits when not extents mode)
        let is_extents_mode =
            inode.layout == DataLayout::CompressedFull && (advise & Z_ADVISE_EXTENTS) != 0;

        if is_extents_mode {
            let hi = read_u16_le(&raw, 6) as u64;
            let extent_count = (hi << 32) | (field0 as u64);
            return Ok(Self {
                header_offset,
                index_base: header_offset + 8,
                advise,
                alg_head1: 0,
                alg_head2: 0,
                lclusterbits: sb.blkszbits as u32,
                idata_size: 0,
                fragment_off: 0,
                is_fragment_inode: false,
                is_extents_mode: true,
                extent_count,
            });
        }

        let is_fragment_inode = (cluster_byte & (1 << Z_FRAGMENT_INODE_BIT)) != 0;
        let lcluster_delta = (cluster_byte & 0x0F) as u32;
        let lclusterbits = (sb.blkszbits as u32) + lcluster_delta;
        if !is_fragment_inode && lclusterbits > 16 {
            return Err(ErofsError::Corrupted(format!(
                "invalid lclusterbits {lclusterbits} for nid={}",
                inode.nid
            )));
        }

        let alg_head1 = alg_byte & 0x0F;
        let alg_head2 = (alg_byte >> 4) & 0x0F;
        let idata_size = if (advise & Z_ADVISE_INLINE_PCLUSTER) != 0 {
            read_u16_le(&raw, 2)
        } else {
            0
        };
        let fragment_off = if is_fragment_inode || (advise & Z_ADVISE_FRAGMENT_PCLUSTER) != 0 {
            field0
        } else {
            0
        };

        let index_base = match inode.layout {
            DataLayout::CompressedCompact => header_offset + 8,
            DataLayout::CompressedFull => header_offset + 16,
            _ => {
                return Err(ErofsError::InvalidFormat(
                    "ZHeader::parse called on non-compressed inode".into(),
                ));
            }
        };

        Ok(Self {
            header_offset,
            index_base,
            advise,
            alg_head1,
            alg_head2,
            lclusterbits,
            idata_size,
            fragment_off,
            is_fragment_inode,
            is_extents_mode: false,
            extent_count: 0,
        })
    }

    pub fn lcluster_size(&self) -> u64 {
        1u64 << self.lclusterbits
    }

    fn alg_for_head(&self, head_type: u8, clusterofs: u16) -> Result<DecodeAlg> {
        match head_type {
            Z_LCLUSTER_PLAIN => {
                if (self.advise & Z_ADVISE_INTERLACED_PCLUSTER) != 0 {
                    Ok(DecodeAlg::Interlaced {
                        offset: clusterofs as usize,
                    })
                } else {
                    Ok(DecodeAlg::Shifted)
                }
            }
            Z_LCLUSTER_HEAD1 => DecodeAlg::from_id(self.alg_head1),
            Z_LCLUSTER_HEAD2 => DecodeAlg::from_id(self.alg_head2),
            other => Err(ErofsError::Corrupted(format!(
                "unexpected head lcluster type {other}"
            ))),
        }
    }

    fn has_big_pcluster_for(&self, head_type: u8) -> bool {
        match head_type {
            Z_LCLUSTER_HEAD1 => (self.advise & Z_ADVISE_BIG_PCLUSTER_1) != 0,
            Z_LCLUSTER_HEAD2 => (self.advise & Z_ADVISE_BIG_PCLUSTER_2) != 0,
            _ => false,
        }
    }
}

#[derive(Debug, Clone, Default)]
struct LClusterRec {
    lcn: u64,
    ltype: u8,
    clusterofs: u16,
    delta0: u16,
    delta1: u16,
    pblk: u64,
    compressed_blks: u64,
    partial_ref: bool,
    next_pack_off: u64,
}

fn decode_compacted_bits(lobits: u32, pack: &[u8], bit_pos: u32) -> (u16, u8) {
    let byte_pos = (bit_pos / 8) as usize;
    let shift = bit_pos & 7;
    let mut tmp = [0u8; 4];
    let avail = (pack.len().saturating_sub(byte_pos)).min(4);
    tmp[..avail].copy_from_slice(&pack[byte_pos..byte_pos + avail]);
    let v = u32::from_le_bytes(tmp) >> shift;
    let lo = (v & ((1u32 << lobits) - 1)) as u16;
    let ltype = ((v >> lobits) & 3) as u8;
    (lo, ltype)
}

fn compacted_lookahead_distance(
    lobits: u32,
    encodebits: u32,
    vcnt: usize,
    pack: &[u8],
    mut idx: usize,
) -> u16 {
    let mut d1 = 0u16;
    let mut lo = 0u16;
    while idx < vcnt {
        let (v_lo, ltype) = decode_compacted_bits(lobits, pack, encodebits * (idx as u32));
        lo = v_lo;
        if ltype != Z_LCLUSTER_NONHEAD {
            return d1;
        }
        d1 += 1;
        idx += 1;
    }
    if (lo & Z_LI_D0_CBLKCNT) == 0 && lo > 0 {
        d1 = d1.saturating_add(lo - 1);
    }
    d1
}

/// Compute `(pack_base, unit_bytes, index_in_pack, vcnt, amortized_shift)` for a compact lcluster.
fn compact_lcn_geometry(
    hdr: &ZHeader,
    total_idx: u64,
    lcn: u64,
) -> Result<(u64, u64, usize, usize, u32)> {
    let ebase = hdr.index_base;
    let compacted_4b_initial = ((32 - (ebase % 32)) / 4) & 7;
    let compacted_2b = if (hdr.advise & Z_ADVISE_COMPACTED_2B) != 0 && compacted_4b_initial < total_idx
    {
        (total_idx - compacted_4b_initial) & !15u64
    } else {
        0
    };

    let mut pos = ebase;
    let mut shift = 2u32; // 4B
    let mut eff = lcn;
    if eff >= compacted_4b_initial {
        pos += compacted_4b_initial * 4;
        eff -= compacted_4b_initial;
        if eff < compacted_2b {
            shift = 1; // 2B
        } else {
            pos += compacted_2b * 2;
            eff -= compacted_2b;
        }
    }
    pos += eff << shift;

    let vcnt = match (1u32 << shift, hdr.lclusterbits) {
        (4, b) if b <= 14 => 2usize,
        (2, b) if b <= 12 => 16usize,
        _ => {
            return Err(ErofsError::Unsupported(format!(
                "unsupported compact index parameters shift={shift} lclusterbits={}",
                hdr.lclusterbits
            )));
        }
    };

    let unit = (vcnt as u64) << shift;
    let base = pos & !(unit - 1);
    let idx = ((pos - base) >> shift) as usize;
    Ok((base, unit, idx, vcnt, shift))
}

fn load_compact_lcluster<F>(
    hdr: &ZHeader,
    total_idx: u64,
    lcn: u64,
    lookahead: bool,
    read_meta: &mut F,
) -> Result<LClusterRec>
where
    F: FnMut(u64, usize) -> Result<Vec<u8>>,
{
    if lcn >= total_idx {
        return Err(ErofsError::Corrupted(format!(
            "lcn {lcn} out of range (total {total_idx})"
        )));
    }
    let (base, unit, idx, vcnt, _shift) = compact_lcn_geometry(hdr, total_idx, lcn)?;
    let pack = read_meta(base, unit as usize)?;
    if pack.len() < unit as usize {
        return Err(ErofsError::Corrupted("truncated compact zmap pack".into()));
    }

    let lobits = hdr.lclusterbits.max(12); // ilog2(Z_LI_D0_CBLKCNT) + 1 = 12
    let encodebits = (((unit as u32) - 4) * 8) / (vcnt as u32);
    let big_pcluster = (hdr.advise & Z_ADVISE_BIG_PCLUSTER_1) != 0;

    let (lo, ltype) = decode_compacted_bits(lobits, &pack, encodebits * (idx as u32));
    let mut rec = LClusterRec {
        lcn,
        ltype,
        next_pack_off: base + unit,
        ..Default::default()
    };

    if ltype == Z_LCLUSTER_NONHEAD {
        rec.clusterofs = (1u64 << hdr.lclusterbits) as u16;
        if lookahead {
            rec.delta1 = compacted_lookahead_distance(lobits, encodebits, vcnt, &pack, idx);
        }
        if (lo & Z_LI_D0_CBLKCNT) != 0 {
            if !big_pcluster {
                return Err(ErofsError::Corrupted(
                    "CBLKCNT flag present without BIG_PCLUSTER".into(),
                ));
            }
            rec.compressed_blks = (lo & !Z_LI_D0_CBLKCNT) as u64;
            rec.delta0 = 1;
            return Ok(rec);
        }
        if idx + 1 != vcnt {
            rec.delta0 = lo;
            return Ok(rec);
        }
        // Last slot in pack stores delta1; infer delta0 from predecessor.
        let (prev_lo, prev_type) =
            decode_compacted_bits(lobits, &pack, encodebits * ((idx - 1) as u32));
        let prev_d0 = if prev_type != Z_LCLUSTER_NONHEAD {
            0
        } else if (prev_lo & Z_LI_D0_CBLKCNT) != 0 {
            1
        } else {
            prev_lo
        };
        rec.delta0 = prev_d0 + 1;
        return Ok(rec);
    }

    rec.clusterofs = lo;
    rec.delta0 = 0;

    // Accumulate physical blocks preceding `idx` inside this pack.
    let mut nblk: u64 = if !big_pcluster { 1 } else { 0 };
    let mut i = idx as isize;
    if !big_pcluster {
        while i > 0 {
            i -= 1;
            let (v_lo, v_type) = decode_compacted_bits(lobits, &pack, encodebits * (i as u32));
            if v_type == Z_LCLUSTER_NONHEAD {
                i -= v_lo as isize;
            }
            if i >= 0 {
                nblk += 1;
            }
        }
    } else {
        while i > 0 {
            i -= 1;
            let (v_lo, v_type) = decode_compacted_bits(lobits, &pack, encodebits * (i as u32));
            if v_type == Z_LCLUSTER_NONHEAD {
                if (v_lo & Z_LI_D0_CBLKCNT) != 0 {
                    i -= 1;
                    nblk += (v_lo & !Z_LI_D0_CBLKCNT) as u64;
                    continue;
                }
                if v_lo <= 1 {
                    return Err(ErofsError::Corrupted(
                        "invalid delta0 <= 1 in BIG_PCLUSTER compact pack".into(),
                    ));
                }
                i -= (v_lo as isize) - 2;
                continue;
            }
            nblk += 1;
        }
    }

    let base_blk = read_u32_le(&pack, (unit as usize) - 4) as u64;
    rec.pblk = base_blk.wrapping_add(nblk) & 0xFFFF_FFFF;
    Ok(rec)
}

fn load_full_lcluster<F>(
    hdr: &ZHeader,
    total_idx: u64,
    lcn: u64,
    read_meta: &mut F,
) -> Result<LClusterRec>
where
    F: FnMut(u64, usize) -> Result<Vec<u8>>,
{
    if lcn >= total_idx {
        return Err(ErofsError::Corrupted(format!(
            "full lcn {lcn} out of range (total {total_idx})"
        )));
    }
    let pos = hdr.index_base + lcn * 8;
    let raw = read_meta(pos, 8)?;
    if raw.len() < 8 {
        return Err(ErofsError::Corrupted("truncated full zmap index".into()));
    }
    let di_advise = read_u16_le(&raw, 0);
    let di_clusterofs = read_u16_le(&raw, 2);
    let ltype = (di_advise & 3) as u8;

    let mut rec = LClusterRec {
        lcn,
        ltype,
        next_pack_off: pos + 8,
        ..Default::default()
    };

    if ltype == Z_LCLUSTER_NONHEAD {
        rec.clusterofs = (1u64 << hdr.lclusterbits) as u16;
        let d0 = read_u16_le(&raw, 4);
        let d1 = read_u16_le(&raw, 6);
        if (d0 & Z_LI_D0_CBLKCNT) != 0 {
            if (hdr.advise & (Z_ADVISE_BIG_PCLUSTER_1 | Z_ADVISE_BIG_PCLUSTER_2)) == 0 {
                return Err(ErofsError::Corrupted(
                    "full index CBLKCNT without BIG_PCLUSTER".into(),
                ));
            }
            rec.compressed_blks = (d0 & !Z_LI_D0_CBLKCNT) as u64;
            rec.delta0 = 1;
        } else {
            rec.delta0 = d0;
        }
        rec.delta1 = d1;
    } else {
        rec.partial_ref = (di_advise & Z_LI_PARTIAL_REF) != 0;
        rec.clusterofs = di_clusterofs;
        rec.pblk = read_u32_le(&raw, 4) as u64;
    }
    Ok(rec)
}

fn load_lcluster<F>(
    inode: &Inode,
    hdr: &ZHeader,
    total_idx: u64,
    lcn: u64,
    lookahead: bool,
    read_meta: &mut F,
) -> Result<LClusterRec>
where
    F: FnMut(u64, usize) -> Result<Vec<u8>>,
{
    match inode.layout {
        DataLayout::CompressedCompact => {
            load_compact_lcluster(hdr, total_idx, lcn, lookahead, read_meta)
        }
        DataLayout::CompressedFull => load_full_lcluster(hdr, total_idx, lcn, read_meta),
        _ => Err(ErofsError::InvalidFormat("not a compressed inode".into())),
    }
}

/// Enumerate all logical-to-physical compressed extents covering `[0..inode.size)`.
pub fn map_all_extents<F>(
    sb: &SuperBlock,
    inode: &Inode,
    read_meta: &mut F,
) -> Result<(ZHeader, Vec<ZMappedExtent>)>
where
    F: FnMut(u64, usize) -> Result<Vec<u8>>,
{
    if inode.size == 0 {
        let hdr = ZHeader::parse(sb, inode, read_meta)?;
        return Ok((hdr, Vec::new()));
    }

    let hdr = ZHeader::parse(sb, inode, read_meta)?;
    let bs = sb.block_size as u64;

    // Special case 1: whole file stored inside packed inode (fragment inode).
    if hdr.is_fragment_inode {
        return Ok((
            hdr.clone(),
            vec![ZMappedExtent {
                la: 0,
                llen: inode.size,
                pa: hdr.fragment_off as u64,
                plen: inode.size as usize,
                alg: DecodeAlg::Shifted,
                partial: false,
                is_inline: false,
                is_fragment: true,
            }],
        ));
    }

    // Special case 2: 48-bit encoded extent metadata (Linux 6.15+ Z_EROFS_ADVISE_EXTENTS).
    if hdr.is_extents_mode {
        let recsize = 4usize
            << (((hdr.advise >> Z_ADVISE_EXTRECSZ_BIT) & Z_ADVISE_EXTRECSZ_MASK) as usize);
        let base = align_up(hdr.index_base, recsize as u64);
        let mut extents = Vec::with_capacity(hdr.extent_count as usize);
        let mut cur_la = 0u64;
        for i in 0..hdr.extent_count {
            let off = base + i * (recsize as u64);
            let raw = read_meta(off, recsize)?;
            if raw.len() < recsize {
                return Err(ErofsError::Corrupted("truncated z_erofs_extent record".into()));
            }
            let plen_raw = read_u32_le(&raw, 0);
            let partial = (plen_raw & Z_EXTENT_PLEN_PARTIAL) != 0;
            let fmt = ((plen_raw >> Z_EXTENT_PLEN_FMT_BIT) & 0x0F) as u8;
            let plen = (plen_raw & Z_EXTENT_PLEN_MASK) as usize;

            let pstart_lo = if recsize >= 8 { read_u32_le(&raw, 4) as u64 } else { 0 };
            let pstart_hi = if recsize >= 12 { read_u32_le(&raw, 8) as u64 } else { 0 };
            let pa = (pstart_hi << 32) | pstart_lo;

            let next_la = if i + 1 < hdr.extent_count {
                let nraw = read_meta(base + (i + 1) * (recsize as u64), recsize)?;
                if recsize >= 20 {
                    let lo = read_u32_le(&nraw, 12) as u64;
                    let hi = read_u32_le(&nraw, 16) as u64;
                    (hi << 32) | lo
                } else if recsize >= 16 {
                    read_u32_le(&nraw, 12) as u64
                } else {
                    inode.size
                }
            } else {
                inode.size
            };
            let llen = next_la.saturating_sub(cur_la);
            let alg = if fmt == 0 {
                DecodeAlg::Shifted
            } else {
                DecodeAlg::from_id(fmt - 1)?
            };
            extents.push(ZMappedExtent {
                la: cur_la,
                llen,
                pa,
                plen,
                alg,
                partial,
                is_inline: false,
                is_fragment: false,
            });
            cur_la = next_la;
        }
        return Ok((hdr, extents));
    }

    // Standard lcluster-based mapping (COMPRESSED_COMPACT & COMPRESSED_FULL).
    let lcluster_sz = hdr.lcluster_size();
    let total_idx = div_round_up(inode.size, lcluster_sz);
    let mut extents = Vec::new();
    let mut ofs = 0u64;
    let mut guard = 0usize;

    while ofs < inode.size {
        guard += 1;
        if guard > (total_idx as usize) + 16 {
            return Err(ErofsError::Corrupted(format!(
                "infinite loop while mapping compressed nid={}",
                inode.nid
            )));
        }

        let initial_lcn = ofs >> hdr.lclusterbits;
        let ofs_in_lcn = (ofs & (lcluster_sz - 1)) as u16;
        let mut rec = load_lcluster(inode, &hdr, total_idx, initial_lcn, false, read_meta)?;

        // Locate the HEAD lcluster covering `ofs`.
        let mut head_rec = rec.clone();
        let mut compressed_blks = rec.compressed_blks;
        let mut end_la: Option<u64> = None;

        if rec.ltype != Z_LCLUSTER_NONHEAD && ofs_in_lcn < rec.clusterofs {
            // `ofs` falls before this head's start -> belongs to previous extent.
            if initial_lcn == 0 {
                return Err(ErofsError::Corrupted(
                    "first lcluster has non-zero clusterofs".into(),
                ));
            }
            end_la = Some((initial_lcn << hdr.lclusterbits) + rec.clusterofs as u64);
            let mut back_lcn = initial_lcn - 1;
            loop {
                let prev = load_lcluster(inode, &hdr, total_idx, back_lcn, false, read_meta)?;
                if prev.ltype != Z_LCLUSTER_NONHEAD {
                    head_rec = prev;
                    break;
                }
                if prev.compressed_blks != 0 {
                    compressed_blks = prev.compressed_blks;
                }
                let step = (prev.delta0 as u64).max(1);
                if step > back_lcn {
                    return Err(ErofsError::Corrupted("delta0 underflow".into()));
                }
                back_lcn -= step;
            }
        } else if rec.ltype == Z_LCLUSTER_NONHEAD {
            let mut back_lcn = initial_lcn;
            loop {
                if rec.compressed_blks != 0 {
                    compressed_blks = rec.compressed_blks;
                }
                let step = (rec.delta0 as u64).max(1);
                if step > back_lcn {
                    return Err(ErofsError::Corrupted("delta0 underflow".into()));
                }
                back_lcn -= step;
                let prev = load_lcluster(inode, &hdr, total_idx, back_lcn, false, read_meta)?;
                if prev.ltype != Z_LCLUSTER_NONHEAD {
                    head_rec = prev;
                    break;
                }
                rec = prev;
            }
        }

        let m_la = (head_rec.lcn << hdr.lclusterbits) + head_rec.clusterofs as u64;

        // Determine logical end (`end_la`) by scanning forward to the next HEAD lcluster.
        let m_end = if let Some(e) = end_la {
            e
        } else {
            let mut next_lcn = head_rec.lcn + 1;
            let mut found_end = inode.size;
            while next_lcn < total_idx {
                let nxt = load_lcluster(inode, &hdr, total_idx, next_lcn, true, read_meta)?;
                if nxt.ltype != Z_LCLUSTER_NONHEAD {
                    found_end = (next_lcn << hdr.lclusterbits) + nxt.clusterofs as u64;
                    break;
                }
                if nxt.compressed_blks != 0 && compressed_blks == 0 {
                    compressed_blks = nxt.compressed_blks;
                }
                let jump = (nxt.delta1 as u64).max(1);
                next_lcn += jump;
            }
            found_end.min(inode.size)
        };

        // If BIG_PCLUSTER is active and we haven't seen compressed_blks yet, check lcn + 1.
        if compressed_blks == 0 && hdr.has_big_pcluster_for(head_rec.ltype) {
            if head_rec.lcn + 1 < total_idx {
                let after = load_lcluster(inode, &hdr, total_idx, head_rec.lcn + 1, false, read_meta)?;
                if after.ltype == Z_LCLUSTER_NONHEAD && after.compressed_blks != 0 {
                    compressed_blks = after.compressed_blks;
                }
            }
        }
        if compressed_blks == 0 {
            compressed_blks = 1;
        }

        let llen = m_end.saturating_sub(m_la);
        if llen == 0 {
            return Err(ErofsError::Corrupted(format!(
                "zero-length extent at la={m_la} nid={}",
                inode.nid
            )));
        }

        // Check for tail-packing inline pcluster or fragment pcluster.
        let is_tail_lcn = m_end >= inode.size;
        let is_fragment = is_tail_lcn
            && (hdr.advise & Z_ADVISE_FRAGMENT_PCLUSTER) != 0
            && head_rec.pblk == 0xFFFF_FFFF;

        let is_inline = is_tail_lcn
            && !is_fragment
            && hdr.idata_size > 0
            && head_rec.pblk == 0xFFFF_FFFF;

        let (pa, plen, alg) = if is_fragment {
            (hdr.fragment_off as u64, llen as usize, DecodeAlg::Shifted)
        } else if is_inline {
            // Inline pcluster sits immediately after the last index pack.
            let last_rec = load_lcluster(inode, &hdr, total_idx, total_idx - 1, false, read_meta)?;
            let inline_off = last_rec.next_pack_off;
            let alg = hdr.alg_for_head(head_rec.ltype, head_rec.clusterofs)?;
            (inline_off, hdr.idata_size as usize, alg)
        } else {
            let alg = hdr.alg_for_head(head_rec.ltype, head_rec.clusterofs)?;
            let plen = if head_rec.ltype == Z_LCLUSTER_PLAIN {
                (div_round_up(llen, bs) * bs) as usize
            } else {
                (compressed_blks * bs) as usize
            };
            (head_rec.pblk * bs, plen, alg)
        };

        extents.push(ZMappedExtent {
            la: m_la,
            llen,
            pa,
            plen,
            alg,
            partial: head_rec.partial_ref,
            is_inline,
            is_fragment,
        });

        ofs = m_end;
    }

    Ok((hdr, extents))
}

/// Enumerate every 32-bit `blkaddr` metadata offset & current value inside a compressed inode's
/// index table. Used by the in-place compactor to shift compressed pcluster pointers by a delta.
pub fn compressed_blkaddr_slots<F>(
    sb: &SuperBlock,
    inode: &Inode,
    read_meta: &mut F,
) -> Result<Vec<(u64, u32)>>
where
    F: FnMut(u64, usize) -> Result<Vec<u8>>,
{
    if inode.size == 0 {
        return Ok(Vec::new());
    }
    let hdr = ZHeader::parse(sb, inode, read_meta)?;
    if hdr.is_fragment_inode || hdr.is_extents_mode {
        return Err(ErofsError::Compact(
            "fragment or 48-bit extent compressed inodes are not supported for slot patching".into(),
        ));
    }

    let lcluster_sz = hdr.lcluster_size();
    let total_idx = div_round_up(inode.size, lcluster_sz);
    let mut slots = Vec::new();

    match inode.layout {
        DataLayout::CompressedCompact => {
            let mut seen = std::collections::HashSet::new();
            for lcn in 0..total_idx {
                let (base, unit, _idx, _vcnt, _shift) = compact_lcn_geometry(&hdr, total_idx, lcn)?;
                if !seen.insert(base) {
                    continue;
                }
                let slot_off = base + unit - 4;
                let raw = read_meta(slot_off, 4)?;
                if raw.len() == 4 {
                    slots.push((slot_off, read_u32_le(&raw, 0)));
                }
            }
        }
        DataLayout::CompressedFull => {
            for lcn in 0..total_idx {
                let pos = hdr.index_base + lcn * 8;
                let raw = read_meta(pos, 8)?;
                if raw.len() < 8 {
                    continue;
                }
                let ltype = (read_u16_le(&raw, 0) & 3) as u8;
                if ltype != Z_LCLUSTER_NONHEAD {
                    let val = read_u32_le(&raw, 4);
                    if val != 0xFFFF_FFFF {
                        slots.push((pos + 4, val));
                    }
                }
            }
        }
        _ => {}
    }

    Ok(slots)
}

/// Return the end byte offset of a compressed inode's metadata (header + index table + optional inline pcluster).
pub fn compressed_meta_end<F>(
    sb: &SuperBlock,
    inode: &Inode,
    read_meta: &mut F,
) -> Result<u64>
where
    F: FnMut(u64, usize) -> Result<Vec<u8>>,
{
    let hdr = ZHeader::parse(sb, inode, read_meta)?;
    if inode.size == 0 || hdr.is_fragment_inode {
        return Ok(hdr.header_offset + 8);
    }
    if hdr.is_extents_mode {
        let recsize = 4u64
            << (((hdr.advise >> Z_ADVISE_EXTRECSZ_BIT) & Z_ADVISE_EXTRECSZ_MASK) as u64);
        let base = align_up(hdr.index_base, recsize);
        return Ok(base + hdr.extent_count * recsize);
    }
    let lcluster_sz = hdr.lcluster_size();
    let total_idx = div_round_up(inode.size, lcluster_sz);
    let last = load_lcluster(inode, &hdr, total_idx, total_idx - 1, false, read_meta)?;
    Ok(last.next_pack_off + (hdr.idata_size as u64))
}
