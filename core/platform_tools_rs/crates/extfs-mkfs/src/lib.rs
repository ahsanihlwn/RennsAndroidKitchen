//! Clean-room MIT Ext2 / Ext3 / Ext4 filesystem formatter library (`extfs-mkfs`).

pub mod blocks;
pub mod config;
pub mod crc;
pub mod format;
pub mod layout;
pub mod spec;

pub use config::{parse_cli_args, CliAction, ExtMkfsConfig, ExtMkfsError};
pub use format::{format_extfs, run_cli};
pub use layout::ExtFsLayout;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::spec::*;

    #[test]
    fn test_format_ext4_4k_and_sparse() {
        let dir = tempfile::tempdir().unwrap();
        let raw_path = dir.path().join("ext4_raw.img");
        let sp_path = dir.path().join("ext4_sp.img");

        let args_raw: Vec<String> = vec![
            "-F",
            "-q",
            "-t",
            "ext4",
            "-b",
            "4096",
            "-I",
            "256",
            "-U",
            "11111111-2222-3333-4444-555555555555",
            "-E",
            "hash_seed=22222222-3333-4444-5555-666666666666",
            raw_path.to_str().unwrap(),
            "16384",
        ]
        .into_iter()
        .map(String::from)
        .collect();

        let CliAction::Format(mut cfg_raw) = parse_cli_args(&args_raw).unwrap() else {
            panic!("expected Format");
        };
        cfg_raw.timestamp = 1_700_000_000;
        let layout = format_extfs(cfg_raw).unwrap();
        assert_eq!(layout.total_blocks, 16384);
        assert_eq!(layout.total_inodes, 16384);
        assert_eq!(layout.total_free_blocks, 14327);
        assert_eq!(layout.total_free_inodes, 16373);
        assert_eq!(layout.overhead_clusters, 2052);

        let raw_bytes = std::fs::read(&raw_path).unwrap();
        assert_eq!(raw_bytes.len(), 64 * 1024 * 1024);
        let magic = u16::from_le_bytes(raw_bytes[1024 + 56..1024 + 58].try_into().unwrap());
        assert_eq!(magic, EXT4_SUPER_MAGIC);

        // Also format sparse and verify unsparsed bytes match raw_bytes 100%!
        let args_sp: Vec<String> = vec![
            "-F",
            "-q",
            "-t",
            "ext4",
            "-b",
            "4096",
            "-I",
            "256",
            "-U",
            "11111111-2222-3333-4444-555555555555",
            "-E",
            "android_sparse,hash_seed=22222222-3333-4444-5555-666666666666",
            sp_path.to_str().unwrap(),
            "16384",
        ]
        .into_iter()
        .map(String::from)
        .collect();

        let CliAction::Format(mut cfg_sp) = parse_cli_args(&args_sp).unwrap() else {
            panic!("expected Format");
        };
        cfg_sp.timestamp = 1_700_000_000;
        format_extfs(cfg_sp).unwrap();

        let sp_file = std::fs::File::open(&sp_path).unwrap();
        let mut unsparsed = std::io::Cursor::new(Vec::new());
        android_sparse::unsparse(sp_file, &mut unsparsed, false).unwrap();
        assert_eq!(raw_bytes, unsparsed.into_inner());
    }
}
