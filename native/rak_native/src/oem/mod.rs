// SPDX-License-Identifier: MPL-2.0
//! Clean-room OEM firmware container and embedded filesystem detection, inspection, and extraction.

pub mod containers;
pub mod crypto_fw;
pub mod filesystems;

use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use serde::Serialize;
use std::collections::{HashMap, HashSet};
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Component, Path, PathBuf};

#[derive(Serialize)]
pub struct OemEntry {
    pub name: String,
    pub size: u64,
    pub offset: u64,
    pub kind: String,
    pub extra: HashMap<String, String>,
}

#[derive(Serialize)]
pub struct OemInspectResult {
    pub kind: String,
    pub metadata: HashMap<String, String>,
    pub entries: Vec<OemEntry>,
}

#[derive(Serialize)]
pub struct OemExtractResult {
    pub ok: bool,
    pub kind: String,
    pub extracted: Vec<String>,
}

/// Reject absolute paths, drive prefixes, NUL bytes, and parent-directory traversal.
pub fn safe_join_relative(out_dir: &Path, relative: &str) -> Result<PathBuf, String> {
    if relative.is_empty() || relative.contains('\0') {
        return Err("Empty or invalid archive entry path".to_string());
    }
    let normalized = relative.replace('\\', "/");
    if normalized.starts_with('/') {
        return Err(format!("Absolute entry path rejected: {relative}"));
    }
    let rel_path = Path::new(&normalized);
    let mut clean = PathBuf::new();
    for comp in rel_path.components() {
        match comp {
            Component::Normal(part) => {
                let s = part.to_string_lossy();
                if s.contains(':') {
                    return Err(format!("Drive-qualified entry path rejected: {relative}"));
                }
                clean.push(part);
            }
            Component::CurDir => {}
            _ => return Err(format!("Unsafe path traversal rejected: {relative}")),
        }
    }
    if clean.as_os_str().is_empty() {
        return Err(format!("Empty resolved path rejected: {relative}"));
    }
    Ok(out_dir.join(clean))
}

#[inline]
fn le_u32(b: &[u8], o: usize) -> Option<u32> {
    let s: [u8; 4] = b.get(o..o + 4)?.try_into().ok()?;
    Some(u32::from_le_bytes(s))
}

#[inline]
fn le_u16(b: &[u8], o: usize) -> Option<u16> {
    let s: [u8; 2] = b.get(o..o + 2)?.try_into().ok()?;
    Some(u16::from_le_bytes(s))
}

pub fn detect_oem_magic_bytes(data: &[u8], ext_lower: &str) -> &'static str {
    if data.len() < 4 {
        return "";
    }
    if data.starts_with(b"OPPOENCRYPT!") {
        return "OZIP";
    }
    if data.starts_with(b"IMAGEWTY") {
        return "ALLWINNER";
    }
    if data.starts_with(b"-rom1fs-") {
        return "ROMFS";
    }
    if data.starts_with(b"hsqs") || data.starts_with(b"sqsh") {
        return "SQUASHFS";
    }
    if data.starts_with(b"AVB0") {
        return "VBMETA";
    }
    if data.len() >= 8
        && (&data[..8] == b"\x28\x05\x00\x00$8\"%"
            || &data[..8] == b"\x28\x05\x00\x0041%\x80"
            || &data[..8] == b"\x18\x05\x00\x002yDP")
    {
        return "KDZ";
    }
    if &data[..4] == b"\x32\x96\x18\x74" {
        return "DZ";
    }
    if data.len() >= 12 && le_u32(data, 8) == Some(0x27B5_1956) {
        return "AMLOGIC";
    }
    if data.len() >= 2120 && le_u32(data, 2116) == Some(0xFFFA_FFFA) {
        return "PAC";
    }
    // Older BP_R1.0.0 PAC images may omit the 0xFFFAFFFA magic but still carry UTF-16LE version header.
    if data.len() >= 44
        && (data.starts_with(b"B\x00P\x00_\x00R\x001\x00.\x000\x00.\x000\x00\x00\x00")
            || data.starts_with(b"B\x00P\x00_\x00R\x002\x00.\x000\x00.\x001\x00\x00\x00"))
    {
        return "PAC";
    }
    if data.starts_with(b"CP\x01\x07")
        || (data.starts_with(b"CP")
            && data.len() >= 420
            && le_u32(data, 416).map(|v| v >= 552).unwrap_or(false))
    {
        return "CPB";
    }
    if data.len() >= 1028 && le_u32(data, 1024) == Some(0xF2F5_2010) {
        return "F2FS";
    }
    // Huawei UPDATE.APP commonly has 92 zero bytes followed by 55 AA 5A A5.
    if data.windows(4).take(4096).any(|w| w == [0x55, 0xAA, 0x5A, 0xA5]) {
        return "UPDATE_APP";
    }
    if ext_lower == "ofp" && data.starts_with(b"PK") {
        return "OFP";
    }
    if let Some(0xEF53) = le_u16(data, 1024 + 0x38) {
        let _ = ext_lower;
        return "";
    }
    ""
}

pub fn detect_oem_magic_file(path: &Path) -> String {
    let ext_lower = path
        .extension()
        .and_then(|s| s.to_str())
        .map(|s| s.to_ascii_lowercase())
        .unwrap_or_default();
    let Ok(mut f) = File::open(path) else {
        return String::new();
    };
    let Ok(meta) = f.metadata() else {
        return String::new();
    };
    let file_len = meta.len();
    let mut head = vec![0u8; (file_len as usize).min(65536)];
    if f.read_exact(&mut head).is_err() {
        return String::new();
    }
    let kind = detect_oem_magic_bytes(&head, &ext_lower);
    if !kind.is_empty() {
        return kind.to_string();
    }

    // Check OFP / OPS footer (0x7CEF magic at tail page).
    if matches!(ext_lower.as_str(), "ofp" | "ops" | "") && file_len >= 0x200 {
        for &pagesize in &[0x200u64, 0x1000u64] {
            if file_len >= pagesize && f.seek(SeekFrom::Start(file_len - pagesize)).is_ok() {
                let mut tail = [0u8; 0x20];
                if f.read_exact(&mut tail).is_ok() && le_u32(&tail, 0x10) == Some(0x7CEF) {
                    return if ext_lower == "ops" {
                        "OPS".to_string()
                    } else {
                        "OFP".to_string()
                    };
                }
            }
        }
        if ext_lower == "ofp" && crypto_fw::inspect_ofp(path).is_ok() {
            return "OFP".to_string();
        }
    }

    String::new()
}

pub fn inspect_oem_path(path: &Path) -> Result<OemInspectResult, String> {
    let kind = detect_oem_magic_file(path);
    match kind.as_str() {
        "UPDATE_APP" => containers::inspect_update_app(path),
        "PAC" => containers::inspect_pac(path),
        "KDZ" => containers::inspect_kdz(path),
        "DZ" => containers::inspect_dz(path),
        "CPB" => containers::inspect_cpb(path),
        "AMLOGIC" => containers::inspect_amlogic(path),
        "ALLWINNER" => containers::inspect_allwinner(path),
        "ROMFS" => filesystems::inspect_romfs(path),
        "SQUASHFS" => filesystems::inspect_squashfs(path),
        "OZIP" => crypto_fw::inspect_ozip(path),
        "OFP" => crypto_fw::inspect_ofp(path),
        "OPS" => crypto_fw::inspect_ops(path),
        "" => Err("Unsupported or unrecognized OEM/embedded format".to_string()),
        other => Err(format!("{other} inspection is handled by filesystem tools")),
    }
}

pub fn extract_oem_path(
    path: &Path,
    out_dir: &Path,
    selected: Option<Vec<String>>,
) -> Result<OemExtractResult, String> {
    let kind = detect_oem_magic_file(path);
    let sel_set: Option<HashSet<String>> = selected
        .filter(|v| !v.is_empty())
        .map(|v| v.into_iter().collect());
    let sel_ref = sel_set.as_ref();

    let extracted = match kind.as_str() {
        "UPDATE_APP" => containers::extract_update_app(path, out_dir, sel_ref)?,
        "PAC" => containers::extract_pac(path, out_dir, sel_ref)?,
        "KDZ" => containers::extract_kdz(path, out_dir, sel_ref)?,
        "DZ" => containers::extract_dz(path, out_dir, sel_ref)?,
        "CPB" => containers::extract_cpb(path, out_dir, sel_ref)?,
        "AMLOGIC" => containers::extract_amlogic(path, out_dir, sel_ref)?,
        "ALLWINNER" => containers::extract_allwinner(path, out_dir, sel_ref)?,
        "ROMFS" => filesystems::extract_romfs(path, out_dir, sel_ref)?,
        "SQUASHFS" => filesystems::extract_squashfs(path, out_dir, sel_ref)?,
        "OZIP" => crypto_fw::extract_ozip(path, out_dir)?,
        "OFP" => crypto_fw::extract_ofp(path, out_dir, sel_ref)?,
        "OPS" => crypto_fw::extract_ops(path, out_dir, sel_ref)?,
        "" => return Err("Unsupported or unrecognized OEM/embedded format".to_string()),
        other => return Err(format!("{other} extraction requires external filesystem tools")),
    };

    Ok(OemExtractResult {
        ok: true,
        kind,
        extracted,
    })
}

#[pyfunction]
pub fn oem_detect_magic(py: Python<'_>, path: PathBuf) -> PyResult<String> {
    Ok(py.detach(move || detect_oem_magic_file(&path)))
}

#[pyfunction]
pub fn oem_inspect_json(py: Python<'_>, path: PathBuf) -> PyResult<String> {
    let res = py
        .detach(move || inspect_oem_path(&path))
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&res).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (path, out_dir, selected=None))]
pub fn oem_extract_json(
    py: Python<'_>,
    path: PathBuf,
    out_dir: PathBuf,
    selected: Option<Vec<String>>,
) -> PyResult<String> {
    let res = py
        .detach(move || extract_oem_path(&path, &out_dir, selected))
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&res).map_err(|e| PyValueError::new_err(e.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use aes::cipher::{block_padding::NoPadding, BlockEncryptMut, KeyInit};
    use flate2::write::ZlibEncoder;
    use flate2::Compression;
    use md5::Digest;
    use std::fs;
    use std::io::Write;

    #[test]
    fn rejects_path_traversal_entries() {
        let base = Path::new("out");
        assert!(safe_join_relative(base, "../evil.img").is_err());
        assert!(safe_join_relative(base, "/etc/passwd").is_err());
        assert!(safe_join_relative(base, "C:\\Windows\\win.ini").is_err());
        assert_eq!(
            safe_join_relative(base, "sub/boot.img").unwrap(),
            PathBuf::from("out/sub/boot.img")
        );
    }

    #[test]
    fn roundtrip_update_app_and_romfs_and_ozip_and_dz() {
        let root = std::env::temp_dir().join("rak_oem_unit_test");
        let _ = fs::remove_dir_all(&root);
        fs::create_dir_all(&root).unwrap();

        // 1. Synthetic UPDATE.APP
        let uapp_path = root.join("UPDATE.APP");
        {
            let payload = b"HELLO_BOOT_IMAGE";
            let mut hdr = vec![0u8; 98];
            hdr[0..4].copy_from_slice(&[0x55, 0xAA, 0x5A, 0xA5]);
            hdr[4..8].copy_from_slice(&98u32.to_le_bytes());
            hdr[24..28].copy_from_slice(&(payload.len() as u32).to_le_bytes());
            hdr[60..64].copy_from_slice(b"boot");
            let mut f = File::create(&uapp_path).unwrap();
            f.write_all(&hdr).unwrap();
            f.write_all(payload).unwrap();
        }
        assert_eq!(detect_oem_magic_file(&uapp_path), "UPDATE_APP");
        let inspected = inspect_oem_path(&uapp_path).unwrap();
        assert_eq!(inspected.kind, "UPDATE_APP");
        assert_eq!(inspected.entries[0].name, "boot.img");
        let uapp_out = root.join("uapp_out");
        let ex = extract_oem_path(&uapp_path, &uapp_out, None).unwrap();
        assert_eq!(ex.extracted, vec!["boot.img"]);
        assert_eq!(
            fs::read(uapp_out.join("boot.img")).unwrap(),
            b"HELLO_BOOT_IMAGE"
        );

        // 2. Synthetic OZIP (mode 1 single blob)
        let ozip_path = root.join("firmware.ozip");
        {
            let key_bytes: [u8; 16] = [
                0xD6, 0xEE, 0xCF, 0x0A, 0xE5, 0xAC, 0xD4, 0xE0, 0xE9, 0xFE, 0x52, 0x2D, 0xE7, 0xCE,
                0x38, 0x1E,
            ];
            let mut plain = vec![0u8; 32];
            plain[..4].copy_from_slice(b"PK\x03\x04");
            plain[4..12].copy_from_slice(b"OZIPTEST");
            let enc = ecb::Encryptor::<aes::Aes128>::new_from_slice(&key_bytes)
                .unwrap()
                .encrypt_padded_mut::<NoPadding>(&mut plain, 32)
                .unwrap()
                .to_vec();
            let mut buf = vec![0u8; 0x1050 + enc.len()];
            buf[0..12].copy_from_slice(b"OPPOENCRYPT!");
            buf[0x10..0x12].copy_from_slice(b"32");
            buf[0x1050..].copy_from_slice(&enc);
            fs::write(&ozip_path, buf).unwrap();
        }
        assert_eq!(detect_oem_magic_file(&ozip_path), "OZIP");
        let ozip_out = root.join("ozip_out");
        let ozip_ex = extract_oem_path(&ozip_path, &ozip_out, None).unwrap();
        assert_eq!(ozip_ex.extracted, vec!["firmware.zip"]);
        let dec = fs::read(ozip_out.join("firmware.zip")).unwrap();
        assert!(dec.starts_with(b"PK\x03\x04OZIPTEST"));

        // 3. Synthetic DZ
        let dz_path = root.join("test.dz");
        {
            let slice_raw = b"DZ_PARTITION_DATA_OK";
            let md5 = md5::Md5::digest(slice_raw);
            let mut zenc = ZlibEncoder::new(Vec::new(), Compression::default());
            zenc.write_all(slice_raw).unwrap();
            let comp = zenc.finish().unwrap();

            let mut file_hdr = vec![0u8; 512];
            file_hdr[0..4].copy_from_slice(&[0x32, 0x96, 0x18, 0x74]);
            file_hdr[16..22].copy_from_slice(b"LG-G8\0");
            file_hdr[192..196].copy_from_slice(&1u32.to_le_bytes());

            let mut ch_hdr = vec![0u8; 512];
            ch_hdr[0..4].copy_from_slice(&[0x30, 0x12, 0x95, 0x78]);
            ch_hdr[4..10].copy_from_slice(b"boot_a");
            ch_hdr[36..44].copy_from_slice(b"chunk000");
            ch_hdr[100..104].copy_from_slice(&(slice_raw.len() as u32).to_le_bytes());
            ch_hdr[104..108].copy_from_slice(&(comp.len() as u32).to_le_bytes());
            ch_hdr[108..124].copy_from_slice(md5.as_slice());

            let mut f = File::create(&dz_path).unwrap();
            f.write_all(&file_hdr).unwrap();
            f.write_all(&ch_hdr).unwrap();
            f.write_all(&comp).unwrap();
        }
        assert_eq!(detect_oem_magic_file(&dz_path), "DZ");
        let dz_out = root.join("dz_out");
        let dz_ex = extract_oem_path(&dz_path, &dz_out, None).unwrap();
        assert_eq!(dz_ex.extracted, vec!["boot_a.img"]);
        assert_eq!(fs::read(dz_out.join("boot_a.img")).unwrap(), b"DZ_PARTITION_DATA_OK");

        // 4. Synthetic RomFS
        let romfs_path = root.join("test.romfs");
        {
            let mut img = vec![0u8; 256];
            img[0..8].copy_from_slice(b"-rom1fs-");
            img[8..12].copy_from_slice(&256u32.to_be_bytes());
            // volume name "vol\0" at offset 16 (padded to 16 -> root entry starts at 32)
            img[16..20].copy_from_slice(b"vol\0");
            // file entry at offset 32: next=0 | type=2 (file), spec=0, size=5, csum=0
            img[32..36].copy_from_slice(&2u32.to_be_bytes());
            img[40..44].copy_from_slice(&5u32.to_be_bytes());
            // filename "a.txt\0" at offset 48 (padded to 16 -> data starts at 64)
            img[48..54].copy_from_slice(b"a.txt\0");
            img[64..69].copy_from_slice(b"hello");
            fs::write(&romfs_path, img).unwrap();
        }
        assert_eq!(detect_oem_magic_file(&romfs_path), "ROMFS");
        let romfs_out = root.join("romfs_out");
        let rfs_ex = extract_oem_path(&romfs_path, &romfs_out, None).unwrap();
        assert_eq!(rfs_ex.extracted, vec!["a.txt"]);
        assert_eq!(fs::read(romfs_out.join("a.txt")).unwrap(), b"hello");

        let _ = fs::remove_dir_all(&root);
    }
}
