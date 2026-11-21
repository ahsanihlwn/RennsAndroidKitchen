//! Clean-room MIT Android Debug Bridge (`adb`) CLI client and host server daemon (`1.0.41`).

pub mod auth;
pub mod client;
pub mod config;
pub mod protocol;
pub mod server;
pub mod usb;

pub use client::{run_cli, run_cli_captured, AdbEvent, AdbEventCallback, AdbRunResult};
pub use config::{
    help_body, help_output, is_embedded_mode, parse_cli_args, set_embedded_mode, version_output,
    AdbCliConfig, AdbCommand, DeviceTarget,
};
pub use protocol::{AdbError, AdbPacket};
pub use server::{
    ensure_in_process_server, ensure_in_process_server_async, shutdown_in_process_servers,
    AdbHostServer,
};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cli_parsing() {
        let args: Vec<String> = vec![
            "-H",
            "localhost",
            "-P",
            "5039",
            "-s",
            "127.0.0.1:5555",
            "shell",
            "getprop",
            "ro.build.version.release",
        ]
        .into_iter()
        .map(String::from)
        .collect();
        let cfg = parse_cli_args(&args).unwrap();
        assert_eq!(cfg.server_host, "127.0.0.1");
        assert_eq!(cfg.server_port, 5039);
        assert_eq!(cfg.target.serial.as_deref(), Some("127.0.0.1:5555"));
        assert!(matches!(cfg.command, AdbCommand::Shell { .. }));
    }

    #[test]
    fn test_sendrecv_v2_compression_codecs() {
        use std::io::{Read, Write};
        let sample = b"Android Debug Bridge sendrecv_v2 compression payload test! ".repeat(256);

        // 1. Brotli via ahsanihlwn/brotli-rs
        let br_compressed = brotli_rs::io::compress(
            &sample,
            1,
            22,
            brotli_rs::enc::params::BrotliEncoderMode::Generic,
        )
        .unwrap();
        let br_decompressed = brotli_rs::io::decompress(&br_compressed).unwrap();
        assert_eq!(br_decompressed, sample);

        // 2. LZ4 Frame
        let mut lz4_enc = lz4_flex::frame::FrameEncoder::new(Vec::new());
        lz4_enc.write_all(&sample).unwrap();
        let lz4_compressed = lz4_enc.finish().unwrap();
        let mut lz4_dec = lz4_flex::frame::FrameDecoder::new(lz4_compressed.as_slice());
        let mut lz4_decompressed = Vec::new();
        lz4_dec.read_to_end(&mut lz4_decompressed).unwrap();
        assert_eq!(lz4_decompressed, sample);

        // 3. Zstd
        let zstd_compressed = zstd::stream::encode_all(sample.as_slice(), 1).unwrap();
        let zstd_decompressed = zstd::stream::decode_all(zstd_compressed.as_slice()).unwrap();
        assert_eq!(zstd_decompressed, sample);
    }
}
