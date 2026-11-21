//! Fastboot wire protocol messages, response parser, and error types (MIT).

use std::sync::Arc;
use thiserror::Error;

/// Maximum fastboot command length (4096 bytes in fastbootd, 64 bytes in legacy bootloaders).
pub const MAX_COMMAND_SIZE: usize = 4096;
/// Default download buffer block chunk size (1 MiB).
pub const DOWNLOAD_CHUNK_SIZE: usize = 1024 * 1024;
/// TCP handshake magic (`"FB01"`).
pub const TCP_HANDSHAKE: &[u8; 4] = b"FB01";
/// Default TCP/UDP fastboot port (`5554`).
pub const DEFAULT_NET_PORT: u16 = 5554;

/// Errors occurring during fastboot protocol communication or command execution.
#[derive(Debug, Error)]
pub enum FastbootError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("USB error: {0}")]
    Usb(String),
    #[error("Sparse image error: {0}")]
    Sparse(#[from] android_sparse::SparseError),
    #[error("Boot image error: {0}")]
    BootImg(#[from] android_bootimg::BootImgError),
    #[error("AVB vbmeta error: {0}")]
    Avb(#[from] android_avb::AvbError),
    #[error("Dynamic partition (liblp) error: {0}")]
    Lp(#[from] android_lp::LpError),
    #[error("ZIP archive error: {0}")]
    Zip(#[from] zip::result::ZipError),
    #[error("FAILED (remote: '{0}')")]
    RemoteFail(String),
    #[error("FAILED (protocol: '{0}')")]
    Protocol(String),
    #[error("no fastboot devices found")]
    NoDevice,
    #[error("invalid argument: {0}")]
    InvalidArgument(String),
    #[error("operation cancelled")]
    Cancelled,
    #[error("operation timed out")]
    TimedOut,
}

/// Streaming event emitted during an embedded/captured `fastboot` execution.
#[derive(Debug, Clone)]
pub enum FastbootEvent {
    Output {
        text: String,
        is_stderr: bool,
    },
    Progress {
        partition: String,
        stage: String,
        chunk_index: usize,
        chunk_total: usize,
        bytes_sent: u64,
        bytes_total: u64,
        percent: i32,
        speed_bps: u64,
        detail: String,
    },
}

/// Captured execution result returned by `run_cli_captured`.
#[derive(Debug, Clone)]
pub struct FastbootRunResult {
    pub exit_code: i32,
    pub stdout: String,
    pub stderr: String,
    pub output: String,
}

pub type FastbootEventCallback = Arc<dyn Fn(FastbootEvent) + Send + Sync>;

/// Parsed response packet from a fastboot bootloader or `fastbootd` daemon.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FastbootResponse {
    Okay(String),
    Fail(String),
    Info(String),
    Text(String),
    Data(u32),
}

impl FastbootResponse {
    /// Parses a fastboot response packet (`OKAY`, `FAIL`, `INFO`, `TEXT`, `DATA`).
    pub fn from_bytes(bytes: &[u8]) -> Result<Self, FastbootError> {
        if bytes.len() < 4 {
            return Err(FastbootError::Protocol(format!(
                "short response packet ({} bytes)",
                bytes.len()
            )));
        }
        let prefix = std::str::from_utf8(&bytes[0..4])
            .map_err(|_| FastbootError::Protocol("non-UTF8 response prefix".into()))?;
        let payload_raw = &bytes[4..];
        let payload_end = payload_raw
            .iter()
            .position(|&b| b == 0)
            .unwrap_or(payload_raw.len());
        let payload = String::from_utf8_lossy(&payload_raw[..payload_end]).into_owned();

        match prefix {
            "OKAY" => Ok(Self::Okay(payload)),
            "FAIL" => Ok(Self::Fail(payload)),
            "INFO" => Ok(Self::Info(payload)),
            "TEXT" => Ok(Self::Text(payload)),
            "DATA" => {
                let hex = payload.trim();
                let len = u32::from_str_radix(hex, 16).map_err(|_| {
                    FastbootError::Protocol(format!("invalid DATA length '{payload}'"))
                })?;
                Ok(Self::Data(len))
            }
            other => Err(FastbootError::Protocol(format!(
                "unknown response status '{other}'"
            ))),
        }
    }
}

/// Parses a hex (`0x...`) or decimal integer string into `u64`.
pub fn parse_u64_auto(s: &str) -> Result<u64, FastbootError> {
    let trimmed = s.trim();
    if let Some(hex) = trimmed
        .strip_prefix("0x")
        .or_else(|| trimmed.strip_prefix("0X"))
    {
        u64::from_str_radix(hex, 16)
            .map_err(|_| FastbootError::InvalidArgument(format!("invalid hex number '{s}'")))
    } else {
        trimmed
            .parse::<u64>()
            .or_else(|_| u64::from_str_radix(trimmed, 16))
            .map_err(|_| FastbootError::InvalidArgument(format!("invalid number '{s}'")))
    }
}

/// Parses a size string with optional `K`/`M`/`G` suffix (e.g., `256M`, `512K`, `1G`, or bytes).
pub fn parse_size_with_suffix(s: &str) -> Result<u64, FastbootError> {
    let trimmed = s.trim();
    if trimmed.is_empty() {
        return Err(FastbootError::InvalidArgument("empty size".into()));
    }
    let last = trimmed.chars().last().unwrap();
    if last.is_ascii_alphabetic() {
        let num = parse_u64_auto(&trimmed[..trimmed.len() - 1])?;
        let mult = match last {
            'k' | 'K' => 1024u64,
            'm' | 'M' => 1024u64 * 1024,
            'g' | 'G' => 1024u64 * 1024 * 1024,
            _ => {
                return Err(FastbootError::InvalidArgument(format!(
                    "unknown size suffix in '{s}'"
                )));
            }
        };
        Ok(num * mult)
    } else {
        parse_u64_auto(trimmed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_response_parsing() {
        assert_eq!(
            FastbootResponse::from_bytes(b"OKAY0.4\0").unwrap(),
            FastbootResponse::Okay("0.4".into())
        );
        assert_eq!(
            FastbootResponse::from_bytes(b"DATA00100000").unwrap(),
            FastbootResponse::Data(0x0010_0000)
        );
        assert_eq!(
            FastbootResponse::from_bytes(b"FAILlocked").unwrap(),
            FastbootResponse::Fail("locked".into())
        );
        assert_eq!(parse_size_with_suffix("256M").unwrap(), 256 * 1024 * 1024);
    }
}
