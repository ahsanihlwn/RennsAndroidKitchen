use std::cell::RefCell;
use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom};
use std::rc::Rc;

use crc32fast::Hasher;

use super::{
    SparseFile, CHUNK_HEADER_LEN, CHUNK_TYPE_CRC32, CHUNK_TYPE_DONT_CARE, CHUNK_TYPE_FILL,
    CHUNK_TYPE_RAW, SPARSE_HEADER_LEN, SPARSE_HEADER_MAGIC, SPARSE_HEADER_MAJOR_VER,
};

impl SparseFile {
    pub fn import_file(mut file: File, verbose: bool, crc: bool) -> io::Result<Self> {
        let mut hdr = [0u8; 28];
        file.read_exact(&mut hdr)?;

        let magic = u32::from_le_bytes([hdr[0], hdr[1], hdr[2], hdr[3]]);
        let major = u16::from_le_bytes([hdr[4], hdr[5]]);
        let file_hdr_sz = u16::from_le_bytes([hdr[8], hdr[9]]);
        let chunk_hdr_sz = u16::from_le_bytes([hdr[10], hdr[11]]);
        let blk_sz = u32::from_le_bytes([hdr[12], hdr[13], hdr[14], hdr[15]]);
        let total_blks = u32::from_le_bytes([hdr[16], hdr[17], hdr[18], hdr[19]]);
        let total_chunks = u32::from_le_bytes([hdr[20], hdr[21], hdr[22], hdr[23]]);

        if magic != SPARSE_HEADER_MAGIC {
            if verbose {
                eprintln!("Invalid sparse file format at header magic");
            }
            return Err(io::Error::new(io::ErrorKind::InvalidData, "invalid sparse magic"));
        }
        if major != SPARSE_HEADER_MAJOR_VER {
            if verbose {
                eprintln!("Invalid sparse file format at header major version");
            }
            return Err(io::Error::new(io::ErrorKind::InvalidData, "unsupported sparse major version"));
        }
        if file_hdr_sz < SPARSE_HEADER_LEN || chunk_hdr_sz < CHUNK_HEADER_LEN {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "invalid sparse header size"));
        }

        if file_hdr_sz > SPARSE_HEADER_LEN {
            file.seek(SeekFrom::Current((file_hdr_sz - SPARSE_HEADER_LEN) as i64))?;
        }

        let total_len = total_blks as i64 * blk_sz as i64;
        let mut sf = SparseFile::new(blk_sz, total_len);
        sf.verbose = verbose;

        let shared_file = Rc::new(RefCell::new(file));
        let mut hasher = if crc { Some(Hasher::new()) } else { None };
        let mut cur_block = 0u32;
        let mut buf = if crc { vec![0u8; 1024 * 1024] } else { Vec::new() };

        for _ in 0..total_chunks {
            let mut chdr = [0u8; 12];
            {
                let mut f = shared_file.borrow_mut();
                f.read_exact(&mut chdr)?;
                if chunk_hdr_sz > CHUNK_HEADER_LEN {
                    f.seek(SeekFrom::Current((chunk_hdr_sz - CHUNK_HEADER_LEN) as i64))?;
                }
            }

            let chunk_type = u16::from_le_bytes([chdr[0], chdr[1]]);
            let chunk_sz = u32::from_le_bytes([chdr[4], chdr[5], chdr[6], chdr[7]]);
            let total_sz = u32::from_le_bytes([chdr[8], chdr[9], chdr[10], chdr[11]]);
            let chunk_data_size = total_sz.checked_sub(chunk_hdr_sz as u32).ok_or_else(|| {
                io::Error::new(io::ErrorKind::InvalidData, "invalid chunk total_sz")
            })?;
            let offset = shared_file.borrow_mut().stream_position()?;

            match chunk_type {
                CHUNK_TYPE_RAW => {
                    let len = chunk_sz * blk_sz;
                    if chunk_data_size % blk_sz != 0 || chunk_data_size / blk_sz != chunk_sz {
                        return Err(io::Error::new(io::ErrorKind::InvalidData, "invalid raw chunk size"));
                    }
                    sf.add_open_file(Rc::clone(&shared_file), offset, len, cur_block);
                    let mut f = shared_file.borrow_mut();
                    if let Some(ref mut h) = hasher {
                        let mut rem = len as usize;
                        while rem > 0 {
                            let c = rem.min(buf.len());
                            f.read_exact(&mut buf[..c])?;
                            h.update(&buf[..c]);
                            rem -= c;
                        }
                    } else {
                        f.seek(SeekFrom::Current(len as i64))?;
                    }
                    cur_block += chunk_sz;
                }
                CHUNK_TYPE_FILL => {
                    if chunk_data_size != 4 {
                        return Err(io::Error::new(io::ErrorKind::InvalidData, "invalid fill chunk size"));
                    }
                    let mut val_bytes = [0u8; 4];
                    shared_file.borrow_mut().read_exact(&mut val_bytes)?;
                    let fill_val = u32::from_le_bytes(val_bytes);
                    let len = chunk_sz * blk_sz;
                    sf.add_fill(fill_val, len, cur_block);
                    if let Some(ref mut h) = hasher {
                        for chunk in buf.chunks_exact_mut(4) {
                            chunk.copy_from_slice(&val_bytes);
                        }
                        let mut rem = len as usize;
                        while rem > 0 {
                            let c = rem.min(buf.len());
                            h.update(&buf[..c]);
                            rem -= c;
                        }
                    }
                    cur_block += chunk_sz;
                }
                CHUNK_TYPE_DONT_CARE => {
                    if chunk_data_size != 0 {
                        return Err(io::Error::new(io::ErrorKind::InvalidData, "invalid skip chunk size"));
                    }
                    if let Some(ref mut h) = hasher {
                        buf.fill(0);
                        let mut rem = chunk_sz as u64 * blk_sz as u64;
                        while rem > 0 {
                            let c = (rem as usize).min(buf.len());
                            h.update(&buf[..c]);
                            rem -= c as u64;
                        }
                    }
                    cur_block += chunk_sz;
                }
                CHUNK_TYPE_CRC32 => {
                    if chunk_data_size != 4 {
                        return Err(io::Error::new(io::ErrorKind::InvalidData, "invalid crc chunk size"));
                    }
                    let mut crc_bytes = [0u8; 4];
                    shared_file.borrow_mut().read_exact(&mut crc_bytes)?;
                    let file_crc = u32::from_le_bytes(crc_bytes);
                    if let Some(h) = hasher.take() {
                        if file_crc != h.finalize() {
                            return Err(io::Error::new(io::ErrorKind::InvalidData, "crc32 mismatch"));
                        }
                    }
                }
                _ => {
                    if verbose {
                        eprintln!("unknown block {:04X} at {}", chunk_type, offset);
                    }
                }
            }
        }

        if total_blks != cur_block {
            return Err(io::Error::new(io::ErrorKind::InvalidData, "block count mismatch"));
        }

        Ok(sf)
    }

    pub fn read_normal_file(mut file: File, block_size: u32) -> io::Result<Self> {
        let len = file.seek(SeekFrom::End(0))? as i64;
        file.seek(SeekFrom::Start(0))?;

        let mut sf = SparseFile::new(block_size, len);
        let shared_file = Rc::new(RefCell::new(file));
        let mut buf = vec![0u8; block_size as usize];
        let mut remain = len as u64;
        let mut offset = 0u64;
        let mut block = 0u32;

        while remain > 0 {
            let to_read = remain.min(block_size as u64) as usize;
            shared_file.borrow_mut().read_exact(&mut buf[..to_read])?;

            let is_fill = if to_read == block_size as usize {
                let first = &buf[0..4];
                buf[4..].chunks_exact(4).all(|c| c == first)
            } else {
                false
            };

            if is_fill {
                let val = u32::from_le_bytes([buf[0], buf[1], buf[2], buf[3]]);
                sf.add_fill(val, to_read as u32, block);
            } else {
                sf.add_open_file(Rc::clone(&shared_file), offset, to_read as u32, block);
            }

            remain -= to_read as u64;
            offset += to_read as u64;
            block += 1;
        }

        Ok(sf)
    }
}
