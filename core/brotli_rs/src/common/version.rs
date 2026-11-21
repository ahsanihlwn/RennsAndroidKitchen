//! Brotli format and library version constants.

pub const BROTLI_VERSION_MAJOR: u32 = 1;
pub const BROTLI_VERSION_MINOR: u32 = 2;
pub const BROTLI_VERSION_PATCH: u32 = 0;

pub const fn brotli_make_hex_version(major: u32, minor: u32, patch: u32) -> u32 {
    (major << 24) | (minor << 12) | patch
}

pub const BROTLI_VERSION: u32 =
    brotli_make_hex_version(BROTLI_VERSION_MAJOR, BROTLI_VERSION_MINOR, BROTLI_VERSION_PATCH);
