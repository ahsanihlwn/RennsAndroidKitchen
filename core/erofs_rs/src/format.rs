// SPDX-License-Identifier: MIT

//! EROFS on-disk binary structures, constants, and feature flags.

use crate::error::{ErofsError, Result};
use serde::Serialize;

pub const EROFS_SUPER_MAGIC: u32 = 0xE0F5_E1E2;
pub const EROFS_SUPER_OFFSET: u64 = 1024;
pub const EROFS_SUPER_BASE_SIZE: usize = 128;
pub const EROFS_SUPER_MAX_SIZE: usize = 144;
pub const EROFS_SB_EXTSLOT_SIZE: usize = 16;
pub const EROFS_ISLOT_BITS: u32 = 5;
pub const EROFS_ISLOT_SIZE: u64 = 32;
pub const EROFS_DIRENT_SIZE: usize = 12;
pub const EROFS_DEVT_SLOT_SIZE: usize = 128;

pub const EROFS_NULL_ADDR_32: u32 = 0xFFFF_FFFF;
pub const EROFS_NULL_ADDR_48: u64 = (1u64 << 48) - 1;
pub const EROFS_NID_METABOX_BIT: u64 = 1u64 << 63;
pub const EROFS_NID_MASK: u64 = EROFS_NID_METABOX_BIT - 1;

// Compatible feature flags
pub const FEATURE_COMPAT_SB_CHKSUM: u32 = 0x0000_0001;
pub const FEATURE_COMPAT_MTIME: u32 = 0x0000_0002;
pub const FEATURE_COMPAT_XATTR_FILTER: u32 = 0x0000_0004;
pub const FEATURE_COMPAT_SHARED_EA_IN_METABOX: u32 = 0x0000_0008;
pub const FEATURE_COMPAT_PLAIN_XATTR_PFX: u32 = 0x0000_0010;
pub const FEATURE_COMPAT_ISHARE_XATTRS: u32 = 0x0000_0020;

// Incompatible feature flags
pub const FEATURE_INCOMPAT_LZ4_0PADDING: u32 = 0x0000_0001;
pub const FEATURE_INCOMPAT_COMPR_CFGS: u32 = 0x0000_0002;
pub const FEATURE_INCOMPAT_BIG_PCLUSTER: u32 = 0x0000_0002;
pub const FEATURE_INCOMPAT_CHUNKED_FILE: u32 = 0x0000_0004;
pub const FEATURE_INCOMPAT_DEVICE_TABLE: u32 = 0x0000_0008;
pub const FEATURE_INCOMPAT_COMPR_HEAD2: u32 = 0x0000_0008;
pub const FEATURE_INCOMPAT_ZTAILPACKING: u32 = 0x0000_0010;
pub const FEATURE_INCOMPAT_FRAGMENTS: u32 = 0x0000_0020;
pub const FEATURE_INCOMPAT_DEDUPE: u32 = 0x0000_0020;
pub const FEATURE_INCOMPAT_XATTR_PREFIXES: u32 = 0x0000_0040;
pub const FEATURE_INCOMPAT_48BIT: u32 = 0x0000_0080;
pub const FEATURE_INCOMPAT_METABOX: u32 = 0x0000_0100;

// Inode format bit masks
pub const INODE_VERSION_COMPACT: u16 = 0;
pub const INODE_VERSION_EXTENDED: u16 = 1;
pub const INODE_DATALAYOUT_SHIFT: u16 = 1;
pub const INODE_DATALAYOUT_MASK: u16 = 0x07;
pub const INODE_NLINK_1_BIT: u16 = 1 << 4;
pub const INODE_DOT_OMITTED_BIT: u16 = 1 << 4;

// Chunk format flags
pub const CHUNK_FORMAT_BLKBITS_MASK: u16 = 0x001F;
pub const CHUNK_FORMAT_INDEXES: u16 = 0x0020;
pub const CHUNK_FORMAT_48BIT: u16 = 0x0040;

// POSIX mode masks
pub const S_IFMT: u16 = 0o170000;
pub const S_IFSOCK: u16 = 0o140000;
pub const S_IFLNK: u16 = 0o120000;
pub const S_IFREG: u16 = 0o100000;
pub const S_IFBLK: u16 = 0o060000;
pub const S_IFDIR: u16 = 0o040000;
pub const S_IFCHR: u16 = 0o020000;
pub const S_IFIFO: u16 = 0o010000;
pub const S_ISUID: u16 = 0o4000;
pub const S_ISGID: u16 = 0o2000;
pub const S_ISVTX: u16 = 0o1000;

// Directory entry file types
pub const FT_UNKNOWN: u8 = 0;
pub const FT_REG_FILE: u8 = 1;
pub const FT_DIR: u8 = 2;
pub const FT_CHRDEV: u8 = 3;
pub const FT_BLKDEV: u8 = 4;
pub const FT_FIFO: u8 = 5;
pub const FT_SOCK: u8 = 6;
pub const FT_SYMLINK: u8 = 7;

// Compression algorithm IDs
pub const COMPR_ALG_LZ4: u8 = 0;
pub const COMPR_ALG_LZMA: u8 = 1;
pub const COMPR_ALG_DEFLATE: u8 = 2;
pub const COMPR_ALG_ZSTD: u8 = 3;

// Compressed map header advise flags
pub const Z_ADVISE_COMPACTED_2B: u16 = 0x0001;
pub const Z_ADVISE_EXTENTS: u16 = 0x0001;
pub const Z_ADVISE_BIG_PCLUSTER_1: u16 = 0x0002;
pub const Z_ADVISE_BIG_PCLUSTER_2: u16 = 0x0004;
pub const Z_ADVISE_INLINE_PCLUSTER: u16 = 0x0008;
pub const Z_ADVISE_INTERLACED_PCLUSTER: u16 = 0x0010;
pub const Z_ADVISE_FRAGMENT_PCLUSTER: u16 = 0x0020;
pub const Z_ADVISE_EXTRECSZ_BIT: u16 = 1;
pub const Z_ADVISE_EXTRECSZ_MASK: u16 = 0x03;

pub const Z_FRAGMENT_INODE_BIT: u8 = 7;
pub const Z_LCLUSTER_PLAIN: u8 = 0;
pub const Z_LCLUSTER_HEAD1: u8 = 1;
pub const Z_LCLUSTER_NONHEAD: u8 = 2;
pub const Z_LCLUSTER_HEAD2: u8 = 3;
pub const Z_LI_PARTIAL_REF: u16 = 1 << 15;
pub const Z_LI_D0_CBLKCNT: u16 = 1 << 11;

pub const Z_PCLUSTER_MAX_SIZE: usize = 1024 * 1024;
pub const Z_PCLUSTER_MAX_DSIZE: usize = 12 * 1024 * 1024;
pub const Z_EXTENT_PLEN_PARTIAL: u32 = 1 << 27;
pub const Z_EXTENT_PLEN_FMT_BIT: u32 = 28;
pub const Z_EXTENT_PLEN_MASK: u32 = ((Z_PCLUSTER_MAX_SIZE as u32) << 1) - 1;

// Xattr constants
pub const XATTR_INDEX_USER: u8 = 1;
pub const XATTR_INDEX_POSIX_ACL_ACCESS: u8 = 2;
pub const XATTR_INDEX_POSIX_ACL_DEFAULT: u8 = 3;
pub const XATTR_INDEX_TRUSTED: u8 = 4;
pub const XATTR_INDEX_LUSTRE: u8 = 5;
pub const XATTR_INDEX_SECURITY: u8 = 6;
pub const XATTR_LONG_PREFIX_FLAG: u8 = 0x80;
pub const XATTR_LONG_PREFIX_MASK: u8 = 0x7F;
pub const XATTR_FILTER_SEED: u32 = 0x25BB_E08F;

/// Inode data layout mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum DataLayout {
    FlatPlain = 0,
    CompressedFull = 1,
    FlatInline = 2,
    CompressedCompact = 3,
    ChunkBased = 4,
}

impl DataLayout {
    pub fn from_raw(v: u8) -> Result<Self> {
        match v {
            0 => Ok(Self::FlatPlain),
            1 => Ok(Self::CompressedFull),
            2 => Ok(Self::FlatInline),
            3 => Ok(Self::CompressedCompact),
            4 => Ok(Self::ChunkBased),
            other => Err(ErofsError::Unsupported(format!(
                "unknown inode data layout {other}"
            ))),
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            Self::FlatPlain => "PLAIN",
            Self::CompressedFull => "COMPRESSED_FULL",
            Self::FlatInline => "INLINE",
            Self::CompressedCompact => "COMPRESSED_COMPACT",
            Self::ChunkBased => "CHUNK",
        }
    }

    pub fn is_compressed(&self) -> bool {
        matches!(self, Self::CompressedFull | Self::CompressedCompact)
    }
}

/// Parsed EROFS superblock.
#[derive(Debug, Clone, Serialize)]
pub struct SuperBlock {
    pub magic: u32,
    pub checksum: u32,
    pub feature_compat: u32,
    pub blkszbits: u8,
    pub block_size: u32,
    pub sb_extslots: u8,
    pub sb_size: usize,
    pub root_nid: u64,
    pub inos: u64,
    pub epoch: u64,
    pub fixed_nsec: u32,
    pub blocks: u64,
    pub meta_blkaddr: u32,
    pub xattr_blkaddr: u32,
    pub uuid: [u8; 16],
    pub volume_name: String,
    pub feature_incompat: u32,
    pub available_compr_algs: u16,
    pub lz4_max_distance: u16,
    pub extra_devices: u16,
    pub devt_slotoff: u16,
    pub dirblkbits: u8,
    pub xattr_prefix_count: u8,
    pub xattr_prefix_start: u32,
    pub packed_nid: u64,
    pub xattr_filter_reserved: u8,
    pub ishare_xattr_prefix_id: u8,
    pub build_time: u32,
    pub metabox_nid: u64,
}

impl SuperBlock {
    pub fn parse(raw: &[u8]) -> Result<Self> {
        if raw.len() < EROFS_SUPER_BASE_SIZE {
            return Err(ErofsError::InvalidFormat("truncated superblock".into()));
        }
        let magic = read_u32_le(raw, 0);
        if magic != EROFS_SUPER_MAGIC {
            return Err(ErofsError::InvalidFormat(format!(
                "bad superblock magic 0x{magic:08x} (expected 0x{EROFS_SUPER_MAGIC:08x})"
            )));
        }

        let checksum = read_u32_le(raw, 4);
        let feature_compat = read_u32_le(raw, 8);
        let blkszbits = raw[12];
        if !(9..=16).contains(&blkszbits) {
            return Err(ErofsError::InvalidFormat(format!(
                "invalid blkszbits {blkszbits}"
            )));
        }
        let block_size = 1u32 << blkszbits;
        let sb_extslots = raw[13];
        let sb_size = EROFS_SUPER_BASE_SIZE + (sb_extslots as usize) * EROFS_SB_EXTSLOT_SIZE;

        let rb = read_u16_le(raw, 14);
        let inos = read_u64_le(raw, 16);
        let epoch = read_u64_le(raw, 24);
        let fixed_nsec = read_u32_le(raw, 32);
        let blocks_lo = read_u32_le(raw, 36);
        let meta_blkaddr = read_u32_le(raw, 40);
        let xattr_blkaddr = read_u32_le(raw, 44);

        let mut uuid = [0u8; 16];
        uuid.copy_from_slice(&raw[48..64]);

        let vol_bytes = &raw[64..80];
        let vol_end = vol_bytes.iter().position(|&b| b == 0).unwrap_or(16);
        let volume_name = String::from_utf8_lossy(&vol_bytes[..vol_end]).into_owned();

        let feature_incompat = read_u32_le(raw, 80);
        let u1_val = read_u16_le(raw, 84);
        let extra_devices = read_u16_le(raw, 86);
        let devt_slotoff = read_u16_le(raw, 88);
        let dirblkbits = raw[90];
        let xattr_prefix_count = raw[91];
        let xattr_prefix_start = read_u32_le(raw, 92);
        let packed_nid = read_u64_le(raw, 96);
        let xattr_filter_reserved = raw[104];
        let ishare_xattr_prefix_id = raw[105];
        let build_time = read_u32_le(raw, 108);

        let has_48bit = (feature_incompat & FEATURE_INCOMPAT_48BIT) != 0;
        let has_metabox = (feature_incompat & FEATURE_INCOMPAT_METABOX) != 0;

        let rootnid_8b = if raw.len() >= 120 {
            read_u64_le(raw, 112)
        } else {
            0
        };
        // Note: In erofs_super_block, byte 112..119 is rootnid_8b, 120..127 is reserved2,
        // 128..135 is metabox_nid (extension slot 0), 136..143 is reserved3.
        // Wait: let's double-check exact offsets from erofs_fs.h!
        // 0: magic(4)
        // 4: checksum(4)
        // 8: feature_compat(4)
        // 12: blkszbits(1)
        // 13: sb_extslots(1)
        // 14: rb(2)
        // 16: inos(8)
        // 24: epoch(8)
        // 32: fixed_nsec(4)
        // 36: blocks_lo(4)
        // 40: meta_blkaddr(4)
        // 44: xattr_blkaddr(4)
        // 48: uuid(16)
        // 64: volume_name(16)
        // 80: feature_incompat(4)
        // 84: u1(2)
        // 86: extra_devices(2)
        // 88: devt_slotoff(2)
        // 90: dirblkbits(1)
        // 91: xattr_prefix_count(1)
        // 92: xattr_prefix_start(4)
        // 96: packed_nid(8)
        // 104: xattr_filter_reserved(1)
        // 105: ishare_xattr_prefix_id(1)
        // 106: reserved(2)
        // 108: build_time(4)
        // 112: rootnid_8b(8)
        // 120: reserved2(8)
        // --- 128 bytes base superblock ends here ---
        // 128: metabox_nid(8)  (extension slot 0)
        // 136: reserved3(8)
        let metabox_nid = if has_metabox {
            if sb_extslots < 1 || raw.len() < 136 {
                return Err(ErofsError::InvalidFormat(
                    "METABOX enabled without superblock extension slot".into(),
                ));
            }
            let mn = read_u64_le(raw, 128);
            if (mn & EROFS_NID_METABOX_BIT) != 0 {
                return Err(ErofsError::InvalidFormat(
                    "METABOX nid points to itself".into(),
                ));
            }
            mn
        } else {
            0
        };

        let (blocks, root_nid) = if has_48bit && rootnid_8b != 0 {
            (((rb as u64) << 32) | (blocks_lo as u64), rootnid_8b)
        } else {
            (blocks_lo as u64, rb as u64)
        };

        let (available_compr_algs, lz4_max_distance) =
            if (feature_incompat & FEATURE_INCOMPAT_COMPR_CFGS) != 0 {
                (u1_val, 0)
            } else if u1_val != 0 || (feature_incompat & FEATURE_INCOMPAT_LZ4_0PADDING) != 0 {
                (1u16 << COMPR_ALG_LZ4, u1_val)
            } else {
                (0, 0)
            };

        Ok(Self {
            magic,
            checksum,
            feature_compat,
            blkszbits,
            block_size,
            sb_extslots,
            sb_size,
            root_nid,
            inos,
            epoch,
            fixed_nsec,
            blocks,
            meta_blkaddr,
            xattr_blkaddr,
            uuid,
            volume_name,
            feature_incompat,
            available_compr_algs,
            lz4_max_distance,
            extra_devices,
            devt_slotoff,
            dirblkbits,
            xattr_prefix_count,
            xattr_prefix_start,
            packed_nid,
            xattr_filter_reserved,
            ishare_xattr_prefix_id,
            build_time,
            metabox_nid,
        })
    }

    pub fn serialize(&self) -> Vec<u8> {
        let sz = self.sb_size.max(EROFS_SUPER_BASE_SIZE);
        let mut buf = vec![0u8; sz];
        write_u32_le(&mut buf, 0, self.magic);
        write_u32_le(&mut buf, 4, self.checksum);
        write_u32_le(&mut buf, 8, self.feature_compat);
        buf[12] = self.blkszbits;
        buf[13] = self.sb_extslots;
        if self.has_48bit() {
            write_u16_le(&mut buf, 14, ((self.blocks >> 32) & 0xFFFF) as u16);
        } else {
            write_u16_le(&mut buf, 14, (self.root_nid & 0xFFFF) as u16);
        }
        write_u64_le(&mut buf, 16, self.inos);
        write_u64_le(&mut buf, 24, self.epoch);
        write_u32_le(&mut buf, 32, self.fixed_nsec);
        write_u32_le(&mut buf, 36, (self.blocks & 0xFFFF_FFFF) as u32);
        write_u32_le(&mut buf, 40, self.meta_blkaddr);
        write_u32_le(&mut buf, 44, self.xattr_blkaddr);
        buf[48..64].copy_from_slice(&self.uuid);
        let vn = self.volume_name.as_bytes();
        let vn_len = vn.len().min(16);
        buf[64..64 + vn_len].copy_from_slice(&vn[..vn_len]);
        write_u32_le(&mut buf, 80, self.feature_incompat);
        if self.has_compr_cfgs() {
            write_u16_le(&mut buf, 84, self.available_compr_algs);
        } else {
            write_u16_le(&mut buf, 84, self.lz4_max_distance);
        }
        write_u16_le(&mut buf, 86, self.extra_devices);
        write_u16_le(&mut buf, 88, self.devt_slotoff);
        buf[90] = self.dirblkbits;
        buf[91] = self.xattr_prefix_count;
        write_u32_le(&mut buf, 92, self.xattr_prefix_start);
        write_u64_le(&mut buf, 96, self.packed_nid);
        buf[104] = self.xattr_filter_reserved;
        buf[105] = self.ishare_xattr_prefix_id;
        write_u32_le(&mut buf, 108, self.build_time);
        if self.has_48bit() {
            write_u64_le(&mut buf, 112, self.root_nid);
        }
        if self.has_metabox() && buf.len() >= 136 {
            write_u64_le(&mut buf, 128, self.metabox_nid);
        }
        buf
    }

    pub fn has_sb_chksum(&self) -> bool {
        (self.feature_compat & FEATURE_COMPAT_SB_CHKSUM) != 0
    }
    pub fn has_mtime(&self) -> bool {
        (self.feature_compat & FEATURE_COMPAT_MTIME) != 0
    }
    pub fn has_xattr_filter(&self) -> bool {
        (self.feature_compat & FEATURE_COMPAT_XATTR_FILTER) != 0
    }
    pub fn has_shared_ea_in_metabox(&self) -> bool {
        (self.feature_compat & FEATURE_COMPAT_SHARED_EA_IN_METABOX) != 0
    }
    pub fn has_plain_xattr_pfx(&self) -> bool {
        (self.feature_compat & FEATURE_COMPAT_PLAIN_XATTR_PFX) != 0
    }
    pub fn has_ishare_xattrs(&self) -> bool {
        (self.feature_compat & FEATURE_COMPAT_ISHARE_XATTRS) != 0
    }
    pub fn has_lz4_0padding(&self) -> bool {
        (self.feature_incompat & FEATURE_INCOMPAT_LZ4_0PADDING) != 0
    }
    pub fn has_compr_cfgs(&self) -> bool {
        (self.feature_incompat & FEATURE_INCOMPAT_COMPR_CFGS) != 0
    }
    pub fn has_big_pcluster(&self) -> bool {
        (self.feature_incompat & FEATURE_INCOMPAT_BIG_PCLUSTER) != 0
    }
    pub fn has_chunked_file(&self) -> bool {
        (self.feature_incompat & FEATURE_INCOMPAT_CHUNKED_FILE) != 0
    }
    pub fn has_device_table(&self) -> bool {
        (self.feature_incompat & FEATURE_INCOMPAT_DEVICE_TABLE) != 0
    }
    pub fn has_ztailpacking(&self) -> bool {
        (self.feature_incompat & FEATURE_INCOMPAT_ZTAILPACKING) != 0
    }
    pub fn has_fragments(&self) -> bool {
        (self.feature_incompat & FEATURE_INCOMPAT_FRAGMENTS) != 0
    }
    pub fn has_xattr_prefixes(&self) -> bool {
        (self.feature_incompat & FEATURE_INCOMPAT_XATTR_PREFIXES) != 0
    }
    pub fn has_48bit(&self) -> bool {
        (self.feature_incompat & FEATURE_INCOMPAT_48BIT) != 0
    }
    pub fn has_metabox(&self) -> bool {
        (self.feature_incompat & FEATURE_INCOMPAT_METABOX) != 0
    }

    pub fn build_timestamp(&self) -> u64 {
        self.epoch.saturating_add(self.build_time as u64)
    }

    pub fn dir_block_size(&self) -> usize {
        1usize << (self.blkszbits as u32 + self.dirblkbits as u32)
    }

    pub fn nid_to_offset(&self, nid: u64) -> u64 {
        let slot = nid & EROFS_NID_MASK;
        (self.meta_blkaddr as u64) * (self.block_size as u64) + slot * EROFS_ISLOT_SIZE
    }

    pub fn uuid_string(&self) -> String {
        format_uuid(&self.uuid)
    }

    pub fn compat_feature_names(&self) -> Vec<&'static str> {
        let mut out = Vec::new();
        if self.has_sb_chksum() {
            out.push("sb_chksum");
        }
        if self.has_mtime() {
            out.push("mtime");
        }
        if self.has_xattr_filter() {
            out.push("xattr_filter");
        }
        if self.has_shared_ea_in_metabox() {
            out.push("shared_ea_in_metabox");
        }
        if self.has_plain_xattr_pfx() {
            out.push("plain_xattr_pfx");
        }
        if self.has_ishare_xattrs() {
            out.push("ishare_xattrs");
        }
        out
    }

    pub fn incompat_feature_names(&self) -> Vec<&'static str> {
        let mut out = Vec::new();
        if self.has_lz4_0padding() {
            out.push("lz4_0padding");
        }
        if self.has_compr_cfgs() {
            out.push("compr_cfgs/big_pcluster");
        }
        if self.has_chunked_file() {
            out.push("chunked_file");
        }
        if self.has_device_table() {
            out.push("device_table/compr_head2");
        }
        if self.has_ztailpacking() {
            out.push("ztailpacking");
        }
        if self.has_fragments() {
            out.push("fragments/dedupe");
        }
        if self.has_xattr_prefixes() {
            out.push("xattr_prefixes");
        }
        if self.has_48bit() {
            out.push("48bit");
        }
        if self.has_metabox() {
            out.push("metabox");
        }
        out
    }

    pub fn algorithm_names(&self) -> Vec<&'static str> {
        let mut out = Vec::new();
        if (self.available_compr_algs & (1 << COMPR_ALG_LZ4)) != 0 {
            out.push("lz4");
        }
        if (self.available_compr_algs & (1 << COMPR_ALG_LZMA)) != 0 {
            out.push("lzma");
        }
        if (self.available_compr_algs & (1 << COMPR_ALG_DEFLATE)) != 0 {
            out.push("deflate");
        }
        if (self.available_compr_algs & (1 << COMPR_ALG_ZSTD)) != 0 {
            out.push("zstd");
        }
        out
    }
}

/// Parsed on-disk inode (compact 32-byte or extended 64-byte).
#[derive(Debug, Clone, Serialize)]
pub struct Inode {
    pub nid: u64,
    pub in_metabox: bool,
    pub offset: u64,
    pub format: u16,
    pub is_extended: bool,
    pub layout: DataLayout,
    pub xattr_icount: u16,
    pub mode: u16,
    pub nlink: u32,
    pub size: u64,
    pub uid: u32,
    pub gid: u32,
    pub mtime: u64,
    pub mtime_nsec: u32,
    pub ino: u32,
    pub u_raw: u32,
    pub nb_raw: u16,
    pub start_blk: u64,
    pub addr_mask: u64,
    pub rdev: u32,
    pub chunk_format: u16,
    pub hdr_size: u64,
    pub xattr_offset: u64,
    pub xattr_size: u64,
    pub data_offset: u64,
    pub dot_omitted: bool,
}

impl Inode {
    pub fn parse(sb: &SuperBlock, nid: u64, raw: &[u8]) -> Result<Self> {
        if raw.len() < 32 {
            return Err(ErofsError::Corrupted(format!(
                "inode nid={nid} truncated ({} bytes)",
                raw.len()
            )));
        }
        let in_metabox = (nid & EROFS_NID_METABOX_BIT) != 0;
        let slot = nid & EROFS_NID_MASK;
        let offset = if in_metabox {
            slot * EROFS_ISLOT_SIZE
        } else {
            sb.nid_to_offset(nid)
        };

        let format = read_u16_le(raw, 0);
        let is_extended = (format & 1) == INODE_VERSION_EXTENDED;
        let layout_raw = ((format >> INODE_DATALAYOUT_SHIFT) & INODE_DATALAYOUT_MASK) as u8;
        let layout = DataLayout::from_raw(layout_raw)?;

        if is_extended && raw.len() < 64 {
            return Err(ErofsError::Corrupted(format!(
                "extended inode nid={nid} truncated ({} bytes)",
                raw.len()
            )));
        }

        let xattr_icount = read_u16_le(raw, 2);
        let mode = read_u16_le(raw, 4);
        let nb_raw = read_u16_le(raw, 6);
        let is_dir = (mode & S_IFMT) == S_IFDIR;
        let dot_omitted = is_dir && (format & INODE_DOT_OMITTED_BIT) != 0;

        let (size, u_raw, ino, uid, gid, mtime, mtime_nsec, nlink, start_hi, addr_mask, hdr_size) =
            if is_extended {
                let size = read_u64_le(raw, 8);
                let u_raw = read_u32_le(raw, 16);
                let ino = read_u32_le(raw, 20);
                let uid = read_u32_le(raw, 24);
                let gid = read_u32_le(raw, 28);
                let mtime = read_u64_le(raw, 32);
                let mtime_nsec = read_u32_le(raw, 40);
                let nlink = read_u32_le(raw, 44);
                (
                    size,
                    u_raw,
                    ino,
                    uid,
                    gid,
                    mtime,
                    mtime_nsec,
                    nlink,
                    nb_raw as u64,
                    EROFS_NULL_ADDR_48,
                    64u64,
                )
            } else {
                let size = read_u32_le(raw, 8) as u64;
                let mtime_delta = read_u32_le(raw, 12) as u64;
                let u_raw = read_u32_le(raw, 16);
                let ino = read_u32_le(raw, 20);
                let uid = read_u16_le(raw, 24) as u32;
                let gid = read_u16_le(raw, 26) as u32;
                let has_nlink1 = !is_dir && (format & INODE_NLINK_1_BIT) != 0;
                let (nlink, start_hi, addr_mask) = if has_nlink1 {
                    (1u32, nb_raw as u64, EROFS_NULL_ADDR_48)
                } else {
                    (nb_raw as u32, 0u64, EROFS_NULL_ADDR_32 as u64)
                };
                let (mtime, mtime_nsec) = if sb.has_mtime() {
                    (sb.epoch.saturating_add(mtime_delta), sb.fixed_nsec)
                } else {
                    (sb.epoch, sb.fixed_nsec)
                };
                (
                    size, u_raw, ino, uid, gid, mtime, mtime_nsec, nlink, start_hi, addr_mask,
                    32u64,
                )
            };

        let start_blk = (start_hi << 32) | (u_raw as u64);
        let xattr_offset = offset + hdr_size;
        let xattr_size = if xattr_icount == 0 {
            0
        } else {
            12 + ((xattr_icount as u64) - 1) * 4
        };
        let data_offset = xattr_offset + xattr_size;

        Ok(Self {
            nid,
            in_metabox,
            offset,
            format,
            is_extended,
            layout,
            xattr_icount,
            mode,
            nlink,
            size,
            uid,
            gid,
            mtime,
            mtime_nsec,
            ino,
            u_raw,
            nb_raw,
            start_blk,
            addr_mask,
            rdev: u_raw,
            chunk_format: (u_raw & 0xFFFF) as u16,
            hdr_size,
            xattr_offset,
            xattr_size,
            data_offset,
            dot_omitted,
        })
    }

    pub fn is_null_start(&self) -> bool {
        ((self.start_blk ^ EROFS_NULL_ADDR_48) & self.addr_mask) == 0
    }

    pub fn file_type(&self) -> u8 {
        mode_to_ftype(self.mode)
    }

    pub fn is_dir(&self) -> bool {
        (self.mode & S_IFMT) == S_IFDIR
    }

    pub fn is_reg(&self) -> bool {
        (self.mode & S_IFMT) == S_IFREG
    }

    pub fn is_symlink(&self) -> bool {
        (self.mode & S_IFMT) == S_IFLNK
    }

    pub fn is_special(&self) -> bool {
        matches!(self.mode & S_IFMT, S_IFCHR | S_IFBLK | S_IFIFO | S_IFSOCK)
    }
}

/// Directory entry decoded from an EROFS directory block.
#[derive(Debug, Clone)]
pub struct DirEntry {
    pub nid: u64,
    pub name: String,
    pub raw_name: Vec<u8>,
    pub file_type: u8,
}

pub fn mode_to_ftype(mode: u16) -> u8 {
    match mode & S_IFMT {
        S_IFREG => FT_REG_FILE,
        S_IFDIR => FT_DIR,
        S_IFCHR => FT_CHRDEV,
        S_IFBLK => FT_BLKDEV,
        S_IFIFO => FT_FIFO,
        S_IFSOCK => FT_SOCK,
        S_IFLNK => FT_SYMLINK,
        _ => FT_UNKNOWN,
    }
}

pub fn ftype_label(ftype: u8) -> &'static str {
    match ftype {
        FT_DIR => "DIR",
        FT_REG_FILE => "FILE",
        FT_SYMLINK => "LINK",
        FT_CHRDEV => "CHR",
        FT_BLKDEV => "BLK",
        FT_FIFO => "FIFO",
        FT_SOCK => "SOCK",
        _ => "UNKNOWN",
    }
}

pub fn mode_to_string(mode: u16) -> String {
    let type_ch = match mode & S_IFMT {
        S_IFSOCK => 's',
        S_IFLNK => 'l',
        S_IFREG => '-',
        S_IFBLK => 'b',
        S_IFDIR => 'd',
        S_IFCHR => 'c',
        S_IFIFO => 'p',
        _ => '?',
    };
    let rwx = |bits: u16, special: bool, lo: char, hi: char| -> [char; 3] {
        let r = if (bits & 0o4) != 0 { 'r' } else { '-' };
        let w = if (bits & 0o2) != 0 { 'w' } else { '-' };
        let x = if special {
            if (bits & 0o1) != 0 { lo } else { hi }
        } else if (bits & 0o1) != 0 {
            'x'
        } else {
            '-'
        };
        [r, w, x]
    };
    let u = rwx((mode >> 6) & 7, (mode & S_ISUID) != 0, 's', 'S');
    let g = rwx((mode >> 3) & 7, (mode & S_ISGID) != 0, 's', 'S');
    let o = rwx(mode & 7, (mode & S_ISVTX) != 0, 't', 'T');
    let mut s = String::with_capacity(10);
    s.push(type_ch);
    for ch in u.into_iter().chain(g).chain(o) {
        s.push(ch);
    }
    s
}

pub fn format_uuid(u: &[u8; 16]) -> String {
    format!(
        "{:02x}{:02x}{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
        u[0], u[1], u[2], u[3], u[4], u[5], u[6], u[7],
        u[8], u[9], u[10], u[11], u[12], u[13], u[14], u[15]
    )
}

pub fn parse_uuid(s: &str) -> Result<[u8; 16]> {
    let hex: String = s.chars().filter(|&c| c != '-').collect();
    if hex.len() != 32 {
        return Err(ErofsError::InvalidArg(format!("invalid UUID: {s}")));
    }
    let mut out = [0u8; 16];
    for i in 0..16 {
        out[i] = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16)
            .map_err(|_| ErofsError::InvalidArg(format!("invalid UUID hex: {s}")))?;
    }
    Ok(out)
}

#[inline]
pub fn read_u16_le(buf: &[u8], off: usize) -> u16 {
    u16::from_le_bytes([buf[off], buf[off + 1]])
}

#[inline]
pub fn read_u32_le(buf: &[u8], off: usize) -> u32 {
    u32::from_le_bytes([buf[off], buf[off + 1], buf[off + 2], buf[off + 3]])
}

#[inline]
pub fn read_u64_le(buf: &[u8], off: usize) -> u64 {
    u64::from_le_bytes([
        buf[off],
        buf[off + 1],
        buf[off + 2],
        buf[off + 3],
        buf[off + 4],
        buf[off + 5],
        buf[off + 6],
        buf[off + 7],
    ])
}

#[inline]
pub fn write_u16_le(buf: &mut [u8], off: usize, v: u16) {
    buf[off..off + 2].copy_from_slice(&v.to_le_bytes());
}

#[inline]
pub fn write_u32_le(buf: &mut [u8], off: usize, v: u32) {
    buf[off..off + 4].copy_from_slice(&v.to_le_bytes());
}

#[inline]
pub fn write_u64_le(buf: &mut [u8], off: usize, v: u64) {
    buf[off..off + 8].copy_from_slice(&v.to_le_bytes());
}

#[inline]
pub fn align_up(v: u64, align: u64) -> u64 {
    debug_assert!(align.is_power_of_two());
    (v + align - 1) & !(align - 1)
}

#[inline]
pub fn align_up_usize(v: usize, align: usize) -> usize {
    debug_assert!(align.is_power_of_two());
    (v + align - 1) & !(align - 1)
}

#[inline]
pub fn div_round_up(a: u64, b: u64) -> u64 {
    if a == 0 { 0 } else { (a + b - 1) / b }
}
