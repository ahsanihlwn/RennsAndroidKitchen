use std::fs;
use std::path::PathBuf;

use ext_cli::check::detect_type;
use ext_cli::extfs::{
    Volume, COMPAT_HAS_JOURNAL, EXT4_EXTENTS_FL, EXT4_SUPER_MAGIC, INCOMPAT_EXTENTS,
    INCOMPAT_FILETYPE, S_IFDIR, S_IFLNK, S_IFREG,
};
use ext_cli::unpack::unpack_image;

fn write_u16(buf: &mut [u8], off: usize, v: u16) {
    buf[off..off + 2].copy_from_slice(&v.to_le_bytes());
}

fn write_u32(buf: &mut [u8], off: usize, v: u32) {
    buf[off..off + 4].copy_from_slice(&v.to_le_bytes());
}

fn build_ext2_or_ext3_image(with_journal: bool) -> Vec<u8> {
    // 1024-byte blocks, 512 blocks total = 512 KiB
    let bs = 1024usize;
    let total_blocks = 512u32;
    let mut img = vec![0u8; total_blocks as usize * bs];

    // Superblock at offset 1024 (block 1)
    let sb = &mut img[1024..2048];
    write_u32(sb, 0x00, 64); // s_inodes_count
    write_u32(sb, 0x04, total_blocks); // s_blocks_count_lo
    write_u32(sb, 0x14, 1); // s_first_data_block (1 for 1024-byte blocks)
    write_u32(sb, 0x18, 0); // s_log_block_size (1024 << 0)
    write_u32(sb, 0x20, total_blocks); // s_blocks_per_group
    write_u32(sb, 0x28, 64); // s_inodes_per_group
    write_u16(sb, 0x38, EXT4_SUPER_MAGIC);
    write_u32(sb, 0x4C, 1); // s_rev_level = 1
    write_u16(sb, 0x58, 128); // s_inode_size = 128 (EXT2/EXT3)
    write_u32(sb, 0x5C, if with_journal { COMPAT_HAS_JOURNAL } else { 0 });
    write_u32(sb, 0x60, INCOMPAT_FILETYPE);
    write_u16(sb, 0xFE, 32);

    // GDT at block 2 (offset 2048): inode table at block 5
    let gdt = &mut img[2 * bs..3 * bs];
    write_u32(gdt, 0x08, 5); // bg_inode_table

    // Inode table starts at block 5 (64 * 128 = 8192 bytes -> blocks 5..13)
    let it_off = 5 * bs;

    // Root inode (#2) at it_off + 1 * 128, data at block 20
    let ino2 = &mut img[it_off + 128..it_off + 256];
    write_u16(ino2, 0x00, S_IFDIR | 0o755);
    write_u32(ino2, 0x04, bs as u32);
    write_u32(ino2, 0x28, 20); // i_block[0] = 20

    // Big file inode (#11) at it_off + 10 * 128: 15 blocks = 15360 bytes (uses 12 direct + 3 in singly indirect block 35!)
    let big_len = 15 * bs;
    let ino11 = &mut img[it_off + 10 * 128..it_off + 11 * 128];
    write_u16(ino11, 0x00, S_IFREG | 0o644);
    write_u32(ino11, 0x04, big_len as u32);
    for i in 0..12 {
        write_u32(ino11, 0x28 + i * 4, (21 + i) as u32);
    }
    write_u32(ino11, 0x28 + 12 * 4, 35); // singly indirect block = 35

    // Fill indirect block 35 with blocks 36, 37, 38
    write_u32(&mut img[35 * bs..35 * bs + 4], 0, 36);
    write_u32(&mut img[35 * bs + 4..35 * bs + 8], 0, 37);
    write_u32(&mut img[35 * bs + 8..35 * bs + 12], 0, 38);

    // Fill data blocks 21..33 and 36..39 with deterministic pattern
    for blk_idx in 0..15u32 {
        let disk_blk = if blk_idx < 12 { 21 + blk_idx } else { 36 + (blk_idx - 12) };
        let start = disk_blk as usize * bs;
        img[start..start + bs].fill((blk_idx as u8).wrapping_add(0x41));
    }

    // Fast symlink inode (#12) -> "target_abc"
    let ino12 = &mut img[it_off + 11 * 128..it_off + 12 * 128];
    write_u16(ino12, 0x00, S_IFLNK | 0o777);
    write_u32(ino12, 0x04, 10);
    ino12[0x28..0x28 + 10].copy_from_slice(b"target_abc");

    // Root directory entries at block 20
    let dir = &mut img[20 * bs..21 * bs];
    // "."
    write_u32(dir, 0, 2);
    write_u16(dir, 4, 12);
    dir[6] = 1;
    dir[7] = 2;
    dir[8] = b'.';
    // ".."
    write_u32(dir, 12, 2);
    write_u16(dir, 16, 12);
    dir[18] = 2;
    dir[19] = 2;
    dir[20..22].copy_from_slice(b"..");
    // "big.bin" (ino 11)
    write_u32(dir, 24, 11);
    write_u16(dir, 28, 16);
    dir[30] = 7;
    dir[31] = 1;
    dir[32..39].copy_from_slice(b"big.bin");
    // "lnk" (ino 12)
    write_u32(dir, 40, 12);
    write_u16(dir, 44, (bs - 40) as u16);
    dir[46] = 3;
    dir[47] = 7;
    dir[48..51].copy_from_slice(b"lnk");

    img
}

#[test]
fn test_ext2_and_ext3_indirect_and_unpack() {
    let mut dir = std::env::temp_dir();
    dir.push(format!("ext_cli_test_ext23_{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();

    for (name, journal, expected_fmt) in [
        ("part2.img", false, "EXT2"),
        ("part3.img", true, "EXT3"),
    ] {
        let img_path = dir.join(name);
        fs::write(&img_path, build_ext2_or_ext3_image(journal)).unwrap();

        let vol = Volume::open(fs::File::open(&img_path).unwrap()).unwrap();
        let det = detect_type(&vol.superblock);
        assert_eq!(det.fs_type, expected_fmt);
        assert_eq!(det.has_journal, journal);
        assert!(!det.has_extents);
        drop(vol);

        let out_dir = unpack_image(&img_path).unwrap();
        let big = fs::read(out_dir.join("big.bin")).unwrap();
        assert_eq!(big.len(), 15 * 1024);
        for blk_idx in 0..15 {
            let expected = (blk_idx as u8).wrapping_add(0x41);
            assert!(big[blk_idx * 1024..(blk_idx + 1) * 1024]
                .iter()
                .all(|&b| b == expected));
        }

        let part_stem = img_path.file_stem().unwrap().to_str().unwrap();
        let fs_cfg = fs::read_to_string(dir.join("config").join(format!("{}_fs_config", part_stem))).unwrap();
        assert!(fs_cfg.contains(&format!("{}/big.bin 0 0 0644", part_stem)));
        assert!(fs_cfg.contains(&format!("{}/lnk 0 0 0777 target_abc", part_stem)));
    }

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_ext4_sparse_and_xattr_capabilities() {
    let mut dir = std::env::temp_dir();
    dir.push(format!("ext_cli_test_sparse_{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();

    // Build a 4096-byte block EXT4 image with 64-bit capabilities xattr & selinux xattr
    let bs = 4096usize;
    let total_blocks = 128u32;
    let mut img = vec![0u8; total_blocks as usize * bs];

    let sb = &mut img[1024..2048];
    write_u32(sb, 0x00, 64);
    write_u32(sb, 0x04, total_blocks);
    write_u32(sb, 0x18, 2); // 1024 << 2 = 4096
    write_u32(sb, 0x20, total_blocks);
    write_u32(sb, 0x28, 64);
    write_u16(sb, 0x38, EXT4_SUPER_MAGIC);
    write_u32(sb, 0x4C, 1);
    write_u16(sb, 0x58, 256); // EXT4 inode size
    write_u32(sb, 0x5C, COMPAT_HAS_JOURNAL);
    write_u32(sb, 0x60, INCOMPAT_FILETYPE | INCOMPAT_EXTENTS);
    write_u16(sb, 0xFE, 32);

    // GDT at block 1 (offset 4096): inode table at block 4
    write_u32(&mut img[bs..bs + 32], 0x08, 4);

    let it_off = 4 * bs;
    // Root inode (#2), extent at block 10
    {
        let ino2 = &mut img[it_off + 256..it_off + 512];
        write_u16(ino2, 0x00, S_IFDIR | 0o755);
        write_u32(ino2, 0x04, bs as u32);
        write_u32(ino2, 0x20, EXT4_EXTENTS_FL);
        write_u16(ino2, 0x28, 0xF30A);
        write_u16(ino2, 0x2A, 1);
        write_u16(ino2, 0x2C, 3);
        write_u32(ino2, 0x34, 0);
        write_u16(ino2, 0x38, 1);
        write_u32(ino2, 0x3C, 10);
    }

    // File inode (#11): has 64-bit capability (CAP_NET_RAW (13) | CAP_BLOCK_SUSPEND (36))
    {
        let ino11 = &mut img[it_off + 10 * 256..it_off + 11 * 256];
        write_u16(ino11, 0x00, S_IFREG | 0o750);
        write_u32(ino11, 0x04, 5);
        write_u32(ino11, 0x20, EXT4_EXTENTS_FL);
        write_u16(ino11, 0x28, 0xF30A);
        write_u16(ino11, 0x2A, 1);
        write_u16(ino11, 0x2C, 3);
        write_u16(ino11, 0x38, 1);
        write_u32(ino11, 0x3C, 11);
        write_u16(ino11, 0x80, 28); // i_extra_isize

        // Inline xattrs starting at 128 + 28 = 156
        write_u32(ino11, 156, 0xEA02_0000);
        // Entry 1: security.selinux
        let e1 = 160;
        ino11[e1] = 7;
        ino11[e1 + 1] = 6; // security.
        write_u16(ino11, e1 + 2, 72); // relative to 160 -> offset 232
        write_u32(ino11, e1 + 8, 22);
        ino11[e1 + 16..e1 + 23].copy_from_slice(b"selinux");
        ino11[160 + 72..160 + 72 + 22].copy_from_slice(b"u:object_r:foo_exec:s0");
        // Entry 2: security.capability
        let e2 = 160 + 24;
        ino11[e2] = 10;
        ino11[e2 + 1] = 6;
        write_u16(ino11, e2 + 2, 52); // relative to 160 -> offset 212
        write_u32(ino11, e2 + 8, 20);
        ino11[e2 + 16..e2 + 26].copy_from_slice(b"capability");
        let cap_off = 160 + 52;
        write_u32(ino11, cap_off, 0x0200_0001);
        write_u32(ino11, cap_off + 4, 1 << 13); // low 32
        write_u32(ino11, cap_off + 12, 1 << 4); // high 32 (bit 36)
    }

    img[11 * bs..11 * bs + 5].copy_from_slice(b"hello");

    let dir_blk = &mut img[10 * bs..11 * bs];
    write_u32(dir_blk, 0, 2);
    write_u16(dir_blk, 4, 12);
    dir_blk[6] = 1;
    dir_blk[7] = 2;
    dir_blk[8] = b'.';
    write_u32(dir_blk, 12, 2);
    write_u16(dir_blk, 16, 12);
    dir_blk[18] = 2;
    dir_blk[19] = 2;
    dir_blk[20..22].copy_from_slice(b"..");
    write_u32(dir_blk, 24, 11);
    write_u16(dir_blk, 28, (bs - 24) as u16);
    dir_blk[30] = 3;
    dir_blk[31] = 1;
    dir_blk[32..35].copy_from_slice(b"bin");

    // Wrap raw image in Android sparse container (1 RAW chunk)
    let mut sparse = Vec::new();
    sparse.extend_from_slice(&0xED26_FF3Au32.to_le_bytes());
    sparse.extend_from_slice(&1u16.to_le_bytes());
    sparse.extend_from_slice(&0u16.to_le_bytes());
    sparse.extend_from_slice(&28u16.to_le_bytes());
    sparse.extend_from_slice(&12u16.to_le_bytes());
    sparse.extend_from_slice(&(bs as u32).to_le_bytes());
    sparse.extend_from_slice(&total_blocks.to_le_bytes());
    sparse.extend_from_slice(&1u32.to_le_bytes());
    sparse.extend_from_slice(&0u32.to_le_bytes());
    sparse.extend_from_slice(&0xCAC1u16.to_le_bytes());
    sparse.extend_from_slice(&0u16.to_le_bytes());
    sparse.extend_from_slice(&total_blocks.to_le_bytes());
    sparse.extend_from_slice(&(12 + img.len() as u32).to_le_bytes());
    sparse.extend_from_slice(&img);

    let sp_path = dir.join("sys.img");
    fs::write(&sp_path, &sparse).unwrap();
    let out_dir = unpack_image(&sp_path).unwrap();
    assert_eq!(fs::read(out_dir.join("bin")).unwrap(), b"hello");

    let cfg = fs::read_to_string(dir.join("config").join("sys_fs_config")).unwrap();
    let expected_cap = (1u64 << 36) | (1u64 << 13);
    assert!(
        cfg.contains(&format!("sys/bin 0 0 0750 capabilities={:#x}", expected_cap)),
        "got cfg:\n{}",
        cfg
    );

    let fc = fs::read_to_string(dir.join("config").join("sys_file_contexts")).unwrap();
    assert!(fc.contains("/sys/bin u:object_r:foo_exec:s0"));
    assert!(fc.contains("/sys/lost\\+found "));

    let _ = fs::remove_dir_all(&dir);
    let _ = PathBuf::new();
}

#[test]
fn test_fast_symlink_with_external_xattr_and_detect_type_256_ext3() {
    use ext_cli::extfs::Superblock;

    // 1) mke2fs-style 256-byte inode EXT2 & EXT3 (no extents/ext4 features) must NOT detect as EXT4
    let mut raw_sb = [0u8; 1024];
    write_u16(&mut raw_sb, 0x38, EXT4_SUPER_MAGIC);
    write_u32(&mut raw_sb, 0x4C, 1);
    write_u16(&mut raw_sb, 0x58, 256);
    write_u32(&mut raw_sb, 0x60, INCOMPAT_FILETYPE);
    write_u32(&mut raw_sb, 0x5C, COMPAT_HAS_JOURNAL);
    assert_eq!(detect_type(&Superblock::parse(&raw_sb)).fs_type, "EXT3");

    write_u32(&mut raw_sb, 0x5C, 0);
    assert_eq!(detect_type(&Superblock::parse(&raw_sb)).fs_type, "EXT2");

    // EXT4 with 128-byte inode + extents
    write_u16(&mut raw_sb, 0x58, 128);
    write_u32(&mut raw_sb, 0x60, INCOMPAT_FILETYPE | INCOMPAT_EXTENTS);
    assert_eq!(detect_type(&Superblock::parse(&raw_sb)).fs_type, "EXT4");

    // 2) Fast symlink with i_file_acl != 0 (i_blocks_lo = bs/512 = 8) + filename ending in " (2)"
    let bs = 4096usize;
    let total_blocks = 64u32;
    let mut img = vec![0u8; total_blocks as usize * bs];
    let sb = &mut img[1024..2048];
    write_u32(sb, 0x00, 64);
    write_u32(sb, 0x04, total_blocks);
    write_u32(sb, 0x18, 2);
    write_u32(sb, 0x20, total_blocks);
    write_u32(sb, 0x28, 64);
    write_u16(sb, 0x38, EXT4_SUPER_MAGIC);
    write_u32(sb, 0x4C, 1);
    write_u16(sb, 0x58, 256);
    write_u32(sb, 0x60, INCOMPAT_FILETYPE | INCOMPAT_EXTENTS);
    write_u16(sb, 0xFE, 32);

    write_u32(&mut img[bs..bs + 32], 0x08, 4);
    let it_off = 4 * bs;

    // Root dir inode (#2) at block 10
    {
        let ino2 = &mut img[it_off + 256..it_off + 512];
        write_u16(ino2, 0x00, S_IFDIR | 0o755);
        write_u32(ino2, 0x04, bs as u32);
        write_u32(ino2, 0x20, EXT4_EXTENTS_FL);
        write_u16(ino2, 0x28, 0xF30A);
        write_u16(ino2, 0x2A, 1);
        write_u16(ino2, 0x2C, 3);
        write_u16(ino2, 0x38, 1);
        write_u32(ino2, 0x3C, 10);
    }

    // Fast symlink inode (#11) pointing to "/system/bin/sh", WITH external xattr block 12 (i_blocks_lo = 8)
    {
        let ino11 = &mut img[it_off + 10 * 256..it_off + 11 * 256];
        write_u16(ino11, 0x00, S_IFLNK | 0o777);
        write_u32(ino11, 0x04, 14);
        write_u32(ino11, 0x1C, 8); // bs/512 for external xattr block
        ino11[0x28..0x28 + 14].copy_from_slice(b"/system/bin/sh");
        write_u32(ino11, 0x68, 12); // i_file_acl_lo = block 12
    }

    // External xattr block 12 with security.selinux
    {
        let xb = &mut img[12 * bs..13 * bs];
        write_u32(xb, 0, 0xEA02_0000);
        write_u32(xb, 4, 1);
        write_u32(xb, 8, 1);
        let e = 32;
        xb[e] = 7;
        xb[e + 1] = 6;
        write_u16(xb, e + 2, 128);
        write_u32(xb, e + 8, 22);
        xb[e + 16..e + 23].copy_from_slice(b"selinux");
        xb[128..128 + 22].copy_from_slice(b"u:object_r:foo_exec:s0");
    }

    // Regular file inode (#12) named "app (2).apk"
    {
        let ino12 = &mut img[it_off + 11 * 256..it_off + 12 * 256];
        write_u16(ino12, 0x00, S_IFREG | 0o644);
        write_u32(ino12, 0x04, 3);
        write_u32(ino12, 0x20, EXT4_EXTENTS_FL);
        write_u16(ino12, 0x28, 0xF30A);
        write_u16(ino12, 0x2A, 1);
        write_u16(ino12, 0x2C, 3);
        write_u16(ino12, 0x38, 1);
        write_u32(ino12, 0x3C, 11);
    }
    img[11 * bs..11 * bs + 3].copy_from_slice(b"apk");

    let dir_blk = &mut img[10 * bs..11 * bs];
    write_u32(dir_blk, 0, 2);
    write_u16(dir_blk, 4, 12);
    dir_blk[6] = 1;
    dir_blk[7] = 2;
    dir_blk[8] = b'.';
    write_u32(dir_blk, 12, 2);
    write_u16(dir_blk, 16, 12);
    dir_blk[18] = 2;
    dir_blk[19] = 2;
    dir_blk[20..22].copy_from_slice(b"..");
    write_u32(dir_blk, 24, 11);
    write_u16(dir_blk, 28, 12);
    dir_blk[30] = 2;
    dir_blk[31] = 7;
    dir_blk[32..34].copy_from_slice(b"sh");
    write_u32(dir_blk, 36, 12);
    write_u16(dir_blk, 40, (bs - 36) as u16);
    dir_blk[42] = 11;
    dir_blk[43] = 1;
    dir_blk[44..55].copy_from_slice(b"app (2).apk");

    let mut dir = std::env::temp_dir();
    dir.push(format!("ext_cli_test_regress_{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    let p = dir.join("reg.img");
    fs::write(&p, &img).unwrap();

    let out_dir = unpack_image(&p).unwrap();
    assert_eq!(fs::read(out_dir.join("app_(2).apk")).unwrap(), b"apk");
    let cfg = fs::read_to_string(dir.join("config").join("reg_fs_config")).unwrap();
    assert!(cfg.contains("reg/sh 0 0 0777 /system/bin/sh"), "got:\n{}", cfg);
    assert!(cfg.contains("reg/app_(2).apk 0 0 0644"), "got:\n{}", cfg);
    let fc = fs::read_to_string(dir.join("config").join("reg_file_contexts")).unwrap();
    assert!(fc.contains("/reg/sh u:object_r:foo_exec:s0"));

    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn test_root_inode_ownership_and_cap_revision_1() {
    let bs = 4096usize;
    let total_blocks = 64u32;
    let mut img = vec![0u8; total_blocks as usize * bs];

    let sb = &mut img[1024..2048];
    write_u32(sb, 0x00, 64);
    write_u32(sb, 0x04, total_blocks);
    write_u32(sb, 0x18, 2); // 1024 << 2 = 4096
    write_u32(sb, 0x20, total_blocks);
    write_u32(sb, 0x28, 64);
    write_u16(sb, 0x38, EXT4_SUPER_MAGIC);
    write_u32(sb, 0x4C, 1);
    write_u16(sb, 0x58, 256);
    write_u32(sb, 0x60, INCOMPAT_FILETYPE | INCOMPAT_EXTENTS);
    write_u16(sb, 0xFE, 32);

    write_u32(&mut img[bs..bs + 32], 0x08, 4);
    let it_off = 4 * bs;

    // Root inode (#2): custom uid/gid (including high 16-bit halves) and sticky/setgid bits
    {
        let ino2 = &mut img[it_off + 256..it_off + 512];
        write_u16(ino2, 0x00, S_IFDIR | 0o2751);
        write_u16(ino2, 0x02, 1000); // i_uid_lo
        write_u32(ino2, 0x04, bs as u32);
        write_u16(ino2, 0x18, 2000); // i_gid_lo
        write_u16(ino2, 0x78, 1); // l_i_uid_high -> (1 << 16) + 1000 = 66536
        write_u16(ino2, 0x7A, 2); // l_i_gid_high -> (2 << 16) + 2000 = 133072
        write_u32(ino2, 0x20, EXT4_EXTENTS_FL);
        write_u16(ino2, 0x28, 0xF30A);
        write_u16(ino2, 0x2A, 1);
        write_u16(ino2, 0x2C, 3);
        write_u16(ino2, 0x38, 1);
        write_u32(ino2, 0x3C, 10);
    }

    // File inode (#11): inline VFS_CAP_REVISION_1 (12 bytes) security.capability
    {
        let ino11 = &mut img[it_off + 10 * 256..it_off + 11 * 256];
        write_u16(ino11, 0x00, S_IFREG | 0o755);
        write_u32(ino11, 0x04, 2);
        write_u32(ino11, 0x20, EXT4_EXTENTS_FL);
        write_u16(ino11, 0x28, 0xF30A);
        write_u16(ino11, 0x2A, 1);
        write_u16(ino11, 0x2C, 3);
        write_u16(ino11, 0x38, 1);
        write_u32(ino11, 0x3C, 11);
        write_u16(ino11, 0x80, 28); // i_extra_isize

        write_u32(ino11, 156, 0xEA02_0000);
        let e1 = 160;
        ino11[e1] = 10;
        ino11[e1 + 1] = 6; // security.
        write_u16(ino11, e1 + 2, 32); // relative to 160 -> offset 192
        write_u32(ino11, e1 + 8, 12); // VFS_CAP_REVISION_1 payload size
        ino11[e1 + 16..e1 + 26].copy_from_slice(b"capability");
        let cap_off = 160 + 32;
        write_u32(ino11, cap_off, 0x0100_0001); // VFS_CAP_REVISION_1 | effective
        write_u32(ino11, cap_off + 4, 1 << 12); // permitted low (CAP_NET_ADMIN)
        write_u32(ino11, cap_off + 8, 0); // inheritable low
    }

    img[11 * bs..11 * bs + 2].copy_from_slice(b"ok");

    let dir_blk = &mut img[10 * bs..11 * bs];
    write_u32(dir_blk, 0, 2);
    write_u16(dir_blk, 4, 12);
    dir_blk[6] = 1;
    dir_blk[7] = 2;
    dir_blk[8] = b'.';
    write_u32(dir_blk, 12, 2);
    write_u16(dir_blk, 16, 12);
    dir_blk[18] = 2;
    dir_blk[19] = 2;
    dir_blk[20..22].copy_from_slice(b"..");
    write_u32(dir_blk, 24, 11);
    write_u16(dir_blk, 28, (bs - 24) as u16);
    dir_blk[30] = 4;
    dir_blk[31] = 1;
    dir_blk[32..36].copy_from_slice(b"cap1");

    let mut dir = std::env::temp_dir();
    dir.push(format!("ext_cli_test_cap1_{}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    let p = dir.join("captest.img");
    fs::write(&p, &img).unwrap();

    let out_dir = unpack_image(&p).unwrap();
    assert_eq!(fs::read(out_dir.join("cap1")).unwrap(), b"ok");
    let cfg = fs::read_to_string(dir.join("config").join("captest_fs_config")).unwrap();
    assert!(cfg.contains("/ 66536 133072 2751\n"), "got:\n{}", cfg);
    assert!(cfg.contains("captest 66536 133072 2751\n"), "got:\n{}", cfg);
    assert!(
        cfg.contains("captest/cap1 0 0 0755 capabilities=0x1000"),
        "got:\n{}",
        cfg
    );

    let _ = fs::remove_dir_all(&dir);
}
