//! Pure-Rust implementation of the Android sparse image format (`libsparse`).

pub mod crc32;
pub mod format;
pub mod reader;
pub mod split;
pub mod writer;

pub use crc32::{sparse_crc32, sparse_crc32_fill, sparse_crc32_zeros};
pub use format::{
    ChunkHeader, ChunkType, FileHeader, SparseError, CHUNK_HEADER_SIZE, DEFAULT_BLOCK_SIZE,
    FILE_HEADER_SIZE, MAJOR_VERSION, MINOR_VERSION, SPARSE_HEADER_MAGIC,
};
pub use reader::{is_sparse_image, unsparse, ParsedChunk, SparseReader};
pub use split::{split_raw, split_sparse, SplitChunk, SplitPart};
pub use writer::SparseImage;

#[cfg(test)]
mod tests {
    use std::io::{Cursor, Read, Seek, SeekFrom};
    use std::num::NonZeroU32;

    use super::*;

    fn write_split_sparse(part: &SplitPart, src_sparse: &[u8]) -> Vec<u8> {
        let mut out = Vec::new();
        out.extend_from_slice(&part.header.to_bytes());
        for c in &part.chunks {
            out.extend_from_slice(&c.header.to_bytes());
            if c.data_size > 0 {
                let start = c.input_offset as usize;
                let end = start + c.data_size as usize;
                out.extend_from_slice(&src_sparse[start..end]);
            }
        }
        out
    }

    #[test]
    fn test_overlapping_writes_and_unsparse_with_crc() {
        let bs = DEFAULT_BLOCK_SIZE;
        let total_blocks = 12u32;
        let mut expected = vec![0u8; total_blocks as usize * bs.get() as usize];

        let mut img = SparseImage::new(bs, total_blocks).unwrap();

        // Fill blocks 1..5 with 0x11223344.
        img.add_fill_blocks(1, 4, 0x1122_3344).unwrap();
        for blk in 1..5 {
            for w in expected[blk * 4096..(blk + 1) * 4096].chunks_exact_mut(4) {
                w.copy_from_slice(&0x1122_3344u32.to_le_bytes());
            }
        }

        // Write raw data overlapping blocks 2..4.
        let mut raw_2_4 = vec![0u8; 2 * 4096];
        for (i, b) in raw_2_4.iter_mut().enumerate() {
            *b = ((i * 17 + 3) & 0xff) as u8;
        }
        img.add_raw_blocks(2, &raw_2_4).unwrap();
        expected[2 * 4096..4 * 4096].copy_from_slice(&raw_2_4);

        // Mark block 3 as don't-care (matches zeros in expected).
        img.add_dont_care(3, 1).unwrap();
        expected[3 * 4096..4 * 4096].fill(0);

        // Write adjacent raw block 4..5 and also raw uniform block 7..8.
        let mut raw_4_5 = vec![0u8; 4096];
        for (i, b) in raw_4_5.iter_mut().enumerate() {
            *b = ((i * 31 + 7) & 0xff) as u8;
        }
        img.add_raw_blocks(4, &raw_4_5).unwrap();
        expected[4 * 4096..5 * 4096].copy_from_slice(&raw_4_5);

        let mut uniform_7 = vec![0u8; 4096];
        for w in uniform_7.chunks_exact_mut(4) {
            w.copy_from_slice(&0xcafe_babeu32.to_le_bytes());
        }
        img.add_raw_blocks(7, &uniform_7).unwrap();
        expected[7 * 4096..8 * 4096].copy_from_slice(&uniform_7);

        let mut sparse_buf = Vec::new();
        let written_hdr = img.write_to(&mut sparse_buf, true).unwrap();
        assert_eq!(written_hdr.total_blocks, total_blocks);

        let mut cur = Cursor::new(&sparse_buf);
        assert!(is_sparse_image(&mut cur).unwrap());
        assert_eq!(cur.stream_position().unwrap(), 0);

        let mut out_raw = Cursor::new(Vec::new());
        unsparse(&mut cur, &mut out_raw, true).unwrap();
        assert_eq!(out_raw.into_inner(), expected);
    }

    #[test]
    fn test_crc_mismatch_detected() {
        let mut img = SparseImage::new(DEFAULT_BLOCK_SIZE, 2).unwrap();
        img.add_fill_blocks(0, 2, 0xaaaa_bbbb).unwrap();
        let mut sparse_buf = Vec::new();
        img.write_to(&mut sparse_buf, true).unwrap();

        // Corrupt last byte (the stored CRC32).
        let last = sparse_buf.len() - 1;
        sparse_buf[last] ^= 0xff;

        let mut out = Cursor::new(Vec::new());
        let err = unsparse(Cursor::new(&sparse_buf), &mut out, true).unwrap_err();
        assert!(matches!(err, SparseError::ChecksumMismatch { .. }));
    }

    #[test]
    fn test_split_sparse_and_reassemble() {
        let bs = DEFAULT_BLOCK_SIZE;
        let total_blocks = 20u32;
        let mut raw = vec![0u8; total_blocks as usize * bs.get() as usize];
        for (i, b) in raw[4096..10 * 4096].iter_mut().enumerate() {
            *b = (i % 251) as u8;
        }
        for w in raw[12 * 4096..15 * 4096].chunks_exact_mut(4) {
            w.copy_from_slice(&0x5566_7788u32.to_le_bytes());
        }

        let img = SparseImage::from_raw_with_options(
            &mut Cursor::new(&raw),
            bs,
            raw.len() as u64,
            true,
        )
        .unwrap();
        let mut sparse_buf = Vec::new();
        let hdr = img.write_to(&mut sparse_buf, true).unwrap();

        // Collect ChunkHeaders via SparseReader.
        let mut sr = SparseReader::new(Cursor::new(&sparse_buf), false).unwrap();
        let mut chunks = Vec::new();
        while let Some(c) = sr.next_chunk().unwrap() {
            match c {
                ParsedChunk::Raw { header, .. } => {
                    sr.skip_raw_data().unwrap();
                    chunks.push(header);
                }
                ParsedChunk::Fill { header, .. }
                | ParsedChunk::DontCare { header }
                | ParsedChunk::Crc32 { header, .. } => chunks.push(header),
            }
        }

        let max_part = 16 * 1024;
        let parts = split_sparse(&hdr, &chunks, max_part).unwrap();
        assert!(parts.len() > 1);
        for p in &parts {
            assert!(p.sparse_size() <= max_part);
            assert!(p.chunks.iter().any(|c| c.header.chunk_type != ChunkType::DontCare));
        }

        let mut reassembled = Cursor::new(vec![0u8; hdr.expanded_size() as usize]);
        for p in &parts {
            let part_bytes = write_split_sparse(p, &sparse_buf);
            reassembled.seek(SeekFrom::Start(0)).unwrap();
            unsparse(Cursor::new(&part_bytes), &mut reassembled, false).unwrap();
        }
        assert_eq!(reassembled.into_inner(), raw);
    }

    #[test]
    fn test_split_raw_and_reassemble() {
        let bs = NonZeroU32::new(4096).unwrap();
        let raw_size = 10 * 4096 + 123;
        let mut raw = vec![0u8; raw_size];
        for (i, b) in raw.iter_mut().enumerate() {
            *b = ((i * 13 + 5) & 0xff) as u8;
        }
        let max_part = 16 * 1024;
        let parts = split_raw(raw_size as u64, bs, max_part).unwrap();
        assert!(parts.len() >= 3);

        let padded_len = (raw_size as u64).div_ceil(4096) as usize * 4096;
        let mut expected_padded = raw.clone();
        expected_padded.resize(padded_len, 0);

        let mut reassembled = Cursor::new(vec![0u8; padded_len]);
        for p in &parts {
            assert!(p.sparse_size() <= max_part);
            let mut part_bytes = Vec::new();
            part_bytes.extend_from_slice(&p.header.to_bytes());
            for c in &p.chunks {
                part_bytes.extend_from_slice(&c.header.to_bytes());
                if c.data_size > 0 {
                    let start = c.input_offset as usize;
                    let end = (start + c.data_size as usize).min(raw.len());
                    part_bytes.extend_from_slice(&raw[start..end]);
                    let pad = c.data_size as usize - (end - start);
                    if pad > 0 {
                        part_bytes.extend(std::iter::repeat_n(0u8, pad));
                    }
                }
            }
            reassembled.seek(SeekFrom::Start(0)).unwrap();
            unsparse(Cursor::new(&part_bytes), &mut reassembled, false).unwrap();
        }
        assert_eq!(reassembled.into_inner(), expected_padded);
    }

    #[test]
    fn test_from_raw_and_non_sparse_detection() {
        let mut not_sparse = Cursor::new(b"not a sparse file at all....".to_vec());
        assert!(!is_sparse_image(&mut not_sparse).unwrap());
        let mut buf = Vec::new();
        not_sparse.read_to_end(&mut buf).unwrap();
        assert_eq!(buf.len(), 28);

        let min_size = FILE_HEADER_SIZE as u64 + 2 * CHUNK_HEADER_SIZE as u64 + 4096;
        assert!(matches!(
            split_raw(4096, DEFAULT_BLOCK_SIZE, min_size - 1),
            Err(SparseError::OutputTooSmall)
        ));
    }
}
