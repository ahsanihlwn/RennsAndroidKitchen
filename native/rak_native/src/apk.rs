// SPDX-License-Identifier: MPL-2.0
//! Clean-room parallel APK inventory scanner and AOSP binary XML / resources.arsc parser.

use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use rayon::prelude::*;
use serde::Serialize;
use std::collections::HashSet;
use std::fs::{self, File};
use std::io::{BufReader, Read};
use std::path::{Path, PathBuf};
use zip::ZipArchive;

const RES_STRING_POOL_TYPE: u16 = 0x0001;
const RES_TABLE_TYPE: u16 = 0x0002;
const RES_XML_TYPE: u16 = 0x0003;
const RES_XML_RESOURCE_MAP_TYPE: u16 = 0x0180;
const RES_XML_START_ELEMENT_TYPE: u16 = 0x0102;
const RES_TABLE_PACKAGE_TYPE: u16 = 0x0200;
const RES_TABLE_TYPE_TYPE: u16 = 0x0201;

const UTF8_FLAG: u32 = 1 << 8;

const TYPE_REFERENCE: u8 = 0x01;
const TYPE_STRING: u8 = 0x03;
const TYPE_INT_DEC: u8 = 0x10;
const TYPE_INT_HEX: u8 = 0x11;
const TYPE_INT_BOOLEAN: u8 = 0x12;

const ATTR_ID_LABEL: u32 = 0x0101_0001;
const ATTR_ID_ICON: u32 = 0x0101_0002;
const ATTR_ID_NAME: u32 = 0x0101_0003;
const ATTR_ID_MIN_SDK: u32 = 0x0101_020c;
const ATTR_ID_VERSION_CODE: u32 = 0x0101_021b;
const ATTR_ID_VERSION_NAME: u32 = 0x0101_021c;
const ATTR_ID_TARGET_SDK: u32 = 0x0101_0270;

#[derive(Serialize)]
pub struct ApkRecord {
    pub path: String,
    pub rel_path: String,
    pub filename: String,
    pub partition: String,
    pub category: String,
    pub name: String,
    pub package: String,
    pub version_name: String,
    pub version_code: String,
    pub min_sdk: String,
    pub target_sdk: String,
    pub permissions: Vec<String>,
    pub icon_entry: String,
    pub size: u64,
    pub risk: String,
    pub error: String,
}

#[inline]
fn r_u16(data: &[u8], off: usize) -> Option<u16> {
    let bytes: [u8; 2] = data.get(off..off + 2)?.try_into().ok()?;
    Some(u16::from_le_bytes(bytes))
}

#[inline]
fn r_u32(data: &[u8], off: usize) -> Option<u32> {
    let bytes: [u8; 4] = data.get(off..off + 4)?.try_into().ok()?;
    Some(u32::from_le_bytes(bytes))
}

#[inline]
fn r_i32(data: &[u8], off: usize) -> Option<i32> {
    let bytes: [u8; 4] = data.get(off..off + 4)?.try_into().ok()?;
    Some(i32::from_le_bytes(bytes))
}

fn decode_utf8_len(data: &[u8], pos: &mut usize) -> Option<usize> {
    let first = *data.get(*pos)? as usize;
    *pos += 1;
    if (first & 0x80) != 0 {
        let second = *data.get(*pos)? as usize;
        *pos += 1;
        Some(((first & 0x7f) << 8) | second)
    } else {
        Some(first)
    }
}

fn decode_utf16_len(data: &[u8], pos: &mut usize) -> Option<usize> {
    let first = r_u16(data, *pos)? as usize;
    *pos += 2;
    if (first & 0x8000) != 0 {
        let second = r_u16(data, *pos)? as usize;
        *pos += 2;
        Some(((first & 0x7fff) << 16) | second)
    } else {
        Some(first)
    }
}

fn parse_string_pool(chunk: &[u8]) -> Option<Vec<String>> {
    if chunk.len() < 28 {
        return None;
    }
    let header_size = r_u16(chunk, 2)? as usize;
    let string_count = r_u32(chunk, 8)? as usize;
    let flags = r_u32(chunk, 16)?;
    let strings_start = r_u32(chunk, 20)? as usize;

    // Guard against corrupted headers claiming huge string pools.
    if string_count > 1_000_000 || header_size > chunk.len() || strings_start > chunk.len() {
        return None;
    }
    let offsets_end = header_size.checked_add(string_count.checked_mul(4)?)?;
    if offsets_end > chunk.len() {
        return None;
    }

    let is_utf8 = (flags & UTF8_FLAG) != 0;
    let mut out = Vec::with_capacity(string_count);

    for i in 0..string_count {
        let rel_off = r_u32(chunk, header_size + i * 4)? as usize;
        let Some(mut pos) = strings_start.checked_add(rel_off) else {
            out.push(String::new());
            continue;
        };
        if pos >= chunk.len() {
            out.push(String::new());
            continue;
        }
        if is_utf8 {
            let _utf16_chars = decode_utf8_len(chunk, &mut pos);
            let Some(byte_len) = decode_utf8_len(chunk, &mut pos) else {
                out.push(String::new());
                continue;
            };
            let end = pos.saturating_add(byte_len).min(chunk.len());
            out.push(String::from_utf8_lossy(&chunk[pos..end]).into_owned());
        } else {
            let Some(char_len) = decode_utf16_len(chunk, &mut pos) else {
                out.push(String::new());
                continue;
            };
            let byte_len = char_len.saturating_mul(2);
            let end = pos.saturating_add(byte_len).min(chunk.len());
            let slice = &chunk[pos..end];
            let units: Vec<u16> = slice
                .chunks_exact(2)
                .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
                .collect();
            out.push(String::from_utf16_lossy(&units));
        }
    }

    Some(out)
}

#[derive(Default)]
struct ManifestParsed {
    package: String,
    version_name: String,
    version_code: String,
    min_sdk: String,
    target_sdk: String,
    label: String,
    label_res_id: Option<u32>,
    icon_raw: String,
    icon_res_id: Option<u32>,
    permissions: Vec<String>,
}

fn parse_axml_manifest(data: &[u8]) -> Option<ManifestParsed> {
    if data.len() < 8 || r_u16(data, 0)? != RES_XML_TYPE {
        return None;
    }
    let total_size = (r_u32(data, 4)? as usize).min(data.len());
    let mut pos = r_u16(data, 2)? as usize;

    let mut pool: Vec<String> = Vec::new();
    let mut res_map: Vec<u32> = Vec::new();
    let mut manifest = ManifestParsed::default();
    let mut perm_seen: HashSet<String> = HashSet::new();

    while pos + 8 <= total_size {
        let chunk_type = r_u16(data, pos)?;
        let header_size = r_u16(data, pos + 2)? as usize;
        let chunk_size = r_u32(data, pos + 4)? as usize;
        if chunk_size < 8 || pos + chunk_size > total_size {
            break;
        }
        let chunk = &data[pos..pos + chunk_size];

        match chunk_type {
            RES_STRING_POOL_TYPE => {
                if let Some(p) = parse_string_pool(chunk) {
                    pool = p;
                }
            }
            RES_XML_RESOURCE_MAP_TYPE => {
                if header_size <= chunk.len() {
                    for pair in chunk[header_size..].chunks_exact(4) {
                        res_map.push(u32::from_le_bytes([pair[0], pair[1], pair[2], pair[3]]));
                    }
                }
            }
            RES_XML_START_ELEMENT_TYPE => {
                let body = header_size;
                if body + 20 <= chunk.len() {
                    let elem_name_idx = r_i32(chunk, body + 4)?;
                    let attr_start = r_u16(chunk, body + 8)? as usize;
                    let attr_size = r_u16(chunk, body + 10)? as usize;
                    let attr_count = r_u16(chunk, body + 12)? as usize;
                    let elem_name = usize::try_from(elem_name_idx)
                        .ok()
                        .and_then(|i| pool.get(i))
                        .map(|s| s.as_str())
                        .unwrap_or("");

                    if attr_size >= 20 {
                        let attrs_base = body + attr_start;
                        for a in 0..attr_count {
                            let a_off = attrs_base + a * attr_size;
                            if a_off + 20 > chunk.len() {
                                break;
                            }
                            let name_idx = r_i32(chunk, a_off + 4)?;
                            let raw_val_idx = r_i32(chunk, a_off + 8)?;
                            let data_type = chunk[a_off + 15];
                            let typed_data = r_u32(chunk, a_off + 16)?;

                            let attr_name = usize::try_from(name_idx)
                                .ok()
                                .and_then(|i| pool.get(i))
                                .map(|s| s.as_str())
                                .unwrap_or("");
                            let attr_res_id = usize::try_from(name_idx)
                                .ok()
                                .and_then(|i| res_map.get(i))
                                .copied()
                                .unwrap_or(0);

                            let val_str = match data_type {
                                TYPE_STRING => pool.get(typed_data as usize).cloned().unwrap_or_default(),
                                TYPE_INT_DEC => (typed_data as i32).to_string(),
                                TYPE_INT_HEX => format!("0x{typed_data:x}"),
                                TYPE_INT_BOOLEAN => {
                                    if typed_data != 0 {
                                        "true".to_string()
                                    } else {
                                        "false".to_string()
                                    }
                                }
                                TYPE_REFERENCE => format!("@0x{typed_data:08x}"),
                                _ => usize::try_from(raw_val_idx)
                                    .ok()
                                    .and_then(|i| pool.get(i))
                                    .cloned()
                                    .unwrap_or_default(),
                            };

                            match elem_name {
                                "manifest" => {
                                    if attr_name == "package" {
                                        manifest.package = val_str;
                                    } else if attr_res_id == ATTR_ID_VERSION_NAME || attr_name == "versionName" {
                                        manifest.version_name = val_str;
                                    } else if attr_res_id == ATTR_ID_VERSION_CODE || attr_name == "versionCode" {
                                        manifest.version_code = val_str;
                                    }
                                }
                                "uses-sdk" => {
                                    if attr_res_id == ATTR_ID_MIN_SDK || attr_name == "minSdkVersion" {
                                        manifest.min_sdk = val_str;
                                    } else if attr_res_id == ATTR_ID_TARGET_SDK || attr_name == "targetSdkVersion" {
                                        manifest.target_sdk = val_str;
                                    }
                                }
                                "uses-permission" | "uses-permission-sdk-23" => {
                                    if (attr_res_id == ATTR_ID_NAME || attr_name == "name")
                                        && !val_str.is_empty()
                                        && perm_seen.insert(val_str.clone())
                                    {
                                        manifest.permissions.push(val_str);
                                    }
                                }
                                "application" => {
                                    if attr_res_id == ATTR_ID_LABEL || attr_name == "label" {
                                        if data_type == TYPE_REFERENCE {
                                            manifest.label_res_id = Some(typed_data);
                                        } else if !val_str.starts_with('@') {
                                            manifest.label = val_str;
                                        }
                                    } else if attr_res_id == ATTR_ID_ICON || attr_name == "icon" {
                                        if data_type == TYPE_REFERENCE {
                                            manifest.icon_res_id = Some(typed_data);
                                        } else {
                                            manifest.icon_raw = val_str;
                                        }
                                    }
                                }
                                _ => {}
                            }
                        }
                    }
                }
            }
            _ => {}
        }

        pos += chunk_size;
    }

    Some(manifest)
}

struct ArscResolved {
    label: Option<String>,
    icon_entry: Option<String>,
}

fn is_raster_icon(path: &str) -> bool {
    let lower = path.to_ascii_lowercase();
    lower.ends_with(".png")
        || lower.ends_with(".webp")
        || lower.ends_with(".jpg")
        || lower.ends_with(".jpeg")
}

fn resolve_arsc(
    arsc: &[u8],
    label_id: Option<u32>,
    icon_id: Option<u32>,
    zip_names: &HashSet<String>,
) -> ArscResolved {
    let mut out = ArscResolved {
        label: None,
        icon_entry: None,
    };
    if label_id.is_none() && icon_id.is_none() {
        return out;
    }
    if arsc.len() < 12 || r_u16(arsc, 0) != Some(RES_TABLE_TYPE) {
        return out;
    }
    let table_hdr_size = r_u16(arsc, 2).unwrap_or(0) as usize;
    let total_size = (r_u32(arsc, 4).unwrap_or(0) as usize).min(arsc.len());
    let mut pos = table_hdr_size;

    let mut global_pool: Vec<String> = Vec::new();
    let mut icon_candidates: Vec<(u16, String)> = Vec::new();
    let mut label_candidates: Vec<(bool, String)> = Vec::new();

    while pos + 8 <= total_size {
        let Some(chunk_type) = r_u16(arsc, pos) else {
            break;
        };
        let Some(header_size) = r_u16(arsc, pos + 2).map(|v| v as usize) else {
            break;
        };
        let Some(chunk_size) = r_u32(arsc, pos + 4).map(|v| v as usize) else {
            break;
        };
        if chunk_size < 8 || pos + chunk_size > total_size {
            break;
        }
        let chunk = &arsc[pos..pos + chunk_size];

        if chunk_type == RES_STRING_POOL_TYPE && global_pool.is_empty() {
            if let Some(pool) = parse_string_pool(chunk) {
                global_pool = pool;
            }
        } else if chunk_type == RES_TABLE_PACKAGE_TYPE && chunk.len() >= header_size && header_size >= 12 {
            let pkg_id = r_u32(chunk, 8).unwrap_or(0) & 0xFF;
            let want_label_pkg = label_id.map(|id| (id >> 24) == pkg_id).unwrap_or(false);
            let want_icon_pkg = icon_id.map(|id| (id >> 24) == pkg_id).unwrap_or(false);

            if want_label_pkg || want_icon_pkg {
                let mut sub = header_size;
                while sub + 8 <= chunk.len() {
                    let Some(st) = r_u16(chunk, sub) else { break };
                    let Some(sh) = r_u16(chunk, sub + 2).map(|v| v as usize) else { break };
                    let Some(ss) = r_u32(chunk, sub + 4).map(|v| v as usize) else { break };
                    if ss < 8 || sub + ss > chunk.len() {
                        break;
                    }
                    let tchunk = &chunk[sub..sub + ss];
                    if st == RES_TABLE_TYPE_TYPE && sh >= 24 && tchunk.len() >= sh {
                        let type_id = tchunk[8] as u32;
                        let entry_count = r_u32(tchunk, 12).unwrap_or(0) as usize;
                        let entries_start = r_u32(tchunk, 16).unwrap_or(0) as usize;
                        // ResTable_config begins at offset 20; density is u16 at config+12 (=32) when config size >= 14.
                        let cfg_size = r_u32(tchunk, 20).unwrap_or(0);
                        let density = if cfg_size >= 14 && sh >= 34 {
                            r_u16(tchunk, 32).unwrap_or(0)
                        } else {
                            0
                        };
                        let is_default_lang = if cfg_size >= 12 && sh >= 32 {
                            &tchunk[28..32] == b"\0\0\0\0"
                        } else {
                            false
                        };

                        let check_entry = |target_id: u32| -> Option<String> {
                            if ((target_id >> 16) & 0xFF) != type_id {
                                return None;
                            }
                            let idx = (target_id & 0xFFFF) as usize;
                            if idx >= entry_count || sh + (idx + 1) * 4 > tchunk.len() {
                                return None;
                            }
                            let entry_rel = r_u32(tchunk, sh + idx * 4)?;
                            if entry_rel == 0xFFFF_FFFF {
                                return None;
                            }
                            let eoff = entries_start.checked_add(entry_rel as usize)?;
                            if eoff + 16 > tchunk.len() {
                                return None;
                            }
                            let esize = r_u16(tchunk, eoff)? as usize;
                            let eflags = r_u16(tchunk, eoff + 2)?;
                            if (eflags & 0x0001) != 0 {
                                return None; // complex bag entry
                            }
                            let val_off = eoff + esize;
                            if val_off + 8 > tchunk.len() {
                                return None;
                            }
                            let val_type = tchunk[val_off + 3];
                            let val_data = r_u32(tchunk, val_off + 4)? as usize;
                            if val_type == TYPE_STRING {
                                global_pool.get(val_data).cloned()
                            } else {
                                None
                            }
                        };

                        if want_label_pkg {
                            if let Some(lid) = label_id {
                                if let Some(s) = check_entry(lid) {
                                    if !s.is_empty() {
                                        label_candidates.push((is_default_lang, s));
                                    }
                                }
                            }
                        }
                        if want_icon_pkg {
                            if let Some(iid) = icon_id {
                                if let Some(s) = check_entry(iid) {
                                    if is_raster_icon(&s) && zip_names.contains(&s) {
                                        icon_candidates.push((density, s));
                                    }
                                }
                            }
                        }
                    }
                    sub += ss;
                }
            }
        }

        pos += chunk_size;
    }

    if let Some((_, s)) = label_candidates
        .iter()
        .find(|(is_def, _)| *is_def)
        .or_else(|| label_candidates.first())
    {
        out.label = Some(s.clone());
    }

    if !icon_candidates.is_empty() {
        let best = icon_candidates
            .iter()
            .filter(|(d, _)| *d > 0 && *d <= 480)
            .max_by_key(|(d, _)| *d)
            .or_else(|| icon_candidates.iter().max_by_key(|(d, _)| *d));
        if let Some((_, p)) = best {
            out.icon_entry = Some(p.clone());
        }
    }

    out
}

fn classify_apk(rel_posix: &str, package: &str) -> (String, String, String) {
    let parts: Vec<&str> = rel_posix.split('/').collect();
    let partition = parts.first().copied().unwrap_or("root").to_string();
    let lower = rel_posix.to_ascii_lowercase();
    let category = if lower.contains("/priv-app/") || lower.starts_with("priv-app/") {
        "priv-app"
    } else if lower.contains("/overlay/") || lower.starts_with("overlay/") {
        "overlay"
    } else if lower.contains("/framework/") || lower.starts_with("framework/") {
        "framework"
    } else if lower.contains("/app/") || lower.starts_with("app/") {
        "app"
    } else {
        "other"
    }
    .to_string();

    let pkg_lower = package.to_ascii_lowercase();
    let risk = if category == "framework"
        || pkg_lower == "android"
        || pkg_lower.starts_with("com.android.systemui")
        || pkg_lower.starts_with("com.android.phone")
        || pkg_lower.starts_with("com.android.providers.settings")
        || pkg_lower.starts_with("com.android.settings")
    {
        "critical"
    } else if category == "priv-app" || pkg_lower.starts_with("com.google.android.gms") {
        "caution"
    } else {
        "safe"
    }
    .to_string();

    (partition, category, risk)
}

pub fn inspect_single_apk(root: &Path, path: &Path) -> ApkRecord {
    let filename = path
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let rel_path = path
        .strip_prefix(root)
        .map(|p| p.to_string_lossy().replace('\\', "/"))
        .unwrap_or_else(|_| filename.clone());
    let fallback_name = path
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| filename.clone());

    let size = fs::metadata(path).map(|m| m.len()).unwrap_or(0);
    let mut rec = ApkRecord {
        path: path.to_string_lossy().into_owned(),
        rel_path: rel_path.clone(),
        filename,
        partition: String::new(),
        category: String::new(),
        name: fallback_name,
        package: String::new(),
        version_name: String::new(),
        version_code: String::new(),
        min_sdk: String::new(),
        target_sdk: String::new(),
        permissions: Vec::new(),
        icon_entry: String::new(),
        size,
        risk: "safe".to_string(),
        error: String::new(),
    };

    let file = match File::open(path) {
        Ok(f) => f,
        Err(e) => {
            rec.error = e.to_string();
            return rec;
        }
    };
    let mut archive = match ZipArchive::new(BufReader::new(file)) {
        Ok(a) => a,
        Err(e) => {
            rec.error = e.to_string();
            return rec;
        }
    };

    let mut manifest_bytes = Vec::new();
    match archive.by_name("AndroidManifest.xml") {
        Ok(mut mf) => {
            if let Err(e) = mf.read_to_end(&mut manifest_bytes) {
                rec.error = e.to_string();
                return rec;
            }
        }
        Err(e) => {
            rec.error = e.to_string();
            return rec;
        }
    }

    let Some(parsed) = parse_axml_manifest(&manifest_bytes) else {
        rec.error = "Invalid binary AndroidManifest.xml".to_string();
        return rec;
    };

    rec.package = parsed.package;
    rec.version_name = parsed.version_name;
    rec.version_code = parsed.version_code;
    rec.min_sdk = parsed.min_sdk;
    rec.target_sdk = parsed.target_sdk;
    rec.permissions = parsed.permissions;
    if !parsed.label.is_empty() {
        rec.name = parsed.label;
    } else if !rec.package.is_empty() && rec.name.is_empty() {
        rec.name = rec.package.clone();
    }

    if !parsed.icon_raw.is_empty() && is_raster_icon(&parsed.icon_raw) {
        rec.icon_entry = parsed.icon_raw.clone();
    }

    if parsed.label_res_id.is_some() || (parsed.icon_res_id.is_some() && rec.icon_entry.is_empty()) {
        let zip_names: HashSet<String> = (0..archive.len())
            .filter_map(|i| archive.name_for_index(i).map(|s| s.to_string()))
            .collect();
        let mut arsc_bytes = Vec::new();
        if let Ok(mut rf) = archive.by_name("resources.arsc") {
            let _ = rf.read_to_end(&mut arsc_bytes);
        }
        if !arsc_bytes.is_empty() {
            let resolved = resolve_arsc(
                &arsc_bytes,
                parsed.label_res_id,
                parsed.icon_res_id,
                &zip_names,
            );
            if let Some(lbl) = resolved.label {
                rec.name = lbl;
            }
            if rec.icon_entry.is_empty() {
                if let Some(ico) = resolved.icon_entry {
                    rec.icon_entry = ico;
                }
            }
        }
    }

    let (partition, category, risk) = classify_apk(&rel_path, &rec.package);
    rec.partition = partition;
    rec.category = category;
    rec.risk = risk;
    rec
}

fn collect_apk_paths(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(entries) = fs::read_dir(&dir) else {
            continue;
        };
        for entry in entries.flatten() {
            let Ok(ft) = entry.file_type() else {
                continue;
            };
            if ft.is_symlink() {
                continue;
            }
            let name = entry.file_name();
            let name_str = name.to_string_lossy();
            if ft.is_dir() {
                if name_str.starts_with('.')
                    || matches!(
                        name_str.as_ref(),
                        "config" | "neocat_config" | "neobranch" | "automation"
                    )
                {
                    continue;
                }
                stack.push(entry.path());
            } else if ft.is_file() && name_str.to_ascii_lowercase().ends_with(".apk") {
                out.push(entry.path());
            }
        }
    }
    out.sort();
    out
}

#[pyfunction]
pub fn scan_apks_json(py: Python<'_>, root: PathBuf) -> PyResult<String> {
    let records = py.detach(move || -> Vec<ApkRecord> {
        let paths = collect_apk_paths(&root);
        let mut recs: Vec<ApkRecord> = paths
            .par_iter()
            .map(|p| inspect_single_apk(&root, p))
            .collect();
        recs.sort_by(|a, b| a.rel_path.cmp(&b.rel_path));
        recs
    });
    serde_json::to_string(&records).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pyfunction]
pub fn read_apk_icon_bytes<'py>(
    py: Python<'py>,
    apk_path: PathBuf,
    entry_name: String,
) -> PyResult<Bound<'py, PyBytes>> {
    let bytes = py
        .detach(move || -> Result<Vec<u8>, String> {
            let file = File::open(&apk_path).map_err(|e| e.to_string())?;
            let mut archive = ZipArchive::new(BufReader::new(file)).map_err(|e| e.to_string())?;
            let mut entry = archive.by_name(&entry_name).map_err(|e| e.to_string())?;
            let mut buf = Vec::new();
            entry.read_to_end(&mut buf).map_err(|e| e.to_string())?;
            Ok(buf)
        })
        .map_err(PyOSError::new_err)?;
    Ok(PyBytes::new(py, &bytes))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use zip::write::SimpleFileOptions;
    use zip::ZipWriter;

    fn build_minimal_string_pool(strings: &[&str]) -> Vec<u8> {
        let mut pool_body = Vec::new();
        let mut offsets = Vec::new();
        for s in strings {
            offsets.push(pool_body.len() as u32);
            let bytes = s.as_bytes();
            pool_body.push(bytes.len() as u8);
            pool_body.push(bytes.len() as u8);
            pool_body.extend_from_slice(bytes);
            pool_body.push(0);
        }
        while pool_body.len() % 4 != 0 {
            pool_body.push(0);
        }
        let header_size = 28u16;
        let strings_start = header_size as u32 + (strings.len() as u32) * 4;
        let chunk_size = strings_start + pool_body.len() as u32;
        let mut out = Vec::new();
        out.extend_from_slice(&RES_STRING_POOL_TYPE.to_le_bytes());
        out.extend_from_slice(&header_size.to_le_bytes());
        out.extend_from_slice(&chunk_size.to_le_bytes());
        out.extend_from_slice(&(strings.len() as u32).to_le_bytes());
        out.extend_from_slice(&0u32.to_le_bytes()); // styleCount
        out.extend_from_slice(&UTF8_FLAG.to_le_bytes());
        out.extend_from_slice(&strings_start.to_le_bytes());
        out.extend_from_slice(&0u32.to_le_bytes()); // stylesStart
        for off in offsets {
            out.extend_from_slice(&off.to_le_bytes());
        }
        out.extend_from_slice(&pool_body);
        out
    }

    fn build_start_element(name_idx: i32, attrs: &[(i32, u8, u32)]) -> Vec<u8> {
        let mut out = Vec::new();
        let header_size = 16u16;
        let body_size = 20 + attrs.len() * 20;
        let total = header_size as usize + body_size;
        out.extend_from_slice(&RES_XML_START_ELEMENT_TYPE.to_le_bytes());
        out.extend_from_slice(&header_size.to_le_bytes());
        out.extend_from_slice(&(total as u32).to_le_bytes());
        out.extend_from_slice(&1u32.to_le_bytes()); // line
        out.extend_from_slice(&(-1i32).to_le_bytes()); // comment
        out.extend_from_slice(&(-1i32).to_le_bytes()); // ns
        out.extend_from_slice(&name_idx.to_le_bytes());
        out.extend_from_slice(&20u16.to_le_bytes()); // attr_start
        out.extend_from_slice(&20u16.to_le_bytes()); // attr_size
        out.extend_from_slice(&(attrs.len() as u16).to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        out.extend_from_slice(&0u16.to_le_bytes());
        for &(aname, dtype, dval) in attrs {
            out.extend_from_slice(&(-1i32).to_le_bytes()); // ns
            out.extend_from_slice(&aname.to_le_bytes());
            out.extend_from_slice(&(-1i32).to_le_bytes()); // raw
            out.extend_from_slice(&8u16.to_le_bytes());
            out.push(0);
            out.push(dtype);
            out.extend_from_slice(&dval.to_le_bytes());
        }
        out
    }

    #[test]
    fn parses_axml_manifest_and_scans_apk() {
        // String pool indices:
        // 0: manifest, 1: package, 2: com.example.bloat, 3: versionName, 4: 2.1.0,
        // 5: application, 6: label, 7: Sample Bloat, 8: uses-permission, 9: name,
        // 10: android.permission.INTERNET, 11: uses-sdk, 12: minSdkVersion, 13: targetSdkVersion
        let strings = [
            "manifest",
            "package",
            "com.example.bloat",
            "versionName",
            "2.1.0",
            "application",
            "label",
            "Sample Bloat",
            "uses-permission",
            "name",
            "android.permission.INTERNET",
            "uses-sdk",
            "minSdkVersion",
            "targetSdkVersion",
        ];
        let sp = build_minimal_string_pool(&strings);
        let elem_manifest = build_start_element(0, &[(1, TYPE_STRING, 2), (3, TYPE_STRING, 4)]);
        let elem_sdk = build_start_element(11, &[(12, TYPE_INT_DEC, 29), (13, TYPE_INT_DEC, 34)]);
        let elem_perm = build_start_element(8, &[(9, TYPE_STRING, 10)]);
        let elem_app = build_start_element(5, &[(6, TYPE_STRING, 7)]);

        let total = 8 + sp.len() + elem_manifest.len() + elem_sdk.len() + elem_perm.len() + elem_app.len();
        let mut axml = Vec::new();
        axml.extend_from_slice(&RES_XML_TYPE.to_le_bytes());
        axml.extend_from_slice(&8u16.to_le_bytes());
        axml.extend_from_slice(&(total as u32).to_le_bytes());
        axml.extend_from_slice(&sp);
        axml.extend_from_slice(&elem_manifest);
        axml.extend_from_slice(&elem_sdk);
        axml.extend_from_slice(&elem_perm);
        axml.extend_from_slice(&elem_app);

        let dir = std::env::temp_dir().join("rak_apk_unit_test");
        let app_dir = dir.join("product").join("app").join("SampleBloat");
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&app_dir).unwrap();
        let apk_path = app_dir.join("SampleBloat.apk");

        {
            let file = File::create(&apk_path).unwrap();
            let mut zw = ZipWriter::new(file);
            zw.start_file("AndroidManifest.xml", SimpleFileOptions::default())
                .unwrap();
            zw.write_all(&axml).unwrap();
            zw.finish().unwrap();
        }

        let rec = inspect_single_apk(&dir, &apk_path);
        assert_eq!(rec.error, "");
        assert_eq!(rec.package, "com.example.bloat");
        assert_eq!(rec.name, "Sample Bloat");
        assert_eq!(rec.version_name, "2.1.0");
        assert_eq!(rec.min_sdk, "29");
        assert_eq!(rec.target_sdk, "34");
        assert_eq!(rec.permissions, vec!["android.permission.INTERNET"]);
        assert_eq!(rec.partition, "product");
        assert_eq!(rec.category, "app");
        assert_eq!(rec.risk, "safe");
        let _ = fs::remove_dir_all(&dir);
    }
}
