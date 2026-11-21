// SPDX-License-Identifier: MPL-2.0
//! Clean-room OEM encrypted firmware decryptors & inspectors:
//! OPPO/Realme OZIP, OFP (Qualcomm & MediaTek), and OnePlus OPS.

use super::{safe_join_relative, OemEntry, OemInspectResult};
use aes::cipher::{AsyncStreamCipher, BlockDecryptMut, KeyInit, KeyIvInit};
use cfb_mode::Decryptor as CfbDecryptor;
use ecb::Decryptor as EcbDecryptor;
use md5::{Digest as Md5Digest, Md5};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::{BufReader, Read, Seek, SeekFrom, Write};
use std::path::Path;
use xml::reader::{EventReader, XmlEvent};
use zip::ZipArchive;

type Aes128EcbDec = EcbDecryptor<aes::Aes128>;
type Aes128CfbDec = CfbDecryptor<aes::Aes128>;

#[inline]
fn le_u32(b: &[u8], o: usize) -> u32 {
    u32::from_le_bytes([b[o], b[o + 1], b[o + 2], b[o + 3]])
}

#[inline]
fn le_u16(b: &[u8], o: usize) -> u16 {
    u16::from_le_bytes([b[o], b[o + 1]])
}

#[inline]
fn le_u64(b: &[u8], o: usize) -> u64 {
    u64::from_le_bytes([
        b[o], b[o + 1], b[o + 2], b[o + 3], b[o + 4], b[o + 5], b[o + 6], b[o + 7],
    ])
}

fn hex_to_bytes(hex: &str) -> Vec<u8> {
    hex.as_bytes()
        .chunks_exact(2)
        .filter_map(|pair| std::str::from_utf8(pair).ok())
        .filter_map(|s| u8::from_str_radix(s, 16).ok())
        .collect()
}

fn bytes_to_lower_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for &b in bytes {
        out.push(HEX[(b >> 4) as usize] as char);
        out.push(HEX[(b & 0xF) as usize] as char);
    }
    out
}

fn copy_stream_range(
    src: &mut File,
    dst: &mut File,
    offset: u64,
    mut length: u64,
) -> Result<(), String> {
    src.seek(SeekFrom::Start(offset))
        .map_err(|e| e.to_string())?;
    let mut buf = vec![0u8; 1024 * 1024];
    while length > 0 {
        let want = (length as usize).min(buf.len());
        src.read_exact(&mut buf[..want])
            .map_err(|e| format!("Unexpected EOF copying firmware slice: {e}"))?;
        dst.write_all(&buf[..want]).map_err(|e| e.to_string())?;
        length -= want as u64;
    }
    Ok(())
}

// ============================================================================
// 1. OPPO / Realme OZIP
// ============================================================================

const OZIP_MAGIC: &[u8; 12] = b"OPPOENCRYPT!";

const OZIP_HEX_KEYS: &[&str] = &[
    "D6EECF0AE5ACD4E0E9FE522DE7CE381E",
    "D6ECCF0AE5ACD4E0E92E522DE7C1381E",
    "D6DCCF0AD5ACD4E0292E522DB7C1381E",
    "D7DCCE1AD4AFDCE2393E5161CBDC4321",
    "D7DBCE2AD4ADDCE1393E5521CBDC4321",
    "D7DBCE1AD4AFDCE1393E5121CBDC4321",
    "D4D2CD61D4AFDCE13B5E01221BD14D20",
    "261CC7131D7C1481294E532DB752381E",
    "1CA21E12271335AE33AB81B2A7B14622",
    "D4D2CE11D4AFDCE13B3E0121CBD14D20",
    "1C4C1EA3A12531AE491B21BB31613C11",
    "1C4C1EA3A12531AE4A1B21BB31C13C21",
    "1C4A11A3A12513AE441B23BB31513121",
    "1C4A11A3A12589AE441A23BB31517733",
    "1C4A11A3A22513AE541B53BB31513121",
    "2442CE821A4F352E33AE81B22BC1462E",
    "14C2CD6214CFDC2733AE81B22BC1462C",
    "1E38C1B72D522E29E0D4ACD50ACFDCD6",
    "12341EAAC4C123CE193556A1BBCC232D",
    "2143DCCB21513E39E1DCAFD41ACEDBD7",
    "2D23CCBBA1563519CE23C1C4AA1E3412",
    "172B3E14E46F3CE13E2B5121CBDC4321",
    "ACAA1E12A71431CE4A1B21BBA1C1C6A2",
    "ACAC1E13A72531AE4A1B22BB31C1CC22",
    "1C4411A3A12533AE441B21BB31613C11",
    "1C4416A8A42717AE441523B336513121",
    "55EEAA33112133AE441B23BB31513121",
    "ACAC1E13A12531AE4A1B21BB31C13C21",
    "ACAC1E13A72431AE4A1B22BBA1C1C6A2",
    "12CAC11211AAC3AEA2658690122C1E81",
    "1CA21E12271435AE331B81BBA7C14612",
    "D1DACF24351CE428A9CE32ED87323216",
    "A1CC75115CAECB890E4A563CA1AC67C8",
    "2132321EA2CA86621A11241ABA512722",
    "22A21E821743E5EE33AE81B227B1462E",
];

fn ecb_decrypt_block_in_place(key: &[u8; 16], data: &mut [u8]) -> Result<(), String> {
    use aes::cipher::block_padding::NoPadding;
    let dec = Aes128EcbDec::new_from_slice(key).map_err(|e| e.to_string())?;
    dec.decrypt_padded_mut::<NoPadding>(data)
        .map_err(|e| e.to_string())?;
    Ok(())
}

fn probe_ozip_key(sample16: &[u8; 16]) -> Option<[u8; 16]> {
    for &hex in OZIP_HEX_KEYS {
        let raw = hex_to_bytes(hex);
        if raw.len() != 16 {
            continue;
        }
        let mut key = [0u8; 16];
        key.copy_from_slice(&raw);
        let mut test = *sample16;
        if ecb_decrypt_block_in_place(&key, &mut test).is_ok()
            && (&test[0..4] == b"PK\x03\x04"
                || &test[0..4] == b"AVB0"
                || &test[0..4] == b"ANDR")
        {
            return Some(key);
        }
    }
    None
}

fn parse_ozip_decimal_size(field: &[u8; 16]) -> Result<u64, String> {
    let s = std::str::from_utf8(field)
        .map_err(|_| "Invalid OZIP size header".to_string())?
        .trim_matches('\0')
        .trim();
    s.parse::<u64>()
        .map_err(|_| format!("Invalid OZIP payload size: {s:?}"))
}

pub fn inspect_ozip(path: &Path) -> Result<OemInspectResult, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let file_len = f.metadata().map_err(|e| e.to_string())?.len();
    let mut magic = [0u8; 12];
    f.read_exact(&mut magic).map_err(|e| e.to_string())?;
    if &magic != OZIP_MAGIC {
        return Err("Not an OPPOENCRYPT! OZIP file".to_string());
    }
    let mut sz_buf = [0u8; 16];
    f.seek(SeekFrom::Start(0x10)).map_err(|e| e.to_string())?;
    f.read_exact(&mut sz_buf).map_err(|e| e.to_string())?;
    let declared = parse_ozip_decimal_size(&sz_buf).unwrap_or(0);

    let mut metadata = HashMap::new();
    metadata.insert("declared_size".to_string(), declared.to_string());
    let stem = path
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "decrypted".to_string());
    let out_name = format!("{stem}.zip");

    Ok(OemInspectResult {
        kind: "OZIP".to_string(),
        metadata,
        entries: vec![OemEntry {
            name: out_name,
            size: if declared > 0 { declared } else { file_len },
            offset: 0x1050,
            kind: "decrypted_zip".to_string(),
            extra: HashMap::new(),
        }],
    })
}

pub fn extract_ozip(path: &Path, out_dir: &Path) -> Result<Vec<String>, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let file_len = f.metadata().map_err(|e| e.to_string())?.len();
    let mut magic = [0u8; 12];
    f.read_exact(&mut magic).map_err(|e| e.to_string())?;
    if &magic != OZIP_MAGIC {
        return Err("Not an OPPOENCRYPT! OZIP file".to_string());
    }

    fs::create_dir_all(out_dir).map_err(|e| e.to_string())?;
    let stem = path
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "decrypted".to_string());
    let out_name = format!("{stem}.zip");
    let dest = safe_join_relative(out_dir, &out_name)?;

    // Determine whether this OZIP uses single-blob ECB (0x1050) or 0x40000-chunked (0x50) framing.
    let mut sample_1050 = [0u8; 16];
    let mut sample_0050 = [0u8; 16];
    let has_1050 = file_len >= 0x1060 && {
        f.seek(SeekFrom::Start(0x1050)).is_ok() && f.read_exact(&mut sample_1050).is_ok()
    };
    let has_0050 = file_len >= 0x60 && {
        f.seek(SeekFrom::Start(0x50)).is_ok() && f.read_exact(&mut sample_0050).is_ok()
    };

    if has_1050 {
        if let Some(key) = probe_ozip_key(&sample_1050) {
            let mut sz_buf = [0u8; 16];
            f.seek(SeekFrom::Start(0x10)).map_err(|e| e.to_string())?;
            f.read_exact(&mut sz_buf).map_err(|e| e.to_string())?;
            let mut remaining = parse_ozip_decimal_size(&sz_buf)?;
            f.seek(SeekFrom::Start(0x1050)).map_err(|e| e.to_string())?;

            let mut dst = File::create(&dest).map_err(|e| e.to_string())?;
            let mut chunk = vec![0u8; 0x4000];
            while remaining > 0 {
                let n = f.read(&mut chunk).map_err(|e| e.to_string())?;
                if n == 0 {
                    break;
                }
                let aligned = (n / 16) * 16;
                if aligned > 0 {
                    ecb_decrypt_block_in_place(&key, &mut chunk[..aligned])?;
                }
                let write_len = (remaining as usize).min(n);
                dst.write_all(&chunk[..write_len])
                    .map_err(|e| e.to_string())?;
                remaining -= write_len as u64;
            }
            return Ok(vec![out_name]);
        }
    }

    if has_0050 {
        if let Some(key) = probe_ozip_key(&sample_0050) {
            let mut dst = File::create(&dest).map_err(|e| e.to_string())?;
            let mut bstart = 0u64;
            let mut go_on = true;
            while go_on && bstart + 0x50 <= file_len {
                f.seek(SeekFrom::Start(bstart)).map_err(|e| e.to_string())?;
                let mut hdr_magic = [0u8; 12];
                if f.read_exact(&mut hdr_magic).is_err() || &hdr_magic != OZIP_MAGIC {
                    break;
                }
                f.seek(SeekFrom::Start(bstart + 0x10))
                    .map_err(|e| e.to_string())?;
                let mut sz_buf = [0u8; 16];
                f.read_exact(&mut sz_buf).map_err(|e| e.to_string())?;
                let mut bdsize = parse_ozip_decimal_size(&sz_buf)?;
                if bdsize < 0x40000 {
                    go_on = false;
                }
                f.seek(SeekFrom::Start(bstart + 0x50))
                    .map_err(|e| e.to_string())?;
                let mut blk = [0u8; 16];
                let mut plain = vec![0u8; 0x3FF0];
                while bdsize > 0 {
                    let n = f.read(&mut blk).map_err(|e| e.to_string())?;
                    if n < 16 {
                        break;
                    }
                    ecb_decrypt_block_in_place(&key, &mut blk)?;
                    let w1 = (bdsize as usize).min(16);
                    dst.write_all(&blk[..w1]).map_err(|e| e.to_string())?;
                    bdsize = bdsize.saturating_sub(16);
                    if bdsize == 0 {
                        break;
                    }
                    let n2 = f.read(&mut plain).map_err(|e| e.to_string())?;
                    if n2 == 0 {
                        break;
                    }
                    let w2 = (bdsize as usize).min(n2);
                    dst.write_all(&plain[..w2]).map_err(|e| e.to_string())?;
                    bdsize = bdsize.saturating_sub(0x3FF0);
                }
                bstart += 0x40050;
            }
            return Ok(vec![out_name]);
        }
    }

    Err("No matching OZIP AES key found".to_string())
}

// ============================================================================
// 2. OFP (Qualcomm & MediaTek)
// ============================================================================

const OFP_QC_KEYS: &[(&str, &str, &str)] = &[
    (
        "27827963787265EF89D126B69A495A21",
        "82C50203285A2CE7D8C3E198383CE94C",
        "422DD5399181E223813CD8ECDF2E4D72",
    ),
    (
        "E11AA7BB558A436A8375FD15DDD4651F",
        "77DDF6A0696841F6B74782C097835169",
        "A739742384A44E8BA45207AD5C3700EA",
    ),
    (
        "67657963787565E837D226B69A495D21",
        "F6C50203515A2CE7D8C3E1F938B7E94C",
        "42F2D5399137E2B2813CD8ECDF2F4D72",
    ),
    (
        "3C2D518D9BF2E4279DC758CD535147C3",
        "87C74A29709AC1BF2382276C4E8DF232",
        "598D92E967265E9BCABE2469FE4A915E",
    ),
    (
        "8FB8FB261930260BE945B841AEFA9FD4",
        "E529E82B28F5A2F8831D860AE39E425D",
        "8A09DA60ED36F125D64709973372C1CF",
    ),
    (
        "E8AE288C0192C54BF10C5707E9C4705B",
        "D64FC385DCD52A3C9B5FBA8650F92EDA",
        "79051FD8D8B6297E2E4559E997F63B7F",
    ),
];

const OFP_MTK_KEYS: &[(&str, &str, Option<&str>)] = &[
    (
        "67657963787565E837D226B69A495D21",
        "F6C50203515A2CE7D8C3E1F938B7E94C",
        Some("42F2D5399137E2B2813CD8ECDF2F4D72"),
    ),
    (
        "9E4F32639D21357D37D226B69A495D21",
        "A3D8D358E42F5A9E931DD3917D9A3218",
        Some("386935399137416B67416BECF22F519A"),
    ),
    (
        "892D57E92A4D8A975E3C216B7C9DE189",
        "D26DF2D9913785B145D18C7219B89F26",
        Some("516989E4A1BFC78B365C6BC57D944391"),
    ),
    (
        "27827963787265EF89D126B69A495A21",
        "82C50203285A2CE7D8C3E198383CE94C",
        Some("422DD5399181E223813CD8ECDF2E4D72"),
    ),
    (
        "3C4A618D9BF2E4279DC758CD535147C3",
        "87B13D29709AC1BF2382276C4E8DF232",
        Some("59B7A8E967265E9BCABE2469FE4A915E"),
    ),
    (
        "1C3288822BF824259DC852C1733127D3",
        "E7918D22799181CF2312176C9E2DF298",
        Some("3247F889A7B6DECBCA3E28693E4AAAFE"),
    ),
    (
        "1E4F32239D65A57D37D2266D9A775D43",
        "A332D3C3E42F5A9E931DD391729A321D",
        Some("3F2A35399A373377674155ECF28FD19A"),
    ),
    (
        "122D57E92A518AFF5E3C786B7C34E189",
        "DD6DF2D9543785674522717219989FB0",
        Some("12698965A132C76136CC88C5DD94EE91"),
    ),
    ("ab3f76d7989207f2", "2bf515b3a9737835", None),
];

fn deobfuscate_nibble_xor(data: &[u8], mask: &[u8]) -> Vec<u8> {
    data.iter()
        .zip(mask.iter())
        .map(|(&d, &m)| (d ^ m).rotate_left(4))
        .collect()
}

fn derive_md5_16_ascii(buf: &[u8]) -> [u8; 16] {
    let hex = bytes_to_lower_hex(&Md5::digest(buf));
    let mut out = [0u8; 16];
    out.copy_from_slice(&hex.as_bytes()[..16]);
    out
}

fn aes_cfb_decrypt_buf(key: &[u8; 16], iv: &[u8; 16], data: &mut [u8]) -> Result<(), String> {
    let dec = Aes128CfbDec::new_from_slices(key, iv).map_err(|e| e.to_string())?;
    dec.decrypt(data);
    Ok(())
}

struct OfpQcItem {
    name: String,
    start: u64,
    rlength: u64,
    decrypt_size: u64,
    copy_only: bool,
    sha256: String,
    md5: String,
}

fn find_ofp_qc_xml(f: &mut File, file_len: u64) -> Result<(u64, [u8; 16], [u8; 16], String), String> {
    for &pagesize in &[0x200u64, 0x1000u64] {
        if file_len < pagesize {
            continue;
        }
        let xml_footer = file_len - pagesize;
        let mut hdr = [0u8; 0x20];
        f.seek(SeekFrom::Start(xml_footer)).map_err(|e| e.to_string())?;
        f.read_exact(&mut hdr).map_err(|e| e.to_string())?;
        if le_u32(&hdr, 0x10) != 0x7CEF {
            continue;
        }
        let offset_pages = le_u32(&hdr, 0x14) as u64;
        let mut length = le_u32(&hdr, 0x18) as u64;
        let offset = offset_pages * pagesize;
        if length < 200 && xml_footer > offset + 0x57 {
            length = xml_footer - offset - 0x57;
        }
        if offset + length > xml_footer || length > 32 * 1024 * 1024 {
            continue;
        }
        f.seek(SeekFrom::Start(offset)).map_err(|e| e.to_string())?;
        let mut enc_xml = vec![0u8; length as usize];
        f.read_exact(&mut enc_xml).map_err(|e| e.to_string())?;

        for &(mc_h, uk_h, iv_h) in OFP_QC_KEYS {
            let mc = hex_to_bytes(mc_h);
            let uk = hex_to_bytes(uk_h);
            let iv_raw = hex_to_bytes(iv_h);
            let key = derive_md5_16_ascii(&deobfuscate_nibble_xor(&uk, &mc));
            let iv = derive_md5_16_ascii(&deobfuscate_nibble_xor(&iv_raw, &mc));
            let mut dec = enc_xml.clone();
            if aes_cfb_decrypt_buf(&key, &iv, &mut dec).is_ok() {
                if let Some(pos) = dec.windows(5).position(|w| w == b"<?xml") {
                    let end = dec.iter().rposition(|&b| b == b'>').map(|i| i + 1).unwrap_or(dec.len());
                    if end > pos {
                        let xml = String::from_utf8_lossy(&dec[pos..end]).into_owned();
                        return Ok((pagesize, key, iv, xml));
                    }
                }
            }
        }
    }
    Err("Not a recognized Qualcomm OFP firmware".to_string())
}

fn parse_ofp_xml_items(xml: &str, pagesize: u64) -> Vec<OfpQcItem> {
    let mut items = Vec::new();
    let parser = EventReader::from_str(xml);
    let mut stack: Vec<String> = Vec::new();

    for ev in parser.into_iter().flatten() {
        match ev {
            XmlEvent::StartElement {
                name, attributes, ..
            } => {
                let tag = name.local_name.clone();
                let section = stack.get(1).map(|s| s.as_str()).unwrap_or("");
                let mut fname = String::new();
                let mut offset_pages: Option<u64> = None;
                let mut rlength: Option<u64> = None;
                let mut slength: Option<u64> = None;
                let mut sha256 = String::new();
                let mut md5 = String::new();

                for attr in attributes {
                    let k = attr.name.local_name.as_str();
                    match k {
                        "Path" | "filename" => {
                            if fname.is_empty() {
                                fname = attr.value;
                            }
                        }
                        "FileOffsetInSrc" => offset_pages = attr.value.parse().ok(),
                        "SizeInByteInSrc" => rlength = attr.value.parse().ok(),
                        "SizeInSectorInSrc" => slength = attr.value.parse().ok(),
                        "sha256" | "Sha256" => sha256 = attr.value,
                        "md5" => md5 = attr.value,
                        _ => {}
                    }
                }

                if !fname.is_empty() {
                    if let Some(op) = offset_pages.or(slength) {
                        let start = op * pagesize;
                        let rlen = rlength.or_else(|| slength.map(|s| s * pagesize)).unwrap_or(0);
                        let decrypt_size = if section == "Sahara" { rlen } else { 0x40000 };
                        let copy_only = matches!(
                            section,
                            "DigestsToSign" | "ChainedTableOfDigests" | "Firmware"
                        );
                        if rlen > 0 {
                            items.push(OfpQcItem {
                                name: fname,
                                start,
                                rlength: rlen,
                                decrypt_size,
                                copy_only,
                                sha256,
                                md5,
                            });
                        }
                    }
                }
                stack.push(tag);
            }
            XmlEvent::EndElement { .. } => {
                stack.pop();
            }
            _ => {}
        }
    }
    items
}

struct OfpMtkEntry {
    label: String,
    filename: String,
    start: u64,
    length: u64,
    enclength: u64,
}

fn try_ofp_mtk(f: &mut File, file_len: u64) -> Result<([u8; 16], [u8; 16], HashMap<String, String>, Vec<OfpMtkEntry>), String> {
    if file_len < 0x6C + 16 {
        return Err("File too small for OFP MTK".to_string());
    }
    f.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    let mut probe = [0u8; 16];
    f.read_exact(&mut probe).map_err(|e| e.to_string())?;

    let mut found_key_iv = None;
    for &(k0, k1, k2) in OFP_MTK_KEYS {
        let (key, iv) = if let Some(iv_hex) = k2 {
            let mc = hex_to_bytes(k0);
            let ek = hex_to_bytes(k1);
            let ei = hex_to_bytes(iv_hex);
            (
                derive_md5_16_ascii(&deobfuscate_nibble_xor(&ek, &mc)),
                derive_md5_16_ascii(&deobfuscate_nibble_xor(&ei, &mc)),
            )
        } else {
            let mut key = [0u8; 16];
            let mut iv = [0u8; 16];
            key.copy_from_slice(k0.as_bytes());
            iv.copy_from_slice(k1.as_bytes());
            (key, iv)
        };
        let mut test = probe;
        if aes_cfb_decrypt_buf(&key, &iv, &mut test).is_ok() && &test[..3] == b"MMM" {
            found_key_iv = Some((key, iv));
            break;
        }
    }
    let Some((key, iv)) = found_key_iv else {
        return Err("Not a recognized MediaTek OFP firmware".to_string());
    };

    let hdr_key = b"geyixue";
    let mut hdr1 = vec![0u8; 0x6C];
    f.seek(SeekFrom::Start(file_len - 0x6C))
        .map_err(|e| e.to_string())?;
    f.read_exact(&mut hdr1).map_err(|e| e.to_string())?;
    for (i, byte) in hdr1.iter_mut().enumerate() {
        *byte = hdr_key[i % hdr_key.len()] ^ byte.rotate_left(4);
    }
    let prjname = String::from_utf8_lossy(&hdr1[0..46])
        .trim_matches('\0')
        .to_string();
    let cpu = String::from_utf8_lossy(&hdr1[60..67])
        .trim_matches('\0')
        .to_string();
    let flashtype = String::from_utf8_lossy(&hdr1[67..72])
        .trim_matches('\0')
        .to_string();
    let hdr2_count = le_u16(&hdr1, 72) as usize;
    let prjinfo = String::from_utf8_lossy(&hdr1[74..106])
        .trim_matches('\0')
        .to_string();

    let hdr2_len = (hdr2_count * 0x60) as u64;
    if hdr2_len + 0x6C > file_len || hdr2_count > 1024 {
        return Err("Invalid OFP MTK entry table".to_string());
    }
    let mut hdr2 = vec![0u8; hdr2_len as usize];
    f.seek(SeekFrom::Start(file_len - 0x6C - hdr2_len))
        .map_err(|e| e.to_string())?;
    f.read_exact(&mut hdr2).map_err(|e| e.to_string())?;
    for (i, byte) in hdr2.iter_mut().enumerate() {
        *byte = hdr_key[i % hdr_key.len()] ^ byte.rotate_left(4);
    }

    let mut entries = Vec::with_capacity(hdr2_count);
    for chunk in hdr2.chunks_exact(0x60) {
        let label = String::from_utf8_lossy(&chunk[0..32])
            .trim_matches('\0')
            .to_string();
        let start = le_u64(chunk, 32);
        let length = le_u64(chunk, 40);
        let enclength = le_u64(chunk, 48);
        let filename = String::from_utf8_lossy(&chunk[56..88])
            .trim_matches('\0')
            .to_string();
        if !filename.is_empty() && start + length <= file_len {
            entries.push(OfpMtkEntry {
                label,
                filename,
                start,
                length,
                enclength,
            });
        }
    }

    let mut meta = HashMap::new();
    meta.insert("project".to_string(), prjname);
    meta.insert("project_info".to_string(), prjinfo);
    meta.insert("cpu".to_string(), cpu);
    meta.insert("flash_type".to_string(), flashtype);
    Ok((key, iv, meta, entries))
}

pub fn inspect_ofp(path: &Path) -> Result<OemInspectResult, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let file_len = f.metadata().map_err(|e| e.to_string())?.len();

    let mut magic2 = [0u8; 2];
    if f.read_exact(&mut magic2).is_ok() && &magic2 == b"PK" {
        f.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
        let archive = ZipArchive::new(BufReader::new(f)).map_err(|e| e.to_string())?;
        let entries = (0..archive.len())
            .filter_map(|i| archive.name_for_index(i).map(|n| n.to_string()))
            .map(|name| OemEntry {
                name,
                size: 0,
                offset: 0,
                kind: "zip_entry".to_string(),
                extra: HashMap::new(),
            })
            .collect();
        return Ok(OemInspectResult {
            kind: "OFP_ZIP".to_string(),
            metadata: HashMap::new(),
            entries,
        });
    }

    if let Ok((pagesize, _k, _iv, xml)) = find_ofp_qc_xml(&mut f, file_len) {
        let mut meta = HashMap::new();
        meta.insert("variant".to_string(), "Qualcomm".to_string());
        meta.insert("pagesize".to_string(), pagesize.to_string());
        let entries = parse_ofp_xml_items(&xml, pagesize)
            .into_iter()
            .map(|it| OemEntry {
                name: it.name,
                size: it.rlength,
                offset: it.start,
                kind: if it.copy_only { "raw" } else { "encrypted" }.to_string(),
                extra: HashMap::new(),
            })
            .collect();
        return Ok(OemInspectResult {
            kind: "OFP_QC".to_string(),
            metadata: meta,
            entries,
        });
    }

    if let Ok((_k, _iv, mut meta, items)) = try_ofp_mtk(&mut f, file_len) {
        meta.insert("variant".to_string(), "MediaTek".to_string());
        let entries = items
            .into_iter()
            .map(|it| {
                let mut extra = HashMap::new();
                extra.insert("label".to_string(), it.label);
                OemEntry {
                    name: it.filename,
                    size: it.length,
                    offset: it.start,
                    kind: if it.enclength > 0 { "encrypted" } else { "raw" }.to_string(),
                    extra,
                }
            })
            .collect();
        return Ok(OemInspectResult {
            kind: "OFP_MTK".to_string(),
            metadata: meta,
            entries,
        });
    }

    Err("Unrecognized OFP container format or unsupported key".to_string())
}

pub fn extract_ofp(
    path: &Path,
    out_dir: &Path,
    selected: Option<&HashSet<String>>,
) -> Result<Vec<String>, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let file_len = f.metadata().map_err(|e| e.to_string())?.len();
    fs::create_dir_all(out_dir).map_err(|e| e.to_string())?;

    let mut magic2 = [0u8; 2];
    if f.read_exact(&mut magic2).is_ok() && &magic2 == b"PK" {
        const REALME_PW: &[u8] = b"flash@realme$50E7F7D847732396F1582CD62DD385ED7ABB0897";
        f.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
        let mut archive = ZipArchive::new(BufReader::new(f)).map_err(|e| e.to_string())?;
        let mut extracted = Vec::new();
        for idx in 0..archive.len() {
            let mut entry = archive
                .by_index_decrypt(idx, REALME_PW)
                .map_err(|e| e.to_string())?;
            let name = entry.name().to_string();
            if entry.is_dir() {
                continue;
            }
            if let Some(sel) = selected {
                if !sel.contains(&name) {
                    continue;
                }
            }
            let dest = safe_join_relative(out_dir, &name)?;
            if let Some(p) = dest.parent() {
                fs::create_dir_all(p).map_err(|e| e.to_string())?;
            }
            let mut dst = File::create(&dest).map_err(|e| e.to_string())?;
            std::io::copy(&mut entry, &mut dst).map_err(|e| e.to_string())?;
            extracted.push(name);
        }
        return Ok(extracted);
    }

    if let Ok((pagesize, key, iv, xml)) = find_ofp_qc_xml(&mut f, file_len) {
        let items = parse_ofp_xml_items(&xml, pagesize);
        let mut extracted = Vec::new();
        for item in items {
            if let Some(sel) = selected {
                if !sel.contains(&item.name) {
                    continue;
                }
            }
            let dest = safe_join_relative(out_dir, &item.name)?;
            if let Some(p) = dest.parent() {
                fs::create_dir_all(p).map_err(|e| e.to_string())?;
            }
            let mut dst = File::create(&dest).map_err(|e| e.to_string())?;
            if item.copy_only {
                copy_stream_range(&mut f, &mut dst, item.start, item.rlength)?;
            } else {
                let enc_len = item.rlength.min(item.decrypt_size) as usize;
                let mut buf = vec![0u8; enc_len];
                f.seek(SeekFrom::Start(item.start))
                    .map_err(|e| e.to_string())?;
                f.read_exact(&mut buf).map_err(|e| e.to_string())?;
                aes_cfb_decrypt_buf(&key, &iv, &mut buf)?;
                dst.write_all(&buf).map_err(|e| e.to_string())?;
                if item.rlength > enc_len as u64 {
                    copy_stream_range(
                        &mut f,
                        &mut dst,
                        item.start + enc_len as u64,
                        item.rlength - enc_len as u64,
                    )?;
                }
            }
            let _ = (&item.sha256, &item.md5);
            extracted.push(item.name);
        }
        return Ok(extracted);
    }

    if let Ok((key, iv, _meta, items)) = try_ofp_mtk(&mut f, file_len) {
        let mut extracted = Vec::new();
        for item in items {
            if let Some(sel) = selected {
                if !sel.contains(&item.filename) {
                    continue;
                }
            }
            let dest = safe_join_relative(out_dir, &item.filename)?;
            if let Some(p) = dest.parent() {
                fs::create_dir_all(p).map_err(|e| e.to_string())?;
            }
            let mut dst = File::create(&dest).map_err(|e| e.to_string())?;
            let mut rem = item.length;
            let mut pos = item.start;
            if item.enclength > 0 {
                let mut enc = vec![0u8; item.enclength as usize];
                f.seek(SeekFrom::Start(pos)).map_err(|e| e.to_string())?;
                f.read_exact(&mut enc).map_err(|e| e.to_string())?;
                aes_cfb_decrypt_buf(&key, &iv, &mut enc)?;
                dst.write_all(&enc).map_err(|e| e.to_string())?;
                pos += item.enclength;
                rem = rem.saturating_sub(item.enclength);
            }
            if rem > 0 {
                copy_stream_range(&mut f, &mut dst, pos, rem)?;
            }
            extracted.push(item.filename);
        }
        return Ok(extracted);
    }

    Err("Unrecognized OFP container format or unsupported key".to_string())
}

// ============================================================================
// 3. OnePlus OPS (Custom 4-round Feistel-like S-box table cipher)
// ============================================================================

const OPS_INIT_KEY: [u32; 4] = [0x9EE3_B5D1, 0x9D04_EA5E, 0xABD5_1D67, 0xAFCB_AFD2];

const OPS_MBOX_4: [u32; 64] = [
    0xC4, 0x5D, 0x05, 0x71, 0x99, 0xDD, 0xBB, 0xEE, 0x29, 0xA1, 0x6D, 0xC7, 0xAD, 0xBF, 0xA4, 0x3F,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x0a, 0, 0, 0,
];
const OPS_MBOX_5: [u32; 64] = [
    0x60, 0x8a, 0x3f, 0x2d, 0x68, 0x6b, 0xd4, 0x23, 0x51, 0x0c, 0xd0, 0x95, 0xbb, 0x40, 0xe9, 0x76,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x0a, 0, 0, 0,
];
const OPS_MBOX_6: [u32; 64] = [
    0xAA, 0x69, 0x82, 0x9E, 0x5D, 0xDE, 0xB1, 0x3D, 0x30, 0xBB, 0x81, 0xA3, 0x46, 0x65, 0xa3, 0xe1,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x0a, 0, 0, 0,
];

#[rustfmt::skip]
const OPS_SBOX: &[u8] = b"\xc6cc\xa5\xc6cc\xa5\xf8||\x84\xf8||\x84\xeeww\x99\xeeww\x99\xf6{{\x8d\xf6{{\x8d\xff\xf2\xf2\r\xff\xf2\xf2\r\xd6kk\xbd\xd6kk\xbd\xdeoo\xb1\xdeoo\xb1\x91\xc5\xc5T\x91\xc5\xc5T`00P`00P\x02\x01\x01\x03\x02\x01\x01\x03\xcegg\xa9\xcegg\xa9V++}V++}\xe7\xfe\xfe\x19\xe7\xfe\xfe\x19\xb5\xd7\xd7b\xb5\xd7\xd7bM\xab\xab\xe6M\xab\xab\xe6\xecvv\x9a\xecvv\x9a\x8f\xca\xcaE\x8f\xca\xcaE\x1f\x82\x82\x9d\x1f\x82\x82\x9d\x89\xc9\xc9@\x89\xc9\xc9@\xfa}}\x87\xfa}}\x87\xef\xfa\xfa\x15\xef\xfa\xfa\x15\xb2YY\xeb\xb2YY\xeb\x8eGG\xc9\x8eGG\xc9\xfb\xf0\xf0\x0b\xfb\xf0\xf0\x0bA\xad\xad\xecA\xad\xad\xec\xb3\xd4\xd4g\xb3\xd4\xd4g_\xa2\xa2\xfd_\xa2\xa2\xfdE\xaf\xaf\xeaE\xaf\xaf\xea#\x9c\x9c\xbf#\x9c\x9c\xbfS\xa4\xa4\xf7S\xa4\xa4\xf7\xe4rr\x96\xe4rr\x96\x9b\xc0\xc0[\x9b\xc0\xc0[u\xb7\xb7\xc2u\xb7\xb7\xc2\xe1\xfd\xfd\x1c\xe1\xfd\xfd\x1c=\x93\x93\xae=\x93\x93\xaeL&&jL&&jl66Zl66Z~??A~??A\xf5\xf7\xf7\x02\xf5\xf7\xf7\x02\x83\xcc\xccO\x83\xcc\xccOh44\\h44\\Q\xa5\xa5\xf4Q\xa5\xa5\xf4\xd1\xe5\xe54\xd1\xe5\xe54\xf9\xf1\xf1\x08\xf9\xf1\xf1\x08\xe2qq\x93\xe2qq\x93\xab\xd8\xd8s\xab\xd8\xd8sb11Sb11S*\x15\x15?*\x15\x15?\x08\x04\x04\x0c\x08\x04\x04\x0c\x95\xc7\xc7R\x95\xc7\xc7RF##eF##e\x9d\xc3\xc3^\x9d\xc3\xc3^0\x18\x18(0\x18\x18(7\x96\x96\xa17\x96\x96\xa1\n\x05\x05\x0f\n\x05\x05\x0f/\x9a\x9a\xb5/\x9a\x9a\xb5\x0e\x07\x07\t\x0e\x07\x07\t$\x12\x126$\x12\x126\x1b\x80\x80\x9b\x1b\x80\x80\x9b\xdf\xe2\xe2=\xdf\xe2\xe2=\xcd\xeb\xeb&\xcd\xeb\xeb&N''iN''i\x7f\xb2\xb2\xcd\x7f\xb2\xb2\xcd\xeauu\x9f\xeauu\x9f\x12\t\t\x1b\x12\t\t\x1b\x1d\x83\x83\x9e\x1d\x83\x83\x9eX,,tX,,t4\x1a\x1a.4\x1a\x1a.6\x1b\x1b-6\x1b\x1b-\xdcnn\xb2\xdcnn\xb2\xb4ZZ\xee\xb4ZZ\xee[\xa0\xa0\xfb[\xa0\xa0\xfb\xa4RR\xf6\xa4RR\xf6v;;Mv;;M\xb7\xd6\xd6a\xb7\xd6\xd6a}\xb3\xb3\xce}\xb3\xb3\xceR)){R)){\xdd\xe3\xe3>\xdd\xe3\xe3>^//q^//q\x13\x84\x84\x97\x13\x84\x84\x97\xa6SS\xf5\xa6SS\xf5\xb9\xd1\xd1h\xb9\xd1\xd1h\x00\x00\x00\x00\x00\x00\x00\x00\xc1\xed\xed,\xc1\xed\xed,@  `@  `\xe3\xfc\xfc\x1f\xe3\xfc\xfc\x1fy\xb1\xb1\xc8y\xb1\xb1\xc8\xb6[[\xed\xb6[[\xed\xd4jj\xbe\xd4jj\xbe\x8d\xcb\xcbF\x8d\xcb\xcbFg\xbe\xbe\xd9g\xbe\xbe\xd9r99Kr99K\x94JJ\xde\x94JJ\xde\x98LL\xd4\x98LL\xd4\xb0XX\xe8\xb0XX\xe8\x85\xcf\xcfJ\x85\xcf\xcfJ\xbb\xd0\xd0k\xbb\xd0\xd0k\xc5\xef\xef*\xc5\xef\xef*O\xaa\xaa\xe5O\xaa\xaa\xe5\xed\xfb\xfb\x16\xed\xfb\xfb\x16\x86CC\xc5\x86CC\xc5\x9aMM\xd7\x9aMM\xd7f33Uf33U\x11\x85\x85\x94\x11\x85\x85\x94\x8aEE\xcf\x8aEE\xcf\xe9\xf9\xf9\x10\xe9\xf9\xf9\x10\x04\x02\x02\x06\x04\x02\x02\x06\xfe\x7f\x7f\x81\xfe\x7f\x7f\x81\xa0PP\xf0\xa0PP\xf0x<<Dx<<D%\x9f\x9f\xba%\x9f\x9f\xba\x8a?\x92\x92\xad?\x92\x92\xad!\x9d\x9d\xbc!\x9d\x9d\xbcp88Hp88H\xf1\xf5\xf5\x04\xf1\xf5\xf5\x04c\xbc\xbc\xdfc\xbc\xbc\xdfw\xb6\xb6\xc1w\xb6\xb6\xc1\xaf\xda\xdau\xaf\xda\xdauB!!cB!!c \x10\x100 \x10\x100\xe5\xff\xff\x1a\xe5\xff\xff\x1a\xfd\xf3\xf3\x0e\xfd\xf3\xf3\x0e\xbf\xd2\xd2m\xbf\xd2\xd2m\x81\xcd\xcdL\x81\xcd\xcdL\x18\x0c\x0c\x14\x18\x0c\x0c\x14&\x13\x135&\x13\x135\xc3\xec\xec/\xc3\xec\xec/\xbe__\xe1\xbe__\xe15\x97\x97\xa25\x97\x97\xa2\x88DD\xcc\x88DD\xcc.\x17\x179.\x17\x179\x93\xc4\xc4W\x93\xc4\xc4WU\xa7\xa7\xf2U\xa7\xa7\xf2\xfc~~\x82\xfc~~\x82z==Gz==G\xc8dd\xac\xc8dd\xac\xba]]\xe7\xba]]\xe72\x19\x19+2\x19\x19+\xe6ss\x95\xe6ss\x95\xc0``\xa0\xc0``\xa0\x19\x81\x81\x98\x19\x81\x81\x98\x9eOO\xd1\x9eOO\xd1\xa3\xdc\xdc\x7f\xa3\xdc\xdc\x7fD\"\"fD\"\"fT**~T**~;\x90\x90\xab;\x90\x90\xab\x0b\x88\x88\x83\x0b\x88\x88\x83\x8cFF\xca\x8cFF\xca\xc7\xee\xee)\xc7\xee\xee)k\xb8\xb8\xd3k\xb8\xb8\xd3(\x14\x14<(\x14\x14<\xa7\xde\xdey\xa7\xde\xdey\xbc^^\xe2\xbc^^\xe2\x16\x0b\x0b\x1d\x16\x0b\x0b\x1d\xad\xdb\xdbv\xad\xdb\xdbv\xdb\xe0\xe0;\xdb\xe0\xe0;d22Vd22Vt::Nt::N\x14\n\n\x1e\x14\n\n\x1e\x92II\xdb\x92II\xdb\x0c\x06\x06\n\x0c\x06\x06\nH$$lH$$l\xb8\\\\\xe4\xb8\\\\\xe4\x9f\xc2\xc2]\x9f\xc2\xc2]\xbd\xd3\xd3n\xbd\xd3\xd3nC\xac\xac\xefC\xac\xac\xef\xc4bb\xa6\xc4bb\xa69\x91\x91\xa89\x91\x91\xa81\x95\x95\xa41\x95\x95\xa4\xd3\xe4\xe47\xd3\xe4\xe47\xf2yy\x8b\xf2yy\x8b\xd5\xe7\xe72\xd5\xe7\xe72\x8b\xc8\xc8C\x8b\xc8\xc8Cn77Yn77Y\xdamm\xb7\xdamm\xb7\x01\x8d\x8d\x8c\x01\x8d\x8d\x8c\xb1\xd5\xd5d\xb1\xd5\xd5d\x9cNN\xd2\x9cNN\xd2I\xa9\xa9\xe0I\xa9\xa9\xe0\xd8ll\xb4\xd8ll\xb4\xacVV\xfa\xacVV\xfa\xf3\xf4\xf4\x07\xf3\xf4\xf4\x07\xcf\xea\xea%\xcf\xea\xea%\xcaee\xaf\xcaee\xaf\xf4zz\x8e\xf4zz\x8eG\xae\xae\xe9G\xae\xae\xe9\x10\x08\x08\x18\x10\x08\x08\x18o\xba\xba\xd5o\xba\xba\xd5\xf0xx\x88\xf0xx\x88J%%oJ%%o\\..r\\..r8\x1c\x1c$8\x1c\x1c$W\xa6\xa6\xf1W\xa6\xa6\xf1s\xb4\xb4\xc7s\xb4\xb4\xc7\x97\xc6\xc6Q\x97\xc6\xc6Q\xcb\xe8\xe8#\xcb\xe8\xe8#\xa1\xdd\xdd|\xa1\xdd\xdd|\xe8tt\x9c\xe8tt\x9c>\x1f\x1f!>\x1f\x1f!\x96KK\xdd\x96KK\xdda\xbd\xbd\xdca\xbd\xbd\xdc\r\x8b\x8b\x86\r\x8b\x8b\x86\x0f\x8a\x8a\x85\x0f\x8a\x8a\x85\xe0pp\x90\xe0pp\x90|>>B|>>Bq\xb5\xb5\xc4q\xb5\xb5\xc4\xccff\xaa\xccff\xaa\x90HH\xd8\x90HH\xd8\x06\x03\x03\x05\x06\x03\x03\x05\xf7\xf6\xf6\x01\xf7\xf6\xf6\x01\x1c\x0e\x0e\x12\x1c\x0e\x0e\x12\xc2aa\xa3\xc2aa\xa3j55_j55_\xaeWW\xf9\xaeWW\xf9i\xb9\xb9\xd0i\xb9\xb9\xd0\x17\x86\x86\x91\x17\x86\x86\x91\x99\xc1\xc1X\x99\xc1\xc1X:\x1d\x1d':\x1d\x1d''\x9e\x9e\xb9'\x9e\x9e\xb9\xd9\xe1\xe18\xd9\xe1\xe18\xeb\xf8\xf8\x13\xeb\xf8\xf8\x13+\x98\x98\xb3+\x98\x98\xb3\"\x11\x113\"\x11\x113\xd2ii\xbb\xd2ii\xbb\xa9\xd9\xd9p\xa9\xd9\xd9p\x07\x8e\x8e\x89\x07\x8e\x8e\x893\x94\x94\xa73\x94\x94\xa7-\x9b\x9b\xb6-\x9b\x9b\xb6<\x1e\x1e\"<\x1e\x1e\"\x15\x87\x87\x92\x15\x87\x87\x92\xc9\xe9\xe9 \xc9\xe9\xe9 \x87\xce\xceI\x87\xce\xceI\xaaUU\xff\xaaUU\xffP((xP((x\xa5\xdf\xdfz\xa5\xdf\xdfz\x03\x8c\x8c\x8f\x03\x8c\x8c\x8fY\xa1\xa1\xf8Y\xa1\xa1\xf8\t\x89\x89\x80\t\x89\x89\x80\x1a\r\r\x17\x1a\r\r\x17e\xbf\xbf\xdae\xbf\xbf\xda\xd7\xe6\xe61\xd7\xe6\xe61\x84BB\xc6\x84BB\xc6\xd0hh\xb8\xd0hh\xb8\x82AA\xc3\x82AA\xc3)\x99\x99\xb0)\x99\x99\xb0Z--wZ--w\x1e\x0f\x0f\x11\x1e\x0f\x0f\x11{\xb0\xb0\xcb{\xb0\xb0\xcb\xa8TT\xfc\xa8TT\xfcm\xbb\xbb\xd6m\xbb\xbb\xd6,\x16\x16:,\x16\x16:";

#[inline]
fn gsbox(offset: usize) -> u32 {
    le_u32(OPS_SBOX, offset)
}

fn ops_key_update(iv: [u32; 4], mbox: &[u32; 64]) -> [u32; 4] {
    let d = iv[0] ^ mbox[0];
    let a0 = iv[1] ^ mbox[1];
    let b = iv[2] ^ mbox[2];
    let c = iv[3] ^ mbox[3];

    let mut e = gsbox((((b >> 16) & 0xff) as usize) * 8 + 2)
        ^ gsbox((((a0 >> 8) & 0xff) as usize) * 8 + 3)
        ^ gsbox(((c >> 24) as usize) * 8 + 1)
        ^ gsbox(((d & 0xff) as usize) * 8)
        ^ mbox[4];
    let mut h = gsbox((((c >> 16) & 0xff) as usize) * 8 + 2)
        ^ gsbox((((b >> 8) & 0xff) as usize) * 8 + 3)
        ^ gsbox(((d >> 24) as usize) * 8 + 1)
        ^ gsbox(((a0 & 0xff) as usize) * 8)
        ^ mbox[5];
    let mut i = gsbox((((d >> 16) & 0xff) as usize) * 8 + 2)
        ^ gsbox((((c >> 8) & 0xff) as usize) * 8 + 3)
        ^ gsbox(((a0 >> 24) as usize) * 8 + 1)
        ^ gsbox(((b & 0xff) as usize) * 8)
        ^ mbox[6];
    let mut a = gsbox((((d >> 8) & 0xff) as usize) * 8 + 3)
        ^ gsbox((((a0 >> 16) & 0xff) as usize) * 8 + 2)
        ^ gsbox(((b >> 24) as usize) * 8 + 1)
        ^ gsbox(((c & 0xff) as usize) * 8)
        ^ mbox[7];

    let mut g = 8usize;
    let rounds = mbox[0x3c].saturating_sub(2) as usize;
    for _ in 0..rounds {
        let d_hi = e >> 24;
        let m_hi = h >> 16;
        let s_hi = h >> 24;
        let z_hi = e >> 16;
        let l_hi = i >> 24;
        let t_hi = e >> 8;

        let ne = gsbox((((i >> 16) & 0xff) as usize) * 8 + 2)
            ^ gsbox((((h >> 8) & 0xff) as usize) * 8 + 3)
            ^ gsbox(((a >> 24) as usize) * 8 + 1)
            ^ gsbox(((e & 0xff) as usize) * 8)
            ^ mbox[g];
        let nh = gsbox((((a >> 16) & 0xff) as usize) * 8 + 2)
            ^ gsbox((((i >> 8) & 0xff) as usize) * 8 + 3)
            ^ gsbox((d_hi as usize) * 8 + 1)
            ^ gsbox(((h & 0xff) as usize) * 8)
            ^ mbox[g + 1];
        let ni = gsbox(((z_hi & 0xff) as usize) * 8 + 2)
            ^ gsbox((((a >> 8) & 0xff) as usize) * 8 + 3)
            ^ gsbox((s_hi as usize) * 8 + 1)
            ^ gsbox(((i & 0xff) as usize) * 8)
            ^ mbox[g + 2];
        let na = gsbox(((t_hi & 0xff) as usize) * 8 + 3)
            ^ gsbox(((m_hi & 0xff) as usize) * 8 + 2)
            ^ gsbox((l_hi as usize) * 8 + 1)
            ^ gsbox(((a & 0xff) as usize) * 8)
            ^ mbox[g + 3];
        e = ne;
        h = nh;
        i = ni;
        a = na;
        g += 4;
    }

    [
        (gsbox((((i >> 16) & 0xff) as usize) * 8) & 0xff0000)
            ^ (gsbox((((h >> 8) & 0xff) as usize) * 8 + 1) & 0xff00)
            ^ (gsbox(((a >> 24) as usize) * 8 + 3) & 0xff000000)
            ^ (gsbox(((e & 0xff) as usize) * 8 + 2) & 0xFF)
            ^ mbox[g],
        (gsbox((((a >> 16) & 0xff) as usize) * 8) & 0xff0000)
            ^ (gsbox((((i >> 8) & 0xff) as usize) * 8 + 1) & 0xff00)
            ^ (gsbox(((e >> 24) as usize) * 8 + 3) & 0xff000000)
            ^ (gsbox(((h & 0xff) as usize) * 8 + 2) & 0xFF)
            ^ mbox[g + 3],
        (gsbox((((e >> 16) & 0xff) as usize) * 8) & 0xff0000)
            ^ (gsbox((((a >> 8) & 0xff) as usize) * 8 + 1) & 0xff00)
            ^ (gsbox(((h >> 24) as usize) * 8 + 3) & 0xff000000)
            ^ (gsbox(((i & 0xff) as usize) * 8 + 2) & 0xFF)
            ^ mbox[g + 2],
        (gsbox((((h >> 16) & 0xff) as usize) * 8) & 0xff0000)
            ^ (gsbox((((e >> 8) & 0xff) as usize) * 8 + 1) & 0xff00)
            ^ (gsbox(((i >> 24) as usize) * 8 + 3) & 0xff000000)
            ^ (gsbox(((a & 0xff) as usize) * 8 + 2) & 0xFF)
            ^ mbox[g + 1],
    ]
}

fn ops_decrypt_bytes(inp: &[u8], mbox: &[u32; 64]) -> Vec<u8> {
    let mut rkey = OPS_INIT_KEY;
    let mut out = Vec::with_capacity(inp.len());
    let mut ptr = 0usize;
    while ptr + 16 <= inp.len() {
        rkey = ops_key_update(rkey, mbox);
        let mut next_rkey = [0u32; 4];
        for i in 0..4 {
            let word = le_u32(inp, ptr + i * 4);
            out.extend_from_slice(&(rkey[i] ^ word).to_le_bytes());
            next_rkey[i] = word;
        }
        rkey = next_rkey;
        ptr += 16;
    }
    if ptr < inp.len() {
        // Remaining bytes < 16 are padded to 4-byte boundary.
        rkey = ops_key_update(rkey, mbox);
        let rem = &inp[ptr..];
        for (idx, chunk) in rem.chunks(4).enumerate() {
            let mut b = [0u8; 4];
            b[..chunk.len()].copy_from_slice(chunk);
            let w = u32::from_le_bytes(b);
            out.extend_from_slice(&(w ^ rkey[idx]).to_le_bytes());
        }
        out.truncate(inp.len());
    }
    out
}

struct OpsItem {
    name: String,
    start: u64,
    length: u64,
    encrypted: bool,
    sha256: String,
}

fn find_ops_xml(f: &mut File, file_len: u64) -> Result<(&'static [u32; 64], String, String, String), String> {
    if file_len < 0x200 {
        return Err("File too small for OPS footer".to_string());
    }
    let mut hdr = [0u8; 0x200];
    f.seek(SeekFrom::Start(file_len - 0x200))
        .map_err(|e| e.to_string())?;
    f.read_exact(&mut hdr).map_err(|e| e.to_string())?;
    if le_u32(&hdr, 0x10) != 0x7CEF {
        return Err("Missing OPS footer magic 0x7CEF".to_string());
    }
    let xmllen = le_u32(&hdr, 0x18) as u64;
    let xmlpad = 0x200 - (xmllen % 0x200);
    let total = xmllen + xmlpad;
    if total + 0x200 > file_len || total > 32 * 1024 * 1024 {
        return Err("Invalid OPS XML length".to_string());
    }
    let proj_id = String::from_utf8_lossy(&hdr[0x1C..0x2C])
        .trim_matches('\0')
        .to_string();
    let fw_name = String::from_utf8_lossy(&hdr[0x2C..0x6C])
        .trim_matches('\0')
        .to_string();

    let mut enc_xml = vec![0u8; total as usize];
    f.seek(SeekFrom::Start(file_len - 0x200 - total))
        .map_err(|e| e.to_string())?;
    f.read_exact(&mut enc_xml).map_err(|e| e.to_string())?;

    for mbox in [&OPS_MBOX_5, &OPS_MBOX_6, &OPS_MBOX_4] {
        let dec = ops_decrypt_bytes(&enc_xml, mbox);
        if dec[..xmllen.min(dec.len() as u64) as usize]
            .windows(4)
            .any(|w| w == b"xml ")
        {
            let xml = String::from_utf8_lossy(&dec[..xmllen as usize]).into_owned();
            return Ok((mbox, proj_id, fw_name, xml));
        }
    }
    Err("Unsupported OPS MBox key".to_string())
}

fn parse_ops_xml_items(xml: &str) -> Vec<OpsItem> {
    let mut items = Vec::new();
    let parser = EventReader::from_str(xml);
    let mut stack: Vec<String> = Vec::new();
    for ev in parser.into_iter().flatten() {
        match ev {
            XmlEvent::StartElement {
                name, attributes, ..
            } => {
                let tag = name.local_name.clone();
                let section = stack.get(1).map(|s| s.as_str()).unwrap_or("");
                let mut fname = String::new();
                let mut start_sec: Option<u64> = None;
                let mut len_bytes: Option<u64> = None;
                let mut sha256 = String::new();
                for attr in attributes {
                    match attr.name.local_name.as_str() {
                        "Path" | "filename" => {
                            if fname.is_empty() {
                                fname = attr.value;
                            }
                        }
                        "FileOffsetInSrc" => start_sec = attr.value.parse().ok(),
                        "SizeInByteInSrc" => len_bytes = attr.value.parse().ok(),
                        "Sha256" | "sha256" => sha256 = attr.value,
                        _ => {}
                    }
                }
                if !fname.is_empty() {
                    if let (Some(sec), Some(length)) = (start_sec, len_bytes) {
                        if length > 0 {
                            items.push(OpsItem {
                                name: fname,
                                start: sec * 0x200,
                                length,
                                encrypted: section == "SAHARA",
                                sha256,
                            });
                        }
                    }
                }
                stack.push(tag);
            }
            XmlEvent::EndElement { .. } => {
                stack.pop();
            }
            _ => {}
        }
    }
    items
}

pub fn inspect_ops(path: &Path) -> Result<OemInspectResult, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let file_len = f.metadata().map_err(|e| e.to_string())?.len();
    let (_mbox, proj_id, fw_name, xml) = find_ops_xml(&mut f, file_len)?;
    let mut metadata = HashMap::new();
    metadata.insert("project_id".to_string(), proj_id);
    metadata.insert("firmware".to_string(), fw_name);
    let entries = parse_ops_xml_items(&xml)
        .into_iter()
        .map(|it| OemEntry {
            name: it.name,
            size: it.length,
            offset: it.start,
            kind: if it.encrypted { "encrypted" } else { "raw" }.to_string(),
            extra: HashMap::new(),
        })
        .collect();
    Ok(OemInspectResult {
        kind: "OPS".to_string(),
        metadata,
        entries,
    })
}

pub fn extract_ops(
    path: &Path,
    out_dir: &Path,
    selected: Option<&HashSet<String>>,
) -> Result<Vec<String>, String> {
    let mut f = File::open(path).map_err(|e| e.to_string())?;
    let file_len = f.metadata().map_err(|e| e.to_string())?.len();
    let (mbox, _proj, _fw, xml) = find_ops_xml(&mut f, file_len)?;
    fs::create_dir_all(out_dir).map_err(|e| e.to_string())?;
    let items = parse_ops_xml_items(&xml);

    let mut extracted = Vec::new();
    for item in items {
        if let Some(sel) = selected {
            if !sel.contains(&item.name) {
                continue;
            }
        }
        let dest = safe_join_relative(out_dir, &item.name)?;
        if let Some(p) = dest.parent() {
            fs::create_dir_all(p).map_err(|e| e.to_string())?;
        }
        let mut dst = File::create(&dest).map_err(|e| e.to_string())?;
        if item.encrypted {
            f.seek(SeekFrom::Start(item.start))
                .map_err(|e| e.to_string())?;
            let mut buf = vec![0u8; item.length as usize];
            f.read_exact(&mut buf).map_err(|e| e.to_string())?;
            let dec = ops_decrypt_bytes(&buf, mbox);
            dst.write_all(&dec[..item.length as usize])
                .map_err(|e| e.to_string())?;
        } else {
            copy_stream_range(&mut f, &mut dst, item.start, item.length)?;
        }
        let _ = &item.sha256;
        extracted.push(item.name);
    }
    Ok(extracted)
}
