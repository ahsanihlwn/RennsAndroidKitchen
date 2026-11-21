//! On-disk ext4 structures, constants, and byte-level serialization helpers.

pub const EXT4_SUPER_MAGIC: u16 = 0xEF53;
pub const EXT4_VALID_FS: u16 = 0x0001;
pub const EXT4_ERRORS_RO: u16 = 2;
pub const EXT4_OS_LINUX: u32 = 0;
pub const EXT4_DYNAMIC_REV: u32 = 1;
pub const EXT4_GOOD_OLD_FIRST_INO: u32 = 11;
pub const EXT4_GOOD_OLD_INODE_SIZE: u16 = 128;
pub const EXT4_INODE_STRUCT_SIZE: usize = 156;
pub const EXT4_DEF_RESUID: u16 = 0;
pub const EXT4_DEF_RESGID: u16 = 0;
pub const EXT4_JNL_BACKUP_BLOCKS: u8 = 1;
pub const DX_HASH_TEA: u8 = 2;

pub const EXT4_ROOT_INO: u32 = 2;
pub const EXT4_RESIZE_INO: u32 = 7;
pub const EXT4_JOURNAL_INO: u32 = 8;

pub const EXT4_FEATURE_COMPAT_HAS_JOURNAL: u16 = 0x0004;
pub const EXT4_FEATURE_COMPAT_EXT_ATTR: u16 = 0x0008;
pub const EXT4_FEATURE_COMPAT_RESIZE_INODE: u16 = 0x0010;

pub const EXT4_FEATURE_RO_COMPAT_SPARSE_SUPER: u16 = 0x0001;
pub const EXT4_FEATURE_RO_COMPAT_LARGE_FILE: u16 = 0x0002;
pub const EXT4_FEATURE_RO_COMPAT_GDT_CSUM: u16 = 0x0010;

pub const EXT4_FEATURE_INCOMPAT_FILETYPE: u16 = 0x0002;
pub const EXT4_FEATURE_INCOMPAT_EXTENTS: u16 = 0x0040;

pub const EXT4_BG_INODE_UNINIT: u16 = 0x0001;

pub const EXT4_NOATIME_FL: u32 = 0x0000_0080;
pub const EXT4_EXTENTS_FL: u32 = 0x0008_0000;

pub const EXT4_NDIR_BLOCKS: u32 = 12;
pub const EXT4_IND_BLOCK: usize = 12;
pub const EXT4_DIND_BLOCK: usize = 13;
pub const EXT4_TIND_BLOCK: usize = 14;
pub const EXT4_N_BLOCKS: usize = 15;

pub const EXT4_EXT_MAGIC: u16 = 0xF30A;

pub const EXT4_FT_UNKNOWN: u8 = 0;
pub const EXT4_FT_REG_FILE: u8 = 1;
pub const EXT4_FT_DIR: u8 = 2;
pub const EXT4_FT_CHRDEV: u8 = 3;
pub const EXT4_FT_BLKDEV: u8 = 4;
pub const EXT4_FT_FIFO: u8 = 5;
pub const EXT4_FT_SOCK: u8 = 6;
pub const EXT4_FT_SYMLINK: u8 = 7;

pub const S_IFMT: u32 = 0o170000;
pub const S_IFSOCK: u32 = 0o140000;
pub const S_IFLNK: u32 = 0o120000;
pub const S_IFREG: u32 = 0o100000;
pub const S_IFBLK: u32 = 0o060000;
pub const S_IFDIR: u32 = 0o040000;
pub const S_IFCHR: u32 = 0o020000;
pub const S_IFIFO: u32 = 0o010000;

pub const JBD2_MAGIC_NUMBER: u32 = 0xC03B_3998;
pub const JBD2_SUPERBLOCK_V2: u32 = 4;

pub const EXT4_XATTR_MAGIC: u32 = 0xEA02_0000;
pub const EXT4_XATTR_INDEX_SECURITY: u8 = 6;
pub const EXT4_XATTR_HEADER_SIZE: usize = 32;
pub const EXT4_XATTR_ENTRY_SIZE: usize = 16;

pub const VFS_CAP_FLAGS_EFFECTIVE: u32 = 0x000001;
pub const VFS_CAP_REVISION_2: u32 = 0x0200_0000;

pub const GROUP_DESC_SIZE: usize = 32;
pub const SUPERBLOCK_SIZE: usize = 1024;

#[inline]
pub const fn div_round_up_u32(x: u32, y: u32) -> u32 {
    (x + y - 1) / y
}

#[inline]
pub const fn div_round_up_u64(x: u64, y: u64) -> u64 {
    (x + y - 1) / y
}

#[inline]
pub const fn align_u32(x: u32, y: u32) -> u32 {
    y * div_round_up_u32(x, y)
}

#[inline]
pub fn read_u16_le(buf: &[u8], off: usize) -> u16 {
    u16::from_le_bytes([buf[off], buf[off + 1]])
}

#[inline]
pub fn write_u16_le(buf: &mut [u8], off: usize, val: u16) {
    buf[off..off + 2].copy_from_slice(&val.to_le_bytes());
}

#[inline]
pub fn read_u32_le(buf: &[u8], off: usize) -> u32 {
    u32::from_le_bytes([buf[off], buf[off + 1], buf[off + 2], buf[off + 3]])
}

#[inline]
pub fn write_u32_le(buf: &mut [u8], off: usize, val: u32) {
    buf[off..off + 4].copy_from_slice(&val.to_le_bytes());
}

/// Byte offsets within `ext4_super_block` (1024 bytes).
pub mod sb_off {
    pub const S_INODES_COUNT: usize = 0x00;
    pub const S_BLOCKS_COUNT_LO: usize = 0x04;
    pub const S_FREE_BLOCKS_COUNT_LO: usize = 0x0C;
    pub const S_FREE_INODES_COUNT: usize = 0x10;
    pub const S_FIRST_DATA_BLOCK: usize = 0x14;
    pub const S_LOG_BLOCK_SIZE: usize = 0x18;
    pub const S_OBSO_LOG_FRAG_SIZE: usize = 0x1C;
    pub const S_BLOCKS_PER_GROUP: usize = 0x20;
    pub const S_OBSO_FRAGS_PER_GROUP: usize = 0x24;
    pub const S_INODES_PER_GROUP: usize = 0x28;
    pub const S_MAX_MNT_COUNT: usize = 0x36;
    pub const S_MAGIC: usize = 0x38;
    pub const S_STATE: usize = 0x3A;
    pub const S_ERRORS: usize = 0x3C;
    pub const S_CREATOR_OS: usize = 0x48;
    pub const S_REV_LEVEL: usize = 0x4C;
    pub const S_DEF_RESUID: usize = 0x50;
    pub const S_DEF_RESGID: usize = 0x52;
    pub const S_FIRST_INO: usize = 0x54;
    pub const S_INODE_SIZE: usize = 0x58;
    pub const S_BLOCK_GROUP_NR: usize = 0x5A;
    pub const S_FEATURE_COMPAT: usize = 0x5C;
    pub const S_FEATURE_INCOMPAT: usize = 0x60;
    pub const S_FEATURE_RO_COMPAT: usize = 0x64;
    pub const S_UUID: usize = 0x68;
    pub const S_VOLUME_NAME: usize = 0x78;
    pub const S_RESERVED_GDT_BLOCKS: usize = 0xCE;
    pub const S_JOURNAL_INUM: usize = 0xE0;
    pub const S_DEF_HASH_VERSION: usize = 0xFC;
    pub const S_RESERVED_CHAR_PAD: usize = 0xFD;
    pub const S_DESC_SIZE: usize = 0xFE;
    pub const S_JNL_BLOCKS: usize = 0x10C;
    pub const S_BLOCKS_COUNT_HI: usize = 0x150;
    pub const S_MIN_EXTRA_ISIZE: usize = 0x15C;
    pub const S_WANT_EXTRA_ISIZE: usize = 0x15E;
    pub const S_FLAGS: usize = 0x160;
}

/// Byte offsets within `ext2_group_desc` (32 bytes).
pub mod bg_off {
    pub const BG_BLOCK_BITMAP: usize = 0x00;
    pub const BG_INODE_BITMAP: usize = 0x04;
    pub const BG_INODE_TABLE: usize = 0x08;
    pub const BG_FREE_BLOCKS_COUNT: usize = 0x0C;
    pub const BG_FREE_INODES_COUNT: usize = 0x0E;
    pub const BG_USED_DIRS_COUNT: usize = 0x10;
    pub const BG_FLAGS: usize = 0x12;
    pub const BG_CHECKSUM: usize = 0x1E;
}

/// Byte offsets within `ext4_inode`.
pub mod inode_off {
    pub const I_MODE: usize = 0x00;
    pub const I_UID: usize = 0x02;
    pub const I_SIZE_LO: usize = 0x04;
    pub const I_ATIME: usize = 0x08;
    pub const I_CTIME: usize = 0x0C;
    pub const I_MTIME: usize = 0x10;
    pub const I_GID: usize = 0x18;
    pub const I_LINKS_COUNT: usize = 0x1A;
    pub const I_BLOCKS_LO: usize = 0x1C;
    pub const I_FLAGS: usize = 0x20;
    pub const I_BLOCK: usize = 0x28;
    pub const I_FILE_ACL_LO: usize = 0x68;
    pub const I_SIZE_HIGH: usize = 0x6C;
    pub const L_I_BLOCKS_HIGH: usize = 0x74;
    pub const I_EXTRA_ISIZE: usize = 0x80;
}
