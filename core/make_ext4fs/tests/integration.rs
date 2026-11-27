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
        "/system/bin/inputflinger",
        false,
        &mut uid,
        &mut gid,
        &mut mode,
        &mut caps,
    );
    assert_eq!(uid, 1000);
    assert_eq!(gid, 2000);
    assert_eq!(mode & 0o7777, 0o0700);
    assert_eq!(caps, 1u64 << 36);

    // System-as-root normalization: system/system/* -> system/*
    let mut sar_mode = 0o100666;
    default_fs_config(
        "/system/system/bin/simpleperf_app_runner",
        false,
        &mut uid,
        &mut gid,
        &mut sar_mode,
        &mut caps,
    );
    assert_eq!(uid, 0);
    assert_eq!(gid, 2000);
    assert_eq!(sar_mode & 0o7777, 0o0750);
    assert_eq!(caps, 0xC0);

    let mut dmode = 0o040777;
    default_fs_config("/data/misc", true, &mut uid, &mut gid, &mut dmode, &mut caps);
    assert_eq!(uid, 1000);
    assert_eq!(gid, 9998);
    assert_eq!(dmode & 0o7777, 0o1771);

    let mut cfg_mode = 0o040777;
    default_fs_config("/config", true, &mut uid, &mut gid, &mut cfg_mode, &mut caps);
    assert_eq!(cfg_mode & 0o7777, 0o0555);

    // Middle wildcard & APEX bin match (AOSP fnmatch)
    let mut apex_dmode = 0o040777;
    default_fs_config(
        "/product/apex/com.android.art/bin",
        true,
        &mut uid,
        &mut gid,
        &mut apex_dmode,
        &mut caps,
    );
    assert_eq!(uid, 0);
    assert_eq!(gid, 2000);
    assert_eq!(apex_dmode & 0o7777, 0o0751);

    let mut apex_fmode = 0o100644;
    default_fs_config(
        "/system/apex/com.android.runtime/bin/linker",
        false,
        &mut uid,
        &mut gid,
        &mut apex_fmode,
        &mut caps,
    );
    assert_eq!(uid, 0);
    assert_eq!(gid, 2000);
    assert_eq!(apex_fmode & 0o7777, 0o0755);

    // Logical partition aliasing: system/vendor/bin/* -> vendor/bin/*
    let mut lv_mode = 0o100644;
    default_fs_config(
        "/system/vendor/bin/hw_tool",
        false,
        &mut uid,
        &mut gid,
        &mut lv_mode,
        &mut caps,
    );
    assert_eq!(uid, 0);
    assert_eq!(gid, 2000);
    assert_eq!(lv_mode & 0o7777, 0o0755);
}

#[test]
fn test_aosp_canned_last_wins_space_root_and_selinux_trailing_slash() {
    let mut dir = std::env::temp_dir();
    dir.push(format!("make_ext4fs_rs_aosp_{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();

    // Canned fs_config with space root + duplicate override ("last wins") + 32-bit UID/GID
    let cfg_path = dir.join("fs_config");
    fs::write(
        &cfg_path,
        " 1000 1000 0755\nsystem/bin/su 0 2000 0755\nsystem/bin/su 0 0 4750\nsystem/app/hi 100000 100001 0644\n",
    )
    .unwrap();
    let canned = CannedFsConfig::load(&cfg_path).unwrap();
    let mut uid = 0;
    let mut gid = 0;
    let mut mode = 0;
    let mut caps = 0;

    canned.lookup("", true, &mut uid, &mut gid, &mut mode, &mut caps);
    assert_eq!((uid, gid, mode), (1000, 1000, 0o0755));

    canned.lookup("system/bin/su", false, &mut uid, &mut gid, &mut mode, &mut caps);
    assert_eq!((uid, gid, mode), (0, 0, 0o4750));

    canned.lookup("system/app/hi", false, &mut uid, &mut gid, &mut mode, &mut caps);
    assert_eq!((uid, gid, mode), (100000, 100001, 0o0644));

    // FileContexts with -s (socket) spec and exact directory lookup with trailing slash
    let fc_path = dir.join("file_contexts");
    fs::write(
        &fc_path,
        "/system\tu:object_r:system_file:s0\n/dev/socket/.*\t-s\tu:object_r:socket_device:s0\n",
    )
    .unwrap();
    let fc = FileContexts::open(&fc_path).unwrap();
    assert_eq!(
        fc.lookup("/system/", 0o040000).as_deref(),
        Some("u:object_r:system_file:s0")
    );
    assert_eq!(
        fc.lookup("/dev/socket/logd", 0o140000).as_deref(),
        Some("u:object_r:socket_device:s0")
    );

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_canned_high_bit_caps_and_128b_inode_xattr() {
    let mut dir = std::env::temp_dir();
    dir.push(format!("make_ext4fs_rs_caps128_{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    let root = dir.join("root");
    fs::create_dir_all(&root).unwrap();
    fs::write(root.join("tool"), b"elf").unwrap();

    let cfg_path = dir.join("fs_config");
    fs::write(&cfg_path, "system/tool 0 2000 0755 capabilities=0x8000000000000001\n").unwrap();
    let canned = CannedFsConfig::load(&cfg_path).unwrap();
    let mut uid = 99;
    let mut gid = 99;
    let mut mode = 0;
    let mut caps = 0;
    canned.lookup("system/tool", false, &mut uid, &mut gid, &mut mode, &mut caps);
    assert_eq!(caps, 0x8000_0000_0000_0001u64);

    let fc_path = dir.join("file_contexts");
    fs::write(&fc_path, "/.*\tu:object_r:system_file:s0\n").unwrap();
    let fc = FileContexts::open(&fc_path).unwrap();

    let mut opts = Ext4Options::default();
    opts.len = 8 * 1024 * 1024;
    opts.inode_size = 128;
    opts.fixed_time = 1_700_000_000;
    let provider = FsConfigProvider::Canned(canned);
    let out_img = dir.join("in128.img");
    make_ext4fs(
        OutputTarget::File(fs::File::create(&out_img).unwrap()),
        Some(root.to_str().unwrap()),
        Some("system"),
        &provider,
        Some(&fc),
        &opts,
        None,
    )
    .unwrap();
    assert_eq!(fs::metadata(&out_img).unwrap().len(), 8 * 1024 * 1024);

    let _ = fs::remove_dir_all(&dir);
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
