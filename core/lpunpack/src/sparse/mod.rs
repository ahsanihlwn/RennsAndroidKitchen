//! Android sparse image format support (reading and writing).

pub mod format;
pub mod reader;
pub mod writer;

pub use format::{SparseError, SparseHeader};
pub use reader::{ImageSource, SparseReader};
pub use writer::SparseFile;
