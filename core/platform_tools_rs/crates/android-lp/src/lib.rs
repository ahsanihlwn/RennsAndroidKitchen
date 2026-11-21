//! Android Dynamic Partitions (`liblp`) `super` partition metadata parser and builder (MIT).

use sha2::{Digest, Sha256};
use thiserror::Error;

/// `LpMetadataGeometry` magic (`0x616c4467`).
pub const LP_METADATA_GEOMETRY_MAGIC: u32 = 0x616c_4467;
/// `LpMetadataGeometry` padded block size (`4096` bytes).
pub const LP_METADATA_GEOMETRY_SIZE: usize = 4096;
/// `LpMetadataHeader` magic (`0x414c5030`).
pub const LP_METADATA_HEADER_MAGIC: u32 = 0x414c_5030;
/// Reserved bytes at the start of `super` before primary geometry (when on a raw block device).
pub const LP_PARTITION_RESERVED_BYTES: usize = 4096;

/// Major version of `liblp` metadata.
pub const LP_METADATA_MAJOR_VERSION: u16 = 10;
/// Default minor version of `liblp` metadata.
pub const LP_METADATA_MINOR_VERSION: u16 = 0;

/// Errors when parsing or building `liblp` dynamic partition metadata.
#[derive(Debug, Error)]
pub enum LpError {
    #[error("metadata buffer is too small ({0} bytes)")]
    TooSmall(usize),
    #[error("invalid LpMetadataGeometry magic (0x{0:08x})")]
    InvalidGeometryMagic(u32),
    #[error("LpMetadataGeometry SHA-256 checksum mismatch")]
    GeometryChecksumMismatch,
    #[error("invalid LpMetadataHeader magic (0x{0:08x})")]
    InvalidHeaderMagic(u32),
    #[error("LpMetadataHeader SHA-256 checksum mismatch")]
    HeaderChecksumMismatch,
    #[error("LpMetadata tables SHA-256 checksum mismatch")]
    TablesChecksumMismatch,
}

/// Parsed `LpMetadataGeometry` (52 bytes stored in a 4096-byte block).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LpMetadataGeometry {
    pub metadata_max_size: u32,
    pub metadata_slot_count: u32,
    pub logical_block_size: u32,
}

impl LpMetadataGeometry {
    /// Serializes this geometry into a 4096-byte padded block with its SHA-256 checksum.
    pub fn to_block_bytes(&self) -> [u8; LP_METADATA_GEOMETRY_SIZE] {
        let mut blk = [0u8; LP_METADATA_GEOMETRY_SIZE];
        blk[0..4].copy_from_slice(&LP_METADATA_GEOMETRY_MAGIC.to_le_bytes());
        blk[4..8].copy_from_slice(&52u32.to_le_bytes()); // struct_size = 52
        // bytes 8..40 = checksum (zeroed during SHA-256 calculation)
        blk[40..44].copy_from_slice(&self.metadata_max_size.to_le_bytes());
        blk[44..48].copy_from_slice(&self.metadata_slot_count.to_le_bytes());
        blk[48..52].copy_from_slice(&self.logical_block_size.to_le_bytes());

        let digest = Sha256::digest(&blk[0..52]);
        blk[8..40].copy_from_slice(&digest);
        blk
    }

    /// Parses and validates an `LpMetadataGeometry` from the first 52 bytes of `buf`.
    pub fn from_bytes(buf: &[u8]) -> Result<Self, LpError> {
        if buf.len() < 52 {
            return Err(LpError::TooSmall(buf.len()));
        }
        let magic = u32::from_le_bytes(buf[0..4].try_into().unwrap());
        if magic != LP_METADATA_GEOMETRY_MAGIC {
            return Err(LpError::InvalidGeometryMagic(magic));
        }
        let mut check_buf = [0u8; 52];
        check_buf.copy_from_slice(&buf[0..52]);
        let expected_csum = buf[8..40].to_vec();
        check_buf[8..40].fill(0);
        let actual_csum = Sha256::digest(check_buf);
        if actual_csum.as_slice() != expected_csum.as_slice() {
            return Err(LpError::GeometryChecksumMismatch);
        }
        Ok(Self {
            metadata_max_size: u32::from_le_bytes(buf[40..44].try_into().unwrap()),
            metadata_slot_count: u32::from_le_bytes(buf[44..48].try_into().unwrap()),
            logical_block_size: u32::from_le_bytes(buf[48..52].try_into().unwrap()),
        })
    }
}

/// Logical partition entry inside `LpMetadata`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LpPartition {
    pub name: String,
    pub attributes: u32,
    pub first_extent_index: u32,
    pub num_extents: u32,
    pub group_index: u32,
}

/// Builds a valid `super_empty.img` image buffer containing `LpMetadataGeometry` (2 copies)
/// and empty `LpMetadataHeader` slots for `fastboot wipe-super`.
pub fn build_empty_super_metadata(
    metadata_max_size: u32,
    metadata_slot_count: u32,
    logical_block_size: u32,
    super_size_bytes: u64,
) -> Vec<u8> {
    let geom = LpMetadataGeometry {
        metadata_max_size,
        metadata_slot_count,
        logical_block_size,
    };
    let geom_blk = geom.to_block_bytes();

    // Build one default group ("default") and one block device ("super")
    let mut tables = Vec::new();
    // 1. Partitions table: 0 entries (52 bytes each)
    let part_count = 0u32;
    // 2. Extents table: 0 entries (24 bytes each)
    let ext_count = 0u32;
    // 3. Groups table: 1 entry ("default", 48 bytes)
    let grp_offset = tables.len() as u32;
    let mut grp_entry = [0u8; 48];
    grp_entry[0..7].copy_from_slice(b"default");
    tables.extend_from_slice(&grp_entry);
    // 4. Block devices table: 1 entry ("super", 64 bytes)
    let bdev_offset = tables.len() as u32;
    let mut bdev_entry = [0u8; 64];
    let first_logical_sector =
        ((LP_PARTITION_RESERVED_BYTES + LP_METADATA_GEOMETRY_SIZE * 2) as u64
            + u64::from(metadata_max_size) * u64::from(metadata_slot_count) * 2)
            .div_ceil(512);
    bdev_entry[0..8].copy_from_slice(&first_logical_sector.to_le_bytes());
    bdev_entry[8..12].copy_from_slice(&(1024u32 * 1024).to_le_bytes()); // alignment = 1 MiB
    bdev_entry[12..16].copy_from_slice(&0u32.to_le_bytes()); // alignment_offset
    bdev_entry[16..24].copy_from_slice(&super_size_bytes.to_le_bytes());
    bdev_entry[24..29].copy_from_slice(b"super");
    tables.extend_from_slice(&bdev_entry);

    let tables_csum = Sha256::digest(&tables);

    // LpMetadataHeader v10.0 is 128 bytes
    let mut hdr = [0u8; 128];
    hdr[0..4].copy_from_slice(&LP_METADATA_HEADER_MAGIC.to_le_bytes());
    hdr[4..6].copy_from_slice(&LP_METADATA_MAJOR_VERSION.to_le_bytes());
    hdr[6..8].copy_from_slice(&LP_METADATA_MINOR_VERSION.to_le_bytes());
    hdr[8..12].copy_from_slice(&128u32.to_le_bytes()); // header_size
    // hdr[12..44] = header_checksum
    hdr[44..48].copy_from_slice(&(tables.len() as u32).to_le_bytes()); // tables_size
    hdr[48..80].copy_from_slice(&tables_csum);
    // Table descriptors (12 bytes each: offset, num_entries, entry_size):
    // partitions at 80..92
    hdr[80..84].copy_from_slice(&0u32.to_le_bytes());
    hdr[84..88].copy_from_slice(&part_count.to_le_bytes());
    hdr[88..92].copy_from_slice(&52u32.to_le_bytes());
    // extents at 92..104
    hdr[92..96].copy_from_slice(&0u32.to_le_bytes());
    hdr[96..100].copy_from_slice(&ext_count.to_le_bytes());
    hdr[100..104].copy_from_slice(&24u32.to_le_bytes());
    // groups at 104..116
    hdr[104..108].copy_from_slice(&grp_offset.to_le_bytes());
    hdr[108..112].copy_from_slice(&1u32.to_le_bytes());
    hdr[112..116].copy_from_slice(&48u32.to_le_bytes());
    // block_devices at 116..128
    hdr[116..120].copy_from_slice(&bdev_offset.to_le_bytes());
    hdr[120..124].copy_from_slice(&1u32.to_le_bytes());
    hdr[124..128].copy_from_slice(&64u32.to_le_bytes());

    let hdr_csum = Sha256::digest(hdr);
    hdr[12..44].copy_from_slice(&hdr_csum);

    let mut out = Vec::with_capacity(
        LP_METADATA_GEOMETRY_SIZE * 2
            + (metadata_max_size as usize) * (metadata_slot_count as usize),
    );
    out.extend_from_slice(&geom_blk);
    out.extend_from_slice(&geom_blk);
    for _ in 0..metadata_slot_count {
        let mut slot_buf = vec![0u8; metadata_max_size as usize];
        slot_buf[0..128].copy_from_slice(&hdr);
        slot_buf[128..128 + tables.len()].copy_from_slice(&tables);
        out.extend_from_slice(&slot_buf);
    }
    out
}

/// Parses an `LpMetadataGeometry` and partition list from a `super_empty.img` or raw `super`
/// metadata buffer.
pub fn parse_super_metadata(
    data: &[u8],
) -> Result<(LpMetadataGeometry, Vec<LpPartition>), LpError> {
    let geom_offset = if data.len() >= 52
        && u32::from_le_bytes(data[0..4].try_into().unwrap()) == LP_METADATA_GEOMETRY_MAGIC
    {
        0
    } else if data.len() >= LP_PARTITION_RESERVED_BYTES + 52
        && u32::from_le_bytes(
            data[LP_PARTITION_RESERVED_BYTES..LP_PARTITION_RESERVED_BYTES + 4]
                .try_into()
                .unwrap(),
        ) == LP_METADATA_GEOMETRY_MAGIC
    {
        LP_PARTITION_RESERVED_BYTES
    } else {
        return Err(LpError::TooSmall(data.len()));
    };

    let geom = LpMetadataGeometry::from_bytes(&data[geom_offset..])?;
    let hdr_offset = geom_offset + LP_METADATA_GEOMETRY_SIZE * 2;
    if data.len() < hdr_offset + 128 {
        return Err(LpError::TooSmall(data.len()));
    }
    let hdr = &data[hdr_offset..];
    let magic = u32::from_le_bytes(hdr[0..4].try_into().unwrap());
    if magic != LP_METADATA_HEADER_MAGIC {
        return Err(LpError::InvalidHeaderMagic(magic));
    }
    let hdr_size = u32::from_le_bytes(hdr[8..12].try_into().unwrap()) as usize;
    let tables_size = u32::from_le_bytes(hdr[44..48].try_into().unwrap()) as usize;
    if hdr.len() < hdr_size + tables_size || hdr_size < 128 {
        return Err(LpError::TooSmall(data.len()));
    }

    let mut hdr_check = hdr[0..hdr_size].to_vec();
    let expected_hdr_csum = hdr[12..44].to_vec();
    hdr_check[12..44].fill(0);
    if Sha256::digest(&hdr_check).as_slice() != expected_hdr_csum.as_slice() {
        return Err(LpError::HeaderChecksumMismatch);
    }

    let tables = &hdr[hdr_size..hdr_size + tables_size];
    let expected_tbl_csum = &hdr[48..80];
    if Sha256::digest(tables).as_slice() != expected_tbl_csum {
        return Err(LpError::TablesChecksumMismatch);
    }

    let part_off = u32::from_le_bytes(hdr[80..84].try_into().unwrap()) as usize;
    let part_num = u32::from_le_bytes(hdr[84..88].try_into().unwrap()) as usize;
    let part_esz = u32::from_le_bytes(hdr[88..92].try_into().unwrap()) as usize;

    let mut partitions = Vec::with_capacity(part_num);
    for i in 0..part_num {
        let off = part_off + i * part_esz;
        if off + 52 > tables.len() {
            break;
        }
        let p = &tables[off..off + 52];
        let name_end = p[0..36].iter().position(|&b| b == 0).unwrap_or(36);
        let name = String::from_utf8_lossy(&p[0..name_end]).into_owned();
        let attributes = u32::from_le_bytes(p[36..40].try_into().unwrap());
        let first_extent_index = u32::from_le_bytes(p[40..44].try_into().unwrap());
        let num_extents = u32::from_le_bytes(p[44..48].try_into().unwrap());
        let group_index = u32::from_le_bytes(p[48..52].try_into().unwrap());
        partitions.push(LpPartition {
            name,
            attributes,
            first_extent_index,
            num_extents,
            group_index,
        });
    }

    Ok((geom, partitions))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_and_parse_empty_super_metadata() {
        let buf = build_empty_super_metadata(65536, 2, 4096, 4 * 1024 * 1024 * 1024);
        let (geom, parts) = parse_super_metadata(&buf).unwrap();
        assert_eq!(geom.metadata_max_size, 65536);
        assert_eq!(geom.metadata_slot_count, 2);
        assert_eq!(geom.logical_block_size, 4096);
        assert!(parts.is_empty());
    }
}
