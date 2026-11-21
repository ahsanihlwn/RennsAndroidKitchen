use std::cell::RefCell;
use std::rc::Rc;

use crate::ext4::allocator::{allocate_best_fit_regions, allocate_blocks, BlockAllocation, BlockGroup};
use crate::ext4::types::{
    div_round_up_u32, inode_off, write_u16_le, write_u32_le, EXT4_DIND_BLOCK, EXT4_IND_BLOCK,
    EXT4_NDIR_BLOCKS, EXT4_TIND_BLOCK,
};
use crate::sparse::{SharedBuf, SparseFile};

fn indirect_blocks_needed(mut len: u32, blocks_per_ind: u32, blocks_per_dind: u32, blocks_per_tind: u32) -> Result<u32, String> {
    let mut ind = 0u32;
    if len <= EXT4_NDIR_BLOCKS {
        return Ok(ind);
    }
    len -= EXT4_NDIR_BLOCKS;
    ind += div_round_up_u32(len, blocks_per_ind);
    if len <= blocks_per_ind {
        return Ok(ind);
    }
    len -= blocks_per_ind;
    ind += div_round_up_u32(len, blocks_per_dind);
    if len <= blocks_per_dind {
        return Ok(ind);
    }
    len -= blocks_per_dind;
    ind += div_round_up_u32(len, blocks_per_tind);
    if len <= blocks_per_tind {
        return Ok(ind);
    }
    Err("request too large".to_string())
}

fn reserve_indirect_block(alloc: &mut BlockAllocation, len: u32) -> Result<(), String> {
    alloc.reserve_oob_blocks(1).map_err(|_| "failed to reserve oob block")?;
    alloc.advance_blocks(len).map_err(|_| format!("failed to advance {} blocks", len))?;
    Ok(())
}

fn reserve_dindirect_block(alloc: &mut BlockAllocation, mut len: u32, blocks_per_ind: u32) -> Result<(), String> {
    alloc.reserve_oob_blocks(1).map_err(|_| "failed to reserve oob block")?;
    while len > 0 {
        let ind_len = blocks_per_ind.min(len);
        reserve_indirect_block(alloc, ind_len)?;
        len -= ind_len;
    }
    Ok(())
}

fn reserve_tindirect_block(
    alloc: &mut BlockAllocation,
    mut len: u32,
    blocks_per_ind: u32,
    blocks_per_dind: u32,
) -> Result<(), String> {
    alloc.reserve_oob_blocks(1).map_err(|_| "failed to reserve oob block")?;
    while len > 0 {
        let dind_len = blocks_per_dind.min(len);
        reserve_dindirect_block(alloc, dind_len, blocks_per_ind)?;
        len -= dind_len;
    }
    Ok(())
}

fn reserve_all_indirect_blocks(
    alloc: &mut BlockAllocation,
    mut len: u32,
    blocks_per_ind: u32,
    blocks_per_dind: u32,
    blocks_per_tind: u32,
) -> Result<(), String> {
    if len <= EXT4_NDIR_BLOCKS {
        return Ok(());
    }
    len -= EXT4_NDIR_BLOCKS;
    let _ = alloc.advance_blocks(EXT4_NDIR_BLOCKS);

    let ind_len = blocks_per_ind.min(len);
    reserve_indirect_block(alloc, ind_len)?;
    len -= ind_len;
    if len == 0 {
        return Ok(());
    }

    let dind_len = blocks_per_dind.min(len);
    reserve_dindirect_block(alloc, dind_len, blocks_per_ind)?;
    len -= dind_len;
    if len == 0 {
        return Ok(());
    }

    let tind_len = blocks_per_tind.min(len);
    reserve_tindirect_block(alloc, tind_len, blocks_per_ind, blocks_per_dind)?;
    len -= tind_len;
    if len == 0 {
        return Ok(());
    }

    Err(format!("{} blocks remaining", len))
}

fn fill_indirect_block(ind_block: &mut [u8], len: u32, alloc: &BlockAllocation) {
    for i in 0..len {
        let blk = alloc.get_block(i).unwrap_or(u32::MAX);
        write_u32_le(ind_block, (i as usize) * 4, blk);
    }
}

fn fill_dindirect_block(
    dind_block: &mut [u8],
    mut len: u32,
    alloc: &mut BlockAllocation,
    block_size: u32,
    blocks_per_ind: u32,
    sparse_file: &mut SparseFile,
) -> Result<(), String> {
    let mut i = 0usize;
    while len > 0 {
        let ind_blk = alloc.get_oob_block(0).unwrap();
        alloc.advance_oob_blocks(1).map_err(|_| "failed to reserve oob block")?;
        write_u32_le(dind_block, i * 4, ind_blk);

        let mut ind_data = vec![0u8; block_size as usize];
        let ind_len = blocks_per_ind.min(len);
        fill_indirect_block(&mut ind_data, ind_len, alloc);
        sparse_file.add_owned_data(ind_data, ind_blk);

        alloc.advance_blocks(ind_len).map_err(|_| format!("failed to advance {} blocks", ind_len))?;
        len -= ind_len;
        i += 1;
    }
    Ok(())
}

fn fill_tindirect_block(
    tind_block: &mut [u8],
    mut len: u32,
    alloc: &mut BlockAllocation,
    block_size: u32,
    blocks_per_ind: u32,
    blocks_per_dind: u32,
    sparse_file: &mut SparseFile,
) -> Result<(), String> {
    let mut i = 0usize;
    while len > 0 {
        let dind_blk = alloc.get_oob_block(0).unwrap();
        alloc.advance_oob_blocks(1).map_err(|_| "failed to reserve oob block")?;
        write_u32_le(tind_block, i * 4, dind_blk);

        let mut dind_data = vec![0u8; block_size as usize];
        let dind_len = blocks_per_dind.min(len);
        fill_dindirect_block(&mut dind_data, dind_len, alloc, block_size, blocks_per_ind, sparse_file)?;
        sparse_file.add_owned_data(dind_data, dind_blk);

        len -= dind_len;
        i += 1;
    }
    Ok(())
}

fn do_inode_attach_indirect(
    inode_buf: &mut [u8],
    alloc: &mut BlockAllocation,
    block_len: u32,
    block_size: u32,
    blocks_per_ind: u32,
    blocks_per_dind: u32,
    blocks_per_tind: u32,
    sparse_file: &mut SparseFile,
) -> Result<(), String> {
    let mut count = block_len;

    // Direct blocks
    let dir_len = count.min(EXT4_NDIR_BLOCKS);
    for i in 0..dir_len {
        let blk = alloc.get_block(i).unwrap_or(u32::MAX);
        write_u32_le(inode_buf, inode_off::I_BLOCK + (i as usize) * 4, blk);
    }
    alloc.advance_blocks(dir_len).map_err(|_| format!("failed to advance {} blocks", dir_len))?;
    count -= dir_len;

    // Indirect
    if count > 0 {
        let len = count.min(blocks_per_ind);
        let ind_blk = alloc.get_oob_block(0).unwrap();
        write_u32_le(inode_buf, inode_off::I_BLOCK + EXT4_IND_BLOCK * 4, ind_blk);
        alloc.advance_oob_blocks(1).map_err(|_| "failed to advance oob block")?;
        let mut ind_data = vec![0u8; block_size as usize];
        fill_indirect_block(&mut ind_data, len, alloc);
        sparse_file.add_owned_data(ind_data, ind_blk);
        alloc.advance_blocks(len).map_err(|_| format!("failed to advance {} blocks", len))?;
        count -= len;
    }

    // Doubly indirect
    if count > 0 {
        let len = count.min(blocks_per_dind);
        let dind_blk = alloc.get_oob_block(0).unwrap();
        write_u32_le(inode_buf, inode_off::I_BLOCK + EXT4_DIND_BLOCK * 4, dind_blk);
        alloc.advance_oob_blocks(1).map_err(|_| "failed to advance oob block")?;
        let mut dind_data = vec![0u8; block_size as usize];
        fill_dindirect_block(&mut dind_data, len, alloc, block_size, blocks_per_ind, sparse_file)?;
        sparse_file.add_owned_data(dind_data, dind_blk);
        alloc.advance_blocks(len).map_err(|_| format!("failed to advance {} blocks", len))?;
        count -= len;
    }

    // Triply indirect
    if count > 0 {
        let len = count.min(blocks_per_tind);
        let tind_blk = alloc.get_oob_block(0).unwrap();
        write_u32_le(inode_buf, inode_off::I_BLOCK + EXT4_TIND_BLOCK * 4, tind_blk);
        alloc.advance_oob_blocks(1).map_err(|_| "failed to advance oob block")?;
        let mut tind_data = vec![0u8; block_size as usize];
        fill_tindirect_block(
            &mut tind_data,
            len,
            alloc,
            block_size,
            blocks_per_ind,
            blocks_per_dind,
            sparse_file,
        )?;
        sparse_file.add_owned_data(tind_data, tind_blk);
        alloc.advance_blocks(len).map_err(|_| format!("failed to advance {} blocks", len))?;
        count -= len;
    }

    if count > 0 {
        return Err("blocks left after triply-indirect allocation".to_string());
    }

    alloc.rewind();
    Ok(())
}

pub fn inode_allocate_data_indirect(
    inode_buf: &mut [u8],
    len: u32,
    backing_len: u32,
    block_size: u32,
    blocks_per_ind: u32,
    blocks_per_dind: u32,
    blocks_per_tind: u32,
    bgs: &mut [BlockGroup],
    sparse_file: &mut SparseFile,
) -> Result<Option<SharedBuf>, String> {
    let block_len = div_round_up_u32(len, block_size);
    let indirect_len = indirect_blocks_needed(block_len, blocks_per_ind, blocks_per_dind, blocks_per_tind)?;
    let mut alloc = allocate_blocks(bgs, block_len + indirect_len)?;

    reserve_all_indirect_blocks(&mut alloc, block_len, blocks_per_ind, blocks_per_dind, blocks_per_tind)?;
    alloc.rewind();

    let data = if backing_len > 0 {
        if div_round_up_u32(backing_len, block_size) > EXT4_NDIR_BLOCKS {
            return Err(format!("indirect backing larger than {} blocks", EXT4_NDIR_BLOCKS));
        }
        let buf = Rc::new(RefCell::new(vec![0u8; backing_len as usize]));
        let mut ptr = 0usize;
        let mut rem = backing_len;
        while !alloc.at_end() && rem > 0 {
            let (reg_blk, reg_len) = alloc.current_region().unwrap();
            let chunk_len = (reg_len * block_size).min(rem);
            sparse_file.add_shared_data_slice(Rc::clone(&buf), ptr, chunk_len, reg_blk);
            ptr += chunk_len as usize;
            rem -= chunk_len;
            alloc.next_region();
        }
        Some(buf)
    } else {
        None
    };

    alloc.rewind();
    do_inode_attach_indirect(
        inode_buf,
        &mut alloc,
        block_len,
        block_size,
        blocks_per_ind,
        blocks_per_dind,
        blocks_per_tind,
        sparse_file,
    )?;

    Ok(data)
}

pub fn inode_attach_resize(
    inode_buf: &mut [u8],
    alloc: &mut BlockAllocation,
    block_size: u32,
    bg_desc_blocks: u32,
    bg_desc_reserve_blocks: u32,
    blocks_per_ind: u32,
    bgs: &mut [BlockGroup],
    sparse_file: &mut SparseFile,
) -> Result<(), String> {
    let block_len = alloc.total_len();
    if block_len % bg_desc_reserve_blocks != 0 {
        return Err(format!(
            "reserved blocks not a multiple of {}",
            bg_desc_reserve_blocks
        ));
    }
    let superblocks = block_len / bg_desc_reserve_blocks;

    let oob_regs = allocate_best_fit_regions(bgs, 1)?;
    alloc.oob_regions.extend(oob_regs);
    let dind_block = alloc.get_oob_block(0).unwrap();

    let mut dind_block_data = vec![0u8; block_size as usize];
    let mut ind_block_data = vec![0u8; block_size as usize * bg_desc_reserve_blocks as usize];

    for i in 0..bg_desc_reserve_blocks {
        // Unsigned wraparound modulo for reserved GDT block indexing
        let r = i.wrapping_sub(bg_desc_blocks) % bg_desc_reserve_blocks;
        let blk = alloc.get_block(r).unwrap();
        write_u32_le(&mut dind_block_data, (i as usize) * 4, blk);

        for j in 1..superblocks {
            let b = j * bg_desc_reserve_blocks + r;
            let b_blk = alloc.get_block(b).unwrap();
            let idx = (r * blocks_per_ind + j - 1) as usize;
            write_u32_le(&mut ind_block_data, idx * 4, b_blk);
        }
    }

    sparse_file.add_owned_data(dind_block_data, dind_block);
    sparse_file.add_owned_data(ind_block_data, alloc.get_block(0).unwrap());

    let last_block = EXT4_NDIR_BLOCKS
        + blocks_per_ind
        + blocks_per_ind * (bg_desc_reserve_blocks - 1)
        + superblocks
        - 2;

    let blocks = (block_len as u64 + 1) * block_size as u64 / 512;
    let size = last_block as u64 * block_size as u64;

    write_u32_le(inode_buf, inode_off::I_BLOCK + EXT4_DIND_BLOCK * 4, dind_block);
    write_u32_le(inode_buf, inode_off::I_FLAGS, 0);
    write_u32_le(inode_buf, inode_off::I_BLOCKS_LO, blocks as u32);
    write_u16_le(inode_buf, inode_off::L_I_BLOCKS_HIGH, (blocks >> 32) as u16);
    write_u32_le(inode_buf, inode_off::I_SIZE_LO, size as u32);
    write_u32_le(inode_buf, inode_off::I_SIZE_HIGH, (size >> 32) as u32);

    Ok(())
}
