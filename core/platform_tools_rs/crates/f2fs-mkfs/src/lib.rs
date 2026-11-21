//! Clean-room MIT implementation of the F2FS v1.16 filesystem formatter (`make_f2fs` / `make_f2fs_casefold`).

pub mod blocks;
pub mod config;
pub mod crc32;
pub mod format;
pub mod layout;
pub mod spec;
pub mod verify;

pub use config::{CliAction, MkfsConfig, MkfsError, parse_cli_args, parse_uuid, usage_string};
pub use crc32::{f2fs_crc32_raw, f2fs_dentry_hash, f2fs_inode_checksum};
pub use format::{FormatResult, build_f2fs_blocks, format_f2fs, run_cli};
pub use layout::F2fsLayout;
pub use verify::{VerifiedF2fsInfo, verify_f2fs_image};

#[cfg(test)]
mod tests {
    use std::fs::File;

    use tempfile::tempdir;

    use super::*;

    #[test]
    fn test_format_and_verify_sparse_default_and_all_features() {
        let dir = tempdir().unwrap();

        // 1. Default 64 MiB sparse image
        let img1 = dir.path().join("default.simg");
        let args1 = vec![
            "make_f2fs".to_string(),
            "-q".to_string(),
            "-f".to_string(),
            "-S".to_string(),
            (64 * 1024 * 1024).to_string(),
            "-r".to_string(),
            "-T".to_string(),
            "1700000000".to_string(),
            "-U".to_string(),
            "11111111-2222-3333-4444-555555555555".to_string(),
            img1.display().to_string(),
        ];
        let CliAction::Format(cfg1) = parse_cli_args(&args1[1..], false).unwrap() else {
            panic!("expected Format");
        };
        format_f2fs(&cfg1).unwrap();
        let info1 = verify_f2fs_image(File::open(&img1).unwrap()).unwrap();
        assert_eq!(info1.block_count, 16384);
        assert_eq!(info1.valid_inode_count, 1);

        // 2. Casefold + all features 64 MiB sparse image
        let img2 = dir.path().join("allfeat.simg");
        let args2 = vec![
            "make_f2fs_casefold".to_string(),
            "-q".to_string(),
            "-f".to_string(),
            "-S".to_string(),
            (64 * 1024 * 1024).to_string(),
            "-r".to_string(),
            "-T".to_string(),
            "1700000000".to_string(),
            "-U".to_string(),
            "11111111-2222-3333-4444-555555555555".to_string(),
            "-O".to_string(),
            "extra_attr,inode_checksum,sb_checksum,project_quota,quota,encrypt,verity,compression,lost_found,inode_crtime,flexible_inline_xattr,casefold".to_string(),
            "-C".to_string(),
            "utf8:strict".to_string(),
            "-l".to_string(),
            "userdata".to_string(),
            img2.display().to_string(),
        ];
        let CliAction::Format(cfg2) = parse_cli_args(&args2[1..], true).unwrap() else {
            panic!("expected Format");
        };
        format_f2fs(&cfg2).unwrap();
        let info2 = verify_f2fs_image(File::open(&img2).unwrap()).unwrap();
        assert_eq!(info2.qf_ino, [4, 5, 6]);
        assert_eq!(info2.valid_inode_count, 5);
        assert_eq!(info2.next_free_nid, 8);
    }
}
