//! Public Linux F2FS v1.16 on-disk format constants and feature flags.

/// F2FS superblock magic number (`0xF2F52010`).
pub const F2FS_SUPER_MAGIC: u32 = 0xf2f5_2010;
/// On-disk major version.
pub const F2FS_MAJOR_VERSION: u16 = 1;
/// On-disk minor version.
pub const F2FS_MINOR_VERSION: u16 = 16;

/// Byte offset of the F2FS superblock inside block 0 (and backup block 1).
pub const F2FS_SUPER_OFFSET: usize = 1024;
/// Size in bytes of the on-disk `f2fs_super_block` structure.
pub const F2FS_SUPER_SIZE: usize = 3072;
/// Standard F2FS block size in bytes.
pub const F2FS_BLKSIZE: u32 = 4096;
/// Base-2 log of `F2FS_BLKSIZE`.
pub const F2FS_LOG_BLKSIZE: u32 = 12;
/// Number of 4 KiB blocks per 2 MiB segment.
pub const BLOCKS_PER_SEG: u32 = 512;
/// Base-2 log of `BLOCKS_PER_SEG`.
pub const LOG_BLOCKS_PER_SEG: u32 = 9;

/// Number of SIT entries per 4 KiB SIT block (`4096 / 74 = 55`).
pub const SIT_ENTRY_PER_BLOCK: u32 = 55;
/// Number of NAT entries per 4 KiB NAT block (`4096 / 9 = 455`).
pub const NAT_ENTRY_PER_BLOCK: u32 = 455;

/// Special reserved inode numbers.
pub const F2FS_NODE_INO: u32 = 1;
pub const F2FS_META_INO: u32 = 2;
pub const F2FS_ROOT_INO: u32 = 3;

/// Number of active node and data cursegs.
pub const NR_CURSEG_DATA_TYPE: usize = 3;
pub const NR_CURSEG_NODE_TYPE: usize = 3;
pub const NR_CURSEG_TYPE: usize = NR_CURSEG_DATA_TYPE + NR_CURSEG_NODE_TYPE;

/// Curseg type indices.
pub const CURSEG_HOT_DATA: u16 = 0;
pub const CURSEG_WARM_DATA: u16 = 1;
pub const CURSEG_COLD_DATA: u16 = 2;
pub const CURSEG_HOT_NODE: u16 = 3;
pub const CURSEG_WARM_NODE: u16 = 4;
pub const CURSEG_COLD_NODE: u16 = 5;

/// F2FS superblock feature bitmask constants (`sb.feature` at offset 2180).
pub const F2FS_FEATURE_ENCRYPT: u32 = 0x0001;
pub const F2FS_FEATURE_BLKZONED: u32 = 0x0002;
pub const F2FS_FEATURE_ATOMIC_WRITE: u32 = 0x0004;
pub const F2FS_FEATURE_EXTRA_ATTR: u32 = 0x0008;
pub const F2FS_FEATURE_PRJQUOTA: u32 = 0x0010;
pub const F2FS_FEATURE_INODE_CHKSUM: u32 = 0x0020;
pub const F2FS_FEATURE_FLEXIBLE_INLINE_XATTR: u32 = 0x0040;
pub const F2FS_FEATURE_QUOTA_INO: u32 = 0x0080;
pub const F2FS_FEATURE_INODE_CRTIME: u32 = 0x0100;
pub const F2FS_FEATURE_LOST_FOUND: u32 = 0x0200;
pub const F2FS_FEATURE_VERITY: u32 = 0x0400;
pub const F2FS_FEATURE_SB_CHKSUM: u32 = 0x0800;
pub const F2FS_FEATURE_CASEFOLD: u32 = 0x1000;
pub const F2FS_FEATURE_COMPRESSION: u32 = 0x2000;
pub const F2FS_FEATURE_RO: u32 = 0x4000;

/// Checkpoint flags (`cp.ckpt_flags` at offset 132).
pub const CP_UMOUNT_FLAG: u32 = 0x0000_0001;
pub const CP_COMPACT_SUM_FLAG: u32 = 0x0000_0004;
pub const CP_NAT_BITS_FLAG: u32 = 0x0000_0080;
pub const CP_TRIMMED_FLAG: u32 = 0x0000_0100;

/// Filename encoding constants (`sb.s_encoding` and `sb.s_encoding_flags`).
pub const F2FS_ENC_UTF8_12_1: u16 = 1;
pub const F2FS_ENC_STRICT_MODE_FL: u16 = 0x0001;
pub const F2FS_ENC_NO_COMPAT_FALLBACK_FL: u16 = 0x0002;

/// Inode inline flags (`i_inline`).
pub const F2FS_EXTRA_ATTR_FL: u8 = 0x20;

/// Inode flags (`i_flags`).
pub const F2FS_IMMUTABLE_FL: u32 = 0x0000_0010;
pub const F2FS_NOATIME_FL: u32 = 0x0000_0080;
pub const F2FS_CASEFOLD_FL: u32 = 0x4000_0000;

/// Directory entry file types.
pub const F2FS_FT_DIR: u8 = 2;

/// Summary block footer types.
pub const SUM_TYPE_DATA: u8 = 0;
pub const SUM_TYPE_NODE: u8 = 1;

/// Deterministic checkpoint version used when `-r` is specified (`srand(0)` -> `rand()`).
pub const DETERMINISTIC_CP_VER: u64 = 0x6b8b_4567;

/// Linux VFS quota v2r1 (`vfsv1`) magic numbers for `[USRQUOTA, GRPQUOTA, PRJQUOTA]`.
pub const V2_INITQMAGICS: [u32; 3] = [0xd9c0_1f11, 0xd9c0_1927, 0xd9c0_3f14];
pub const V2_INITQVERSION: u32 = 1;
pub const V2_MAX_DQ_TIME: u32 = 604_800;
pub const V2_MAX_IQ_TIME: u32 = 604_800;

/// Maximum number of extensions stored in `sb.extension_list` (64 entries of 8 bytes).
pub const F2FS_MAX_EXTENSION: usize = 64;

/// Default cold file extensions (media, archives, packages, shared libraries).
pub const DEFAULT_COLD_EXTENSIONS: &[&str] = &[
    "mp", "wm", "og", "jp", "avi", "m4v", "m4p", "mkv", "mov", "webm", "wav", "m4a", "3gp",
    "opus", "flac", "gif", "png", "svg", "webp", "jar", "deb", "iso", "gz", "xz", "zst", "pdf",
    "pyc", "ttc", "ttf", "exe", "apk", "cnt", "exo", "odex", "vdex", "so",
];

/// Default hot file extensions (databases).
pub const DEFAULT_HOT_EXTENSIONS: &[&str] = &["db"];
