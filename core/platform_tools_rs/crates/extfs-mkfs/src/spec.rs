//! Ext2 / Ext3 / Ext4 and JBD2 on-disk specification constants (MIT clean-room).
//!
//! All structures and constants are derived from the public Linux kernel
//! Ext4 Disk Layout documentation (`Documentation/filesystems/ext4/`).

/// Ext2/Ext3/Ext4 superblock magic (`s_magic` at offset `0x38` = 56).
pub const EXT4_SUPER_MAGIC: u16 = 0xEF53;

/// Byte offset of primary superblock from start of volume.
pub const SUPERBLOCK_OFFSET: u64 = 1024;
/// Byte size of the Ext4 superblock structure.
pub const SUPERBLOCK_SIZE: usize = 1024;

/// Dynamic revision level (`EXT2_DYNAMIC_REV`).
pub const EXT2_DYNAMIC_REV: u32 = 1;
/// Good old revision level (`EXT2_GOOD_OLD_REV`).
pub const EXT2_GOOD_OLD_REV: u32 = 0;

/// Filesystem states (`s_state`).
pub const EXT2_VALID_FS: u16 = 0x0001;

/// Error handling behaviors (`s_errors`).
pub const EXT2_ERRORS_CONTINUE: u16 = 1;
pub const EXT2_ERRORS_RO: u16 = 2;
pub const EXT2_ERRORS_PANIC: u16 = 3;

/// Creator OS codes (`s_creator_os`).
pub const EXT2_OS_LINUX: u32 = 0;
pub const EXT2_OS_HURD: u32 = 1;
pub const EXT2_OS_MASIX: u32 = 2;
pub const EXT2_OS_FREEBSD: u32 = 3;
pub const EXT2_OS_LITES: u32 = 4;

/// Compatible feature flags (`s_feature_compat` at offset `0x5C` = 92).
pub const EXT2_FEATURE_COMPAT_DIR_PREALLOC: u32 = 0x0001;
pub const EXT2_FEATURE_COMPAT_IMAGIC_INODES: u32 = 0x0002;
pub const EXT3_FEATURE_COMPAT_HAS_JOURNAL: u32 = 0x0004;
pub const EXT2_FEATURE_COMPAT_EXT_ATTR: u32 = 0x0008;
pub const EXT2_FEATURE_COMPAT_RESIZE_INODE: u32 = 0x0010;
pub const EXT2_FEATURE_COMPAT_DIR_INDEX: u32 = 0x0020;
pub const EXT4_FEATURE_COMPAT_SPARSE_SUPER2: u32 = 0x0200;
pub const EXT4_FEATURE_COMPAT_FAST_COMMIT: u32 = 0x0400;
pub const EXT4_FEATURE_COMPAT_STABLE_INODES: u32 = 0x0800;
pub const EXT4_FEATURE_COMPAT_ORPHAN_FILE: u32 = 0x1000;

/// Incompatible feature flags (`s_feature_incompat` at offset `0x60` = 96).
pub const EXT2_FEATURE_INCOMPAT_COMPRESSION: u32 = 0x0001;
pub const EXT2_FEATURE_INCOMPAT_FILETYPE: u32 = 0x0002;
pub const EXT3_FEATURE_INCOMPAT_RECOVER: u32 = 0x0004;
pub const EXT3_FEATURE_INCOMPAT_JOURNAL_DEV: u32 = 0x0008;
pub const EXT2_FEATURE_INCOMPAT_META_BG: u32 = 0x0010;
pub const EXT4_FEATURE_INCOMPAT_EXTENTS: u32 = 0x0040;
pub const EXT4_FEATURE_INCOMPAT_64BIT: u32 = 0x0080;
pub const EXT4_FEATURE_INCOMPAT_MMP: u32 = 0x0100;
pub const EXT4_FEATURE_INCOMPAT_FLEX_BG: u32 = 0x0200;
pub const EXT4_FEATURE_INCOMPAT_EA_INODE: u32 = 0x0400;
pub const EXT4_FEATURE_INCOMPAT_DIRDATA: u32 = 0x1000;
pub const EXT4_FEATURE_INCOMPAT_CSUM_SEED: u32 = 0x2000;
pub const EXT4_FEATURE_INCOMPAT_LARGEDIR: u32 = 0x4000;
pub const EXT4_FEATURE_INCOMPAT_INLINE_DATA: u32 = 0x8000;
pub const EXT4_FEATURE_INCOMPAT_ENCRYPT: u32 = 0x10000;
pub const EXT4_FEATURE_INCOMPAT_CASEFOLD: u32 = 0x20000;

/// Read-only compatible feature flags (`s_feature_ro_compat` at offset `0x64` = 100).
pub const EXT2_FEATURE_RO_COMPAT_SPARSE_SUPER: u32 = 0x0001;
pub const EXT2_FEATURE_RO_COMPAT_LARGE_FILE: u32 = 0x0002;
pub const EXT2_FEATURE_RO_COMPAT_BTREE_DIR: u32 = 0x0004;
pub const EXT4_FEATURE_RO_COMPAT_HUGE_FILE: u32 = 0x0008;
pub const EXT4_FEATURE_RO_COMPAT_GDT_CSUM: u32 = 0x0010; // uninit_bg
pub const EXT4_FEATURE_RO_COMPAT_DIR_NLINK: u32 = 0x0020;
pub const EXT4_FEATURE_RO_COMPAT_EXTRA_ISIZE: u32 = 0x0040;
pub const EXT4_FEATURE_RO_COMPAT_QUOTA: u32 = 0x0100;
pub const EXT4_FEATURE_RO_COMPAT_BIGALLOC: u32 = 0x0200;
pub const EXT4_FEATURE_RO_COMPAT_METADATA_CSUM: u32 = 0x0400;
pub const EXT4_FEATURE_RO_COMPAT_REPLICA: u32 = 0x0800;
pub const EXT4_FEATURE_RO_COMPAT_READONLY: u32 = 0x1000;
pub const EXT4_FEATURE_RO_COMPAT_PROJECT: u32 = 0x2000;
pub const EXT4_FEATURE_RO_COMPAT_VERITY: u32 = 0x8000;
pub const EXT4_FEATURE_RO_COMPAT_ORPHAN_PRESENT: u32 = 0x10000;

/// Default mount options (`s_default_mount_opts` at offset `0x100` = 256).
pub const EXT2_DEFM_DEBUG: u32 = 0x0001;
pub const EXT2_DEFM_BSDGROUPS: u32 = 0x0002;
pub const EXT2_DEFM_XATTR_USER: u32 = 0x0004;
pub const EXT2_DEFM_ACL: u32 = 0x0008;
pub const EXT2_DEFM_UID16: u32 = 0x0010;
pub const EXT3_DEFM_JMODE_DATA: u32 = 0x0020;
pub const EXT3_DEFM_JMODE_ORDERED: u32 = 0x0040;
pub const EXT3_DEFM_JMODE_WBACK: u32 = 0x0060;
pub const EXT4_DEFM_NOBARRIER: u32 = 0x0100;
pub const EXT4_DEFM_BLOCK_VALIDITY: u32 = 0x0200;
pub const EXT4_DEFM_DISCARD: u32 = 0x0400;
pub const EXT4_DEFM_NODELALLOC: u32 = 0x0800;

/// Superblock flags (`s_flags` at offset `0x160` = 352).
pub const EXT2_FLAGS_SIGNED_HASH: u32 = 0x0001;
pub const EXT2_FLAGS_UNSIGNED_HASH: u32 = 0x0002;
pub const EXT2_FLAGS_TEST_FILESYS: u32 = 0x0004;

/// Directory hash versions (`s_def_hash_version` at offset `0xFC` = 252).
pub const DX_HASH_LEGACY: u8 = 0;
pub const DX_HASH_HALF_MD4: u8 = 1;
pub const DX_HASH_TEA: u8 = 2;

/// Checksum algorithm type (`s_checksum_type` at offset `0x174` = 372).
pub const EXT4_CRC32C_CHKSUM: u8 = 1;

/// Character encoding values (`s_encoding` at offset `0x23C` = 572).
pub const EXT4_ENC_UTF8_12_1: u16 = 1;
pub const EXT4_ENC_STRICT_MODE_FL: u16 = 0x0001;

/// Reserved special inode numbers.
pub const EXT2_BAD_INO: u32 = 1;
pub const EXT2_ROOT_INO: u32 = 2;
pub const EXT4_USR_QUOTA_INO: u32 = 3;
pub const EXT4_GRP_QUOTA_INO: u32 = 4;
pub const EXT2_BOOT_LOADER_INO: u32 = 5;
pub const EXT2_UNDEL_DIR_INO: u32 = 6;
pub const EXT2_RESIZE_INO: u32 = 7;
pub const EXT2_JOURNAL_INO: u32 = 8;
pub const EXT2_GOOD_OLD_FIRST_INO: u32 = 11;

/// Block group descriptor flags (`bg_flags` at offset `0x12` = 18).
pub const EXT2_BG_INODE_UNINIT: u16 = 0x0001;
pub const EXT2_BG_BLOCK_UNINIT: u16 = 0x0002;
pub const EXT2_BG_INODE_ZEROED: u16 = 0x0004;

/// Inode mode constants (`i_mode`).
pub const S_IFMT: u16 = 0o170000;
pub const S_IFSOCK: u16 = 0o140000;
pub const S_IFLNK: u16 = 0o120000;
pub const S_IFREG: u16 = 0o100000;
pub const S_IFBLK: u16 = 0o060000;
pub const S_IFDIR: u16 = 0o040000;
pub const S_IFCHR: u16 = 0o020000;
pub const S_IFIFO: u16 = 0o010000;

/// Inode flags (`i_flags` at offset `0x20` = 32).
pub const EXT4_INDEX_FL: u32 = 0x0000_1000;
pub const EXT4_HUGE_FILE_FL: u32 = 0x0004_0000;
pub const EXT4_EXTENTS_FL: u32 = 0x0008_0000;
pub const EXT4_VERITY_FL: u32 = 0x0010_0000;
pub const EXT4_EA_INODE_FL: u32 = 0x0020_0000;
pub const EXT4_INLINE_DATA_FL: u32 = 0x1000_0000;
pub const EXT4_PROJINHERIT_FL: u32 = 0x2000_0000;
pub const EXT4_CASEFOLD_FL: u32 = 0x4000_0000;

/// Directory entry file types (`file_type` in `ext2_dir_entry_2`).
pub const EXT2_FT_UNKNOWN: u8 = 0;
pub const EXT2_FT_REG_FILE: u8 = 1;
pub const EXT2_FT_DIR: u8 = 2;
pub const EXT2_FT_CHRDEV: u8 = 3;
pub const EXT2_FT_BLKDEV: u8 = 4;
pub const EXT2_FT_FIFO: u8 = 5;
pub const EXT2_FT_SOCK: u8 = 6;
pub const EXT2_FT_SYMLINK: u8 = 7;
pub const EXT2_FT_CHECKSUM: u8 = 0xDE;

/// Extent tree header magic (`eh_magic` = `0xF30A`).
pub const EXT4_EXT_MAGIC: u16 = 0xF30A;

/// Extended attribute block / ibody magic (`0xEA020000`).
pub const EXT4_XATTR_MAGIC: u32 = 0xEA02_0000;

/// JBD2 Journal header magic (`0xC03B3998`, stored big-endian!).
pub const JBD2_MAGIC_NUMBER: u32 = 0xC03B_3998;
/// JBD2 Superblock v2 blocktype (`4`, stored big-endian!).
pub const JBD2_SUPERBLOCK_V2: u32 = 4;
/// JBD2 Checksum type CRC32C (`4`).
pub const JBD2_CRC32C_CHKSUM: u8 = 4;
/// JBD2 Incompat feature: 64-bit block numbers.
pub const JBD2_FEATURE_INCOMPAT_64BIT: u32 = 0x0000_0002;
/// JBD2 Incompat feature: v3 checksum.
pub const JBD2_FEATURE_INCOMPAT_CSUM_V3: u32 = 0x0000_0010;
/// Backup type in `s_jnl_backup_type`: `EXT3_JNL_BACKUP_BLOCKS = 1`.
pub const EXT3_JNL_BACKUP_BLOCKS: u8 = 1;
