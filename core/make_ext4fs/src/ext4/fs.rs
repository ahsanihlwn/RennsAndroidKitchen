use std::cell::RefCell;
use std::collections::HashMap;
use std::fs::File;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::rc::Rc;

use crate::config::fs_config::FsConfigProvider;
use crate::config::selinux::FileContexts;
use crate::ext4::allocator::{bg_has_super_block, BlockAllocation, BlockGroup};
use crate::ext4::extent::{inode_allocate_data_extents, inode_allocate_file_extents};
use crate::ext4::hash::{ext4_crc16, generate_uuid};
use crate::ext4::indirect::{inode_allocate_data_indirect, inode_attach_resize};
use crate::ext4::types::*;
use crate::ext4::xattr::{inode_set_capabilities, inode_set_selinux};
use crate::platform::{lstat_entry, scandir_sorted, EntryKind, MODE_PERM_MASK};
use crate::sparse::{SharedBuf, SparseFile};

pub struct Ext4Options {
    pub len: i64,
    pub block_size: u32,
    pub blocks_per_group: u32,
    pub inodes: u32,
    pub inode_size: u32,
    pub journal_blocks: u32,
    pub no_journal: bool,
    pub label: String,
    pub force: bool,
    pub verbose: bool,
    pub fixed_time: i64,
    pub gzip: bool,
    pub sparse: bool,
    pub crc: bool,
    pub wipe: bool,
}

impl Default for Ext4Options {
    fn default() -> Self {
        Self {
            len: 0,
            block_size: 0,
            blocks_per_group: 0,
            inodes: 0,
            inode_size: 0,
            journal_blocks: 0,
            no_journal: false,
            label: String::new(),
            force: false,
            verbose: false,
            fixed_time: -1,
            gzip: false,
            sparse: false,
            crc: false,
            wipe: false,
        }
    }
}

#[allow(dead_code)]
struct FsInfo {
    len: u64,
    block_size: u32,
    blocks_per_group: u32,
    inodes_per_group: u32,
    inode_size: u32,
    inodes: u32,
    journal_blocks: u32,
    feat_ro_compat: u16,
    feat_compat: u16,
    feat_incompat: u16,
    bg_desc_reserve_blocks: u32,
    label: String,
}

struct Dentry {
    path: String,
    full_path: Option<PathBuf>,
    filename: String,
    link: Option<String>,
    size: u64,
    file_type: u8,
    mode: u16,
    uid: u16,
    gid: u16,
    mtime: u32,
    secon: Option<String>,
    capabilities: u64,
    inode_slot_offset: usize,
}

struct Ext4State {
    info: FsInfo,
    first_data_block: u32,
    len_blocks: u64,
    inode_table_blocks: u32,
    groups: u32,
    bg_desc_blocks: u32,
    default_i_flags: u32,
    blocks_per_ind: u32,
    blocks_per_dind: u32,
    blocks_per_tind: u32,
    sb: SharedBuf,
    bg_desc: SharedBuf,
    bgs: Vec<BlockGroup>,
    xattr_blocks: HashMap<u32, SharedBuf>,
    sparse_file: SparseFile,
    saved_allocations: Vec<BlockAllocation>,
    force: bool,
    verbose: bool,
    fixed_time: i64,
}

#[inline]
fn log_2(mut j: u32) -> u32 {
    let mut i = 0i32;
    while j > 0 {
        i += 1;
        j >>= 1;
    }
    (i - 1) as u32
}

fn canonicalize_slashes(s: &str, absolute: bool) -> String {
    if s.is_empty() {
        return if absolute {
            "/".to_string()
        } else {
            String::new()
        };
    }
    let mut out = String::with_capacity(s.len() + 2);
    if absolute && !s.starts_with('/') {
        out.push('/');
    }
    out.push_str(s);
    if !s.ends_with('/') {
        out.push('/');
    }
    out
}

impl Ext4State {
    fn fail_or_warn(&self, msg: &str) -> Result<(), String> {
        eprintln!("error: {}", msg);
        if self.force {
            Ok(())
        } else {
            Err(msg.to_string())
        }
    }

    fn with_inode_mut<R>(&mut self, inode_num: u32, f: impl FnOnce(&mut [u8]) -> R) -> R {
        let idx = inode_num - 1;
        let bg_idx = (idx / self.info.inodes_per_group) as usize;
        let local_idx = (idx % self.info.inodes_per_group) as usize;
        let tbl = self.bgs[bg_idx].ensure_inode_table(
            self.inode_table_blocks,
            self.bg_desc_blocks,
            self.info.bg_desc_reserve_blocks,
            &mut self.sparse_file,
        );
        let mut tbl_mut = tbl.borrow_mut();
        let off = local_idx * self.info.inode_size as usize;
        let end = off + self.info.inode_size as usize;
        f(&mut tbl_mut[off..end])
    }

    fn allocate_inode(&mut self) -> Option<u32> {
        for (bg_idx, bg) in self.bgs.iter_mut().enumerate() {
            if let Some(local) = bg.reserve_inodes(1) {
                return Some((bg_idx as u32) * self.info.inodes_per_group + local);
            }
        }
        None
    }

    fn add_directory(&mut self, inode_num: u32) {
        let bg_idx = ((inode_num - 1) / self.info.inodes_per_group) as usize;
        self.bgs[bg_idx].used_dirs += 1;
    }

    fn fill_in_sb(&mut self) {
        {
            let mut sb_buf = self.sb.borrow_mut();
            let sb = &mut sb_buf[..SUPERBLOCK_SIZE];
            write_u32_le(sb, sb_off::S_INODES_COUNT, self.info.inodes_per_group * self.groups);
            write_u32_le(sb, sb_off::S_BLOCKS_COUNT_LO, self.len_blocks as u32);
            write_u32_le(sb, sb_off::S_FIRST_DATA_BLOCK, self.first_data_block);
            let log_bs = log_2(self.info.block_size / 1024);
            write_u32_le(sb, sb_off::S_LOG_BLOCK_SIZE, log_bs);
            write_u32_le(sb, sb_off::S_OBSO_LOG_FRAG_SIZE, log_bs);
            write_u32_le(sb, sb_off::S_BLOCKS_PER_GROUP, self.info.blocks_per_group);
            write_u32_le(sb, sb_off::S_OBSO_FRAGS_PER_GROUP, self.info.blocks_per_group);
            write_u32_le(sb, sb_off::S_INODES_PER_GROUP, self.info.inodes_per_group);
            write_u16_le(sb, sb_off::S_MAX_MNT_COUNT, 0xFFFF);
            write_u16_le(sb, sb_off::S_MAGIC, EXT4_SUPER_MAGIC);
            write_u16_le(sb, sb_off::S_STATE, EXT4_VALID_FS);
            write_u16_le(sb, sb_off::S_ERRORS, EXT4_ERRORS_RO);
            write_u32_le(sb, sb_off::S_CREATOR_OS, EXT4_OS_LINUX);
            write_u32_le(sb, sb_off::S_REV_LEVEL, EXT4_DYNAMIC_REV);
            write_u16_le(sb, sb_off::S_DEF_RESUID, EXT4_DEF_RESUID);
            write_u16_le(sb, sb_off::S_DEF_RESGID, EXT4_DEF_RESGID);
            write_u32_le(sb, sb_off::S_FIRST_INO, EXT4_GOOD_OLD_FIRST_INO);
            write_u16_le(sb, sb_off::S_INODE_SIZE, self.info.inode_size as u16);
            write_u16_le(sb, sb_off::S_BLOCK_GROUP_NR, 0);
            write_u32_le(sb, sb_off::S_FEATURE_COMPAT, self.info.feat_compat as u32);
            write_u32_le(sb, sb_off::S_FEATURE_INCOMPAT, self.info.feat_incompat as u32);
            write_u32_le(sb, sb_off::S_FEATURE_RO_COMPAT, self.info.feat_ro_compat as u32);

            let uuid = generate_uuid("extandroid/make_ext4fs", &self.info.label);
            sb[sb_off::S_UUID..sb_off::S_UUID + 16].copy_from_slice(&uuid);

            let vol_bytes = self.info.label.as_bytes();
            let copy_len = vol_bytes.len().min(16);
            sb[sb_off::S_VOLUME_NAME..sb_off::S_VOLUME_NAME + copy_len]
                .copy_from_slice(&vol_bytes[..copy_len]);

            write_u16_le(sb, sb_off::S_RESERVED_GDT_BLOCKS, self.info.bg_desc_reserve_blocks as u16);
            if (self.info.feat_compat & EXT4_FEATURE_COMPAT_HAS_JOURNAL) != 0 {
                write_u32_le(sb, sb_off::S_JOURNAL_INUM, EXT4_JOURNAL_INO);
            }
            sb[sb_off::S_DEF_HASH_VERSION] = DX_HASH_TEA;
            sb[sb_off::S_RESERVED_CHAR_PAD] = EXT4_JNL_BACKUP_BLOCKS;
            write_u16_le(sb, sb_off::S_DESC_SIZE, GROUP_DESC_SIZE as u16);
            write_u32_le(sb, sb_off::S_BLOCKS_COUNT_HI, (self.len_blocks >> 32) as u32);

            let extra_isize = (EXT4_INODE_STRUCT_SIZE as u16) - EXT4_GOOD_OLD_INODE_SIZE;
            write_u16_le(sb, sb_off::S_MIN_EXTRA_ISIZE, extra_isize);
            write_u16_le(sb, sb_off::S_WANT_EXTRA_ISIZE, extra_isize);
            write_u32_le(sb, sb_off::S_FLAGS, 2);
        }

        let sb_snapshot = self.sb.borrow().clone();
        let mut bg_desc = self.bg_desc.borrow_mut();

        for i in 0..self.groups {
            let group_start_block = self.first_data_block + i * self.info.blocks_per_group;
            let mut header_size = 0u32;
            if bg_has_super_block(i, self.info.feat_ro_compat) {
                if i != 0 {
                    let mut backup = sb_snapshot.clone();
                    write_u16_le(&mut backup, sb_off::S_BLOCK_GROUP_NR, i as u16);
                    self.sparse_file.add_owned_data(backup, group_start_block);
                }
                self.sparse_file.add_shared_data(
                    Rc::clone(&self.bg_desc),
                    self.bg_desc_blocks * self.info.block_size,
                    group_start_block + 1,
                );
                header_size = 1 + self.bg_desc_blocks + self.info.bg_desc_reserve_blocks;
            }

            let gd_off = (i as usize) * GROUP_DESC_SIZE;
            write_u32_le(&mut bg_desc, gd_off + bg_off::BG_BLOCK_BITMAP, group_start_block + header_size);
            write_u32_le(&mut bg_desc, gd_off + bg_off::BG_INODE_BITMAP, group_start_block + header_size + 1);
            write_u32_le(&mut bg_desc, gd_off + bg_off::BG_INODE_TABLE, group_start_block + header_size + 2);
            write_u16_le(&mut bg_desc, gd_off + bg_off::BG_FREE_BLOCKS_COUNT, self.info.blocks_per_group as u16);
            write_u16_le(&mut bg_desc, gd_off + bg_off::BG_FREE_INODES_COUNT, self.info.inodes_per_group as u16);
            write_u16_le(&mut bg_desc, gd_off + bg_off::BG_USED_DIRS_COUNT, 0);
        }
    }

    fn create_journal_inode(&mut self) -> Result<(), String> {
        let len = self.info.journal_blocks as u64 * self.info.block_size as u64;
        let idx = EXT4_JOURNAL_INO - 1;
        let bg_idx = (idx / self.info.inodes_per_group) as usize;
        let local_idx = (idx % self.info.inodes_per_group) as usize;
        let tbl = self.bgs[bg_idx].ensure_inode_table(
            self.inode_table_blocks,
            self.bg_desc_blocks,
            self.info.bg_desc_reserve_blocks,
            &mut self.sparse_file,
        );

        let mut i_block_copy = [0u8; 60];
        let journal_data = {
            let mut tbl_mut = tbl.borrow_mut();
            let off = local_idx * self.info.inode_size as usize;
            let inode_buf = &mut tbl_mut[off..off + self.info.inode_size as usize];
            let jdata = inode_allocate_data_extents(
                inode_buf,
                len,
                len,
                self.info.block_size,
                &mut self.bgs,
                &mut self.sparse_file,
            )?
            .ok_or_else(|| "failed to allocate extents for journal data".to_string())?;

            write_u16_le(inode_buf, inode_off::I_MODE, (S_IFREG | 0o600) as u16);
            write_u16_le(inode_buf, inode_off::I_LINKS_COUNT, 1);
            i_block_copy.copy_from_slice(&inode_buf[inode_off::I_BLOCK..inode_off::I_BLOCK + 60]);
            jdata
        };

        {
            let mut jd = journal_data.borrow_mut();
            // journal_superblock_t (big-endian fields)
            jd[0..4].copy_from_slice(&JBD2_MAGIC_NUMBER.to_be_bytes());
            jd[4..8].copy_from_slice(&JBD2_SUPERBLOCK_V2.to_be_bytes());
            jd[12..16].copy_from_slice(&self.info.block_size.to_be_bytes());
            jd[16..20].copy_from_slice(&self.info.journal_blocks.to_be_bytes());
            jd[20..24].copy_from_slice(&1u32.to_be_bytes()); // s_first
            jd[24..28].copy_from_slice(&1u32.to_be_bytes()); // s_sequence
            jd[64..68].copy_from_slice(&1u32.to_be_bytes()); // s_nr_users at offset 64
        }

        let mut sb = self.sb.borrow_mut();
        sb[sb_off::S_JNL_BLOCKS..sb_off::S_JNL_BLOCKS + 60].copy_from_slice(&i_block_copy);
        Ok(())
    }

    fn create_resize_inode(&mut self) -> Result<(), String> {
        let mut alloc = BlockAllocation::new();
        for i in 0..self.groups {
            if bg_has_super_block(i, self.info.feat_ro_compat) {
                let group_start_block = self.first_data_block + i * self.info.blocks_per_group;
                let reserved_start = group_start_block + 1 + self.bg_desc_blocks;
                let reserved_len = self.info.bg_desc_reserve_blocks;
                alloc.append_region(reserved_start, reserved_len, i as usize);
            }
        }

        let idx = EXT4_RESIZE_INO - 1;
        let bg_idx = (idx / self.info.inodes_per_group) as usize;
        let local_idx = (idx % self.info.inodes_per_group) as usize;
        let tbl = self.bgs[bg_idx].ensure_inode_table(
            self.inode_table_blocks,
            self.bg_desc_blocks,
            self.info.bg_desc_reserve_blocks,
            &mut self.sparse_file,
        );

        let mut tbl_mut = tbl.borrow_mut();
        let off = local_idx * self.info.inode_size as usize;
        let inode_buf = &mut tbl_mut[off..off + self.info.inode_size as usize];

        inode_attach_resize(
            inode_buf,
            &mut alloc,
            self.info.block_size,
            self.bg_desc_blocks,
            self.info.bg_desc_reserve_blocks,
            self.blocks_per_ind,
            &mut self.bgs,
            &mut self.sparse_file,
        )?;

        write_u16_le(inode_buf, inode_off::I_MODE, (S_IFREG | 0o600) as u16);
        write_u16_le(inode_buf, inode_off::I_LINKS_COUNT, 1);
        Ok(())
    }

    fn dentry_size(&self, dentries: &[Dentry]) -> u32 {
        let mut len = 24u32;
        let bs = self.info.block_size;
        for d in dentries {
            let dentry_len = 8 + align_u32(d.filename.len() as u32, 4);
            if (len % bs) + dentry_len > bs {
                len += bs - (len % bs);
            }
            len += dentry_len;
        }
        len
    }

    fn add_dentry_raw(
        &self,
        data: &mut [u8],
        offset: &mut u32,
        prev_off: Option<usize>,
        inode: u32,
        name: &str,
        file_type: u8,
    ) -> Result<usize, String> {
        let bs = self.info.block_size;
        let name_bytes = name.as_bytes();
        let name_len = name_bytes.len() as u8;
        let rec_len = (8 + align_u32(name_len as u32, 4)) as u16;

        let start_block = *offset / bs;
        let end_block = (*offset + rec_len as u32 - 1) / bs;
        if start_block != end_block {
            let po = prev_off.ok_or_else(|| "no prev".to_string())?;
            let old_rec_len = read_u16_le(data, po + 4);
            let pad = (end_block * bs - *offset) as u16;
            write_u16_le(data, po + 4, old_rec_len + pad);
            *offset = end_block * bs;
        }

        let d_off = *offset as usize;
        write_u32_le(data, d_off, inode);
        write_u16_le(data, d_off + 4, rec_len);
        data[d_off + 6] = name_len;
        data[d_off + 7] = file_type;
        data[d_off + 8..d_off + 8 + name_bytes.len()].copy_from_slice(name_bytes);

        *offset += rec_len as u32;
        Ok(d_off)
    }

    fn make_directory(
        &mut self,
        mut dir_inode_num: u32,
        dentries: &mut [Dentry],
        dirs: u32,
    ) -> Result<(u32, SharedBuf), String> {
        let bs = self.info.block_size;
        let blocks = div_round_up_u32(self.dentry_size(dentries), bs);
        let len = blocks * bs;

        let inode_num = if dir_inode_num != 0 {
            self.allocate_inode()
                .ok_or_else(|| "failed to allocate inode".to_string())?
        } else {
            dir_inode_num = EXT4_ROOT_INO;
            EXT4_ROOT_INO
        };

        self.add_directory(inode_num);

        let idx = inode_num - 1;
        let bg_idx = (idx / self.info.inodes_per_group) as usize;
        let local_idx = (idx % self.info.inodes_per_group) as usize;
        let tbl = self.bgs[bg_idx].ensure_inode_table(
            self.inode_table_blocks,
            self.bg_desc_blocks,
            self.info.bg_desc_reserve_blocks,
            &mut self.sparse_file,
        );

        let data = {
            let mut tbl_mut = tbl.borrow_mut();
            let off = local_idx * self.info.inode_size as usize;
            let inode_buf = &mut tbl_mut[off..off + self.info.inode_size as usize];

            let data = inode_allocate_data_extents(
                inode_buf,
                len as u64,
                len as u64,
                bs,
                &mut self.bgs,
                &mut self.sparse_file,
            )?
            .ok_or_else(|| format!("failed to allocate {} extents", len))?;

            write_u16_le(inode_buf, inode_off::I_MODE, S_IFDIR as u16);
            write_u16_le(inode_buf, inode_off::I_LINKS_COUNT, (dirs + 2) as u16);
            let flags = read_u32_le(inode_buf, inode_off::I_FLAGS) | self.default_i_flags;
            write_u32_le(inode_buf, inode_off::I_FLAGS, flags);
            data
        };

        {
            let mut d_mut = data.borrow_mut();
            let mut offset = 0u32;
            let mut prev = self.add_dentry_raw(&mut d_mut, &mut offset, None, inode_num, ".", EXT4_FT_DIR)?;
            prev = self.add_dentry_raw(&mut d_mut, &mut offset, Some(prev), dir_inode_num, "..", EXT4_FT_DIR)?;

            let count = dentries.len();
            for (i, d) in dentries.iter_mut().enumerate() {
                prev = self.add_dentry_raw(&mut d_mut, &mut offset, Some(prev), 0, &d.filename, d.file_type)?;
                if offset > len || (offset == len && i != count - 1) {
                    return Err(format!(
                        "internal error: dentry for {} ends at {}, past {}",
                        d.filename, offset, len
                    ));
                }
                d.inode_slot_offset = prev;
            }

            let last_rec_len = read_u16_le(&d_mut, prev + 4);
            write_u16_le(&mut d_mut, prev + 4, last_rec_len + (len - offset) as u16);
        }

        Ok((inode_num, data))
    }

    fn make_file(&mut self, filepath: &Path, filename_str: String, len: u64) -> Result<u32, String> {
        let inode_num = self
            .allocate_inode()
            .ok_or_else(|| "failed to allocate inode".to_string())?;

        let idx = inode_num - 1;
        let bg_idx = (idx / self.info.inodes_per_group) as usize;
        let local_idx = (idx % self.info.inodes_per_group) as usize;
        let tbl = self.bgs[bg_idx].ensure_inode_table(
            self.inode_table_blocks,
            self.bg_desc_blocks,
            self.info.bg_desc_reserve_blocks,
            &mut self.sparse_file,
        );

        let bs = self.info.block_size;
        let default_flags = self.default_i_flags;
        let mut alloc_opt = None;
        {
            let mut tbl_mut = tbl.borrow_mut();
            let off = local_idx * self.info.inode_size as usize;
            let inode_buf = &mut tbl_mut[off..off + self.info.inode_size as usize];

            if len > 0 {
                let mut alloc = inode_allocate_file_extents(
                    inode_buf,
                    len,
                    filepath,
                    bs,
                    &mut self.bgs,
                    &mut self.sparse_file,
                )?;
                alloc.filename = Some(filename_str);
                alloc_opt = Some(alloc);
            }

            write_u16_le(inode_buf, inode_off::I_MODE, S_IFREG as u16);
            write_u16_le(inode_buf, inode_off::I_LINKS_COUNT, 1);
            let flags = read_u32_le(inode_buf, inode_off::I_FLAGS) | default_flags;
            write_u32_le(inode_buf, inode_off::I_FLAGS, flags);
        }

        if let Some(alloc) = alloc_opt {
            self.saved_allocations.push(alloc);
        }

        Ok(inode_num)
    }

    fn make_link(&mut self, link: &str) -> Result<u32, String> {
        let inode_num = self
            .allocate_inode()
            .ok_or_else(|| "failed to allocate inode".to_string())?;

        let idx = inode_num - 1;
        let bg_idx = (idx / self.info.inodes_per_group) as usize;
        let local_idx = (idx % self.info.inodes_per_group) as usize;
        let tbl = self.bgs[bg_idx].ensure_inode_table(
            self.inode_table_blocks,
            self.bg_desc_blocks,
            self.info.bg_desc_reserve_blocks,
            &mut self.sparse_file,
        );

        let bs = self.info.block_size;
        let default_flags = self.default_i_flags;
        let link_bytes = link.as_bytes();
        let len = link_bytes.len();

        let mut tbl_mut = tbl.borrow_mut();
        let off = local_idx * self.info.inode_size as usize;
        let inode_buf = &mut tbl_mut[off..off + self.info.inode_size as usize];

        write_u16_le(inode_buf, inode_off::I_MODE, S_IFLNK as u16);
        write_u16_le(inode_buf, inode_off::I_LINKS_COUNT, 1);
        let flags = read_u32_le(inode_buf, inode_off::I_FLAGS) | default_flags;
        write_u32_le(inode_buf, inode_off::I_FLAGS, flags);
        write_u32_le(inode_buf, inode_off::I_SIZE_LO, len as u32);

        if len + 1 <= 60 {
            inode_buf[inode_off::I_BLOCK..inode_off::I_BLOCK + len].copy_from_slice(link_bytes);
        } else {
            let data = inode_allocate_data_indirect(
                inode_buf,
                bs,
                bs,
                bs,
                self.blocks_per_ind,
                self.blocks_per_dind,
                self.blocks_per_tind,
                &mut self.bgs,
                &mut self.sparse_file,
            )?
            .unwrap();
            data.borrow_mut()[..len].copy_from_slice(link_bytes);
            write_u32_le(inode_buf, inode_off::I_BLOCKS_LO, bs / 512);
        }

        Ok(inode_num)
    }

    fn inode_set_permissions(&mut self, inode_num: u32, mode: u16, uid: u16, gid: u16, mtime: u32) {
        self.with_inode_mut(inode_num, |inode_buf| {
            let cur_mode = read_u16_le(inode_buf, inode_off::I_MODE);
            write_u16_le(inode_buf, inode_off::I_MODE, cur_mode | mode);
            write_u16_le(inode_buf, inode_off::I_UID, uid);
            write_u16_le(inode_buf, inode_off::I_GID, gid);
            write_u32_le(inode_buf, inode_off::I_MTIME, mtime);
            write_u32_le(inode_buf, inode_off::I_ATIME, mtime);
            write_u32_le(inode_buf, inode_off::I_CTIME, mtime);
        });
    }

    fn set_inode_selinux(&mut self, inode_num: u32, secon: Option<&str>) -> Result<(), String> {
        let Some(secon) = secon else {
            return Ok(());
        };
        let inode_size = self.info.inode_size;
        let block_size = self.info.block_size;
        let idx = inode_num - 1;
        let bg_idx = (idx / self.info.inodes_per_group) as usize;
        let local_idx = (idx % self.info.inodes_per_group) as usize;
        let tbl = self.bgs[bg_idx].ensure_inode_table(
            self.inode_table_blocks,
            self.bg_desc_blocks,
            self.info.bg_desc_reserve_blocks,
            &mut self.sparse_file,
        );
        let mut tbl_mut = tbl.borrow_mut();
        let off = local_idx * inode_size as usize;
        let inode_buf = &mut tbl_mut[off..off + inode_size as usize];

        inode_set_selinux(
            inode_num,
            inode_buf,
            inode_size,
            block_size,
            secon,
            &mut self.bgs,
            &mut self.xattr_blocks,
            &mut self.sparse_file,
        )
    }

    fn set_inode_capabilities(&mut self, inode_num: u32, capabilities: u64) -> Result<(), String> {
        if capabilities == 0 {
            return Ok(());
        }
        let inode_size = self.info.inode_size;
        let block_size = self.info.block_size;
        let idx = inode_num - 1;
        let bg_idx = (idx / self.info.inodes_per_group) as usize;
        let local_idx = (idx % self.info.inodes_per_group) as usize;
        let tbl = self.bgs[bg_idx].ensure_inode_table(
            self.inode_table_blocks,
            self.bg_desc_blocks,
            self.info.bg_desc_reserve_blocks,
            &mut self.sparse_file,
        );
        let mut tbl_mut = tbl.borrow_mut();
        let off = local_idx * inode_size as usize;
        let inode_buf = &mut tbl_mut[off..off + inode_size as usize];

        inode_set_capabilities(
            inode_num,
            inode_buf,
            inode_size,
            block_size,
            capabilities,
            &mut self.bgs,
            &mut self.xattr_blocks,
            &mut self.sparse_file,
        )
    }

    fn build_default_directory_structure(
        &mut self,
        dir_path: &str,
        sehnd: Option<&FileContexts>,
    ) -> Result<u32, String> {
        let mut dentries = vec![Dentry {
            path: String::new(),
            full_path: None,
            filename: "lost+found".to_string(),
            link: None,
            size: 0,
            file_type: EXT4_FT_DIR,
            mode: 0o700,
            uid: 0,
            gid: 0,
            mtime: 0,
            secon: None,
            capabilities: 0,
            inode_slot_offset: 0,
        }];

        let (root_inode, root_data) = self.make_directory(0, &mut dentries, 1)?;
        let mut empty = Vec::new();
        let (lf_inode, _) = self.make_directory(root_inode, &mut empty, 0)?;

        write_u32_le(&mut root_data.borrow_mut(), dentries[0].inode_slot_offset, lf_inode);
        self.inode_set_permissions(lf_inode, dentries[0].mode, dentries[0].uid, dentries[0].gid, dentries[0].mtime);

        if let Some(se) = sehnd {
            let path = format!("{}lost+found", dir_path);
            match se.lookup(&path, S_IFDIR) {
                Some(ctx) => self.set_inode_selinux(lf_inode, Some(&ctx))?,
                None => self.fail_or_warn(&format!("cannot lookup security context for {}", path))?,
            }
        }

        Ok(root_inode)
    }

    fn build_directory_structure(
        &mut self,
        full_path: Option<&str>,
        dir_path: &str,
        dir_inode: u32,
        fs_config: &FsConfigProvider,
        sehnd: Option<&FileContexts>,
    ) -> Result<u32, String> {
        let mut dentries: Vec<Dentry> = Vec::new();
        let mut dirs = 0u32;
        let mut has_lost_and_found = false;

        if let Some(fp) = full_path {
            let names = scandir_sorted(Path::new(fp)).map_err(|e| format!("scandir: {}", e))?;
            if dir_inode == 0 {
                has_lost_and_found = names.iter().any(|n| n == "lost+found");
            }

            for name_os in names {
                let name = name_os.to_string_lossy().into_owned();
                let entry_path = format!("{}{}", dir_path, name);
                let entry_full_path_str = format!("{}{}", fp, name);
                let entry_full_path = PathBuf::from(&entry_full_path_str);

                let st = match lstat_entry(&entry_full_path, self.info.block_size) {
                    Ok(s) => s,
                    Err(e) => {
                        self.fail_or_warn(&format!("lstat: {}", e))?;
                        continue;
                    }
                };

                let mtime = if self.fixed_time == -1 {
                    st.mtime
                } else {
                    self.fixed_time as u32
                };

                let mut mode = st.st_mode;
                let mut uid = 0u32;
                let mut gid = 0u32;
                let mut capabilities = 0u64;

                if fs_config.is_active() {
                    let is_dir = st.kind == EntryKind::Directory;
                    fs_config.apply(
                        &entry_path,
                        is_dir,
                        &mut uid,
                        &mut gid,
                        &mut mode,
                        &mut capabilities,
                    );
                }
                mode &= MODE_PERM_MASK;

                let secon = if let Some(se) = sehnd {
                    let ctx = se.lookup(&entry_path, st.st_mode);
                    if ctx.is_none() {
                        self.fail_or_warn(&format!(
                            "cannot lookup security context for {}",
                            entry_path
                        ))?;
                    }
                    if let Some(ref c) = ctx {
                        if self.verbose {
                            println!("Labeling {} as {}", entry_path, c);
                        }
                    }
                    ctx
                } else {
                    None
                };

                let file_type = match st.kind {
                    EntryKind::Regular => EXT4_FT_REG_FILE,
                    EntryKind::Directory => {
                        dirs += 1;
                        EXT4_FT_DIR
                    }
                    EntryKind::Chr => EXT4_FT_CHRDEV,
                    EntryKind::Blk => EXT4_FT_BLKDEV,
                    EntryKind::Fifo => EXT4_FT_FIFO,
                    EntryKind::Sock => EXT4_FT_SOCK,
                    EntryKind::Symlink => EXT4_FT_SYMLINK,
                };

                dentries.push(Dentry {
                    path: entry_path,
                    full_path: Some(entry_full_path),
                    filename: name,
                    link: st.link_target,
                    size: st.size,
                    file_type,
                    mode: mode as u16,
                    uid: uid as u16,
                    gid: gid as u16,
                    mtime,
                    secon,
                    capabilities,
                    inode_slot_offset: 0,
                });
            }
        }

        if dir_inode == 0 && !has_lost_and_found {
            let lf_path = format!("{}lost+found", dir_path);
            let lf_secon = if let Some(se) = sehnd {
                let ctx = se.lookup(&lf_path, 0o700);
                if ctx.is_none() {
                    self.fail_or_warn(&format!("cannot lookup security context for {}", lf_path))?;
                }
                ctx
            } else {
                None
            };
            dentries.insert(
                0,
                Dentry {
                    path: lf_path,
                    full_path: None,
                    filename: "lost+found".to_string(),
                    link: None,
                    size: 0,
                    file_type: EXT4_FT_DIR,
                    mode: 0o700,
                    uid: 0,
                    gid: 0,
                    mtime: 0,
                    secon: lf_secon,
                    capabilities: 0,
                    inode_slot_offset: 0,
                },
            );
            dirs += 1;
        }

        let (inode, dir_data) = self.make_directory(dir_inode, &mut dentries, dirs)?;

        for d in dentries {
            let entry_inode = match d.file_type {
                EXT4_FT_REG_FILE => {
                    let fp = d.full_path.as_ref().unwrap();
                    let fp_str = fp.to_string_lossy().into_owned();
                    self.make_file(fp, fp_str, d.size)?
                }
                EXT4_FT_DIR => {
                    let sub_full = d
                        .full_path
                        .as_ref()
                        .map(|p| format!("{}/", p.to_string_lossy()));
                    let sub_dir = format!("{}/", d.path);
                    self.build_directory_structure(
                        sub_full.as_deref(),
                        &sub_dir,
                        inode,
                        fs_config,
                        sehnd,
                    )?
                }
                EXT4_FT_SYMLINK => {
                    let target = d.link.as_deref().unwrap_or("");
                    self.make_link(target)?
                }
                _ => {
                    self.fail_or_warn(&format!("unknown file type on {}", d.path))?;
                    0
                }
            };

            write_u32_le(&mut dir_data.borrow_mut(), d.inode_slot_offset, entry_inode);
            self.inode_set_permissions(entry_inode, d.mode, d.uid, d.gid, d.mtime);
            self.set_inode_selinux(entry_inode, d.secon.as_deref())?;
            self.set_inode_capabilities(entry_inode, d.capabilities)?;
        }

        Ok(inode)
    }

    fn update_free(&mut self) {
        let mut sb = self.sb.borrow_mut();
        let mut bg_desc = self.bg_desc.borrow_mut();

        let mut total_free_blocks = 0u32;
        let mut total_free_inodes = 0u32;

        for (i, bg) in self.bgs.iter().enumerate() {
            let gd_off = i * GROUP_DESC_SIZE;
            write_u16_le(&mut bg_desc, gd_off + bg_off::BG_FREE_BLOCKS_COUNT, bg.free_blocks as u16);
            total_free_blocks += bg.free_blocks;

            write_u16_le(&mut bg_desc, gd_off + bg_off::BG_FREE_INODES_COUNT, bg.free_inodes as u16);
            total_free_inodes += bg.free_inodes;

            let old_dirs = read_u16_le(&bg_desc, gd_off + bg_off::BG_USED_DIRS_COUNT);
            write_u16_le(&mut bg_desc, gd_off + bg_off::BG_USED_DIRS_COUNT, old_dirs + bg.used_dirs);

            write_u16_le(&mut bg_desc, gd_off + bg_off::BG_FLAGS, bg.flags);

            let mut crc = ext4_crc16(0xFFFF, &sb[sb_off::S_UUID..sb_off::S_UUID + 16]);
            crc = ext4_crc16(crc, &(i as u32).to_le_bytes());
            crc = ext4_crc16(crc, &bg_desc[gd_off..gd_off + bg_off::BG_CHECKSUM]);
            write_u16_le(&mut bg_desc, gd_off + bg_off::BG_CHECKSUM, crc);
        }

        let old_fb = read_u32_le(&sb, sb_off::S_FREE_BLOCKS_COUNT_LO);
        write_u32_le(&mut sb, sb_off::S_FREE_BLOCKS_COUNT_LO, old_fb + total_free_blocks);
        let old_fi = read_u32_le(&sb, sb_off::S_FREE_INODES_COUNT);
        write_u32_le(&mut sb, sb_off::S_FREE_INODES_COUNT, old_fi + total_free_inodes);
    }

    fn queue_sb(&mut self) {
        if self.info.block_size > 1024 {
            let mut buf = vec![0u8; self.info.block_size as usize];
            let sb = self.sb.borrow();
            buf[1024..2048].copy_from_slice(&sb[..1024]);
            self.sparse_file.add_owned_data(buf, 0);
        } else {
            self.sparse_file.add_shared_data(Rc::clone(&self.sb), 1024, 1);
        }
    }
}

pub enum OutputTarget {
    File(File),
    Stdout,
}

#[allow(clippy::too_many_arguments)]
pub fn make_ext4fs(
    output: OutputTarget,
    directory: Option<&str>,
    mountpoint: Option<&str>,
    fs_config: &FsConfigProvider,
    sehnd: Option<&FileContexts>,
    opts: &Ext4Options,
    mut block_list_file: Option<&mut File>,
) -> Result<(), String> {
    let mp = match mountpoint {
        None => String::new(),
        Some(m) => canonicalize_slashes(m, true),
    };
    let dir = directory.map(|d| canonicalize_slashes(d, false));

    let mut len_i64 = opts.len;
    if len_i64 <= 0 {
        if let OutputTarget::File(ref f) = output {
            if let Ok(meta) = f.metadata() {
                if meta.is_file() {
                    let reserve = if len_i64 < 0 { (-len_i64) as u64 } else { 0 };
                    let sz = meta.len() as i64 - reserve as i64;
                    if sz > 0 {
                        len_i64 = sz;
                    } else {
                        len_i64 = 0;
                    }
                }
            }
        }
    }
    if len_i64 <= 0 {
        return Err("Need size of filesystem".to_string());
    }

    let block_size = if opts.block_size == 0 {
        4096
    } else {
        opts.block_size
    };
    let len = (len_i64 as u64) & !(block_size as u64 - 1);

    let mut journal_blocks = if opts.journal_blocks == 0 {
        ((div_round_up_u64(len, block_size as u64) / 64) as u32).clamp(1024, 32768)
    } else {
        opts.journal_blocks
    };

    let mut feat_compat = if opts.no_journal {
        journal_blocks = 0;
        0u16
    } else {
        EXT4_FEATURE_COMPAT_HAS_JOURNAL
    };

    let blocks_per_group = if opts.blocks_per_group == 0 {
        block_size * 8
    } else {
        opts.blocks_per_group
    };

    let mut inodes = if opts.inodes == 0 {
        (div_round_up_u64(len, block_size as u64) / 4) as u32
    } else {
        opts.inodes
    };

    let inode_size = if opts.inode_size == 0 {
        256
    } else {
        opts.inode_size
    };

    let blocks_total = div_round_up_u64(len, block_size as u64) as u32;
    let block_groups_init = div_round_up_u32(blocks_total, blocks_per_group);
    let raw_ipg = div_round_up_u32(inodes, block_groups_init);
    let inodes_per_group = align_u32(raw_ipg, block_size / inode_size);
    inodes = inodes_per_group * block_groups_init;

    feat_compat |= EXT4_FEATURE_COMPAT_RESIZE_INODE | EXT4_FEATURE_COMPAT_EXT_ATTR;
    let feat_ro_compat =
        EXT4_FEATURE_RO_COMPAT_SPARSE_SUPER | EXT4_FEATURE_RO_COMPAT_LARGE_FILE | EXT4_FEATURE_RO_COMPAT_GDT_CSUM;
    let feat_incompat = EXT4_FEATURE_INCOMPAT_EXTENTS | EXT4_FEATURE_INCOMPAT_FILETYPE;

    let bg_desc_blocks_init = div_round_up_u32(block_groups_init * GROUP_DESC_SIZE as u32, block_size);
    let mut bg_desc_reserve_blocks =
        div_round_up_u32(block_groups_init * 1024 * GROUP_DESC_SIZE as u32, block_size) - bg_desc_blocks_init;
    if bg_desc_reserve_blocks > block_size / 4 {
        bg_desc_reserve_blocks = block_size / 4;
    }

    println!("Creating filesystem with parameters:");
    println!("    Size: {}", len);
    println!("    Block size: {}", block_size);
    println!("    Blocks per group: {}", blocks_per_group);
    println!("    Inodes per group: {}", inodes_per_group);
    println!("    Inode size: {}", inode_size);
    println!("    Journal blocks: {}", journal_blocks);
    println!("    Label: {}", opts.label);

    let first_data_block = if block_size > 1024 { 0 } else { 1 };
    let mut len_blocks = len / block_size as u64;
    let inode_table_blocks = div_round_up_u32(inodes_per_group * inode_size, block_size);
    let mut groups = div_round_up_u64(len_blocks - first_data_block as u64, blocks_per_group as u64) as u32;
    let blocks_per_ind = block_size / 4;
    let blocks_per_dind = blocks_per_ind.wrapping_mul(blocks_per_ind);
    let blocks_per_tind = blocks_per_dind.wrapping_mul(blocks_per_dind);
    let bg_desc_blocks = div_round_up_u32(groups * GROUP_DESC_SIZE as u32, block_size);

    let last_group_size = (len_blocks % blocks_per_group as u64) as u32;
    let mut last_header_size = 2 + inode_table_blocks;
    if bg_has_super_block(groups - 1, feat_ro_compat) {
        last_header_size += 1 + bg_desc_blocks + bg_desc_reserve_blocks;
    }
    if last_group_size > 0 && last_group_size < last_header_size {
        groups -= 1;
        len_blocks -= last_group_size as u64;
    }

    println!("    Blocks: {}", len_blocks);
    println!("    Block groups: {}", groups);
    println!("    Reserved block group size: {}", bg_desc_reserve_blocks);

    let mut sparse_file = SparseFile::new(block_size, len as i64);
    let mut bgs = Vec::with_capacity(groups as usize);
    for i in 0..groups {
        let has_sb = bg_has_super_block(i, feat_ro_compat);
        bgs.push(BlockGroup::new(
            i,
            first_data_block,
            blocks_per_group,
            inodes_per_group,
            inode_table_blocks,
            bg_desc_blocks,
            bg_desc_reserve_blocks,
            len_blocks,
            block_size,
            has_sb,
            &mut sparse_file,
        )?);
    }

    let sb = Rc::new(RefCell::new(vec![0u8; block_size as usize]));
    let bg_desc = Rc::new(RefCell::new(vec![
        0u8;
        block_size as usize * bg_desc_blocks as usize
    ]));

    let mut state = Ext4State {
        info: FsInfo {
            len,
            block_size,
            blocks_per_group,
            inodes_per_group,
            inode_size,
            inodes,
            journal_blocks,
            feat_ro_compat,
            feat_compat,
            feat_incompat,
            bg_desc_reserve_blocks,
            label: opts.label.clone(),
        },
        first_data_block,
        len_blocks,
        inode_table_blocks,
        groups,
        bg_desc_blocks,
        default_i_flags: EXT4_NOATIME_FL,
        blocks_per_ind,
        blocks_per_dind,
        blocks_per_tind,
        sb,
        bg_desc,
        bgs,
        xattr_blocks: HashMap::new(),
        sparse_file,
        saved_allocations: Vec::new(),
        force: opts.force,
        verbose: opts.verbose,
        fixed_time: opts.fixed_time,
    };

    state.fill_in_sb();

    if state.bgs[0].reserve_inodes(10).is_none() {
        state.fail_or_warn("failed to reserve first 10 inodes")?;
    }

    if (state.info.feat_compat & EXT4_FEATURE_COMPAT_HAS_JOURNAL) != 0 {
        if let Err(e) = state.create_journal_inode() {
            state.fail_or_warn(&e)?;
        }
    }

    if (state.info.feat_compat & EXT4_FEATURE_COMPAT_RESIZE_INODE) != 0 {
        if let Err(e) = state.create_resize_inode() {
            state.fail_or_warn(&e)?;
        }
    }

    let root_inode_num = if let Some(ref d) = dir {
        state.build_directory_structure(Some(d), &mp, 0, fs_config, sehnd)?
    } else {
        state.build_default_directory_structure(&mp, sehnd)?
    };

    let root_mode: u16 = 0o755;
    state.inode_set_permissions(root_inode_num, root_mode, 0, 0, 0);

    if let Some(se) = sehnd {
        match se.lookup(&mp, S_IFDIR) {
            Some(ctx) => {
                if state.verbose {
                    println!("Labeling {} as {}", mp, ctx);
                }
                state.set_inode_selinux(root_inode_num, Some(&ctx))?;
            }
            None => {
                state.fail_or_warn(&format!("cannot lookup security context for {}", mp))?;
            }
        }
    }

    state.update_free();
    state.queue_sb();

    if let Some(ref mut blf) = block_list_file {
        let dir_prefix = dir.as_deref().unwrap_or("");
        // Emit entries in LIFO allocation order
        for alloc in state.saved_allocations.iter().rev() {
            let fname = alloc.filename.as_deref().unwrap_or("");
            if !dir_prefix.is_empty() && fname.starts_with(dir_prefix) {
                write!(blf, "{}{}", mp, &fname[dir_prefix.len()..])
                    .map_err(|e| format!("block_list_file write error: {}", e))?;
            } else {
                write!(blf, "{}", fname)
                    .map_err(|e| format!("block_list_file write error: {}", e))?;
            }
            for reg in &alloc.regions {
                if reg.len == 1 {
                    write!(blf, " {}", reg.block)
                        .map_err(|e| format!("block_list_file write error: {}", e))?;
                } else {
                    write!(blf, " {}-{}", reg.block, reg.block + reg.len - 1)
                        .map_err(|e| format!("block_list_file write error: {}", e))?;
                }
            }
            #[cfg(windows)]
            write!(blf, "\r\n").map_err(|e| format!("block_list_file write error: {}", e))?;
            #[cfg(not(windows))]
            writeln!(blf).map_err(|e| format!("block_list_file write error: {}", e))?;
        }
    }

    let (inodes_used, inodes_total, blocks_used, blocks_total) = {
        let sb = state.sb.borrow();
        let ic = read_u32_le(&sb, sb_off::S_INODES_COUNT);
        let fi = read_u32_le(&sb, sb_off::S_FREE_INODES_COUNT);
        let bc = read_u32_le(&sb, sb_off::S_BLOCKS_COUNT_LO);
        let fb = read_u32_le(&sb, sb_off::S_FREE_BLOCKS_COUNT_LO);
        (ic - fi, ic, bc - fb, bc)
    };
    println!(
        "Created filesystem with {}/{} inodes and {}/{} blocks",
        inodes_used, inodes_total, blocks_used, blocks_total
    );

    if opts.wipe {
        #[cfg(target_os = "linux")]
        {
            // Wipe only applies to block devices on Linux; regular files are unaffected.
        }
    }

    match output {
        OutputTarget::File(file) => {
            state
                .sparse_file
                .write_to_file(file, opts.gzip, opts.sparse, opts.crc)
                .map_err(|e| format!("failed to write ext4 image: {}", e))?;
        }
        OutputTarget::Stdout => {
            let stdout = io::stdout();
            let handle = stdout.lock();
            state
                .sparse_file
                .write_to_stream(handle, opts.gzip, opts.sparse, opts.crc)
                .map_err(|e| format!("failed to write ext4 image: {}", e))?;
        }
    }

    let _ = state.info.inodes;
    Ok(())
}
