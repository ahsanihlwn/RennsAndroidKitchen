//! Logical partition (liblp) metadata reading, building, and image serialization.

pub mod builder;
pub mod dump;
pub mod format;
pub mod reader;
pub mod unpack;
pub mod writer;

pub use builder::{Extent, Interval, MetadataBuilder, Partition, PartitionGroup};
pub use dump::{format_metadata_json, format_metadata_text};
pub use format::{
    BlockDeviceInfo, LpError, LpMetadata, LpMetadataBlockDevice, LpMetadataExtent,
    LpMetadataGeometry, LpMetadataHeader, LpMetadataPartition, LpMetadataPartitionGroup,
    LpMetadataTableDescriptor, DEFAULT_BLOCK_SIZE, DEFAULT_GROUP, DEFAULT_PARTITION_ALIGNMENT,
    LP_HEADER_FLAG_VIRTUAL_AB_DEVICE, LP_METADATA_DEFAULT_PARTITION_NAME,
    LP_METADATA_GEOMETRY_MAGIC, LP_METADATA_GEOMETRY_SIZE, LP_METADATA_HEADER_MAGIC,
    LP_PARTITION_ATTR_DISABLED, LP_PARTITION_ATTR_NONE, LP_PARTITION_ATTR_READONLY,
    LP_PARTITION_ATTR_SLOT_SUFFIXED, LP_PARTITION_ATTR_UPDATED, LP_PARTITION_RESERVED_BYTES,
    LP_SECTOR_SIZE, LP_TARGET_TYPE_LINEAR, LP_TARGET_TYPE_ZERO,
};
pub use reader::{
    is_empty_super_image, parse_geometry, parse_metadata, read_from_image_blob,
    read_from_image_reader, read_logical_partition_geometry, read_metadata, read_metadata_auto,
};
pub use writer::{
    serialize_geometry, serialize_metadata, write_empty_image_file, write_split_image_files,
    write_to_image_file, write_to_image_file_with_progress, ImageBuilder,
};
pub use unpack::{
    extract_partition_to_dir, extract_partition_to_dir_with_progress, filter_partitions,
    inspect_super_image, select_partitions_for_extract, strip_slot_suffix, unpack_super_image,
    unpack_super_image_with_progress, unsparse_super_image, unsparse_super_image_with_progress,
    SparseHoleWriter, SuperImageInfo,
};
