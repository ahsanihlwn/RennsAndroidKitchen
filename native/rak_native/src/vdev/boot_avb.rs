// SPDX-License-Identifier: MPL-2.0
//! Fast in-memory Rust inspectors for boot/vendor_boot/kernel/ramdisk, AVB0 descriptors, and dm-verity hashtrees.

use crate::vdev::container::read_zip_entry_vec;
use erofs_rs::sparse::SparseImage;
use magiskboot::compress::decompress_bytes;
use magiskboot::format::{Format, detect_format_lg};
use magiskboot::{
    BootContents, BootHeaderInfo, DtbFstabEntry, DtbMetadata, extract_dtbo_fdt_meta,
    inspect_boot_contents_bytes, inspect_boot_contents_file,
};
use flate2::read::GzDecoder;
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use rayon::prelude::*;
use serde::Serialize;
use sha2::{Digest, Sha256, Sha512};
use std::collections::BTreeMap;
use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::PathBuf;
use std::time::Instant;

#[derive(Serialize)]
pub struct KernelSummary {
    pub size: usize,
    pub arch: String,
    pub page_size: String,
    pub version: String,
    pub banner: String,
    pub vermagic: String,
    pub has_config: bool,
    pub kernel_config: BTreeMap<String, String>,
    pub kernel_config_count: usize,
}

#[derive(Serialize)]
pub struct KoModuleInfo {
    pub path: String,
    pub name: String,
    pub vermagic: String,
    pub depends: Vec<String>,
    pub has_crcs: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub module_layout_crc: Option<u32>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub firmware: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub of_aliases: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub exported_syms: Vec<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub exported_crcs: Vec<(String, u32)>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub imported_crcs: Vec<(String, u32)>,
}

#[derive(Serialize)]
pub struct RamdiskSummary {
    pub name: String,
    pub ramdisk_type: u32,
    pub format: String,
    pub entry_count: usize,
    pub files: Vec<String>,
    pub symlinks: BTreeMap<String, String>,
    pub fstabs: BTreeMap<String, String>,
    pub modules_load: BTreeMap<String, Vec<String>>,
    pub modules_dep: BTreeMap<String, String>,
    pub init_rc: BTreeMap<String, String>,
    pub ko_modules: Vec<KoModuleInfo>,
}

#[derive(Serialize)]
pub struct AvbDescriptorSummary {
    pub kind: String,
    pub partition: String,
    pub image_size: u64,
    pub tree_offset: u64,
    pub tree_size: u64,
    pub data_block_size: u32,
    pub hash_block_size: u32,
    pub hash_algorithm: String,
    pub salt_hex: String,
    pub digest_hex: String,
    pub rollback_index_location: u32,
    pub flags: u32,
    pub key_val: Option<(String, String)>,
}

#[derive(Serialize)]
pub struct VbMetaSummary {
    pub valid: bool,
    pub flags: u32,
    pub algorithm: u32,
    pub rollback_index: u64,
    pub release: String,
    pub hashtree_disabled: bool,
    pub verification_disabled: bool,
    pub descriptors: Vec<AvbDescriptorSummary>,
}

#[derive(Serialize)]
pub struct BootInspectionReport {
    pub header: BootHeaderInfo,
    pub bootconfig: String,
    pub kernel: Option<KernelSummary>,
    pub dtb: DtbMetadata,
    pub kernel_dtb: DtbMetadata,
    pub ramdisks: Vec<RamdiskSummary>,
    pub embedded_vbmeta: Option<VbMetaSummary>,
}

fn find_ascii_token(haystack: &[u8], prefix: &[u8], max_len: usize) -> String {
    let Some(pos) = haystack
        .windows(prefix.len())
        .position(|w| w == prefix)
    else {
        return String::new();
    };
    let tail = &haystack[pos..];
    let end = tail
        .iter()
        .take(max_len)
        .position(|&b| b == 0 || b == b'\n' || b == b'\r')
        .unwrap_or_else(|| tail.len().min(max_len));
    String::from_utf8_lossy(&tail[..end]).trim().to_string()
}

fn is_tracked_kernel_config(key: &str) -> bool {
    matches!(
        key,
        "CONFIG_EROFS_FS"
            | "CONFIG_EROFS_FS_ZIP"
            | "CONFIG_EROFS_FS_ZIP_LZMA"
            | "CONFIG_F2FS_FS"
            | "CONFIG_F2FS_FS_COMPRESSION"
            | "CONFIG_F2FS_FS_SECURITY"
            | "CONFIG_F2FS_FS_ENCRYPTION"
            | "CONFIG_EXT4_FS"
            | "CONFIG_EXT4_FS_SECURITY"
            | "CONFIG_SQUASHFS"
            | "CONFIG_OVERLAY_FS"
            | "CONFIG_TMPFS"
            | "CONFIG_INCREMENTAL_FS"
            | "CONFIG_FUSE_FS"
            | "CONFIG_FS_ENCRYPTION"
            | "CONFIG_FS_ENCRYPTION_INLINE_CRYPT"
            | "CONFIG_BLK_INLINE_ENCRYPTION"
            | "CONFIG_DM_VERITY"
            | "CONFIG_DM_VERITY_FEC"
            | "CONFIG_DM_BOW"
            | "CONFIG_DM_DEFAULT_KEY"
            | "CONFIG_DM_SNAPSHOT"
            | "CONFIG_BLK_DEV_DM"
            | "CONFIG_BLK_DEV_LOOP"
            | "CONFIG_ANDROID_BINDERFS"
            | "CONFIG_ANDROID_BINDER_IPC"
            | "CONFIG_BPF_SYSCALL"
            | "CONFIG_CGROUP_BPF"
            | "CONFIG_BPF_JIT"
            | "CONFIG_PSI"
            | "CONFIG_USERFAULTFD"
            | "CONFIG_ASHMEM"
            | "CONFIG_MEMFD_CREATE"
            | "CONFIG_SECURITY_SELINUX"
            | "CONFIG_SECURITY_SELINUX_DEVELOP"
            | "CONFIG_KALLSYMS"
            | "CONFIG_MODULES"
            | "CONFIG_MODVERSIONS"
            | "CONFIG_ARM64_4K_PAGES"
            | "CONFIG_ARM64_16K_PAGES"
            | "CONFIG_ARM64_64K_PAGES"
            | "CONFIG_CFG80211"
            | "CONFIG_MAC80211"
            | "CONFIG_BT"
            | "CONFIG_SND"
            | "CONFIG_SOUND"
            | "CONFIG_MEDIA_SUPPORT"
            | "CONFIG_VIDEO_DEV"
            | "CONFIG_DRM"
            | "CONFIG_INPUT_TOUCHSCREEN"
            | "CONFIG_DMABUF_HEAPS"
            | "CONFIG_ANDROID_VENDOR_HOOKS"
    )
}

fn extract_ikcfg(img: &[u8]) -> (bool, BTreeMap<String, String>, usize) {
    let Some(st) = img.windows(8).position(|w| w == b"IKCFG_ST") else {
        return (false, BTreeMap::new(), 0);
    };
    let gz_start = st + 8;
    if gz_start >= img.len() {
        return (true, BTreeMap::new(), 0);
    }
    let tail = &img[gz_start..];
    let gz_slice = if let Some(ed) = tail.windows(8).position(|w| w == b"IKCFG_ED") {
        &tail[..ed]
    } else {
        &tail[..tail.len().min(512 * 1024)]
    };
    let mut dec = GzDecoder::new(gz_slice);
    let mut raw = Vec::new();
    // Cap decoded .config at 2 MiB to stay safe against malformed streams
    let _ = dec.by_ref().take(2 * 1024 * 1024).read_to_end(&mut raw);
    if raw.is_empty() {
        return (true, BTreeMap::new(), 0);
    }
    let text = String::from_utf8_lossy(&raw);
    let mut map = BTreeMap::new();
    let mut total = 0usize;
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Some(rest) = trimmed.strip_prefix("# CONFIG_") {
            if let Some(name) = rest.strip_suffix(" is not set") {
                total += 1;
                let key = format!("CONFIG_{name}");
                if is_tracked_kernel_config(&key) {
                    map.insert(key, "n".into());
                }
            }
            continue;
        }
        if trimmed.starts_with("CONFIG_") {
            if let Some((k, v)) = trimmed.split_once('=') {
                total += 1;
                if is_tracked_kernel_config(k) {
                    map.insert(k.to_string(), v.trim().trim_matches('"').to_string());
                }
            }
        }
    }
    (true, map, total)
}

fn analyze_kernel_bytes(raw: &[u8]) -> Option<KernelSummary> {
    if raw.is_empty() {
        return None;
    }
    let fmt = detect_format_lg(raw);
    let img_cow;
    let img: &[u8] = if fmt.is_compressed() && fmt != Format::Unknown {
        if let Ok(dec) = decompress_bytes(fmt, raw) {
            img_cow = dec;
            &img_cow
        } else {
            raw
        }
    } else {
        raw
    };

    let mut arch = String::new();
    let mut page_size = String::new();
    if img.len() >= 64 && &img[56..60] == b"ARM\x64" {
        arch = "arm64".into();
        let flags = u64::from_le_bytes(img[16..24].try_into().unwrap_or([0; 8]));
        page_size = match (flags >> 1) & 0x3 {
            1 => "4K",
            2 => "16K",
            3 => "64K",
            _ => "unspecified",
        }
        .into();
    }

    let banner = find_ascii_token(img, b"Linux version ", 240);
    let version = banner
        .strip_prefix("Linux version ")
        .and_then(|s| s.split_whitespace().next())
        .unwrap_or("")
        .to_string();
    let vermagic_full = find_ascii_token(img, b"vermagic=", 180);
    let vermagic = vermagic_full
        .strip_prefix("vermagic=")
        .unwrap_or("")
        .to_string();
    let (has_config, kernel_config, kernel_config_count) = extract_ikcfg(img);

    Some(KernelSummary {
        size: img.len(),
        arch,
        page_size,
        version,
        banner,
        vermagic,
        has_config,
        kernel_config,
        kernel_config_count,
    })
}

fn extract_of_compat_from_alias(raw: &str) -> Option<String> {
    // Format: "N*T*Cqcom,adsp-loader" or "N*T*Cqcom,adsp-loaderC*"
    let after_c = raw
        .split_once("T*C")
        .map(|(_, r)| r)
        .or_else(|| raw.split_once("C").map(|(_, r)| r))?;
    let trimmed = after_c
        .trim_end_matches("C*")
        .trim_end_matches('*')
        .trim();
    if trimmed.is_empty() || trimmed == "*" {
        return None;
    }
    Some(trimmed.to_string())
}

pub(crate) fn parse_ko_modinfo(path: &str, data: &[u8]) -> KoModuleInfo {
    let mut name = std::path::Path::new(path)
        .file_stem()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_string();
    let mut vermagic = String::new();
    let mut depends = Vec::new();
    let mut has_crcs = false;
    let mut module_layout_crc: Option<u32> = None;
    let mut firmware: Vec<String> = Vec::new();
    let mut of_aliases: Vec<String> = Vec::new();
    let mut exported_syms: Vec<String> = Vec::new();
    let mut exported_crcs: Vec<(String, u32)> = Vec::new();
    let mut imported_crcs: Vec<(String, u32)> = Vec::new();

    if let Ok(elf) = goblin::elf::Elf::parse(data) {
        let crc_width = if elf.is_64 { 8usize } else { 4usize };
        for sh in &elf.section_headers {
            let sname = elf.shdr_strtab.get_at(sh.sh_name).unwrap_or("");
            let start = sh.sh_offset as usize;
            let end = start.saturating_add(sh.sh_size as usize).min(data.len());
            if sname == "__versions" {
                has_crcs = true;
                if start < end {
                    for ent in data[start..end].chunks_exact(64) {
                        let crc = if crc_width == 8 {
                            u64::from_le_bytes(ent[0..8].try_into().unwrap_or([0; 8])) as u32
                        } else {
                            u32::from_le_bytes(ent[0..4].try_into().unwrap_or([0; 4]))
                        };
                        let name_bytes = &ent[crc_width..];
                        let nul = name_bytes.iter().position(|&b| b == 0).unwrap_or(name_bytes.len());
                        let sym_name = String::from_utf8_lossy(&name_bytes[..nul]).trim().to_string();
                        if !sym_name.is_empty() {
                            if sym_name == "module_layout" {
                                module_layout_crc = Some(crc);
                            }
                            imported_crcs.push((sym_name, crc));
                        }
                    }
                }
            } else if sname == "__ksymtab_strings" && start < end {
                for raw_sym in data[start..end].split(|&b| b == 0) {
                    if raw_sym.is_empty() {
                        continue;
                    }
                    let s = String::from_utf8_lossy(raw_sym).trim().to_string();
                    if !s.is_empty() && !exported_syms.contains(&s) {
                        exported_syms.push(s);
                    }
                }
            } else if sname == ".modinfo" && start < end {
                for kv in data[start..end].split(|&b| b == 0) {
                    if kv.is_empty() {
                        continue;
                    }
                    let text = String::from_utf8_lossy(kv);
                    if let Some(v) = text.strip_prefix("vermagic=") {
                        vermagic = v.trim().to_string();
                    } else if let Some(v) = text.strip_prefix("name=") {
                        name = v.trim().to_string();
                    } else if let Some(v) = text.strip_prefix("depends=") {
                        depends = v
                            .split(',')
                            .map(str::trim)
                            .filter(|s| !s.is_empty())
                            .map(str::to_string)
                            .collect();
                    } else if let Some(v) = text.strip_prefix("firmware=") {
                        let fw = v.trim().to_string();
                        if !fw.is_empty() && !firmware.contains(&fw) {
                            firmware.push(fw);
                        }
                    } else if let Some(v) = text.strip_prefix("alias=of:") {
                        if let Some(compat) = extract_of_compat_from_alias(v) {
                            if !of_aliases.contains(&compat) {
                                of_aliases.push(compat);
                            }
                        }
                    }
                }
            }
        }
        // Extract exact exported symbol CRCs from __crc_<symname> entries in .symtab:
        // - Linux <= 5.15: SHN_ABS (0xfff1) where st_value IS the u32 CRC
        // - Linux >= 6.0 (Android 15/16): data symbol inside __kcrctab* where st_value is byte offset to u32 CRC
        const SHN_ABS: usize = 0xfff1;
        for sym in &elf.syms {
            if let Some(sname) = elf.strtab.get_at(sym.st_name) {
                if let Some(exported) = sname.strip_prefix("__crc_") {
                    if exported.is_empty() {
                        continue;
                    }
                    if sym.st_shndx == SHN_ABS {
                        exported_crcs.push((exported.to_string(), sym.st_value as u32));
                    } else if let Some(sh) = elf.section_headers.get(sym.st_shndx) {
                        let off = (sh.sh_offset as usize).saturating_add(sym.st_value as usize);
                        if let Some(b) = data.get(off..off + 4) {
                            let crc = u32::from_le_bytes([b[0], b[1], b[2], b[3]]);
                            exported_crcs.push((exported.to_string(), crc));
                        }
                    }
                }
            }
        }
    } else {
        let vm = find_ascii_token(data, b"vermagic=", 180);
        if let Some(v) = vm.strip_prefix("vermagic=") {
            vermagic = v.trim().to_string();
        }
    }

    KoModuleInfo {
        path: path.to_string(),
        name,
        vermagic,
        depends,
        has_crcs,
        module_layout_crc,
        firmware,
        of_aliases,
        exported_syms,
        exported_crcs,
        imported_crcs,
    }
}

fn summarize_boot(contents: BootContents) -> BootInspectionReport {
    let kernel = analyze_kernel_bytes(&contents.kernel_bytes);
    let ramdisks = contents
        .ramdisks
        .into_iter()
        .map(|rd| {
            let mut files = Vec::new();
            let mut symlinks = BTreeMap::new();
            let mut fstabs = BTreeMap::new();
            let mut modules_load = BTreeMap::new();
            let mut modules_dep = BTreeMap::new();
            let mut init_rc = BTreeMap::new();
            let mut ko_modules = Vec::new();

            for (raw_name, entry) in &rd.archive.entries {
                let vpath = if raw_name.starts_with('/') {
                    raw_name.clone()
                } else {
                    format!("/{raw_name}")
                };
                if entry.is_lnk() {
                    let target = String::from_utf8_lossy(&entry.data)
                        .trim_end_matches('\0')
                        .to_string();
                    symlinks.insert(vpath.clone(), target);
                    continue;
                }
                if entry.is_dir() {
                    continue;
                }
                files.push(vpath.clone());
                let base = std::path::Path::new(&vpath)
                    .file_name()
                    .and_then(|s| s.to_str())
                    .unwrap_or("");
                if base.starts_with("fstab.") && entry.data.len() <= 512 * 1024 {
                    fstabs.insert(vpath, String::from_utf8_lossy(&entry.data).into_owned());
                } else if base == "modules.load" || base.starts_with("modules.load.") {
                    let mods: Vec<String> = String::from_utf8_lossy(&entry.data)
                        .lines()
                        .map(str::trim)
                        .filter(|s| !s.is_empty() && !s.starts_with('#'))
                        .map(str::to_string)
                        .collect();
                    modules_load.insert(vpath, mods);
                } else if base == "modules.dep" && entry.data.len() <= 2 * 1024 * 1024 {
                    modules_dep.insert(vpath, String::from_utf8_lossy(&entry.data).into_owned());
                } else if vpath.ends_with(".rc") && entry.data.len() <= 2 * 1024 * 1024 {
                    init_rc.insert(vpath, String::from_utf8_lossy(&entry.data).into_owned());
                } else if vpath.ends_with(".ko") {
                    ko_modules.push(parse_ko_modinfo(&vpath, &entry.data));
                }
            }

            RamdiskSummary {
                name: rd.name,
                ramdisk_type: rd.ramdisk_type,
                format: rd.format,
                entry_count: rd.archive.entries.len(),
                files,
                symlinks,
                fstabs,
                modules_load,
                modules_dep,
                init_rc,
                ko_modules,
            }
        })
        .collect();

    let embedded_vbmeta = contents
        .vbmeta_bytes
        .as_deref()
        .and_then(parse_vbmeta_summary);

    BootInspectionReport {
        header: contents.header,
        bootconfig: contents.bootconfig,
        kernel,
        dtb: contents.dtb_meta,
        kernel_dtb: contents.kernel_dtb_meta,
        ramdisks,
        embedded_vbmeta,
    }
}

fn u32_be(b: &[u8], off: usize) -> Option<u32> {
    Some(u32::from_be_bytes(b.get(off..off + 4)?.try_into().ok()?))
}

fn u64_be(b: &[u8], off: usize) -> Option<u64> {
    Some(u64::from_be_bytes(b.get(off..off + 8)?.try_into().ok()?))
}

fn cstr(b: &[u8]) -> String {
    let end = b.iter().position(|&c| c == 0).unwrap_or(b.len());
    String::from_utf8_lossy(&b[..end]).into_owned()
}

fn hex_encode(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        s.push(HEX[(b >> 4) as usize] as char);
        s.push(HEX[(b & 0xf) as usize] as char);
    }
    s
}

fn hex_decode(s: &str) -> Result<Vec<u8>, String> {
    let clean = s.trim();
    if clean.len() % 2 != 0 {
        return Err("odd hex length".into());
    }
    let mut out = Vec::with_capacity(clean.len() / 2);
    for pair in clean.as_bytes().chunks_exact(2) {
        let hi = (pair[0] as char)
            .to_digit(16)
            .ok_or_else(|| "invalid hex".to_string())?;
        let lo = (pair[1] as char)
            .to_digit(16)
            .ok_or_else(|| "invalid hex".to_string())?;
        out.push(((hi << 4) | lo) as u8);
    }
    Ok(out)
}

pub(crate) fn parse_vbmeta_summary(data: &[u8]) -> Option<VbMetaSummary> {
    const HEADER_SIZE: usize = 256;
    if data.len() < HEADER_SIZE || &data[0..4] != b"AVB0" {
        return None;
    }
    let auth_size = u64_be(data, 12)? as usize;
    let algorithm = u32_be(data, 28)?;
    let desc_off = u64_be(data, 96)? as usize;
    let desc_size = u64_be(data, 104)? as usize;
    let rollback_index = u64_be(data, 112)?;
    let flags = u32_be(data, 120)?;
    let release = cstr(data.get(128..176)?);

    let mut descriptors = Vec::new();
    let base = HEADER_SIZE.saturating_add(auth_size).saturating_add(desc_off);
    let end = base.saturating_add(desc_size).min(data.len());
    let mut pos = base;

    while pos + 16 <= end {
        let tag = u64_be(data, pos)?;
        let nbytes = u64_be(data, pos + 8)? as usize;
        let body = pos + 16;
        let body_end = body.saturating_add(nbytes);
        if nbytes == 0 || body_end > end {
            break;
        }
        let slice = &data[body..body_end];
        match tag {
            0 if slice.len() >= 16 => {
                // Property descriptor
                let klen = u64_be(slice, 0).unwrap_or(0) as usize;
                let vlen = u64_be(slice, 8).unwrap_or(0) as usize;
                let k = slice.get(16..16 + klen).map(cstr).unwrap_or_default();
                let v = slice
                    .get(16 + klen..16 + klen + vlen)
                    .map(cstr)
                    .unwrap_or_default();
                descriptors.push(AvbDescriptorSummary {
                    kind: "property".into(),
                    partition: String::new(),
                    image_size: 0,
                    tree_offset: 0,
                    tree_size: 0,
                    data_block_size: 0,
                    hash_block_size: 0,
                    hash_algorithm: String::new(),
                    salt_hex: String::new(),
                    digest_hex: String::new(),
                    rollback_index_location: 0,
                    flags: 0,
                    key_val: Some((k, v)),
                });
            }
            1 if slice.len() >= 164 => {
                // Hashtree descriptor
                let image_size = u64_be(slice, 4).unwrap_or(0);
                let tree_offset = u64_be(slice, 12).unwrap_or(0);
                let tree_size = u64_be(slice, 20).unwrap_or(0);
                let data_bs = u32_be(slice, 28).unwrap_or(0);
                let hash_bs = u32_be(slice, 32).unwrap_or(0);
                let algo = cstr(&slice[56..88]);
                let name_len = u32_be(slice, 88).unwrap_or(0) as usize;
                let salt_len = u32_be(slice, 92).unwrap_or(0) as usize;
                let digest_len = u32_be(slice, 96).unwrap_or(0) as usize;
                let dflags = u32_be(slice, 100).unwrap_or(0);
                let mut p = 164;
                let part = slice.get(p..p + name_len).map(cstr).unwrap_or_default();
                p += name_len;
                let salt = slice.get(p..p + salt_len).unwrap_or(&[]);
                p += salt_len;
                let digest = slice.get(p..p + digest_len).unwrap_or(&[]);
                descriptors.push(AvbDescriptorSummary {
                    kind: "hashtree".into(),
                    partition: part,
                    image_size,
                    tree_offset,
                    tree_size,
                    data_block_size: data_bs,
                    hash_block_size: hash_bs,
                    hash_algorithm: algo,
                    salt_hex: hex_encode(salt),
                    digest_hex: hex_encode(digest),
                    rollback_index_location: 0,
                    flags: dflags,
                    key_val: None,
                });
            }
            2 if slice.len() >= 116 => {
                // Hash descriptor (AvbHashDescriptor body: 56B fields + 60B reserved = 116B)
                let image_size = u64_be(slice, 0).unwrap_or(0);
                let algo = cstr(&slice[8..40]);
                let name_len = u32_be(slice, 40).unwrap_or(0) as usize;
                let salt_len = u32_be(slice, 44).unwrap_or(0) as usize;
                let digest_len = u32_be(slice, 48).unwrap_or(0) as usize;
                let dflags = u32_be(slice, 52).unwrap_or(0);
                let mut p = 116;
                let part = slice.get(p..p + name_len).map(cstr).unwrap_or_default();
                p += name_len;
                let salt = slice.get(p..p + salt_len).unwrap_or(&[]);
                p += salt_len;
                let digest = slice.get(p..p + digest_len).unwrap_or(&[]);
                descriptors.push(AvbDescriptorSummary {
                    kind: "hash".into(),
                    partition: part,
                    image_size,
                    tree_offset: 0,
                    tree_size: 0,
                    data_block_size: 0,
                    hash_block_size: 0,
                    hash_algorithm: algo,
                    salt_hex: hex_encode(salt),
                    digest_hex: hex_encode(digest),
                    rollback_index_location: 0,
                    flags: dflags,
                    key_val: None,
                });
            }
            4 if slice.len() >= 76 => {
                // Chain partition descriptor (AvbChainPartitionDescriptor body: 12B fields + 64B reserved = 76B)
                let rollback_loc = u32_be(slice, 0).unwrap_or(0);
                let name_len = u32_be(slice, 4).unwrap_or(0) as usize;
                let key_len = u32_be(slice, 8).unwrap_or(0) as usize;
                let cflags = u32_be(slice, 12).unwrap_or(0);
                let part = slice.get(76..76 + name_len).map(cstr).unwrap_or_default();
                let key_slice = slice.get(76 + name_len..76 + name_len + key_len).unwrap_or(&[]);
                let key_sha = if key_slice.is_empty() {
                    String::new()
                } else {
                    hex_encode(&Sha256::digest(key_slice))
                };
                descriptors.push(AvbDescriptorSummary {
                    kind: "chain".into(),
                    partition: part,
                    image_size: 0,
                    tree_offset: 0,
                    tree_size: 0,
                    data_block_size: 0,
                    hash_block_size: 0,
                    hash_algorithm: "sha256_pubkey".into(),
                    salt_hex: String::new(),
                    digest_hex: key_sha,
                    rollback_index_location: rollback_loc,
                    flags: cflags,
                    key_val: None,
                });
            }
            _ => {}
        }
        pos = (body_end + 7) & !7;
    }

    Some(VbMetaSummary {
        valid: true,
        flags,
        algorithm,
        rollback_index,
        release,
        hashtree_disabled: (flags & 1) != 0,
        verification_disabled: (flags & 2) != 0,
        descriptors,
    })
}

#[pyfunction]
pub fn vdev_inspect_boot_json(py: Python<'_>, path: PathBuf) -> PyResult<String> {
    let report = py
        .detach(move || -> Result<BootInspectionReport, String> {
            let s = path.to_string_lossy().into_owned();
            let contents = inspect_boot_contents_file(&s)?;
            Ok(summarize_boot(contents))
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&report).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
pub fn vdev_inspect_boot_bytes_json(py: Python<'_>, data: &[u8]) -> PyResult<String> {
    let owned = data.to_vec();
    let report = py
        .detach(move || -> Result<BootInspectionReport, String> {
            let contents = inspect_boot_contents_bytes(owned)?;
            Ok(summarize_boot(contents))
        })
        .map_err(PyValueError::new_err)?;
    serde_json::to_string(&report).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (zip_path, entry_name, cache_dir=None))]
pub fn vdev_inspect_zip_boot_json(
    py: Python<'_>,
    zip_path: PathBuf,
    entry_name: String,
    cache_dir: Option<PathBuf>,
) -> PyResult<String> {
    py.detach(move || -> Result<String, String> {
        let mut cache_file: Option<PathBuf> = None;
        if let Some(dir) = cache_dir {
            if let Ok(meta) = std::fs::metadata(&zip_path) {
                let mtime = meta
                    .modified()
                    .ok()
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| d.as_nanos())
                    .unwrap_or(0);
                let safe: String = entry_name
                    .chars()
                    .map(|c| if c.is_ascii_alphanumeric() { c } else { '_' })
                    .collect();
                let key = format!("boot6_{}_{}_{}.json", meta.len(), mtime, safe);
                let p = dir.join(key);
                if let Ok(cached) = std::fs::read_to_string(&p) {
                    return Ok(cached);
                }
                let _ = std::fs::create_dir_all(&dir);
                cache_file = Some(p);
            }
        }
        let data = read_zip_entry_vec(&zip_path, &entry_name)?;
        let contents = inspect_boot_contents_bytes(data)?;
        let report = summarize_boot(contents);
        let json = serde_json::to_string(&report).map_err(|e| e.to_string())?;
        if let Some(p) = cache_file {
            let _ = std::fs::write(p, &json);
        }
        Ok(json)
    })
    .map_err(PyOSError::new_err)
}

#[pyfunction]
pub fn vdev_inspect_vbmeta_json(py: Python<'_>, path: PathBuf) -> PyResult<String> {
    let summary = py
        .detach(move || -> Result<Option<VbMetaSummary>, String> {
            let data = std::fs::read(&path).map_err(|e| e.to_string())?;
            Ok(parse_vbmeta_summary(&data))
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&summary).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
pub fn vdev_inspect_vbmeta_bytes_json(py: Python<'_>, data: &[u8]) -> PyResult<String> {
    let owned = data.to_vec();
    let summary = py.detach(move || parse_vbmeta_summary(&owned));
    serde_json::to_string(&summary).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (zip_path, entry_name, cache_dir=None))]
pub fn vdev_inspect_zip_vbmeta_json(
    py: Python<'_>,
    zip_path: PathBuf,
    entry_name: String,
    cache_dir: Option<PathBuf>,
) -> PyResult<String> {
    py.detach(move || -> Result<String, String> {
        let mut cache_file: Option<PathBuf> = None;
        if let Some(dir) = cache_dir {
            if let Ok(meta) = std::fs::metadata(&zip_path) {
                let mtime = meta
                    .modified()
                    .ok()
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| d.as_nanos())
                    .unwrap_or(0);
                let safe: String = entry_name
                    .chars()
                    .map(|c| if c.is_ascii_alphanumeric() { c } else { '_' })
                    .collect();
                let key = format!("vbmeta2_{}_{}_{}.json", meta.len(), mtime, safe);
                let p = dir.join(key);
                if let Ok(cached) = std::fs::read_to_string(&p) {
                    return Ok(cached);
                }
                let _ = std::fs::create_dir_all(&dir);
                cache_file = Some(p);
            }
        }
        let data = read_zip_entry_vec(&zip_path, &entry_name)?;
        let summary = parse_vbmeta_summary(&data);
        let json = serde_json::to_string(&summary).map_err(|e| e.to_string())?;
        if let Some(p) = cache_file {
            let _ = std::fs::write(p, &json);
        }
        Ok(json)
    })
    .map_err(PyOSError::new_err)
}

#[derive(Serialize)]
pub struct HashtreeVerifyResult {
    pub matched: Option<bool>,
    pub computed_root: String,
    pub elapsed_ms: u128,
}

fn hash_salted_block(algo: &str, salt: &[u8], block: &[u8]) -> Result<Vec<u8>, String> {
    match algo {
        "sha256" => {
            let mut h = Sha256::new();
            h.update(salt);
            h.update(block);
            Ok(h.finalize().to_vec())
        }
        "sha512" => {
            let mut h = Sha512::new();
            h.update(salt);
            h.update(block);
            Ok(h.finalize().to_vec())
        }
        _ => Err(format!("Unsupported hashtree algorithm: {algo}")),
    }
}

#[pyfunction]
#[pyo3(signature = (img_path, image_size, salt_hex, data_block_size, hash_block_size, algorithm="sha256", expected_root_hex=""))]
pub fn vdev_verify_hashtree_json(
    py: Python<'_>,
    img_path: PathBuf,
    image_size: u64,
    salt_hex: String,
    data_block_size: usize,
    hash_block_size: usize,
    algorithm: &str,
    expected_root_hex: &str,
) -> PyResult<String> {
    let algo = algorithm.to_ascii_lowercase();
    let expected = expected_root_hex.trim().to_ascii_lowercase();
    let res = py
        .detach(move || -> Result<HashtreeVerifyResult, String> {
            let t0 = Instant::now();
            if image_size == 0 || data_block_size == 0 || hash_block_size == 0 {
                return Ok(HashtreeVerifyResult {
                    matched: None,
                    computed_root: String::new(),
                    elapsed_ms: 0,
                });
            }
            let salt = hex_decode(&salt_hex)?;
            let digest_len = match algo.as_str() {
                "sha256" => 32usize,
                "sha512" => 64usize,
                _ => {
                    return Ok(HashtreeVerifyResult {
                        matched: None,
                        computed_root: String::new(),
                        elapsed_ms: 0,
                    })
                }
            };

            let mut sparse_check = File::open(&img_path).map_err(|e| e.to_string())?;
            let mut magic = [0u8; 4];
            let is_sparse =
                sparse_check.read_exact(&mut magic).is_ok() && u32::from_le_bytes(magic) == 0xED26_FF3A;
            drop(sparse_check);

            let chunk_bytes = data_block_size * 512;
            let mut level = Vec::<u8>::with_capacity(((image_size as usize / data_block_size) + 1) * digest_len);

            if is_sparse {
                let sp = SparseImage::open(&img_path).map_err(|e| e.to_string())?;
                let mut offset = 0u64;
                let mut buf = vec![0u8; chunk_bytes];
                while offset < image_size {
                    let want = ((image_size - offset) as usize).min(chunk_bytes);
                    let n = sp.read_at(offset, &mut buf[..want]).map_err(|e| e.to_string())?;
                    if n == 0 {
                        break;
                    }
                    let slice = &buf[..n];
                    let hashes: Vec<Result<Vec<u8>, String>> = slice
                        .par_chunks(data_block_size)
                        .map(|blk| {
                            if blk.len() == data_block_size {
                                hash_salted_block(&algo, &salt, blk)
                            } else {
                                let mut padded = vec![0u8; data_block_size];
                                padded[..blk.len()].copy_from_slice(blk);
                                hash_salted_block(&algo, &salt, &padded)
                            }
                        })
                        .collect();
                    for h in hashes {
                        level.extend_from_slice(&h?);
                    }
                    offset += n as u64;
                }
            } else {
                let mut f = File::open(&img_path).map_err(|e| e.to_string())?;
                f.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
                let mut remaining = image_size;
                let mut buf = vec![0u8; chunk_bytes];
                while remaining > 0 {
                    let want = (remaining as usize).min(chunk_bytes);
                    let n = f.read(&mut buf[..want]).map_err(|e| e.to_string())?;
                    if n == 0 {
                        break;
                    }
                    let hashes: Vec<Result<Vec<u8>, String>> = buf[..n]
                        .par_chunks(data_block_size)
                        .map(|blk| {
                            if blk.len() == data_block_size {
                                hash_salted_block(&algo, &salt, blk)
                            } else {
                                let mut padded = vec![0u8; data_block_size];
                                padded[..blk.len()].copy_from_slice(blk);
                                hash_salted_block(&algo, &salt, &padded)
                            }
                        })
                        .collect();
                    for h in hashes {
                        level.extend_from_slice(&h?);
                    }
                    remaining -= n as u64;
                }
            }

            if level.is_empty() {
                return Ok(HashtreeVerifyResult {
                    matched: None,
                    computed_root: String::new(),
                    elapsed_ms: t0.elapsed().as_millis(),
                });
            }

            while level.len() > digest_len {
                let rem = level.len() % hash_block_size;
                if rem != 0 {
                    level.resize(level.len() + (hash_block_size - rem), 0);
                }
                let next_parts: Vec<Result<Vec<u8>, String>> = level
                    .par_chunks(hash_block_size)
                    .map(|blk| hash_salted_block(&algo, &salt, blk))
                    .collect();
                let mut next = Vec::with_capacity(next_parts.len() * digest_len);
                for part in next_parts {
                    next.extend_from_slice(&part?);
                }
                level = next;
            }

            let root_hex = hex_encode(&level);
            let matched = if expected.is_empty() {
                None
            } else {
                Some(root_hex == expected)
            };
            Ok(HashtreeVerifyResult {
                matched,
                computed_root: root_hex,
                elapsed_ms: t0.elapsed().as_millis(),
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&res).map_err(|e| PyOSError::new_err(e.to_string()))
}

// ── Deep DTBO Inspection & AVB Hash Verification ──

#[derive(Serialize)]
pub struct VdevDtboEntryReport {
    pub index: usize,
    pub id: u32,
    pub rev: u32,
    pub flags: u32,
    pub custom: [u32; 4],
    pub dt_size: u32,
    pub is_fdt: bool,
    pub model: String,
    pub compatibles: Vec<String>,
    pub fixups: Vec<String>,
    pub target_paths: Vec<String>,
    pub bootargs: Vec<String>,
    pub fstab: Vec<DtbFstabEntry>,
}

#[derive(Serialize)]
pub struct VdevDtboReport {
    pub valid: bool,
    pub error: String,
    pub version: u32,
    pub page_size: u32,
    pub total_size: u32,
    pub file_size: u64,
    pub entry_count: usize,
    pub valid_fdt_count: usize,
    pub invalid_fdt_indices: Vec<usize>,
    pub entries: Vec<VdevDtboEntryReport>,
    pub all_compatibles: Vec<String>,
    pub all_fixups: Vec<String>,
    pub all_bootargs: Vec<String>,
    pub all_fstab: Vec<DtbFstabEntry>,
    pub embedded_vbmeta: Option<VbMetaSummary>,
    pub avb_original_image_size: u64,
}

fn extract_avb_footer_vbmeta(blob: &[u8]) -> (Option<VbMetaSummary>, u64) {
    // AVB footer is the last 64 bytes of a signed partition image, starting with "AVBf".
    if blob.len() < 64 {
        return (None, 0);
    }
    let foot = &blob[blob.len() - 64..];
    if &foot[0..4] != b"AVBf" {
        return (None, 0);
    }
    let orig_size = u64_be(foot, 12).unwrap_or(0);
    let vbmeta_off = u64_be(foot, 20).unwrap_or(0) as usize;
    let vbmeta_sz = u64_be(foot, 28).unwrap_or(0) as usize;
    if vbmeta_sz >= 256 && vbmeta_off.saturating_add(vbmeta_sz) <= blob.len() {
        let vb = parse_vbmeta_summary(&blob[vbmeta_off..vbmeta_off + vbmeta_sz]);
        return (vb, orig_size);
    }
    (None, orig_size)
}

fn summarize_dtbo_bytes(blob: &[u8]) -> VdevDtboReport {
    let (embedded_vbmeta, avb_original_image_size) = extract_avb_footer_vbmeta(blob);
    let parsed = crate::dtbo::parse_dtbo_container(blob);
    match parsed {
        Err(err) => VdevDtboReport {
            valid: false,
            error: err,
            version: 0,
            page_size: 0,
            total_size: 0,
            file_size: blob.len() as u64,
            entry_count: 0,
            valid_fdt_count: 0,
            invalid_fdt_indices: Vec::new(),
            entries: Vec::new(),
            all_compatibles: Vec::new(),
            all_fixups: Vec::new(),
            all_bootargs: Vec::new(),
            all_fstab: Vec::new(),
            embedded_vbmeta,
            avb_original_image_size,
        },
        Ok((hdr, raw_entries)) => {
            let mut entries = Vec::with_capacity(raw_entries.len());
            let mut valid_fdt_count = 0usize;
            let mut invalid_fdt_indices = Vec::new();
            let mut all_compatibles = Vec::new();
            let mut all_fixups = Vec::new();
            let mut all_bootargs = Vec::new();
            let mut all_fstab = Vec::new();

            for (info, fdt_bytes) in raw_entries {
                let meta = if info.is_fdt {
                    valid_fdt_count += 1;
                    extract_dtbo_fdt_meta(&fdt_bytes)
                } else {
                    invalid_fdt_indices.push(info.index);
                    Default::default()
                };
                for c in &meta.compatibles {
                    if !all_compatibles.contains(c) {
                        all_compatibles.push(c.clone());
                    }
                }
                for fx in &meta.fixups {
                    if !all_fixups.contains(fx) {
                        all_fixups.push(fx.clone());
                    }
                }
                for ba in &meta.bootargs {
                    if !all_bootargs.contains(ba) {
                        all_bootargs.push(ba.clone());
                    }
                }
                for fe in &meta.fstab {
                    all_fstab.push(fe.clone());
                }
                entries.push(VdevDtboEntryReport {
                    index: info.index,
                    id: info.id,
                    rev: info.rev,
                    flags: info.flags,
                    custom: info.custom,
                    dt_size: info.dt_size,
                    is_fdt: info.is_fdt,
                    model: meta.model,
                    compatibles: meta.compatibles,
                    fixups: meta.fixups,
                    target_paths: meta.target_paths,
                    bootargs: meta.bootargs,
                    fstab: meta.fstab,
                });
            }

            VdevDtboReport {
                valid: !entries.is_empty() && invalid_fdt_indices.is_empty(),
                error: if entries.is_empty() {
                    "DTBO container has 0 overlay entries".into()
                } else if !invalid_fdt_indices.is_empty() {
                    format!("Invalid FDT header in DTBO entry index {:?}", invalid_fdt_indices)
                } else {
                    String::new()
                },
                version: hdr.version,
                page_size: hdr.page_size,
                total_size: hdr.total_size,
                file_size: hdr.file_size,
                entry_count: entries.len(),
                valid_fdt_count,
                invalid_fdt_indices,
                entries,
                all_compatibles,
                all_fixups,
                all_bootargs,
                all_fstab,
                embedded_vbmeta,
                avb_original_image_size,
            }
        }
    }
}

fn summarize_dtbo_dir(dir: &std::path::Path) -> Result<VdevDtboReport, String> {
    let dtb_dir = dir.join("dtb");
    let scan_dir = if dtb_dir.is_dir() { dtb_dir } else { dir.to_path_buf() };
    let mut dtb_paths: Vec<PathBuf> = std::fs::read_dir(&scan_dir)
        .map_err(|e| e.to_string())?
        .filter_map(|e| e.ok().map(|x| x.path()))
        .filter(|p| {
            p.is_file()
                && p.file_name()
                    .and_then(|n| n.to_str())
                    .is_some_and(|n| {
                        let low = n.to_ascii_lowercase();
                        low.ends_with(".dtb") || low.starts_with("dtbo.")
                    })
        })
        .collect();
    dtb_paths.sort();

    let mut entries = Vec::with_capacity(dtb_paths.len());
    let mut valid_fdt_count = 0usize;
    let mut invalid_fdt_indices = Vec::new();
    let mut all_compatibles = Vec::new();
    let mut all_fixups = Vec::new();
    let mut all_bootargs = Vec::new();
    let mut all_fstab = Vec::new();

    for (idx, p) in dtb_paths.iter().enumerate() {
        let raw = std::fs::read(p).map_err(|e| e.to_string())?;
        let is_fdt = raw.len() >= 4 && &raw[0..4] == b"\xd0\x0d\xfe\xed";
        let meta = if is_fdt {
            valid_fdt_count += 1;
            extract_dtbo_fdt_meta(&raw)
        } else {
            invalid_fdt_indices.push(idx);
            Default::default()
        };
        for c in &meta.compatibles {
            if !all_compatibles.contains(c) {
                all_compatibles.push(c.clone());
            }
        }
        for fx in &meta.fixups {
            if !all_fixups.contains(fx) {
                all_fixups.push(fx.clone());
            }
        }
        for ba in &meta.bootargs {
            if !all_bootargs.contains(ba) {
                all_bootargs.push(ba.clone());
            }
        }
        for fe in &meta.fstab {
            all_fstab.push(fe.clone());
        }
        entries.push(VdevDtboEntryReport {
            index: idx,
            id: 0,
            rev: 0,
            flags: 0,
            custom: [0; 4],
            dt_size: raw.len() as u32,
            is_fdt,
            model: meta.model,
            compatibles: meta.compatibles,
            fixups: meta.fixups,
            target_paths: meta.target_paths,
            bootargs: meta.bootargs,
            fstab: meta.fstab,
        });
    }

    Ok(VdevDtboReport {
        valid: !entries.is_empty() && invalid_fdt_indices.is_empty(),
        error: if entries.is_empty() {
            "Unpacked DTBO folder contains no .dtb entries".into()
        } else {
            String::new()
        },
        version: 0,
        page_size: 2048,
        total_size: 0,
        file_size: 0,
        entry_count: entries.len(),
        valid_fdt_count,
        invalid_fdt_indices,
        entries,
        all_compatibles,
        all_fixups,
        all_bootargs,
        all_fstab,
        embedded_vbmeta: None,
        avb_original_image_size: 0,
    })
}

#[pyfunction]
pub fn vdev_inspect_dtbo_json(py: Python<'_>, path: PathBuf) -> PyResult<String> {
    let report = py
        .detach(move || -> Result<VdevDtboReport, String> {
            if path.is_dir() {
                return summarize_dtbo_dir(&path);
            }
            let blob = std::fs::read(&path).map_err(|e| e.to_string())?;
            Ok(summarize_dtbo_bytes(&blob))
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&report).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (zip_path, entry_name, cache_dir=None))]
pub fn vdev_inspect_zip_dtbo_json(
    py: Python<'_>,
    zip_path: PathBuf,
    entry_name: String,
    cache_dir: Option<PathBuf>,
) -> PyResult<String> {
    py.detach(move || -> Result<String, String> {
        let mut cache_file: Option<PathBuf> = None;
        if let Some(dir) = cache_dir {
            if let Ok(meta) = std::fs::metadata(&zip_path) {
                let mtime = meta
                    .modified()
                    .ok()
                    .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                    .map(|d| d.as_nanos())
                    .unwrap_or(0);
                let safe: String = entry_name
                    .chars()
                    .map(|c| if c.is_ascii_alphanumeric() { c } else { '_' })
                    .collect();
                let key = format!("dtbo2_{}_{}_{}.json", meta.len(), mtime, safe);
                let p = dir.join(key);
                if let Ok(cached) = std::fs::read_to_string(&p) {
                    return Ok(cached);
                }
                let _ = std::fs::create_dir_all(&dir);
                cache_file = Some(p);
            }
        }
        let data = read_zip_entry_vec(&zip_path, &entry_name)?;
        let report = summarize_dtbo_bytes(&data);
        let json = serde_json::to_string(&report).map_err(|e| e.to_string())?;
        if let Some(p) = cache_file {
            let _ = std::fs::write(p, &json);
        }
        Ok(json)
    })
    .map_err(PyOSError::new_err)
}

#[pyfunction]
#[pyo3(signature = (path, image_size, hash_algo, salt_hex, expected_digest_hex, zip_entry=None))]
pub fn vdev_verify_avb_hash_json(
    py: Python<'_>,
    path: PathBuf,
    image_size: u64,
    hash_algo: String,
    salt_hex: String,
    expected_digest_hex: String,
    zip_entry: Option<String>,
) -> PyResult<String> {
    let res = py
        .detach(move || -> Result<HashtreeVerifyResult, String> {
            let t0 = std::time::Instant::now();
            let algo = hash_algo.to_ascii_lowercase();
            let salt = hex_decode(&salt_hex)?;
            let expected = expected_digest_hex.trim().to_ascii_lowercase();
            let data = if let Some(ent) = zip_entry {
                read_zip_entry_vec(&path, &ent)?
            } else {
                std::fs::read(&path).map_err(|e| e.to_string())?
            };
            let take_len = if image_size > 0 && (image_size as usize) <= data.len() {
                image_size as usize
            } else {
                data.len()
            };
            let computed = hash_salted_block(&algo, &salt, &data[..take_len])?;
            let computed_hex = hex_encode(&computed);
            let matched = if expected.is_empty() {
                None
            } else {
                Some(computed_hex == expected)
            };
            Ok(HashtreeVerifyResult {
                matched,
                computed_root: computed_hex,
                elapsed_ms: t0.elapsed().as_millis(),
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&res).map_err(|e| PyOSError::new_err(e.to_string()))
}
