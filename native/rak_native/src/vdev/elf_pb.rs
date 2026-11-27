// SPDX-License-Identifier: MPL-2.0
//! Fast batch ELF (DT_NEEDED / ABI / dynsym), range-seek APK+cert, parallel APEX,
//! DLKM .ko modinfo, and classpath protobuf scanners.

use crate::apk::parse_axml_manifest;
use crate::image_tools::{NativeErofsReader, NativeExtReader};
use crate::vdev::boot_avb::{KoModuleInfo, parse_ko_modinfo};
use goblin::elf::Elf;
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use rayon::prelude::*;
use serde::Serialize;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::io::{Cursor, Read, Seek, SeekFrom};
use std::path::PathBuf;
use zip::ZipArchive;

fn hex_lower(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        s.push(HEX[(b >> 4) as usize] as char);
        s.push(HEX[(b & 0xf) as usize] as char);
    }
    s
}

#[derive(Serialize)]
pub struct ElfDepRecord {
    pub path: String,
    pub ok: bool,
    pub is_64: bool,
    pub machine: u16,
    pub soname: String,
    pub interpreter: String,
    pub needed: Vec<String>,
    pub runpath: Vec<String>,
    pub rpath: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub defined_syms: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub undef_syms: Vec<String>,
}

fn parse_elf_bytes(path: &str, data: &[u8], with_symbols: bool) -> ElfDepRecord {
    let mut rec = ElfDepRecord {
        path: path.to_string(),
        ok: false,
        is_64: false,
        machine: 0,
        soname: String::new(),
        interpreter: String::new(),
        needed: Vec::new(),
        runpath: Vec::new(),
        rpath: Vec::new(),
        defined_syms: Vec::new(),
        undef_syms: Vec::new(),
    };
    if data.len() < 52 || &data[0..4] != b"\x7fELF" {
        return rec;
    }
    let Ok(elf) = Elf::parse(data) else {
        return rec;
    };
    rec.ok = true;
    rec.is_64 = elf.is_64;
    rec.machine = elf.header.e_machine;
    if let Some(interp) = elf.interpreter {
        rec.interpreter = interp.to_string();
    }
    if let Some(so) = elf.soname {
        rec.soname = so.to_string();
    }
    rec.needed = elf.libraries.iter().map(|s| (*s).to_string()).collect();
    rec.runpath = elf.runpaths.iter().map(|s| (*s).to_string()).collect();
    rec.rpath = elf.rpaths.iter().map(|s| (*s).to_string()).collect();

    if with_symbols {
        let mut def_seen = HashSet::new();
        let mut und_seen = HashSet::new();
        for sym in &elf.dynsyms {
            let bind = sym.st_bind();
            // STB_GLOBAL = 1, STB_WEAK = 2
            if bind != 1 && bind != 2 {
                continue;
            }
            let Some(name) = elf.dynstrtab.get_at(sym.st_name) else {
                continue;
            };
            if name.is_empty() {
                continue;
            }
            if sym.is_import() {
                // Only strong (STB_GLOBAL) undefined symbols are hard runtime requirements
                if bind == 1 && und_seen.insert(name) {
                    rec.undef_syms.push(name.to_string());
                }
            } else if def_seen.insert(name) {
                rec.defined_syms.push(name.to_string());
            }
        }
    }
    rec
}

#[pyfunction]
#[pyo3(signature = (paths, with_symbols=false))]
pub fn vdev_scan_elfs_json(
    py: Python<'_>,
    paths: Vec<PathBuf>,
    with_symbols: bool,
) -> PyResult<String> {
    let records = py.detach(move || -> Vec<ElfDepRecord> {
        paths
            .par_iter()
            .map(|p| {
                let s = p.to_string_lossy().into_owned();
                match std::fs::read(p) {
                    Ok(bytes) => parse_elf_bytes(&s, &bytes, with_symbols),
                    Err(_) => parse_elf_bytes(&s, &[], with_symbols),
                }
            })
            .collect()
    });
    serde_json::to_string(&records).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (reader, paths, with_symbols=false))]
pub fn vdev_scan_erofs_elfs_json(
    py: Python<'_>,
    reader: &NativeErofsReader,
    paths: Vec<String>,
    with_symbols: bool,
) -> PyResult<String> {
    let img = reader.get_image()?;
    let records = py.detach(move || -> Vec<ElfDepRecord> {
        paths
            .par_iter()
            .map(|vp| {
                let internal = vp.trim_start_matches('/');
                match img.read_path(internal) {
                    Ok((bytes, _node)) => parse_elf_bytes(vp, &bytes, with_symbols),
                    Err(_) => parse_elf_bytes(vp, &[], with_symbols),
                }
            })
            .collect()
    });
    serde_json::to_string(&records).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (reader, paths, with_symbols=false))]
pub fn vdev_scan_ext_elfs_json(
    py: Python<'_>,
    reader: &NativeExtReader,
    paths: Vec<String>,
    with_symbols: bool,
) -> PyResult<String> {
    let blobs: Vec<(String, Vec<u8>)> = py.detach(|| -> PyResult<Vec<(String, Vec<u8>)>> {
        reader.with_vol(|vol| {
            let mut out = Vec::with_capacity(paths.len());
            for vp in &paths {
                let internal = vp.trim_start_matches('/');
                let bytes = vol.read_path(internal).map(|(b, _)| b).unwrap_or_default();
                out.push((vp.clone(), bytes));
            }
            Ok(out)
        })
    })?;
    let records: Vec<ElfDepRecord> = py.detach(move || {
        blobs
            .par_iter()
            .map(|(vp, bytes)| parse_elf_bytes(vp, bytes, with_symbols))
            .collect()
    });
    serde_json::to_string(&records).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (items, with_symbols=false))]
pub fn vdev_scan_elf_bytes_json(
    py: Python<'_>,
    items: Vec<(String, Vec<u8>)>,
    with_symbols: bool,
) -> PyResult<String> {
    let records: Vec<ElfDepRecord> = py.detach(move || {
        items
            .par_iter()
            .map(|(vp, bytes)| parse_elf_bytes(vp, bytes, with_symbols))
            .collect()
    });
    serde_json::to_string(&records).map_err(|e| PyOSError::new_err(e.to_string()))
}

// ── DLKM .ko kernel module scanners ──

#[pyfunction]
pub fn vdev_scan_ko_files_json(py: Python<'_>, items: Vec<(String, PathBuf)>) -> PyResult<String> {
    let mods = py.detach(move || -> Vec<KoModuleInfo> {
        items
            .par_iter()
            .filter_map(|(vp, hp)| std::fs::read(hp).ok().map(|b| parse_ko_modinfo(vp, &b)))
            .collect()
    });
    serde_json::to_string(&mods).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
pub fn vdev_scan_erofs_kos_json(
    py: Python<'_>,
    reader: &NativeErofsReader,
    items: Vec<(String, String)>,
) -> PyResult<String> {
    let img = reader.get_image()?;
    let mods = py.detach(move || -> Vec<KoModuleInfo> {
        items
            .par_iter()
            .filter_map(|(vp, rel)| {
                img.read_path(rel.trim_start_matches('/'))
                    .ok()
                    .map(|(b, _)| parse_ko_modinfo(vp, &b))
            })
            .collect()
    });
    serde_json::to_string(&mods).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
pub fn vdev_scan_ext_kos_json(
    py: Python<'_>,
    reader: &NativeExtReader,
    items: Vec<(String, String)>,
) -> PyResult<String> {
    let blobs: Vec<(String, Vec<u8>)> = py.detach(|| {
        reader.with_vol(|vol| {
            let mut out = Vec::with_capacity(items.len());
            for (vp, rel) in &items {
                if let Ok((b, _)) = vol.read_path(rel.trim_start_matches('/')) {
                    out.push((vp.clone(), b));
                }
            }
            Ok(out)
        })
    })?;
    let mods: Vec<KoModuleInfo> = py.detach(move || {
        blobs
            .par_iter()
            .map(|(vp, b)| parse_ko_modinfo(vp, b))
            .collect()
    });
    serde_json::to_string(&mods).map_err(|e| PyOSError::new_err(e.to_string()))
}

// ── Fast range-seek APK + APK Signing Block v2/v3 cert extractor ──

const EOCD_MAGIC: &[u8; 4] = b"PK\x05\x06";
const APK_SIG_BLOCK_MAGIC: &[u8; 16] = b"APK Sig Block 42";
const SIG_V2_ID: u32 = 0x7109_871a;
const SIG_V3_ID: u32 = 0xf053_68c0;
const SIG_V31_ID: u32 = 0x1b93_ad61;

fn r_u32_le(b: &[u8], off: usize) -> Option<u32> {
    Some(u32::from_le_bytes(b.get(off..off + 4)?.try_into().ok()?))
}

fn r_u64_le(b: &[u8], off: usize) -> Option<u64> {
    Some(u64::from_le_bytes(b.get(off..off + 8)?.try_into().ok()?))
}

fn scan_sig_pairs(body: &[u8]) -> Option<String> {
    let mut pos = 0usize;
    let end = body.len();
    let mut value_off = None;
    while pos + 12 <= end {
        let pair_len = r_u64_le(body, pos)? as usize;
        let pair_id = r_u32_le(body, pos + 8)?;
        if matches!(pair_id, SIG_V2_ID | SIG_V3_ID | SIG_V31_ID) {
            value_off = Some(pos + 12);
            break;
        }
        if pair_len == 0 {
            return None;
        }
        pos = pos.checked_add(8)?.checked_add(pair_len)?;
    }
    let mut p = value_off?;
    p = p.checked_add(12)?; // signers len + first signer len + signed data len
    let digests_len = r_u32_le(body, p)? as usize;
    p = p.checked_add(4)?.checked_add(digests_len)?;
    p = p.checked_add(4)?; // certs sequence len
    let cert_len = r_u32_le(body, p)? as usize;
    p = p.checked_add(4)?;
    let der = body.get(p..p.checked_add(cert_len)?)?;
    if der.is_empty() {
        return None;
    }
    Some(hex_lower(&Sha256::digest(der)))
}

fn extract_apk_cert_v2_seek<R: Read + Seek>(reader: &mut R) -> Option<String> {
    let size = reader.seek(SeekFrom::End(0)).ok()?;
    if size < 32 {
        return None;
    }
    let tail_len = size.min(65557) as usize;
    reader.seek(SeekFrom::Start(size - tail_len as u64)).ok()?;
    let mut tail = vec![0u8; tail_len];
    reader.read_exact(&mut tail).ok()?;
    let eocd_idx = tail.windows(4).rposition(|w| w == EOCD_MAGIC)?;
    let cd_offset = r_u32_le(&tail, eocd_idx + 16)? as u64;
    if cd_offset < 24 || cd_offset > size {
        return None;
    }
    reader.seek(SeekFrom::Start(cd_offset - 24)).ok()?;
    let mut footer = [0u8; 24];
    reader.read_exact(&mut footer).ok()?;
    if &footer[8..24] != APK_SIG_BLOCK_MAGIC {
        return None;
    }
    let block_size = r_u64_le(&footer, 0)?;
    if block_size == 0 || block_size > 16 * 1024 * 1024 {
        return None;
    }
    let start = cd_offset.checked_sub(8)?.checked_sub(block_size)?;
    reader.seek(SeekFrom::Start(start)).ok()?;
    let total = (block_size + 8) as usize;
    let mut data = vec![0u8; total];
    reader.read_exact(&mut data).ok()?;
    if data.len() < 32 {
        return None;
    }
    scan_sig_pairs(&data[8..data.len() - 24])
}

#[derive(Serialize)]
pub struct VdevApkRecord {
    pub vpath: String,
    pub package: String,
    pub shared_user_id: String,
    pub permissions: Vec<String>,
    pub uses_libraries: Vec<(String, bool)>,
    pub overlay_target: String,
    pub is_launcher: bool,
    pub declared_permissions: Vec<(String, u32)>,
    pub components: Vec<(String, String)>,
    pub cert_sha256: String,
    pub error: String,
}

fn empty_apk_err(vpath: &str, err: String) -> VdevApkRecord {
    VdevApkRecord {
        vpath: vpath.to_string(),
        package: String::new(),
        shared_user_id: String::new(),
        permissions: Vec::new(),
        uses_libraries: Vec::new(),
        overlay_target: String::new(),
        is_launcher: false,
        declared_permissions: Vec::new(),
        components: Vec::new(),
        cert_sha256: String::new(),
        error: err,
    }
}

fn parse_apk_seekable<R: Read + Seek>(vpath: &str, mut reader: R) -> VdevApkRecord {
    let cert_sha256 = extract_apk_cert_v2_seek(&mut reader).unwrap_or_default();
    let _ = reader.seek(SeekFrom::Start(0));
    let mut arc = match ZipArchive::new(reader) {
        Ok(a) => a,
        Err(e) => return empty_apk_err(vpath, e.to_string()),
    };
    let mut manifest_bytes = Vec::new();
    match arc.by_name("AndroidManifest.xml") {
        Ok(mut f) => {
            if let Err(e) = f.read_to_end(&mut manifest_bytes) {
                return empty_apk_err(vpath, e.to_string());
            }
        }
        Err(_) => return empty_apk_err(vpath, "no AndroidManifest.xml".into()),
    }
    let Some(parsed) = parse_axml_manifest(&manifest_bytes) else {
        return empty_apk_err(vpath, "AndroidManifest is not AXML".into());
    };
    VdevApkRecord {
        vpath: vpath.to_string(),
        package: parsed.package,
        shared_user_id: parsed.shared_user_id,
        permissions: parsed.permissions,
        uses_libraries: parsed.uses_libraries,
        overlay_target: parsed.overlay_target,
        is_launcher: parsed.is_launcher,
        declared_permissions: parsed.declared_permissions,
        components: parsed.components,
        cert_sha256,
        error: String::new(),
    }
}

#[pyfunction]
pub fn vdev_scan_apk_files_json(
    py: Python<'_>,
    items: Vec<(String, PathBuf)>,
) -> PyResult<String> {
    let records = py.detach(move || -> Vec<VdevApkRecord> {
        items
            .par_iter()
            .map(|(vp, path)| match std::fs::File::open(path) {
                Ok(f) => parse_apk_seekable(vp, std::io::BufReader::new(f)),
                Err(e) => empty_apk_err(vp, e.to_string()),
            })
            .collect()
    });
    serde_json::to_string(&records).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
pub fn vdev_scan_erofs_apks_json(
    py: Python<'_>,
    reader: &NativeErofsReader,
    items: Vec<(String, String)>,
) -> PyResult<String> {
    let img = reader.get_image()?;
    let records = py.detach(move || -> Vec<VdevApkRecord> {
        items
            .par_iter()
            .map(|(vp, internal)| {
                let clean = internal.trim_start_matches('/');
                match img.open_path_reader(clean) {
                    Ok(freader) => parse_apk_seekable(vp, freader),
                    Err(e) => empty_apk_err(vp, e.to_string()),
                }
            })
            .collect()
    });
    serde_json::to_string(&records).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
pub fn vdev_scan_ext_apks_json(
    py: Python<'_>,
    reader: &NativeExtReader,
    items: Vec<(String, String)>,
) -> PyResult<String> {
    let records: Vec<VdevApkRecord> = py.detach(|| {
        reader.with_vol(|vol| {
            let mut out = Vec::with_capacity(items.len());
            for (vp, internal) in &items {
                let rec = match vol.open_path_reader(internal.trim_start_matches('/')) {
                    Ok(rdr) => parse_apk_seekable(vp, rdr),
                    Err(e) => empty_apk_err(vp, e.to_string()),
                };
                out.push(rec);
            }
            Ok(out)
        })
    })?;
    serde_json::to_string(&records).map_err(|e| PyOSError::new_err(e.to_string()))
}

// ── Parallel Rust APEX Indexer ──

#[derive(Serialize)]
pub struct ClasspathJar {
    pub path: String,
    pub min_sdk_version: String,
    pub max_sdk_version: String,
    pub classpath: String,
}

#[derive(Serialize)]
pub struct ApexEntrySummary {
    pub vpath: String,
    pub stem: String,
    pub pkg_name: String,
    pub version: Option<u64>,
    pub files: Vec<String>,
    pub libs: Vec<String>,
    pub public_libs: Vec<String>,
    pub classpath_jars: Vec<ClasspathJar>,
    pub error: String,
}

fn parse_apex_manifest_pb(raw: &[u8]) -> (String, Option<u64>) {
    let mut name = String::new();
    let mut version = None;
    let mut pos = 0usize;
    while pos < raw.len() {
        let Some(tag) = read_varint(raw, &mut pos) else {
            break;
        };
        let field = tag >> 3;
        let wire = tag & 7;
        match (field, wire) {
            (1, 2) => {
                if let Some(len) = read_varint(raw, &mut pos) {
                    let l = len as usize;
                    if let Some(s) = raw.get(pos..pos + l) {
                        name = String::from_utf8_lossy(s).into_owned();
                        pos += l;
                    } else {
                        break;
                    }
                } else {
                    break;
                }
            }
            (2, 0) => {
                version = read_varint(raw, &mut pos);
            }
            _ => {
                if skip_field(raw, &mut pos, wire).is_none() {
                    break;
                }
            }
        }
    }
    (name, version)
}

fn detect_payload_fs(head: &[u8]) -> &'static str {
    for off in [0usize, 512, 1024, 4096] {
        if let Some(c) = head.get(off..off + 4) {
            if u32::from_le_bytes([c[0], c[1], c[2], c[3]]) == 0xE0F5_E1E2 {
                return "EROFS";
            }
        }
    }
    for sb in [1024usize, 0] {
        if let Some(c) = head.get(sb + 0x38..sb + 0x3A) {
            if u16::from_le_bytes([c[0], c[1]]) == 0xEF53 {
                return "EXT";
            }
        }
    }
    ""
}

fn index_apex_payload(payload: Vec<u8>, summary: &mut ApexEntrySummary) {
    let fs = detect_payload_fs(&payload[..payload.len().min(8192)]);
    let mut public_seen = HashSet::new();
    let mut lib_seen = HashSet::new();

    if fs == "EROFS" {
        let Ok(img) = erofs_rs::reader::ErofsImage::open_bytes(payload) else {
            summary.error = "erofs apex_payload unreadable".into();
            return;
        };
        let Ok(nodes) = img.walk(false) else {
            summary.error = "erofs apex_payload walk failed".into();
            return;
        };
        for n in nodes {
            let rel = n.path.trim_start_matches('/').to_string();
            if rel.is_empty() {
                continue;
            }
            let base = rel.rsplit('/').next().unwrap_or("");
            if rel.ends_with(".so") && lib_seen.insert(base.to_string()) {
                summary.libs.push(base.to_string());
            } else if (base.starts_with("llndk.libraries") || base.starts_with("vndksp.libraries"))
                && base.ends_with(".txt")
            {
                if let Ok((raw, _)) = img.read_path(&rel) {
                    for line in String::from_utf8_lossy(&raw).lines() {
                        let tok = line.split('#').next().unwrap_or("").trim();
                        if tok.ends_with(".so") && public_seen.insert(tok.to_string()) {
                            summary.public_libs.push(tok.to_string());
                        }
                    }
                }
            } else if rel == "etc/classpaths/bootclasspath.pb"
                || rel == "etc/classpaths/systemserverclasspath.pb"
            {
                if let Ok((raw, _)) = img.read_path(&rel) {
                    if let Ok(jars) = decode_classpath_pb(&raw) {
                        summary.classpath_jars.extend(jars);
                    }
                }
            }
            summary.files.push(rel);
        }
    } else if fs == "EXT" {
        let Ok(mut vol) = ext_cli::extfs::Volume::open_bytes(payload) else {
            summary.error = "ext apex_payload unreadable".into();
            return;
        };
        let Ok(nodes) = vol.walk_nodes() else {
            summary.error = "ext apex_payload walk failed".into();
            return;
        };
        for n in nodes {
            let rel = n.path.trim_start_matches('/').to_string();
            if rel.is_empty() {
                continue;
            }
            let base = rel.rsplit('/').next().unwrap_or("");
            if rel.ends_with(".so") && lib_seen.insert(base.to_string()) {
                summary.libs.push(base.to_string());
            } else if (base.starts_with("llndk.libraries") || base.starts_with("vndksp.libraries"))
                && base.ends_with(".txt")
            {
                if let Ok((raw, _)) = vol.read_path(&rel) {
                    for line in String::from_utf8_lossy(&raw).lines() {
                        let tok = line.split('#').next().unwrap_or("").trim();
                        if tok.ends_with(".so") && public_seen.insert(tok.to_string()) {
                            summary.public_libs.push(tok.to_string());
                        }
                    }
                }
            } else if rel == "etc/classpaths/bootclasspath.pb"
                || rel == "etc/classpaths/systemserverclasspath.pb"
            {
                if let Ok((raw, _)) = vol.read_path(&rel) {
                    if let Ok(jars) = decode_classpath_pb(&raw) {
                        summary.classpath_jars.extend(jars);
                    }
                }
            }
            summary.files.push(rel);
        }
    } else {
        summary.error = format!("unknown payload fs ({})", if fs.is_empty() { "?" } else { fs });
    }
}

fn index_apex_zip<R: Read + Seek>(vpath: &str, reader: R, depth: u8) -> ApexEntrySummary {
    let fname = vpath.rsplit('/').next().unwrap_or(vpath);
    let stem = fname.rsplit_once('.').map(|(a, _)| a).unwrap_or(fname).to_string();
    let mut summary = ApexEntrySummary {
        vpath: vpath.to_string(),
        stem: stem.clone(),
        pkg_name: String::new(),
        version: None,
        files: Vec::new(),
        libs: Vec::new(),
        public_libs: Vec::new(),
        classpath_jars: Vec::new(),
        error: String::new(),
    };
    let mut arc = match ZipArchive::new(reader) {
        Ok(a) => a,
        Err(e) => {
            summary.error = e.to_string();
            return summary;
        }
    };
    if let Ok(mut mf) = arc.by_name("apex_manifest.pb") {
        let mut raw = Vec::new();
        if mf.read_to_end(&mut raw).is_ok() {
            let (pkg, ver) = parse_apex_manifest_pb(&raw);
            summary.pkg_name = pkg;
            summary.version = ver;
        }
    }
    if let Ok(mut pf) = arc.by_name("apex_payload.img") {
        let mut payload = Vec::with_capacity(pf.size().min(128 * 1024 * 1024) as usize);
        if let Err(e) = pf.read_to_end(&mut payload) {
            summary.error = e.to_string();
            return summary;
        }
        index_apex_payload(payload, &mut summary);
        return summary;
    }
    if depth < 3 {
        if let Ok(mut orig) = arc.by_name("original_apex") {
            let mut inner = Vec::with_capacity(orig.size().min(128 * 1024 * 1024) as usize);
            if orig.read_to_end(&mut inner).is_ok() {
                let mut inner_sum = index_apex_zip(vpath, Cursor::new(inner), depth + 1);
                if inner_sum.pkg_name.is_empty() {
                    inner_sum.pkg_name = summary.pkg_name;
                }
                if inner_sum.version.is_none() {
                    inner_sum.version = summary.version;
                }
                return inner_sum;
            }
        }
    }
    summary.error = "no apex_payload.img".into();
    summary
}

#[pyfunction]
pub fn vdev_index_erofs_apexes_json(
    py: Python<'_>,
    reader: &NativeErofsReader,
    items: Vec<(String, String)>,
) -> PyResult<String> {
    let img = reader.get_image()?;
    let out = py.detach(move || -> Vec<ApexEntrySummary> {
        items
            .par_iter()
            .map(|(vp, rel)| match img.open_path_reader(rel.trim_start_matches('/')) {
                Ok(fr) => index_apex_zip(vp, fr, 0),
                Err(e) => ApexEntrySummary {
                    vpath: vp.clone(),
                    stem: String::new(),
                    pkg_name: String::new(),
                    version: None,
                    files: Vec::new(),
                    libs: Vec::new(),
                    public_libs: Vec::new(),
                    classpath_jars: Vec::new(),
                    error: e.to_string(),
                },
            })
            .collect()
    });
    serde_json::to_string(&out).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
pub fn vdev_index_ext_apexes_json(
    py: Python<'_>,
    reader: &NativeExtReader,
    items: Vec<(String, String)>,
) -> PyResult<String> {
    let blobs: Vec<(String, Result<Vec<u8>, String>)> = py.detach(|| {
        reader.with_vol(|vol| {
            let mut out = Vec::with_capacity(items.len());
            for (vp, rel) in &items {
                out.push((
                    vp.clone(),
                    vol.read_path(rel.trim_start_matches('/'))
                        .map(|(b, _)| b)
                        .map_err(|e| e.to_string()),
                ));
            }
            Ok(out)
        })
    })?;
    let out: Vec<ApexEntrySummary> = py.detach(move || {
        blobs
            .par_iter()
            .map(|(vp, res)| match res {
                Ok(bytes) => index_apex_zip(vp, Cursor::new(bytes.as_slice()), 0),
                Err(e) => ApexEntrySummary {
                    vpath: vp.clone(),
                    stem: String::new(),
                    pkg_name: String::new(),
                    version: None,
                    files: Vec::new(),
                    libs: Vec::new(),
                    public_libs: Vec::new(),
                    classpath_jars: Vec::new(),
                    error: e.clone(),
                },
            })
            .collect()
    });
    serde_json::to_string(&out).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
pub fn vdev_index_file_apexes_json(
    py: Python<'_>,
    items: Vec<(String, PathBuf)>,
) -> PyResult<String> {
    let out = py.detach(move || -> Vec<ApexEntrySummary> {
        items
            .par_iter()
            .map(|(vp, hp)| match std::fs::File::open(hp) {
                Ok(f) => index_apex_zip(vp, std::io::BufReader::new(f), 0),
                Err(e) => ApexEntrySummary {
                    vpath: vp.clone(),
                    stem: String::new(),
                    pkg_name: String::new(),
                    version: None,
                    files: Vec::new(),
                    libs: Vec::new(),
                    public_libs: Vec::new(),
                    classpath_jars: Vec::new(),
                    error: e.to_string(),
                },
            })
            .collect()
    });
    serde_json::to_string(&out).map_err(|e| PyOSError::new_err(e.to_string()))
}

// ── Classpath protobuf decoder ──

fn read_varint(buf: &[u8], pos: &mut usize) -> Option<u64> {
    let mut val = 0u64;
    let mut shift = 0u32;
    while *pos < buf.len() {
        let b = buf[*pos];
        *pos += 1;
        val |= ((b & 0x7f) as u64) << shift;
        if (b & 0x80) == 0 {
            return Some(val);
        }
        shift += 7;
        if shift >= 64 {
            return None;
        }
    }
    None
}

fn skip_field(buf: &[u8], pos: &mut usize, wire: u64) -> Option<()> {
    match wire {
        0 => {
            read_varint(buf, pos)?;
        }
        1 => {
            *pos = pos.checked_add(8)?;
        }
        2 => {
            let len = read_varint(buf, pos)? as usize;
            *pos = pos.checked_add(len)?;
        }
        5 => {
            *pos = pos.checked_add(4)?;
        }
        _ => return None,
    }
    if *pos <= buf.len() { Some(()) } else { None }
}

fn decode_jar_msg(buf: &[u8]) -> Option<ClasspathJar> {
    let mut jar = ClasspathJar {
        path: String::new(),
        min_sdk_version: String::new(),
        max_sdk_version: String::new(),
        classpath: "UNKNOWN".into(),
    };
    let mut pos = 0usize;
    while pos < buf.len() {
        let tag = read_varint(buf, &mut pos)?;
        let field = tag >> 3;
        let wire = tag & 7;
        match (field, wire) {
            (1, 2) => {
                let len = read_varint(buf, &mut pos)? as usize;
                let s = buf.get(pos..pos + len)?;
                pos += len;
                jar.path = String::from_utf8_lossy(s).into_owned();
            }
            (2, 2) => {
                let len = read_varint(buf, &mut pos)? as usize;
                let s = buf.get(pos..pos + len)?;
                pos += len;
                jar.min_sdk_version = String::from_utf8_lossy(s).into_owned();
            }
            (3, 2) => {
                let len = read_varint(buf, &mut pos)? as usize;
                let s = buf.get(pos..pos + len)?;
                pos += len;
                jar.max_sdk_version = String::from_utf8_lossy(s).into_owned();
            }
            (4, 0) => {
                let v = read_varint(buf, &mut pos)?;
                jar.classpath = match v {
                    1 => "BOOTCLASSPATH",
                    2 => "DEX2OATBOOTCLASSPATH",
                    3 => "SYSTEMSERVERCLASSPATH",
                    4 => "STANDALONE_SYSTEMSERVER_JARS",
                    _ => "UNKNOWN",
                }
                .into();
            }
            _ => skip_field(buf, &mut pos, wire)?,
        }
    }
    Some(jar)
}

pub(crate) fn decode_classpath_pb(owned: &[u8]) -> Result<Vec<ClasspathJar>, String> {
    let mut out = Vec::new();
    let mut pos = 0usize;
    while pos < owned.len() {
        let tag = read_varint(owned, &mut pos)
            .ok_or_else(|| "truncated protobuf tag".to_string())?;
        let field = tag >> 3;
        let wire = tag & 7;
        if field == 1 && wire == 2 {
            let len = read_varint(owned, &mut pos)
                .ok_or_else(|| "truncated protobuf len".to_string())? as usize;
            let slice = owned
                .get(pos..pos + len)
                .ok_or_else(|| "truncated Jar message".to_string())?;
            pos += len;
            if let Some(jar) = decode_jar_msg(slice) {
                if !jar.path.is_empty() {
                    out.push(jar);
                }
            }
        } else {
            skip_field(owned, &mut pos, wire)
                .ok_or_else(|| "invalid protobuf wire field".to_string())?;
        }
    }
    Ok(out)
}

#[pyfunction]
pub fn vdev_parse_classpath_pb_json(py: Python<'_>, data: &[u8]) -> PyResult<String> {
    let owned = data.to_vec();
    let jars = py
        .detach(move || decode_classpath_pb(&owned))
        .map_err(PyValueError::new_err)?;
    serde_json::to_string(&jars).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[derive(Serialize)]
pub struct VfsQuickWalkEntry {
    pub path: String,
    pub is_dir: bool,
    pub is_link: bool,
    pub size: u64,
    pub target: Option<String>,
    pub selinux: Option<String>,
}

#[pyfunction]
pub fn vdev_walk_erofs_json(py: Python<'_>, reader: &NativeErofsReader) -> PyResult<String> {
    let img = reader.get_image()?;
    let rows = py
        .detach(move || -> Result<Vec<VfsQuickWalkEntry>, String> {
            let nodes = img.walk(false).map_err(|e| e.to_string())?;
            Ok(nodes
                .into_iter()
                .map(|n| VfsQuickWalkEntry {
                    path: n.path,
                    is_dir: n.is_dir,
                    is_link: n.is_link,
                    size: n.size,
                    target: n.target,
                    selinux: n.selinux,
                })
                .collect())
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&rows).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
pub fn vdev_walk_ext_json(py: Python<'_>, reader: &NativeExtReader) -> PyResult<String> {
    let rows = py.detach(move || -> PyResult<Vec<VfsQuickWalkEntry>> {
        reader.with_vol(|vol| {
            let nodes = vol.walk_nodes().map_err(|e| e.to_string())?;
            Ok(nodes
                .into_iter()
                .filter(|n| !n.path.is_empty() && n.path != "/")
                .map(|n| VfsQuickWalkEntry {
                    path: n.path,
                    is_dir: n.is_dir,
                    is_link: n.is_link,
                    size: n.size,
                    target: n.target,
                    selinux: n.selinux,
                })
                .collect())
        })
    })?;
    serde_json::to_string(&rows).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
pub fn vdev_read_erofs_paths_json(
    py: Python<'_>,
    reader: &NativeErofsReader,
    paths: Vec<String>,
) -> PyResult<HashMap<String, Vec<u8>>> {
    let img = reader.get_image()?;
    let map = py.detach(move || -> HashMap<String, Vec<u8>> {
        paths
            .par_iter()
            .filter_map(|vp| {
                img.read_path(vp.trim_start_matches('/'))
                    .ok()
                    .map(|(b, _)| (vp.clone(), b))
            })
            .collect()
    });
    Ok(map)
}
