use std::fs;

use make_ext4fs_rs::config::fs_config::{default_fs_config, CannedFsConfig, FsConfigProvider};
use make_ext4fs_rs::config::selinux::FileContexts;
use make_ext4fs_rs::ext4::fs::{make_ext4fs, Ext4Options, OutputTarget};
use make_ext4fs_rs::ext4::hash::{ext4_crc16, generate_uuid};
use make_ext4fs_rs::sparse::SparseFile;

#[test]
fn test_uuid_and_crc16() {
    let u1 = generate_uuid("extandroid/make_ext4fs", "");
    let u2 = generate_uuid("extandroid/make_ext4fs", "system");
    assert_ne!(u1, u2);
    assert_eq!(u1[7] >> 4, 5);
    assert_eq!(u1[8] >> 6, 2);

    let crc = ext4_crc16(0xFFFF, &u1);
    assert_ne!(crc, 0);
}

#[test]
fn test_default_fs_config_rules() {
    let mut uid = 0;
    let mut gid = 0;
    let mut mode = 0o100666;
    let mut caps = 0;
    default_fs_config(
        "/system/bin/surfaceflinger",
        false,
        &mut uid,
        &mut gid,
        &mut mode,
        &mut caps,
    );
    assert_eq!(uid, 1000);
    assert_eq!(gid, 1003);
    assert_eq!(mode & 0o7777, 0o0755);
    assert_eq!(caps, 1u64 << 23);

    let mut dmode = 0o040777;
    default_fs_config("/data/misc", true, &mut uid, &mut gid, &mut dmode, &mut caps);
    assert_eq!(uid, 1000);
    assert_eq!(gid, 9998);
    assert_eq!(dmode & 0o7777, 0o1771);
}

#[test]
fn test_sparse_roundtrip_and_make_ext4fs() {
    let mut dir = std::env::temp_dir();
    dir.push(format!("make_ext4fs_rs_test_{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    let root = dir.join("root");
    fs::create_dir_all(root.join("bin")).unwrap();
    fs::write(root.join("bin").join("hello"), b"hello world\n").unwrap();

    let fc_path = dir.join("file_contexts");
    fs::write(
        &fc_path,
        "/.*\tu:object_r:system_file:s0\n/system/bin(/.*)?\tu:object_r:system_bin_file:s0\n",
    )
    .unwrap();
    let fc = FileContexts::open(&fc_path).unwrap();
    assert_eq!(
        fc.lookup("/system/bin/hello", 0o100000).as_deref(),
        Some("u:object_r:system_bin_file:s0")
    );

    let cfg_path = dir.join("fs_config");
    fs::write(
        &cfg_path,
        "system/bin 0 2000 0755\nsystem/bin/hello -1 2000 0755\n",
    )
    .unwrap();
    let canned = CannedFsConfig::load(&cfg_path).unwrap();
    let provider = FsConfigProvider::Canned(canned);

    let raw_img = dir.join("raw.img");
    let sparse_img = dir.join("sparse.img");
    let mut opts = Ext4Options::default();
    opts.len = 16 * 1024 * 1024;
    opts.fixed_time = 1_700_000_000;
    opts.label = "test".to_string();

    let raw_f = fs::File::create(&raw_img).unwrap();
    make_ext4fs(
        OutputTarget::File(raw_f),
        Some(root.to_str().unwrap()),
        Some("system"),
        &provider,
        Some(&fc),
        &opts,
        None,
    )
    .unwrap();
    assert_eq!(fs::metadata(&raw_img).unwrap().len(), 16 * 1024 * 1024);

    opts.sparse = true;
    opts.crc = true;
    let sparse_f = fs::File::create(&sparse_img).unwrap();
    make_ext4fs(
        OutputTarget::File(sparse_f),
        Some(root.to_str().unwrap()),
        Some("system"),
        &provider,
        Some(&fc),
        &opts,
        None,
    )
    .unwrap();

    let mut imported =
        SparseFile::import_file(fs::File::open(&sparse_img).unwrap(), true, false).unwrap();
    let unsparsed = dir.join("unsparsed.img");
    imported
        .write_to_file(fs::File::create(&unsparsed).unwrap(), false, false, false)
        .unwrap();
    assert_eq!(fs::read(&raw_img).unwrap(), fs::read(&unsparsed).unwrap());

    let _ = fs::remove_dir_all(&dir);
}
