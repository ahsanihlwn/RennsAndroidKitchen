// SPDX-License-Identifier: MIT

use erofs_rs::avb::read_avb_footer;
use erofs_rs::compact::compact_image;
use erofs_rs::compress::CompressionAlg;
use erofs_rs::edit::{
    WriteMode, unlink_path, usage_report, write_path_data, write_path_mode, write_path_selinux,
};
use erofs_rs::extract::{ExtractOptions, SymlinkMode, extract_image};
use erofs_rs::mount_index::MountRegistry;
use erofs_rs::pack::{PackOptions, pack_directory};
use erofs_rs::reader::ErofsImage;
use erofs_rs::verify::verify_image;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

fn make_temp_dir(label: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "erofs_rs_test_{label}_{}",
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn populate_sample_tree(root: &Path) -> (PathBuf, PathBuf) {
    fs::create_dir_all(root.join("etc")).unwrap();
    fs::create_dir_all(root.join("bin")).unwrap();
    fs::create_dir_all(root.join("data")).unwrap();

    fs::write(
        root.join("etc/build.prop"),
        b"ro.product.name=erofs_rs_demo\nro.build.version=1.0\n",
    )
    .unwrap();

    // Multi-block compressible file (~24 KiB)
    let mut big = Vec::new();
    for i in 0..600 {
        big.extend_from_slice(
            format!("line {i:04}: EROFS Rust clean-room MIT implementation test payload!\n")
                .as_bytes(),
        );
    }
    fs::write(root.join("data/big_text.log"), &big).unwrap();

    // Small file to test inline layout & in-place inline edit
    fs::write(root.join("data/small.txt"), b"0123456789ABCDEF").unwrap();

    // Executable binary-like file (~9 KiB)
    let mut bin = vec![0u8; 9000];
    for (i, b) in bin.iter_mut().enumerate() {
        *b = ((i * 37) ^ (i >> 3)) as u8;
    }
    fs::write(root.join("bin/tool"), &bin).unwrap();

    let fs_cfg = root.parent().unwrap().join("sample_fs_config");
    fs::write(
        &fs_cfg,
        "/ 0 0 0755\n\
         /etc 0 0 0755\n\
         /etc/build.prop 0 0 0644\n\
         /bin 0 2000 0755\n\
         /bin/tool 0 2000 0755 capabilities=0x1000001400\n\
         /data 1000 1000 0755\n\
         /data/big_text.log 1000 1000 0644\n\
         /data/small.txt 1000 1000 0644\n",
    )
    .unwrap();

    let file_ctx = root.parent().unwrap().join("sample_file_contexts");
    fs::write(
        &file_ctx,
        "/ u:object_r:system_file:s0\n\
         /etc u:object_r:system_file:s0\n\
         /etc/build\\.prop u:object_r:unique_prop_context:s0\n\
         /bin u:object_r:system_file:s0\n\
         /bin/tool u:object_r:tool_exec:s0\n\
         /data u:object_r:system_file:s0\n\
         /data/.* u:object_r:system_file:s0\n",
    )
    .unwrap();

    (fs_cfg, file_ctx)
}

#[test]
fn roundtrip_all_compression_algorithms() {
    let base = make_temp_dir("algos");
    let src = base.join("src");
    let (fs_cfg, file_ctx) = populate_sample_tree(&src);
    let orig_big = fs::read(src.join("data/big_text.log")).unwrap();
    let orig_bin = fs::read(src.join("bin/tool")).unwrap();

    for (alg, pc) in [
        (CompressionAlg::None, 4096),
        (CompressionAlg::Lz4, 4096),
        (CompressionAlg::Lz4Hc, 16384),
        (CompressionAlg::Deflate, 8192),
        (CompressionAlg::Zstd, 8192),
        (CompressionAlg::Lzma, 8192),
    ] {
        let out_img = base.join(format!("test_{}.img", alg.name()));
        let opts = PackOptions {
            source_dir: src.clone(),
            output_image: out_img.clone(),
            algorithm: alg,
            pcluster_size: pc,
            fs_config: Some(fs_cfg.clone()),
            file_contexts: Some(file_ctx.clone()),
            volume_label: format!("vol_{}", alg.name()),
            timestamp: Some(1_700_000_000),
            ..Default::default()
        };
        let pack_rep = pack_directory(&opts).expect("pack failed");
        assert!(pack_rep.image_bytes >= 4096);
        assert!(pack_rep.crc32c.is_some());

        let img = ErofsImage::open(&out_img, 0).expect("open failed");
        let summary = img.summary();
        assert_eq!(summary.crc_valid, Some(true));
        assert_eq!(summary.volume_name, format!("vol_{}", alg.name()));

        // Verify entire image integrity
        let vrep = verify_image(&img, true, false, 2).expect("verify failed");
        assert!(vrep.ok, "verify errors for {}: {:?}", alg.name(), vrep.errors);

        // Check file contents match byte-for-byte
        let (big_read, _) = img.read_path("/data/big_text.log").unwrap();
        assert_eq!(big_read, orig_big, "mismatch big_text.log for {}", alg.name());
        let (bin_read, bin_node) = img.read_path("/bin/tool").unwrap();
        assert_eq!(bin_read, orig_bin, "mismatch bin/tool for {}", alg.name());
        assert_eq!(bin_node.uid, 0);
        assert_eq!(bin_node.gid, 2000);
        assert_eq!(bin_node.mode & 0o7777, 0o755);
        assert_eq!(bin_node.capabilities, Some(0x1000001400));
        assert_eq!(bin_node.selinux.as_deref(), Some("u:object_r:tool_exec:s0"));
    }

    let _ = fs::remove_dir_all(&base);
}

#[test]
fn unpack_direct_edit_and_compact_workflow() {
    let base = make_temp_dir("edit_compact");
    let src = base.join("src");
    let (fs_cfg, file_ctx) = populate_sample_tree(&src);
    let img_path = base.join("product.img");

    let opts = PackOptions {
        source_dir: src.clone(),
        output_image: img_path.clone(),
        algorithm: CompressionAlg::Lz4Hc,
        pcluster_size: 4096,
        fs_config: Some(fs_cfg),
        file_contexts: Some(file_ctx),
        timestamp: Some(1_700_000_000),
        ..Default::default()
    };
    pack_directory(&opts).unwrap();

    // 1. Unpack & verify Android config files
    let unpack_dir = base.join("unpacked");
    let img = ErofsImage::open(&img_path, 0).unwrap();
    let ex_rep = extract_image(
        &img,
        &ExtractOptions {
            out_dir: unpack_dir.clone(),
            image_base_name: "product".into(),
            targets: Vec::new(),
            recursive: true,
            config_only: false,
            write_configs: true,
            overwrite: true,
            verify_digest: false,
            symlink_mode: SymlinkMode::Auto,
            threads: 2,
            quiet: true,
        },
    )
    .unwrap();
    assert!(ex_rep.errors.is_empty());
    assert_eq!(ex_rep.files_extracted, 4);
    let fc_text = fs::read_to_string(ex_rep.fs_config.unwrap()).unwrap();
    assert!(fc_text.contains("product/bin/tool 0 2000 0755 capabilities=0x1000001400"));
    let sx_text = fs::read_to_string(ex_rep.file_contexts.unwrap()).unwrap();
    assert!(sx_text.contains("/product/etc/build\\.prop u:object_r:unique_prop_context:s0"));
    let fo_text = fs::read_to_string(ex_rep.fs_options.unwrap()).unwrap();
    assert!(fo_text.contains("-zlz4 "), "unexpected fs_options: {fo_text}");
    assert!(fo_text.contains("-T 1700000000"), "unexpected fs_options: {fo_text}");
    drop(img);

    // 2. Direct in-place inline edit (shrink small.txt)
    let w1 = write_path_data(&img_path, "/data/small.txt", b"HELLO_RUST", true, true).unwrap();
    assert_eq!(w1.mode, WriteMode::InplaceInline);
    let img = ErofsImage::open(&img_path, 0).unwrap();
    assert_eq!(img.read_path("/data/small.txt").unwrap().0, b"HELLO_RUST");
    assert_eq!(img.summary().crc_valid, Some(true));
    drop(img);

    // 3. Direct append edit on compressed file (repoints to FLAT_PLAIN at tail)
    let replacement = b"replaced big_text.log directly without unpack!\n".repeat(120);
    let w2 = write_path_data(&img_path, "/data/big_text.log", &replacement, true, true).unwrap();
    assert_eq!(w2.mode, WriteMode::Append);
    assert!(w2.grew_bytes > 0);
    let img = ErofsImage::open(&img_path, 0).unwrap();
    assert_eq!(img.read_path("/data/big_text.log").unwrap().0, replacement);
    assert_eq!(img.summary().crc_valid, Some(true));
    drop(img);

    // 4. Direct chmod & inline SELinux edit (unique_prop_context is inline because count==1)
    let m = write_path_mode(&img_path, "/etc/build.prop", 0o600).unwrap();
    assert_eq!(m & 0o7777, 0o600);
    write_path_selinux(&img_path, "/etc/build.prop", "u:object_r:new_prop:s0").unwrap();
    let img = ErofsImage::open(&img_path, 0).unwrap();
    let (_, prop_node) = img.read_path("/etc/build.prop").unwrap();
    assert_eq!(prop_node.mode & 0o7777, 0o600);
    assert_eq!(prop_node.selinux.as_deref(), Some("u:object_r:new_prop:s0"));
    drop(img);

    // 5. Direct unlink + usage report + in-place compaction
    let ures = unlink_path(&img_path, "/bin/tool", false, true).unwrap();
    assert_eq!(ures.removed, "bin/tool");
    let usage_before = usage_report(&img_path, 0).unwrap();
    assert!(usage_before.wasted_blocks > 0);

    let crep = compact_image(&img_path, false, true).unwrap();
    assert!(crep.after_bytes < crep.before_bytes, "expected compaction to shrink image");

    let img = ErofsImage::open(&img_path, 0).unwrap();
    let vrep = verify_image(&img, true, false, 1).unwrap();
    assert!(vrep.ok, "post-compact verify failed: {:?}", vrep.errors);
    assert_eq!(img.read_path("/data/big_text.log").unwrap().0, replacement);
    assert!(img.lookup("/bin/tool").is_err());
    drop(img);

    // 6. Test fake AVB footer detection & auto-strip on append write
    let orig_len = fs::metadata(&img_path).unwrap().len();
    {
        let mut f = OpenOptions::new().append(true).open(&img_path).unwrap();
        let vbmeta = vec![0xAAu8; 4096];
        f.write_all(&vbmeta).unwrap();
        let mut footer = [0u8; 64];
        footer[0..4].copy_from_slice(b"AVBf");
        footer[4..8].copy_from_slice(&1u32.to_be_bytes());
        footer[8..12].copy_from_slice(&0u32.to_be_bytes());
        footer[12..20].copy_from_slice(&orig_len.to_be_bytes());
        footer[20..28].copy_from_slice(&orig_len.to_be_bytes());
        footer[28..36].copy_from_slice(&(vbmeta.len() as u64).to_be_bytes());
        f.write_all(&footer).unwrap();
    }
    let mut fcheck = File::open(&img_path).unwrap();
    assert!(read_avb_footer(&mut fcheck).unwrap().is_some());
    drop(fcheck);

    let big_new = vec![b'Z'; 8192];
    let w3 = write_path_data(&img_path, "/data/small.txt", &big_new, true, true).unwrap();
    assert!(w3.avb_stripped.is_some());
    let img = ErofsImage::open(&img_path, 0).unwrap();
    assert_eq!(img.read_path("/data/small.txt").unwrap().0, big_new);
    assert!(img.avb_footer.is_none());

    // 7. Cross-partition MountRegistry symlink resolution check
    let mut reg = MountRegistry::default();
    reg.insert("product.img".into(), &img.walk(false).unwrap());
    let resolved = reg.resolve_symlink("system.img", "etc/prop_link", "/product/etc/build.prop");
    assert!(resolved.exists);
    assert_eq!(resolved.target_image.as_deref(), Some("product.img"));
    assert_eq!(resolved.target_internal_path.as_deref(), Some("etc/build.prop"));

    let _ = fs::remove_dir_all(&base);
}

fn create_test_symlink(link_path: &Path, target: &str) {
    #[cfg(windows)]
    {
        let mut data = Vec::from(&b"!<symlink>\xff\xfe"[..]);
        for u in target.encode_utf16() {
            data.extend_from_slice(&u.to_le_bytes());
        }
        data.extend_from_slice(&[0u8, 0u8]);
        fs::write(link_path, &data).unwrap();
        let status = std::process::Command::new("attrib")
            .arg("+S")
            .arg(link_path)
            .status()
            .expect("attrib +S failed");
        assert!(status.success());
    }
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(target, link_path).unwrap();
    }
}

#[test]
fn roundtrip_symlinks_hardlinks_sar_configs_and_large_zero_pclusters() {
    let base = make_temp_dir("sar_hardlink_zero");
    let src = base.join("src");
    fs::create_dir_all(src.join("system/bin")).unwrap();
    fs::create_dir_all(src.join("system/app/App+One(v1)")).unwrap();
    fs::create_dir_all(src.join("lost+found")).unwrap();

    fs::write(src.join("system/bin/run-as"), b"run-as-binary-payload").unwrap();
    fs::hard_link(
        src.join("system/bin/run-as"),
        src.join("system/bin/run-as-hardlink"),
    )
    .unwrap();
    create_test_symlink(&src.join("system/bin/sh"), "/system/bin/toybox");
    fs::write(src.join("system/app/App+One(v1)/App.apk"), b"apk-content").unwrap();

    let zeros_2m = vec![0u8; 2 * 1024 * 1024];
    fs::write(src.join("big_zeros.bin"), &zeros_2m).unwrap();

    let fs_cfg = base.join("system_fs_config");
    fs::write(
        &fs_cfg,
        "/ 0 0 0755\n\
         system 0 0 0755\n\
         system/system 0 0 0755\n\
         system/bin 0 2000 0755\n\
         system/bin/run-as 0 0 0644\n\
         system/system/bin 0 2000 0755\n\
         system/system/bin/run-as 0 2000 0750 capabilities=0xc0\n\
         system/system/bin/run-as-hardlink 0 2000 0750 capabilities=0xc0\n\
         system/system/bin/sh 0 2000 0755\n",
    )
    .unwrap();

    let file_ctx = base.join("system_file_contexts");
    fs::write(
        &file_ctx,
        "/ u:object_r:rootfs:s0\n\
         /system/system/app(/.*)? u:object_r:system_file:s0\n\
         /system/bin/run-as u:object_r:wrong_unprefixed:s0\n\
         /system/system/bin/.* u:object_r:bin_wildcard:s0\n\
         /system/system/bin/run-as u:object_r:first_match:s0\n\
         /system/system/bin/run-as u:object_r:runas_final:s0\n\
         /system/bin/.* u:object_r:late_unprefixed_wildcard:s0\n",
    )
    .unwrap();

    // Test 2 MiB zeros + metadata across high-ratio pcluster compressors
    for (alg, pc, lvl) in [
        (CompressionAlg::Lz4Hc, 16384, Some(9)),
        (CompressionAlg::Zstd, 8192, Some(19)),
        (CompressionAlg::Deflate, 8192, None),
        (CompressionAlg::Lzma, 8192, None),
    ] {
        let img_path = base.join(format!("sar_{}.img", alg.name()));
        pack_directory(&PackOptions {
            source_dir: src.clone(),
            output_image: img_path.clone(),
            algorithm: alg,
            level: lvl,
            pcluster_size: pc,
            mount_point: "/system".into(),
            fs_config: Some(fs_cfg.clone()),
            file_contexts: Some(file_ctx.clone()),
            timestamp: Some(1_700_000_000),
            ..Default::default()
        })
        .unwrap();

        let img = ErofsImage::open(&img_path, 0).unwrap();
        let vrep = verify_image(&img, true, false, 2).unwrap();
        assert!(vrep.ok, "verify failed for {}: {:?}", alg.name(), vrep.errors);

        let (zread, _) = img.read_path("/big_zeros.bin").unwrap();
        assert_eq!(zread.len(), zeros_2m.len());
        assert!(zread.iter().all(|&b| b == 0), "non-zero byte in {}", alg.name());

        let run_as = img.lookup("/system/bin/run-as").unwrap();
        let run_as_hl = img.lookup("/system/bin/run-as-hardlink").unwrap();
        let run_as_node = img
            .build_node("/system/bin/run-as".into(), &run_as)
            .unwrap();
        assert_eq!(run_as.nid, run_as_hl.nid);
        assert_eq!(run_as.nlink, 2);
        assert_eq!(run_as_node.capabilities, Some(0xc0));
        assert_eq!(run_as.mode & 0o7777, 0o750);
        assert_eq!(
            run_as_node.selinux.as_deref(),
            Some("u:object_r:runas_final:s0")
        );

        let sh = img.lookup("/system/bin/sh").unwrap();
        assert!(sh.is_symlink());
        assert_eq!(img.read_symlink(&sh).unwrap(), "/system/bin/toybox");
    }

    // Unpack the LZ4HC 16K image using Cygwin symlink mode & repack the unpacked tree
    let lz4hc_img = base.join("sar_lz4hc.img");
    let unpack_out = base.join("unpacked_sar");
    let img = ErofsImage::open(&lz4hc_img, 0).unwrap();
    let ex_rep = extract_image(
        &img,
        &ExtractOptions {
            out_dir: unpack_out,
            image_base_name: "system".into(),
            targets: Vec::new(),
            recursive: true,
            config_only: false,
            write_configs: true,
            overwrite: true,
            verify_digest: false,
            symlink_mode: SymlinkMode::Cygwin,
            threads: 2,
            quiet: true,
        },
    )
    .unwrap();
    assert!(ex_rep.errors.is_empty(), "unpack errors: {:?}", ex_rep.errors);
    assert_eq!(ex_rep.symlinks_created, 1);
    assert_eq!(ex_rep.symlinks_skipped, 0);
    assert!(ex_rep.hardlinks_created >= 1);

    let gen_ctx = fs::read_to_string(ex_rep.file_contexts.as_ref().unwrap()).unwrap();
    assert!(gen_ctx.contains(r"App\+One\(v1\)"), "unescaped regex in: {gen_ctx}");
    let gen_opts = fs::read_to_string(ex_rep.fs_options.as_ref().unwrap()).unwrap();
    assert!(gen_opts.contains("-zlz4 -C 16384"), "unexpected fs_options: {gen_opts}");

    // Also confirm uncompressed image omits -z in fs_options
    let raw_img = base.join("sar_none.img");
    pack_directory(&PackOptions {
        source_dir: PathBuf::from(&ex_rep.extract_dir),
        output_image: raw_img.clone(),
        algorithm: CompressionAlg::None,
        pcluster_size: 4096,
        fs_config: ex_rep.fs_config.as_ref().map(PathBuf::from),
        file_contexts: ex_rep.file_contexts.as_ref().map(PathBuf::from),
        timestamp: Some(1_700_000_000),
        ..Default::default()
    })
    .unwrap();
    let raw_opened = ErofsImage::open(&raw_img, 0).unwrap();
    let raw_vrep = verify_image(&raw_opened, true, false, 2).unwrap();
    assert!(raw_vrep.ok, "repack verify failed: {:?}", raw_vrep.errors);
    let raw_run_as = raw_opened.lookup("/system/bin/run-as").unwrap();
    let raw_run_as_node = raw_opened
        .build_node("/system/bin/run-as".into(), &raw_run_as)
        .unwrap();
    assert_eq!(raw_run_as_node.capabilities, Some(0xc0));
    assert_eq!(raw_opened.lookup("/system/bin/run-as-hardlink").unwrap().nid, raw_run_as.nid);
    let raw_sh = raw_opened.lookup("/system/bin/sh").unwrap();
    assert_eq!(raw_opened.read_symlink(&raw_sh).unwrap(), "/system/bin/toybox");

    let raw_unpack = base.join("unpacked_raw/system");
    let raw_ex = extract_image(
        &raw_opened,
        &ExtractOptions {
            out_dir: raw_unpack,
            image_base_name: "system".into(),
            targets: Vec::new(),
            recursive: true,
            config_only: true,
            write_configs: true,
            overwrite: true,
            verify_digest: false,
            symlink_mode: SymlinkMode::Auto,
            threads: 1,
            quiet: true,
        },
    )
    .unwrap();
    let raw_opts = fs::read_to_string(raw_ex.fs_options.unwrap()).unwrap();
    assert!(!raw_opts.contains("-z"), "uncompressed fs_options had -z: {raw_opts}");

    let _ = fs::remove_dir_all(&base);
}
