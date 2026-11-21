use std::collections::BTreeMap;
use std::fs;
use std::io::{Cursor, Read, Seek, SeekFrom, Write};
use std::path::PathBuf;
use std::process::Command;

use lp_tools::lp::{
    format_metadata_json, format_metadata_text, is_empty_super_image,
    read_from_image_blob, read_metadata, read_metadata_auto, write_empty_image_file,
    write_split_image_files, write_to_image_file,
    BlockDeviceInfo, MetadataBuilder, LP_METADATA_GEOMETRY_SIZE, LP_PARTITION_ATTR_NONE,
    LP_PARTITION_ATTR_READONLY, LP_PARTITION_RESERVED_BYTES,
};
use lp_tools::sparse::{ImageSource, SparseFile, SparseReader};

struct TempDir {
    path: PathBuf,
}

impl TempDir {
    fn new(name: &str) -> Self {
        let mut path = std::env::temp_dir();
        let pid = std::process::id();
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        path.push(format!("lp_tools_test_{name}_{pid}_{nanos}"));
        fs::create_dir_all(&path).unwrap();
        Self { path }
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

#[test]
fn test_sparse_roundtrip_with_crc() {
    let blk = 4096u32;
    let total_len = 8 * blk as u64;
    let mut sf = SparseFile::new(blk, total_len).unwrap();

    let mut pattern_a = vec![0u8; blk as usize];
    for (i, b) in pattern_a.iter_mut().enumerate() {
        *b = (i % 251) as u8;
    }
    sf.add_data(pattern_a.clone(), 0);
    // Block 1 is skipped (don't-care)
    sf.add_fill(0xDEADBEEF, (2 * blk) as u64, 2);
    // Blocks 4..6 skipped
    let mut pattern_b = vec![0u8; (2 * blk) as usize];
    for (i, b) in pattern_b.iter_mut().enumerate() {
        *b = ((i * 7) % 256) as u8;
    }
    sf.add_data(pattern_b.clone(), 6);

    let mut encoded = Vec::new();
    sf.write_sparse(&mut encoded, true).unwrap();

    let mut reader = SparseReader::open(Cursor::new(encoded), true).unwrap();
    assert_eq!(reader.logical_size(), total_len);

    let mut decoded = Vec::new();
    reader.read_to_end(&mut decoded).unwrap();
    assert_eq!(decoded.len(), total_len as usize);

    assert_eq!(&decoded[0..4096], &pattern_a[..]);
    assert!(decoded[4096..8192].iter().all(|&b| b == 0));
    for chunk in decoded[8192..16384].chunks_exact(4) {
        assert_eq!(chunk, &0xDEADBEEFu32.to_le_bytes());
    }
    assert!(decoded[16384..24576].iter().all(|&b| b == 0));
    assert_eq!(&decoded[24576..32768], &pattern_b[..]);

    // Random seek across chunk boundary
    reader.seek(SeekFrom::Start(8190)).unwrap();
    let mut slice = [0u8; 8];
    reader.read_exact(&mut slice).unwrap();
    assert_eq!(&slice[..2], &[0, 0]);
    assert_eq!(&slice[2..6], &0xDEADBEEFu32.to_le_bytes());
}

#[test]
fn test_empty_super_image_and_auto_slot_suffixing() {
    let dir = TempDir::new("empty_super");
    let dev = BlockDeviceInfo::new("super", 64 * 1024 * 1024, 1024 * 1024, 0, 4096).unwrap();
    let mut builder = MetadataBuilder::new(&[dev], "super", 65536, 2).unwrap();
    builder.set_auto_slot_suffixing();
    builder.set_virtual_ab_device_flag();
    builder.add_group("qti_dynamic_partitions", 32 * 1024 * 1024).unwrap();
    let p_idx = builder
        .add_partition("system", "qti_dynamic_partitions", LP_PARTITION_ATTR_READONLY)
        .unwrap();
    builder.resize_partition(p_idx, 4 * 1024 * 1024, &[]).unwrap();

    let exported = builder.export().unwrap();
    let empty_path = dir.path.join("super_empty.img");
    write_empty_image_file(&empty_path, &exported).unwrap();

    let blob = fs::read(&empty_path).unwrap();
    let parsed_blob = read_from_image_blob(&blob).unwrap();
    assert_eq!(parsed_blob.header.minor_version, 2);
    assert_eq!(parsed_blob.partitions[0].name(), "system");

    let mut f = fs::File::open(&empty_path).unwrap();
    assert!(is_empty_super_image(&mut f).unwrap());

    let (slot0, is_empty0) = read_metadata_auto(&mut f, 0).unwrap();
    assert!(is_empty0);
    assert_eq!(slot0.partitions[0].name(), "system_a");
    assert_eq!(slot0.groups[1].name(), "qti_dynamic_partitions_a");
    assert_eq!(slot0.block_devices[0].partition_name(), "super_a");

    let (slot1, is_empty1) = read_metadata_auto(&mut f, 1).unwrap();
    assert!(is_empty1);
    assert_eq!(slot1.partitions[0].name(), "system_b");
    assert_eq!(slot1.groups[1].name(), "qti_dynamic_partitions_b");
    assert_eq!(slot1.block_devices[0].partition_name(), "super_b");
}

#[test]
fn test_backup_geometry_and_metadata_fallback() {
    let dir = TempDir::new("backup_fallback");
    let dev = BlockDeviceInfo::new("super", 16 * 1024 * 1024, 1024 * 1024, 0, 4096).unwrap();
    let mut builder = MetadataBuilder::new(&[dev], "super", 65536, 2).unwrap();
    let p_idx = builder
        .add_partition("vendor_a", "default", LP_PARTITION_ATTR_READONLY)
        .unwrap();
    builder.resize_partition(p_idx, 1024 * 1024, &[]).unwrap();
    let exported = builder.export().unwrap();

    let super_path = dir.path.join("super.img");
    write_to_image_file(&super_path, &exported, 4096, &BTreeMap::new(), false).unwrap();

    // Corrupt primary geometry and primary metadata for slot 0
    {
        let mut f = fs::OpenOptions::new().read(true).write(true).open(&super_path).unwrap();
        f.seek(SeekFrom::Start(LP_PARTITION_RESERVED_BYTES)).unwrap();
        f.write_all(&[0xAAu8; LP_METADATA_GEOMETRY_SIZE]).unwrap();

        let primary_meta_off = LP_PARTITION_RESERVED_BYTES + (LP_METADATA_GEOMETRY_SIZE as u64 * 2);
        f.seek(SeekFrom::Start(primary_meta_off)).unwrap();
        f.write_all(&[0x55u8; 256]).unwrap();
    }

    let mut f = fs::File::open(&super_path).unwrap();
    let recovered = read_metadata(&mut f, 0).unwrap();
    assert_eq!(recovered.partitions.len(), 1);
    assert_eq!(recovered.partitions[0].name(), "vendor_a");
}

#[test]
fn test_group_limit_and_resize_shrink() {
    let dev = BlockDeviceInfo::new("super", 32 * 1024 * 1024, 1024 * 1024, 0, 4096).unwrap();
    let mut builder = MetadataBuilder::new(&[dev], "super", 65536, 2).unwrap();
    builder.add_group("main", 2 * 1024 * 1024).unwrap();
    let p1 = builder.add_partition("sys", "main", LP_PARTITION_ATTR_NONE).unwrap();
    builder.resize_partition(p1, 1024 * 1024, &[]).unwrap();

    let p2 = builder.add_partition("ven", "main", LP_PARTITION_ATTR_NONE).unwrap();
    // Exceeds group maximum_size
    assert!(builder.resize_partition(p2, 2 * 1024 * 1024, &[]).is_err());
    // Fits within remaining 1 MiB
    builder.resize_partition(p2, 1024 * 1024, &[]).unwrap();

    // Shrink p1 and grow p2
    builder.resize_partition(p1, 512 * 1024, &[]).unwrap();
    builder.resize_partition(p2, 1536 * 1024, &[]).unwrap();
    let meta = builder.export().unwrap();
    assert_eq!(meta.partition_size(&meta.partitions[0]), 512 * 1024);
    assert_eq!(meta.partition_size(&meta.partitions[1]), 1536 * 1024);
}

#[test]
fn test_full_lpmake_lpunpack_roundtrip_raw_and_sparse() {
    let dir = TempDir::new("roundtrip");

    // Create sample partition images: one raw (with zero holes + data), one sparse input image
    let sys_size = 512 * 1024;
    let mut sys_data = vec![0u8; sys_size];
    for i in 0..4096 {
        sys_data[i] = (i % 199) as u8;
    }
    // Middle is all zeroes (blocks 1..100), end has fill/non-zero pattern
    for i in (sys_size - 8192)..sys_size {
        sys_data[i] = ((i * 3) % 253) as u8;
    }
    let sys_raw_path = dir.path.join("system.img");
    fs::write(&sys_raw_path, &sys_data).unwrap();

    let ven_size = 256 * 1024;
    let mut ven_data = vec![0u8; ven_size];
    for i in 0..8192 {
        ven_data[i] = ((i + 17) % 251) as u8;
    }
    for i in (128 * 1024)..(132 * 1024) {
        ven_data[i] = 0x77;
    }
    // Save vendor as an Android sparse image input
    let ven_sparse_path = dir.path.join("vendor_sparse.img");
    {
        let mut sf = SparseFile::new(4096, ven_size as u64).unwrap();
        sf.add_data(ven_data[..8192].to_vec(), 0);
        sf.add_fill(0x77777777, 4096, 32);
        let mut f = fs::File::create(&ven_sparse_path).unwrap();
        sf.write_sparse(&mut f, true).unwrap();
    }

    let lpmake = env!("CARGO_BIN_EXE_lpmake");
    let lpunpack = env!("CARGO_BIN_EXE_lpunpack");

    for sparse in [false, true] {
        let super_path = dir.path.join(if sparse { "super_sparse.img" } else { "super_raw.img" });
        let out_dir = dir.path.join(if sparse { "out_sparse" } else { "out_raw" });

        let mut cmd = Command::new(lpmake);
        cmd.arg("--device-size").arg("16777216")
            .arg("--metadata-size").arg("65536")
            .arg("--metadata-slots").arg("2")
            .arg("--group").arg("main_a:8388608")
            .arg("--partition").arg(format!("system_a:readonly:{sys_size}:main_a"))
            .arg("--partition").arg(format!("vendor_a:none:{ven_size}:main_a"))
            .arg("--image").arg(format!("system_a={}", sys_raw_path.display()))
            .arg("--image").arg(format!("vendor_a={}", ven_sparse_path.display()))
            .arg("--virtual-ab")
            .arg("--output").arg(&super_path);
        if sparse {
            cmd.arg("--sparse");
        }
        let status = cmd.status().unwrap();
        assert!(status.success(), "lpmake failed for sparse={sparse}");

        // Verify sparse header presence
        {
            let mut f = fs::File::open(&super_path).unwrap();
            let src = ImageSource::open(&mut f).unwrap();
            assert_eq!(src.is_sparse(), sparse);
        }

        // Verify --info -f json
        let info_out = Command::new(lpunpack)
            .arg("--info")
            .arg("-f").arg("json")
            .arg(&super_path)
            .output()
            .unwrap();
        assert!(info_out.status.success());
        let json_str = String::from_utf8(info_out.stdout).unwrap();
        assert!(json_str.contains("\"system_a\""));
        assert!(json_str.contains("\"vendor_a\""));
        assert!(json_str.contains("\"main_a\""));

        // Verify --info text
        let text_out = Command::new(lpunpack)
            .arg("--info")
            .arg(&super_path)
            .output()
            .unwrap();
        assert!(text_out.status.success());
        let text_str = String::from_utf8(text_out.stdout).unwrap();
        assert!(text_str.contains("Metadata version: 10.2"));
        assert!(text_str.contains("Header flags: virtual_ab_device"));

        // Unpack all partitions
        let unpack_status = Command::new(lpunpack)
            .arg(&super_path)
            .arg(&out_dir)
            .status()
            .unwrap();
        assert!(unpack_status.success());

        let extracted_sys = fs::read(out_dir.join("system_a.img")).unwrap();
        let extracted_ven = fs::read(out_dir.join("vendor_a.img")).unwrap();
        assert_eq!(extracted_sys, sys_data);
        assert_eq!(extracted_ven, ven_data);

        // Selective unpack with -p delimiter
        let sel_dir = dir.path.join(if sparse { "sel_sparse" } else { "sel_raw" });
        let sel_status = Command::new(lpunpack)
            .arg("-p").arg("vendor_a")
            .arg(&super_path)
            .arg(&sel_dir)
            .status()
            .unwrap();
        assert!(sel_status.success());
        assert!(!sel_dir.join("system_a.img").exists());
        assert_eq!(fs::read(sel_dir.join("vendor_a.img")).unwrap(), ven_data);
    }
}

#[test]
fn test_split_devices_super() {
    let dir = TempDir::new("split_devs");
    let dev_a = BlockDeviceInfo::new("system_a", 16 * 1024 * 1024, 1024 * 1024, 0, 4096).unwrap();
    let dev_b = BlockDeviceInfo::new("vendor_a", 16 * 1024 * 1024, 1024 * 1024, 0, 4096).unwrap();

    let mut builder = MetadataBuilder::new(&[dev_a, dev_b], "system_a", 65536, 2).unwrap();
    let p1 = builder.add_partition("system_a", "default", LP_PARTITION_ATTR_READONLY).unwrap();
    builder.resize_partition(p1, 14 * 1024 * 1024, &[]).unwrap();
    let p2 = builder.add_partition("vendor_a", "default", LP_PARTITION_ATTR_READONLY).unwrap();
    builder.resize_partition(p2, 4 * 1024 * 1024, &[]).unwrap();

    let metadata = builder.export().unwrap();
    let mut vendor_img = vec![0u8; 256 * 1024];
    for (i, b) in vendor_img.iter_mut().enumerate() {
        *b = (i % 173) as u8;
    }
    let vendor_img_path = dir.path.join("vendor_in.img");
    fs::write(&vendor_img_path, &vendor_img).unwrap();

    let mut images = BTreeMap::new();
    images.insert("vendor_a".to_string(), vendor_img_path);

    let split_out = dir.path.join("split_out");
    write_split_image_files(&split_out, &metadata, 4096, &images, true).unwrap();
    assert!(split_out.join("super_system_a.img").exists());
    assert!(split_out.join("super_vendor_a.img").exists());

    let text = format_metadata_text(&metadata, 0);
    let json = format_metadata_json(&metadata);
    assert!(text.contains("vendor_a"));
    assert!(json.contains("super") || json.contains("system_a"));

    // Unpack vendor_a across split super files
    let unpack_out = dir.path.join("unpacked_split");
    let lpunpack = env!("CARGO_BIN_EXE_lpunpack");
    let status = Command::new(lpunpack)
        .arg("-p").arg("vendor_a")
        .arg(split_out.join("super_system_a.img"))
        .arg(&unpack_out)
        .status()
        .unwrap();
    assert!(status.success());
    let unpacked_vendor = fs::read(unpack_out.join("vendor_a.img")).unwrap();
    assert_eq!(&unpacked_vendor[..vendor_img.len()], &vendor_img[..]);
    assert!(unpacked_vendor[vendor_img.len()..].iter().all(|&b| b == 0));
}

#[test]
fn test_lpunpack_norm_and_unsparse() {
    let dir = TempDir::new("norm_unsparse");

    let part_size = 256 * 1024usize;
    let sys_a_data = vec![0xAAu8; part_size];
    let sys_b_data = vec![0xBBu8; part_size];
    let ven_b_data = vec![0xCCu8; part_size];

    let sys_a_path = dir.path.join("sys_a.img");
    let sys_b_path = dir.path.join("sys_b.img");
    let ven_b_path = dir.path.join("ven_b.img");
    fs::write(&sys_a_path, &sys_a_data).unwrap();
    fs::write(&sys_b_path, &sys_b_data).unwrap();
    fs::write(&ven_b_path, &ven_b_data).unwrap();

    let super_sparse = dir.path.join("super.img");
    let lpmake = env!("CARGO_BIN_EXE_lpmake");
    let lpunpack = env!("CARGO_BIN_EXE_lpunpack");

    let status = Command::new(lpmake)
        .arg("--device-size").arg("16777216")
        .arg("--metadata-size").arg("65536")
        .arg("--metadata-slots").arg("2")
        .arg("--group").arg("main_a:8388608")
        .arg("--group").arg("main_b:8388608")
        .arg("--partition").arg(format!("system_a:readonly:{part_size}:main_a"))
        .arg("--partition").arg(format!("system_b:readonly:{part_size}:main_b"))
        .arg("--partition").arg("vendor_a:readonly:0:main_a")
        .arg("--partition").arg(format!("vendor_b:readonly:{part_size}:main_b"))
        .arg("--image").arg(format!("system_a={}", sys_a_path.display()))
        .arg("--image").arg(format!("system_b={}", sys_b_path.display()))
        .arg("--image").arg(format!("vendor_b={}", ven_b_path.display()))
        .arg("--sparse")
        .arg("--output").arg(&super_sparse)
        .status()
        .unwrap();
    assert!(status.success());

    // --norm with default slot 0 picks system_a -> system.img and non-empty vendor_b -> vendor.img
    let norm_a_dir = dir.path.join("norm_a");
    let status = Command::new(lpunpack)
        .arg(&super_sparse)
        .arg(&norm_a_dir)
        .arg("--norm")
        .status()
        .unwrap();
    assert!(status.success());
    assert_eq!(fs::read(norm_a_dir.join("system.img")).unwrap(), sys_a_data);
    assert_eq!(fs::read(norm_a_dir.join("vendor.img")).unwrap(), ven_b_data);
    assert!(!norm_a_dir.join("system_a.img").exists());
    assert!(!norm_a_dir.join("system_b.img").exists());
    assert!(!norm_a_dir.join("vendor_a.img").exists());
    assert!(!norm_a_dir.join("vendor_b.img").exists());

    // --norm with -S 1 prefers slot 1 (_b)
    let norm_b_dir = dir.path.join("norm_b");
    let status = Command::new(lpunpack)
        .arg("-S").arg("1")
        .arg(&super_sparse)
        .arg(&norm_b_dir)
        .arg("--norm")
        .status()
        .unwrap();
    assert!(status.success());
    assert_eq!(fs::read(norm_b_dir.join("system.img")).unwrap(), sys_b_data);
    assert_eq!(fs::read(norm_b_dir.join("vendor.img")).unwrap(), ven_b_data);

    // --unsparse converts sparse super.img to raw in-place in out_dir
    let unsparse_status = Command::new(lpunpack)
        .arg(&super_sparse)
        .arg(&dir.path)
        .arg("--unsparse")
        .status()
        .unwrap();
    assert!(unsparse_status.success());
    {
        let mut f = fs::File::open(&super_sparse).unwrap();
        let src = ImageSource::open(&mut f).unwrap();
        assert!(!src.is_sparse());
    }
}
