use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

use crate::ext4::allocator::{allocate_single_block, BlockGroup};
use crate::ext4::types::{
    inode_off, read_u32_le, write_u16_le, write_u32_le, EXT4_GOOD_OLD_INODE_SIZE,
    EXT4_INODE_STRUCT_SIZE, EXT4_XATTR_ENTRY_SIZE, EXT4_XATTR_HEADER_SIZE, EXT4_XATTR_INDEX_SECURITY,
    EXT4_XATTR_MAGIC, VFS_CAP_FLAGS_EFFECTIVE, VFS_CAP_REVISION_2,
};
use crate::sparse::{SharedBuf, SparseFile};

#[inline]
const fn xattr_len(name_len: usize) -> usize {
    (name_len + 3 + EXT4_XATTR_ENTRY_SIZE) & !3
}

#[inline]
const fn xattr_size(val_len: usize) -> usize {
    (val_len + 3) & !3
}

fn xattr_free_space(buf: &[u8], first_off: usize, block_end: usize) -> Result<(usize, usize), String> {
    let mut end = block_end - 4; // 4 null bytes
    let mut off = first_off;
    while off < end {
        let first_word = read_u32_le(buf, off);
        if first_word == 0 {
            break;
        }
        let name_len = buf[off] as usize;
        let val_size = read_u32_le(buf, off + 8) as usize;
        end -= xattr_size(val_size);
        off += xattr_len(name_len);
    }
    if off > end {
        return Err("unexpected read beyond end of xattr space".to_string());
    }
    Ok((off, end - off))
}

fn xattr_assert_sane(buf: &[u8], first_off: usize) -> Result<(), String> {
    let mut off = first_off;
    while read_u32_le(buf, off) != 0 {
        let name_len = buf[off] as usize;
        let next_off = off + xattr_len(name_len);
        if read_u32_le(buf, next_off) == 0 {
            return Ok(());
        }
        let cmp_idx = (buf[next_off + 1] as i32) - (buf[off + 1] as i32);
        let cmp = if cmp_idx != 0 {
            cmp_idx
        } else {
            let next_len = buf[next_off] as usize;
            let cmp_len = (next_len as i32) - (name_len as i32);
            if cmp_len != 0 {
                cmp_len
            } else {
                let a = &buf[next_off + 16..next_off + 16 + next_len];
                let b = &buf[off + 16..off + 16 + next_len];
                match a.cmp(b) {
                    std::cmp::Ordering::Less => -1,
                    std::cmp::Ordering::Equal => 0,
                    std::cmp::Ordering::Greater => 1,
                }
            }
        };
        if cmp < 0 {
            return Err("BUG: extended attributes are not sorted".to_string());
        }
        if cmp == 0 {
            return Err("BUG: duplicate extended attributes detected".to_string());
        }
        off = next_off;
    }
    Ok(())
}

fn xattr_addto_range(
    buf: &mut [u8],
    block_start: usize,
    block_end: usize,
    first_off: usize,
    name_index: u8,
    name: &[u8],
    value: &[u8],
) -> Result<Option<usize>, String> {
    if name.len() > 255 {
        return Ok(None);
    }
    let (new_entry_off, avail) = xattr_free_space(buf, first_off, block_end)?;
    let entry_bytes = xattr_len(name.len());
    let val_bytes = xattr_size(value.len());
    if entry_bytes + val_bytes > avail {
        return Ok(None);
    }

    buf[new_entry_off..new_entry_off + entry_bytes].fill(0);
    buf[new_entry_off] = name.len() as u8;
    buf[new_entry_off + 1] = name_index;
    buf[new_entry_off + 16..new_entry_off + 16 + name.len()].copy_from_slice(name);
    write_u32_le(buf, new_entry_off + 4, 0); // e_value_block
    write_u32_le(buf, new_entry_off + 8, value.len() as u32); // e_value_size

    let val_off = new_entry_off + avail - val_bytes;
    let e_value_offs = (val_off - block_start) as u16;
    write_u16_le(buf, new_entry_off + 2, e_value_offs);

    buf[val_off..val_off + val_bytes].fill(0);
    buf[val_off..val_off + value.len()].copy_from_slice(value);

    xattr_assert_sane(buf, first_off)?;
    Ok(Some(new_entry_off))
}

fn ext4_xattr_hash_entry(buf: &mut [u8], entry_off: usize) {
    const NAME_HASH_SHIFT: u32 = 5;
    const VALUE_HASH_SHIFT: u32 = 16;

    let mut hash = 0u32;
    let name_len = buf[entry_off] as usize;
    for i in 0..name_len {
        let ch = buf[entry_off + 16 + i] as i8 as u32;
        hash = (hash << NAME_HASH_SHIFT) ^ (hash >> (32 - NAME_HASH_SHIFT)) ^ ch;
    }

    let val_block = read_u32_le(buf, entry_off + 4);
    let val_size = read_u32_le(buf, entry_off + 8);
    if val_block == 0 && val_size != 0 {
        let val_off = u16::from_le_bytes([buf[entry_off + 2], buf[entry_off + 3]]) as usize;
        let words = ((val_size + 3) >> 2) as usize;
        for i in 0..words {
            let word = read_u32_le(buf, val_off + i * 4);
            hash = (hash << VALUE_HASH_SHIFT) ^ (hash >> (32 - VALUE_HASH_SHIFT)) ^ word;
        }
    }

    write_u32_le(buf, entry_off + 12, hash);
}

#[allow(clippy::too_many_arguments)]
pub fn xattr_add(
    inode_num: u32,
    inode_buf: &mut [u8],
    inode_size: u32,
    block_size: u32,
    name_index: u8,
    name: &[u8],
    value: &[u8],
    bgs: &mut [BlockGroup],
    xattr_blocks: &mut HashMap<u32, SharedBuf>,
    sparse_file: &mut SparseFile,
) -> Result<(), String> {
    let ibody_off = EXT4_INODE_STRUCT_SIZE;
    let first_off = ibody_off + 4;
    if let Some(_) = xattr_addto_range(
        inode_buf,
        first_off,
        inode_size as usize,
        first_off,
        name_index,
        name,
        value,
    )? {
        write_u32_le(inode_buf, ibody_off, EXT4_XATTR_MAGIC);
        let extra = (EXT4_INODE_STRUCT_SIZE as u16) - EXT4_GOOD_OLD_INODE_SIZE;
        write_u16_le(inode_buf, inode_off::I_EXTRA_ISIZE, extra);
        return Ok(());
    }

    let xattr_block = if let Some(existing) = xattr_blocks.get(&inode_num) {
        Rc::clone(existing)
    } else {
        let block_num = allocate_single_block(bgs)
            .ok_or_else(|| format!("get_xattr: failed to allocate block of size {}", block_size))?;
        let mut data = vec![0u8; block_size as usize];
        write_u32_le(&mut data, 0, EXT4_XATTR_MAGIC);
        write_u32_le(&mut data, 4, 1); // h_refcount
        write_u32_le(&mut data, 8, 1); // h_blocks

        let i_blocks = read_u32_le(inode_buf, inode_off::I_BLOCKS_LO) + (block_size / 512);
        write_u32_le(inode_buf, inode_off::I_BLOCKS_LO, i_blocks);
        write_u32_le(inode_buf, inode_off::I_FILE_ACL_LO, block_num);

        let shared = Rc::new(RefCell::new(data));
        sparse_file.add_shared_data(Rc::clone(&shared), block_size, block_num);
        xattr_blocks.insert(inode_num, Rc::clone(&shared));
        shared
    };

    let mut blk_mut = xattr_block.borrow_mut();
    let entry_off = xattr_addto_range(
        &mut blk_mut,
        0,
        block_size as usize,
        EXT4_XATTR_HEADER_SIZE,
        name_index,
        name,
        value,
    )?
    .ok_or_else(|| "failed to add xattr to external block".to_string())?;
    ext4_xattr_hash_entry(&mut blk_mut, entry_off);

    Ok(())
}

pub fn inode_set_selinux(
    inode_num: u32,
    inode_buf: &mut [u8],
    inode_size: u32,
    block_size: u32,
    secon: &str,
    bgs: &mut [BlockGroup],
    xattr_blocks: &mut HashMap<u32, SharedBuf>,
    sparse_file: &mut SparseFile,
) -> Result<(), String> {
    let mut val = Vec::with_capacity(secon.len() + 1);
    val.extend_from_slice(secon.as_bytes());
    val.push(0);
    xattr_add(
        inode_num,
        inode_buf,
        inode_size,
        block_size,
        EXT4_XATTR_INDEX_SECURITY,
        b"selinux",
        &val,
        bgs,
        xattr_blocks,
        sparse_file,
    )
}

pub fn inode_set_capabilities(
    inode_num: u32,
    inode_buf: &mut [u8],
    inode_size: u32,
    block_size: u32,
    capabilities: u64,
    bgs: &mut [BlockGroup],
    xattr_blocks: &mut HashMap<u32, SharedBuf>,
    sparse_file: &mut SparseFile,
) -> Result<(), String> {
    if capabilities == 0 {
        return Ok(());
    }
    let mut cap_data = [0u8; 20];
    write_u32_le(&mut cap_data, 0, VFS_CAP_REVISION_2 | VFS_CAP_FLAGS_EFFECTIVE);
    write_u32_le(&mut cap_data, 4, (capabilities & 0xFFFF_FFFF) as u32);
    write_u32_le(&mut cap_data, 8, 0);
    write_u32_le(&mut cap_data, 12, (capabilities >> 32) as u32);
    write_u32_le(&mut cap_data, 16, 0);

    xattr_add(
        inode_num,
        inode_buf,
        inode_size,
        block_size,
        EXT4_XATTR_INDEX_SECURITY,
        b"capability",
        &cap_data,
        bgs,
        xattr_blocks,
        sparse_file,
    )
}
