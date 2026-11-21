// SPDX-License-Identifier: MPL-2.0
//! Clean-room Android configuration, fstab, vbmeta, prop, and SELinux audit transform utilities.

use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use serde::Serialize;
use similar::TextDiff;
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::PathBuf;

const AVB_MAGIC: &[u8; 4] = b"AVB0";
const AVB_FLAGS_OFFSET: usize = 120;
const AVB_HEADER_MIN_SIZE: usize = 256;
pub const AVB_FLAG_HASHTREE_DISABLED: u32 = 1 << 0;
pub const AVB_FLAG_VERIFICATION_DISABLED: u32 = 1 << 1;

#[derive(Serialize)]
pub struct LineChange {
    pub line: usize,
    pub before: String,
    pub after: String,
}

#[derive(Serialize)]
pub struct TextPatchResult {
    pub changed: bool,
    pub modified_text: String,
    pub diff: String,
    pub changes: Vec<LineChange>,
}

#[derive(Serialize)]
pub struct VbmetaInfo {
    pub valid: bool,
    pub flags: u32,
    pub hashtree_disabled: bool,
    pub verification_disabled: bool,
    pub size: u64,
}

#[derive(Serialize)]
pub struct AuditAllowResult {
    pub denial_count: usize,
    pub rule_count: usize,
    pub te_rules: String,
    pub magisk_rules: String,
    pub cil_rules: String,
}

fn is_avb_flag(token: &str) -> bool {
    let lower = token.trim().to_ascii_lowercase();
    lower == "avb"
        || lower == "verify"
        || lower.starts_with("avb=")
        || lower.starts_with("avb_keys=")
}

fn is_encryption_flag(token: &str) -> bool {
    let lower = token.trim().to_ascii_lowercase();
    matches!(
        lower.as_str(),
        "forceencrypt"
            | "forcecrypt"
            | "fsverity"
            | "inlinecrypt"
            | "forcefdeorfbe"
            | "wrappedkey"
            | "encryptable"
    ) || lower.starts_with("fileencryption=")
        || lower.starts_with("metadata_encryption=")
        || lower.starts_with("keydirectory=")
        || lower.starts_with("encryptable=")
        || lower.starts_with("forceencrypt=")
        || lower.starts_with("forcefdeorfbe=")
}

fn clean_option_column(col: &str, disable_avb: bool, disable_encryption: bool) -> (String, bool) {
    let tokens: Vec<&str> = col.split(',').collect();
    let mut kept = Vec::with_capacity(tokens.len());
    let mut changed = false;

    for raw in tokens {
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            continue;
        }
        let drop_token =
            (disable_avb && is_avb_flag(trimmed)) || (disable_encryption && is_encryption_flag(trimmed));
        if drop_token {
            changed = true;
        } else {
            kept.push(trimmed);
        }
    }

    if !changed {
        return (col.to_string(), false);
    }
    if kept.is_empty() {
        ("defaults".to_string(), true)
    } else {
        (kept.join(","), true)
    }
}

/// Split a single fstab line into alternating (whitespace/token) segments while keeping layout intact.
fn transform_fstab_line(line: &str, disable_avb: bool, disable_encryption: bool) -> (String, bool) {
    let trimmed = line.trim();
    if trimmed.is_empty() || trimmed.starts_with('#') {
        return (line.to_string(), false);
    }

    let mut segments: Vec<String> = Vec::new();
    let mut token_indices: Vec<usize> = Vec::new();
    let mut idx = 0;
    let bytes = line.as_bytes();

    while idx < bytes.len() {
        let start = idx;
        if bytes[idx].is_ascii_whitespace() {
            while idx < bytes.len() && bytes[idx].is_ascii_whitespace() {
                idx += 1;
            }
            segments.push(line[start..idx].to_string());
        } else {
            while idx < bytes.len() && !bytes[idx].is_ascii_whitespace() {
                idx += 1;
            }
            token_indices.push(segments.len());
            segments.push(line[start..idx].to_string());
        }
    }

    // Standard Android fstab has 5 columns: <src> <mnt_point> <type> <mnt_flags> <fs_mgr_flags>.
    // Check both column 4 (mnt_flags) and column 5 (fs_mgr_flags) when present.
    let mut line_changed = false;
    for &col_slot in &[3usize, 4usize] {
        if let Some(&seg_idx) = token_indices.get(col_slot) {
            let (updated, changed) =
                clean_option_column(&segments[seg_idx], disable_avb, disable_encryption);
            if changed {
                segments[seg_idx] = updated;
                line_changed = true;
            }
        }
    }

    if line_changed {
        (segments.concat(), true)
    } else {
        (line.to_string(), false)
    }
}

pub fn patch_fstab_core(
    input: &str,
    disable_avb: bool,
    disable_encryption: bool,
) -> TextPatchResult {
    let use_crlf = input.contains("\r\n");
    let ending = if use_crlf { "\r\n" } else { "\n" };
    let has_trailing_newline = input.ends_with('\n');

    let mut out_lines: Vec<String> = Vec::new();
    let mut changes: Vec<LineChange> = Vec::new();

    for (line_idx, raw_line) in input.lines().enumerate() {
        let (updated, changed) = transform_fstab_line(raw_line, disable_avb, disable_encryption);
        if changed {
            changes.push(LineChange {
                line: line_idx + 1,
                before: raw_line.to_string(),
                after: updated.clone(),
            });
        }
        out_lines.push(updated);
    }

    let mut modified_text = out_lines.join(ending);
    if has_trailing_newline && !modified_text.is_empty() {
        modified_text.push_str(ending);
    }

    let diff = if changes.is_empty() {
        String::new()
    } else {
        TextDiff::from_lines(input, &modified_text)
            .unified_diff()
            .header("fstab (before)", "fstab (after)")
            .to_string()
    };

    TextPatchResult {
        changed: !changes.is_empty(),
        modified_text,
        diff,
        changes,
    }
}

pub fn patch_props_core(
    input: &str,
    updates: &[(String, String)],
    removals: &[String],
) -> TextPatchResult {
    let use_crlf = input.contains("\r\n");
    let ending = if use_crlf { "\r\n" } else { "\n" };
    let has_trailing_newline = input.ends_with('\n');

    let remove_set: BTreeSet<&str> = removals.iter().map(|s| s.as_str()).collect();
    let mut pending: BTreeMap<&str, &str> = BTreeMap::new();
    for (k, v) in updates {
        pending.insert(k.as_str(), v.as_str());
    }
    let mut seen: BTreeSet<String> = BTreeSet::new();

    let mut out_lines: Vec<String> = Vec::new();
    let mut changes: Vec<LineChange> = Vec::new();

    for (idx, raw_line) in input.lines().enumerate() {
        let trimmed = raw_line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            out_lines.push(raw_line.to_string());
            continue;
        }
        if let Some((key_raw, _val_raw)) = raw_line.split_once('=') {
            let key = key_raw.trim();
            if remove_set.contains(key) {
                changes.push(LineChange {
                    line: idx + 1,
                    before: raw_line.to_string(),
                    after: String::new(),
                });
                continue;
            }
            if let Some(&new_val) = pending.get(key) {
                let updated = format!("{key}={new_val}");
                seen.insert(key.to_string());
                if updated != raw_line {
                    changes.push(LineChange {
                        line: idx + 1,
                        before: raw_line.to_string(),
                        after: updated.clone(),
                    });
                }
                out_lines.push(updated);
                continue;
            }
        }
        out_lines.push(raw_line.to_string());
    }

    for (k, v) in updates {
        if !seen.contains(k) && !remove_set.contains(k.as_str()) {
            let line = format!("{k}={v}");
            changes.push(LineChange {
                line: out_lines.len() + 1,
                before: String::new(),
                after: line.clone(),
            });
            out_lines.push(line);
        }
    }

    let mut modified_text = out_lines.join(ending);
    if has_trailing_newline && !modified_text.is_empty() {
        modified_text.push_str(ending);
    }

    let diff = if changes.is_empty() {
        String::new()
    } else {
        TextDiff::from_lines(input, &modified_text)
            .unified_diff()
            .header("prop (before)", "prop (after)")
            .to_string()
    };

    TextPatchResult {
        changed: !changes.is_empty(),
        modified_text,
        diff,
        changes,
    }
}

fn extract_selinux_type(context: &str) -> Option<&str> {
    // SELinux context format: user:role:type[:mls]
    let mut parts = context.split(':');
    let _user = parts.next()?;
    let _role = parts.next()?;
    let ty = parts.next()?;
    if ty.is_empty() {
        None
    } else {
        Some(ty)
    }
}

fn extract_kv<'a>(line: &'a str, key: &str) -> Option<&'a str> {
    let needle = format!("{key}=");
    let start = line.find(&needle)? + needle.len();
    let rest = &line[start..];
    let end = rest.find(char::is_whitespace).unwrap_or(rest.len());
    let val = &rest[..end];
    if val.is_empty() {
        None
    } else {
        Some(val)
    }
}

pub fn audit_to_allow_core(log_text: &str, include_untrusted: bool) -> AuditAllowResult {
    let mut rules: BTreeMap<(String, String, String), BTreeSet<String>> = BTreeMap::new();
    let mut denial_count = 0usize;

    for line in log_text.lines() {
        if !line.contains("avc:") || !line.contains("denied") {
            continue;
        }
        if !include_untrusted && line.contains("untrusted_app") {
            continue;
        }

        let Some(open_brace) = line.find('{') else {
            continue;
        };
        let Some(rel_close) = line[open_brace + 1..].find('}') else {
            continue;
        };
        let perms_raw = &line[open_brace + 1..open_brace + 1 + rel_close];
        let perms: Vec<&str> = perms_raw.split_whitespace().collect();
        if perms.is_empty() {
            continue;
        }

        let Some(scontext_raw) = extract_kv(line, "scontext") else {
            continue;
        };
        let Some(tcontext_raw) = extract_kv(line, "tcontext") else {
            continue;
        };
        let Some(tclass) = extract_kv(line, "tclass") else {
            continue;
        };

        let Some(stype) = extract_selinux_type(scontext_raw) else {
            continue;
        };
        let Some(ttype) = extract_selinux_type(tcontext_raw) else {
            continue;
        };

        denial_count += 1;
        let entry = rules
            .entry((stype.to_string(), ttype.to_string(), tclass.to_string()))
            .or_default();
        for p in perms {
            entry.insert(p.to_string());
        }
    }

    let mut te_lines = Vec::with_capacity(rules.len());
    let mut magisk_lines = Vec::with_capacity(rules.len());
    let mut cil_lines = Vec::with_capacity(rules.len());

    for ((s, t, c), perms) in &rules {
        let joined: String = perms.iter().cloned().collect::<Vec<_>>().join(" ");
        if perms.len() == 1 {
            te_lines.push(format!("allow {s} {t}:{c} {joined};"));
        } else {
            te_lines.push(format!("allow {s} {t}:{c} {{ {joined} }};"));
        }
        magisk_lines.push(format!("allow {s} {t} {c} {{ {joined} }}"));
        cil_lines.push(format!("(allow {s} {t} ({c} ({joined})))"));
    }

    AuditAllowResult {
        denial_count,
        rule_count: rules.len(),
        te_rules: te_lines.join("\n"),
        magisk_rules: magisk_lines.join("\n"),
        cil_rules: cil_lines.join("\n"),
    }
}

pub fn inspect_vbmeta_bytes(header: &[u8], file_size: u64) -> VbmetaInfo {
    if header.len() < AVB_HEADER_MIN_SIZE || &header[..4] != AVB_MAGIC {
        return VbmetaInfo {
            valid: false,
            flags: 0,
            hashtree_disabled: false,
            verification_disabled: false,
            size: file_size,
        };
    }
    let flags = u32::from_be_bytes([
        header[AVB_FLAGS_OFFSET],
        header[AVB_FLAGS_OFFSET + 1],
        header[AVB_FLAGS_OFFSET + 2],
        header[AVB_FLAGS_OFFSET + 3],
    ]);
    VbmetaInfo {
        valid: true,
        flags,
        hashtree_disabled: (flags & AVB_FLAG_HASHTREE_DISABLED) != 0,
        verification_disabled: (flags & AVB_FLAG_VERIFICATION_DISABLED) != 0,
        size: file_size,
    }
}

#[pyfunction]
#[pyo3(signature = (text, disable_avb=true, disable_encryption=true))]
pub fn patch_fstab_json(
    py: Python<'_>,
    text: String,
    disable_avb: bool,
    disable_encryption: bool,
) -> PyResult<String> {
    let result = py.detach(move || patch_fstab_core(&text, disable_avb, disable_encryption));
    serde_json::to_string(&result).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (text, updates, removals=vec![]))]
pub fn patch_props_json(
    py: Python<'_>,
    text: String,
    updates: Vec<(String, String)>,
    removals: Vec<String>,
) -> PyResult<String> {
    let result = py.detach(move || patch_props_core(&text, &updates, &removals));
    serde_json::to_string(&result).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (log_text, include_untrusted=false))]
pub fn selinux_audit_allow_json(
    py: Python<'_>,
    log_text: String,
    include_untrusted: bool,
) -> PyResult<String> {
    let result = py.detach(move || audit_to_allow_core(&log_text, include_untrusted));
    serde_json::to_string(&result).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pyfunction]
pub fn inspect_vbmeta_json(py: Python<'_>, path: PathBuf) -> PyResult<String> {
    let info = py
        .detach(move || -> std::io::Result<VbmetaInfo> {
            let meta = fs::metadata(&path)?;
            let mut file = fs::File::open(&path)?;
            let mut buf = [0u8; AVB_HEADER_MIN_SIZE];
            let n = file.read(&mut buf)?;
            Ok(inspect_vbmeta_bytes(&buf[..n], meta.len()))
        })
        .map_err(|e| PyOSError::new_err(e.to_string()))?;
    serde_json::to_string(&info).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (path, disable_hashtree=true, disable_verification=true))]
pub fn patch_vbmeta_json(
    py: Python<'_>,
    path: PathBuf,
    disable_hashtree: bool,
    disable_verification: bool,
) -> PyResult<String> {
    let info = py
        .detach(move || -> Result<VbmetaInfo, String> {
            let mut file = OpenOptions::new()
                .read(true)
                .write(true)
                .open(&path)
                .map_err(|e| e.to_string())?;
            let file_size = file.metadata().map_err(|e| e.to_string())?.len();
            let mut buf = [0u8; AVB_HEADER_MIN_SIZE];
            file.read_exact(&mut buf).map_err(|e| e.to_string())?;
            let current = inspect_vbmeta_bytes(&buf, file_size);
            if !current.valid {
                return Err("Not a valid AVB2 vbmeta image (missing AVB0 magic)".to_string());
            }
            let mut new_flags = current.flags;
            if disable_hashtree {
                new_flags |= AVB_FLAG_HASHTREE_DISABLED;
            }
            if disable_verification {
                new_flags |= AVB_FLAG_VERIFICATION_DISABLED;
            }
            if new_flags != current.flags {
                file.seek(SeekFrom::Start(AVB_FLAGS_OFFSET as u64))
                    .map_err(|e| e.to_string())?;
                file.write_all(&new_flags.to_be_bytes())
                    .map_err(|e| e.to_string())?;
                file.flush().map_err(|e| e.to_string())?;
            }
            Ok(VbmetaInfo {
                valid: true,
                flags: new_flags,
                hashtree_disabled: (new_flags & AVB_FLAG_HASHTREE_DISABLED) != 0,
                verification_disabled: (new_flags & AVB_FLAG_VERIFICATION_DISABLED) != 0,
                size: file_size,
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&info).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fstab_removes_avb_and_encryption_preserves_latemount() {
        let input = "\
# Android fstab
/dev/block/by-name/system  /system  ext4  ro,barrier=1  wait,slotselect,avb=vbmeta_system,logical,first_stage_mount
/dev/block/by-name/userdata  /data  f2fs  noatime,nosuid,nodev  latemount,wait,check,fileencryption=aes-256-xts:aes-256-cts:v2+inlinecrypt_optimized,keydirectory=/metadata/vold/metadata_encryption,quota
/dev/block/by-name/vendor  /vendor  erofs  ro  wait,avb
";
        let res = patch_fstab_core(input, true, true);
        assert!(res.changed);
        assert_eq!(res.changes.len(), 3);
        assert!(res.modified_text.contains("wait,slotselect,logical,first_stage_mount"));
        assert!(res.modified_text.contains("latemount,wait,check,quota"));
        assert!(!res.modified_text.contains("fileencryption"));
        assert!(!res.modified_text.contains("keydirectory"));
        assert!(!res.modified_text.contains("avb=vbmeta_system"));
        assert!(res.modified_text.ends_with("erofs  ro  wait\n"));
        assert!(res.diff.contains("-/dev/block/by-name/system"));
        assert!(res.diff.contains("+/dev/block/by-name/system"));
    }

    #[test]
    fn fstab_falls_back_to_defaults_when_all_flags_removed() {
        let input = "/dev/block/by-name/misc /misc emmc defaults avb,verify\n";
        let res = patch_fstab_core(input, true, false);
        assert!(res.changed);
        assert_eq!(res.modified_text, "/dev/block/by-name/misc /misc emmc defaults defaults\n");
    }

    #[test]
    fn vbmeta_header_inspection_and_patching() {
        let mut hdr = vec![0u8; 256];
        hdr[..4].copy_from_slice(b"AVB0");
        hdr[120..124].copy_from_slice(&0u32.to_be_bytes());
        let info = inspect_vbmeta_bytes(&hdr, 4096);
        assert!(info.valid);
        assert_eq!(info.flags, 0);
        assert!(!info.hashtree_disabled);
        assert!(!info.verification_disabled);
    }

    #[test]
    fn selinux_audit_merges_and_formats_rules() {
        let log = "\
01-01 00:00:01.000 I/auditd(100): avc: denied { read open } for pid=123 scontext=u:r:hal_audio_default:s0 tcontext=u:object_r:vendor_file:s0 tclass=file permissive=0
01-01 00:00:02.000 I/auditd(100): avc: denied { getattr } for pid=123 scontext=u:r:hal_audio_default:s0 tcontext=u:object_r:vendor_file:s0 tclass=file permissive=0
01-01 00:00:03.000 I/auditd(100): avc: denied { read } for pid=999 scontext=u:r:untrusted_app:s0:c512,c768 tcontext=u:object_r:sysfs:s0 tclass=file permissive=0
";
        let res = audit_to_allow_core(log, false);
        assert_eq!(res.denial_count, 2);
        assert_eq!(res.rule_count, 1);
        assert_eq!(
            res.te_rules,
            "allow hal_audio_default vendor_file:file { getattr open read };"
        );
        assert_eq!(
            res.cil_rules,
            "(allow hal_audio_default vendor_file (file (getattr open read)))"
        );
    }

    #[test]
    fn prop_patch_updates_and_appends() {
        let src = "# comment\nro.debuggable=0\nro.secure=1\n";
        let res = patch_props_core(
            src,
            &[
                ("ro.debuggable".into(), "1".into()),
                ("persist.sys.usb.config".into(), "adb".into()),
            ],
            &["ro.secure".into()],
        );
        assert!(res.changed);
        assert_eq!(
            res.modified_text,
            "# comment\nro.debuggable=1\npersist.sys.usb.config=adb\n"
        );
    }
}
