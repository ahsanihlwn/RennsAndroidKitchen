use std::collections::HashMap;
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use sdatimg::blockimgdiff::{BLOCK_SIZE, BlockImageDiff, BlockOptions, DataImage, EmptyImage, Image};
use sdatimg::sparse_img::{
    CHUNK_HEADER_SIZE, CHUNK_TYPE_DONT_CARE, CHUNK_TYPE_FILL, CHUNK_TYPE_RAW, SPARSE_HEADER_MAGIC,
    SPARSE_HEADER_SIZE,
};
use sdatimg::{
    Img2SdatOptions, InputFormat, Sdat2ImgOptions, SparseImage, img2sdat,
    parse_transfer_list_file, raw_to_sparse, sdat2img,
};

struct TempDir(PathBuf);

impl TempDir {
    fn new(name: &str) -> Self {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let pid = std::process::id();
        let dir = std::env::temp_dir().join(format!("sdatimg-test-{name}-{pid}-{nanos}"));
        fs::create_dir_all(&dir).unwrap();
        Self(dir)
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

enum SynthChunk {
    Raw(Vec<Vec<u8>>),
    Fill { blocks: u32, pattern: [u8; 4] },
    DontCare { blocks: u32 },
}

fn write_synthetic_sparse(path: &Path, chunks: &[SynthChunk]) -> Vec<u8> {
    let total_blocks: u32 = chunks
        .iter()
        .map(|c| match c {
            SynthChunk::Raw(blks) => blks.len() as u32,
            SynthChunk::Fill { blocks, .. } => *blocks,
            SynthChunk::DontCare { blocks } => *blocks,
        })
        .sum();
    let total_chunks = chunks.len() as u32;

    let mut f = File::create(path).unwrap();
    let mut hdr = [0u8; 28];
    hdr[0..4].copy_from_slice(&SPARSE_HEADER_MAGIC.to_le_bytes());
    hdr[4..6].copy_from_slice(&1u16.to_le_bytes());
    hdr[6..8].copy_from_slice(&0u16.to_le_bytes());
    hdr[8..10].copy_from_slice(&SPARSE_HEADER_SIZE.to_le_bytes());
    hdr[10..12].copy_from_slice(&CHUNK_HEADER_SIZE.to_le_bytes());
    hdr[12..16].copy_from_slice(&(BLOCK_SIZE as u32).to_le_bytes());
    hdr[16..20].copy_from_slice(&total_blocks.to_le_bytes());
    hdr[20..24].copy_from_slice(&total_chunks.to_le_bytes());
    hdr[24..28].copy_from_slice(&0u32.to_le_bytes());
    f.write_all(&hdr).unwrap();

    let mut expected_raw = Vec::with_capacity((total_blocks as usize) * (BLOCK_SIZE as usize));

    for chunk in chunks {
        let mut ch = [0u8; 12];
        match chunk {
            SynthChunk::Raw(blks) => {
                let n = blks.len() as u32;
                let total_sz = 12 + n * (BLOCK_SIZE as u32);
                ch[0..2].copy_from_slice(&CHUNK_TYPE_RAW.to_le_bytes());
                ch[4..8].copy_from_slice(&n.to_le_bytes());
                ch[8..12].copy_from_slice(&total_sz.to_le_bytes());
                f.write_all(&ch).unwrap();
                for b in blks {
                    assert_eq!(b.len(), BLOCK_SIZE as usize);
                    f.write_all(b).unwrap();
                    expected_raw.extend_from_slice(b);
                }
            }
            SynthChunk::Fill { blocks, pattern } => {
                ch[0..2].copy_from_slice(&CHUNK_TYPE_FILL.to_le_bytes());
                ch[4..8].copy_from_slice(&blocks.to_le_bytes());
                ch[8..12].copy_from_slice(&16u32.to_le_bytes());
                f.write_all(&ch).unwrap();
                f.write_all(pattern).unwrap();
                for _ in 0..(*blocks as usize * (BLOCK_SIZE as usize / 4)) {
                    expected_raw.extend_from_slice(pattern);
                }
            }
            SynthChunk::DontCare { blocks } => {
                ch[0..2].copy_from_slice(&CHUNK_TYPE_DONT_CARE.to_le_bytes());
                ch[4..8].copy_from_slice(&blocks.to_le_bytes());
                ch[8..12].copy_from_slice(&12u32.to_le_bytes());
                f.write_all(&ch).unwrap();
                expected_raw.resize(
                    expected_raw.len() + (*blocks as usize) * (BLOCK_SIZE as usize),
                    0,
                );
            }
        }
    }

    expected_raw
}

fn make_block(seed: u32) -> Vec<u8> {
    let mut b = vec![0u8; BLOCK_SIZE as usize];
    for (i, byte) in b.iter_mut().enumerate() {
        *byte = ((seed.wrapping_mul(31).wrapping_add(i as u32)) & 0xFF) as u8;
    }
    // Guarantee non-zero
    b[0] = (seed & 0xFF) as u8 | 1;
    b
}

#[test]
fn test_sparse_img2sdat_sdat2img_roundtrip_all_versions() {
    let tmp = TempDir::new("roundtrip");
    let sparse_path = tmp.path().join("system.sparse.img");

    // 600 non-zero blocks (> 512 so multiple __NONZERO groups are produced),
    // zero fill, non-zero fill, and dont_care gaps (triggering extended + erase).
    let raw_blocks_a: Vec<Vec<u8>> = (1..=600).map(make_block).collect();
    let raw_blocks_b: Vec<Vec<u8>> = (700..720).map(make_block).collect();

    let expected_raw = write_synthetic_sparse(
        &sparse_path,
        &[
            SynthChunk::Raw(vec![make_block(0)]),        // block 0
            SynthChunk::Fill { blocks: 10, pattern: [0; 4] }, // 1..11
            SynthChunk::Raw(raw_blocks_a),               // 11..611
            SynthChunk::DontCare { blocks: 40 },         // 611..651
            SynthChunk::Fill { blocks: 15, pattern: [0xDE, 0xAD, 0xBE, 0xEF] }, // 651..666
            SynthChunk::Raw(raw_blocks_b),               // 666..686
            SynthChunk::DontCare { blocks: 600 },        // 686..1286 (> 512 extended gap)
        ],
    );

    // Check simg2img matches expected_raw
    let simg2img_raw = tmp.path().join("simg2img.raw.img");
    {
        let mut sp = SparseImage::open(&sparse_path, None, None, true, true).unwrap();
        sp.to_raw_image(&simg2img_raw).unwrap();
    }
    assert_eq!(fs::read(&simg2img_raw).unwrap(), expected_raw);

    // Roundtrip across versions 1..=4
    for version in 1u8..=4 {
        let outdir = tmp.path().join(format!("v{version}"));
        let opts = Img2SdatOptions {
            outdir: outdir.clone(),
            version,
            prefix: "system".to_string(),
            quiet: true,
            ..Default::default()
        };
        img2sdat(&sparse_path, &opts).unwrap();

        let tl = outdir.join("system.transfer.list");
        let new_dat = outdir.join("system.new.dat");
        let patch_dat = outdir.join("system.patch.dat");
        assert!(tl.exists());
        assert!(new_dat.exists());
        assert_eq!(fs::metadata(&patch_dat).unwrap().len(), 0);

        let parsed = parse_transfer_list_file(&tl).unwrap();
        assert_eq!(parsed.version, version as u32);

        let restored = tmp.path().join(format!("restored_v{version}.img"));
        sdat2img(
            &tl,
            &new_dat,
            &restored,
            &Sdat2ImgOptions {
                quiet: true,
                ..Default::default()
            },
        )
        .unwrap();

        let actual_raw = fs::read(&restored).unwrap();
        assert_eq!(
            actual_raw.len(),
            expected_raw.len(),
            "length mismatch on version {version}"
        );
        assert_eq!(
            actual_raw, expected_raw,
            "content mismatch on version {version}"
        );
    }
}

#[test]
fn test_raw_auto_and_sparse_output_roundtrip() {
    let tmp = TempDir::new("raw_sparse");
    let raw_in = tmp.path().join("raw.img");

    let mut raw_data = Vec::new();
    raw_data.extend_from_slice(&make_block(1)); // block 0
    raw_data.extend(std::iter::repeat_n(0u8, 8 * BLOCK_SIZE as usize)); // 8 zero blocks
    for i in 10..30 {
        raw_data.extend_from_slice(&make_block(i));
    }
    raw_data.extend(std::iter::repeat_n(0u8, 5 * BLOCK_SIZE as usize));
    fs::write(&raw_in, &raw_data).unwrap();

    // Auto-detect raw in img2sdat
    let outdir = tmp.path().join("sdat_from_raw");
    img2sdat(
        &raw_in,
        &Img2SdatOptions {
            outdir: outdir.clone(),
            version: 4,
            prefix: "vendor".to_string(),
            input_format: InputFormat::Auto,
            quiet: true,
            ..Default::default()
        },
    )
    .unwrap();

    // Convert back with --sparse
    let sparse_out = tmp.path().join("vendor.sparse.img");
    sdat2img(
        &outdir.join("vendor.transfer.list"),
        &outdir.join("vendor.new.dat"),
        &sparse_out,
        &Sdat2ImgOptions {
            sparse_output: true,
            quiet: true,
        ..Default::default()
        },
    )
    .unwrap();

    // And convert sparse_out back to raw and compare
    let raw_roundtrip = tmp.path().join("raw_roundtrip.img");
    let mut sp = SparseImage::open(&sparse_out, None, None, true, true).unwrap();
    sp.to_raw_image(&raw_roundtrip).unwrap();
    assert_eq!(fs::read(&raw_roundtrip).unwrap(), raw_data);

    // Also test direct raw_to_sparse
    let direct_sparse = tmp.path().join("direct.sparse.img");
    raw_to_sparse(&raw_in, &direct_sparse).unwrap();
    let direct_raw = tmp.path().join("direct.raw.img");
    let mut sp2 = SparseImage::open(&direct_sparse, None, None, true, true).unwrap();
    sp2.to_raw_image(&direct_raw).unwrap();
    assert_eq!(fs::read(&direct_raw).unwrap(), raw_data);
}

#[test]
fn test_sparse_append_fill_and_file_map() {
    let tmp = TempDir::new("file_map");
    let sparse_path = tmp.path().join("mapped.sparse.img");
    let map_path = tmp.path().join("file.map");

    write_synthetic_sparse(
        &sparse_path,
        &[
            SynthChunk::Raw(vec![make_block(0), make_block(1), make_block(2)]),
            SynthChunk::Fill {
                blocks: 2,
                pattern: [0; 4],
            },
        ],
    );

    {
        let mut rw = SparseImage::open_rw(&sparse_path, None, None, false, true).unwrap();
        rw.append_fill_chunk(0x11223344, 3).unwrap();
        assert_eq!(rw.total_blocks, 8);
        assert_eq!(rw.total_chunks, 3);
    }

    fs::write(&map_path, "/system/bin/app 1-2\n/system/etc/conf 5-6\n").unwrap();

    let mut sp = SparseImage::open(&sparse_path, Some(&map_path), Some("0"), true, true).unwrap();
    assert_eq!(sp.file_map["/system/bin/app"].to_range_string(), "1-2");
    assert_eq!(sp.file_map["/system/etc/conf"].to_range_string(), "5-6");
    assert_eq!(sp.file_map["__ZERO"].to_range_string(), "3-4");
    assert_eq!(sp.file_map["__NONZERO-0"].to_range_string(), "7");
    assert_eq!(sp.file_map["__COPY"].to_range_string(), "0");

    let sha_all = sp.total_sha1(true).unwrap();
    let sha_no_clob = sp.total_sha1(false).unwrap();
    assert_ne!(sha_all, sha_no_clob);

    sp.reset_file_map();
    assert_eq!(sp.file_map.len(), 1);
    assert!(sp.file_map.contains_key("__DATA"));
}

#[test]
fn test_blockimgdiff_incremental_move_and_stash() {
    let tmp = TempDir::new("incremental");
    let src_sparse = tmp.path().join("src.sparse.img");
    let tgt_sparse = tmp.path().join("tgt.sparse.img");
    let src_map = tmp.path().join("src.map");
    let tgt_map = tmp.path().join("tgt.map");

    let b0 = make_block(10);
    let b1 = make_block(20);
    let b2 = make_block(30);
    let b3 = make_block(40);
    let b4 = make_block(50);

    // src has fileA at 1-2, fileB at 3-4; tgt swaps them: fileA at 3-4, fileB at 1-2.
    write_synthetic_sparse(
        &src_sparse,
        &[SynthChunk::Raw(vec![
            b0.clone(),
            b1.clone(),
            b2.clone(),
            b3.clone(),
            b4.clone(),
        ])],
    );
    write_synthetic_sparse(
        &tgt_sparse,
        &[SynthChunk::Raw(vec![b0, b3, b4, b1, b2])],
    );

    fs::write(&src_map, "fileA 1-2\nfileB 3-4\n").unwrap();
    fs::write(&tgt_map, "fileA 3-4\nfileB 1-2\n").unwrap();

    let mut src = SparseImage::open(&src_sparse, Some(&src_map), Some("0"), true, true).unwrap();
    let mut tgt = SparseImage::open(&tgt_sparse, Some(&tgt_map), Some("0"), true, true).unwrap();

    let prefix = tmp.path().join("inc_v4");
    let mut diff = BlockImageDiff::new(
        &mut tgt,
        &mut src,
        4,
        Some(1),
        true,
        BlockOptions {
            cache_size: Some(64 * 1024),
            stash_threshold: 0.8,
        },
    )
    .unwrap();
    diff.quiet = true;
    diff.compute(&prefix).unwrap();

    let tl_text = fs::read_to_string(tmp.path().join("inc_v4.transfer.list")).unwrap();
    assert!(tl_text.starts_with("4\n"));
    assert!(tl_text.contains("stash "));
    assert!(tl_text.contains("move "));
    assert!(tl_text.contains("free "));

    // Also test v1 (remove_backward_edges) on EmptyImage -> DataImage
    let mut data_tgt = DataImage::new(
        [make_block(1), make_block(2), vec![0u8; BLOCK_SIZE as usize]].concat(),
        false,
        false,
    )
    .unwrap();
    let mut empty = EmptyImage::new();
    let prefix_v1 = tmp.path().join("full_v1");
    let mut diff_v1 = BlockImageDiff::new(
        &mut data_tgt,
        &mut empty,
        1,
        Some(1),
        true,
        BlockOptions::default(),
    )
    .unwrap();
    diff_v1.quiet = true;
    diff_v1.compute(&prefix_v1).unwrap();
    let tl_v1 = fs::read_to_string(tmp.path().join("full_v1.transfer.list")).unwrap();
    assert!(tl_v1.starts_with("1\n"));
}

#[test]
fn test_sdat2img_rejects_invalid_commands_and_ranges() {
    let tmp = TempDir::new("errors");
    let tl = tmp.path().join("bad.transfer.list");
    let dat = tmp.path().join("bad.new.dat");
    let out = tmp.path().join("bad.img");

    fs::write(&dat, []).unwrap();
    fs::write(&tl, "4\n1\n0\n0\nbogus 2,0,1\n").unwrap();

    let err = sdat2img(&tl, &dat, &out, &Sdat2ImgOptions { quiet: true, ..Default::default() })
        .unwrap_err();
    assert!(err.to_string().contains("bogus"));

    fs::write(&tl, "4\n1\n0\n0\nnew 3,0,1\n").unwrap();
    assert!(parse_transfer_list_file(&tl).is_err());

    // Valid numeric lines in transfer.list should be skipped cleanly
    fs::write(&tl, "4\n0\n0\n0\n12345\nerase 2,0,2\n").unwrap();
    let parsed = parse_transfer_list_file(&tl).unwrap();
    assert_eq!(parsed.commands.len(), 1);

    let _unused: HashMap<u32, u32> = HashMap::new();
}

#[test]
fn test_img2sdat_rejects_unaligned_non_sparse_image() {
    let tmp = TempDir::new("unaligned");
    let bad_img = tmp.path().join("unaligned.img");
    fs::write(&bad_img, b"NOT_ALIGNED_DATA_123456789").unwrap();

    let err = img2sdat(
        &bad_img,
        &Img2SdatOptions {
            outdir: tmp.path().to_path_buf(),
            quiet: true,
            ..Default::default()
        },
    )
    .unwrap_err();

    assert!(err.to_string().contains("neither an Android sparse image"));
}

