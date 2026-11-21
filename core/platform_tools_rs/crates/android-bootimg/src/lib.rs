//! Android `boot.img` (v0–v4) and `vendor_boot.img` (v3–v4) parser, builder, and repacker (MIT).

use sha2::{Digest, Sha256};
use thiserror::Error;

/// `ANDROID!` magic bytes at offset `0..8` of `boot_img_hdr`.
pub const BOOT_MAGIC: &[u8; 8] = b"ANDROID!";
/// `VNDRBOOT` magic bytes at offset `0..8` of `vendor_boot_img_hdr`.
pub const VENDOR_BOOT_MAGIC: &[u8; 8] = b"VNDRBOOT";

/// Errors returned when parsing or building boot / vendor_boot images.
#[derive(Debug, Error)]
pub enum BootImgError {
    #[error("image is too small ({0} bytes)")]
    TooSmall(usize),
    #[error("invalid boot image magic")]
    InvalidBootMagic,
    #[error("invalid vendor_boot image magic (expected 'VNDRBOOT')")]
    InvalidVendorBootMagic,
    #[error("unsupported header version {0}")]
    UnsupportedHeaderVersion(u32),
    #[error("invalid OS version or patch level: {0}")]
    InvalidOsVersion(String),
    #[error("vendor ramdisk fragment '{0}' not found in vendor_boot v4 table")]
    VendorRamdiskNotFound(String),
}

/// Configuration parameters for `fastboot boot` and `fastboot flash:raw`.
#[derive(Debug, Clone)]
pub struct BootImageArgs {
    pub header_version: u32,
    pub page_size: u32,
    pub base: u32,
    pub kernel_offset: u32,
    pub ramdisk_offset: u32,
    pub second_offset: u32,
    pub tags_offset: u32,
    pub dtb_offset: u64,
    pub os_version: u32,
    pub cmdline: Option<String>,
}

impl Default for BootImageArgs {
    fn default() -> Self {
        Self {
            header_version: 0,
            page_size: 2048,
            base: 0x1000_0000,
            kernel_offset: 0x0000_8000,
            ramdisk_offset: 0x0100_0000,
            second_offset: 0x00f0_0000,
            tags_offset: 0x0000_0100,
            dtb_offset: 0x0110_0000,
            os_version: 0,
            cmdline: None,
        }
    }
}

/// Encodes `MAJOR[.MINOR[.PATCH]]` into the upper 21 bits (`bits 11..32`) of `os_version`.
pub fn parse_os_version(s: &str) -> Result<u32, BootImgError> {
    let parts: Vec<&str> = s.trim().split('.').collect();
    if parts.is_empty() || parts.len() > 3 {
        return Err(BootImgError::InvalidOsVersion(s.to_string()));
    }
    let major: u32 = parts[0]
        .parse()
        .map_err(|_| BootImgError::InvalidOsVersion(s.to_string()))?;
    let minor: u32 = parts
        .get(1)
        .copied()
        .unwrap_or("0")
        .parse()
        .map_err(|_| BootImgError::InvalidOsVersion(s.to_string()))?;
    let patch: u32 = parts
        .get(2)
        .copied()
        .unwrap_or("0")
        .parse()
        .map_err(|_| BootImgError::InvalidOsVersion(s.to_string()))?;
    if major > 127 || minor > 127 || patch > 127 {
        return Err(BootImgError::InvalidOsVersion(s.to_string()));
    }
    Ok((major << 14) | (minor << 7) | patch)
}

/// Encodes `YYYY-MM[-DD]` into the lower 11 bits (`bits 0..11`) of `os_version`.
pub fn parse_os_patch_level(s: &str) -> Result<u32, BootImgError> {
    let parts: Vec<&str> = s.trim().split('-').collect();
    if parts.len() < 2 {
        return Err(BootImgError::InvalidOsVersion(s.to_string()));
    }
    let year: u32 = parts[0]
        .parse()
        .map_err(|_| BootImgError::InvalidOsVersion(s.to_string()))?;
    let month: u32 = parts[1]
        .parse()
        .map_err(|_| BootImgError::InvalidOsVersion(s.to_string()))?;
    if !(2000..=2127).contains(&year) || !(1..=12).contains(&month) {
        return Err(BootImgError::InvalidOsVersion(s.to_string()));
    }
    Ok(((year - 2000) << 4) | month)
}

fn append_padded(out: &mut Vec<u8>, data: &[u8], page_size: usize) {
    if data.is_empty() {
        return;
    }
    out.extend_from_slice(data);
    let rem = data.len() % page_size;
    if rem != 0 {
        out.resize(out.len() + (page_size - rem), 0);
    }
}

/// Builds or overrides an Android `boot.img` (v0–v4).
///
/// If `kernel_or_bootimg` already starts with `"ANDROID!"` and `ramdisk` is `None`,
/// its existing command line is overridden if `args.cmdline` is `Some`.
pub fn build_boot_image(
    args: &BootImageArgs,
    kernel_or_bootimg: &[u8],
    ramdisk: Option<&[u8]>,
    second: Option<&[u8]>,
    dtb: Option<&[u8]>,
) -> Result<Vec<u8>, BootImgError> {
    if kernel_or_bootimg.len() >= 2048
        && &kernel_or_bootimg[0..8] == BOOT_MAGIC
        && ramdisk.is_none()
        && second.is_none()
        && dtb.is_none()
    {
        let mut img = kernel_or_bootimg.to_vec();
        if let Some(cmd) = &args.cmdline {
            let hdr_ver = u32::from_le_bytes(img[40..44].try_into().unwrap());
            let cbytes = cmd.as_bytes();
            if hdr_ver >= 3 {
                img[44..1580].fill(0);
                let n = cbytes.len().min(1535);
                img[44..44 + n].copy_from_slice(&cbytes[..n]);
            } else {
                img[64..576].fill(0);
                let n = cbytes.len().min(511);
                img[64..64 + n].copy_from_slice(&cbytes[..n]);
            }
        }
        return Ok(img);
    }

    let ramdisk_bytes = ramdisk.unwrap_or(&[]);
    let second_bytes = second.unwrap_or(&[]);
    let dtb_bytes = dtb.unwrap_or(&[]);
    let cmdline_str = args.cmdline.as_deref().unwrap_or("");
    let cmd_bytes = cmdline_str.as_bytes();

    if args.header_version >= 3 {
        let page_size = 4096usize;
        let mut hdr = vec![0u8; page_size];
        hdr[0..8].copy_from_slice(BOOT_MAGIC);
        hdr[8..12].copy_from_slice(&(kernel_or_bootimg.len() as u32).to_le_bytes());
        hdr[12..16].copy_from_slice(&(ramdisk_bytes.len() as u32).to_le_bytes());
        hdr[16..20].copy_from_slice(&args.os_version.to_le_bytes());
        let hdr_sz: u32 = if args.header_version >= 4 { 1584 } else { 1580 };
        hdr[20..24].copy_from_slice(&hdr_sz.to_le_bytes());
        hdr[40..44].copy_from_slice(&args.header_version.to_le_bytes());
        let c_len = cmd_bytes.len().min(1535);
        hdr[44..44 + c_len].copy_from_slice(&cmd_bytes[..c_len]);

        let mut out = hdr;
        append_padded(&mut out, kernel_or_bootimg, page_size);
        append_padded(&mut out, ramdisk_bytes, page_size);
        return Ok(out);
    }

    let page_size = (args.page_size.max(2048)) as usize;
    let mut hdr = vec![0u8; page_size];
    hdr[0..8].copy_from_slice(BOOT_MAGIC);
    hdr[8..12].copy_from_slice(&(kernel_or_bootimg.len() as u32).to_le_bytes());
    hdr[12..16].copy_from_slice(&(args.base.wrapping_add(args.kernel_offset)).to_le_bytes());
    hdr[16..20].copy_from_slice(&(ramdisk_bytes.len() as u32).to_le_bytes());
    hdr[20..24].copy_from_slice(&(args.base.wrapping_add(args.ramdisk_offset)).to_le_bytes());
    hdr[24..28].copy_from_slice(&(second_bytes.len() as u32).to_le_bytes());
    hdr[28..32].copy_from_slice(&(args.base.wrapping_add(args.second_offset)).to_le_bytes());
    hdr[32..36].copy_from_slice(&(args.base.wrapping_add(args.tags_offset)).to_le_bytes());
    hdr[36..40].copy_from_slice(&(page_size as u32).to_le_bytes());
    hdr[40..44].copy_from_slice(&args.header_version.to_le_bytes());
    hdr[44..48].copy_from_slice(&args.os_version.to_le_bytes());

    let c_len = cmd_bytes.len().min(511);
    hdr[64..64 + c_len].copy_from_slice(&cmd_bytes[..c_len]);
    if cmd_bytes.len() > 511 {
        let extra = &cmd_bytes[511..];
        let e_len = extra.len().min(1023);
        hdr[608..608 + e_len].copy_from_slice(&extra[..e_len]);
    }

    let mut hasher = Sha256::new();
    hasher.update(kernel_or_bootimg);
    hasher.update((kernel_or_bootimg.len() as u32).to_le_bytes());
    hasher.update(ramdisk_bytes);
    hasher.update((ramdisk_bytes.len() as u32).to_le_bytes());
    hasher.update(second_bytes);
    hasher.update((second_bytes.len() as u32).to_le_bytes());
    if args.header_version >= 1 {
        let hdr_sz: u32 = if args.header_version == 2 { 1660 } else { 1648 };
        hdr[1644..1648].copy_from_slice(&hdr_sz.to_le_bytes());
        hasher.update(0u32.to_le_bytes()); // recovery_dtbo_size
    }
    if args.header_version == 2 {
        hdr[1648..1652].copy_from_slice(&(dtb_bytes.len() as u32).to_le_bytes());
        let dtb_addr = u64::from(args.base).wrapping_add(args.dtb_offset);
        hdr[1652..1660].copy_from_slice(&dtb_addr.to_le_bytes());
        hasher.update(dtb_bytes);
        hasher.update((dtb_bytes.len() as u32).to_le_bytes());
    }
    let digest = hasher.finalize();
    hdr[576..608].copy_from_slice(&digest);

    let mut out = hdr;
    append_padded(&mut out, kernel_or_bootimg, page_size);
    append_padded(&mut out, ramdisk_bytes, page_size);
    append_padded(&mut out, second_bytes, page_size);
    if args.header_version == 2 {
        append_padded(&mut out, dtb_bytes, page_size);
    }
    Ok(out)
}

/// Repackages an existing `vendor_boot.img` (v3 or v4) with a new ramdisk (`target_ramdisk` =
/// `"default"` or a v4 fragment name) and optional replacement DTB (`--dtb`).
pub fn repack_vendor_boot_image(
    existing: &[u8],
    target_ramdisk: &str,
    new_ramdisk: &[u8],
    new_dtb: Option<&[u8]>,
) -> Result<Vec<u8>, BootImgError> {
    if existing.len() < 2112 {
        return Err(BootImgError::TooSmall(existing.len()));
    }
    if &existing[0..8] != VENDOR_BOOT_MAGIC {
        return Err(BootImgError::InvalidVendorBootMagic);
    }
    let hdr_ver = u32::from_le_bytes(existing[8..12].try_into().unwrap());
    if hdr_ver != 3 && hdr_ver != 4 {
        return Err(BootImgError::UnsupportedHeaderVersion(hdr_ver));
    }
    let page_size = u32::from_le_bytes(existing[12..16].try_into().unwrap()).max(2048) as usize;
    let old_ramdisk_sz = u32::from_le_bytes(existing[24..28].try_into().unwrap()) as usize;
    let old_dtb_sz = u32::from_le_bytes(existing[2100..2104].try_into().unwrap()) as usize;

    let align = |n: usize| -> usize { n.div_ceil(page_size) * page_size };
    let hdr_pages = align(if hdr_ver == 4 { 2128 } else { 2112 });
    let ramdisk_start = hdr_pages;
    let dtb_start = ramdisk_start + align(old_ramdisk_sz);
    if existing.len() < dtb_start + old_dtb_sz {
        return Err(BootImgError::TooSmall(existing.len()));
    }

    let dtb_payload = new_dtb.unwrap_or(&existing[dtb_start..dtb_start + old_dtb_sz]);

    let mut new_hdr = existing[..hdr_pages].to_vec();

    if hdr_ver == 3 || target_ramdisk.is_empty() || target_ramdisk == "default" {
        new_hdr[24..28].copy_from_slice(&(new_ramdisk.len() as u32).to_le_bytes());
        new_hdr[2100..2104].copy_from_slice(&(dtb_payload.len() as u32).to_le_bytes());
        if hdr_ver == 4 {
            let entry_num = u32::from_le_bytes(existing[2116..2120].try_into().unwrap());
            let entry_sz = u32::from_le_bytes(existing[2120..2124].try_into().unwrap()) as usize;
            let bootcfg_sz = u32::from_le_bytes(existing[2124..2128].try_into().unwrap()) as usize;
            let tbl_start = dtb_start + align(old_dtb_sz);
            let tbl_sz = (entry_num as usize) * entry_sz;
            let bootcfg_start = tbl_start + align(tbl_sz);

            // Replace entire vendor ramdisk with a single entry table if table existed
            if entry_num <= 1 && existing.len() >= bootcfg_start + bootcfg_sz {
                let mut out = new_hdr;
                let mut tbl_page = existing[tbl_start..tbl_start + align(tbl_sz)].to_vec();
                if entry_num == 1 && tbl_page.len() >= 8 {
                    tbl_page[0..4].copy_from_slice(&(new_ramdisk.len() as u32).to_le_bytes());
                    tbl_page[4..8].copy_from_slice(&0u32.to_le_bytes());
                }
                append_padded(&mut out, new_ramdisk, page_size);
                append_padded(&mut out, dtb_payload, page_size);
                out.extend_from_slice(&tbl_page);
                append_padded(
                    &mut out,
                    &existing[bootcfg_start..bootcfg_start + bootcfg_sz],
                    page_size,
                );
                return Ok(out);
            }
        }
        let mut out = new_hdr;
        append_padded(&mut out, new_ramdisk, page_size);
        append_padded(&mut out, dtb_payload, page_size);
        return Ok(out);
    }

    // vendor_boot v4 named ramdisk fragment replacement
    let tbl_sz = u32::from_le_bytes(existing[2112..2116].try_into().unwrap()) as usize;
    let entry_num = u32::from_le_bytes(existing[2116..2120].try_into().unwrap()) as usize;
    let entry_sz = u32::from_le_bytes(existing[2120..2124].try_into().unwrap()) as usize;
    let bootcfg_sz = u32::from_le_bytes(existing[2124..2128].try_into().unwrap()) as usize;

    let tbl_start = dtb_start + align(old_dtb_sz);
    let bootcfg_start = tbl_start + align(tbl_sz);
    if existing.len() < bootcfg_start + bootcfg_sz || entry_sz < 44 {
        return Err(BootImgError::TooSmall(existing.len()));
    }

    let old_ramdisk_area = &existing[ramdisk_start..ramdisk_start + old_ramdisk_sz];
    let old_table = &existing[tbl_start..tbl_start + tbl_sz];

    let mut new_ramdisk_concat = Vec::new();
    let mut new_table = old_table.to_vec();
    let mut found = false;

    for idx in 0..entry_num {
        let e_off = idx * entry_sz;
        let frag_sz = u32::from_le_bytes(old_table[e_off..e_off + 4].try_into().unwrap()) as usize;
        let frag_off =
            u32::from_le_bytes(old_table[e_off + 4..e_off + 8].try_into().unwrap()) as usize;
        let name_raw = &old_table[e_off + 12..e_off + 44];
        let name_end = name_raw
            .iter()
            .position(|&b| b == 0)
            .unwrap_or(name_raw.len());
        let frag_name = std::str::from_utf8(&name_raw[..name_end]).unwrap_or("");

        let new_frag_off = new_ramdisk_concat.len() as u32;
        let new_frag_bytes = if frag_name == target_ramdisk {
            found = true;
            new_ramdisk
        } else {
            &old_ramdisk_area[frag_off..frag_off + frag_sz]
        };
        new_ramdisk_concat.extend_from_slice(new_frag_bytes);
        new_table[e_off..e_off + 4]
            .copy_from_slice(&(new_frag_bytes.len() as u32).to_le_bytes());
        new_table[e_off + 4..e_off + 8].copy_from_slice(&new_frag_off.to_le_bytes());
    }

    if !found {
        return Err(BootImgError::VendorRamdiskNotFound(
            target_ramdisk.to_string(),
        ));
    }

    new_hdr[24..28].copy_from_slice(&(new_ramdisk_concat.len() as u32).to_le_bytes());
    new_hdr[2100..2104].copy_from_slice(&(dtb_payload.len() as u32).to_le_bytes());

    let mut out = new_hdr;
    append_padded(&mut out, &new_ramdisk_concat, page_size);
    append_padded(&mut out, dtb_payload, page_size);
    append_padded(&mut out, &new_table, page_size);
    append_padded(
        &mut out,
        &existing[bootcfg_start..bootcfg_start + bootcfg_sz],
        page_size,
    );
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_boot_image_v2_and_v4() {
        let args = BootImageArgs {
            header_version: 2,
            os_version: (parse_os_version("14.0.0").unwrap() << 11)
                | parse_os_patch_level("2024-05-05").unwrap(),
            cmdline: Some("console=ttyMSM0,115200n8".to_string()),
            ..Default::default()
        };

        let kernel = vec![0xAA; 5000];
        let ramdisk = vec![0xBB; 3000];
        let dtb = vec![0xCC; 1000];

        let img = build_boot_image(&args, &kernel, Some(&ramdisk), None, Some(&dtb)).unwrap();
        assert_eq!(&img[0..8], BOOT_MAGIC);
        assert_eq!(img.len(), 2048 + 6144 + 4096 + 2048);

        let args4 = BootImageArgs {
            header_version: 4,
            ..Default::default()
        };
        let img4 = build_boot_image(&args4, &kernel, Some(&ramdisk), None, None).unwrap();
        assert_eq!(&img4[0..8], BOOT_MAGIC);
        assert_eq!(img4.len(), 4096 + 8192 + 4096);
    }
}
