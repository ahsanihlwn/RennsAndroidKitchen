use std::collections::BTreeSet;
use std::env;
use std::fs;
use std::ops::Deref;
use std::path::Path;

use memchr::memmem;
use memmap2::Mmap;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use sha1::{Digest as _, Sha1};
use sha2::{Sha256, Sha512};

use crate::compress::{compress_bytes, decompress_bytes};
use crate::cpio::{CpioArchive, CpioEntry};
use crate::dtb::{DtbMetadata, extract_dtb_metadata, find_embedded_dtb_offset};
use crate::format::*;

pub const HEADER_FILE: &str = "header";
pub const KERNEL_FILE: &str = "kernel";
pub const RAMDISK_FILE: &str = "ramdisk.cpio";
pub const SECOND_FILE: &str = "second";
pub const EXTRA_FILE: &str = "extra";
pub const KER_DTB_FILE: &str = "kernel_dtb";
pub const RECV_DTBO_FILE: &str = "recovery_dtbo";
pub const DTB_FILE: &str = "dtb";
pub const BOOTCONFIG_FILE: &str = "bootconfig";
pub const V4_META_FILE: &str = ".magiskboot_v4.json";
pub const NEW_BOOT: &str = "new-boot.img";

const BOOT_ARGS_SIZE: usize = 512;
const BOOT_EXTRA_ARGS_SIZE: usize = 1024;
const BOOT_ID_SIZE: usize = 32;
const VENDOR_BOOT_ARGS_SIZE: usize = 2048;
const VENDOR_RAMDISK_TABLE_ENTRY_SIZE: usize = 108;

const MTK_HDR_SIZE: usize = 512;
const DHTB_HDR_SIZE: usize = 512;
const BLOB_HDR_SIZE: usize = 104;
const AVB_FOOTER_SIZE: usize = 64;
const PADDING: usize = 15;

fn align_to(val: usize, align: usize) -> usize {
    if align <= 1 {
        return val;
    }
    (val + align - 1) / align * align
}

fn read_u32_le(buf: &[u8], off: usize) -> u32 {
    u32::from_le_bytes(buf[off..off + 4].try_into().unwrap())
}

fn write_u32_le(buf: &mut [u8], off: usize, v: u32) {
    buf[off..off + 4].copy_from_slice(&v.to_le_bytes());
}

fn read_u64_le(buf: &[u8], off: usize) -> u64 {
    u64::from_le_bytes(buf[off..off + 8].try_into().unwrap())
}

fn write_u64_le(buf: &mut [u8], off: usize, v: u64) {
    buf[off..off + 8].copy_from_slice(&v.to_le_bytes());
}

fn read_u32_be(buf: &[u8], off: usize) -> u32 {
    u32::from_be_bytes(buf[off..off + 4].try_into().unwrap())
}

fn write_u32_be(buf: &mut [u8], off: usize, v: u32) {
    buf[off..off + 4].copy_from_slice(&v.to_be_bytes());
}

fn read_u64_be(buf: &[u8], off: usize) -> u64 {
    u64::from_be_bytes(buf[off..off + 8].try_into().unwrap())
}

fn write_u64_be(buf: &mut [u8], off: usize, v: u64) {
    buf[off..off + 8].copy_from_slice(&v.to_be_bytes());
}

fn cstr_from_slice(s: &[u8]) -> String {
    let end = s.iter().position(|&b| b == 0).unwrap_or(s.len());
    String::from_utf8_lossy(&s[..end]).into_owned()
}

fn write_padded_str(dst: &mut [u8], s: &str) {
    dst.fill(0);
    let bytes = s.as_bytes();
    let max_len = dst.len().saturating_sub(1);
    let n = bytes.len().min(max_len);
    dst[..n].copy_from_slice(&bytes[..n]);
}

fn sha256_hex(data: &[u8]) -> String {
    let digest = Sha256::digest(data);
    let mut s = String::with_capacity(64);
    for b in digest {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum HeaderKind {
    V0,
    V1,
    V2,
    Pxa,
    V3,
    V4,
    VndV3,
    VndV4,
}

impl HeaderKind {
    fn struct_size(self) -> usize {
        match self {
            Self::V0 => 1632,
            Self::V1 => 1648,
            Self::V2 => 1660,
            Self::Pxa => 1640,
            Self::V3 => 1580,
            Self::V4 => 1584,
            Self::VndV3 => 2112,
            Self::VndV4 => 2128,
        }
    }

    fn is_vendor(self) -> bool {
        matches!(self, Self::VndV3 | Self::VndV4)
    }
}

#[derive(Debug, Clone)]
struct DynHeader {
    kind: HeaderKind,
    raw: Vec<u8>,
    kernel_dt_size: u32,
}

impl DynHeader {
    fn parse(addr: &[u8], is_vendor: bool) -> Result<Self, String> {
        let kind = if is_vendor {
            if addr.len() < 12 {
                return Err("Truncated vendor_boot header".into());
            }
            let ver = read_u32_le(addr, 8);
            if ver >= 4 {
                HeaderKind::VndV4
            } else {
                HeaderKind::VndV3
            }
        } else {
            if addr.len() < 44 {
                return Err("Truncated boot header".into());
            }
            let page_sz = read_u32_le(addr, 36);
            if page_sz >= 0x0200_0000 {
                HeaderKind::Pxa
            } else {
                match read_u32_le(addr, 40) {
                    1 => HeaderKind::V1,
                    2 => HeaderKind::V2,
                    3 => HeaderKind::V3,
                    4 => HeaderKind::V4,
                    _ => HeaderKind::V0,
                }
            }
        };

        let sz = kind.struct_size();
        if addr.len() < sz {
            return Err(format!("Boot image truncated before header size ({sz})"));
        }
        Ok(Self {
            kind,
            raw: addr[..sz].to_vec(),
            kernel_dt_size: 0,
        })
    }

    fn is_vendor(&self) -> bool {
        self.kind.is_vendor()
    }

    fn hdr_size(&self) -> usize {
        self.kind.struct_size()
    }

    fn page_size(&self) -> u32 {
        match self.kind {
            HeaderKind::V0 | HeaderKind::V1 | HeaderKind::V2 => read_u32_le(&self.raw, 36),
            HeaderKind::Pxa => read_u32_le(&self.raw, 44),
            HeaderKind::V3 | HeaderKind::V4 => 4096,
            HeaderKind::VndV3 | HeaderKind::VndV4 => read_u32_le(&self.raw, 12),
        }
    }

    fn hdr_space(&self) -> usize {
        let ps = self.page_size().max(1) as usize;
        if self.is_vendor() {
            let recorded = self.header_size() as usize;
            align_to(self.hdr_size().max(recorded), ps)
        } else {
            ps
        }
    }

    fn header_version(&self) -> u32 {
        match self.kind {
            HeaderKind::V0 | HeaderKind::Pxa => 0,
            HeaderKind::V1 | HeaderKind::V2 | HeaderKind::V3 | HeaderKind::V4 => {
                read_u32_le(&self.raw, 40)
            }
            HeaderKind::VndV3 | HeaderKind::VndV4 => read_u32_le(&self.raw, 8),
        }
    }

    fn kernel_size(&self) -> u32 {
        if self.is_vendor() {
            0
        } else {
            read_u32_le(&self.raw, 8)
        }
    }

    fn set_kernel_size(&mut self, v: u32) {
        if !self.is_vendor() {
            write_u32_le(&mut self.raw, 8, v);
        }
    }

    fn ramdisk_size(&self) -> u32 {
        match self.kind {
            HeaderKind::V3 | HeaderKind::V4 => read_u32_le(&self.raw, 12),
            HeaderKind::VndV3 | HeaderKind::VndV4 => read_u32_le(&self.raw, 24),
            _ => read_u32_le(&self.raw, 16),
        }
    }

    fn set_ramdisk_size(&mut self, v: u32) {
        match self.kind {
            HeaderKind::V3 | HeaderKind::V4 => write_u32_le(&mut self.raw, 12, v),
            HeaderKind::VndV3 | HeaderKind::VndV4 => write_u32_le(&mut self.raw, 24, v),
            _ => write_u32_le(&mut self.raw, 16, v),
        }
    }

    fn second_size(&self) -> u32 {
        match self.kind {
            HeaderKind::V0 | HeaderKind::V1 | HeaderKind::V2 | HeaderKind::Pxa => {
                read_u32_le(&self.raw, 24)
            }
            _ => 0,
        }
    }

    fn set_second_size(&mut self, v: u32) {
        if matches!(
            self.kind,
            HeaderKind::V0 | HeaderKind::V1 | HeaderKind::V2 | HeaderKind::Pxa
        ) {
            write_u32_le(&mut self.raw, 24, v);
        }
    }

    fn extra_size(&self) -> u32 {
        match self.kind {
            HeaderKind::V0 => read_u32_le(&self.raw, 40),
            HeaderKind::Pxa => read_u32_le(&self.raw, 32),
            _ => 0,
        }
    }

    fn set_extra_size(&mut self, v: u32) {
        match self.kind {
            HeaderKind::V0 => write_u32_le(&mut self.raw, 40, v),
            HeaderKind::Pxa => write_u32_le(&mut self.raw, 32, v),
            _ => {}
        }
    }

    fn os_version(&self) -> u32 {
        match self.kind {
            HeaderKind::V0 | HeaderKind::V1 | HeaderKind::V2 => read_u32_le(&self.raw, 44),
            HeaderKind::V3 | HeaderKind::V4 => read_u32_le(&self.raw, 16),
            _ => 0,
        }
    }

    fn set_os_version(&mut self, v: u32) {
        match self.kind {
            HeaderKind::V0 | HeaderKind::V1 | HeaderKind::V2 => write_u32_le(&mut self.raw, 44, v),
            HeaderKind::V3 | HeaderKind::V4 => write_u32_le(&mut self.raw, 16, v),
            _ => {}
        }
    }

    fn recovery_dtbo_size(&self) -> u32 {
        match self.kind {
            HeaderKind::V1 | HeaderKind::V2 => read_u32_le(&self.raw, 1632),
            _ => 0,
        }
    }

    fn set_recovery_dtbo_size(&mut self, v: u32) {
        if matches!(self.kind, HeaderKind::V1 | HeaderKind::V2) {
            write_u32_le(&mut self.raw, 1632, v);
        }
    }

    fn recovery_dtbo_offset(&self) -> u64 {
        match self.kind {
            HeaderKind::V1 | HeaderKind::V2 => read_u64_le(&self.raw, 1636),
            _ => 0,
        }
    }

    fn set_recovery_dtbo_offset(&mut self, v: u64) {
        if matches!(self.kind, HeaderKind::V1 | HeaderKind::V2) {
            write_u64_le(&mut self.raw, 1636, v);
        }
    }

    fn header_size(&self) -> u32 {
        match self.kind {
            HeaderKind::V1 | HeaderKind::V2 => read_u32_le(&self.raw, 1644),
            HeaderKind::V3 | HeaderKind::V4 => read_u32_le(&self.raw, 20),
            HeaderKind::VndV3 | HeaderKind::VndV4 => read_u32_le(&self.raw, 2096),
            _ => 0,
        }
    }

    fn set_header_size(&mut self, v: u32) {
        match self.kind {
            HeaderKind::V1 | HeaderKind::V2 => write_u32_le(&mut self.raw, 1644, v),
            HeaderKind::V3 | HeaderKind::V4 => write_u32_le(&mut self.raw, 20, v),
            HeaderKind::VndV3 | HeaderKind::VndV4 => write_u32_le(&mut self.raw, 2096, v),
            _ => {}
        }
    }

    fn dtb_size(&self) -> u32 {
        match self.kind {
            HeaderKind::V2 => read_u32_le(&self.raw, 1648),
            HeaderKind::VndV3 | HeaderKind::VndV4 => read_u32_le(&self.raw, 2100),
            _ => 0,
        }
    }

    fn set_dtb_size(&mut self, v: u32) {
        match self.kind {
            HeaderKind::V2 => write_u32_le(&mut self.raw, 1648, v),
            HeaderKind::VndV3 | HeaderKind::VndV4 => write_u32_le(&mut self.raw, 2100, v),
            _ => {}
        }
    }

    fn signature_size(&self) -> u32 {
        match self.kind {
            HeaderKind::V4 => read_u32_le(&self.raw, 1580),
            _ => 0,
        }
    }

    fn vendor_ramdisk_table_size(&self) -> u32 {
        match self.kind {
            HeaderKind::VndV4 => read_u32_le(&self.raw, 2112),
            _ => 0,
        }
    }

    fn set_vendor_ramdisk_table_size(&mut self, v: u32) {
        if self.kind == HeaderKind::VndV4 {
            write_u32_le(&mut self.raw, 2112, v);
        }
    }

    fn vendor_ramdisk_table_entry_num(&self) -> u32 {
        match self.kind {
            HeaderKind::VndV4 => read_u32_le(&self.raw, 2116),
            _ => 0,
        }
    }

    fn set_vendor_ramdisk_table_entry_num(&mut self, v: u32) {
        if self.kind == HeaderKind::VndV4 {
            write_u32_le(&mut self.raw, 2116, v);
        }
    }

    fn set_vendor_ramdisk_table_entry_size(&mut self, v: u32) {
        if self.kind == HeaderKind::VndV4 {
            write_u32_le(&mut self.raw, 2120, v);
        }
    }

    fn bootconfig_size(&self) -> u32 {
        match self.kind {
            HeaderKind::VndV4 => read_u32_le(&self.raw, 2124),
            _ => 0,
        }
    }

    fn set_bootconfig_size(&mut self, v: u32) {
        if self.kind == HeaderKind::VndV4 {
            write_u32_le(&mut self.raw, 2124, v);
        }
    }

    fn name(&self) -> Option<String> {
        match self.kind {
            HeaderKind::V0 | HeaderKind::V1 | HeaderKind::V2 => {
                Some(cstr_from_slice(&self.raw[48..64]))
            }
            HeaderKind::Pxa => Some(cstr_from_slice(&self.raw[48..72])),
            HeaderKind::VndV3 | HeaderKind::VndV4 => {
                Some(cstr_from_slice(&self.raw[2080..2096]))
            }
            _ => None,
        }
    }

    fn set_name(&mut self, name: &str) {
        match self.kind {
            HeaderKind::V0 | HeaderKind::V1 | HeaderKind::V2 => {
                write_padded_str(&mut self.raw[48..64], name);
            }
            HeaderKind::Pxa => {
                write_padded_str(&mut self.raw[48..72], name);
            }
            HeaderKind::VndV3 | HeaderKind::VndV4 => {
                write_padded_str(&mut self.raw[2080..2096], name);
            }
            _ => {}
        }
    }

    fn cmdline(&self) -> String {
        match self.kind {
            HeaderKind::V0 | HeaderKind::V1 | HeaderKind::V2 => {
                let main = cstr_from_slice(&self.raw[64..64 + BOOT_ARGS_SIZE]);
                let extra = cstr_from_slice(&self.raw[608..608 + BOOT_EXTRA_ARGS_SIZE]);
                format!("{main}{extra}")
            }
            HeaderKind::Pxa => {
                let main = cstr_from_slice(&self.raw[72..72 + BOOT_ARGS_SIZE]);
                let extra = cstr_from_slice(&self.raw[616..616 + BOOT_EXTRA_ARGS_SIZE]);
                format!("{main}{extra}")
            }
            HeaderKind::V3 | HeaderKind::V4 => {
                cstr_from_slice(&self.raw[44..44 + BOOT_ARGS_SIZE + BOOT_EXTRA_ARGS_SIZE])
            }
            HeaderKind::VndV3 | HeaderKind::VndV4 => {
                cstr_from_slice(&self.raw[28..28 + VENDOR_BOOT_ARGS_SIZE])
            }
        }
    }

    fn set_cmdline(&mut self, val: &str) {
        let bytes = val.as_bytes();
        match self.kind {
            HeaderKind::V0 | HeaderKind::V1 | HeaderKind::V2 => {
                self.raw[64..64 + BOOT_ARGS_SIZE].fill(0);
                self.raw[608..608 + BOOT_EXTRA_ARGS_SIZE].fill(0);
                let main_len = bytes.len().min(BOOT_ARGS_SIZE);
                self.raw[64..64 + main_len].copy_from_slice(&bytes[..main_len]);
                if bytes.len() > BOOT_ARGS_SIZE {
                    let rest = &bytes[BOOT_ARGS_SIZE..];
                    let extra_len = rest.len().min(BOOT_EXTRA_ARGS_SIZE);
                    self.raw[608..608 + extra_len].copy_from_slice(&rest[..extra_len]);
                }
            }
            HeaderKind::Pxa => {
                self.raw[72..72 + BOOT_ARGS_SIZE].fill(0);
                self.raw[616..616 + BOOT_EXTRA_ARGS_SIZE].fill(0);
                let main_len = bytes.len().min(BOOT_ARGS_SIZE);
                self.raw[72..72 + main_len].copy_from_slice(&bytes[..main_len]);
                if bytes.len() > BOOT_ARGS_SIZE {
                    let rest = &bytes[BOOT_ARGS_SIZE..];
                    let extra_len = rest.len().min(BOOT_EXTRA_ARGS_SIZE);
                    self.raw[616..616 + extra_len].copy_from_slice(&rest[..extra_len]);
                }
            }
            HeaderKind::V3 | HeaderKind::V4 => {
                let dst = &mut self.raw[44..44 + BOOT_ARGS_SIZE + BOOT_EXTRA_ARGS_SIZE];
                dst.fill(0);
                let n = bytes.len().min(dst.len());
                dst[..n].copy_from_slice(&bytes[..n]);
            }
            HeaderKind::VndV3 | HeaderKind::VndV4 => {
                let dst = &mut self.raw[28..28 + VENDOR_BOOT_ARGS_SIZE];
                dst.fill(0);
                let n = bytes.len().min(dst.len());
                dst[..n].copy_from_slice(&bytes[..n]);
            }
        }
    }

    fn id(&self) -> Option<&[u8]> {
        match self.kind {
            HeaderKind::V0 | HeaderKind::V1 | HeaderKind::V2 => Some(&self.raw[576..608]),
            HeaderKind::Pxa => Some(&self.raw[584..616]),
            _ => None,
        }
    }

    fn id_mut(&mut self) -> Option<&mut [u8]> {
        match self.kind {
            HeaderKind::V0 | HeaderKind::V1 | HeaderKind::V2 => Some(&mut self.raw[576..608]),
            HeaderKind::Pxa => Some(&mut self.raw[584..616]),
            _ => None,
        }
    }

    fn format_os_version(os_ver: u32) -> (String, String) {
        let version = os_ver >> 11;
        let patch_level = os_ver & 0x7ff;
        let a = (version >> 14) & 0x7f;
        let b = (version >> 7) & 0x7f;
        let c = version & 0x7f;
        let y = (patch_level >> 4) + 2000;
        let m = patch_level & 0xf;
        (format!("{a}.{b}.{c}"), format!("{y}-{m:02}"))
    }

    fn print(&self, v4_entries_count: usize) {
        let ver = self.header_version();
        eprintln!("{:<PADDING$} [{}]", "HEADER_VER", ver);
        if !self.is_vendor() {
            eprintln!("{:<PADDING$} [{}]", "KERNEL_SZ", self.kernel_size());
        }
        eprintln!("{:<PADDING$} [{}]", "RAMDISK_SZ", self.ramdisk_size());
        if ver < 3 {
            eprintln!("{:<PADDING$} [{}]", "SECOND_SZ", self.second_size());
        }
        if ver == 0 && !self.is_vendor() {
            eprintln!("{:<PADDING$} [{}]", "EXTRA_SZ", self.extra_size());
        }
        if (ver == 1 || ver == 2) && !self.is_vendor() {
            eprintln!(
                "{:<PADDING$} [{}]",
                "RECOV_DTBO_SZ",
                self.recovery_dtbo_size()
            );
        }
        if ver == 2 || self.is_vendor() {
            eprintln!("{:<PADDING$} [{}]", "DTB_SZ", self.dtb_size());
        }
        if self.kind == HeaderKind::VndV4 {
            eprintln!("{:<PADDING$} [{}]", "V4_ENTRIES", v4_entries_count);
            eprintln!("{:<PADDING$} [{}]", "BOOTCONFIG_SZ", self.bootconfig_size());
        }
        let os_ver = self.os_version();
        if os_ver != 0 {
            let (ver_str, patch_str) = Self::format_os_version(os_ver);
            eprintln!("{:<PADDING$} [{ver_str}]", "OS_VERSION");
            eprintln!("{:<PADDING$} [{patch_str}]", "OS_PATCH_LEVEL");
        }
        eprintln!("{:<PADDING$} [{}]", "PAGESIZE", self.page_size());
        if let Some(name) = self.name() {
            eprintln!("{:<PADDING$} [{name}]", "NAME");
        }
        eprintln!("{:<PADDING$} [{}]", "CMDLINE", self.cmdline());
        if let Some(id) = self.id() {
            let mut hex = String::with_capacity(64);
            for b in id {
                hex.push_str(&format!("{b:02x}"));
            }
            eprintln!("{:<PADDING$} [{hex}]", "CHECKSUM");
        }
    }

    fn dump_hdr_file(&self) -> Result<(), String> {
        let mut content = String::new();
        if let Some(name) = self.name() {
            content.push_str(&format!("name={name}\n"));
        }
        content.push_str(&format!("cmdline={}\n", self.cmdline()));
        let os_ver = self.os_version();
        if os_ver != 0 {
            let (ver_str, patch_str) = Self::format_os_version(os_ver);
            content.push_str(&format!("os_version={ver_str}\n"));
            content.push_str(&format!("os_patch_level={patch_str}\n"));
        }
        fs::write(HEADER_FILE, content)
            .map_err(|e| format!("Failed writing [{HEADER_FILE}]: {e}"))
    }

    fn load_hdr_file(&mut self) -> Result<(), String> {
        let text = fs::read_to_string(HEADER_FILE)
            .map_err(|e| format!("Failed reading [{HEADER_FILE}]: {e}"))?;
        for raw_line in text.lines() {
            let line = raw_line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let Some((k, v)) = line.split_once('=') else {
                continue;
            };
            match k {
                "name" => self.set_name(v),
                "cmdline" => self.set_cmdline(v),
                "os_version" => {
                    let nums: Vec<u32> = v.split('.').filter_map(|p| p.parse().ok()).collect();
                    if nums.len() == 3 {
                        let patch_level = self.os_version() & 0x7ff;
                        let packed =
                            (((nums[0] << 14) | (nums[1] << 7) | nums[2]) << 11) | patch_level;
                        self.set_os_version(packed);
                    }
                }
                "os_patch_level" => {
                    let nums: Vec<u32> = v.split('-').filter_map(|p| p.parse().ok()).collect();
                    if nums.len() == 2 && nums[0] >= 2000 {
                        let os_ver = self.os_version() >> 11;
                        let y = nums[0] - 2000;
                        let m = nums[1];
                        self.set_os_version((os_ver << 11) | (y << 4) | m);
                    }
                }
                _ => {}
            }
        }
        Ok(())
    }
}

#[derive(Debug, Default)]
struct BootFlags {
    mtk_kernel: bool,
    mtk_ramdisk: bool,
    chromeos: bool,
    dhtb: bool,
    seandroid: bool,
    lg_bump: bool,
    sha256: bool,
    blob: bool,
    nookhd: bool,
    acclaim: bool,
    amonet: bool,
    avb: bool,
    zimage_kernel: bool,
}

#[derive(Debug, Clone)]
struct VendorRamdiskTableEntry {
    index: usize,
    ramdisk_size: u32,
    ramdisk_offset: u32,
    ramdisk_type: u32,
    ramdisk_name: String,
    board_id: [u8; 64],
}

impl VendorRamdiskTableEntry {
    fn parse(index: usize, buf: &[u8]) -> Self {
        let ramdisk_size = read_u32_le(buf, 0);
        let ramdisk_offset = read_u32_le(buf, 4);
        let ramdisk_type = read_u32_le(buf, 8);
        let ramdisk_name = cstr_from_slice(&buf[12..44]);
        let mut board_id = [0u8; 64];
        board_id.copy_from_slice(&buf[44..108]);
        Self {
            index,
            ramdisk_size,
            ramdisk_offset,
            ramdisk_type,
            ramdisk_name,
            board_id,
        }
    }

    fn encode(&self) -> [u8; VENDOR_RAMDISK_TABLE_ENTRY_SIZE] {
        let mut out = [0u8; VENDOR_RAMDISK_TABLE_ENTRY_SIZE];
        write_u32_le(&mut out, 0, self.ramdisk_size);
        write_u32_le(&mut out, 4, self.ramdisk_offset);
        write_u32_le(&mut out, 8, self.ramdisk_type);
        let nb = self.ramdisk_name.as_bytes();
        let n = nb.len().min(32);
        out[12..12 + n].copy_from_slice(&nb[..n]);
        out[44..108].copy_from_slice(&self.board_id);
        out
    }
}

#[derive(Debug, Serialize, Deserialize)]
struct V4EntryMeta {
    index: usize,
    ramdisk_type: u32,
    ramdisk_name: String,
    board_id_hex: String,
    fmt: String,
    orig_size: u32,
    orig_offset: u32,
    frag_sha256: String,
    entry_paths: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize)]
struct V4Meta {
    entries: Vec<V4EntryMeta>,
    merged_sha256: String,
}

enum BootBacking {
    Mmap(Mmap),
    Owned(Vec<u8>),
}

impl Deref for BootBacking {
    type Target = [u8];
    fn deref(&self) -> &[u8] {
        match self {
            BootBacking::Mmap(m) => m,
            BootBacking::Owned(v) => v,
        }
    }
}

struct ParsedBootImage {
    _backing: BootBacking,
    hdr: DynHeader,
    flags: BootFlags,
    hdr_file_offset: usize,
    k_fmt: Format,
    r_fmt: Format,
    e_fmt: Format,
    k_mtk_hdr: Option<[u8; MTK_HDR_SIZE]>,
    r_mtk_hdr: Option<[u8; MTK_HDR_SIZE]>,
    z_hdr_range: Option<(usize, usize)>,
    z_tail_range: Option<(usize, usize)>,
    kernel_range: (usize, usize),
    kernel_dtb_range: (usize, usize),
    ramdisk_range: (usize, usize),
    second_range: (usize, usize),
    extra_range: (usize, usize),
    recovery_dtbo_range: (usize, usize),
    dtb_range: (usize, usize),
    ignore_range: (usize, usize),
    bootconfig_range: (usize, usize),
    v4_entries: Vec<VendorRamdiskTableEntry>,
    avb_footer_offset: Option<usize>,
    vbmeta_range: Option<(usize, usize)>,
}

impl ParsedBootImage {
    fn slice(&self, r: (usize, usize)) -> &[u8] {
        if r.1 == 0 {
            &[]
        } else {
            &self._backing[r.0..r.0 + r.1]
        }
    }

    fn raw(&self) -> &[u8] {
        &self._backing
    }
}

fn parse_boot_image(path: &str) -> Result<ParsedBootImage, String> {
    eprintln!("Parsing image: [{path}]");
    let file = fs::File::open(path).map_err(|e| format!("Failed opening [{path}]: {e}"))?;
    let mmap = unsafe { Mmap::map(&file) }.map_err(|e| format!("Failed mapping [{path}]: {e}"))?;
    parse_boot_backing(BootBacking::Mmap(mmap), path)
}

fn parse_boot_bytes(data: Vec<u8>, label: &str) -> Result<ParsedBootImage, String> {
    parse_boot_backing(BootBacking::Owned(data), label)
}

fn parse_boot_backing(backing: BootBacking, label: &str) -> Result<ParsedBootImage, String> {
    let data: &[u8] = &backing;

    let mut flags = BootFlags::default();
    let mut pos = 0usize;
    let mut found_addr = None;
    let mut found_type = Format::Unknown;

    while pos < data.len() {
        let fmt = detect_format(&data[pos..]);
        match fmt {
            Format::ChromeOs => {
                flags.chromeos = true;
                pos += 65536;
            }
            Format::Dhtb => {
                flags.dhtb = true;
                flags.seandroid = true;
                eprintln!("DHTB_HDR");
                pos += DHTB_HDR_SIZE;
            }
            Format::Blob => {
                flags.blob = true;
                eprintln!("TEGRA_BLOB");
                pos += BLOB_HDR_SIZE;
            }
            Format::Aosp | Format::AospVendor => {
                found_addr = Some(pos);
                found_type = fmt;
                break;
            }
            _ => {
                pos += 1;
            }
        }
    }

    let mut base_off = found_addr.ok_or_else(|| format!("No valid boot magic found in [{label}]"))?;
    let is_vendor = found_type == Format::AospVendor;
    let mut hdr = if is_vendor {
        eprintln!("VENDOR_BOOT_HDR");
        DynHeader::parse(&data[base_off..], true)?
    } else {
        let mut h = DynHeader::parse(&data[base_off..], false)?;
        if h.kind == HeaderKind::Pxa {
            eprintln!("PXA_BOOT_HDR");
            h
        } else if base_off + AMONET_MICROLOADER_SZ + 8 <= data.len()
            && memmem::find(&data[base_off..base_off + AMONET_MICROLOADER_SZ], AMONET_MICROLOADER_MAGIC).is_some()
            && &data[base_off + AMONET_MICROLOADER_SZ..base_off + AMONET_MICROLOADER_SZ + 8] == BOOT_MAGIC
        {
            flags.amonet = true;
            eprintln!("AMONET_MICROLOADER");
            let shifted = &data[base_off + AMONET_MICROLOADER_SZ..];
            h = DynHeader::parse(shifted, false)?;
            h
        } else {
            let cmd = h.cmdline();
            if [
                NOOKHD_RL_MAGIC,
                NOOKHD_GL_MAGIC,
                NOOKHD_GR_MAGIC,
                NOOKHD_EB_MAGIC,
                NOOKHD_ER_MAGIC,
            ]
            .iter()
            .any(|m| cmd.as_bytes().starts_with(m))
            {
                flags.nookhd = true;
                eprintln!("NOOKHD_LOADER");
                base_off += NOOKHD_PRE_HEADER_SZ;
                DynHeader::parse(&data[base_off..], false)?
            } else if h.name().is_some_and(|n| n.as_bytes().starts_with(ACCLAIM_MAGIC)) {
                flags.acclaim = true;
                eprintln!("ACCLAIM_LOADER");
                base_off += ACCLAIM_PRE_HEADER_SZ;
                DynHeader::parse(&data[base_off..], false)?
            } else {
                h
            }
        }
    };

    if let Some(id) = hdr.id() {
        if id[24..BOOT_ID_SIZE].iter().any(|&b| b != 0) {
            flags.sha256 = true;
        }
    }

    let data_len = data.len();
    let page_sz = hdr.page_size().max(1) as usize;
    let mut off = base_off + hdr.hdr_space();

    let take_block = |off: &mut usize, sz: u32| -> (usize, usize) {
        let sz = sz as usize;
        let start = (*off).min(data_len);
        let avail = sz.min(data_len - start);
        *off += align_to(sz, page_sz);
        (start, avail)
    };

    let mut kernel_range = take_block(&mut off, hdr.kernel_size());
    let mut ramdisk_range = take_block(&mut off, hdr.ramdisk_size());
    let second_range = take_block(&mut off, hdr.second_size());
    let extra_range = take_block(&mut off, hdr.extra_size());
    let recovery_dtbo_range = take_block(&mut off, hdr.recovery_dtbo_size());
    let dtb_range = take_block(&mut off, hdr.dtb_size());

    let ignore_start = off.min(data_len);
    let mut ignore_size = 0usize;
    if hdr.signature_size() > 0 {
        let blk = align_to(hdr.signature_size() as usize, page_sz);
        ignore_size += blk;
        off += blk;
    }

    let v4_table_range = take_block(&mut off, hdr.vendor_ramdisk_table_size());
    let bootconfig_range = take_block(&mut off, hdr.bootconfig_size());

    let mut v4_entries = Vec::new();
    if hdr.kind == HeaderKind::VndV4 {
        if hdr.vendor_ramdisk_table_entry_num() > 0 {
            let entry_num = hdr.vendor_ramdisk_table_entry_num() as usize;
            let tbl = &data[v4_table_range.0..v4_table_range.0 + v4_table_range.1];
            for i in 0..entry_num {
                let start = i * VENDOR_RAMDISK_TABLE_ENTRY_SIZE;
                if start + VENDOR_RAMDISK_TABLE_ENTRY_SIZE <= tbl.len() {
                    v4_entries.push(VendorRamdiskTableEntry::parse(
                        i,
                        &tbl[start..start + VENDOR_RAMDISK_TABLE_ENTRY_SIZE],
                    ));
                }
            }
        }
    } else {
        if v4_table_range.1 > 0 {
            ignore_size += align_to(v4_table_range.1, page_sz);
        }
        if bootconfig_range.1 > 0 {
            ignore_size += align_to(bootconfig_range.1, page_sz);
        }
    }

    hdr.print(v4_entries.len());

    let mut kernel_dtb_range = (0, 0);
    let mut k_fmt = Format::Unknown;
    let mut k_mtk_hdr = None;
    let mut z_hdr_range = None;
    let mut z_tail_range = None;

    if kernel_range.1 > 0 {
        let kslice = &data[kernel_range.0..kernel_range.0 + kernel_range.1];
        if let Some(dtb_off) = find_embedded_dtb_offset(kslice) {
            if dtb_off > 0 {
                kernel_dtb_range = (kernel_range.0 + dtb_off, kernel_range.1 - dtb_off);
                kernel_range.1 = dtb_off;
                hdr.kernel_dt_size = kernel_dtb_range.1 as u32;
                hdr.set_kernel_size(dtb_off as u32);
                eprintln!("{:<PADDING$} [{}]", "KERNEL_DTB_SZ", hdr.kernel_dt_size);
            }
        }

        let kslice = &data[kernel_range.0..kernel_range.0 + kernel_range.1];
        k_fmt = detect_format_lg(kslice);
        if k_fmt == Format::Mtk && kslice.len() >= MTK_HDR_SIZE {
            eprintln!("MTK_KERNEL_HDR");
            flags.mtk_kernel = true;
            let mut m = [0u8; MTK_HDR_SIZE];
            m.copy_from_slice(&kslice[..MTK_HDR_SIZE]);
            eprintln!("{:<PADDING$} [{}]", "SIZE", read_u32_le(&m, 4));
            eprintln!("{:<PADDING$} [{}]", "NAME", cstr_from_slice(&m[8..40]));
            k_mtk_hdr = Some(m);
            kernel_range.0 += MTK_HDR_SIZE;
            kernel_range.1 -= MTK_HDR_SIZE;
            hdr.set_kernel_size(kernel_range.1 as u32);
            k_fmt = detect_format_lg(&data[kernel_range.0..kernel_range.0 + kernel_range.1]);
        }

        if k_fmt == Format::ZImage {
            let kslice = &data[kernel_range.0..kernel_range.0 + kernel_range.1];
            if let Some(gzip_off) = memmem::find(kslice, b"\x1f\x8b\x08\x00") {
                eprintln!("ZIMAGE_KERNEL");
                let z_start = read_u32_le(kslice, 40);
                let z_end = read_u32_le(kslice, 44);
                let zimage_size = z_end.saturating_sub(z_start) as usize;
                let mut piggy_end = zimage_size;
                if zimage_size >= 64 && zimage_size <= kslice.len() {
                    for i in (0..16).rev() {
                        let val = read_u32_le(kslice, zimage_size - 64 + i * 4) as usize;
                        if val > zimage_size.saturating_sub(0xFF) && val < zimage_size {
                            piggy_end = val;
                            break;
                        }
                    }
                }
                if piggy_end == zimage_size || piggy_end <= gzip_off {
                    eprintln!("! Could not find end of zImage piggy, keeping raw kernel");
                } else {
                    flags.zimage_kernel = true;
                    z_hdr_range = Some((kernel_range.0, gzip_off));
                    z_tail_range = Some((kernel_range.0 + piggy_end, kernel_range.1 - piggy_end));
                    kernel_range.0 += gzip_off;
                    kernel_range.1 = piggy_end - gzip_off;
                    hdr.set_kernel_size(kernel_range.1 as u32);
                    k_fmt = detect_format_lg(&data[kernel_range.0..kernel_range.0 + kernel_range.1]);
                }
            } else {
                eprintln!("! Could not find zImage gzip piggy, keeping raw kernel");
            }
        }
        eprintln!("{:<PADDING$} [{}]", "KERNEL_FMT", k_fmt.name());
    }

    let mut r_fmt = Format::Unknown;
    let mut r_mtk_hdr = None;
    if ramdisk_range.1 > 0 {
        if !v4_entries.is_empty() {
            for entry in &v4_entries {
                let s = ramdisk_range.0 + entry.ramdisk_offset as usize;
                let e = (s + entry.ramdisk_size as usize).min(data.len());
                let fmt = detect_format_lg(&data[s..e]);
                eprintln!(
                    "V4_RAMDISK[{}]  name=[{}] type={} sz={} fmt=[{}]",
                    entry.index,
                    entry.ramdisk_name,
                    entry.ramdisk_type,
                    entry.ramdisk_size,
                    fmt.name()
                );
            }
            if v4_entries.len() == 1 {
                let entry = &v4_entries[0];
                let s = ramdisk_range.0 + entry.ramdisk_offset as usize;
                let e = (s + entry.ramdisk_size as usize).min(data.len());
                r_fmt = detect_format_lg(&data[s..e]);
            }
        } else {
            let rslice = &data[ramdisk_range.0..ramdisk_range.0 + ramdisk_range.1];
            r_fmt = detect_format_lg(rslice);
            if r_fmt == Format::Mtk && rslice.len() >= MTK_HDR_SIZE {
                eprintln!("MTK_RAMDISK_HDR");
                flags.mtk_ramdisk = true;
                let mut m = [0u8; MTK_HDR_SIZE];
                m.copy_from_slice(&rslice[..MTK_HDR_SIZE]);
                eprintln!("{:<PADDING$} [{}]", "SIZE", read_u32_le(&m, 4));
                eprintln!("{:<PADDING$} [{}]", "NAME", cstr_from_slice(&m[8..40]));
                r_mtk_hdr = Some(m);
                ramdisk_range.0 += MTK_HDR_SIZE;
                ramdisk_range.1 -= MTK_HDR_SIZE;
                hdr.set_ramdisk_size(ramdisk_range.1 as u32);
                r_fmt = detect_format_lg(&data[ramdisk_range.0..ramdisk_range.0 + ramdisk_range.1]);
            }
            eprintln!("{:<PADDING$} [{}]", "RAMDISK_FMT", r_fmt.name());
        }
    }

    let mut e_fmt = Format::Unknown;
    if extra_range.1 > 0 {
        e_fmt = detect_format_lg(&data[extra_range.0..extra_range.0 + extra_range.1]);
        eprintln!("{:<PADDING$} [{}]", "EXTRA_FMT", e_fmt.name());
    }

    let mut avb_footer_offset = None;
    let mut vbmeta_range = None;

    if off < data.len() {
        let tail = &data[off..];
        if tail.len() >= 16 && tail.starts_with(SEANDROID_MAGIC) {
            eprintln!("SAMSUNG_SEANDROID");
            flags.seandroid = true;
        } else if tail.len() >= 16 && tail.starts_with(LG_BUMP_MAGIC) {
            eprintln!("LG_BUMP_IMAGE");
            flags.lg_bump = true;
        }

        if tail.len() >= AVB_FOOTER_SIZE {
            let footer_off = data.len() - AVB_FOOTER_SIZE;
            let footer = &data[footer_off..];
            if footer.starts_with(AVB_FOOTER_MAGIC) {
                let vb_off = read_u64_be(footer, 20) as usize;
                let vb_sz = read_u64_be(footer, 28) as usize;
                let abs_vb_off = base_off + vb_off;
                if abs_vb_off + vb_sz <= data.len() && data[abs_vb_off..].starts_with(AVB_MAGIC) {
                    eprintln!("VBMETA");
                    flags.avb = true;
                    avb_footer_offset = Some(footer_off);
                    vbmeta_range = Some((abs_vb_off, vb_sz));
                    if hdr.kind == HeaderKind::V4
                        && !hdr.is_vendor()
                        && hdr.signature_size() == 0
                        && ignore_size == 0
                        && abs_vb_off > off
                        && abs_vb_off - off <= 65536
                        && data[off..].starts_with(AVB_MAGIC)
                    {
                        ignore_size = abs_vb_off - off;
                    }
                }
            }
        }
    }

    Ok(ParsedBootImage {
        _backing: backing,
        hdr,
        flags,
        hdr_file_offset: base_off,
        k_fmt,
        r_fmt,
        e_fmt,
        k_mtk_hdr,
        r_mtk_hdr,
        z_hdr_range,
        z_tail_range,
        kernel_range,
        kernel_dtb_range,
        ramdisk_range,
        second_range,
        extra_range,
        recovery_dtbo_range,
        dtb_range,
        ignore_range: (ignore_start, ignore_size.min(data_len - ignore_start)),
        bootconfig_range,
        v4_entries,
        avb_footer_offset,
        vbmeta_range,
    })
}

fn dump_or_decomp(slice: &[u8], fmt: Format, skip_decomp: bool, out_path: &str) -> Result<(), String> {
    if slice.is_empty() {
        return Ok(());
    }
    if !skip_decomp && fmt.is_compressed() {
        let dec = decompress_bytes(fmt, slice)?;
        fs::write(out_path, dec).map_err(|e| format!("Failed writing [{out_path}]: {e}"))
    } else {
        fs::write(out_path, slice).map_err(|e| format!("Failed writing [{out_path}]: {e}"))
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BootHeaderInfo {
    pub header_version: u32,
    pub is_vendor: bool,
    pub kernel_size: u32,
    pub ramdisk_size: u32,
    pub second_size: u32,
    pub extra_size: u32,
    pub recovery_dtbo_size: u32,
    pub dtb_size: u32,
    pub bootconfig_size: u32,
    pub v4_entries: usize,
    pub page_size: u32,
    pub os_version: String,
    pub os_patch_level: String,
    pub name: String,
    pub cmdline: String,
    pub kernel_fmt: String,
    pub ramdisk_fmt: String,
    pub has_avb: bool,
}

impl ParsedBootImage {
    fn header_info(&self) -> BootHeaderInfo {
        let os_ver = self.hdr.os_version();
        let (os_version, os_patch_level) = if os_ver != 0 {
            DynHeader::format_os_version(os_ver)
        } else {
            (String::new(), String::new())
        };
        BootHeaderInfo {
            header_version: self.hdr.header_version(),
            is_vendor: self.hdr.is_vendor(),
            kernel_size: self.hdr.kernel_size(),
            ramdisk_size: self.hdr.ramdisk_size(),
            second_size: self.hdr.second_size(),
            extra_size: self.hdr.extra_size(),
            recovery_dtbo_size: self.hdr.recovery_dtbo_size(),
            dtb_size: self.hdr.dtb_size(),
            bootconfig_size: self.hdr.bootconfig_size(),
            v4_entries: self.v4_entries.len(),
            page_size: self.hdr.page_size(),
            os_version,
            os_patch_level,
            name: self.hdr.name().unwrap_or_default(),
            cmdline: self.hdr.cmdline(),
            kernel_fmt: self.k_fmt.name().to_string(),
            ramdisk_fmt: self.r_fmt.name().to_string(),
            has_avb: self.flags.avb,
        }
    }
}

pub struct ExtractedRamdisk {
    pub name: String,
    pub ramdisk_type: u32,
    pub format: String,
    pub archive: CpioArchive,
}

pub struct BootContents {
    pub header: BootHeaderInfo,
    pub bootconfig: String,
    pub kernel_bytes: Vec<u8>,
    pub dtb_meta: DtbMetadata,
    pub kernel_dtb_meta: DtbMetadata,
    pub ramdisks: Vec<ExtractedRamdisk>,
    pub vbmeta_bytes: Option<Vec<u8>>,
}

fn extract_boot_contents(boot: ParsedBootImage) -> Result<BootContents, String> {
    let header = boot.header_info();
    let bootconfig = if boot.bootconfig_range.1 > 0 {
        String::from_utf8_lossy(boot.slice(boot.bootconfig_range))
            .trim_end_matches('\0')
            .to_string()
    } else {
        String::new()
    };

    let kernel_bytes = if boot.kernel_range.1 > 0 {
        let kslice = boot.slice(boot.kernel_range);
        if boot.k_fmt.is_compressed() {
            decompress_bytes(boot.k_fmt, kslice).unwrap_or_else(|_| kslice.to_vec())
        } else {
            kslice.to_vec()
        }
    } else {
        Vec::new()
    };

    let dtb_meta = if boot.dtb_range.1 > 0 {
        extract_dtb_metadata(boot.slice(boot.dtb_range))
    } else {
        DtbMetadata::default()
    };
    let kernel_dtb_meta = if boot.kernel_dtb_range.1 > 0 {
        extract_dtb_metadata(boot.slice(boot.kernel_dtb_range))
    } else {
        DtbMetadata::default()
    };

    let mut ramdisks = Vec::new();
    if boot.ramdisk_range.1 > 0 {
        let r_base = boot.slice(boot.ramdisk_range);
        if !boot.v4_entries.is_empty() {
            for entry in &boot.v4_entries {
                let s = entry.ramdisk_offset as usize;
                let e = s.saturating_add(entry.ramdisk_size as usize).min(r_base.len());
                if s >= e {
                    continue;
                }
                let frag = &r_base[s..e];
                let fmt = detect_format_lg(frag);
                let raw = if fmt.is_compressed() {
                    decompress_bytes(fmt, frag).unwrap_or_default()
                } else {
                    frag.to_vec()
                };
                if let Ok(archive) = CpioArchive::from_bytes(&raw) {
                    ramdisks.push(ExtractedRamdisk {
                        name: entry.ramdisk_name.clone(),
                        ramdisk_type: entry.ramdisk_type,
                        format: fmt.name().to_string(),
                        archive,
                    });
                }
            }
        } else {
            let fmt = boot.r_fmt;
            let raw = if fmt.is_compressed() {
                decompress_bytes(fmt, r_base).unwrap_or_default()
            } else {
                r_base.to_vec()
            };
            if let Ok(archive) = CpioArchive::from_bytes(&raw) {
                ramdisks.push(ExtractedRamdisk {
                    name: String::from("ramdisk"),
                    ramdisk_type: 1,
                    format: fmt.name().to_string(),
                    archive,
                });
            }
        }
    }

    let vbmeta_bytes = boot.vbmeta_range.map(|r| boot.slice(r).to_vec());

    Ok(BootContents {
        header,
        bootconfig,
        kernel_bytes,
        dtb_meta,
        kernel_dtb_meta,
        ramdisks,
        vbmeta_bytes,
    })
}

pub fn inspect_boot_contents_bytes(data: Vec<u8>) -> Result<BootContents, String> {
    let boot = parse_boot_bytes(data, "<memory>")?;
    extract_boot_contents(boot)
}

pub fn inspect_boot_contents_file(path: &str) -> Result<BootContents, String> {
    let boot = parse_boot_image(path)?;
    extract_boot_contents(boot)
}

pub fn inspect_boot_image(image: &str) -> Result<BootHeaderInfo, String> {
    let boot = parse_boot_image(image)?;
    Ok(boot.header_info())
}

pub fn cmd_unpack(image: &str, skip_decomp: bool, dump_hdr: bool) -> Result<i32, String> {
    let boot = parse_boot_image(image)?;

    if dump_hdr {
        boot.hdr.dump_hdr_file()?;
    }

    dump_or_decomp(
        boot.slice(boot.kernel_range),
        boot.k_fmt,
        skip_decomp,
        KERNEL_FILE,
    )?;

    if boot.kernel_dtb_range.1 > 0 {
        fs::write(KER_DTB_FILE, boot.slice(boot.kernel_dtb_range))
            .map_err(|e| format!("Failed writing [{KER_DTB_FILE}]: {e}"))?;
    }

    if !boot.v4_entries.is_empty() {
        unpack_v4_ramdisks(&boot, skip_decomp)?;
    } else {
        dump_or_decomp(
            boot.slice(boot.ramdisk_range),
            boot.r_fmt,
            skip_decomp,
            RAMDISK_FILE,
        )?;
    }

    if boot.second_range.1 > 0 {
        fs::write(SECOND_FILE, boot.slice(boot.second_range))
            .map_err(|e| format!("Failed writing [{SECOND_FILE}]: {e}"))?;
    }

    dump_or_decomp(
        boot.slice(boot.extra_range),
        boot.e_fmt,
        skip_decomp,
        EXTRA_FILE,
    )?;

    if boot.recovery_dtbo_range.1 > 0 {
        fs::write(RECV_DTBO_FILE, boot.slice(boot.recovery_dtbo_range))
            .map_err(|e| format!("Failed writing [{RECV_DTBO_FILE}]: {e}"))?;
    }

    if boot.dtb_range.1 > 0 {
        fs::write(DTB_FILE, boot.slice(boot.dtb_range))
            .map_err(|e| format!("Failed writing [{DTB_FILE}]: {e}"))?;
    }

    if boot.hdr.kind == HeaderKind::VndV4 && boot.bootconfig_range.1 > 0 {
        fs::write(BOOTCONFIG_FILE, boot.slice(boot.bootconfig_range))
            .map_err(|e| format!("Failed writing [{BOOTCONFIG_FILE}]: {e}"))?;
    }

    Ok(if boot.flags.chromeos { 2 } else { 0 })
}

fn unpack_v4_ramdisks(boot: &ParsedBootImage, skip_decomp: bool) -> Result<(), String> {
    let rbase = boot.ramdisk_range.0;
    let raw = boot.raw();

    let results: Result<Vec<(Format, Vec<u8>, Option<CpioArchive>)>, String> = boot
        .v4_entries
        .par_iter()
        .map(|entry| {
            let s = rbase + entry.ramdisk_offset as usize;
            let e = s + entry.ramdisk_size as usize;
            let frag = &raw[s..e];
            let fmt = detect_format_lg(frag);
            let payload = if !skip_decomp && fmt.is_compressed() {
                decompress_bytes(fmt, frag)?
            } else {
                frag.to_vec()
            };
            let archive = if !skip_decomp && payload.starts_with(b"070701") {
                Some(CpioArchive::from_bytes(&payload)?)
            } else {
                None
            };
            Ok((fmt, payload, archive))
        })
        .collect();

    let results = results?;

    if boot.v4_entries.len() == 1 {
        let (fmt, payload, archive) = &results[0];
        fs::write(RAMDISK_FILE, payload)
            .map_err(|e| format!("Failed writing [{RAMDISK_FILE}]: {e}"))?;
        let paths = archive
            .as_ref()
            .map(|a| a.entries.keys().cloned().collect())
            .unwrap_or_default();
        let meta = V4Meta {
            entries: vec![V4EntryMeta {
                index: 0,
                ramdisk_type: boot.v4_entries[0].ramdisk_type,
                ramdisk_name: boot.v4_entries[0].ramdisk_name.clone(),
                board_id_hex: encode_hex(&boot.v4_entries[0].board_id),
                fmt: fmt.name().to_string(),
                orig_size: boot.v4_entries[0].ramdisk_size,
                orig_offset: boot.v4_entries[0].ramdisk_offset,
                frag_sha256: sha256_hex(payload),
                entry_paths: paths,
            }],
            merged_sha256: sha256_hex(payload),
        };
        let json = serde_json::to_string_pretty(&meta)
            .map_err(|e| format!("Failed serializing v4 metadata: {e}"))?;
        fs::write(V4_META_FILE, json)
            .map_err(|e| format!("Failed writing [{V4_META_FILE}]: {e}"))?;
        return Ok(());
    }

    let mut merged = CpioArchive::new();
    let mut entry_metas = Vec::with_capacity(boot.v4_entries.len());

    for (entry, (fmt, payload, archive)) in boot.v4_entries.iter().zip(results.iter()) {
        let frag_file = format!("ramdisk_{}.cpio", entry.index);
        fs::write(&frag_file, payload)
            .map_err(|e| format!("Failed writing [{frag_file}]: {e}"))?;

        let paths = if let Some(arch) = archive {
            merged.merge_from(arch);
            arch.entries.keys().cloned().collect()
        } else {
            Vec::new()
        };

        entry_metas.push(V4EntryMeta {
            index: entry.index,
            ramdisk_type: entry.ramdisk_type,
            ramdisk_name: entry.ramdisk_name.clone(),
            board_id_hex: encode_hex(&entry.board_id),
            fmt: fmt.name().to_string(),
            orig_size: entry.ramdisk_size,
            orig_offset: entry.ramdisk_offset,
            frag_sha256: sha256_hex(payload),
            entry_paths: paths,
        });
    }

    let merged_bytes = if !skip_decomp && !merged.entries.is_empty() {
        merged.to_bytes()
    } else {
        boot.slice(boot.ramdisk_range).to_vec()
    };

    fs::write(RAMDISK_FILE, &merged_bytes)
        .map_err(|e| format!("Failed writing [{RAMDISK_FILE}]: {e}"))?;

    let meta = V4Meta {
        entries: entry_metas,
        merged_sha256: sha256_hex(&merged_bytes),
    };
    let json = serde_json::to_string_pretty(&meta)
        .map_err(|e| format!("Failed serializing v4 metadata: {e}"))?;
    fs::write(V4_META_FILE, json)
        .map_err(|e| format!("Failed writing [{V4_META_FILE}]: {e}"))?;
    Ok(())
}

fn encode_hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

pub fn cmd_split(filename: &str) -> Result<i32, String> {
    let data = fs::read(filename).map_err(|e| format!("Failed reading [{filename}]: {e}"))?;
    let Some(off) = find_embedded_dtb_offset(&data) else {
        eprintln!("Cannot find DTB in {filename}");
        return Ok(1);
    };
    if off == 0 {
        eprintln!("Cannot find DTB in {filename}");
        return Ok(1);
    }
    let fmt = detect_format_lg(&data[..off]);
    if fmt.is_compressed() {
        let dec = decompress_bytes(fmt, &data[..off])?;
        fs::write(KERNEL_FILE, dec).map_err(|e| format!("Failed writing [{KERNEL_FILE}]: {e}"))?;
    } else {
        fs::write(KERNEL_FILE, &data[..off])
            .map_err(|e| format!("Failed writing [{KERNEL_FILE}]: {e}"))?;
    }
    fs::write(KER_DTB_FILE, &data[off..])
        .map_err(|e| format!("Failed writing [{KER_DTB_FILE}]: {e}"))?;
    Ok(0)
}

fn pad_to_page(buf: &mut Vec<u8>, base: usize, page_size: usize) {
    let rel = buf.len() - base;
    let rem = rel % page_size;
    if rem != 0 {
        buf.resize(buf.len() + (page_size - rem), 0);
    }
}

pub fn cmd_repack(src_img: &str, out_img: &str, skip_comp: bool) -> Result<(), String> {
    let boot = parse_boot_image(src_img)?;
    eprintln!("Repack to image: [{out_img}]");

    if boot.hdr.kind == HeaderKind::VndV4 {
        return repack_vendor_boot_v4(&boot, src_img, out_img, skip_comp);
    }

    let mut hdr = boot.hdr.clone();
    hdr.set_kernel_size(0);
    hdr.set_ramdisk_size(0);
    hdr.set_second_size(0);
    hdr.set_dtb_size(0);
    hdr.kernel_dt_size = 0;

    if Path::new(HEADER_FILE).exists() {
        hdr.load_hdr_file()?;
    }

    let mut out = Vec::with_capacity(boot.raw().len());
    let src = boot.raw();

    if boot.flags.dhtb {
        out.resize(DHTB_HDR_SIZE, 0);
    } else if boot.flags.blob {
        out.extend_from_slice(&src[..BLOB_HDR_SIZE]);
    } else if boot.flags.nookhd {
        out.extend_from_slice(&src[..NOOKHD_PRE_HEADER_SZ]);
    } else if boot.flags.acclaim {
        out.extend_from_slice(&src[..ACCLAIM_PRE_HEADER_SZ]);
    }

    let off_header = out.len();
    let hdr_space = hdr.hdr_space();
    out.extend_from_slice(&src[boot.hdr_file_offset..boot.hdr_file_offset + hdr_space]);

    let page_sz = hdr.page_size().max(1) as usize;

    // Kernel
    let off_kernel = out.len();
    if let Some(m) = &boot.k_mtk_hdr {
        out.extend_from_slice(m);
    }
    if let Some(zr) = boot.z_hdr_range {
        out.extend_from_slice(boot.slice(zr));
    }
    if Path::new(KERNEL_FILE).exists() {
        let kdata = fs::read(KERNEL_FILE)
            .map_err(|e| format!("Failed reading [{KERNEL_FILE}]: {e}"))?;
        let mut written_sz = if !skip_comp
            && !detect_format(&kdata).is_compressed_any()
            && boot.k_fmt.is_compressed()
        {
            let fmt = if boot.flags.zimage_kernel && boot.k_fmt == Format::Gzip {
                Format::Zopfli
            } else {
                boot.k_fmt
            };
            let comp = compress_bytes(fmt, &kdata)?;
            out.extend_from_slice(&comp);
            comp.len() as u32
        } else {
            out.extend_from_slice(&kdata);
            kdata.len() as u32
        };

        if boot.flags.zimage_kernel {
            let orig_k_sz = boot.hdr.kernel_size();
            if (!skip_comp && written_sz + 4 > orig_k_sz) || (skip_comp && written_sz > orig_k_sz) {
                eprintln!("! Recompressed kernel is too large, using original kernel");
                out.truncate(out.len() - written_sz as usize);
                out.extend_from_slice(boot.slice(boot.kernel_range));
            } else if !skip_comp {
                let pad_zeros = (orig_k_sz - written_sz - 4) as usize;
                out.resize(out.len() + pad_zeros, 0);
                out.extend_from_slice(&(kdata.len() as u32).to_le_bytes());
            }
            written_sz = orig_k_sz;
        }
        hdr.set_kernel_size(written_sz);
    } else if boot.hdr.kernel_size() != 0 {
        let kslice = boot.slice(boot.kernel_range);
        out.extend_from_slice(kslice);
        hdr.set_kernel_size(kslice.len() as u32);
    }

    if boot.flags.zimage_kernel {
        let zh_len = boot.z_hdr_range.map_or(0, |r| r.1 as u32);
        let tail_slice = boot.z_tail_range.map_or(&[][..], |r| boot.slice(r));
        out.extend_from_slice(tail_slice);
        hdr.set_kernel_size(hdr.kernel_size() + zh_len + tail_slice.len() as u32);
    }

    if Path::new(KER_DTB_FILE).exists() {
        let dtb = fs::read(KER_DTB_FILE)
            .map_err(|e| format!("Failed reading [{KER_DTB_FILE}]: {e}"))?;
        out.extend_from_slice(&dtb);
        hdr.set_kernel_size(hdr.kernel_size() + dtb.len() as u32);
    }
    pad_to_page(&mut out, off_header, page_sz);

    // Ramdisk
    let off_ramdisk = out.len();
    if let Some(m) = &boot.r_mtk_hdr {
        out.extend_from_slice(m);
    }
    if Path::new(RAMDISK_FILE).exists() {
        let rdata = fs::read(RAMDISK_FILE)
            .map_err(|e| format!("Failed reading [{RAMDISK_FILE}]: {e}"))?;
        let mut r_fmt = boot.r_fmt;
        if !skip_comp && !hdr.is_vendor() && hdr.header_version() == 4 && r_fmt != Format::Lz4Legacy
        {
            eprintln!(
                "RAMDISK_FMT: [{}] -> [{}]",
                r_fmt.name(),
                Format::Lz4Legacy.name()
            );
            r_fmt = Format::Lz4Legacy;
        }
        let r_sz = if !skip_comp && !detect_format(&rdata).is_compressed_any() && r_fmt.is_compressed()
        {
            let comp = compress_bytes(r_fmt, &rdata)?;
            out.extend_from_slice(&comp);
            comp.len() as u32
        } else {
            out.extend_from_slice(&rdata);
            rdata.len() as u32
        };
        hdr.set_ramdisk_size(r_sz);
        pad_to_page(&mut out, off_header, page_sz);
    }

    // Second
    let off_second = out.len();
    if Path::new(SECOND_FILE).exists() {
        let sdata = fs::read(SECOND_FILE)
            .map_err(|e| format!("Failed reading [{SECOND_FILE}]: {e}"))?;
        out.extend_from_slice(&sdata);
        hdr.set_second_size(sdata.len() as u32);
        pad_to_page(&mut out, off_header, page_sz);
    }

    // Extra
    let off_extra = out.len();
    if Path::new(EXTRA_FILE).exists() {
        let edata =
            fs::read(EXTRA_FILE).map_err(|e| format!("Failed reading [{EXTRA_FILE}]: {e}"))?;
        let e_sz = if !skip_comp
            && !detect_format(&edata).is_compressed_any()
            && boot.e_fmt.is_compressed()
        {
            let comp = compress_bytes(boot.e_fmt, &edata)?;
            out.extend_from_slice(&comp);
            comp.len() as u32
        } else {
            out.extend_from_slice(&edata);
            edata.len() as u32
        };
        hdr.set_extra_size(e_sz);
        pad_to_page(&mut out, off_header, page_sz);
    }

    // Recovery DTBO
    if Path::new(RECV_DTBO_FILE).exists() {
        let rdtbo = fs::read(RECV_DTBO_FILE)
            .map_err(|e| format!("Failed reading [{RECV_DTBO_FILE}]: {e}"))?;
        hdr.set_recovery_dtbo_offset(out.len() as u64);
        hdr.set_recovery_dtbo_size(rdtbo.len() as u32);
        out.extend_from_slice(&rdtbo);
        pad_to_page(&mut out, off_header, page_sz);
    }

    // DTB
    let off_dtb = out.len();
    if Path::new(DTB_FILE).exists() {
        let dtb = fs::read(DTB_FILE).map_err(|e| format!("Failed reading [{DTB_FILE}]: {e}"))?;
        hdr.set_dtb_size(dtb.len() as u32);
        out.extend_from_slice(&dtb);
        pad_to_page(&mut out, off_header, page_sz);
    }

    if boot.ignore_range.1 > 0 {
        out.extend_from_slice(boot.slice(boot.ignore_range));
    }

    if boot.flags.seandroid {
        out.extend_from_slice(SEANDROID_MAGIC);
        if boot.flags.dhtb {
            out.extend_from_slice(b"\xff\xff\xff\xff");
        }
    } else if boot.flags.lg_bump {
        out.extend_from_slice(LG_BUMP_MAGIC);
    }

    let off_total = out.len();
    pad_to_page(&mut out, off_header, page_sz);

    let mut off_vbmeta = 0usize;
    if boot.flags.avb {
        pad_to_page(&mut out, off_header, 4096);
        off_vbmeta = out.len();
        if let Some(vb_range) = boot.vbmeta_range {
            out.extend_from_slice(boot.slice(vb_range));
        }
    }

    if !boot.flags.chromeos && out.len() < src.len() {
        out.resize(src.len(), 0);
    }

    // Patch MTK headers
    if boot.flags.mtk_kernel {
        let sz = hdr.kernel_size();
        write_u32_le(&mut out, off_kernel + 4, sz);
        hdr.set_kernel_size(sz + MTK_HDR_SIZE as u32);
    }
    if boot.flags.mtk_ramdisk {
        let sz = hdr.ramdisk_size();
        write_u32_le(&mut out, off_ramdisk + 4, sz);
        hdr.set_ramdisk_size(sz + MTK_HDR_SIZE as u32);
    }

    let struct_sz = hdr.hdr_size() as u32;
    hdr.set_header_size(struct_sz);

    // Update boot id checksum (v0/v1/v2/pxa)
    if hdr.id().is_some() {
        let digest_bytes = compute_boot_id(
            &hdr,
            &out,
            off_kernel,
            off_ramdisk,
            off_second,
            off_extra,
            off_dtb,
            boot.flags.sha256,
        );
        if let Some(id_mut) = hdr.id_mut() {
            id_mut.fill(0);
            id_mut[..digest_bytes.len()].copy_from_slice(&digest_bytes);
        }
    }

    hdr.print(0);

    // Write final header
    if boot.flags.amonet {
        let real_sz = (hdr_space - AMONET_MICROLOADER_SZ).min(hdr.raw.len());
        out[off_header + AMONET_MICROLOADER_SZ..off_header + AMONET_MICROLOADER_SZ + real_sz]
            .copy_from_slice(&hdr.raw[..real_sz]);
    } else {
        out[off_header..off_header + hdr.raw.len()].copy_from_slice(&hdr.raw);
    }

    // Update AVB footer and vbmeta flags
    if boot.flags.avb {
        if let Some(orig_footer_off) = boot.avb_footer_offset {
            let footer_start = out.len() - AVB_FOOTER_SIZE;
            out[footer_start..].copy_from_slice(&src[orig_footer_off..orig_footer_off + AVB_FOOTER_SIZE]);
            write_u64_be(&mut out, footer_start + 12, off_total as u64);
            write_u64_be(&mut out, footer_start + 20, off_vbmeta as u64);
            if env::var("PATCHVBMETAFLAG").is_ok_and(|v| v == "true") {
                write_u32_be(&mut out, off_vbmeta + 120, 3);
            }
        }
    }

    if boot.flags.dhtb {
        out[..8].copy_from_slice(DHTB_MAGIC);
        let payload_sz = (off_total - DHTB_HDR_SIZE) as u32;
        write_u32_le(&mut out, 48, payload_sz);
        let hash = Sha256::digest(&out[DHTB_HDR_SIZE..off_total]);
        out[8..40].copy_from_slice(&hash);
    } else if boot.flags.blob {
        let payload_sz = (off_total - BLOB_HDR_SIZE) as u32;
        write_u32_le(&mut out, 96, payload_sz);
    }

    fs::write(out_img, &out).map_err(|e| format!("Failed writing [{out_img}]: {e}"))
}

#[allow(clippy::too_many_arguments)]
fn compute_boot_id(
    hdr: &DynHeader,
    out: &[u8],
    off_kernel: usize,
    off_ramdisk: usize,
    off_second: usize,
    off_extra: usize,
    off_dtb: usize,
    use_sha256: bool,
) -> Vec<u8> {
    let mut blocks: Vec<&[u8]> = Vec::new();
    let k_sz = hdr.kernel_size();
    let r_sz = hdr.ramdisk_size();
    let s_sz = hdr.second_size();
    let e_sz = hdr.extra_size();
    let ver = hdr.header_version();
    let rdtbo_sz = hdr.recovery_dtbo_size();
    let rdtbo_off = hdr.recovery_dtbo_offset() as usize;
    let dtb_sz = hdr.dtb_size();

    let k_len_bytes = k_sz.to_le_bytes();
    let r_len_bytes = r_sz.to_le_bytes();
    let s_len_bytes = s_sz.to_le_bytes();
    let e_len_bytes = e_sz.to_le_bytes();
    let rdtbo_len_bytes = rdtbo_sz.to_le_bytes();
    let dtb_len_bytes = dtb_sz.to_le_bytes();

    blocks.push(&out[off_kernel..off_kernel + k_sz as usize]);
    blocks.push(&k_len_bytes);
    blocks.push(&out[off_ramdisk..off_ramdisk + r_sz as usize]);
    blocks.push(&r_len_bytes);
    blocks.push(&out[off_second..off_second + s_sz as usize]);
    blocks.push(&s_len_bytes);
    if e_sz > 0 {
        blocks.push(&out[off_extra..off_extra + e_sz as usize]);
        blocks.push(&e_len_bytes);
    }
    if ver == 1 || ver == 2 {
        blocks.push(&out[rdtbo_off..rdtbo_off + rdtbo_sz as usize]);
        blocks.push(&rdtbo_len_bytes);
    }
    if ver == 2 {
        blocks.push(&out[off_dtb..off_dtb + dtb_sz as usize]);
        blocks.push(&dtb_len_bytes);
    }

    if use_sha256 {
        let mut h = Sha256::new();
        for b in blocks {
            h.update(b);
        }
        h.finalize().to_vec()
    } else {
        let mut h = Sha1::new();
        for b in blocks {
            h.update(b);
        }
        h.finalize().to_vec()
    }
}

fn repack_vendor_boot_v4(
    boot: &ParsedBootImage,
    src_img: &str,
    out_img: &str,
    skip_comp: bool,
) -> Result<(), String> {
    let mut hdr = boot.hdr.clone();
    if Path::new(HEADER_FILE).exists() {
        hdr.load_hdr_file()?;
    }

    let dtb_bytes = if Path::new(DTB_FILE).exists() {
        fs::read(DTB_FILE).map_err(|e| format!("Failed reading [{DTB_FILE}]: {e}"))?
    } else {
        Vec::new()
    };

    let bc_bytes = if Path::new(BOOTCONFIG_FILE).exists() {
        fs::read(BOOTCONFIG_FILE)
            .map_err(|e| format!("Failed reading [{BOOTCONFIG_FILE}]: {e}"))?
    } else {
        Vec::new()
    };

    let patch_vbmeta = env::var("PATCHVBMETAFLAG").is_ok_and(|v| v == "true");
    let v4_meta: Option<V4Meta> = fs::read_to_string(V4_META_FILE)
        .ok()
        .and_then(|s| serde_json::from_str(&s).ok());

    let comp_pieces = build_v4_ramdisk_pieces(boot, v4_meta.as_ref(), skip_comp)?;

    let (total_ramdisk_sz, updated_entries, ramdisks_unchanged) = if boot.v4_entries.is_empty() {
        let single_sz = comp_pieces.first().map_or(0, |p| p.len() as u32);
        let unchanged = comp_pieces
            .first()
            .map_or(boot.ramdisk_range.1 == 0, |p| p.as_slice() == boot.slice(boot.ramdisk_range));
        (single_sz, Vec::new(), unchanged)
    } else {
        let mut total_sz = 0u32;
        let mut entries = Vec::with_capacity(boot.v4_entries.len());
        for (entry, comp) in boot.v4_entries.iter().zip(comp_pieces.iter()) {
            entries.push(VendorRamdiskTableEntry {
                index: entry.index,
                ramdisk_size: comp.len() as u32,
                ramdisk_offset: total_sz,
                ramdisk_type: entry.ramdisk_type,
                ramdisk_name: entry.ramdisk_name.clone(),
                board_id: entry.board_id,
            });
            total_sz += comp.len() as u32;
        }
        let unchanged = boot
            .v4_entries
            .iter()
            .zip(comp_pieces.iter())
            .all(|(orig, piece)| {
                let s = boot.ramdisk_range.0 + orig.ramdisk_offset as usize;
                let e = s + orig.ramdisk_size as usize;
                piece.as_slice() == &boot.raw()[s..e]
            });
        (total_sz, entries, unchanged)
    };

    // Fast path: if everything is byte-identical to original image, copy directly
    let hdr_unchanged = hdr.raw == boot.hdr.raw;
    let dtb_unchanged = dtb_bytes == boot.slice(boot.dtb_range);
    let bc_unchanged = bc_bytes == boot.slice(boot.bootconfig_range);

    if hdr_unchanged && dtb_unchanged && bc_unchanged && ramdisks_unchanged && !patch_vbmeta {
        fs::copy(src_img, out_img)
            .map_err(|e| format!("Failed copying [{src_img}] to [{out_img}]: {e}"))?;
        hdr.print(updated_entries.len());
        return Ok(());
    }

    let mut tbl_bytes = Vec::with_capacity(updated_entries.len() * VENDOR_RAMDISK_TABLE_ENTRY_SIZE);
    for ue in &updated_entries {
        tbl_bytes.extend_from_slice(&ue.encode());
    }

    hdr.set_ramdisk_size(total_ramdisk_sz);
    hdr.set_dtb_size(dtb_bytes.len() as u32);
    hdr.set_vendor_ramdisk_table_size(tbl_bytes.len() as u32);
    hdr.set_vendor_ramdisk_table_entry_num(updated_entries.len() as u32);
    hdr.set_vendor_ramdisk_table_entry_size(VENDOR_RAMDISK_TABLE_ENTRY_SIZE as u32);
    hdr.set_bootconfig_size(bc_bytes.len() as u32);
    hdr.set_header_size(hdr.hdr_size() as u32);

    let page_sz = hdr.page_size().max(1) as usize;
    let mut assembled = Vec::with_capacity(boot.raw().len());

    // 1. Header
    assembled.extend_from_slice(&hdr.raw);
    pad_to_page(&mut assembled, 0, page_sz);

    // 2. Vendor ramdisk section
    for piece in &comp_pieces {
        assembled.extend_from_slice(piece);
    }
    pad_to_page(&mut assembled, 0, page_sz);

    // 3. DTB
    assembled.extend_from_slice(&dtb_bytes);
    pad_to_page(&mut assembled, 0, page_sz);

    // 4. Vendor ramdisk table
    assembled.extend_from_slice(&tbl_bytes);
    pad_to_page(&mut assembled, 0, page_sz);

    // 5. Bootconfig
    assembled.extend_from_slice(&bc_bytes);
    pad_to_page(&mut assembled, 0, page_sz);

    let orig_total_size = boot.raw().len();

    if boot.flags.avb {
        let Some(orig_footer_off) = boot.avb_footer_offset else {
            return Err("Missing original AVB footer".into());
        };
        let Some(vb_range) = boot.vbmeta_range else {
            return Err("Missing original AVB vbmeta".into());
        };

        let orig_footer = &boot.raw()[orig_footer_off..orig_footer_off + AVB_FOOTER_SIZE];
        let orig_image_size = read_u64_be(orig_footer, 12) as usize;
        let orig_vbmeta_off = read_u64_be(orig_footer, 20) as usize;
        let vmaj = read_u32_be(orig_footer, 4);
        let vmin = read_u32_be(orig_footer, 8);

        let mut vbmeta_blob = boot.slice(vb_range).to_vec();
        let max_payload = orig_total_size
            .checked_sub(AVB_FOOTER_SIZE + vbmeta_blob.len())
            .ok_or("Invalid original AVB partition size")?;
        if assembled.len() > max_payload {
            return Err(format!(
                "Repacked vendor_boot needs {} bytes before AVB, but original partition allows {}",
                assembled.len(),
                max_payload
            ));
        }

        let image_before_vbmeta = orig_image_size.max(assembled.len());
        let vbmeta_offset = orig_vbmeta_off.max(image_before_vbmeta);
        assembled.resize(image_before_vbmeta, 0);

        if patch_vbmeta && vbmeta_blob.len() >= 124 {
            write_u32_be(&mut vbmeta_blob, 120, 3);
        }

        update_avb_hash_descriptor(&mut vbmeta_blob, &assembled, image_before_vbmeta)?;

        if vbmeta_offset + vbmeta_blob.len() > orig_total_size - AVB_FOOTER_SIZE {
            return Err("Repacked AVB metadata does not fit in original partition".into());
        }
        assembled.resize(vbmeta_offset, 0);
        assembled.extend_from_slice(&vbmeta_blob);
        assembled.resize(orig_total_size - AVB_FOOTER_SIZE, 0);

        let mut footer = orig_footer.to_vec();
        write_u32_be(&mut footer, 4, vmaj);
        write_u32_be(&mut footer, 8, vmin);
        write_u64_be(&mut footer, 12, image_before_vbmeta as u64);
        write_u64_be(&mut footer, 20, vbmeta_offset as u64);
        write_u64_be(&mut footer, 28, vbmeta_blob.len() as u64);
        assembled.extend_from_slice(&footer);
    } else {
        if assembled.len() > orig_total_size {
            return Err(format!(
                "Repacked vendor_boot needs {} bytes, but original image is {} bytes",
                assembled.len(),
                orig_total_size
            ));
        }
        assembled.resize(orig_total_size, 0);
    }

    hdr.print(updated_entries.len());
    fs::write(out_img, &assembled).map_err(|e| format!("Failed writing [{out_img}]: {e}"))
}

fn build_v4_ramdisk_pieces(
    boot: &ParsedBootImage,
    v4_meta: Option<&V4Meta>,
    skip_comp: bool,
) -> Result<Vec<Vec<u8>>, String> {
    let rbase = boot.ramdisk_range.0;
    let raw = boot.raw();
    let count = boot.v4_entries.len();

    if count == 0 {
        if Path::new(RAMDISK_FILE).exists() {
            let rdata = fs::read(RAMDISK_FILE)
                .map_err(|e| format!("Failed reading [{RAMDISK_FILE}]: {e}"))?;
            if !skip_comp
                && !detect_format(&rdata).is_compressed_any()
                && boot.r_fmt.is_compressed()
            {
                return Ok(vec![compress_bytes(boot.r_fmt, &rdata)?]);
            }
            return Ok(vec![rdata]);
        }
        return Ok(Vec::new());
    }

    if count == 1 {
        let orig_entry = &boot.v4_entries[0];
        let orig_comp = &raw[rbase + orig_entry.ramdisk_offset as usize
            ..rbase + orig_entry.ramdisk_offset as usize + orig_entry.ramdisk_size as usize];
        let orig_fmt = detect_format_lg(orig_comp);
        if Path::new(RAMDISK_FILE).exists() {
            let rdata = fs::read(RAMDISK_FILE)
                .map_err(|e| format!("Failed reading [{RAMDISK_FILE}]: {e}"))?;
            if let Some(meta) = v4_meta {
                if meta.entries.first().is_some_and(|e| e.frag_sha256 == sha256_hex(&rdata)) {
                    return Ok(vec![orig_comp.to_vec()]);
                }
            }
            if !skip_comp && !detect_format(&rdata).is_compressed_any() && orig_fmt.is_compressed() {
                return Ok(vec![compress_bytes(orig_fmt, &rdata)?]);
            }
            return Ok(vec![rdata]);
        }
        return Ok(vec![orig_comp.to_vec()]);
    }

    // Multiple ramdisk fragments
    let mut frag_modified = vec![false; count];
    let mut frag_raw_data: Vec<Option<Vec<u8>>> = vec![None; count];

    for (i, entry) in boot.v4_entries.iter().enumerate() {
        let frag_file = format!("ramdisk_{}.cpio", entry.index);
        if Path::new(&frag_file).exists() {
            let bytes = fs::read(&frag_file)
                .map_err(|e| format!("Failed reading [{frag_file}]: {e}"))?;
            let unchanged = v4_meta
                .and_then(|m| m.entries.get(i))
                .is_some_and(|em| em.frag_sha256 == sha256_hex(&bytes));
            if !unchanged {
                frag_modified[i] = true;
            }
            frag_raw_data[i] = Some(bytes);
        }
    }

    let any_frag_edited = frag_modified.iter().any(|&b| b);

    // If individual ramdisk_i.cpio files were NOT edited, check if merged ramdisk.cpio was edited
    if !any_frag_edited && Path::new(RAMDISK_FILE).exists() {
        if let Some(meta) = v4_meta {
            let merged_disk_bytes = fs::read(RAMDISK_FILE)
                .map_err(|e| format!("Failed reading [{RAMDISK_FILE}]: {e}"))?;
            if sha256_hex(&merged_disk_bytes) != meta.merged_sha256
                && merged_disk_bytes.starts_with(b"070701")
            {
                let patched_frags =
                    apply_merged_ramdisk_diff(boot, meta, &merged_disk_bytes, &frag_raw_data)?;
                return patched_frags
                    .into_par_iter()
                    .enumerate()
                    .map(|(i, maybe_cpio)| {
                        let orig_entry = &boot.v4_entries[i];
                        let s = rbase + orig_entry.ramdisk_offset as usize;
                        let e = s + orig_entry.ramdisk_size as usize;
                        let orig_comp = &raw[s..e];
                        match maybe_cpio {
                            None => Ok(orig_comp.to_vec()),
                            Some(cpio_bytes) => {
                                let fmt = detect_format_lg(orig_comp);
                                if !skip_comp && fmt.is_compressed() {
                                    compress_bytes(fmt, &cpio_bytes)
                                } else {
                                    Ok(cpio_bytes)
                                }
                            }
                        }
                    })
                    .collect();
            }
        }
    }

    (0..count)
        .into_par_iter()
        .map(|i| {
            let orig_entry = &boot.v4_entries[i];
            let s = rbase + orig_entry.ramdisk_offset as usize;
            let e = s + orig_entry.ramdisk_size as usize;
            let orig_comp = &raw[s..e];

            if !frag_modified[i] {
                return Ok(orig_comp.to_vec());
            }

            let data = frag_raw_data[i].as_ref().unwrap();
            let orig_fmt = detect_format_lg(orig_comp);
            if !skip_comp && !detect_format(data).is_compressed_any() && orig_fmt.is_compressed() {
                compress_bytes(orig_fmt, data)
            } else {
                Ok(data.clone())
            }
        })
        .collect()
}

fn apply_merged_ramdisk_diff(
    boot: &ParsedBootImage,
    meta: &V4Meta,
    merged_disk_bytes: &[u8],
    frag_raw_data: &[Option<Vec<u8>>],
) -> Result<Vec<Option<Vec<u8>>>, String> {
    let rbase = boot.ramdisk_range.0;
    let raw = boot.raw();

    let mut orig_frags: Vec<CpioArchive> = Vec::with_capacity(boot.v4_entries.len());
    let mut orig_merged = CpioArchive::new();

    for (i, entry) in boot.v4_entries.iter().enumerate() {
        let cpio_bytes = if let Some(Some(b)) = frag_raw_data.get(i) {
            if b.starts_with(b"070701") {
                b.clone()
            } else {
                let fmt = detect_format_lg(b);
                decompress_bytes(fmt, b)?
            }
        } else {
            let s = rbase + entry.ramdisk_offset as usize;
            let e = s + entry.ramdisk_size as usize;
            let orig_comp = &raw[s..e];
            let fmt = detect_format_lg(orig_comp);
            if fmt.is_compressed() {
                decompress_bytes(fmt, orig_comp)?
            } else {
                orig_comp.to_vec()
            }
        };
        let arch = CpioArchive::from_bytes(&cpio_bytes)?;
        orig_merged.merge_from(&arch);
        orig_frags.push(arch);
    }

    let new_merged = CpioArchive::from_bytes(merged_disk_bytes)?;

    let mut frag_dirty = vec![false; orig_frags.len()];
    let platform_idx = boot
        .v4_entries
        .iter()
        .position(|e| e.ramdisk_type == 1)
        .unwrap_or(0);
    let recovery_idx = boot.v4_entries.iter().position(|e| e.ramdisk_type == 2);

    let entry_path_sets: Vec<BTreeSet<&str>> = meta
        .entries
        .iter()
        .map(|em| em.entry_paths.iter().map(|s| s.as_str()).collect())
        .collect();

    // Removals
    for path in orig_merged.entries.keys() {
        if !new_merged.entries.contains_key(path) {
            for (i, set) in entry_path_sets.iter().enumerate() {
                if set.contains(path.as_str()) {
                    orig_frags[i].entries.remove(path);
                    frag_dirty[i] = true;
                }
            }
        }
    }

    // Additions and modifications
    for (path, new_entry) in &new_merged.entries {
        let changed = match orig_merged.entries.get(path) {
            None => true,
            Some(old_entry) => old_entry != new_entry,
        };
        if !changed {
            continue;
        }

        let basename = path.rsplit('/').next().unwrap_or(path);
        let is_policy = matches!(
            basename,
            "file_contexts" | "sepolicy" | "precompiled_sepolicy"
        ) || basename.ends_with("_contexts")
            || basename.ends_with("_sepolicy.cil");

        let mut targets: Vec<usize> = entry_path_sets
            .iter()
            .enumerate()
            .filter_map(|(i, set)| if set.contains(path.as_str()) { Some(i) } else { None })
            .collect();

        if is_policy && targets.len() > 1 {
            return Err(format!(
                "{path} exists in multiple vendor_boot fragments; edit the specific fragment cpio directly"
            ));
        }

        if targets.is_empty() && new_entry.is_dir() {
            let prefix = format!("{path}/");
            targets = entry_path_sets
                .iter()
                .enumerate()
                .filter_map(|(i, set)| {
                    if set.iter().any(|p| p.starts_with(&prefix)) {
                        Some(i)
                    } else {
                        None
                    }
                })
                .collect();
        }

        if targets.is_empty() {
            if is_policy {
                return Err(format!(
                    "Cannot choose a vendor_boot ramdisk fragment for policy {path}; edit the specific fragment cpio directly"
                ));
            }
            targets.push(platform_idx);
            if let Some(rec_i) = recovery_idx {
                if path.starts_with("system/bin/")
                    || path.starts_with("bin/")
                    || path.starts_with("system/lib64/")
                    || path.starts_with("res/")
                    || path.starts_with("twres/")
                    || path.starts_with("FFiles/")
                    || path.starts_with("sbin/")
                    || path.starts_with("system/etc/")
                    || path.contains("recovery")
                    || path.contains("fox")
                {
                    targets.push(rec_i);
                }
            }
        }

        for idx in targets {
            orig_frags[idx]
                .entries
                .insert(path.clone(), CpioEntry::new(new_entry.mode, new_entry.uid, new_entry.gid, new_entry.data.clone()));
            frag_dirty[idx] = true;
        }
    }

    Ok(orig_frags
        .into_iter()
        .zip(frag_dirty)
        .map(|(arch, dirty)| if dirty { Some(arch.to_bytes()) } else { None })
        .collect())
}

fn update_avb_hash_descriptor(
    vbmeta: &mut [u8],
    payload: &[u8],
    image_before_vbmeta: usize,
) -> Result<(), String> {
    if vbmeta.len() < 256 || &vbmeta[..4] != AVB_MAGIC {
        return Err("Invalid original AVB metadata".into());
    }
    let auth_size = read_u64_be(vbmeta, 12) as usize;
    let desc_offset = read_u64_be(vbmeta, 96) as usize;
    let desc_size = read_u64_be(vbmeta, 104) as usize;

    let mut pos = 256 + auth_size + desc_offset;
    let desc_end = pos + desc_size;
    if desc_end > vbmeta.len() {
        return Err("Invalid original AVB descriptor range".into());
    }

    while pos + 16 <= desc_end {
        let tag = read_u64_be(vbmeta, pos);
        let nbytes = read_u64_be(vbmeta, pos + 8) as usize;
        let body = pos + 16;
        let body_end = body + nbytes;
        if nbytes == 0 || body_end > desc_end {
            return Err("Invalid original AVB descriptor".into());
        }
        if tag == 2 && nbytes >= 116 {
            let alg_raw = &vbmeta[body + 8..body + 40];
            let alg_end = alg_raw.iter().position(|&b| b == 0).unwrap_or(alg_raw.len());
            let alg = std::str::from_utf8(&alg_raw[..alg_end])
                .map_err(|_| "Unsupported AVB hash algorithm")?;
            let name_len = read_u32_be(vbmeta, body + 40) as usize;
            let salt_len = read_u32_be(vbmeta, body + 44) as usize;
            let digest_len = read_u32_be(vbmeta, body + 48) as usize;
            let name_pos = body + 116;
            let salt_pos = name_pos + name_len;
            let digest_pos = salt_pos + salt_len;
            if digest_pos + digest_len > body_end {
                return Err("Invalid original AVB hash descriptor".into());
            }
            let part = &vbmeta[name_pos..salt_pos];
            if part.starts_with(b"vendor_boot") {
                let salt = &vbmeta[salt_pos..digest_pos];
                let computed: Vec<u8> = match alg {
                    "sha256" => {
                        let mut h = Sha256::new();
                        h.update(salt);
                        h.update(payload);
                        h.finalize().to_vec()
                    }
                    "sha1" => {
                        let mut h = Sha1::new();
                        h.update(salt);
                        h.update(payload);
                        h.finalize().to_vec()
                    }
                    "sha512" => {
                        let mut h = Sha512::new();
                        h.update(salt);
                        h.update(payload);
                        h.finalize().to_vec()
                    }
                    other => return Err(format!("Unsupported AVB hash algorithm: {other}")),
                };
                if computed.len() != digest_len {
                    return Err("AVB digest length mismatch".into());
                }
                write_u64_be(vbmeta, body, image_before_vbmeta as u64);
                vbmeta[digest_pos..digest_pos + digest_len].copy_from_slice(&computed);
            }
        }
        pos = align_to(body_end, 8);
    }
    Ok(())
}
