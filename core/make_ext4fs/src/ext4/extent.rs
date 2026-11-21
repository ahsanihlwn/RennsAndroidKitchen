use std::cell::RefCell;
use std::path::Path;
use std::rc::Rc;

use crate::ext4::allocator::{allocate_blocks, BlockAllocation, BlockGroup};
use crate::ext4::types::{
    div_round_up_u64, inode_off, read_u32_le, write_u16_le, write_u32_le, EXT4_EXTENTS_FL,
    EXT4_EXT_MAGIC,
};
use crate::sparse::{SharedBuf, SparseFile};

fn write_extent_header(buf: &mut [u8], entries: u16, max: u16, depth: u16) {
    write_u16_le(buf, 0, EXT4_EXT_MAGIC);
    write_u16_le(buf, 2, entries);
    write_u16_le(buf, 4, max);
    write_u16_le(buf, 6, depth);
    write_u32_le(buf, 8, 0);
}

fn write_extent(buf: &mut [u8], ee_block: u32, ee_len: u16, ee_start_lo: u32) {
    write_u32_le(buf, 0, ee_block);
    write_u16_le(buf, 4, ee_len);
    write_u16_le(buf, 6, 0);
    write_u32_le(buf, 8, ee_start_lo);
}

fn do_inode_allocate_extents(
    inode_buf: &mut [u8],
    len: u64,
    block_size: u32,
    bgs: &mut [BlockGroup],
    sparse_file: &mut SparseFile,
) -> Result<BlockAllocation, String> {
    let mut block_len = div_round_up_u64(len, block_size as u64) as u32;
    let mut alloc = allocate_blocks(bgs, block_len + 1)?;

    let allocation_len = alloc.num_regions();
    let mut extent_block = 0u32;
    if allocation_len <= 3 {
        alloc.reduce_allocation(1, bgs);
    } else {
        alloc
            .reserve_oob_blocks(1)
            .map_err(|_| "failed to reserve oob extent block".to_string())?;
        extent_block = alloc.get_oob_block(0).unwrap();
    }

    if extent_block == 0 {
        let i_block = &mut inode_buf[inode_off::I_BLOCK..inode_off::I_BLOCK + 60];
        write_extent_header(&mut i_block[0..12], allocation_len as u16, 3, 0);

        let mut file_block = 0u32;
        for (idx, reg) in alloc.regions.iter().enumerate() {
            let off = 12 + idx * 12;
            write_extent(&mut i_block[off..off + 12], file_block, reg.len as u16, reg.block);
            file_block += reg.len;
        }
    } else {
        let i_block = &mut inode_buf[inode_off::I_BLOCK..inode_off::I_BLOCK + 60];
        write_extent_header(&mut i_block[0..12], 1, 3, 1);
        // ext4_extent_idx
        write_u32_le(&mut i_block[12..24], 0, 0); // ei_block
        write_u32_le(&mut i_block[12..24], 4, extent_block); // ei_leaf_lo
        write_u16_le(&mut i_block[12..24], 8, 0); // ei_leaf_hi
        write_u16_le(&mut i_block[12..24], 10, 0); // ei_unused

        let eh_max = ((block_size - 12) / 12) as usize;
        if allocation_len > eh_max {
            return Err(format!(
                "File size {} is too big to fit in a single extent block",
                len
            ));
        }

        let mut ext_data = vec![0u8; block_size as usize];
        write_extent_header(&mut ext_data[0..12], allocation_len as u16, eh_max as u16, 0);

        let mut file_block = 0u32;
        for (idx, reg) in alloc.regions.iter().enumerate() {
            let off = 12 + idx * 12;
            write_extent(
                &mut ext_data[off..off + 12],
                file_block,
                reg.len as u16,
                reg.block,
            );
            file_block += reg.len;
        }
        sparse_file.add_owned_data(ext_data, extent_block);
    }

    if extent_block != 0 {
        block_len += 1;
    }

    let blocks = block_len as u64 * block_size as u64 / 512;
    let flags = read_u32_le(inode_buf, inode_off::I_FLAGS) | EXT4_EXTENTS_FL;
    write_u32_le(inode_buf, inode_off::I_FLAGS, flags);
    write_u32_le(inode_buf, inode_off::I_SIZE_LO, len as u32);
    write_u32_le(inode_buf, inode_off::I_SIZE_HIGH, (len >> 32) as u32);
    write_u32_le(inode_buf, inode_off::I_BLOCKS_LO, blocks as u32);
    write_u16_le(inode_buf, inode_off::L_I_BLOCKS_HIGH, (blocks >> 32) as u16);

    alloc.rewind();
    Ok(alloc)
}

pub fn inode_allocate_data_extents(
    inode_buf: &mut [u8],
    len: u64,
    backing_len: u64,
    block_size: u32,
    bgs: &mut [BlockGroup],
    sparse_file: &mut SparseFile,
) -> Result<Option<SharedBuf>, String> {
    let alloc = do_inode_allocate_extents(inode_buf, len, block_size, bgs, sparse_file)?;
    if backing_len == 0 {
        return Ok(None);
    }

    let data = Rc::new(RefCell::new(vec![0u8; backing_len as usize]));
    let mut ptr = 0usize;
    let mut rem = backing_len;
    for reg in &alloc.regions {
        if rem == 0 {
            break;
        }
        let chunk_len = ((reg.len as u64) * (block_size as u64)).min(rem) as u32;
        sparse_file.add_shared_data_slice(Rc::clone(&data), ptr, chunk_len, reg.block);
        ptr += chunk_len as usize;
        rem -= chunk_len as u64;
    }

    Ok(Some(data))
}

pub fn inode_allocate_file_extents(
    inode_buf: &mut [u8],
    len: u64,
    filepath: &Path,
    block_size: u32,
    bgs: &mut [BlockGroup],
    sparse_file: &mut SparseFile,
) -> Result<BlockAllocation, String> {
    let alloc = do_inode_allocate_extents(inode_buf, len, block_size, bgs, sparse_file)?;

    let mut offset = 0u64;
    let mut rem = len;
    for reg in &alloc.regions {
        if rem == 0 {
            break;
        }
        let chunk_len = ((reg.len as u64) * (block_size as u64)).min(rem) as u32;
        sparse_file.add_file(filepath.to_path_buf(), offset, chunk_len, reg.block);
        offset += chunk_len as u64;
        rem -= chunk_len as u64;
    }

    Ok(alloc)
}
