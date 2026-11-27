use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::Path;

pub const EXT4_SUPER_MAGIC: u16 = 0xEF53;
pub const EXT4_ROOT_INO: u32 = 2;

pub const COMPAT_HAS_JOURNAL: u32 = 0x0004;
pub const INCOMPAT_FILETYPE: u32 = 0x0002;
pub const INCOMPAT_EXTENTS: u32 = 0x0040;
pub const INCOMPAT_64BIT: u32 = 0x0080;
pub const INCOMPAT_FLEX_BG: u32 = 0x0200;
pub const RO_COMPAT_HUGE_FILE: u32 = 0x0008;
pub const RO_COMPAT_GDT_CSUM: u32 = 0x0010;
pub const RO_COMPAT_DIR_NLINK: u32 = 0x0020;
pub const RO_COMPAT_EXTRA_ISIZE: u32 = 0x0040;
pub const RO_COMPAT_SPARSE_SUPER2: u32 = 0x0200;
pub const RO_COMPAT_METADATA_CSUM: u32 = 0x0400;

pub const EXT4_INDEX_FL: u32 = 0x0000_1000;
pub const EXT4_EXTENTS_FL: u32 = 0x0008_0000;
pub const EXT4_EA_INODE_FL: u32 = 0x0020_0000;
pub const EXT4_INLINE_DATA_FL: u32 = 0x1000_0000;

pub const S_IFMT: u16 = 0xF000;
pub const S_IFIFO: u16 = 0x1000;
pub const S_IFCHR: u16 = 0x2000;
pub const S_IFDIR: u16 = 0x4000;
pub const S_IFBLK: u16 = 0x6000;
pub const S_IFREG: u16 = 0x8000;
pub const S_IFLNK: u16 = 0xA000;
pub const S_IFSOCK: u16 = 0xC000;

pub const S_IXOTH: u16 = 0x0001;
pub const S_IWOTH: u16 = 0x0002;
pub const S_IROTH: u16 = 0x0004;
pub const S_IXGRP: u16 = 0x0008;
pub const S_IWGRP: u16 = 0x0010;
pub const S_IRGRP: u16 = 0x0020;
pub const S_IXUSR: u16 = 0x0040;
pub const S_IWUSR: u16 = 0x0080;
pub const S_IRUSR: u16 = 0x0100;
pub const S_ISVTX: u16 = 0x0200;
pub const S_ISGID: u16 = 0x0400;
pub const S_ISUID: u16 = 0x0800;

pub const FT_UNKNOWN: u8 = 0;
pub const FT_REG_FILE: u8 = 1;
pub const FT_DIR: u8 = 2;
pub const FT_CHRDEV: u8 = 3;
pub const FT_BLKDEV: u8 = 4;
pub const FT_FIFO: u8 = 5;
pub const FT_SOCK: u8 = 6;
pub const FT_SYMLINK: u8 = 7;
pub const FT_CHECKSUM: u8 = 0xDE;

const SPARSE_HEADER_MAGIC: u32 = 0xED26_FF3A;
const CHUNK_TYPE_RAW: u16 = 0xCAC1;
const CHUNK_TYPE_FILL: u16 = 0xCAC2;
const CHUNK_TYPE_DONT_CARE: u16 = 0xCAC3;
const CHUNK_TYPE_CRC32: u16 = 0xCAC4;

#[inline]
pub fn u16_le(b: &[u8], off: usize) -> u16 {
    u16::from_le_bytes([b[off], b[off + 1]])
}

#[inline]
pub fn u32_le(b: &[u8], off: usize) -> u32 {
    u32::from_le_bytes([b[off], b[off + 1], b[off + 2], b[off + 3]])
}

#[derive(Clone, Debug)]
enum SparseChunkKind {
    Raw { file_offset: u64 },
    Fill([u8; 4]),
    Zero,
}

#[derive(Clone, Debug)]
pub struct SparseChunk {
    start: u64,
    end: u64,
    kind: SparseChunkKind,
}

pub trait ExtReadSeek: Read + Seek + Send {}
impl<T: Read + Seek + Send> ExtReadSeek for T {}

pub enum ImageReader {
    Raw(File),
    Memory(Vec<u8>),
    Custom(Box<dyn ExtReadSeek>),
    Sparse {
        file: File,
        chunks: Vec<SparseChunk>,
        total_len: u64,
    },
}

impl ImageReader {
    pub fn from_custom(reader: Box<dyn ExtReadSeek>) -> Self {
        Self::Custom(reader)
    }
    pub fn is_sparse_file(path: &Path) -> io::Result<bool> {
        let mut f = File::open(path)?;
        let mut magic4 = [0u8; 4];
        Ok(f.read_exact(&mut magic4).is_ok() && u32::from_le_bytes(magic4) == SPARSE_HEADER_MAGIC)
    }

    pub fn is_sparse(&self) -> bool {
        matches!(self, Self::Sparse { .. })
    }

    pub fn from_bytes(data: Vec<u8>) -> io::Result<Self> {
        if data.len() >= 28 && u32::from_le_bytes([data[0], data[1], data[2], data[3]]) == SPARSE_HEADER_MAGIC {
            // Unpack in-memory sparse payload if needed
            let mut cursor = io::Cursor::new(&data);
            let mut hdr = [0u8; 28];
            cursor.read_exact(&mut hdr)?;
            let major = u16_le(&hdr, 4);
            let file_hdr_sz = u16_le(&hdr, 8);
            let chunk_hdr_sz = u16_le(&hdr, 10);
            let blk_sz = u32_le(&hdr, 12) as usize;
            let total_blks = u32_le(&hdr, 16) as usize;
            let total_chunks = u32_le(&hdr, 20);
            if major == 1 && file_hdr_sz >= 28 && chunk_hdr_sz >= 12 {
                if file_hdr_sz > 28 {
                    cursor.seek(SeekFrom::Current((file_hdr_sz - 28) as i64))?;
                }
                let mut out = vec![0u8; total_blks.saturating_mul(blk_sz)];
                let mut out_off = 0usize;
                for _ in 0..total_chunks {
                    let mut chdr = [0u8; 12];
                    cursor.read_exact(&mut chdr)?;
                    if chunk_hdr_sz > 12 {
                        cursor.seek(SeekFrom::Current((chunk_hdr_sz - 12) as i64))?;
                    }
                    let ctype = u16_le(&chdr, 0);
                    let chunk_sz = u32_le(&chdr, 4) as usize;
                    let total_sz = u32_le(&chdr, 8) as usize;
                    let data_sz = total_sz.saturating_sub(chunk_hdr_sz as usize);
                    let byte_len = chunk_sz.saturating_mul(blk_sz);
                    match ctype {
                        CHUNK_TYPE_RAW => {
                            let take = byte_len.min(out.len().saturating_sub(out_off));
                            cursor.read_exact(&mut out[out_off..out_off + take])?;
                            if data_sz > take {
                                cursor.seek(SeekFrom::Current((data_sz - take) as i64))?;
                            }
                            out_off = out_off.saturating_add(byte_len);
                        }
                        CHUNK_TYPE_FILL => {
                            let mut val = [0u8; 4];
                            cursor.read_exact(&mut val)?;
                            let take = byte_len.min(out.len().saturating_sub(out_off));
                            for i in 0..take {
                                out[out_off + i] = val[i % 4];
                            }
                            if data_sz > 4 {
                                cursor.seek(SeekFrom::Current((data_sz - 4) as i64))?;
                            }
                            out_off = out_off.saturating_add(byte_len);
                        }
                        CHUNK_TYPE_DONT_CARE => {
                            if data_sz > 0 {
                                cursor.seek(SeekFrom::Current(data_sz as i64))?;
                            }
                            out_off = out_off.saturating_add(byte_len);
                        }
                        _ => {
                            if data_sz > 0 {
                                cursor.seek(SeekFrom::Current(data_sz as i64))?;
                            }
                        }
                    }
                }
                return Ok(Self::Memory(out));
            }
        }
        Ok(Self::Memory(data))
    }

    pub fn open(mut file: File) -> io::Result<Self> {
        let mut magic4 = [0u8; 4];
        file.seek(SeekFrom::Start(0))?;
        if file.read_exact(&mut magic4).is_ok() && u32::from_le_bytes(magic4) == SPARSE_HEADER_MAGIC
        {
            file.seek(SeekFrom::Start(0))?;
            let mut hdr = [0u8; 28];
            file.read_exact(&mut hdr)?;
            let major = u16_le(&hdr, 4);
            let file_hdr_sz = u16_le(&hdr, 8);
            let chunk_hdr_sz = u16_le(&hdr, 10);
            let blk_sz = u32_le(&hdr, 12) as u64;
            let total_blks = u32_le(&hdr, 16) as u64;
            let total_chunks = u32_le(&hdr, 20);

            if major == 1 && file_hdr_sz >= 28 && chunk_hdr_sz >= 12 {
                if file_hdr_sz > 28 {
                    file.seek(SeekFrom::Current((file_hdr_sz - 28) as i64))?;
                }
                let mut chunks = Vec::with_capacity(total_chunks as usize);
                let mut out_offset = 0u64;
                for _ in 0..total_chunks {
                    let mut chdr = [0u8; 12];
                    file.read_exact(&mut chdr)?;
                    if chunk_hdr_sz > 12 {
                        file.seek(SeekFrom::Current((chunk_hdr_sz - 12) as i64))?;
                    }
                    let ctype = u16_le(&chdr, 0);
                    let chunk_sz = u32_le(&chdr, 4) as u64;
                    let total_sz = u32_le(&chdr, 8) as u64;
                    let data_sz = total_sz.saturating_sub(chunk_hdr_sz as u64);
                    let data_off = file.stream_position()?;

                    match ctype {
                        CHUNK_TYPE_RAW => {
                            let byte_len = chunk_sz * blk_sz;
                            chunks.push(SparseChunk {
                                start: out_offset,
                                end: out_offset + byte_len,
                                kind: SparseChunkKind::Raw {
                                    file_offset: data_off,
                                },
                            });
                            file.seek(SeekFrom::Current(data_sz as i64))?;
                            out_offset += byte_len;
                        }
                        CHUNK_TYPE_FILL => {
                            let mut val = [0u8; 4];
                            file.read_exact(&mut val)?;
                            let byte_len = chunk_sz * blk_sz;
                            chunks.push(SparseChunk {
                                start: out_offset,
                                end: out_offset + byte_len,
                                kind: SparseChunkKind::Fill(val),
                            });
                            if data_sz > 4 {
                                file.seek(SeekFrom::Current((data_sz - 4) as i64))?;
                            }
                            out_offset += byte_len;
                        }
                        CHUNK_TYPE_DONT_CARE => {
                            let byte_len = chunk_sz * blk_sz;
                            chunks.push(SparseChunk {
                                start: out_offset,
                                end: out_offset + byte_len,
                                kind: SparseChunkKind::Zero,
                            });
                            if data_sz > 0 {
                                file.seek(SeekFrom::Current(data_sz as i64))?;
                            }
                            out_offset += byte_len;
                        }
                        CHUNK_TYPE_CRC32 => {
                            if data_sz > 0 {
                                file.seek(SeekFrom::Current(data_sz as i64))?;
                            }
                        }
                        _ => {
                            if data_sz > 0 {
                                file.seek(SeekFrom::Current(data_sz as i64))?;
                            }
                        }
                    }
                }
                return Ok(Self::Sparse {
                    file,
                    chunks,
                    total_len: total_blks * blk_sz,
                });
            }
        }
        Ok(Self::Raw(file))
    }

    pub fn read_at(&mut self, offset: u64, buf: &mut [u8]) -> io::Result<()> {
        match self {
            Self::Raw(f) => {
                f.seek(SeekFrom::Start(offset))?;
                f.read_exact(buf)
            }
            Self::Custom(r) => {
                r.seek(SeekFrom::Start(offset))?;
                r.read_exact(buf)
            }
            Self::Memory(data) => {
                let start = offset as usize;
                let end = start.checked_add(buf.len()).ok_or_else(|| {
                    io::Error::new(io::ErrorKind::UnexpectedEof, "offset overflow")
                })?;
                if end > data.len() {
                    return Err(io::Error::new(
                        io::ErrorKind::UnexpectedEof,
                        "read past end of memory image",
                    ));
                }
                buf.copy_from_slice(&data[start..end]);
                Ok(())
            }
            Self::Sparse {
                file,
                chunks,
                total_len,
            } => {
                if offset + buf.len() as u64 > *total_len {
                    return Err(io::Error::new(
                        io::ErrorKind::UnexpectedEof,
                        "read past end of sparse image",
                    ));
                }
                let mut pos = offset;
                let mut dst_off = 0usize;
                let mut rem = buf.len();
                while rem > 0 {
                    let idx = chunks.partition_point(|c| c.end <= pos);
                    if idx >= chunks.len() || chunks[idx].start > pos {
                        let next_start = chunks.get(idx).map(|c| c.start).unwrap_or(*total_len);
                        let take = (next_start - pos).min(rem as u64) as usize;
                        buf[dst_off..dst_off + take].fill(0);
                        pos += take as u64;
                        dst_off += take;
                        rem -= take;
                        continue;
                    }
                    let chunk = &chunks[idx];
                    let take = (chunk.end - pos).min(rem as u64) as usize;
                    let within = pos - chunk.start;
                    match chunk.kind {
                        SparseChunkKind::Raw { file_offset } => {
                            file.seek(SeekFrom::Start(file_offset + within))?;
                            file.read_exact(&mut buf[dst_off..dst_off + take])?;
                        }
                        SparseChunkKind::Fill(val) => {
                            let phase = (within % 4) as usize;
                            for i in 0..take {
                                buf[dst_off + i] = val[(phase + i) % 4];
                            }
                        }
                        SparseChunkKind::Zero => {
                            buf[dst_off..dst_off + take].fill(0);
                        }
                    }
                    pos += take as u64;
                    dst_off += take;
                    rem -= take;
                }
                Ok(())
            }
        }
    }

    pub fn read_vec(&mut self, offset: u64, len: usize) -> io::Result<Vec<u8>> {
        let mut buf = vec![0u8; len];
        self.read_at(offset, &mut buf)?;
        Ok(buf)
    }
}

#[derive(Clone, Debug)]
pub struct Superblock {
    pub s_inodes_count: u32,
    pub s_blocks_count: u64,
    pub s_free_blocks_count: u64,
    pub s_free_inodes_count: u32,
    pub s_first_data_block: u32,
    pub s_log_block_size: u32,
    pub s_blocks_per_group: u32,
    pub s_inodes_per_group: u32,
    pub s_magic: u16,
    pub s_rev_level: u32,
    pub s_inode_size: u16,
    pub s_feature_compat: u32,
    pub s_feature_incompat: u32,
    pub s_feature_ro_compat: u32,
    pub s_uuid: [u8; 16],
    pub s_volume_name: [u8; 16],
    pub s_last_mounted: [u8; 64],
    pub s_desc_size: u16,
}

impl Superblock {
    pub fn parse(raw: &[u8; 1024]) -> Self {
        let s_feature_incompat = u32_le(raw, 0x60);
        let is_64bit = (s_feature_incompat & INCOMPAT_64BIT) != 0;
        let s_rev_level = u32_le(raw, 0x4C);

        let mut s_inode_size = u16_le(raw, 0x58);
        if s_rev_level == 0 || s_inode_size == 0 {
            s_inode_size = 128;
        }

        let mut s_desc_size = u16_le(raw, 0xFE);
        if s_desc_size == 0 {
            s_desc_size = if is_64bit { 64 } else { 32 };
        }

        let blocks_lo = u32_le(raw, 0x04) as u64;
        let blocks_hi = if is_64bit { u32_le(raw, 0x150) as u64 } else { 0 };
        let free_lo = u32_le(raw, 0x0C) as u64;
        let free_hi = if is_64bit { u32_le(raw, 0x158) as u64 } else { 0 };

        let mut s_uuid = [0u8; 16];
        s_uuid.copy_from_slice(&raw[0x68..0x78]);
        let mut s_volume_name = [0u8; 16];
        s_volume_name.copy_from_slice(&raw[0x78..0x88]);
        let mut s_last_mounted = [0u8; 64];
        s_last_mounted.copy_from_slice(&raw[0x88..0xC8]);

        Self {
            s_inodes_count: u32_le(raw, 0x00),
            s_blocks_count: (blocks_hi << 32) | blocks_lo,
            s_free_blocks_count: (free_hi << 32) | free_lo,
            s_free_inodes_count: u32_le(raw, 0x10),
            s_first_data_block: u32_le(raw, 0x14),
            s_log_block_size: u32_le(raw, 0x18),
            s_blocks_per_group: u32_le(raw, 0x20),
            s_inodes_per_group: u32_le(raw, 0x28),
            s_magic: u16_le(raw, 0x38),
            s_rev_level,
            s_inode_size,
            s_feature_compat: u32_le(raw, 0x5C),
            s_feature_incompat,
            s_feature_ro_compat: u32_le(raw, 0x64),
            s_uuid,
            s_volume_name,
            s_last_mounted,
            s_desc_size,
        }
    }

    pub fn block_size(&self) -> u64 {
        1024u64 << self.s_log_block_size
    }

    pub fn is_64bit(&self) -> bool {
        (self.s_feature_incompat & INCOMPAT_64BIT) != 0
    }

    pub fn has_filetype(&self) -> bool {
        (self.s_feature_incompat & INCOMPAT_FILETYPE) != 0
    }

    pub fn uuid_string(&self) -> String {
        let u = &self.s_uuid;
        format!(
            "{:02X}{:02X}{:02X}{:02X}-{:02X}{:02X}-{:02X}{:02X}-{:02X}{:02X}-{:02X}{:02X}{:02X}{:02X}{:02X}{:02X}",
            u[0], u[1], u[2], u[3], u[4], u[5], u[6], u[7],
            u[8], u[9], u[10], u[11], u[12], u[13], u[14], u[15]
        )
    }

    pub fn volume_name(&self) -> String {
        let end = self.s_volume_name.iter().position(|&b| b == 0).unwrap_or(16);
        String::from_utf8_lossy(&self.s_volume_name[..end]).into_owned()
    }

    pub fn last_mounted(&self) -> String {
        let end = self.s_last_mounted.iter().position(|&b| b == 0).unwrap_or(64);
        String::from_utf8_lossy(&self.s_last_mounted[..end]).into_owned()
    }
}

#[derive(Clone, Debug)]
pub struct GroupDescriptor {
    pub bg_inode_table: u64,
}

#[derive(Clone, Debug)]
pub struct InodeData {
    pub inode_idx: u32,
    pub offset: u64,
    pub file_type: u8,
    pub i_mode: u16,
    pub i_uid: u32,
    pub i_gid: u32,
    pub i_size: u64,
    pub i_blocks_lo: u32,
    pub i_flags: u32,
    pub i_block: [u32; 15],
    pub i_block_raw: [u8; 60],
    pub i_file_acl: u64,
    pub i_extra_isize: u16,
    pub raw: Vec<u8>,
}

#[derive(Clone, Debug)]
pub struct DirEntry {
    pub name: String,
    pub inode: u32,
    pub file_type: u8,
}

#[derive(Clone, Debug)]
pub struct ExtentRun {
    pub file_block: u64,
    pub disk_block: u64,
    pub len: u32,
    pub unwritten: bool,
}

#[derive(Clone, Debug)]
pub struct ExtFileNode {
    pub path: String,
    pub ino: u32,
    pub size: u64,
    pub mode: u16,
    pub perm: String,
    pub is_dir: bool,
    pub is_link: bool,
    pub target: Option<String>,
    pub selinux: Option<String>,
}

#[derive(Clone, Debug)]
pub struct ExtChildEntry {
    pub ino: u32,
    pub name: String,
    pub is_dir: bool,
    pub is_link: bool,
    pub size: u64,
}

#[derive(Clone, Debug)]
pub struct ExtUsageReport {
    pub fs: String,
    pub block_size: u64,
    pub file_size: u64,
    pub total_blocks: u64,
    pub meta_bytes: u64,
    pub used_blocks: u64,
    pub used_bytes: u64,
    pub wasted_blocks: u64,
    pub wasted_bytes: u64,
    pub free_blocks: u64,
    pub free_bytes: u64,
    pub logical_bytes: u64,
    pub reclaimable_bytes: u64,
    pub files: usize,
    pub dirs: usize,
    pub links: usize,
}

pub fn mode_to_perm(mode: u16) -> String {
    let tc = match mode & S_IFMT {
        S_IFSOCK => 's',
        S_IFLNK => 'l',
        S_IFREG => '-',
        S_IFBLK => 'b',
        S_IFDIR => 'd',
        S_IFCHR => 'c',
        S_IFIFO => 'p',
        _ => '?',
    };
    let rwx = |bits: u16, special: bool, lo: char, up: char| -> String {
        let r = if (bits & 0o4) != 0 { 'r' } else { '-' };
        let w = if (bits & 0o2) != 0 { 'w' } else { '-' };
        let x = if special {
            if (bits & 0o1) == 0 { up } else { lo }
        } else if (bits & 0o1) != 0 {
            'x'
        } else {
            '-'
        };
        format!("{r}{w}{x}")
    };
    let u = rwx((mode >> 6) & 7, (mode & S_ISUID) != 0, 's', 'S');
    let g = rwx((mode >> 3) & 7, (mode & S_ISGID) != 0, 's', 'S');
    let o = rwx(mode & 7, (mode & S_ISVTX) != 0, 't', 'T');
    format!("{tc}{u}{g}{o}")
}

pub struct Volume {
    pub reader: ImageReader,
    pub superblock: Superblock,
    pub block_size: u64,
    pub group_descriptors: Vec<GroupDescriptor>,
    indirect_cache: HashMap<u64, Vec<u8>>,
}

impl Volume {
    pub fn open(file: File) -> io::Result<Self> {
        let reader = ImageReader::open(file)?;
        Self::open_with_reader(reader)
    }

    pub fn open_bytes(data: Vec<u8>) -> io::Result<Self> {
        let reader = ImageReader::from_bytes(data)?;
        Self::open_with_reader(reader)
    }

    pub fn open_custom(reader: Box<dyn ExtReadSeek>) -> io::Result<Self> {
        Self::open_with_reader(ImageReader::from_custom(reader))
    }

    pub fn open_with_reader(mut reader: ImageReader) -> io::Result<Self> {
        let mut sb_raw = [0u8; 1024];
        reader.read_at(0x400, &mut sb_raw)?;
        let sb = Superblock::parse(&sb_raw);
        if sb.s_magic != EXT4_SUPER_MAGIC {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!(
                    "Invalid magic value in superblock: 0x{:04X} (expected 0xEF53)",
                    sb.s_magic
                ),
            ));
        }

        let block_size = sb.block_size();
        let ino_groups = if sb.s_inodes_per_group > 0 {
            sb.s_inodes_count.div_ceil(sb.s_inodes_per_group) as usize
        } else {
            1
        };
        let blk_groups = if sb.s_blocks_per_group > 0 && sb.s_blocks_count > sb.s_first_data_block as u64 {
            ((sb.s_blocks_count - sb.s_first_data_block as u64)
                .div_ceil(sb.s_blocks_per_group as u64)) as usize
        } else {
            1
        };
        let num_groups = ino_groups.max(blk_groups).max(1);

        let gdt_offset = (0x400 / block_size + 1) * block_size;
        let desc_size = sb.s_desc_size as usize;
        let gdt_bytes = reader.read_vec(gdt_offset, num_groups * desc_size)?;

        let is_64 = sb.is_64bit() && desc_size >= 64;
        let mut group_descriptors = Vec::with_capacity(num_groups);
        for i in 0..num_groups {
            let off = i * desc_size;
            let lo = u32_le(&gdt_bytes, off + 0x08) as u64;
            let hi = if is_64 {
                u32_le(&gdt_bytes, off + 0x28) as u64
            } else {
                0
            };
            group_descriptors.push(GroupDescriptor {
                bg_inode_table: (hi << 32) | lo,
            });
        }

        Ok(Self {
            reader,
            superblock: sb,
            block_size,
            group_descriptors,
            indirect_cache: HashMap::new(),
        })
    }

    pub fn is_sparse(&self) -> bool {
        self.reader.is_sparse()
    }

    pub fn read_inode(&mut self, inode_idx: u32, mut file_type: u8) -> io::Result<InodeData> {
        let ipg = self.superblock.s_inodes_per_group;
        if inode_idx == 0 || ipg == 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("Invalid inode number {} (inodes_per_group={})", inode_idx, ipg),
            ));
        }
        let group_idx = ((inode_idx - 1) / ipg) as usize;
        let entry_idx = ((inode_idx - 1) % ipg) as u64;

        let inode_table_block = self
            .group_descriptors
            .get(group_idx)
            .map(|g| g.bg_inode_table)
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!(
                        "Inode {} maps to out-of-range block group {} (groups={})",
                        inode_idx,
                        group_idx,
                        self.group_descriptors.len()
                    ),
                )
            })?;
        let inode_size = self.superblock.s_inode_size as usize;
        let offset = inode_table_block * self.block_size + entry_idx * inode_size as u64;
        let raw = self.reader.read_vec(offset, inode_size)?;

        let i_mode = u16_le(&raw, 0x00);
        if !self.superblock.has_filetype() || file_type == FT_UNKNOWN {
            file_type = match i_mode & S_IFMT {
                S_IFDIR => FT_DIR,
                S_IFREG => FT_REG_FILE,
                S_IFLNK => FT_SYMLINK,
                S_IFCHR => FT_CHRDEV,
                S_IFBLK => FT_BLKDEV,
                S_IFIFO => FT_FIFO,
                S_IFSOCK => FT_SOCK,
                _ => FT_UNKNOWN,
            };
        }

        let i_size_lo = u32_le(&raw, 0x04) as u64;
        let i_size_hi = if (i_mode & S_IFMT) == S_IFREG {
            u32_le(&raw, 0x6C) as u64
        } else {
            0
        };
        let i_size = (i_size_hi << 32) | i_size_lo;

        let uid_lo = u32_le_u16(&raw, 0x02);
        let gid_lo = u32_le_u16(&raw, 0x18);
        let (uid_hi, gid_hi, acl_hi) = if raw.len() >= 128 {
            let uh = u32_le_u16(&raw, 0x78);
            let gh = u32_le_u16(&raw, 0x7A);
            let ah = if self.superblock.is_64bit() {
                u16_le(&raw, 0x76) as u64
            } else {
                0
            };
            (uh, gh, ah)
        } else {
            (0, 0, 0)
        };
        let extra_isize = if raw.len() >= 130 {
            u16_le(&raw, 0x80)
        } else {
            0
        };

        let mut i_block = [0u32; 15];
        for i in 0..15 {
            i_block[i] = u32_le(&raw, 0x28 + i * 4);
        }
        let mut i_block_raw = [0u8; 60];
        i_block_raw.copy_from_slice(&raw[0x28..0x64]);

        let acl_lo = u32_le(&raw, 0x68) as u64;

        Ok(InodeData {
            inode_idx,
            offset,
            file_type,
            i_mode,
            i_uid: (uid_hi << 16) | uid_lo,
            i_gid: (gid_hi << 16) | gid_lo,
            i_size,
            i_blocks_lo: u32_le(&raw, 0x1C),
            i_flags: u32_le(&raw, 0x20),
            i_block,
            i_block_raw,
            i_file_acl: (acl_hi << 32) | acl_lo,
            i_extra_isize: extra_isize,
            raw,
        })
    }

    fn read_block_cached(&mut self, block_idx: u64) -> io::Result<&[u8]> {
        if !self.indirect_cache.contains_key(&block_idx) {
            let data = self
                .reader
                .read_vec(block_idx * self.block_size, self.block_size as usize)?;
            self.indirect_cache.insert(block_idx, data);
        }
        Ok(self.indirect_cache.get(&block_idx).unwrap())
    }

    pub fn collect_extents(&mut self, inode: &InodeData) -> io::Result<Vec<ExtentRun>> {
        let mut runs = Vec::new();
        let mut queue = vec![inode.offset + 0x28];

        while let Some(hdr_off) = queue.pop() {
            let hdr = self.reader.read_vec(hdr_off, 12)?;
            let magic = u16_le(&hdr, 0);
            if magic != 0xF30A {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!(
                        "Invalid extent magic 0x{:04X} at inode {}",
                        magic, inode.inode_idx
                    ),
                ));
            }
            let entries = u16_le(&hdr, 2) as usize;
            let depth = u16_le(&hdr, 6);
            if depth != 0 {
                let idx_bytes = self.reader.read_vec(hdr_off + 12, entries * 12)?;
                for i in (0..entries).rev() {
                    let b = &idx_bytes[i * 12..i * 12 + 12];
                    let leaf_lo = u32_le(b, 4) as u64;
                    let leaf_hi = u16_le(b, 8) as u64;
                    let leaf = (leaf_hi << 32) | leaf_lo;
                    queue.push(leaf * self.block_size);
                }
            } else {
                let ext_bytes = self.reader.read_vec(hdr_off + 12, entries * 12)?;
                for i in 0..entries {
                    let b = &ext_bytes[i * 12..i * 12 + 12];
                    let ee_block = u32_le(b, 0) as u64;
                    let raw_len = u16_le(b, 4);
                    let unwritten = raw_len > 32768;
                    let ee_len = if unwritten {
                        (raw_len & 0x7FFF) as u32
                    } else {
                        raw_len as u32
                    };
                    let start_hi = u16_le(b, 6) as u64;
                    let start_lo = u32_le(b, 8) as u64;
                    let ee_start = (start_hi << 32) | start_lo;
                    runs.push(ExtentRun {
                        file_block: ee_block,
                        disk_block: ee_start,
                        len: ee_len,
                        unwritten,
                    });
                }
            }
        }

        runs.sort_by_key(|r| r.file_block);
        Ok(runs)
    }

    pub fn resolve_indirect_block(&mut self, inode: &InodeData, mut file_block: u64) -> io::Result<u64> {
        let ptrs_per_blk = self.block_size / 4;
        if file_block < 12 {
            return Ok(inode.i_block[file_block as usize] as u64);
        }
        file_block -= 12;

        if file_block < ptrs_per_blk {
            let ind = inode.i_block[12] as u64;
            if ind == 0 {
                return Ok(0);
            }
            let blk = self.read_block_cached(ind)?;
            return Ok(u32_le(blk, (file_block as usize) * 4) as u64);
        }
        file_block -= ptrs_per_blk;

        let dind_cap = ptrs_per_blk * ptrs_per_blk;
        if file_block < dind_cap {
            let dind = inode.i_block[13] as u64;
            if dind == 0 {
                return Ok(0);
            }
            let d_idx = (file_block / ptrs_per_blk) as usize;
            let i_idx = (file_block % ptrs_per_blk) as usize;
            let ind = {
                let d_blk = self.read_block_cached(dind)?;
                u32_le(d_blk, d_idx * 4) as u64
            };
            if ind == 0 {
                return Ok(0);
            }
            let i_blk = self.read_block_cached(ind)?;
            return Ok(u32_le(i_blk, i_idx * 4) as u64);
        }
        file_block -= dind_cap;

        let tind = inode.i_block[14] as u64;
        if tind == 0 {
            return Ok(0);
        }
        let t_idx = (file_block / dind_cap) as usize;
        let rem = file_block % dind_cap;
        let d_idx = (rem / ptrs_per_blk) as usize;
        let i_idx = (rem % ptrs_per_blk) as usize;

        let dind = {
            let t_blk = self.read_block_cached(tind)?;
            u32_le(t_blk, t_idx * 4) as u64
        };
        if dind == 0 {
            return Ok(0);
        }
        let ind = {
            let d_blk = self.read_block_cached(dind)?;
            u32_le(d_blk, d_idx * 4) as u64
        };
        if ind == 0 {
            return Ok(0);
        }
        let i_blk = self.read_block_cached(ind)?;
        Ok(u32_le(i_blk, i_idx * 4) as u64)
    }

    pub fn read_inode_bytes(&mut self, inode: &InodeData) -> io::Result<Vec<u8>> {
        let mut out = Vec::with_capacity(inode.i_size as usize);
        self.stream_inode_to(inode, &mut out)?;
        Ok(out)
    }

    pub fn stream_inode_to(&mut self, inode: &InodeData, writer: &mut dyn Write) -> io::Result<()> {
        let size = inode.i_size;
        if size == 0 {
            return Ok(());
        }

        // Fast symlink: inline in i_block when size <= 60 and any allocated blocks belong solely to an external EA block
        let ea_blocks = if inode.i_file_acl != 0 {
            (self.block_size / 512) as u32
        } else {
            0
        };
        let is_fast_symlink = inode.is_symlink()
            && (inode.i_flags & (EXT4_EXTENTS_FL | EXT4_INLINE_DATA_FL)) == 0
            && size <= 60
            && inode.i_blocks_lo <= ea_blocks;
        if is_fast_symlink {
            writer.write_all(&inode.i_block_raw[..size as usize])?;
            return Ok(());
        }

        if (inode.i_flags & EXT4_INLINE_DATA_FL) != 0 {
            let inline_take = (size as usize).min(60);
            writer.write_all(&inode.i_block_raw[..inline_take])?;
            if size > 60 {
                for (name, val) in self.read_xattrs(inode)? {
                    if name == "system.data" {
                        let extra = ((size - 60) as usize).min(val.len());
                        writer.write_all(&val[..extra])?;
                        break;
                    }
                }
            }
            return Ok(());
        }

        let bs = self.block_size;
        let total_blocks = (size + bs - 1) / bs;
        let mut buf = vec![0u8; (1024 * 1024).max(bs as usize)];
        let zeros = vec![0u8; 64 * 1024];

        if (inode.i_flags & EXT4_EXTENTS_FL) != 0 {
            let runs = self.collect_extents(inode)?;
            let mut cur_block = 0u64;
            let mut written = 0u64;

            for run in runs {
                if cur_block >= total_blocks {
                    break;
                }
                if run.file_block > cur_block {
                    let gap_blocks = (run.file_block - cur_block).min(total_blocks - cur_block);
                    let gap_bytes = (gap_blocks * bs).min(size - written);
                    write_zeros(writer, gap_bytes, &zeros)?;
                    written += gap_bytes;
                    cur_block += gap_blocks;
                }
                if run.len == 0 || cur_block >= total_blocks {
                    continue;
                }
                let skip = cur_block.saturating_sub(run.file_block);
                let run_rem = (run.len as u64).saturating_sub(skip);
                let take_blocks = run_rem.min(total_blocks - cur_block);
                if take_blocks == 0 {
                    continue;
                }
                let byte_len = (take_blocks * bs).min(size - written);
                if run.unwritten || run.disk_block == 0 {
                    write_zeros(writer, byte_len, &zeros)?;
                } else {
                    let mut src_off = (run.disk_block + skip) * bs;
                    let mut rem = byte_len as usize;
                    while rem > 0 {
                        let chunk = rem.min(buf.len());
                        self.reader.read_at(src_off, &mut buf[..chunk])?;
                        writer.write_all(&buf[..chunk])?;
                        src_off += chunk as u64;
                        rem -= chunk;
                    }
                }
                written += byte_len;
                cur_block += take_blocks;
            }

            if written < size {
                write_zeros(writer, size - written, &zeros)?;
            }
            return Ok(());
        }

        // EXT2 / EXT3 direct + indirect block map
        let mut written = 0u64;
        let mut fb = 0u64;
        while fb < total_blocks {
            let disk_blk = self.resolve_indirect_block(inode, fb)?;
            if disk_blk == 0 {
                let chunk_bytes = bs.min(size - written);
                write_zeros(writer, chunk_bytes, &zeros)?;
                written += chunk_bytes;
                fb += 1;
            } else {
                // Batch contiguous disk blocks for throughput
                let start_disk = disk_blk;
                let mut count = 1u64;
                let max_batch_blocks = (buf.len() as u64 / bs).max(1);
                while fb + count < total_blocks && count < max_batch_blocks {
                    let next_blk = self.resolve_indirect_block(inode, fb + count)?;
                    if next_blk == start_disk + count {
                        count += 1;
                    } else {
                        break;
                    }
                }
                let byte_len = (count * bs).min(size - written) as usize;
                self.reader.read_at(start_disk * bs, &mut buf[..byte_len])?;
                writer.write_all(&buf[..byte_len])?;
                written += byte_len as u64;
                fb += count;
            }
        }

        Ok(())
    }

    pub fn read_dir(&mut self, inode: &InodeData) -> io::Result<Vec<DirEntry>> {
        let raw_data = self.read_inode_bytes(inode)?;
        let mut entries = Vec::new();
        let mut offset = 0usize;

        while offset + 8 <= raw_data.len() {
            let d_inode = u32_le(&raw_data, offset);
            let rec_len = u16_le(&raw_data, offset + 4) as usize;
            let name_len = raw_data[offset + 6] as usize;
            let file_type = raw_data[offset + 7];

            if rec_len < 8 || offset + rec_len > raw_data.len() {
                break;
            }
            if d_inode != 0 && file_type != FT_CHECKSUM && offset + 8 + name_len <= raw_data.len() {
                let name = String::from_utf8_lossy(&raw_data[offset + 8..offset + 8 + name_len])
                    .into_owned();
                entries.push(DirEntry {
                    name,
                    inode: d_inode,
                    file_type,
                });
            }
            offset += rec_len;
        }
        Ok(entries)
    }

    pub fn read_xattrs(&mut self, inode: &InodeData) -> io::Result<Vec<(String, Vec<u8>)>> {
        let mut out = Vec::new();
        let inode_size = self.superblock.s_inode_size as usize;
        let inline_start = 128usize + inode.i_extra_isize as usize;

        if inline_start + 4 < inode_size && inline_start + 4 <= inode.raw.len() {
            let inline_slice = &inode.raw[inline_start..];
            if u32_le(inline_slice, 0) == 0xEA02_0000 {
                let entries_slice = &inline_slice[4..];
                let _ = self.parse_xattr_entries(entries_slice, entries_slice, 0, &mut out);
            }
        }

        if inode.i_file_acl != 0 {
            let blk = self
                .reader
                .read_vec(inode.i_file_acl * self.block_size, self.block_size as usize)?;
            let magic = u32_le(&blk, 0);
            let h_blocks = u32_le(&blk, 8);
            if magic == 0xEA02_0000 && h_blocks == 1 {
                self.parse_xattr_entries(&blk[32..], &blk, 0, &mut out)?;
            }
        }

        Ok(out)
    }

    fn parse_xattr_entries(
        &mut self,
        entries: &[u8],
        val_base: &[u8],
        base_offset: usize,
        out: &mut Vec<(String, Vec<u8>)>,
    ) -> io::Result<()> {
        let mut i = 0usize;
        while i + 16 <= entries.len() {
            let e_name_len = entries[i] as usize;
            let e_name_index = entries[i + 1];
            let e_value_offs = u16_le(entries, i + 2) as usize;
            let e_value_inum = u32_le(entries, i + 4);
            let e_value_size = u32_le(entries, i + 8) as usize;

            if (e_name_len as u32) | (e_name_index as u32) | (e_value_offs as u32) | e_value_inum == 0 {
                break;
            }

            let prefix: std::borrow::Cow<'static, str> = match e_name_index {
                0 => std::borrow::Cow::Borrowed(""),
                1 => std::borrow::Cow::Borrowed("user."),
                2 => std::borrow::Cow::Borrowed("system.posix_acl_access"),
                3 => std::borrow::Cow::Borrowed("system.posix_acl_default"),
                4 => std::borrow::Cow::Borrowed("trusted."),
                5 => std::borrow::Cow::Borrowed("lustre."),
                6 => std::borrow::Cow::Borrowed("security."),
                7 => std::borrow::Cow::Borrowed("system."),
                8 => std::borrow::Cow::Borrowed("system.richacl"),
                other => std::borrow::Cow::Owned(format!("xattr_{}.", other)),
            };

            if i + 16 + e_name_len > entries.len() {
                break;
            }
            let name_bytes = &entries[i + 16..i + 16 + e_name_len];
            let name_suffix: String = name_bytes.iter().map(|&b| b as char).collect();
            let full_name = format!("{}{}", prefix, name_suffix);

            let val = if e_value_inum != 0 {
                let ea_inode = self.read_inode(e_value_inum, FT_REG_FILE)?;
                if (ea_inode.i_flags & EXT4_EA_INODE_FL) == 0 {
                    // Continue gracefully if flag not set on non-standard images
                }
                self.read_inode_bytes(&ea_inode)?
            } else {
                let start = base_offset + e_value_offs;
                let end = start + e_value_size;
                if end <= val_base.len() {
                    val_base[start..end].to_vec()
                } else {
                    Vec::new()
                }
            };

            out.push((full_name, val));
            i += 4 * ((16 + e_name_len + 3) / 4);
        }
        Ok(())
    }

    pub fn inode_selinux(&mut self, inode: &InodeData) -> Option<String> {
        let xattrs = self.read_xattrs(inode).ok()?;
        for (k, v) in xattrs {
            if k == "security.selinux" {
                let end = v.iter().position(|&b| b == 0).unwrap_or(v.len());
                return Some(String::from_utf8_lossy(&v[..end]).into_owned());
            }
        }
        None
    }

    pub fn build_node(&mut self, path: String, inode: &InodeData) -> io::Result<ExtFileNode> {
        let is_dir = inode.is_dir();
        let is_link = inode.is_symlink();
        let target = if is_link {
            let raw = self.read_inode_bytes(inode)?;
            Some(String::from_utf8_lossy(&raw).into_owned())
        } else {
            None
        };
        let selinux = self.inode_selinux(inode);
        Ok(ExtFileNode {
            path,
            ino: inode.inode_idx,
            size: inode.i_size,
            mode: inode.i_mode,
            perm: mode_to_perm(inode.i_mode),
            is_dir,
            is_link,
            target,
            selinux,
        })
    }

    pub fn read_root_node(&mut self) -> io::Result<ExtFileNode> {
        let inode = self.read_inode(EXT4_ROOT_INO, FT_DIR)?;
        self.build_node(String::new(), &inode)
    }

    pub fn stat_ino(&mut self, ino: u32) -> io::Result<ExtFileNode> {
        let inode = self.read_inode(ino, 0)?;
        let path = if ino == EXT4_ROOT_INO {
            String::new()
        } else {
            format!("#{ino}")
        };
        self.build_node(path, &inode)
    }

    pub fn walk_nodes(&mut self) -> io::Result<Vec<ExtFileNode>> {
        let mut nodes = Vec::new();
        let mut visited = HashSet::new();
        self.walk_recursive(EXT4_ROOT_INO, "", &mut visited, &mut nodes)?;
        Ok(nodes)
    }

    fn walk_recursive(
        &mut self,
        dir_ino: u32,
        prefix: &str,
        visited: &mut HashSet<u32>,
        out: &mut Vec<ExtFileNode>,
    ) -> io::Result<()> {
        if !visited.insert(dir_ino) {
            return Ok(());
        }
        let dir_inode = self.read_inode(dir_ino, FT_DIR)?;
        let entries = self.read_dir(&dir_inode)?;
        for entry in entries {
            if entry.name == "." || entry.name == ".." {
                continue;
            }
            if entry.inode == 0 || entry.inode > self.superblock.s_inodes_count {
                continue;
            }
            let child_path = if prefix.is_empty() {
                entry.name.clone()
            } else {
                format!("{prefix}/{}", entry.name)
            };
            let child_inode = self.read_inode(entry.inode, entry.file_type)?;
            let node = self.build_node(child_path.clone(), &child_inode)?;
            let is_dir = node.is_dir;
            out.push(node);
            if is_dir {
                self.walk_recursive(entry.inode, &child_path, visited, out)?;
            }
        }
        Ok(())
    }

    pub fn children_of(&mut self, dir_ino: u32) -> io::Result<Vec<ExtChildEntry>> {
        let dir_inode = self.read_inode(dir_ino, FT_DIR)?;
        let entries = self.read_dir(&dir_inode)?;
        let mut out = Vec::with_capacity(entries.len());
        for entry in entries {
            if entry.name == "." || entry.name == ".." {
                continue;
            }
            if entry.inode == 0 || entry.inode > self.superblock.s_inodes_count {
                continue;
            }
            let child = self.read_inode(entry.inode, entry.file_type)?;
            out.push(ExtChildEntry {
                ino: entry.inode,
                name: entry.name,
                is_dir: child.is_dir(),
                is_link: child.is_symlink(),
                size: child.i_size,
            });
        }
        Ok(out)
    }

    pub fn lookup_path(&mut self, internal_path: &str) -> io::Result<ExtFileNode> {
        let norm = internal_path.trim_matches('/').replace('\\', "/");
        if norm.is_empty() {
            return self.read_root_node();
        }
        let mut cur_ino = EXT4_ROOT_INO;
        let parts: Vec<&str> = norm.split('/').filter(|p| !p.is_empty()).collect();
        for (idx, part) in parts.iter().enumerate() {
            let dir_inode = self.read_inode(cur_ino, FT_DIR)?;
            if !dir_inode.is_dir() {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    format!("Path not found: {norm}"),
                ));
            }
            let entries = self.read_dir(&dir_inode)?;
            let found = entries.into_iter().find(|e| e.name == *part);
            let Some(entry) = found else {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    format!("Path not found: {norm}"),
                ));
            };
            if idx + 1 == parts.len() {
                let child = self.read_inode(entry.inode, entry.file_type)?;
                return self.build_node(norm, &child);
            }
            cur_ino = entry.inode;
        }
        Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!("Path not found: {norm}"),
        ))
    }

    pub fn read_path(&mut self, internal_path: &str) -> io::Result<(Vec<u8>, ExtFileNode)> {
        let node = self.lookup_path(internal_path)?;
        if node.is_dir {
            return Err(io::Error::new(
                io::ErrorKind::Other,
                format!("Is a directory: {internal_path}"),
            ));
        }
        let inode = self.read_inode(node.ino, 0)?;
        let bytes = self.read_inode_bytes(&inode)?;
        Ok((bytes, node))
    }

    pub fn open_inode_reader(&mut self, inode: InodeData) -> io::Result<ExtFileReader<'_>> {
        let size = inode.i_size;
        let block_size = self.block_size;
        let inline_data = if (inode.i_flags & EXT4_INLINE_DATA_FL) != 0 {
            let bytes = self.read_inode_bytes(&inode)?;
            Some(bytes)
        } else {
            None
        };
        let extents = if inline_data.is_none() && (inode.i_flags & EXT4_EXTENTS_FL) != 0 {
            Some(self.collect_extents(&inode)?)
        } else {
            None
        };
        Ok(ExtFileReader {
            vol: self,
            inode,
            size,
            pos: 0,
            block_size,
            inline_data,
            extents,
            cached_blk: None,
        })
    }

    pub fn open_path_reader(&mut self, internal_path: &str) -> io::Result<ExtFileReader<'_>> {
        let node = self.lookup_path(internal_path)?;
        if node.is_dir {
            return Err(io::Error::new(
                io::ErrorKind::Other,
                format!("Is a directory: {internal_path}"),
            ));
        }
        let inode = self.read_inode(node.ino, 0)?;
        self.open_inode_reader(inode)
    }

    pub fn usage_report(&mut self, file_size: u64) -> io::Result<ExtUsageReport> {
        let nodes = self.walk_nodes()?;
        let free_blocks = self.superblock.s_free_blocks_count;
        let total_blocks = self.superblock.s_blocks_count;
        let bs = self.block_size;
        let used_blocks = total_blocks.saturating_sub(free_blocks);
        let mut files = 0usize;
        let mut dirs = 0usize;
        let mut links = 0usize;
        let mut logical_bytes = 0u64;
        for n in &nodes {
            if n.is_dir {
                dirs += 1;
            } else if n.is_link {
                links += 1;
            } else {
                files += 1;
                logical_bytes += n.size;
            }
        }
        Ok(ExtUsageReport {
            fs: "EXT".to_string(),
            block_size: bs,
            file_size,
            total_blocks,
            meta_bytes: 0,
            used_blocks,
            used_bytes: used_blocks * bs,
            wasted_blocks: 0,
            wasted_bytes: 0,
            free_blocks,
            free_bytes: free_blocks * bs,
            logical_bytes,
            reclaimable_bytes: 0,
            files,
            dirs,
            links,
        })
    }
}

/// Range-seekable `Read + Seek` reader for a single EXT2/3/4 file inode.
/// Reads only the exact blocks/extents touched by `read` / `seek`.
pub struct ExtFileReader<'a> {
    vol: &'a mut Volume,
    inode: InodeData,
    size: u64,
    pos: u64,
    block_size: u64,
    inline_data: Option<Vec<u8>>,
    extents: Option<Vec<ExtentRun>>,
    cached_blk: Option<(u64, Vec<u8>)>,
}

impl<'a> ExtFileReader<'a> {
    pub fn len(&self) -> u64 {
        self.size
    }

    pub fn is_empty(&self) -> bool {
        self.size == 0
    }

    fn read_single_block(&mut self, file_block: u64) -> io::Result<&[u8]> {
        let hit = self
            .cached_blk
            .as_ref()
            .is_some_and(|(fb, _)| *fb == file_block);
        if !hit {
            let bs = self.block_size as usize;
            let mut blk = vec![0u8; bs];
            let disk_block = if let Some(ref runs) = self.extents {
                let idx = runs.partition_point(|r| r.file_block + r.len as u64 <= file_block);
                if idx < runs.len() {
                    let r = &runs[idx];
                    if file_block >= r.file_block
                        && file_block < r.file_block + r.len as u64
                        && !r.unwritten
                        && r.disk_block != 0
                    {
                        r.disk_block + (file_block - r.file_block)
                    } else {
                        0
                    }
                } else {
                    0
                }
            } else {
                self.vol.resolve_indirect_block(&self.inode, file_block)?
            };
            if disk_block != 0 {
                self.vol
                    .reader
                    .read_at(disk_block * self.block_size, &mut blk)?;
            }
            self.cached_blk = Some((file_block, blk));
        }
        Ok(&self.cached_blk.as_ref().unwrap().1)
    }
}

impl<'a> Read for ExtFileReader<'a> {
    fn read(&mut self, buf: &mut [u8]) -> io::Result<usize> {
        if self.pos >= self.size || buf.is_empty() {
            return Ok(0);
        }
        let want = ((self.size - self.pos) as usize).min(buf.len());
        if let Some(ref inline) = self.inline_data {
            let start = self.pos as usize;
            let take = want.min(inline.len().saturating_sub(start));
            buf[..take].copy_from_slice(&inline[start..start + take]);
            self.pos += take as u64;
            return Ok(take);
        }

        let bs = self.block_size;
        let mut done = 0usize;
        while done < want {
            let cur_pos = self.pos;
            let file_block = cur_pos / bs;
            let within = (cur_pos % bs) as usize;
            let rem = want - done;

            // Fast contiguous multi-block path when reading >= 1 full block from an extent
            if within == 0 && rem >= bs as usize {
                if let Some(ref runs) = self.extents {
                    let idx = runs.partition_point(|r| r.file_block + r.len as u64 <= file_block);
                    if idx < runs.len() {
                        let r = &runs[idx];
                        if file_block >= r.file_block && file_block < r.file_block + r.len as u64 {
                            let avail_blocks = (r.file_block + r.len as u64) - file_block;
                            let take = (avail_blocks * bs).min(rem as u64) as usize;
                            if r.unwritten || r.disk_block == 0 {
                                buf[done..done + take].fill(0);
                            } else {
                                let disk_off = (r.disk_block + (file_block - r.file_block)) * bs;
                                self.vol.reader.read_at(disk_off, &mut buf[done..done + take])?;
                            }
                            self.pos += take as u64;
                            done += take;
                            continue;
                        }
                    }
                }
            }

            let take = ((bs as usize) - within).min(rem);
            let blk = self.read_single_block(file_block)?;
            buf[done..done + take].copy_from_slice(&blk[within..within + take]);
            self.pos += take as u64;
            done += take;
        }
        Ok(done)
    }
}

impl<'a> Seek for ExtFileReader<'a> {
    fn seek(&mut self, style: SeekFrom) -> io::Result<u64> {
        let next: i64 = match style {
            SeekFrom::Start(off) => off as i64,
            SeekFrom::Current(delta) => self.pos as i64 + delta,
            SeekFrom::End(delta) => self.size as i64 + delta,
        };
        if next < 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "negative seek position",
            ));
        }
        self.pos = next as u64;
        Ok(self.pos)
    }
}

impl InodeData {
    pub fn is_dir(&self) -> bool {
        (self.i_mode & S_IFMT) == S_IFDIR || self.file_type == FT_DIR
    }

    pub fn is_file(&self) -> bool {
        (self.i_mode & S_IFMT) == S_IFREG || self.file_type == FT_REG_FILE
    }

    pub fn is_symlink(&self) -> bool {
        (self.i_mode & S_IFMT) == S_IFLNK || self.file_type == FT_SYMLINK
    }
}

#[inline]
fn u32_le_u16(b: &[u8], off: usize) -> u32 {
    u16_le(b, off) as u32
}

fn write_zeros(writer: &mut dyn Write, mut bytes: u64, zeros: &[u8]) -> io::Result<()> {
    while bytes > 0 {
        let c = (bytes as usize).min(zeros.len());
        writer.write_all(&zeros[..c])?;
        bytes -= c as u64;
    }
    Ok(())
}
