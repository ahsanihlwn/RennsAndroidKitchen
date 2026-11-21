//! Binary structures, constants, and layout helpers for Android logical partition (liblp) metadata.

use std::fmt;

use crate::sparse::SparseError;

pub const LP_METADATA_GEOMETRY_MAGIC: u32 = 0x616c_4467;
pub const LP_METADATA_GEOMETRY_SIZE: usize = 4096;
pub const LP_METADATA_GEOMETRY_STRUCT_SIZE: usize = 52;

pub const LP_METADATA_HEADER_MAGIC: u32 = 0x414C_5030;

pub const LP_METADATA_MAJOR_VERSION: u16 = 10;
pub const LP_METADATA_MINOR_VERSION_MIN: u16 = 0;
pub const LP_METADATA_MINOR_VERSION_MAX: u16 = 2;
pub const LP_METADATA_VERSION_FOR_UPDATED_ATTR: u16 = 1;
pub const LP_METADATA_VERSION_FOR_EXPANDED_HEADER: u16 = 2;

pub const LP_METADATA_HEADER_V1_0_SIZE: usize = 128;
pub const LP_METADATA_HEADER_V1_2_SIZE: usize = 256;

pub const LP_METADATA_TABLE_DESCRIPTOR_SIZE: usize = 12;
pub const LP_METADATA_PARTITION_SIZE: usize = 52;
pub const LP_METADATA_EXTENT_SIZE: usize = 24;
pub const LP_METADATA_GROUP_SIZE: usize = 48;
pub const LP_METADATA_BLOCK_DEVICE_SIZE: usize = 64;

pub const LP_PARTITION_ATTR_NONE: u32 = 0x0;
pub const LP_PARTITION_ATTR_READONLY: u32 = 1 << 0;
pub const LP_PARTITION_ATTR_SLOT_SUFFIXED: u32 = 1 << 1;
pub const LP_PARTITION_ATTR_UPDATED: u32 = 1 << 2;
pub const LP_PARTITION_ATTR_DISABLED: u32 = 1 << 3;

pub const LP_PARTITION_ATTRIBUTE_MASK_V0: u32 =
    LP_PARTITION_ATTR_READONLY | LP_PARTITION_ATTR_SLOT_SUFFIXED;
pub const LP_PARTITION_ATTRIBUTE_MASK_V1: u32 =
    LP_PARTITION_ATTR_UPDATED | LP_PARTITION_ATTR_DISABLED;
pub const LP_PARTITION_ATTRIBUTE_MASK: u32 =
    LP_PARTITION_ATTRIBUTE_MASK_V0 | LP_PARTITION_ATTRIBUTE_MASK_V1;

pub const LP_HEADER_FLAG_VIRTUAL_AB_DEVICE: u32 = 0x1;

pub const LP_TARGET_TYPE_LINEAR: u32 = 0;
pub const LP_TARGET_TYPE_ZERO: u32 = 1;

pub const LP_GROUP_SLOT_SUFFIXED: u32 = 1 << 0;
pub const LP_BLOCK_DEVICE_SLOT_SUFFIXED: u32 = 1 << 0;

pub const LP_METADATA_DEFAULT_PARTITION_NAME: &str = "super";
pub const LP_SECTOR_SIZE: u64 = 512;
pub const LP_PARTITION_RESERVED_BYTES: u64 = 4096;

pub const DEFAULT_PARTITION_ALIGNMENT: u32 = 1024 * 1024;
pub const DEFAULT_BLOCK_SIZE: u32 = 4096;
pub const DEFAULT_GROUP: &str = "default";

#[derive(Debug)]
pub enum LpError {
    Io(std::io::Error),
    Sparse(SparseError),
    InvalidMetadata(String),
}

impl fmt::Display for LpError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            LpError::Io(e) => write!(f, "I/O error: {e}"),
            LpError::Sparse(e) => write!(f, "{e}"),
            LpError::InvalidMetadata(msg) => write!(f, "{msg}"),
        }
    }
}

impl std::error::Error for LpError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            LpError::Io(e) => Some(e),
            LpError::Sparse(e) => Some(e),
            LpError::InvalidMetadata(_) => None,
        }
    }
}

impl From<std::io::Error> for LpError {
    fn from(e: std::io::Error) -> Self {
        LpError::Io(e)
    }
}

impl From<SparseError> for LpError {
    fn from(e: SparseError) -> Self {
        LpError::Sparse(e)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BlockDeviceInfo {
    pub size: u64,
    pub alignment: u32,
    pub alignment_offset: u32,
    pub logical_block_size: u32,
    pub partition_name: [u8; 36],
    pub partition_name_len: usize,
}

impl BlockDeviceInfo {
    pub fn new(
        partition_name: &str,
        size: u64,
        alignment: u32,
        alignment_offset: u32,
        logical_block_size: u32,
    ) -> Result<Self, LpError> {
        let mut buf = [0u8; 36];
        let bytes = partition_name.as_bytes();
        if bytes.len() > buf.len() {
            return Err(LpError::InvalidMetadata(format!(
                "Partition name {partition_name} exceeds maximum length."
            )));
        }
        buf[..bytes.len()].copy_from_slice(bytes);
        Ok(Self {
            size,
            alignment,
            alignment_offset,
            logical_block_size,
            partition_name: buf,
            partition_name_len: bytes.len(),
        })
    }

    pub fn name(&self) -> &str {
        std::str::from_utf8(&self.partition_name[..self.partition_name_len]).unwrap_or("")
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LpMetadataGeometry {
    pub magic: u32,
    pub struct_size: u32,
    pub checksum: [u8; 32],
    pub metadata_max_size: u32,
    pub metadata_slot_count: u32,
    pub logical_block_size: u32,
}

impl LpMetadataGeometry {
    pub fn from_bytes(buf: &[u8]) -> Result<Self, LpError> {
        if buf.len() < LP_METADATA_GEOMETRY_STRUCT_SIZE {
            return Err(LpError::InvalidMetadata(
                "Geometry buffer smaller than struct size.".into(),
            ));
        }
        let mut checksum = [0u8; 32];
        checksum.copy_from_slice(&buf[8..40]);
        Ok(Self {
            magic: u32::from_le_bytes(buf[0..4].try_into().unwrap()),
            struct_size: u32::from_le_bytes(buf[4..8].try_into().unwrap()),
            checksum,
            metadata_max_size: u32::from_le_bytes(buf[40..44].try_into().unwrap()),
            metadata_slot_count: u32::from_le_bytes(buf[44..48].try_into().unwrap()),
            logical_block_size: u32::from_le_bytes(buf[48..52].try_into().unwrap()),
        })
    }

    pub fn to_bytes(&self) -> [u8; LP_METADATA_GEOMETRY_STRUCT_SIZE] {
        let mut out = [0u8; LP_METADATA_GEOMETRY_STRUCT_SIZE];
        out[0..4].copy_from_slice(&self.magic.to_le_bytes());
        out[4..8].copy_from_slice(&self.struct_size.to_le_bytes());
        out[8..40].copy_from_slice(&self.checksum);
        out[40..44].copy_from_slice(&self.metadata_max_size.to_le_bytes());
        out[44..48].copy_from_slice(&self.metadata_slot_count.to_le_bytes());
        out[48..52].copy_from_slice(&self.logical_block_size.to_le_bytes());
        out
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct LpMetadataTableDescriptor {
    pub offset: u32,
    pub num_entries: u32,
    pub entry_size: u32,
}

impl LpMetadataTableDescriptor {
    pub fn from_bytes(buf: &[u8]) -> Self {
        Self {
            offset: u32::from_le_bytes(buf[0..4].try_into().unwrap()),
            num_entries: u32::from_le_bytes(buf[4..8].try_into().unwrap()),
            entry_size: u32::from_le_bytes(buf[8..12].try_into().unwrap()),
        }
    }

    pub fn to_bytes(&self) -> [u8; LP_METADATA_TABLE_DESCRIPTOR_SIZE] {
        let mut out = [0u8; LP_METADATA_TABLE_DESCRIPTOR_SIZE];
        out[0..4].copy_from_slice(&self.offset.to_le_bytes());
        out[4..8].copy_from_slice(&self.num_entries.to_le_bytes());
        out[8..12].copy_from_slice(&self.entry_size.to_le_bytes());
        out
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LpMetadataHeader {
    pub magic: u32,
    pub major_version: u16,
    pub minor_version: u16,
    pub header_size: u32,
    pub header_checksum: [u8; 32],
    pub tables_size: u32,
    pub tables_checksum: [u8; 32],
    pub partitions: LpMetadataTableDescriptor,
    pub extents: LpMetadataTableDescriptor,
    pub groups: LpMetadataTableDescriptor,
    pub block_devices: LpMetadataTableDescriptor,
    pub flags: u32,
    pub reserved: [u8; 124],
}

impl LpMetadataHeader {
    pub fn from_v1_0_bytes(buf: &[u8]) -> Result<Self, LpError> {
        if buf.len() < LP_METADATA_HEADER_V1_0_SIZE {
            return Err(LpError::InvalidMetadata(
                "Header buffer smaller than v1.0 header.".into(),
            ));
        }
        let mut header_checksum = [0u8; 32];
        header_checksum.copy_from_slice(&buf[12..44]);
        let mut tables_checksum = [0u8; 32];
        tables_checksum.copy_from_slice(&buf[48..80]);

        Ok(Self {
            magic: u32::from_le_bytes(buf[0..4].try_into().unwrap()),
            major_version: u16::from_le_bytes(buf[4..6].try_into().unwrap()),
            minor_version: u16::from_le_bytes(buf[6..8].try_into().unwrap()),
            header_size: u32::from_le_bytes(buf[8..12].try_into().unwrap()),
            header_checksum,
            tables_size: u32::from_le_bytes(buf[44..48].try_into().unwrap()),
            tables_checksum,
            partitions: LpMetadataTableDescriptor::from_bytes(&buf[80..92]),
            extents: LpMetadataTableDescriptor::from_bytes(&buf[92..104]),
            groups: LpMetadataTableDescriptor::from_bytes(&buf[104..116]),
            block_devices: LpMetadataTableDescriptor::from_bytes(&buf[116..128]),
            flags: 0,
            reserved: [0u8; 124],
        })
    }

    pub fn fill_v1_2_tail(&mut self, tail: &[u8]) -> Result<(), LpError> {
        if tail.len() < LP_METADATA_HEADER_V1_2_SIZE - LP_METADATA_HEADER_V1_0_SIZE {
            return Err(LpError::InvalidMetadata(
                "Header tail buffer smaller than v1.2 extension.".into(),
            ));
        }
        self.flags = u32::from_le_bytes(tail[0..4].try_into().unwrap());
        self.reserved.copy_from_slice(&tail[4..128]);
        Ok(())
    }

    pub fn to_bytes(&self) -> Vec<u8> {
        let size = self.header_size as usize;
        let mut out = vec![0u8; size];
        out[0..4].copy_from_slice(&self.magic.to_le_bytes());
        out[4..6].copy_from_slice(&self.major_version.to_le_bytes());
        out[6..8].copy_from_slice(&self.minor_version.to_le_bytes());
        out[8..12].copy_from_slice(&self.header_size.to_le_bytes());
        out[12..44].copy_from_slice(&self.header_checksum);
        out[44..48].copy_from_slice(&self.tables_size.to_le_bytes());
        out[48..80].copy_from_slice(&self.tables_checksum);
        out[80..92].copy_from_slice(&self.partitions.to_bytes());
        out[92..104].copy_from_slice(&self.extents.to_bytes());
        out[104..116].copy_from_slice(&self.groups.to_bytes());
        out[116..128].copy_from_slice(&self.block_devices.to_bytes());
        if size >= LP_METADATA_HEADER_V1_2_SIZE {
            out[128..132].copy_from_slice(&self.flags.to_le_bytes());
            out[132..256].copy_from_slice(&self.reserved);
        }
        out
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LpMetadataPartition {
    pub name: [u8; 36],
    pub attributes: u32,
    pub first_extent_index: u32,
    pub num_extents: u32,
    pub group_index: u32,
}

impl LpMetadataPartition {
    pub fn from_bytes(buf: &[u8]) -> Result<Self, LpError> {
        if buf.len() < LP_METADATA_PARTITION_SIZE {
            return Err(LpError::InvalidMetadata("Partition entry truncated.".into()));
        }
        let mut name = [0u8; 36];
        name.copy_from_slice(&buf[0..36]);
        Ok(Self {
            name,
            attributes: u32::from_le_bytes(buf[36..40].try_into().unwrap()),
            first_extent_index: u32::from_le_bytes(buf[40..44].try_into().unwrap()),
            num_extents: u32::from_le_bytes(buf[44..48].try_into().unwrap()),
            group_index: u32::from_le_bytes(buf[48..52].try_into().unwrap()),
        })
    }

    pub fn to_bytes(&self) -> [u8; LP_METADATA_PARTITION_SIZE] {
        let mut out = [0u8; LP_METADATA_PARTITION_SIZE];
        out[0..36].copy_from_slice(&self.name);
        out[36..40].copy_from_slice(&self.attributes.to_le_bytes());
        out[40..44].copy_from_slice(&self.first_extent_index.to_le_bytes());
        out[44..48].copy_from_slice(&self.num_extents.to_le_bytes());
        out[48..52].copy_from_slice(&self.group_index.to_le_bytes());
        out
    }

    pub fn name(&self) -> String {
        name_from_fixed_array(&self.name)
    }

    pub fn set_name(&mut self, name: &str) -> bool {
        set_fixed_array(&mut self.name, name)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LpMetadataExtent {
    pub num_sectors: u64,
    pub target_type: u32,
    pub target_data: u64,
    pub target_source: u32,
}

impl LpMetadataExtent {
    pub fn from_bytes(buf: &[u8]) -> Result<Self, LpError> {
        if buf.len() < LP_METADATA_EXTENT_SIZE {
            return Err(LpError::InvalidMetadata("Extent entry truncated.".into()));
        }
        Ok(Self {
            num_sectors: u64::from_le_bytes(buf[0..8].try_into().unwrap()),
            target_type: u32::from_le_bytes(buf[8..12].try_into().unwrap()),
            target_data: u64::from_le_bytes(buf[12..20].try_into().unwrap()),
            target_source: u32::from_le_bytes(buf[20..24].try_into().unwrap()),
        })
    }

    pub fn to_bytes(&self) -> [u8; LP_METADATA_EXTENT_SIZE] {
        let mut out = [0u8; LP_METADATA_EXTENT_SIZE];
        out[0..8].copy_from_slice(&self.num_sectors.to_le_bytes());
        out[8..12].copy_from_slice(&self.target_type.to_le_bytes());
        out[12..20].copy_from_slice(&self.target_data.to_le_bytes());
        out[20..24].copy_from_slice(&self.target_source.to_le_bytes());
        out
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LpMetadataPartitionGroup {
    pub name: [u8; 36],
    pub flags: u32,
    pub maximum_size: u64,
}

impl LpMetadataPartitionGroup {
    pub fn from_bytes(buf: &[u8]) -> Result<Self, LpError> {
        if buf.len() < LP_METADATA_GROUP_SIZE {
            return Err(LpError::InvalidMetadata("Group entry truncated.".into()));
        }
        let mut name = [0u8; 36];
        name.copy_from_slice(&buf[0..36]);
        Ok(Self {
            name,
            flags: u32::from_le_bytes(buf[36..40].try_into().unwrap()),
            maximum_size: u64::from_le_bytes(buf[40..48].try_into().unwrap()),
        })
    }

    pub fn to_bytes(&self) -> [u8; LP_METADATA_GROUP_SIZE] {
        let mut out = [0u8; LP_METADATA_GROUP_SIZE];
        out[0..36].copy_from_slice(&self.name);
        out[36..40].copy_from_slice(&self.flags.to_le_bytes());
        out[40..48].copy_from_slice(&self.maximum_size.to_le_bytes());
        out
    }

    pub fn name(&self) -> String {
        name_from_fixed_array(&self.name)
    }

    pub fn set_name(&mut self, name: &str) -> bool {
        set_fixed_array(&mut self.name, name)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LpMetadataBlockDevice {
    pub first_logical_sector: u64,
    pub alignment: u32,
    pub alignment_offset: u32,
    pub size: u64,
    pub partition_name: [u8; 36],
    pub flags: u32,
}

impl LpMetadataBlockDevice {
    pub fn from_bytes(buf: &[u8]) -> Result<Self, LpError> {
        if buf.len() < LP_METADATA_BLOCK_DEVICE_SIZE {
            return Err(LpError::InvalidMetadata(
                "Block device entry truncated.".into(),
            ));
        }
        let mut partition_name = [0u8; 36];
        partition_name.copy_from_slice(&buf[24..60]);
        Ok(Self {
            first_logical_sector: u64::from_le_bytes(buf[0..8].try_into().unwrap()),
            alignment: u32::from_le_bytes(buf[8..12].try_into().unwrap()),
            alignment_offset: u32::from_le_bytes(buf[12..16].try_into().unwrap()),
            size: u64::from_le_bytes(buf[16..24].try_into().unwrap()),
            partition_name,
            flags: u32::from_le_bytes(buf[60..64].try_into().unwrap()),
        })
    }

    pub fn to_bytes(&self) -> [u8; LP_METADATA_BLOCK_DEVICE_SIZE] {
        let mut out = [0u8; LP_METADATA_BLOCK_DEVICE_SIZE];
        out[0..8].copy_from_slice(&self.first_logical_sector.to_le_bytes());
        out[8..12].copy_from_slice(&self.alignment.to_le_bytes());
        out[12..16].copy_from_slice(&self.alignment_offset.to_le_bytes());
        out[16..24].copy_from_slice(&self.size.to_le_bytes());
        out[24..60].copy_from_slice(&self.partition_name);
        out[60..64].copy_from_slice(&self.flags.to_le_bytes());
        out
    }

    pub fn partition_name(&self) -> String {
        name_from_fixed_array(&self.partition_name)
    }

    pub fn set_partition_name(&mut self, name: &str) -> bool {
        set_fixed_array(&mut self.partition_name, name)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LpMetadata {
    pub geometry: LpMetadataGeometry,
    pub header: LpMetadataHeader,
    pub partitions: Vec<LpMetadataPartition>,
    pub extents: Vec<LpMetadataExtent>,
    pub groups: Vec<LpMetadataPartitionGroup>,
    pub block_devices: Vec<LpMetadataBlockDevice>,
}

impl LpMetadata {
    pub fn super_block_device(&self) -> Option<&LpMetadataBlockDevice> {
        self.block_devices.first()
    }

    pub fn total_super_partition_size(&self) -> u64 {
        self.block_devices.iter().map(|d| d.size).sum()
    }

    pub fn find_partition(&self, name: &str) -> Option<&LpMetadataPartition> {
        self.partitions.iter().find(|p| p.name() == name)
    }

    pub fn partition_size(&self, partition: &LpMetadataPartition) -> u64 {
        let start = partition.first_extent_index as usize;
        let end = start.saturating_add(partition.num_extents as usize);
        self.extents[start..end.min(self.extents.len())]
            .iter()
            .map(|e| e.num_sectors * LP_SECTOR_SIZE)
            .sum()
    }
}

pub fn name_from_fixed_array(buf: &[u8]) -> String {
    let end = buf.iter().position(|&b| b == 0).unwrap_or(buf.len());
    String::from_utf8_lossy(&buf[..end]).into_owned()
}

pub fn set_fixed_array(buf: &mut [u8], name: &str) -> bool {
    let bytes = name.as_bytes();
    if bytes.len() > buf.len() {
        return false;
    }
    buf.fill(0);
    buf[..bytes.len()].copy_from_slice(bytes);
    true
}

pub const fn align_to(base: u64, alignment: u32) -> u64 {
    if alignment == 0 {
        return base;
    }
    let align = alignment as u64;
    let rem = base % align;
    if rem == 0 {
        base
    } else {
        base + (align - rem)
    }
}

pub const fn align_to_offset(base: u64, alignment: u32, alignment_offset: u32) -> u64 {
    let aligned = align_to(base, alignment) + alignment_offset as u64;
    let align = alignment as u64;
    if aligned >= align && aligned - align >= base {
        aligned - align
    } else {
        aligned
    }
}

pub const fn get_primary_geometry_offset() -> u64 {
    LP_PARTITION_RESERVED_BYTES
}

pub const fn get_backup_geometry_offset() -> u64 {
    get_primary_geometry_offset() + LP_METADATA_GEOMETRY_SIZE as u64
}

pub const fn get_primary_metadata_offset(geometry: &LpMetadataGeometry, slot_number: u32) -> u64 {
    LP_PARTITION_RESERVED_BYTES
        + (LP_METADATA_GEOMETRY_SIZE as u64 * 2)
        + (geometry.metadata_max_size as u64 * slot_number as u64)
}

pub const fn get_backup_metadata_offset(geometry: &LpMetadataGeometry, slot_number: u32) -> u64 {
    let start = LP_PARTITION_RESERVED_BYTES
        + (LP_METADATA_GEOMETRY_SIZE as u64 * 2)
        + (geometry.metadata_max_size as u64 * geometry.metadata_slot_count as u64);
    start + (geometry.metadata_max_size as u64 * slot_number as u64)
}

pub const fn get_total_metadata_size(metadata_max_size: u32, max_slots: u32) -> u64 {
    LP_PARTITION_RESERVED_BYTES
        + ((LP_METADATA_GEOMETRY_SIZE as u64 + metadata_max_size as u64 * max_slots as u64) * 2)
}

pub fn slot_suffix_for_slot_number(slot_number: u32) -> Result<String, LpError> {
    match slot_number {
        0 => Ok("_a".to_string()),
        1 => Ok("_b".to_string()),
        n if n < 26 => Ok(format!("_{}", (b'a' + n as u8) as char)),
        _ => Err(LpError::InvalidMetadata(format!(
            "Unsupported slot number: {slot_number}"
        ))),
    }
}

pub fn slot_number_for_slot_suffix(suffix: &str) -> Result<u32, LpError> {
    if suffix.is_empty() || suffix == "a" || suffix == "_a" {
        Ok(0)
    } else if suffix == "b" || suffix == "_b" {
        Ok(1)
    } else {
        Err(LpError::InvalidMetadata(format!(
            "Slot '{suffix}' does not have a recognized format."
        )))
    }
}

pub fn get_partition_slot_suffix(partition_name: &str) -> &str {
    if partition_name.len() <= 2 {
        return "";
    }
    let tail = &partition_name[partition_name.len() - 2..];
    if tail == "_a" || tail == "_b" {
        tail
    } else {
        ""
    }
}
