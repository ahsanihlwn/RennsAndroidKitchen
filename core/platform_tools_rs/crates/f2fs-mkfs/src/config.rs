use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};

use thiserror::Error;

use crate::spec::{
    DEFAULT_COLD_EXTENSIONS, DEFAULT_HOT_EXTENSIONS, F2FS_BLKSIZE, F2FS_ENC_NO_COMPAT_FALLBACK_FL,
    F2FS_ENC_STRICT_MODE_FL, F2FS_ENC_UTF8_12_1, F2FS_FEATURE_BLKZONED, F2FS_FEATURE_CASEFOLD,
    F2FS_FEATURE_COMPRESSION, F2FS_FEATURE_ENCRYPT, F2FS_FEATURE_EXTRA_ATTR,
    F2FS_FEATURE_FLEXIBLE_INLINE_XATTR, F2FS_FEATURE_INODE_CHKSUM, F2FS_FEATURE_INODE_CRTIME,
    F2FS_FEATURE_LOST_FOUND, F2FS_FEATURE_PRJQUOTA, F2FS_FEATURE_QUOTA_INO, F2FS_FEATURE_RO,
    F2FS_FEATURE_SB_CHKSUM, F2FS_FEATURE_VERITY,
};

#[derive(Debug, Error)]
pub enum MkfsError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("Sparse image error: {0}")]
    Sparse(#[from] android_sparse::SparseError),
    #[error("Error: Device not specified")]
    DeviceNotSpecified,
    #[error("Error: Unknown option {0}")]
    UnknownOption(String),
    #[error("Error: Invalid argument: {0}")]
    InvalidArgument(String),
    #[error("Error: Device size is not sufficient for F2FS volume")]
    VolumeTooSmall,
    #[error("Error: Integrity verification failed: {0}")]
    VerificationFailed(String),
}

/// Action returned after parsing CLI arguments.
#[derive(Debug, Clone)]
#[allow(clippy::large_enum_variant)]
pub enum CliAction {
    ShowVersion,
    ShowHelp,
    Format(MkfsConfig),
}

/// Complete configuration for formatting an F2FS volume.
#[derive(Debug, Clone)]
pub struct MkfsConfig {
    pub device_path: PathBuf,
    pub total_sectors: Option<u64>,
    pub block_size: u32,
    pub sector_size: u32,
    pub sparse_size: Option<u64>,
    pub segs_per_sec: u32,
    pub secs_per_zone: u32,
    pub overprovision_pct: Option<f64>,
    pub reserved_sections: Option<u32>,
    pub features: u32,
    pub usr_grp_quota: bool,
    pub prj_quota: bool,
    pub encoding: u16,
    pub encoding_flags: u16,
    pub label: String,
    pub uuid: [u8; 16],
    pub root_uid: u32,
    pub root_gid: u32,
    pub timestamp: u64,
    pub fixed_seed: bool,
    pub trim: bool,
    pub force: bool,
    pub quiet: bool,
    pub debug_level: u32,
    pub extended_node_bitmap: bool,
    pub zoned: bool,
    pub write_hint: bool,
    pub nat_bits: bool,
    pub cold_extensions: Vec<String>,
    pub hot_extensions: Vec<String>,
    pub version_string: String,
}

impl Default for MkfsConfig {
    fn default() -> Self {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0);
        let mut uuid = [0u8; 16];
        let _ = getrandom::fill(&mut uuid);
        // Set RFC 4122 v4 variant bits for random default UUID.
        uuid[6] = (uuid[6] & 0x0f) | 0x40;
        uuid[8] = (uuid[8] & 0x3f) | 0x80;

        Self {
            device_path: PathBuf::new(),
            total_sectors: None,
            block_size: F2FS_BLKSIZE,
            sector_size: 512,
            sparse_size: None,
            segs_per_sec: 1,
            secs_per_zone: 1,
            overprovision_pct: None,
            reserved_sections: None,
            features: 0,
            usr_grp_quota: false,
            prj_quota: false,
            encoding: 0,
            encoding_flags: 0,
            label: String::new(),
            uuid,
            root_uid: default_owner_id(),
            root_gid: default_group_id(),
            timestamp: now,
            fixed_seed: false,
            trim: true,
            force: false,
            quiet: false,
            debug_level: 0,
            extended_node_bitmap: false,
            zoned: false,
            write_hint: false,
            nat_bits: true,
            cold_extensions: DEFAULT_COLD_EXTENSIONS
                .iter()
                .map(|s| (*s).to_string())
                .collect(),
            hot_extensions: DEFAULT_HOT_EXTENSIONS
                .iter()
                .map(|s| (*s).to_string())
                .collect(),
            version_string: detect_kernel_version(),
        }
    }
}

fn default_owner_id() -> u32 {
    0
}

fn default_group_id() -> u32 {
    0
}

fn detect_kernel_version() -> String {
    if let Ok(v) = std::env::var("F2FS_KERNEL_VERSION") {
        return v;
    }
    if let Ok(s) = std::fs::read_to_string("/proc/version") {
        if let Some(line) = s.lines().next() {
            let trimmed = line.trim();
            let stripped = trimmed.strip_prefix("Linux version ").unwrap_or(trimmed);
            if let Some(tok) = stripped.split_whitespace().next() {
                return tok.to_string();
            }
        }
    }
    "6.18.0".to_string()
}

/// Parses a 36-character UUID string (`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` or 32 hex chars).
pub fn parse_uuid(s: &str) -> Result<[u8; 16], MkfsError> {
    let hex: String = s.chars().filter(|&c| c != '-').collect();
    if hex.len() != 32 {
        return Err(MkfsError::InvalidArgument(format!("invalid UUID '{s}'")));
    }
    let mut out = [0u8; 16];
    for i in 0..16 {
        out[i] = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16)
            .map_err(|_| MkfsError::InvalidArgument(format!("invalid UUID '{s}'")))?;
    }
    Ok(out)
}

fn apply_feature_list(cfg: &mut MkfsConfig, list: &str) -> Result<(), MkfsError> {
    for item in list.split(',').map(str::trim).filter(|s| !s.is_empty()) {
        match item {
            "encrypt" => cfg.features |= F2FS_FEATURE_ENCRYPT,
            "blkzoned" => cfg.features |= F2FS_FEATURE_BLKZONED,
            "extra_attr" => cfg.features |= F2FS_FEATURE_EXTRA_ATTR,
            "project_quota" => {
                cfg.features |= F2FS_FEATURE_PRJQUOTA | F2FS_FEATURE_QUOTA_INO;
                cfg.prj_quota = true;
            }
            "inode_checksum" => cfg.features |= F2FS_FEATURE_INODE_CHKSUM,
            "flexible_inline_xattr" => cfg.features |= F2FS_FEATURE_FLEXIBLE_INLINE_XATTR,
            "quota" => {
                cfg.features |= F2FS_FEATURE_QUOTA_INO;
                cfg.usr_grp_quota = true;
            }
            "inode_crtime" => cfg.features |= F2FS_FEATURE_INODE_CRTIME,
            "lost_found" => cfg.features |= F2FS_FEATURE_LOST_FOUND,
            "verity" => cfg.features |= F2FS_FEATURE_VERITY,
            "sb_checksum" => cfg.features |= F2FS_FEATURE_SB_CHKSUM,
            "casefold" => cfg.features |= F2FS_FEATURE_CASEFOLD,
            "compression" => cfg.features |= F2FS_FEATURE_COMPRESSION,
            "ro" => cfg.features |= F2FS_FEATURE_RO,
            other => {
                return Err(MkfsError::InvalidArgument(format!(
                    "unknown feature '{other}'"
                )));
            }
        }
    }
    Ok(())
}

fn parse_encoding(spec: &str) -> Result<(u16, u16), MkfsError> {
    let mut parts = spec.split(':');
    let enc_name = parts.next().unwrap_or("");
    let encoding = match enc_name {
        "utf8" | "utf8-12.1" | "utf8:12.1.0" => F2FS_ENC_UTF8_12_1,
        other => {
            return Err(MkfsError::InvalidArgument(format!(
                "unsupported encoding '{other}'"
            )));
        }
    };
    let mut flags = 0u16;
    for flag_list in parts {
        for flag in flag_list.split(',').map(str::trim).filter(|s| !s.is_empty()) {
            match flag {
                "strict" => flags |= F2FS_ENC_STRICT_MODE_FL,
                "nocompat" => flags |= F2FS_ENC_NO_COMPAT_FALLBACK_FL,
                "12.1.0" => {}
                other => {
                    return Err(MkfsError::InvalidArgument(format!(
                        "unknown encoding flag '{other}'"
                    )));
                }
            }
        }
    }
    Ok((encoding, flags))
}

/// Prints the standard `mkfs.f2fs` usage message to stderr.
pub fn usage_string() -> &'static str {
    "\
Usage: mkfs.f2fs [options] device [sectors]
[options]:
  -b filesystem block size [default:4096]
  -c [device_name[@alias_filename]] up to 7 additional devices, except meta device
  -d debug level [default:0]
  -e [cold file ext list] e.g. \"mp3,gif,mov\"
  -E [hot file ext list] e.g. \"db\"
  -f force overwrite of the existing filesystem
  -g add default options
  -H support write hint
  -i extended node bitmap, node ratio is 20% by default
  -l label
  -U uuid
  -m support zoned block device [default:0]
  -o overprovision percentage [default:auto]
  -O feature1[,feature2,...] e.g. \"encrypt\"
  -C [encoding[:flag1,...]] Support casefolding with optional flags
  -q quiet mode
  -r set checkpointing seed (srand()) to 0
  -R root_owner [default: 0:0]
  -s # of segments per section [default:1]
  -S sparse mode
  -t 0: nodiscard, 1: discard [default:1]
  -T timestamps
  -w wanted sector size
  -z # of sections per zone [default:1]
  -V print the version number and exit
  -Z # of reserved sections [default:auto]
sectors: number of sectors [default: determined by device size]"
}

/// Parses command-line arguments (`args[1..]`) for `make_f2fs` (`casefold_default = false`)
/// or `make_f2fs_casefold` (`casefold_default = true`).
pub fn parse_cli_args(
    args: &[String],
    casefold_default: bool,
) -> Result<CliAction, MkfsError> {
    let mut cfg = MkfsConfig::default();
    if casefold_default {
        cfg.features |= F2FS_FEATURE_EXTRA_ATTR
            | F2FS_FEATURE_PRJQUOTA
            | F2FS_FEATURE_QUOTA_INO
            | F2FS_FEATURE_CASEFOLD;
        cfg.usr_grp_quota = true;
        cfg.prj_quota = true;
        cfg.encoding = F2FS_ENC_UTF8_12_1;
    }

    let mut android_profile = false;
    let mut explicit_trim: Option<bool> = None;
    let mut positional: Vec<String> = Vec::new();

    let mut i = 0usize;
    while i < args.len() {
        let arg = &args[i];
        if arg == "--" {
            positional.extend_from_slice(&args[i + 1..]);
            break;
        }
        if arg == "--help" || arg == "-h" {
            return Ok(CliAction::ShowHelp);
        }
        if !arg.starts_with('-') || arg == "-" {
            positional.push(arg.clone());
            i += 1;
            continue;
        }

        // Support bundled short options or `-X val` / `-Xval`.
        let chars: Vec<char> = arg[1..].chars().collect();
        let mut ci = 0usize;
        while ci < chars.len() {
            let opt = chars[ci];
            let take_val = |ci: &mut usize, i: &mut usize| -> Result<String, MkfsError> {
                if *ci + 1 < chars.len() {
                    let s: String = chars[*ci + 1..].iter().collect();
                    *ci = chars.len();
                    Ok(s)
                } else if *i + 1 < args.len() {
                    *i += 1;
                    *ci = chars.len();
                    Ok(args[*i].clone())
                } else {
                    Err(MkfsError::InvalidArgument(format!(
                        "option -{opt} requires an argument"
                    )))
                }
            };

            match opt {
                'V' => return Ok(CliAction::ShowVersion),
                'f' => cfg.force = true,
                'q' => cfg.quiet = true,
                'r' => cfg.fixed_seed = true,
                'i' => cfg.extended_node_bitmap = true,
                'm' => {
                    cfg.zoned = true;
                    cfg.features |= F2FS_FEATURE_BLKZONED;
                }
                'H' => cfg.write_hint = true,
                'b' => {
                    let v: u32 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| MkfsError::InvalidArgument("invalid -b block size".into()))?;
                    if v != F2FS_BLKSIZE {
                        return Err(MkfsError::InvalidArgument(format!(
                            "unsupported block size {v} (must be 4096)"
                        )));
                    }
                    cfg.block_size = v;
                }
                'c' => {
                    let _ = take_val(&mut ci, &mut i)?;
                }
                'd' => {
                    cfg.debug_level = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| MkfsError::InvalidArgument("invalid -d debug level".into()))?;
                }
                'e' => {
                    let v = take_val(&mut ci, &mut i)?;
                    for ext in v.split(',').map(str::trim).filter(|s| !s.is_empty()) {
                        if !cfg.cold_extensions.iter().any(|e| e == ext) {
                            cfg.cold_extensions.push(ext.to_string());
                        }
                    }
                }
                'E' => {
                    let v = take_val(&mut ci, &mut i)?;
                    for ext in v.split(',').map(str::trim).filter(|s| !s.is_empty()) {
                        if !cfg.hot_extensions.iter().any(|e| e == ext) {
                            cfg.hot_extensions.push(ext.to_string());
                        }
                    }
                }
                'g' => {
                    let v = take_val(&mut ci, &mut i)?;
                    match v.as_str() {
                        "android" => {
                            android_profile = true;
                            cfg.nat_bits = false;
                            cfg.features |= F2FS_FEATURE_ENCRYPT
                                | F2FS_FEATURE_EXTRA_ATTR
                                | F2FS_FEATURE_PRJQUOTA
                                | F2FS_FEATURE_QUOTA_INO
                                | F2FS_FEATURE_VERITY;
                            cfg.usr_grp_quota = true;
                            cfg.prj_quota = true;
                        }
                        "default" => {}
                        other => {
                            return Err(MkfsError::InvalidArgument(format!(
                                "unknown default option group '{other}'"
                            )));
                        }
                    }
                }
                'l' => {
                    cfg.label = take_val(&mut ci, &mut i)?;
                }
                'U' => {
                    let v = take_val(&mut ci, &mut i)?;
                    cfg.uuid = parse_uuid(&v)?;
                }
                'o' => {
                    let v: f64 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| MkfsError::InvalidArgument("invalid -o percentage".into()))?;
                    if v <= 0.0 || v >= 100.0 {
                        return Err(MkfsError::InvalidArgument(
                            "overprovision percentage must be in (0, 100)".into(),
                        ));
                    }
                    cfg.overprovision_pct = Some(v);
                }
                'O' => {
                    let v = take_val(&mut ci, &mut i)?;
                    apply_feature_list(&mut cfg, &v)?;
                }
                'C' => {
                    let v = take_val(&mut ci, &mut i)?;
                    let (enc, eflags) = parse_encoding(&v)?;
                    cfg.encoding = enc;
                    cfg.encoding_flags |= eflags;
                }
                'R' => {
                    let v = take_val(&mut ci, &mut i)?;
                    let mut parts = v.split(':');
                    let uid: u32 = parts
                        .next()
                        .ok_or_else(|| MkfsError::InvalidArgument("invalid -R owner".into()))?
                        .parse()
                        .map_err(|_| MkfsError::InvalidArgument("invalid -R uid".into()))?;
                    let gid: u32 = parts
                        .next()
                        .unwrap_or("0")
                        .parse()
                        .map_err(|_| MkfsError::InvalidArgument("invalid -R gid".into()))?;
                    cfg.root_uid = uid;
                    cfg.root_gid = gid;
                }
                's' => {
                    let v: u32 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| MkfsError::InvalidArgument("invalid -s value".into()))?;
                    if v == 0 {
                        return Err(MkfsError::InvalidArgument("-s must be >= 1".into()));
                    }
                    cfg.segs_per_sec = v;
                }
                'S' => {
                    let v: u64 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| MkfsError::InvalidArgument("invalid -S size".into()))?;
                    cfg.sparse_size = Some(v);
                }
                't' => {
                    let v: u32 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| MkfsError::InvalidArgument("invalid -t value".into()))?;
                    explicit_trim = Some(v != 0);
                }
                'T' => {
                    let v: i64 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| MkfsError::InvalidArgument("invalid -T timestamp".into()))?;
                    if v >= 0 {
                        cfg.timestamp = v as u64;
                    }
                }
                'w' => {
                    let v: u32 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| MkfsError::InvalidArgument("invalid -w sector size".into()))?;
                    if !v.is_power_of_two() || !(512..=4096).contains(&v) {
                        return Err(MkfsError::InvalidArgument(format!(
                            "invalid wanted sector size {v}"
                        )));
                    }
                    cfg.sector_size = v;
                }
                'z' => {
                    let v: u32 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| MkfsError::InvalidArgument("invalid -z value".into()))?;
                    if v == 0 {
                        return Err(MkfsError::InvalidArgument("-z must be >= 1".into()));
                    }
                    cfg.secs_per_zone = v;
                }
                'Z' => {
                    let v: u32 = take_val(&mut ci, &mut i)?
                        .parse()
                        .map_err(|_| MkfsError::InvalidArgument("invalid -Z value".into()))?;
                    cfg.reserved_sections = Some(v);
                }
                other => {
                    return Err(MkfsError::UnknownOption(format!("-{other}")));
                }
            }
            ci += 1;
        }
        i += 1;
    }

    if cfg.sparse_size.is_some() {
        cfg.trim = false;
    } else if let Some(t) = explicit_trim {
        cfg.trim = t;
    }

    if casefold_default && android_profile {
        cfg.encoding_flags |= F2FS_ENC_NO_COMPAT_FALLBACK_FL;
    }

    // Validate feature dependencies.
    let needs_extra = F2FS_FEATURE_PRJQUOTA
        | F2FS_FEATURE_INODE_CHKSUM
        | F2FS_FEATURE_FLEXIBLE_INLINE_XATTR
        | F2FS_FEATURE_INODE_CRTIME
        | F2FS_FEATURE_COMPRESSION;
    if (cfg.features & needs_extra) != 0 && (cfg.features & F2FS_FEATURE_EXTRA_ATTR) == 0 {
        return Err(MkfsError::InvalidArgument(
            "extended features require '-O extra_attr'".into(),
        ));
    }
    if (cfg.features & F2FS_FEATURE_CASEFOLD) != 0 && cfg.encoding == 0 {
        return Err(MkfsError::InvalidArgument(
            "'-O casefold' requires '-C utf8'".into(),
        ));
    }
    if (cfg.features & F2FS_FEATURE_BLKZONED) != 0 && !cfg.zoned {
        return Err(MkfsError::InvalidArgument(
            "'-O blkzoned' requires '-m'".into(),
        ));
    }

    if positional.is_empty() {
        return Err(MkfsError::DeviceNotSpecified);
    }
    if positional.len() > 2 {
        return Err(MkfsError::InvalidArgument(
            "too many positional arguments".into(),
        ));
    }
    cfg.device_path = PathBuf::from(&positional[0]);
    if positional.len() == 2 {
        let secs: u64 = positional[1]
            .parse()
            .map_err(|_| MkfsError::InvalidArgument("invalid sector count".into()))?;
        cfg.total_sectors = Some(secs);
    }

    Ok(CliAction::Format(cfg))
}
