#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Format {
    Unknown,
    // Boot container formats
    ChromeOs,
    Aosp,
    AospVendor,
    Dhtb,
    Blob,
    // Supported compression formats
    Gzip,
    Zopfli,
    Xz,
    Lzma,
    Bzip2,
    Lz4,
    Lz4Legacy,
    Lz4Lg,
    Zstd,
    // Unsupported compression
    Lzop,
    // Miscellaneous
    Mtk,
    Dtb,
    ZImage,
}

pub const BOOT_MAGIC: &[u8; 8] = b"ANDROID!";
pub const VENDOR_BOOT_MAGIC: &[u8; 8] = b"VNDRBOOT";
pub const CHROMEOS_MAGIC: &[u8; 8] = b"CHROMEOS";
pub const GZIP1_MAGIC: &[u8; 2] = b"\x1f\x8b";
pub const GZIP2_MAGIC: &[u8; 2] = b"\x1f\x9e";
pub const LZOP_MAGIC: &[u8; 4] = b"\x89LZO";
pub const XZ_MAGIC: &[u8; 6] = b"\xfd7zXZ\x00";
pub const BZIP_MAGIC: &[u8; 3] = b"BZh";
pub const LZ4_LEG_MAGIC: &[u8; 4] = b"\x02\x21\x4c\x18";
pub const LZ41_MAGIC: &[u8; 4] = b"\x03\x21\x4c\x18";
pub const LZ42_MAGIC: &[u8; 4] = b"\x04\x22\x4d\x18";
pub const ZSTD_MAGIC: &[u8; 4] = b"\x28\xb5\x2f\xfd";
pub const MTK_MAGIC: &[u8; 4] = b"\x88\x16\x88\x58";
pub const DTB_MAGIC: &[u8; 4] = b"\xd0\x0d\xfe\xed";
pub const LG_BUMP_MAGIC: &[u8; 16] =
    b"\x41\xa9\xe4\x67\x74\x4d\x1d\x1b\xa4\x29\xf2\xec\xea\x65\x52\x79";
pub const DHTB_MAGIC: &[u8; 8] = b"\x44\x48\x54\x42\x01\x00\x00\x00";
pub const SEANDROID_MAGIC: &[u8; 16] = b"SEANDROIDENFORCE";
pub const TEGRABLOB_MAGIC: &[u8; 20] = b"-SIGNED-BY-SIGNBLOB-";
pub const NOOKHD_RL_MAGIC: &[u8] = b"Red Loader";
pub const NOOKHD_GL_MAGIC: &[u8] = b"Green Loader";
pub const NOOKHD_GR_MAGIC: &[u8] = b"Green Recovery";
pub const NOOKHD_EB_MAGIC: &[u8] = b"eMMC boot.img+secondloader";
pub const NOOKHD_ER_MAGIC: &[u8] = b"eMMC recovery.img+secondloader";
pub const NOOKHD_PRE_HEADER_SZ: usize = 1_048_576;
pub const ACCLAIM_MAGIC: &[u8] = b"BauwksBoot";
pub const ACCLAIM_PRE_HEADER_SZ: usize = 262_144;
pub const AMONET_MICROLOADER_MAGIC: &[u8] = b"microloader";
pub const AMONET_MICROLOADER_SZ: usize = 1024;
pub const AVB_FOOTER_MAGIC: &[u8; 4] = b"AVBf";
pub const AVB_MAGIC: &[u8; 4] = b"AVB0";
pub const ZIMAGE_MAGIC: &[u8; 4] = b"\x18\x28\x6f\x01";

impl Format {
    pub fn is_compressed(self) -> bool {
        matches!(
            self,
            Self::Gzip
                | Self::Zopfli
                | Self::Xz
                | Self::Lzma
                | Self::Bzip2
                | Self::Lz4
                | Self::Lz4Legacy
                | Self::Lz4Lg
                | Self::Zstd
        )
    }

    pub fn is_compressed_any(self) -> bool {
        self.is_compressed() || self == Self::Lzop
    }

    pub fn name(self) -> &'static str {
        match self {
            Self::Gzip => "gzip",
            Self::Zopfli => "zopfli",
            Self::Lzop => "lzop",
            Self::Xz => "xz",
            Self::Lzma => "lzma",
            Self::Bzip2 => "bzip2",
            Self::Lz4 => "lz4",
            Self::Lz4Legacy => "lz4_legacy",
            Self::Lz4Lg => "lz4_lg",
            Self::Zstd => "zstd",
            Self::Dtb => "dtb",
            Self::ZImage => "zimage",
            _ => "raw",
        }
    }

    pub fn ext(self) -> &'static str {
        match self {
            Self::Gzip | Self::Zopfli => ".gz",
            Self::Lzop => ".lzo",
            Self::Xz => ".xz",
            Self::Lzma => ".lzma",
            Self::Bzip2 => ".bz2",
            Self::Lz4 | Self::Lz4Legacy | Self::Lz4Lg => ".lz4",
            Self::Zstd => ".zst",
            _ => "",
        }
    }

    pub fn from_name(name: &str) -> Self {
        match name {
            "gzip" => Self::Gzip,
            "zopfli" => Self::Zopfli,
            "xz" => Self::Xz,
            "lzma" => Self::Lzma,
            "bzip2" => Self::Bzip2,
            "lz4" => Self::Lz4,
            "lz4_legacy" => Self::Lz4Legacy,
            "lz4_lg" => Self::Lz4Lg,
            "zstd" => Self::Zstd,
            _ => Self::Unknown,
        }
    }

    pub const SUPPORTED_COMPRESSION_NAMES: &'static [&'static str] = &[
        "gzip",
        "zopfli",
        "xz",
        "lzma",
        "bzip2",
        "lz4",
        "lz4_legacy",
        "lz4_lg",
        "zstd",
    ];
}

pub fn detect_format(buf: &[u8]) -> Format {
    if buf.starts_with(CHROMEOS_MAGIC) {
        Format::ChromeOs
    } else if buf.starts_with(BOOT_MAGIC) {
        Format::Aosp
    } else if buf.starts_with(VENDOR_BOOT_MAGIC) {
        Format::AospVendor
    } else if buf.starts_with(GZIP1_MAGIC) || buf.starts_with(GZIP2_MAGIC) {
        Format::Gzip
    } else if buf.starts_with(LZOP_MAGIC) {
        Format::Lzop
    } else if buf.starts_with(XZ_MAGIC) {
        Format::Xz
    } else if buf.len() >= 13
        && buf.starts_with(b"\x5d\x00\x00")
        && (buf[12] == 0xff || buf[12] == 0x00)
    {
        Format::Lzma
    } else if buf.starts_with(BZIP_MAGIC) {
        Format::Bzip2
    } else if buf.starts_with(LZ41_MAGIC) || buf.starts_with(LZ42_MAGIC) {
        Format::Lz4
    } else if buf.starts_with(LZ4_LEG_MAGIC) {
        Format::Lz4Legacy
    } else if buf.starts_with(ZSTD_MAGIC) {
        Format::Zstd
    } else if buf.starts_with(MTK_MAGIC) {
        Format::Mtk
    } else if buf.starts_with(DTB_MAGIC) {
        Format::Dtb
    } else if buf.starts_with(DHTB_MAGIC) {
        Format::Dhtb
    } else if buf.starts_with(TEGRABLOB_MAGIC) {
        Format::Blob
    } else if buf.len() >= 0x28 && &buf[0x24..0x28] == ZIMAGE_MAGIC {
        Format::ZImage
    } else {
        Format::Unknown
    }
}

pub fn detect_format_lg(buf: &[u8]) -> Format {
    let fmt = detect_format(buf);
    if fmt == Format::Lz4Legacy {
        let mut pos = 4usize;
        while pos + 4 <= buf.len() {
            let val = u32::from_le_bytes(buf[pos..pos + 4].try_into().unwrap());
            pos += 4;
            if val == 0x184c_2102 {
                continue;
            }
            if pos + (val as usize) > buf.len() {
                return Format::Lz4Lg;
            }
            pos += val as usize;
        }
    }
    fmt
}
