//! CLI argument and `mke2fs.conf` parser for `mke2fs` (`extfs-mkfs`).

use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use thiserror::Error;

use crate::spec::*;

/// Built-in default `mke2fs.conf` matching `platform-tools/mke2fs.conf`.
pub const BUILTIN_MKE2FS_CONF: &str = include_str!("../../../config/mke2fs.conf");

/// Errors produced by `extfs-mkfs`.
#[derive(Debug, Error)]
pub enum ExtMkfsError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("Sparse image error: {0}")]
    Sparse(#[from] android_sparse::SparseError),
    #[error("Invalid option or argument: {0}")]
    InvalidArgument(String),
    #[error("Device or image path not specified")]
    DeviceNotSpecified,
    #[error("Filesystem volume is too small ({0} blocks)")]
    VolumeTooSmall(u64),
}

/// CLI action returned by `parse_cli_args`.
#[derive(Debug, Clone)]
#[allow(clippy::large_enum_variant)]
pub enum CliAction {
    ShowVersion,
    ShowUsage { exit_code: i32 },
    Format(ExtMkfsConfig),
}

/// Complete configuration for formatting an Ext2/Ext3/Ext4 filesystem.
#[derive(Debug, Clone)]
pub struct ExtMkfsConfig {
    pub device_path: PathBuf,
    pub blocks_count_arg: Option<u64>,
    pub block_size: u32,
    pub block_size_explicit: bool,
    pub cluster_size: Option<u32>,
    pub inode_size: u16,
    pub inode_size_explicit: bool,
    pub inode_ratio: u32,
    pub inode_ratio_explicit: bool,
    pub num_inodes: Option<u32>,
    pub blocks_per_group: Option<u32>,
    pub flex_bg_size: Option<u32>,
    pub reserved_ratio: f64,
    pub creator_os: u32,
    pub rev_level: u32,
    pub errors_behavior: u16,
    pub feature_compat: u32,
    pub feature_incompat: u32,
    pub feature_ro_compat: u32,
    pub default_mntopts: u32,
    pub super_flags: u32,
    pub encoding: u16,
    pub encoding_flags: u16,
    pub label: String,
    pub last_mounted: String,
    pub uuid: [u8; 16],
    pub hash_seed: [u8; 16],
    pub root_uid: u32,
    pub root_gid: u32,
    pub timestamp: u32,
    pub journal_blocks: Option<u32>,
    pub android_sparse: bool,
    pub lazy_itable_init: bool,
    pub lazy_journal_init: bool,
    pub root_dir: Option<PathBuf>,
    pub offset_bytes: u64,
    pub stride: u16,
    pub stripe_width: u32,
    pub dry_run: bool,
    pub super_only: bool,
    pub quiet: bool,
    pub verbose: bool,
    pub force: bool,
    pub fs_type: String,
    pub usage_type: Option<String>,
}

impl Default for ExtMkfsConfig {
    fn default() -> Self {
        let now = detect_timestamp();
        let mut uuid = [0u8; 16];
        let _ = getrandom::fill(&mut uuid);
        uuid[6] = (uuid[6] & 0x0f) | 0x40;
        uuid[8] = (uuid[8] & 0x3f) | 0x80;

        let mut hash_seed = [0u8; 16];
        let _ = getrandom::fill(&mut hash_seed);
        hash_seed[6] = (hash_seed[6] & 0x0f) | 0x40;
        hash_seed[8] = (hash_seed[8] & 0x3f) | 0x80;

        Self {
            device_path: PathBuf::new(),
            blocks_count_arg: None,
            block_size: 4096,
            block_size_explicit: false,
            cluster_size: None,
            inode_size: 256,
            inode_size_explicit: false,
            inode_ratio: 16384,
            inode_ratio_explicit: false,
            num_inodes: None,
            blocks_per_group: None,
            flex_bg_size: None,
            reserved_ratio: 1.0,
            creator_os: EXT2_OS_LINUX,
            rev_level: EXT2_DYNAMIC_REV,
            errors_behavior: EXT2_ERRORS_CONTINUE,
            // [defaults] base_features = sparse_super,large_file,filetype,dir_index,ext_attr
            feature_compat: EXT2_FEATURE_COMPAT_DIR_INDEX | EXT2_FEATURE_COMPAT_EXT_ATTR,
            feature_incompat: EXT2_FEATURE_INCOMPAT_FILETYPE,
            feature_ro_compat: EXT2_FEATURE_RO_COMPAT_SPARSE_SUPER
                | EXT2_FEATURE_RO_COMPAT_LARGE_FILE,
            // [defaults] default_mntopts = acl,user_xattr
            default_mntopts: EXT2_DEFM_ACL | EXT2_DEFM_XATTR_USER,
            super_flags: EXT2_FLAGS_SIGNED_HASH,
            encoding: 0,
            encoding_flags: 0,
            label: String::new(),
            last_mounted: String::new(),
            uuid,
            hash_seed,
            root_uid: 0,
            root_gid: 0,
            timestamp: now,
            journal_blocks: None,
            android_sparse: false,
            lazy_itable_init: true,
            lazy_journal_init: true,
            root_dir: None,
            offset_bytes: 0,
            stride: 0,
            stripe_width: 0,
            dry_run: false,
            super_only: false,
            quiet: false,
            verbose: false,
            force: false,
            fs_type: "ext2".to_string(),
            usage_type: None,
        }
    }
}

fn detect_timestamp() -> u32 {
    for var in ["E2FSPROGS_FAKE_TIME", "SOURCE_DATE_EPOCH"] {
        if let Ok(val) = std::env::var(var) {
            if let Ok(ts) = val.trim().parse::<u64>() {
                return ts as u32;
            }
        }
    }
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as u32)
        .unwrap_or(0)
}

/// Usage string matching `platform-tools/mke2fs` v1.47.2.
pub fn usage_string() -> &'static str {
    "Usage: mke2fs [-c|-l filename] [-b block-size] [-C cluster-size]\n\
\t[-i bytes-per-inode] [-I inode-size] [-J journal-options]\n\
\t[-G flex-group-size] [-N number-of-inodes] [-d root-directory|tarball]\n\
\t[-m reserved-blocks-percentage] [-o creator-os]\n\
\t[-g blocks-per-group] [-L volume-label] [-M last-mounted-directory]\n\
\t[-O feature[,...]] [-E extended-option[,...]] [-t fs-type]\n\
\t[-T usage-type ] [-U UUID] [-e errors_behavior][-z undo_file]\n\
\t[-jnqvDFSV] device [blocks-count]\n"
}

/// Version banner printed to stderr by `mke2fs`.
pub fn version_banner() -> &'static str {
    "mke2fs 1.47.2 (1-Jan-2025)\n"
}

/// Full `-V` output printed to stderr by `mke2fs -V`.
pub fn full_version_banner() -> &'static str {
    "mke2fs 1.47.2 (1-Jan-2025)\n\tUsing EXT2FS Library version android-platform-15.0.0_r5-314-ga1f793f6b\n"
}

/// Parses a UUID string (`clear`, `null`, `random`, `time`, or 36-char hex UUID).
pub fn parse_uuid(s: &str) -> Result<[u8; 16], ExtMkfsError> {
    match s.trim() {
        "clear" | "null" => return Ok([0u8; 16]),
        "random" | "time" => {
            let mut u = [0u8; 16];
            let _ = getrandom::fill(&mut u);
            u[6] = (u[6] & 0x0f) | 0x40;
            u[8] = (u[8] & 0x3f) | 0x80;
            return Ok(u);
        }
        _ => {}
    }
    let hex: String = s.chars().filter(|&c| c != '-').collect();
    if hex.len() != 32 {
        return Err(ExtMkfsError::InvalidArgument(format!("invalid UUID '{s}'")));
    }
    let mut out = [0u8; 16];
    for i in 0..16 {
        out[i] = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16)
            .map_err(|_| ExtMkfsError::InvalidArgument(format!("invalid UUID '{s}'")))?;
    }
    Ok(out)
}

/// Applies an `[fs_types]` profile from `mke2fs.conf`.
pub fn apply_fs_type_profile(cfg: &mut ExtMkfsConfig, profile: &str) {
    match profile {
        "ext2" => {}
        "ext3" => {
            cfg.feature_compat |= EXT3_FEATURE_COMPAT_HAS_JOURNAL;
        }
        "ext4" => {
            cfg.feature_compat |= EXT3_FEATURE_COMPAT_HAS_JOURNAL;
            cfg.feature_incompat |= EXT4_FEATURE_INCOMPAT_EXTENTS;
            cfg.feature_ro_compat |= EXT4_FEATURE_RO_COMPAT_HUGE_FILE
                | EXT4_FEATURE_RO_COMPAT_DIR_NLINK
                | EXT4_FEATURE_RO_COMPAT_EXTRA_ISIZE
                | EXT4_FEATURE_RO_COMPAT_GDT_CSUM;
            if !cfg.inode_size_explicit {
                cfg.inode_size = 256;
            }
        }
        "ext4dev" => {
            cfg.feature_compat |= EXT3_FEATURE_COMPAT_HAS_JOURNAL;
            cfg.feature_incompat |= EXT4_FEATURE_INCOMPAT_EXTENTS
                | EXT4_FEATURE_INCOMPAT_FLEX_BG
                | EXT4_FEATURE_INCOMPAT_INLINE_DATA
                | EXT4_FEATURE_INCOMPAT_64BIT;
            cfg.feature_ro_compat |= EXT4_FEATURE_RO_COMPAT_HUGE_FILE
                | EXT4_FEATURE_RO_COMPAT_DIR_NLINK
                | EXT4_FEATURE_RO_COMPAT_EXTRA_ISIZE;
            if !cfg.inode_size_explicit {
                cfg.inode_size = 256;
            }
            cfg.super_flags |= EXT2_FLAGS_TEST_FILESYS;
        }
        "small" => {
            if !cfg.block_size_explicit {
                cfg.block_size = 1024;
            }
            if !cfg.inode_size_explicit {
                cfg.inode_size = 128;
            }
            if !cfg.inode_ratio_explicit {
                cfg.inode_ratio = 4096;
            }
        }
        "floppy" => {
            if !cfg.block_size_explicit {
                cfg.block_size = 1024;
            }
            if !cfg.inode_size_explicit {
                cfg.inode_size = 128;
            }
            if !cfg.inode_ratio_explicit {
                cfg.inode_ratio = 8192;
            }
        }
        "big" => {
            if !cfg.inode_ratio_explicit {
                cfg.inode_ratio = 32768;
            }
        }
        "huge" => {
            if !cfg.inode_ratio_explicit {
                cfg.inode_ratio = 65536;
            }
        }
        "news" => {
            if !cfg.inode_ratio_explicit {
                cfg.inode_ratio = 4096;
            }
        }
        "largefile" => {
            if !cfg.inode_ratio_explicit {
                cfg.inode_ratio = 1048576;
            }
        }
        "largefile4" => {
            if !cfg.inode_ratio_explicit {
                cfg.inode_ratio = 4194304;
            }
        }
        "hurd" => {
            if !cfg.block_size_explicit {
                cfg.block_size = 4096;
            }
            if !cfg.inode_size_explicit {
                cfg.inode_size = 128;
            }
            cfg.creator_os = EXT2_OS_HURD;
        }
        _ => {}
    }
}

/// Applies comma-separated `-O` feature tokens (supporting `^feature` or `-feature` to clear, `+feature` to set).
pub fn apply_features(cfg: &mut ExtMkfsConfig, list: &str) -> Result<(), ExtMkfsError> {
    for raw_tok in list.split(',').map(str::trim).filter(|s| !s.is_empty()) {
        if raw_tok == "none" {
            cfg.feature_compat = 0;
            cfg.feature_incompat = 0;
            cfg.feature_ro_compat = 0;
            continue;
        }
        let (enable, name) = if let Some(rest) = raw_tok.strip_prefix('^') {
            (false, rest)
        } else if let Some(rest) = raw_tok.strip_prefix('-') {
            (false, rest)
        } else if let Some(rest) = raw_tok.strip_prefix('+') {
            (true, rest)
        } else {
            (true, raw_tok)
        };

        let set_bit = |target: &mut u32, mask: u32| {
            if enable {
                *target |= mask;
            } else {
                *target &= !mask;
            }
        };

        match name {
            "dir_prealloc" => set_bit(&mut cfg.feature_compat, EXT2_FEATURE_COMPAT_DIR_PREALLOC),
            "imagic_inodes" => set_bit(&mut cfg.feature_compat, EXT2_FEATURE_COMPAT_IMAGIC_INODES),
            "has_journal" => set_bit(&mut cfg.feature_compat, EXT3_FEATURE_COMPAT_HAS_JOURNAL),
            "ext_attr" | "xattr" => set_bit(&mut cfg.feature_compat, EXT2_FEATURE_COMPAT_EXT_ATTR),
            "resize_inode" => set_bit(&mut cfg.feature_compat, EXT2_FEATURE_COMPAT_RESIZE_INODE),
            "dir_index" => set_bit(&mut cfg.feature_compat, EXT2_FEATURE_COMPAT_DIR_INDEX),
            "sparse_super2" => set_bit(&mut cfg.feature_compat, EXT4_FEATURE_COMPAT_SPARSE_SUPER2),
            "fast_commit" => set_bit(&mut cfg.feature_compat, EXT4_FEATURE_COMPAT_FAST_COMMIT),
            "stable_inodes" => set_bit(&mut cfg.feature_compat, EXT4_FEATURE_COMPAT_STABLE_INODES),
            "orphan_file" => set_bit(&mut cfg.feature_compat, EXT4_FEATURE_COMPAT_ORPHAN_FILE),

            "compression" => set_bit(&mut cfg.feature_incompat, EXT2_FEATURE_INCOMPAT_COMPRESSION),
            "filetype" => set_bit(&mut cfg.feature_incompat, EXT2_FEATURE_INCOMPAT_FILETYPE),
            "journal_dev" => set_bit(&mut cfg.feature_incompat, EXT3_FEATURE_INCOMPAT_JOURNAL_DEV),
            "meta_bg" => set_bit(&mut cfg.feature_incompat, EXT2_FEATURE_INCOMPAT_META_BG),
            "extent" | "extents" => {
                set_bit(&mut cfg.feature_incompat, EXT4_FEATURE_INCOMPAT_EXTENTS);
            }
            "64bit" => set_bit(&mut cfg.feature_incompat, EXT4_FEATURE_INCOMPAT_64BIT),
            "mmp" => set_bit(&mut cfg.feature_incompat, EXT4_FEATURE_INCOMPAT_MMP),
            "flex_bg" => set_bit(&mut cfg.feature_incompat, EXT4_FEATURE_INCOMPAT_FLEX_BG),
            "ea_inode" => set_bit(&mut cfg.feature_incompat, EXT4_FEATURE_INCOMPAT_EA_INODE),
            "dirdata" => set_bit(&mut cfg.feature_incompat, EXT4_FEATURE_INCOMPAT_DIRDATA),
            "csum_seed" => set_bit(&mut cfg.feature_incompat, EXT4_FEATURE_INCOMPAT_CSUM_SEED),
            "largedir" => set_bit(&mut cfg.feature_incompat, EXT4_FEATURE_INCOMPAT_LARGEDIR),
            "inline_data" => set_bit(&mut cfg.feature_incompat, EXT4_FEATURE_INCOMPAT_INLINE_DATA),
            "encrypt" => set_bit(&mut cfg.feature_incompat, EXT4_FEATURE_INCOMPAT_ENCRYPT),
            "casefold" => {
                set_bit(&mut cfg.feature_incompat, EXT4_FEATURE_INCOMPAT_CASEFOLD);
                if enable && cfg.encoding == 0 {
                    cfg.encoding = EXT4_ENC_UTF8_12_1;
                }
            }

            "sparse_super" => {
                set_bit(&mut cfg.feature_ro_compat, EXT2_FEATURE_RO_COMPAT_SPARSE_SUPER);
            }
            "large_file" => set_bit(&mut cfg.feature_ro_compat, EXT2_FEATURE_RO_COMPAT_LARGE_FILE),
            "huge_file" => set_bit(&mut cfg.feature_ro_compat, EXT4_FEATURE_RO_COMPAT_HUGE_FILE),
            "uninit_bg" | "gdt_csum" => {
                set_bit(&mut cfg.feature_ro_compat, EXT4_FEATURE_RO_COMPAT_GDT_CSUM);
            }
            "dir_nlink" => set_bit(&mut cfg.feature_ro_compat, EXT4_FEATURE_RO_COMPAT_DIR_NLINK),
            "extra_isize" => {
                set_bit(&mut cfg.feature_ro_compat, EXT4_FEATURE_RO_COMPAT_EXTRA_ISIZE);
            }
            "quota" => set_bit(&mut cfg.feature_ro_compat, EXT4_FEATURE_RO_COMPAT_QUOTA),
            "bigalloc" => set_bit(&mut cfg.feature_ro_compat, EXT4_FEATURE_RO_COMPAT_BIGALLOC),
            "metadata_csum" => {
                set_bit(
                    &mut cfg.feature_ro_compat,
                    EXT4_FEATURE_RO_COMPAT_METADATA_CSUM,
                );
                if enable {
                    cfg.feature_ro_compat &= !EXT4_FEATURE_RO_COMPAT_GDT_CSUM;
                }
            }
            "project" => set_bit(&mut cfg.feature_ro_compat, EXT4_FEATURE_RO_COMPAT_PROJECT),
            "verity" => set_bit(&mut cfg.feature_ro_compat, EXT4_FEATURE_RO_COMPAT_VERITY),
            other => {
                return Err(ExtMkfsError::InvalidArgument(format!(
                    "Invalid filesystem option set: {other}"
                )));
            }
        }
    }
    Ok(())
}

/// Applies comma-separated `-E` extended options.
pub fn apply_extended_options(cfg: &mut ExtMkfsConfig, list: &str) -> Result<(), ExtMkfsError> {
    for tok in list.split(',').map(str::trim).filter(|s| !s.is_empty()) {
        let (key, val) = match tok.split_once('=') {
            Some((k, v)) => (k.trim(), Some(v.trim())),
            None => (tok, None),
        };
        match key {
            "android_sparse" => {
                cfg.android_sparse = true;
            }
            "lazy_itable_init" => {
                cfg.lazy_itable_init = val.map(|v| v != "0").unwrap_or(true);
            }
            "lazy_journal_init" => {
                cfg.lazy_journal_init = val.map(|v| v != "0").unwrap_or(true);
            }
            "root_owner" => {
                if let Some(v) = val {
                    let mut parts = v.split(':');
                    let uid: u32 = parts
                        .next()
                        .unwrap_or("0")
                        .parse()
                        .map_err(|_| ExtMkfsError::InvalidArgument("invalid root_owner uid".into()))?;
                    let gid: u32 = parts
                        .next()
                        .unwrap_or("0")
                        .parse()
                        .map_err(|_| ExtMkfsError::InvalidArgument("invalid root_owner gid".into()))?;
                    cfg.root_uid = uid;
                    cfg.root_gid = gid;
                }
            }
            "hash_seed" => {
                if let Some(v) = val {
                    cfg.hash_seed = parse_uuid(v)?;
                }
            }
            "encoding" => {
                if let Some(v) = val {
                    let mut parts = v.split(':');
                    let enc_name = parts.next().unwrap_or("");
                    match enc_name {
                        "utf8" | "utf8-12.1" => {
                            cfg.encoding = EXT4_ENC_UTF8_12_1;
                            cfg.feature_incompat |= EXT4_FEATURE_INCOMPAT_CASEFOLD;
                        }
                        other => {
                            return Err(ExtMkfsError::InvalidArgument(format!(
                                "invalid encoding '{other}'"
                            )));
                        }
                    }
                    for fl in parts {
                        if fl == "strict" {
                            cfg.encoding_flags |= EXT4_ENC_STRICT_MODE_FL;
                        }
                    }
                }
            }
            "encoding_flags" => {
                if let Some(v) = val {
                    for f in v.split(':') {
                        match f {
                            "strict" => cfg.encoding_flags |= EXT4_ENC_STRICT_MODE_FL,
                            "nostrict" => cfg.encoding_flags &= !EXT4_ENC_STRICT_MODE_FL,
                            _ => {}
                        }
                    }
                }
            }
            "stride" => {
                if let Some(v) = val {
                    cfg.stride = v
                        .parse()
                        .map_err(|_| ExtMkfsError::InvalidArgument("invalid stride".into()))?;
                }
            }
            "stripe_width" | "stripe-width" => {
                if let Some(v) = val {
                    cfg.stripe_width = v
                        .parse()
                        .map_err(|_| ExtMkfsError::InvalidArgument("invalid stripe_width".into()))?;
                }
            }
            "offset" => {
                if let Some(v) = val {
                    cfg.offset_bytes = v
                        .parse()
                        .map_err(|_| ExtMkfsError::InvalidArgument("invalid offset".into()))?;
                }
            }
            "test_fs" => {
                cfg.super_flags |= EXT2_FLAGS_TEST_FILESYS;
            }
            "discard" | "nodiscard" | "resize" | "packed_meta_blocks"
            | "assume_storage_prezeroed" | "quotatype" | "num_backup_sb" | "orphan_file_size" => {}
            other => {
                return Err(ExtMkfsError::InvalidArgument(format!(
                    "unknown extended option '{other}'"
                )));
            }
        }
    }
    Ok(())
}

/// Parses command-line arguments (`args[1..]`) for `mke2fs`.
pub fn parse_cli_args(args: &[String]) -> Result<CliAction, ExtMkfsError> {
    if args.is_empty() {
        return Ok(CliAction::ShowUsage { exit_code: 1 });
    }

    let mut cfg = ExtMkfsConfig::default();
    let mut deferred_features: Vec<String> = Vec::new();
    let mut deferred_extended: Vec<String> = Vec::new();
    let mut explicit_journal = false;
    let mut positional: Vec<String> = Vec::new();

    let mut i = 0usize;
    while i < args.len() {
        let arg = &args[i];
        if arg == "--" {
            positional.extend_from_slice(&args[i + 1..]);
            break;
        }
        if arg == "--help" || arg == "-h" {
            return Ok(CliAction::ShowUsage { exit_code: 0 });
        }
        if !arg.starts_with('-') || arg == "-" {
            positional.push(arg.clone());
            i += 1;
            continue;
        }

        let chars: Vec<char> = arg[1..].chars().collect();
        let mut ci = 0usize;
        while ci < chars.len() {
            let opt = chars[ci];
            let take_val = |ci: &mut usize, i: &mut usize| -> Result<String, ExtMkfsError> {
                if *ci + 1 < chars.len() {
                    let s: String = chars[*ci + 1..].iter().collect();
                    *ci = chars.len();
                    Ok(s)
                } else if *i + 1 < args.len() {
                    *i += 1;
                    *ci = chars.len();
                    Ok(args[*i].clone())
                } else {
                    Err(ExtMkfsError::InvalidArgument(format!(
                        "option -{opt} requires an argument"
                    )))
                }
            };

            match opt {
                'V' => return Ok(CliAction::ShowVersion),
                'F' => cfg.force = true,
                'q' => cfg.quiet = true,
                'v' => cfg.verbose = true,
                'n' => cfg.dry_run = true,
                'S' => cfg.super_only = true,
                'c' | 'D' => {}
                'j' => {
                    explicit_journal = true;
                }
                'b' => {
                    let v: i64 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| ExtMkfsError::InvalidArgument("invalid block size".into()))?;
                    let bsz = v.unsigned_abs() as u32;
                    if !matches!(bsz, 1024 | 2048 | 4096) {
                        return Err(ExtMkfsError::InvalidArgument(format!(
                            "invalid block size: {v}"
                        )));
                    }
                    cfg.block_size = bsz;
                    cfg.block_size_explicit = true;
                }
                'C' => {
                    let v: u32 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| ExtMkfsError::InvalidArgument("invalid cluster size".into()))?;
                    cfg.cluster_size = Some(v);
                }
                'd' => {
                    let v = take_val(&mut ci, &mut i)?;
                    cfg.root_dir = Some(PathBuf::from(v));
                }
                'e' => {
                    let v = take_val(&mut ci, &mut i)?;
                    cfg.errors_behavior = match v.as_str() {
                        "continue" => EXT2_ERRORS_CONTINUE,
                        "remount-ro" => EXT2_ERRORS_RO,
                        "panic" => EXT2_ERRORS_PANIC,
                        _ => {
                            return Err(ExtMkfsError::InvalidArgument(format!(
                                "bad error behavior: {v}"
                            )));
                        }
                    };
                }
                'E' => {
                    deferred_extended.push(take_val(&mut ci, &mut i)?);
                }
                'g' => {
                    let v: u32 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| ExtMkfsError::InvalidArgument("invalid -g value".into()))?;
                    cfg.blocks_per_group = Some(v);
                }
                'G' => {
                    let v: u32 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| ExtMkfsError::InvalidArgument("invalid -G value".into()))?;
                    if !v.is_power_of_two() {
                        return Err(ExtMkfsError::InvalidArgument(
                            "flex_bg size must be a power of 2".into(),
                        ));
                    }
                    cfg.flex_bg_size = Some(v);
                }
                'i' => {
                    let v: u32 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| ExtMkfsError::InvalidArgument("invalid -i ratio".into()))?;
                    cfg.inode_ratio = v;
                    cfg.inode_ratio_explicit = true;
                }
                'I' => {
                    let v: u16 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| ExtMkfsError::InvalidArgument("invalid inode size".into()))?;
                    if !v.is_power_of_two() || !(128..=1024).contains(&v) {
                        return Err(ExtMkfsError::InvalidArgument(format!(
                            "invalid inode size: {v}"
                        )));
                    }
                    cfg.inode_size = v;
                    cfg.inode_size_explicit = true;
                }
                'J' => {
                    let v = take_val(&mut ci, &mut i)?;
                    explicit_journal = true;
                    for jopt in v.split(',') {
                        if let Some(sz_str) = jopt.strip_prefix("size=") {
                            if let Ok(mb) = sz_str.parse::<u32>() {
                                cfg.journal_blocks = Some(mb); // resolved after block_size is final
                            }
                        }
                    }
                }
                'l' | 'z' => {
                    let _ = take_val(&mut ci, &mut i)?;
                }
                'L' => {
                    cfg.label = take_val(&mut ci, &mut i)?;
                }
                'm' => {
                    let v: f64 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| ExtMkfsError::InvalidArgument("invalid -m ratio".into()))?;
                    if !(0.0..=50.0).contains(&v) {
                        return Err(ExtMkfsError::InvalidArgument(format!(
                            "invalid reserved blocks percent: {v}"
                        )));
                    }
                    cfg.reserved_ratio = v;
                }
                'M' => {
                    cfg.last_mounted = take_val(&mut ci, &mut i)?;
                }
                'N' => {
                    let v: u32 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| ExtMkfsError::InvalidArgument("invalid -N count".into()))?;
                    cfg.num_inodes = Some(v);
                }
                'o' => {
                    let v = take_val(&mut ci, &mut i)?;
                    cfg.creator_os = match v.to_ascii_lowercase().as_str() {
                        "linux" => EXT2_OS_LINUX,
                        "hurd" => EXT2_OS_HURD,
                        "masix" => EXT2_OS_MASIX,
                        "freebsd" => EXT2_OS_FREEBSD,
                        "lites" => EXT2_OS_LITES,
                        _ => {
                            return Err(ExtMkfsError::InvalidArgument(format!(
                                "unknown os: {v}"
                            )));
                        }
                    };
                }
                'O' => {
                    deferred_features.push(take_val(&mut ci, &mut i)?);
                }
                'r' => {
                    let v: u32 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| ExtMkfsError::InvalidArgument("invalid -r rev".into()))?;
                    cfg.rev_level = v;
                }
                't' => {
                    cfg.fs_type = take_val(&mut ci, &mut i)?;
                }
                'T' => {
                    cfg.usage_type = Some(take_val(&mut ci, &mut i)?);
                }
                'U' => {
                    let v = take_val(&mut ci, &mut i)?;
                    cfg.uuid = parse_uuid(&v)?;
                }
                other => {
                    return Err(ExtMkfsError::InvalidArgument(format!(
                        "invalid option -- '{other}'"
                    )));
                }
            }
            ci += 1;
        }
        i += 1;
    }

    if positional.is_empty() {
        return Ok(CliAction::ShowUsage { exit_code: 1 });
    }
    if positional.len() > 2 {
        return Err(ExtMkfsError::InvalidArgument(
            "too many positional arguments".into(),
        ));
    }

    cfg.device_path = PathBuf::from(&positional[0]);
    if positional.len() == 2 {
        cfg.blocks_count_arg = Some(parse_blocks_count(&positional[1], cfg.block_size)?);
    }

    // Apply -t fs_type profile from mke2fs.conf
    let fs_type = cfg.fs_type.clone();
    for part in fs_type.split(',') {
        apply_fs_type_profile(&mut cfg, part.trim());
    }
    if let Some(ut) = cfg.usage_type.clone() {
        for part in ut.split(',') {
            apply_fs_type_profile(&mut cfg, part.trim());
        }
    }
    if explicit_journal {
        cfg.feature_compat |= EXT3_FEATURE_COMPAT_HAS_JOURNAL;
    }
    for feat_list in &deferred_features {
        apply_features(&mut cfg, feat_list)?;
    }
    for ext_list in &deferred_extended {
        apply_extended_options(&mut cfg, ext_list)?;
    }

    Ok(CliAction::Format(cfg))
}

fn parse_blocks_count(s: &str, block_size: u32) -> Result<u64, ExtMkfsError> {
    let trimmed = s.trim();
    if trimmed.is_empty() {
        return Err(ExtMkfsError::InvalidArgument("empty blocks count".into()));
    }
    let last = trimmed.chars().last().unwrap();
    if last.is_ascii_alphabetic() {
        let num: u64 = trimmed[..trimmed.len() - 1]
            .parse()
            .map_err(|_| ExtMkfsError::InvalidArgument(format!("invalid blocks count '{s}'")))?;
        let mult_bytes = match last {
            's' => 512u64,
            'k' | 'K' => 1024u64,
            'm' | 'M' => 1024u64 * 1024,
            'g' | 'G' => 1024u64 * 1024 * 1024,
            't' | 'T' => 1024u64 * 1024 * 1024 * 1024,
            _ => {
                return Err(ExtMkfsError::InvalidArgument(format!(
                    "invalid blocks count suffix '{s}'"
                )));
            }
        };
        Ok((num * mult_bytes) / u64::from(block_size))
    } else {
        trimmed
            .parse()
            .map_err(|_| ExtMkfsError::InvalidArgument(format!("invalid blocks count '{s}'")))
    }
}
