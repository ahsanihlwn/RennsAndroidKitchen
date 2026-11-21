//! Spreadtrum/Unisoc `.pac` binary firmware container parser, validator, and extractor.

use std::fmt;
use std::fs::{self, File};
use std::io::{self, BufWriter, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use crate::crc16::{self, Crc16};

/// Fixed byte length of the primary `.pac` file header (`0x84C`).
pub const PAC_HEADER_SIZE: usize = 2124;

/// Byte length of the header region covered by `crc1` (excludes trailing `crc1` and `crc2`).
pub const PAC_HEADER_CRC_SPAN: usize = PAC_HEADER_SIZE - 4;

/// Fixed byte length of each partition descriptor entry (`0xA14`).
pub const PARTITION_HEADER_SIZE: usize = 2580;

/// Signature magic value expected at offset `0x844` in modern `.pac` packages.
pub const PAC_MAGIC: u32 = 0xFFFA_FFFA;

/// Supported `.pac` container format versions.
pub const SUPPORTED_VERSIONS: [&str; 2] = ["BP_R1.0.0", "BP_R2.0.1"];

const IO_CHUNK_SIZE: usize = 64 * 1024;

/// Errors produced while inspecting, validating, or extracting a `.pac` package.
#[derive(Debug)]
pub enum PacError {
    Io(io::Error),
    FileTooSmall {
        actual: u64,
        minimum: u64,
    },
    UnsupportedVersion(String),
    PackageSizeMismatch {
        header_size: u64,
        file_size: u64,
    },
    PartitionTableOutOfBounds {
        start: u64,
        count: u32,
        file_size: u64,
    },
    InvalidPartitionHeaderSize {
        index: u32,
        actual: u32,
        expected: u32,
    },
    PartitionOutOfBounds {
        name: String,
        offset: u64,
        size: u64,
        file_size: u64,
    },
    InvalidEntryFileName {
        partition_id: String,
        raw_name: String,
    },
    CrcMismatch {
        stage: CrcStage,
        computed: u16,
        expected: u16,
    },
    OutputPathIsFile(PathBuf),
}

/// Identifies which of the two `.pac` checksums failed verification.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CrcStage {
    Header,
    Payload,
}

impl fmt::Display for CrcStage {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Header => f.write_str("CRC1 (header)"),
            Self::Payload => f.write_str("CRC2 (payload)"),
        }
    }
}

impl fmt::Display for PacError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Io(err) => write!(f, "I/O error: {err}"),
            Self::FileTooSmall { actual, minimum } => write!(
                f,
                "input is too small to be a PAC archive ({actual} bytes, minimum {minimum})"
            ),
            Self::UnsupportedVersion(ver) => {
                write!(f, "unsupported PAC format version: {ver:?}")
            }
            Self::PackageSizeMismatch {
                header_size,
                file_size,
            } => write!(
                f,
                "PAC size mismatch: header declares {header_size} bytes, file is {file_size} bytes"
            ),
            Self::PartitionTableOutOfBounds {
                start,
                count,
                file_size,
            } => write!(
                f,
                "partition table ({count} entries at offset {start}) exceeds file size ({file_size} bytes)"
            ),
            Self::InvalidPartitionHeaderSize {
                index,
                actual,
                expected,
            } => write!(
                f,
                "partition #{index} header declares unexpected length {actual} (expected {expected})"
            ),
            Self::PartitionOutOfBounds {
                name,
                offset,
                size,
                file_size,
            } => write!(
                f,
                "partition {name:?} data ({size} bytes at offset {offset}) exceeds file size ({file_size} bytes)"
            ),
            Self::InvalidEntryFileName {
                partition_id,
                raw_name,
            } => write!(
                f,
                "partition {partition_id:?} has unusable output filename {raw_name:?}"
            ),
            Self::CrcMismatch {
                stage,
                computed,
                expected,
            } => write!(
                f,
                "{stage} verification failed: computed 0x{computed:04x}, expected 0x{expected:04x}"
            ),
            Self::OutputPathIsFile(path) => {
                write!(f, "output path '{}' already exists as a file", path.display())
            }
        }
    }
}

impl std::error::Error for PacError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Io(err) => Some(err),
            _ => None,
        }
    }
}

impl From<io::Error> for PacError {
    fn from(err: io::Error) -> Self {
        Self::Io(err)
    }
}

/// Decoded primary header of a `.pac` firmware package.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PacHeader {
    pub version: String,
    pub hi_size: u32,
    pub lo_size: u32,
    pub product_name: String,
    pub firmware_name: String,
    pub partition_count: u32,
    pub partitions_list_start: u32,
    pub mode: u32,
    pub flash_type: u32,
    pub nand_strategy: u32,
    pub is_nv_backup: u32,
    pub nand_page_type: u32,
    pub product_alias: String,
    pub oma_dm_product_flag: u32,
    pub is_oma_dm: u32,
    pub is_preload: u32,
    pub magic: u32,
    pub crc1: u16,
    pub crc2: u16,
}

impl PacHeader {
    /// Parses a primary header from a raw 2124-byte slice.
    pub fn from_bytes(buf: &[u8; PAC_HEADER_SIZE]) -> Self {
        let mut cursor = ByteCursor::new(buf);
        let version = cursor.read_utf16_le(44);
        let hi_size = cursor.read_u32_le();
        let lo_size = cursor.read_u32_le();
        let product_name = cursor.read_utf16_le(512);
        let firmware_name = cursor.read_utf16_le(512);
        let partition_count = cursor.read_u32_le();
        let partitions_list_start = cursor.read_u32_le();
        let mode = cursor.read_u32_le();
        let flash_type = cursor.read_u32_le();
        let nand_strategy = cursor.read_u32_le();
        let is_nv_backup = cursor.read_u32_le();
        let nand_page_type = cursor.read_u32_le();
        let product_alias = cursor.read_utf16_le(200);
        let oma_dm_product_flag = cursor.read_u32_le();
        let is_oma_dm = cursor.read_u32_le();
        let is_preload = cursor.read_u32_le();
        cursor.skip(800); // dwReserved
        let magic = cursor.read_u32_le();
        let crc1 = cursor.read_u16_le();
        let crc2 = cursor.read_u16_le();
        debug_assert_eq!(cursor.remaining(), 0);

        Self {
            version,
            hi_size,
            lo_size,
            product_name,
            firmware_name,
            partition_count,
            partitions_list_start,
            mode,
            flash_type,
            nand_strategy,
            is_nv_backup,
            nand_page_type,
            product_alias,
            oma_dm_product_flag,
            is_oma_dm,
            is_preload,
            magic,
            crc1,
            crc2,
        }
    }

    /// Reconstructs the 64-bit declared package size from high and low 32-bit words.
    pub const fn total_size(&self) -> u64 {
        combine_u64(self.hi_size, self.lo_size)
    }

    /// Returns `true` when the header carries the standard `0xFFFAFFFA` magic value.
    pub const fn has_magic(&self) -> bool {
        self.magic == PAC_MAGIC
    }
}

/// Decoded metadata for a single partition/operation entry in the `.pac` table.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PartitionEntry {
    pub struct_size: u32,
    pub partition_id: String,
    pub file_name: String,
    pub reserved_file_name: String,
    pub hi_partition_size: u32,
    pub hi_data_offset: u32,
    pub lo_partition_size: u32,
    pub file_flag: u32,
    pub check_flag: u32,
    pub lo_data_offset: u32,
    pub can_omit_flag: u32,
    pub addr_num: u32,
    pub addrs: [u32; 5],
}

impl PartitionEntry {
    /// Parses a partition descriptor from a raw 2580-byte slice.
    pub fn from_bytes(buf: &[u8; PARTITION_HEADER_SIZE]) -> Self {
        let mut cursor = ByteCursor::new(buf);
        let struct_size = cursor.read_u32_le();
        let partition_id = cursor.read_utf16_le(512);
        let file_name = cursor.read_utf16_le(512);
        let reserved_file_name = cursor.read_utf16_le(504);
        let hi_partition_size = cursor.read_u32_le();
        let hi_data_offset = cursor.read_u32_le();
        let lo_partition_size = cursor.read_u32_le();
        let file_flag = cursor.read_u32_le();
        let check_flag = cursor.read_u32_le();
        let lo_data_offset = cursor.read_u32_le();
        let can_omit_flag = cursor.read_u32_le();
        let addr_num = cursor.read_u32_le();
        let addrs = [
            cursor.read_u32_le(),
            cursor.read_u32_le(),
            cursor.read_u32_le(),
            cursor.read_u32_le(),
            cursor.read_u32_le(),
        ];
        cursor.skip(996); // dwReserved
        debug_assert_eq!(cursor.remaining(), 0);

        Self {
            struct_size,
            partition_id,
            file_name,
            reserved_file_name,
            hi_partition_size,
            hi_data_offset,
            lo_partition_size,
            file_flag,
            check_flag,
            lo_data_offset,
            can_omit_flag,
            addr_num,
            addrs,
        }
    }

    /// Reconstructs the 64-bit byte size of this partition payload.
    pub const fn partition_size(&self) -> u64 {
        combine_u64(self.hi_partition_size, self.lo_partition_size)
    }

    /// Reconstructs the 64-bit file offset where this partition payload begins.
    pub const fn data_offset(&self) -> u64 {
        combine_u64(self.hi_data_offset, self.lo_data_offset)
    }
}

/// Result of verifying `.pac` checksums.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CrcSummary {
    /// Computed and expected CRC1 when `dwMagic == 0xFFFAFFFA`, otherwise `None`.
    pub header_crc: Option<(u16, u16)>,
    /// Computed and expected CRC2 over the rest of the archive.
    pub payload_crc: (u16, u16),
}

/// High-level reader for an open `.pac` firmware package.
pub struct PacReader<R> {
    inner: R,
    file_size: u64,
    header_bytes: [u8; PAC_HEADER_SIZE],
    header: PacHeader,
}

impl<R: Read + Seek> PacReader<R> {
    /// Opens a seekable stream, validates its `.pac` header, and prepares partition inspection.
    pub fn open(mut reader: R, actual_size: u64) -> Result<Self, PacError> {
        if actual_size < PAC_HEADER_SIZE as u64 {
            return Err(PacError::FileTooSmall {
                actual: actual_size,
                minimum: PAC_HEADER_SIZE as u64,
            });
        }

        reader.seek(SeekFrom::Start(0))?;
        let mut header_bytes = [0u8; PAC_HEADER_SIZE];
        reader.read_exact(&mut header_bytes)?;

        let header = PacHeader::from_bytes(&header_bytes);
        if !SUPPORTED_VERSIONS.contains(&header.version.as_str()) {
            return Err(PacError::UnsupportedVersion(header.version));
        }

        let declared_size = header.total_size();
        if declared_size != actual_size {
            return Err(PacError::PackageSizeMismatch {
                header_size: declared_size,
                file_size: actual_size,
            });
        }

        Ok(Self {
            inner: reader,
            file_size: actual_size,
            header_bytes,
            header,
        })
    }

    /// Returns the decoded primary `.pac` header.
    pub fn header(&self) -> &PacHeader {
        &self.header
    }

    /// Verifies `wCRC1` (when magic matches) and `wCRC2` over the remainder of the archive.
    pub fn verify_crc<F>(&mut self, mut on_payload_progress: F) -> Result<CrcSummary, PacError>
    where
        F: FnMut(u64, u64),
    {
        let header_crc = if self.header.has_magic() {
            let computed = crc16::checksum(&self.header_bytes[..PAC_HEADER_CRC_SPAN]);
            let expected = self.header.crc1;
            if computed != expected {
                return Err(PacError::CrcMismatch {
                    stage: CrcStage::Header,
                    computed,
                    expected,
                });
            }
            Some((computed, expected))
        } else {
            None
        };

        let payload_len = self.file_size - PAC_HEADER_SIZE as u64;
        self.inner.seek(SeekFrom::Start(PAC_HEADER_SIZE as u64))?;

        let mut hasher = Crc16::new();
        let mut buffer = vec![0u8; IO_CHUNK_SIZE];
        let mut processed = 0u64;

        while processed < payload_len {
            let remaining = (payload_len - processed) as usize;
            let to_read = remaining.min(buffer.len());
            self.inner.read_exact(&mut buffer[..to_read])?;
            hasher.update(&buffer[..to_read]);
            processed += to_read as u64;
            on_payload_progress(processed, payload_len);
        }

        let computed_crc2 = hasher.finish();
        let expected_crc2 = self.header.crc2;
        if computed_crc2 != expected_crc2 {
            return Err(PacError::CrcMismatch {
                stage: CrcStage::Payload,
                computed: computed_crc2,
                expected: expected_crc2,
            });
        }

        Ok(CrcSummary {
            header_crc,
            payload_crc: (computed_crc2, expected_crc2),
        })
    }

    /// Reads and validates all partition descriptor entries from the archive table.
    pub fn read_partitions(&mut self) -> Result<Vec<PartitionEntry>, PacError> {
        let table_start = u64::from(self.header.partitions_list_start);
        let count = self.header.partition_count;
        let table_bytes = u64::from(count)
            .checked_mul(PARTITION_HEADER_SIZE as u64)
            .ok_or(PacError::PartitionTableOutOfBounds {
                start: table_start,
                count,
                file_size: self.file_size,
            })?;
        let table_end = table_start
            .checked_add(table_bytes)
            .ok_or(PacError::PartitionTableOutOfBounds {
                start: table_start,
                count,
                file_size: self.file_size,
            })?;

        if table_end > self.file_size {
            return Err(PacError::PartitionTableOutOfBounds {
                start: table_start,
                count,
                file_size: self.file_size,
            });
        }

        self.inner.seek(SeekFrom::Start(table_start))?;
        let mut entries = Vec::with_capacity(count as usize);
        let mut raw = [0u8; PARTITION_HEADER_SIZE];

        for idx in 0..count {
            self.inner.read_exact(&mut raw)?;
            let entry = PartitionEntry::from_bytes(&raw);
            if entry.struct_size != PARTITION_HEADER_SIZE as u32 {
                return Err(PacError::InvalidPartitionHeaderSize {
                    index: idx,
                    actual: entry.struct_size,
                    expected: PARTITION_HEADER_SIZE as u32,
                });
            }
            entries.push(entry);
        }

        Ok(entries)
    }

    /// Extracts a single non-empty partition payload into `output_dir`.
    /// Returns `Ok(None)` when the entry has zero payload bytes.
    pub fn extract_entry<F>(
        &mut self,
        entry: &PartitionEntry,
        output_dir: &Path,
        mut on_progress: F,
    ) -> Result<Option<PathBuf>, PacError>
    where
        F: FnMut(u64, u64),
    {
        let size = entry.partition_size();
        if size == 0 {
            return Ok(None);
        }

        let offset = entry.data_offset();
        let end_offset = offset
            .checked_add(size)
            .ok_or_else(|| PacError::PartitionOutOfBounds {
                name: entry.file_name.clone(),
                offset,
                size,
                file_size: self.file_size,
            })?;
        if end_offset > self.file_size {
            return Err(PacError::PartitionOutOfBounds {
                name: entry.file_name.clone(),
                offset,
                size,
                file_size: self.file_size,
            });
        }

        let clean_name = sanitize_output_filename(&entry.file_name).ok_or_else(|| {
            PacError::InvalidEntryFileName {
                partition_id: entry.partition_id.clone(),
                raw_name: entry.file_name.clone(),
            }
        })?;

        let dest_path = output_dir.join(clean_name);
        self.inner.seek(SeekFrom::Start(offset))?;

        let out_file = File::create(&dest_path)?;
        let mut writer = BufWriter::new(out_file);
        let mut buffer = vec![0u8; IO_CHUNK_SIZE];
        let mut written = 0u64;

        while written < size {
            let remaining = (size - written) as usize;
            let to_read = remaining.min(buffer.len());
            self.inner.read_exact(&mut buffer[..to_read])?;
            writer.write_all(&buffer[..to_read])?;
            written += to_read as u64;
            on_progress(written, size);
        }
        writer.flush()?;

        Ok(Some(dest_path))
    }
}

/// Convenience helper for opening a `.pac` file from disk.
pub fn open_pac_file(path: &Path) -> Result<PacReader<File>, PacError> {
    let file = File::open(path)?;
    let size = file.metadata()?.len();
    PacReader::open(file, size)
}

/// Ensures the target extraction directory exists and is not a regular file.
pub fn prepare_output_dir(output_dir: &Path) -> Result<(), PacError> {
    if output_dir.is_file() {
        return Err(PacError::OutputPathIsFile(output_dir.to_path_buf()));
    }
    fs::create_dir_all(output_dir)?;
    Ok(())
}

const fn combine_u64(hi: u32, lo: u32) -> u64 {
    ((hi as u64) << 32) | (lo as u64)
}

fn sanitize_output_filename(raw: &str) -> Option<&str> {
    let leaf = raw
        .rsplit(['/', '\\'])
        .find(|segment| !segment.is_empty())?;
    if leaf == "." || leaf == ".." {
        return None;
    }
    Some(leaf)
}

struct ByteCursor<'a> {
    bytes: &'a [u8],
    offset: usize,
}

impl<'a> ByteCursor<'a> {
    fn new(bytes: &'a [u8]) -> Self {
        Self { bytes, offset: 0 }
    }

    fn remaining(&self) -> usize {
        self.bytes.len() - self.offset
    }

    fn skip(&mut self, len: usize) {
        self.offset += len;
    }

    fn read_slice(&mut self, len: usize) -> &'a [u8] {
        let slice = &self.bytes[self.offset..self.offset + len];
        self.offset += len;
        slice
    }

    fn read_u16_le(&mut self) -> u16 {
        let s = self.read_slice(2);
        u16::from_le_bytes([s[0], s[1]])
    }

    fn read_u32_le(&mut self) -> u32 {
        let s = self.read_slice(4);
        u32::from_le_bytes([s[0], s[1], s[2], s[3]])
    }

    fn read_utf16_le(&mut self, byte_len: usize) -> String {
        let raw = self.read_slice(byte_len);
        let units: Vec<u16> = raw
            .as_chunks::<2>()
            .0
            .iter()
            .map(|&pair| u16::from_le_bytes(pair))
            .take_while(|&unit| unit != 0)
            .collect();
        String::from_utf16_lossy(&units)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn write_utf16_le(dst: &mut [u8], text: &str) {
        dst.fill(0);
        for (idx, unit) in text.encode_utf16().enumerate() {
            let offset = idx * 2;
            if offset + 2 > dst.len() {
                break;
            }
            dst[offset..offset + 2].copy_from_slice(&unit.to_le_bytes());
        }
    }

    fn build_synthetic_pac(partitions: &[(&str, &str, &[u8])]) -> Vec<u8> {
        let table_start = PAC_HEADER_SIZE;
        let table_bytes = partitions.len() * PARTITION_HEADER_SIZE;
        let mut payload_offset = table_start + table_bytes;
        let payload_total: usize = partitions.iter().map(|(_, _, data)| data.len()).sum();
        let total_size = payload_offset + payload_total;

        let mut image = vec![0u8; total_size];

        // Populate partition entries and payloads
        for (idx, (part_id, file_name, data)) in partitions.iter().enumerate() {
            let entry_off = table_start + idx * PARTITION_HEADER_SIZE;
            let entry = &mut image[entry_off..entry_off + PARTITION_HEADER_SIZE];
            entry[0..4].copy_from_slice(&(PARTITION_HEADER_SIZE as u32).to_le_bytes());
            write_utf16_le(&mut entry[4..516], part_id);
            write_utf16_le(&mut entry[516..1028], file_name);

            let size = data.len() as u64;
            let offset = payload_offset as u64;
            entry[1532..1536].copy_from_slice(&((size >> 32) as u32).to_le_bytes());
            entry[1536..1540].copy_from_slice(&((offset >> 32) as u32).to_le_bytes());
            entry[1540..1544].copy_from_slice(&(size as u32).to_le_bytes());
            entry[1544..1548].copy_from_slice(&(if size > 0 { 1u32 } else { 0u32 }).to_le_bytes());
            entry[1548..1552].copy_from_slice(&1u32.to_le_bytes());
            entry[1552..1556].copy_from_slice(&(offset as u32).to_le_bytes());

            image[payload_offset..payload_offset + data.len()].copy_from_slice(data);
            payload_offset += data.len();
        }

        // Populate PAC header
        let hdr = &mut image[0..PAC_HEADER_SIZE];
        write_utf16_le(&mut hdr[0..44], "BP_R2.0.1");
        let total_u64 = total_size as u64;
        hdr[44..48].copy_from_slice(&((total_u64 >> 32) as u32).to_le_bytes());
        hdr[48..52].copy_from_slice(&(total_u64 as u32).to_le_bytes());
        write_utf16_le(&mut hdr[52..564], "SC9863A_Test");
        write_utf16_le(&mut hdr[564..1076], "TEST_FIRMWARE_V1");
        hdr[1076..1080].copy_from_slice(&(partitions.len() as u32).to_le_bytes());
        hdr[1080..1084].copy_from_slice(&(table_start as u32).to_le_bytes());
        write_utf16_le(&mut hdr[1104..1304], "AliasTest");
        hdr[2116..2120].copy_from_slice(&PAC_MAGIC.to_le_bytes());

        let crc1 = crc16::checksum(&image[..PAC_HEADER_CRC_SPAN]);
        let crc2 = crc16::checksum(&image[PAC_HEADER_SIZE..]);
        image[2120..2122].copy_from_slice(&crc1.to_le_bytes());
        image[2122..2124].copy_from_slice(&crc2.to_le_bytes());

        image
    }

    #[test]
    fn parses_verifies_and_extracts_synthetic_pac() {
        let fdl_bytes = b"FDL1-BINARY-PAYLOAD";
        let boot_bytes = vec![0xA5u8; 100_000];
        let image = build_synthetic_pac(&[
            ("FLASH", "", &[]),
            ("FDL1", "fdl1.bin", fdl_bytes),
            ("BOOT", "boot.img", &boot_bytes),
        ]);
        let total_len = image.len() as u64;

        let mut reader = PacReader::open(Cursor::new(image), total_len).expect("valid header");
        assert_eq!(reader.header().version, "BP_R2.0.1");
        assert_eq!(reader.header().product_name, "SC9863A_Test");
        assert_eq!(reader.header().partition_count, 3);

        let crc = reader.verify_crc(|_, _| {}).expect("CRC must match");
        assert!(crc.header_crc.is_some());

        let entries = reader.read_partitions().expect("partition table");
        assert_eq!(entries.len(), 3);
        assert_eq!(entries[0].partition_size(), 0);
        assert_eq!(entries[1].file_name, "fdl1.bin");
        assert_eq!(entries[2].partition_size(), 100_000);

        let temp_dir = std::env::temp_dir().join(format!(
            "pac_extractor_test_{}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&temp_dir);
        prepare_output_dir(&temp_dir).expect("create temp outdir");

        let mut extracted = Vec::new();
        for entry in &entries {
            if let Some(path) = reader.extract_entry(entry, &temp_dir, |_, _| {}).unwrap() {
                extracted.push(path);
            }
        }
        assert_eq!(extracted.len(), 2);
        assert_eq!(fs::read(temp_dir.join("fdl1.bin")).unwrap(), fdl_bytes);
        assert_eq!(fs::read(temp_dir.join("boot.img")).unwrap(), boot_bytes);

        let _ = fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn detects_corrupted_crc1_and_crc2() {
        let mut image = build_synthetic_pac(&[("FDL1", "fdl1.bin", b"hello")]);
        let len = image.len() as u64;

        // Corrupt a byte in payload -> CRC2 failure
        let last = image.len() - 1;
        image[last] ^= 0xFF;
        let mut reader = PacReader::open(Cursor::new(image.clone()), len).unwrap();
        let err = reader.verify_crc(|_, _| {}).unwrap_err();
        assert!(matches!(
            err,
            PacError::CrcMismatch {
                stage: CrcStage::Payload,
                ..
            }
        ));

        // Corrupt a byte in header (product name) -> CRC1 failure
        image[54] ^= 0x01;
        let mut reader = PacReader::open(Cursor::new(image), len).unwrap();
        let err = reader.verify_crc(|_, _| {}).unwrap_err();
        assert!(matches!(
            err,
            PacError::CrcMismatch {
                stage: CrcStage::Header,
                ..
            }
        ));
    }

    #[test]
    fn rejects_unsupported_version_too_small_and_wrong_size() {
        let mut image = build_synthetic_pac(&[]);
        let len = image.len() as u64;
        write_utf16_le(&mut image[0..44], "BP_R9.9.9");
        let err = PacReader::open(Cursor::new(image), len)
            .err()
            .expect("should reject version");
        assert!(matches!(err, PacError::UnsupportedVersion(_)));

        let tiny = vec![0u8; PAC_HEADER_SIZE - 1];
        let err = PacReader::open(Cursor::new(tiny), (PAC_HEADER_SIZE - 1) as u64)
            .err()
            .expect("should reject undersized file");
        assert!(matches!(err, PacError::FileTooSmall { .. }));

        let image2 = build_synthetic_pac(&[("FDL1", "fdl1.bin", b"payload")]);
        let len2 = image2.len() as u64;
        let err = PacReader::open(Cursor::new(image2), len2 - 1)
            .err()
            .expect("should reject size mismatch");
        assert!(matches!(err, PacError::PackageSizeMismatch { .. }));
    }

    #[test]
    fn sanitizes_traversal_paths() {
        assert_eq!(sanitize_output_filename("../../etc/passwd"), Some("passwd"));
        assert_eq!(sanitize_output_filename(r"C:\tmp\boot.img"), Some("boot.img"));
        assert_eq!(sanitize_output_filename(".."), None);
        assert_eq!(sanitize_output_filename(""), None);
    }
}
