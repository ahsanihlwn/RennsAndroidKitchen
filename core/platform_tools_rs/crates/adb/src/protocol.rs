//! ADB Smart-Socket, Transport (`apacket`), Shell v2, and File Sync wire protocols (MIT).

use std::io::{Read, Write};
use thiserror::Error;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

/// Default ADB host server TCP port (`5037`).
pub const DEFAULT_ADB_PORT: u16 = 5037;
/// Default TCP `adbd` device port (`5555`).
pub const DEFAULT_DEVICE_PORT: u16 = 5555;
/// ADB server protocol version number (`41` = `0x0029`, for `1.0.41`).
pub const ADB_SERVER_VERSION_NUM: u32 = 41;

/// Transport protocol version `A_VERSION` (`0x01000000`).
pub const A_VERSION: u32 = 0x0100_0000;
/// Transport protocol version skipping payload checksum (`0x01000001`).
pub const A_VERSION_SKIP_CHECKSUM: u32 = 0x0100_0001;
/// Maximum transport packet payload size (`1 MiB`).
pub const MAX_PAYLOAD: u32 = 1024 * 1024;
/// Fixed byte size of an `apacket` header (`24` bytes).
pub const APACKET_HEADER_SIZE: usize = 24;

/// `A_SYNC` (`"SYNC"` = `0x434e5953`).
pub const A_SYNC: u32 = 0x434e_5953;
/// `A_CNXN` (`"CNXN"` = `0x4e584e43`).
pub const A_CNXN: u32 = 0x4e58_4e43;
/// `A_AUTH` (`"AUTH"` = `0x48545541`).
pub const A_AUTH: u32 = 0x4854_5541;
/// `A_OPEN` (`"OPEN"` = `0x4e45504f`).
pub const A_OPEN: u32 = 0x4e45_504f;
/// `A_OKAY` (`"OKAY"` = `0x59414b4f`).
pub const A_OKAY: u32 = 0x5941_4b4f;
/// `A_CLSE` (`"CLSE"` = `0x45534c43`).
pub const A_CLSE: u32 = 0x4553_4c43;
/// `A_WRTE` (`"WRTE"` = `0x45545257`).
pub const A_WRTE: u32 = 0x4554_5257;
/// `A_STLS` (`"STLS"` = `0x534c5453`).
pub const A_STLS: u32 = 0x534c_5453;

/// `A_AUTH` `arg0` types.
pub const ADB_AUTH_TOKEN: u32 = 1;
pub const ADB_AUTH_SIGNATURE: u32 = 2;
pub const ADB_AUTH_RSAPUBLICKEY: u32 = 3;

/// Host feature banner advertised in `CNXN` and `host:features` / `host:host-features`.
pub const HOST_FEATURES: &str = "shell_v2,cmd,stat_v2,ls_v2,fixed_push_mkdir,apex,abb,fixed_push_symlink_timestamp,abb_exec,remount_shell,track_app,sendrecv_v2,sendrecv_v2_brotli,sendrecv_v2_lz4,sendrecv_v2_zstd,sendrecv_v2_dry_run_send,openscreen_mdns,delayed_ack";

/// Shell v2 packet channel IDs.
pub const SHELL_V2_STDIN: u8 = 0;
pub const SHELL_V2_STDOUT: u8 = 1;
pub const SHELL_V2_STDERR: u8 = 2;
pub const SHELL_V2_EXIT: u8 = 3;
pub const SHELL_V2_CLOSE_STDIN: u8 = 4;
pub const SHELL_V2_WINCH: u8 = 5;

/// File Sync 4-byte command IDs (little-endian `u32`).
pub const SYNC_STAT: &[u8; 4] = b"STAT";
pub const SYNC_STA2: &[u8; 4] = b"STA2";
pub const SYNC_LIST: &[u8; 4] = b"LIST";
pub const SYNC_LST2: &[u8; 4] = b"LST2";
pub const SYNC_DENT: &[u8; 4] = b"DENT";
pub const SYNC_DNT2: &[u8; 4] = b"DNT2";
pub const SYNC_SEND: &[u8; 4] = b"SEND";
pub const SYNC_SND2: &[u8; 4] = b"SND2";
pub const SYNC_RECV: &[u8; 4] = b"RECV";
pub const SYNC_RCV2: &[u8; 4] = b"RCV2";
pub const SYNC_DATA: &[u8; 4] = b"DATA";
pub const SYNC_DONE: &[u8; 4] = b"DONE";
pub const SYNC_OKAY: &[u8; 4] = b"OKAY";
pub const SYNC_FAIL: &[u8; 4] = b"FAIL";
pub const SYNC_QUIT: &[u8; 4] = b"QUIT";
/// Maximum payload per `DATA` chunk in `sync:` (`64 KiB`).
pub const SYNC_DATA_MAX: usize = 64 * 1024;

/// `sendrecv_v2` (`SND2` / `RCV2`) compression and mode flags.
pub const SYNC_FLAG_NONE: u32 = 0;
pub const SYNC_FLAG_BROTLI: u32 = 1;
pub const SYNC_FLAG_LZ4: u32 = 2;
pub const SYNC_FLAG_ZSTD: u32 = 4;
pub const SYNC_FLAG_DRY_RUN: u32 = 0x8000_0000;

/// Directory entry returned by `LIST` (`DENT`) or `LST2` (`DNT2`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SyncDent {
    pub mode: u32,
    pub size: u64,
    pub mtime: i64,
    pub name: String,
}

impl SyncDent {
    pub fn is_dir(&self) -> bool {
        (self.mode & 0o170000) == 0o040000
    }

    pub fn is_regular_file(&self) -> bool {
        (self.mode & 0o170000) == 0o100000
    }
}

/// Errors returned by ADB client, server, or transport operations.
#[derive(Debug, Error)]
pub enum AdbError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("RSA crypto error: {0}")]
    Crypto(String),
    #[error("USB error: {0}")]
    Usb(String),
    #[error("adb: error: {0}")]
    RemoteFail(String),
    #[error("protocol fault: {0}")]
    Protocol(String),
    #[error("device '{0}' not found")]
    DeviceNotFound(String),
    #[error("no devices/emulators found")]
    NoDevices,
    #[error("more than one device/emulator")]
    MoreThanOneDevice,
    #[error("invalid argument: {0}")]
    InvalidArgument(String),
    #[error("operation cancelled")]
    Cancelled,
    #[error("operation timed out")]
    TimedOut,
}

/// 24-byte header + variable payload packet (`apacket`) exchanged between ADB host server and `adbd`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AdbPacket {
    pub command: u32,
    pub arg0: u32,
    pub arg1: u32,
    pub payload: Vec<u8>,
}

impl AdbPacket {
    pub fn new(command: u32, arg0: u32, arg1: u32, payload: Vec<u8>) -> Self {
        Self {
            command,
            arg0,
            arg1,
            payload,
        }
    }

    /// Computes the additive byte checksum of `self.payload`.
    pub fn payload_checksum(&self) -> u32 {
        self.payload
            .iter()
            .fold(0u32, |acc, &b| acc.wrapping_add(u32::from(b)))
    }

    /// Encodes the 24-byte `apacket` header.
    pub fn encode_header(&self) -> [u8; APACKET_HEADER_SIZE] {
        let mut hdr = [0u8; APACKET_HEADER_SIZE];
        let magic = self.command ^ 0xFFFF_FFFF;
        hdr[0..4].copy_from_slice(&self.command.to_le_bytes());
        hdr[4..8].copy_from_slice(&self.arg0.to_le_bytes());
        hdr[8..12].copy_from_slice(&self.arg1.to_le_bytes());
        hdr[12..16].copy_from_slice(&(self.payload.len() as u32).to_le_bytes());
        hdr[16..20].copy_from_slice(&self.payload_checksum().to_le_bytes());
        hdr[20..24].copy_from_slice(&magic.to_le_bytes());
        hdr
    }

    /// Serializes header + payload into a single byte vector.
    pub fn to_bytes(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(APACKET_HEADER_SIZE + self.payload.len());
        out.extend_from_slice(&self.encode_header());
        out.extend_from_slice(&self.payload);
        out
    }

    /// Reads one `AdbPacket` asynchronously from `reader`.
    pub async fn read_from<R: AsyncRead + Unpin>(reader: &mut R) -> Result<Self, AdbError> {
        let mut hdr = [0u8; APACKET_HEADER_SIZE];
        reader.read_exact(&mut hdr).await?;
        let command = u32::from_le_bytes(hdr[0..4].try_into().unwrap());
        let arg0 = u32::from_le_bytes(hdr[4..8].try_into().unwrap());
        let arg1 = u32::from_le_bytes(hdr[8..12].try_into().unwrap());
        let data_len = u32::from_le_bytes(hdr[12..16].try_into().unwrap());
        let _data_check = u32::from_le_bytes(hdr[16..20].try_into().unwrap());
        let magic = u32::from_le_bytes(hdr[20..24].try_into().unwrap());

        if magic != (command ^ 0xFFFF_FFFF) {
            return Err(AdbError::Protocol(format!(
                "bad apacket magic 0x{magic:08x} for command 0x{command:08x}"
            )));
        }
        if data_len > MAX_PAYLOAD * 4 {
            return Err(AdbError::Protocol(format!(
                "apacket payload too large ({data_len})"
            )));
        }
        let mut payload = vec![0u8; data_len as usize];
        if data_len > 0 {
            reader.read_exact(&mut payload).await?;
        }
        Ok(Self {
            command,
            arg0,
            arg1,
            payload,
        })
    }

    /// Writes this `AdbPacket` asynchronously to `writer`.
    pub async fn write_to<W: AsyncWrite + Unpin>(&self, writer: &mut W) -> Result<(), AdbError> {
        let hdr = self.encode_header();
        writer.write_all(&hdr).await?;
        if !self.payload.is_empty() {
            writer.write_all(&self.payload).await?;
        }
        writer.flush().await?;
        Ok(())
    }
}

/// Encodes a smart-socket message with a 4-character hex length prefix (`000c...`).
pub fn encode_smart_packet(msg: &str) -> Vec<u8> {
    format!("{:04x}{}", msg.len(), msg).into_bytes()
}

/// Encodes an `OKAY` response followed by a 4-hex-digit length-prefixed payload.
pub fn encode_okay_with_payload(msg: &str) -> Vec<u8> {
    format!("OKAY{:04x}{}", msg.len(), msg).into_bytes()
}

/// Encodes a `FAIL` response followed by a 4-hex-digit length-prefixed error message.
pub fn encode_fail(err_msg: &str) -> Vec<u8> {
    format!("FAIL{:04x}{}", err_msg.len(), err_msg).into_bytes()
}

/// Reads a 4-hex-digit length-prefixed request from an async stream.
pub async fn read_smart_request<R: AsyncRead + Unpin>(reader: &mut R) -> Result<String, AdbError> {
    let mut len_hex = [0u8; 4];
    reader.read_exact(&mut len_hex).await?;
    let hex_str = std::str::from_utf8(&len_hex)
        .map_err(|_| AdbError::Protocol("non-UTF8 smart packet length".into()))?;
    let len = usize::from_str_radix(hex_str, 16)
        .map_err(|_| AdbError::Protocol(format!("invalid smart packet hex length '{hex_str}'")))?;
    let mut payload = vec![0u8; len];
    if len > 0 {
        reader.read_exact(&mut payload).await?;
    }
    Ok(String::from_utf8_lossy(&payload).into_owned())
}

/// Synchronous helper: sends a 4-hex-digit prefixed smart request and reads `OKAY` or `FAIL<len><msg>`.
pub fn send_smart_command_sync<S: Read + Write>(
    stream: &mut S,
    request: &str,
) -> Result<(), AdbError> {
    let pkt = encode_smart_packet(request);
    stream.write_all(&pkt)?;
    stream.flush()?;
    read_status_sync(stream)
}

/// Synchronous helper: reads `OKAY` or `FAIL<4-hex><err>` from `stream`.
pub fn read_status_sync<R: Read>(stream: &mut R) -> Result<(), AdbError> {
    let mut status = [0u8; 4];
    stream.read_exact(&mut status)?;
    if &status == b"OKAY" {
        Ok(())
    } else if &status == b"FAIL" {
        let msg = read_length_prefixed_string_sync(stream)?;
        Err(AdbError::RemoteFail(msg))
    } else {
        Err(AdbError::Protocol(format!(
            "unexpected status '{}'",
            String::from_utf8_lossy(&status)
        )))
    }
}

/// Synchronous helper: reads a 4-hex-digit length-prefixed string from `stream`.
pub fn read_length_prefixed_string_sync<R: Read>(stream: &mut R) -> Result<String, AdbError> {
    let mut len_hex = [0u8; 4];
    stream.read_exact(&mut len_hex)?;
    let hex_str = std::str::from_utf8(&len_hex)
        .map_err(|_| AdbError::Protocol("non-UTF8 length prefix".into()))?;
    let len = usize::from_str_radix(hex_str, 16)
        .map_err(|_| AdbError::Protocol(format!("invalid hex length '{hex_str}'")))?;
    let mut buf = vec![0u8; len];
    if len > 0 {
        stream.read_exact(&mut buf)?;
    }
    Ok(String::from_utf8_lossy(&buf).into_owned())
}

/// Encodes a 5-byte header + payload Shell v2 frame (`[id: u8, len: u32 LE, data...]`).
pub fn encode_shell_v2_packet(id: u8, data: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(5 + data.len());
    out.push(id);
    out.extend_from_slice(&(data.len() as u32).to_le_bytes());
    out.extend_from_slice(data);
    out
}

/// Synchronous helper: reads a single Shell v2 frame (`(id, payload)`), returning `Ok(None)` on EOF.
pub fn read_shell_v2_packet_sync<R: Read>(
    stream: &mut R,
) -> Result<Option<(u8, Vec<u8>)>, AdbError> {
    let mut hdr = [0u8; 5];
    let mut read = 0usize;
    while read < 5 {
        match stream.read(&mut hdr[read..]) {
            Ok(0) if read == 0 => return Ok(None),
            Ok(0) => {
                return Err(AdbError::Protocol(
                    "unexpected EOF inside shell_v2 frame header".into(),
                ));
            }
            Ok(n) => read += n,
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(AdbError::Io(e)),
        }
    }
    let id = hdr[0];
    let len = u32::from_le_bytes(hdr[1..5].try_into().unwrap()) as usize;
    let mut payload = vec![0u8; len];
    if len > 0 {
        stream.read_exact(&mut payload)?;
    }
    Ok(Some((id, payload)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_apacket_encode_and_checksum() {
        let pkt = AdbPacket::new(A_CNXN, A_VERSION, MAX_PAYLOAD, b"host::".to_vec());
        let hdr = pkt.encode_header();
        assert_eq!(u32::from_le_bytes(hdr[0..4].try_into().unwrap()), A_CNXN);
        assert_eq!(
            u32::from_le_bytes(hdr[20..24].try_into().unwrap()),
            A_CNXN ^ 0xFFFF_FFFF
        );
        assert_eq!(
            pkt.payload_checksum(),
            b"host::".iter().map(|&b| u32::from(b)).sum::<u32>()
        );
    }

    #[test]
    fn test_smart_packet_encoding() {
        assert_eq!(encode_smart_packet("host:version"), b"000chost:version");
        assert_eq!(encode_okay_with_payload("0029"), b"OKAY00040029");
        assert_eq!(encode_fail("closed"), b"FAIL0006closed");
    }
}
