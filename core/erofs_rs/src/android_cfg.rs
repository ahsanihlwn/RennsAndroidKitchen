// SPDX-License-Identifier: MIT

//! Android `fs_config`, `file_contexts`, and `fs_options` generator and parser.
//! Emits standard Android `fs_config`, SELinux `file_contexts`, and ROM kitchen `fs_options`.

use crate::error::{ErofsError, Result};
use crate::reader::{ErofsImage, FileNode};
use regex::Regex;
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::Write;
use std::path::{Path, PathBuf};

/// Escape regex metacharacters for Android `file_contexts` entries.
pub fn escape_file_context_path(path: &str) -> String {
    let mut out = String::with_capacity(path.len() + 8);
    for ch in path.chars() {
        if matches!(
            ch,
            '.' | '+' | '[' | ']' | '*' | '(' | ')' | '?' | '^' | '$' | '{' | '}' | '|'
        ) {
            out.push('\\');
        }
        out.push(ch);
    }
    out
}

/// Format one `fs_config` line with partition-prefixed path.
///
/// `node.path` is the EROFS-internal absolute path (e.g. `/apex/foo`).
/// `partition_name` is the mount-point basename (e.g. `system_ext`).
/// Output: `system_ext/apex/foo <uid> <gid> <mode>[ capabilities=0xHEX]`
/// For the root inode (`node.path == "/"`), output is `/ <uid> <gid> <mode>`.
pub fn format_fs_config_line(node: &FileNode, partition_name: &str) -> String {
    let path = make_fs_config_path(&node.path, partition_name);
    let mut line = format!("{} {} {} {:04o}", path, node.uid, node.gid, node.mode & 0o7777);
    if let Some(cap) = node.capabilities {
        if cap != 0 {
            line.push_str(&format!(" capabilities=0x{cap:X}"));
        }
    }
    line
}

/// Format one `file_contexts` line.
///
/// `node_path` is the EROFS-internal absolute path (e.g. `/apex/foo`).
/// `partition_name` is the mount-point basename (e.g. `system_ext`).
/// Output: `/system_ext/apex/foo <selinux_context>`
/// For root (`node_path == "/"`), output is `/ <ctx>`.
pub fn format_file_context_line(node_path: &str, partition_name: &str, selinux: &str) -> String {
    let path = make_file_contexts_path(node_path, partition_name);
    format!("{} {}", escape_file_context_path(&path), selinux)
}

/// Build the fs_config path string for a given EROFS-internal path.
/// Root `/` → `"/"`, others → `"<partition>/<rest>"` (no leading slash).
fn make_fs_config_path(internal: &str, partition_name: &str) -> String {
    if internal == "/" {
        "/".to_string()
    } else {
        format!("{}/{}", partition_name, internal.trim_start_matches('/'))
    }
}

/// Build the file_contexts path string for a given EROFS-internal path.
/// Root `/` → `"/"`, others → `"/<partition>/<rest>"`.
fn make_file_contexts_path(internal: &str, partition_name: &str) -> String {
    if internal == "/" {
        "/".to_string()
    } else {
        format!("/{}{}", partition_name, internal)
    }
}

#[derive(Debug, Clone)]
pub struct GeneratedConfigs {
    pub fs_config_path: PathBuf,
    pub file_contexts_path: PathBuf,
    pub fs_options_path: Option<PathBuf>,
}

/// Write `<base>_fs_config`, `<base>_file_contexts`, and optionally `<base>_fs_options`.
pub fn write_android_configs(
    img: &ErofsImage,
    nodes: &[FileNode],
    config_dir: &Path,
    extract_dir: &Path,
    image_base_name: &str,
    write_fs_options: bool,
) -> Result<GeneratedConfigs> {
    fs::create_dir_all(config_dir)?;
    let fs_config_path = config_dir.join(format!("{image_base_name}_fs_config"));
    let file_contexts_path = config_dir.join(format!("{image_base_name}_file_contexts"));

    let mut fc_file = File::create(&fs_config_path)?;
    let mut sx_file = File::create(&file_contexts_path)?;

    let has_lost_found = nodes.iter().any(|n| n.path == "/lost+found");
    let mut wrote_root_extras = false;
    for node in nodes {
        // ── fs_config ──────────────────────────────────────────────────────────
        // Root inode → `/ uid gid mode`
        // Others     → `<partition>/path uid gid mode`
        let fc_path = make_fs_config_path(&node.path, image_base_name);
        let mut fc_line = format!("{} {} {} {:04o}", fc_path, node.uid, node.gid, node.mode & 0o7777);
        if let Some(cap) = node.capabilities {
            if cap != 0 {
                fc_line.push_str(&format!(" capabilities=0x{cap:X}"));
            }
        }
        writeln!(fc_file, "{}", fc_line)?;

        // ── file_contexts ───────────────────────────────────────────────────────
        if let Some(raw_ctx) = &node.selinux {
            let ctx = raw_ctx.trim_end_matches('\0');
            if !ctx.is_empty() {
                if node.path == "/" {
                    // Write: `/ ctx`
                    writeln!(sx_file, "/ {}", ctx)?;
                } else {
                    let sx_path = make_file_contexts_path(&node.path, image_base_name);
                    writeln!(sx_file, "{} {}", escape_file_context_path(&sx_path), ctx)?;
                }
            }
        }

        // After root inode: inject partition-root dir in fs_config, and partition rules in file_contexts.
        if !wrote_root_extras && node.path == "/" {
            wrote_root_extras = true;

            // fs_config: `<partition> uid gid mode` (without trailing slash, no synthetic lost+found)
            writeln!(
                fc_file,
                "{} {} {} {:04o}",
                image_base_name, node.uid, node.gid, node.mode & 0o7777
            )?;

            // file_contexts: `/<partition> ctx`, `/<partition>/ ctx`, and optional `/<partition>/lost\+found ctx`
            if let Some(raw_ctx) = &node.selinux {
                let ctx = raw_ctx.trim_end_matches('\0');
                if !ctx.is_empty() {
                    writeln!(sx_file, "/{} {}", image_base_name, ctx)?;
                    writeln!(sx_file, "/{}/ {}", image_base_name, ctx)?;
                    if !has_lost_found {
                        let lost_path = escape_file_context_path(&format!(
                            "/{}/lost+found",
                            image_base_name
                        ));
                        writeln!(sx_file, "{} {}", lost_path, ctx)?;
                    }
                }
            }
        }
    }

    let fs_options_path = if write_fs_options {
        let fo_path = config_dir.join(format!("{image_base_name}_fs_options"));
        let mut fo_file = File::create(&fo_path)?;
        let ts = img.sb.build_timestamp();
        let uuid = img.sb.uuid_string();
        let algos = img.sb.algorithm_names();
        let alg_opt = match algos.first() {
            Some(algo) => format!("-z{algo} "),
            None => String::new(),
        };
        let pcluster_blks = img.compr_cfgs.lz4_max_pclusterblks.unwrap_or(1);
        let pcluster_opt = if pcluster_blks > 1 {
            format!("-C {} ", (pcluster_blks as u64) * (img.sb.block_size as u64))
        } else {
            String::new()
        };
        let digest_opt = if img.ishare_xattr_name().is_some() {
            "--xattr-inode-digest "
        } else {
            ""
        };
        writeln!(fo_file, "Filesystem created:    {}", format_ctime_utc(ts))?;
        writeln!(fo_file, "Filesystem UUID:       {uuid}")?;
        writeln!(
            fo_file,
            "mkfs.erofs options:    {alg_opt}{pcluster_opt}-T {ts} -U {uuid} {digest_opt}--mount-point=/{image_base_name} --fs-config-file={} --file-contexts={} {}_repack.img {}",
            fs_config_path.display(),
            file_contexts_path.display(),
            image_base_name,
            extract_dir.display(),
        )?;
        Some(fo_path)
    } else {
        None
    };

    Ok(GeneratedConfigs {
        fs_config_path,
        file_contexts_path,
        fs_options_path,
    })
}

/// Parsed Android `fs_config` entry.
#[derive(Debug, Clone)]
pub struct FsConfigEntry {
    pub uid: u32,
    pub gid: u32,
    pub mode: u16,
    pub capabilities: u64,
}

/// Lookup table loaded from an Android `fs_config` file.
#[derive(Debug, Default)]
pub struct FsConfigTable {
    entries: HashMap<String, FsConfigEntry>,
}

impl FsConfigTable {
    pub fn load(path: &Path) -> Result<Self> {
        Self::parse_str(&fs::read_to_string(path)?)
    }

    pub fn lookup(&self, path: &str, mount_point: &str) -> Option<&FsConfigEntry> {
        let norm = normalize_cfg_path(path);
        let mp = mount_point.trim_matches('/');
        if !mp.is_empty() {
            let with_mp = if norm == "/" {
                format!("/{mp}")
            } else {
                format!("/{mp}{norm}")
            };
            if let Some(e) = self.entries.get(&with_mp) {
                return Some(e);
            }
            let without_lead = with_mp.trim_start_matches('/');
            if let Some(e) = self.entries.get(without_lead) {
                return Some(e);
            }
        }
        self.entries
            .get(&norm)
            .or_else(|| self.entries.get(norm.trim_start_matches('/')))
    }

    /// Return true when every non-root entry in this table already carries `<part>` / `<part>/...`.
    pub fn has_partition_prefix(&self, part: &str) -> bool {
        let p = part.trim_matches('/');
        if p.is_empty() {
            return false;
        }
        let exact_root = format!("/{p}");
        let sub_prefix = format!("/{p}/");
        let mut saw_non_root = false;
        for k in self.entries.keys() {
            if k == "/" {
                continue;
            }
            saw_non_root = true;
            if k != &exact_root && !k.starts_with(&sub_prefix) {
                return false;
            }
        }
        saw_non_root
    }
}

fn format_ctime_utc(epoch: u64) -> String {
    const WDAYS: [&str; 7] = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const MONTHS: [&str; 12] = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    let days = (epoch / 86400) as i64;
    let rem = (epoch % 86400) as u32;
    let hh = rem / 3600;
    let mm = (rem % 3600) / 60;
    let ss = rem % 60;
    let wday = WDAYS[((days + 4).rem_euclid(7)) as usize];

    let z = days + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u64;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = (yoe as i64) + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = y + if m <= 2 { 1 } else { 0 };
    let mon = MONTHS[(m as usize) - 1];
    format!("{wday} {mon} {d:2} {hh:02}:{mm:02}:{ss:02} {year}")
}

fn normalize_cfg_path(p: &str) -> String {
    let s = p.replace('\\', "/");
    let trimmed = s.trim_matches('/');
    if trimmed.is_empty() {
        "/".to_string()
    } else {
        format!("/{trimmed}")
    }
}

struct FileContextRule {
    order: usize,
    regex: Regex,
    file_type: Option<char>,
    context: String,
}

/// Matcher loaded from an Android `file_contexts` file.
#[derive(Default)]
pub struct FileContextsMatcher {
    exact: HashMap<String, (usize, String)>,
    rules: Vec<FileContextRule>,
    has_non_literal: bool,
}

impl FileContextsMatcher {
    pub fn load(path: &Path) -> Result<Self> {
        Self::parse_str(&fs::read_to_string(path)?)
    }

    pub fn parse_str(text: &str) -> Result<Self> {
        let mut exact = HashMap::new();
        let mut rules = Vec::new();
        let mut has_non_literal = false;
        let mut order = 0usize;
        for raw_line in text.lines() {
            let line = raw_line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let parts: Vec<&str> = line.split_whitespace().collect();
            let (pattern, file_type, context) = match parts.len() {
                2 => (parts[0], None, parts[1]),
                3 if parts[1].starts_with('-') && parts[1].len() == 2 => {
                    (parts[0], parts[1].chars().nth(1), parts[2])
                }
                _ => continue,
            };
            if context == "<<none>>" {
                continue;
            }
            order += 1;
            let is_literal = file_type.is_none() && is_simple_escaped_literal(pattern);
            if is_literal {
                let unescaped = unescape_literal(pattern);
                exact.insert(unescaped, (order, context.to_string()));
            } else {
                has_non_literal = true;
                let anchored = format!("^(?:{pattern})$");
                if let Ok(regex) = Regex::new(&anchored) {
                    rules.push(FileContextRule {
                        order,
                        regex,
                        file_type,
                        context: context.to_string(),
                    });
                }
            }
        }
        Ok(Self {
            exact,
            rules,
            has_non_literal,
        })
    }

    fn lookup_one(&self, candidate: &str, mode_type_char: char) -> Option<&str> {
        let exact_hit = self.exact.get(candidate);
        if !self.has_non_literal {
            return exact_hit.map(|(_, s)| s.as_str());
        }
        let exact_order = exact_hit.map(|(o, _)| *o).unwrap_or(0);
        for rule in self.rules.iter().rev() {
            if rule.order <= exact_order {
                break;
            }
            if let Some(ft) = rule.file_type {
                if ft != mode_type_char {
                    continue;
                }
            }
            if rule.regex.is_match(candidate) {
                return Some(rule.context.as_str());
            }
        }
        exact_hit.map(|(_, s)| s.as_str())
    }

    /// Match a path against `file_contexts` (last matching rule wins, matching libselinux).
    pub fn lookup(&self, path: &str, mode_type_char: char, mount_point: &str) -> Option<&str> {
        let norm = normalize_cfg_path(path);
        let mp = mount_point.trim_matches('/');
        let full = if mp.is_empty() {
            norm.clone()
        } else if norm == "/" {
            format!("/{mp}")
        } else {
            format!("/{mp}{norm}")
        };

        if let Some(ctx) = self.lookup_one(&full, mode_type_char) {
            return Some(ctx);
        }
        if full != norm {
            return self.lookup_one(&norm, mode_type_char);
        }
        None
    }

    /// Return true when every exact/simple path already starts with `/<part>`.
    pub fn has_partition_prefix(&self, part: &str) -> bool {
        let p = part.trim_matches('/');
        if p.is_empty() {
            return false;
        }
        let exact_root = format!("/{p}");
        let sub_prefix = format!("/{p}/");
        let mut saw_non_root = false;
        for k in self.exact.keys() {
            if k == "/" {
                continue;
            }
            saw_non_root = true;
            if k != &exact_root && !k.starts_with(&sub_prefix) {
                return false;
            }
        }
        saw_non_root
    }
}

impl FsConfigTable {
    pub fn parse_str(text: &str) -> Result<Self> {
        let mut entries = HashMap::new();
        for (lineno, raw_line) in text.lines().enumerate() {
            let line = raw_line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let parts: Vec<&str> = line.split_whitespace().collect();
            if parts.len() < 4 {
                continue;
            }
            let key = normalize_cfg_path(parts[0]);
            let uid = parts[1].parse::<u32>().map_err(|_| {
                ErofsError::InvalidArg(format!("fs_config line {}: invalid uid", lineno + 1))
            })?;
            let gid = parts[2].parse::<u32>().map_err(|_| {
                ErofsError::InvalidArg(format!("fs_config line {}: invalid gid", lineno + 1))
            })?;
            let mode = u16::from_str_radix(parts[3], 8).map_err(|_| {
                ErofsError::InvalidArg(format!("fs_config line {}: invalid octal mode", lineno + 1))
            })?;
            let mut capabilities = 0u64;
            for extra in &parts[4..] {
                if let Some(hex) = extra.strip_prefix("capabilities=") {
                    let clean_hex = hex
                        .strip_prefix("0x")
                        .or_else(|| hex.strip_prefix("0X"))
                        .unwrap_or(hex);
                    capabilities = u64::from_str_radix(clean_hex, 16).unwrap_or(0);
                }
            }
            entries.insert(
                key,
                FsConfigEntry {
                    uid,
                    gid,
                    mode,
                    capabilities,
                },
            );
        }
        Ok(Self { entries })
    }
}

fn is_simple_escaped_literal(pat: &str) -> bool {
    let mut chars = pat.chars().peekable();
    while let Some(c) = chars.next() {
        if c == '\\' {
            if chars.next().is_none() {
                return false;
            }
        } else if matches!(c, '.' | '+' | '*' | '?' | '[' | ']' | '(' | ')' | '|' | '^' | '$' | '{' | '}') {
            return false;
        }
    }
    true
}

fn unescape_literal(pat: &str) -> String {
    let mut out = String::with_capacity(pat.len());
    let mut chars = pat.chars();
    while let Some(c) = chars.next() {
        if c == '\\' {
            if let Some(next) = chars.next() {
                out.push(next);
            }
        } else {
            out.push(c);
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fs_config_mount_point_priority_and_sar() {
        let cfg = FsConfigTable::parse_str(
            "/ 0 0 0755\n\
             system 0 0 0755\n\
             system/etc 0 0 0644\n\
             system/system 0 0 0755\n\
             system/system/bin 0 2000 0755\n\
             system/system/bin/run-as 0 2000 0750 capabilities=0xC0\n",
        )
        .unwrap();

        assert!(cfg.has_partition_prefix("system"));
        assert!(!cfg.has_partition_prefix("vendor"));

        assert_eq!(cfg.lookup("/", "/system").unwrap().mode, 0o755);
        assert_eq!(cfg.lookup("/etc", "/system").unwrap().mode, 0o644);
        assert_eq!(cfg.lookup("/system", "/system").unwrap().mode, 0o755);
        assert_eq!(cfg.lookup("/system/bin", "/system").unwrap().gid, 2000);
        let runas = cfg.lookup("/system/bin/run-as", "/system").unwrap();
        assert_eq!(runas.capabilities, 0xC0);

        let vcfg = FsConfigTable::parse_str(
            "/ 0 0 0755\n\
             vendor 0 2000 0755\n\
             vendor/bin 0 2000 0755\n",
        )
        .unwrap();
        // Root "/" under mount_point "/vendor" must match vendor (gid=2000), NOT "/ 0 0 0755".
        assert_eq!(vcfg.lookup("/", "/vendor").unwrap().gid, 2000);
    }

    #[test]
    fn file_contexts_last_rule_and_mount_point_priority() {
        let fc = FileContextsMatcher::parse_str(
            "/ u:object_r:rootfs:s0\n\
             /system u:object_r:rootfs:s0\n\
             /system/system u:object_r:system_file:s0\n\
             /system/system/.* u:object_r:override_regex:s0\n\
             /system/bin/.* -- u:object_r:reg_only:s0\n\
             /system/bin/app_process64 u:object_r:zygote_exec:s0\n\
             /system/system/bin/.* u:object_r:system_bin_wildcard:s0\n",
        )
        .unwrap();

        assert_eq!(fc.lookup("/system", 'd', "/system"), Some("u:object_r:system_file:s0"));
        assert_eq!(
            fc.lookup("/system/foo.txt", '-', "/system"),
            Some("u:object_r:override_regex:s0")
        );
        // Full path "/system/system/bin/x" matches later wildcard rather than norm "/system/bin/..."
        assert_eq!(
            fc.lookup("/system/bin/x", '-', "/system"),
            Some("u:object_r:system_bin_wildcard:s0")
        );
        // Earlier file-type rule overridden or respected cleanly
        assert_eq!(
            fc.lookup("/bin/app_process64", '-', "/system"),
            Some("u:object_r:zygote_exec:s0")
        );
        assert_eq!(
            fc.lookup("/bin/other", '-', "/system"),
            Some("u:object_r:reg_only:s0")
        );
        assert_eq!(fc.lookup("/bin/other", 'd', "/system"), None);
    }

    #[test]
    fn ctime_utc_formatting() {
        assert_eq!(format_ctime_utc(0), "Thu Jan  1 00:00:00 1970");
        assert_eq!(format_ctime_utc(1_230_768_000), "Thu Jan  1 00:00:00 2009");
        assert_eq!(format_ctime_utc(1_700_000_000), "Tue Nov 14 22:13:20 2023");
    }
}
