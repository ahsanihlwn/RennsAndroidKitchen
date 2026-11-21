use std::fs;
use std::io::{self, Read, Write};
use std::num::NonZeroU64;

use rayon::prelude::*;

use crate::format::{Format, detect_format};

const LZ4_BLOCK_MAX: usize = 8 * 1024 * 1024;
const LZ4_LEGACY_MAGIC_U32: u32 = 0x184c_2102;

pub fn compress_bytes(fmt: Format, data: &[u8]) -> Result<Vec<u8>, String> {
    match fmt {
        Format::Gzip => {
            let mut enc = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::best());
            enc.write_all(data).map_err(|e| format!("gzip encode error: {e}"))?;
            enc.finish().map_err(|e| format!("gzip finish error: {e}"))
        }
        Format::Zopfli => {
            let mut out = Vec::new();
            let opts = zopfli::Options {
                iteration_count: NonZeroU64::new(1).unwrap(),
                maximum_block_splits: 0,
                ..Default::default()
            };
            zopfli::compress(opts, zopfli::Format::Gzip, data, &mut out)
                .map_err(|e| format!("zopfli encode error: {e}"))?;
            Ok(out)
        }
        Format::Bzip2 => {
            let mut enc = bzip2::write::BzEncoder::new(Vec::new(), bzip2::Compression::best());
            enc.write_all(data).map_err(|e| format!("bzip2 encode error: {e}"))?;
            enc.finish().map_err(|e| format!("bzip2 finish error: {e}"))
        }
        Format::Xz => {
            let opts = lzma_rust2::XzOptions::with_preset(9);
            let mut enc = lzma_rust2::XzWriter::new(Vec::new(), opts)
                .map_err(|e| format!("xz init error: {e}"))?;
            enc.write_all(data).map_err(|e| format!("xz encode error: {e}"))?;
            enc.finish().map_err(|e| format!("xz finish error: {e}"))
        }
        Format::Lzma => {
            let opts = lzma_rust2::LzmaOptions::with_preset(9);
            let mut enc = lzma_rust2::LzmaWriter::new_use_header(
                Vec::new(),
                &opts,
                Some(data.len() as u64),
            )
            .map_err(|e| format!("lzma init error: {e}"))?;
            enc.write_all(data).map_err(|e| format!("lzma encode error: {e}"))?;
            enc.finish().map_err(|e| format!("lzma finish error: {e}"))
        }
        Format::Lz4 => {
            let mut enc = lz4::EncoderBuilder::new()
                .level(9)
                .block_size(lz4::BlockSize::Max4MB)
                .block_mode(lz4::BlockMode::Independent)
                .checksum(lz4::ContentChecksum::ChecksumEnabled)
                .auto_flush(true)
                .build(Vec::new())
                .map_err(|e| format!("lz4 frame init error: {e}"))?;
            enc.write_all(data).map_err(|e| format!("lz4 frame encode error: {e}"))?;
            let (out, res) = enc.finish();
            res.map_err(|e| format!("lz4 frame finish error: {e}"))?;
            Ok(out)
        }
        Format::Lz4Legacy | Format::Lz4Lg => compress_lz4_legacy(data, fmt == Format::Lz4Lg),
        Format::Zstd => zstd::stream::encode_all(data, 6)
            .map_err(|e| format!("zstd encode error: {e}")),
        _ => Err(format!("Unsupported compression format: {}", fmt.name())),
    }
}

pub fn decompress_bytes(fmt: Format, data: &[u8]) -> Result<Vec<u8>, String> {
    match fmt {
        Format::Gzip | Format::Zopfli => decompress_gzip(data),
        Format::Bzip2 => {
            let mut dec = bzip2::read::MultiBzDecoder::new(data);
            let mut out = Vec::new();
            dec.read_to_end(&mut out)
                .map_err(|e| format!("bzip2 decode error: {e}"))?;
            Ok(out)
        }
        Format::Xz => {
            let mut dec = lzma_rust2::XzReader::new(data, true);
            let mut out = Vec::new();
            dec.read_to_end(&mut out)
                .map_err(|e| format!("xz decode error: {e}"))?;
            Ok(out)
        }
        Format::Lzma => {
            let mut dec = lzma_rust2::LzmaReader::new_mem_limit(data, u32::MAX, None)
                .map_err(|e| format!("lzma init error: {e}"))?;
            let mut out = Vec::new();
            dec.read_to_end(&mut out)
                .map_err(|e| format!("lzma decode error: {e}"))?;
            Ok(out)
        }
        Format::Lz4 => {
            let mut dec = lz4::Decoder::new(data)
                .map_err(|e| format!("lz4 frame init error: {e}"))?;
            let mut out = Vec::new();
            dec.read_to_end(&mut out)
                .map_err(|e| format!("lz4 frame decode error: {e}"))?;
            Ok(out)
        }
        Format::Lz4Legacy | Format::Lz4Lg => decompress_lz4_legacy(data),
        Format::Zstd => zstd::stream::decode_all(data)
            .map_err(|e| format!("zstd decode error: {e}")),
        _ => Err(format!("Unsupported decompression format: {}", fmt.name())),
    }
}

fn decompress_gzip(data: &[u8]) -> Result<Vec<u8>, String> {
    let mut dec = flate2::read::MultiGzDecoder::new(data);
    let mut out = Vec::new();
    match dec.read_to_end(&mut out) {
        Ok(_) => Ok(out),
        Err(e) if !out.is_empty() => {
            // Trailing non-gzip bytes after valid gzip stream (matching gz_decoder WAIT/COPY)
            let _ = e;
            Ok(out)
        }
        Err(e) => Err(format!("gzip decode error: {e}")),
    }
}

pub fn compress_lz4_legacy(data: &[u8], append_total_len: bool) -> Result<Vec<u8>, String> {
    let chunks: Vec<&[u8]> = if data.is_empty() {
        Vec::new()
    } else {
        data.chunks(LZ4_BLOCK_MAX).collect()
    };

    let compressed_chunks: Result<Vec<Vec<u8>>, String> = chunks
        .into_par_iter()
        .map(|chunk| {
            lz4::block::compress(
                chunk,
                Some(lz4::block::CompressionMode::HIGHCOMPRESSION(12)),
                false,
            )
            .map_err(|e| format!("lz4hc compression failure: {e}"))
        })
        .collect();

    let compressed_chunks = compressed_chunks?;
    let total_cap = 4
        + compressed_chunks.iter().map(|c| 4 + c.len()).sum::<usize>()
        + if append_total_len { 4 } else { 0 };
    let mut out = Vec::with_capacity(total_cap);
    out.extend_from_slice(&LZ4_LEGACY_MAGIC_U32.to_le_bytes());
    for c in compressed_chunks {
        out.extend_from_slice(&(c.len() as u32).to_le_bytes());
        out.extend_from_slice(&c);
    }
    if append_total_len {
        out.extend_from_slice(&(data.len() as u32).to_le_bytes());
    }
    Ok(out)
}

pub fn decompress_lz4_legacy(data: &[u8]) -> Result<Vec<u8>, String> {
    if data.len() < 4 {
        return Err("lz4_legacy data too short".into());
    }
    let mut spans: Vec<&[u8]> = Vec::new();
    let mut pos = 0usize;
    while pos + 4 <= data.len() {
        let val = u32::from_le_bytes(data[pos..pos + 4].try_into().unwrap());
        pos += 4;
        if val == LZ4_LEGACY_MAGIC_U32 {
            continue;
        }
        let block_sz = val as usize;
        if block_sz == 0 || pos + block_sz > data.len() {
            // In lz4_lg, the final 4 bytes store uncompressed total length
            if pos == data.len() {
                break;
            }
            return Err(format!(
                "lz4_legacy truncated block at offset {}: expected {}, remaining {}",
                pos - 4,
                block_sz,
                data.len() - pos
            ));
        }
        spans.push(&data[pos..pos + block_sz]);
        pos += block_sz;
    }

    let decoded_blocks: Result<Vec<Vec<u8>>, String> = spans
        .into_par_iter()
        .map(|block| {
            lz4::block::decompress(block, Some(LZ4_BLOCK_MAX as i32))
                .map_err(|e| format!("lz4_legacy decompression failure: {e}"))
        })
        .collect();

    let decoded_blocks = decoded_blocks?;
    let total: usize = decoded_blocks.iter().map(|b| b.len()).sum();
    let mut out = Vec::with_capacity(total);
    for b in decoded_blocks {
        out.extend_from_slice(&b);
    }
    Ok(out)
}

pub fn cmd_compress(method: &str, infile: &str, outfile: Option<&str>) -> Result<(), String> {
    let fmt = Format::from_name(method);
    if fmt == Format::Unknown {
        return Err(format!("Unknown compression method: [{method}]"));
    }

    let in_std = infile == "-";
    let input = if in_std {
        let mut buf = Vec::new();
        io::stdin()
            .read_to_end(&mut buf)
            .map_err(|e| format!("Failed reading stdin: {e}"))?;
        buf
    } else {
        fs::read(infile).map_err(|e| format!("Failed reading [{infile}]: {e}"))?
    };

    let compressed = compress_bytes(fmt, &input)?;

    match outfile {
        None => {
            if in_std {
                io::stdout()
                    .write_all(&compressed)
                    .map_err(|e| format!("Failed writing stdout: {e}"))?;
            } else {
                let target = format!("{infile}{}", fmt.ext());
                eprintln!("Compressing to [{target}]");
                fs::write(&target, &compressed)
                    .map_err(|e| format!("Failed writing [{target}]: {e}"))?;
                let _ = fs::remove_file(infile);
            }
        }
        Some("-") => {
            io::stdout()
                .write_all(&compressed)
                .map_err(|e| format!("Failed writing stdout: {e}"))?;
        }
        Some(path) => {
            fs::write(path, &compressed)
                .map_err(|e| format!("Failed writing [{path}]: {e}"))?;
        }
    }
    Ok(())
}

pub fn cmd_decompress(infile: &str, outfile: Option<&str>) -> Result<(), String> {
    let in_std = infile == "-";
    let input = if in_std {
        let mut buf = Vec::new();
        io::stdin()
            .read_to_end(&mut buf)
            .map_err(|e| format!("Failed reading stdin: {e}"))?;
        buf
    } else {
        fs::read(infile).map_err(|e| format!("Failed reading [{infile}]: {e}"))?
    };

    let fmt = detect_format(&input);
    eprintln!("Detected format: [{}]", fmt.name());
    if !fmt.is_compressed() {
        return Err("Input file is not a supported compressed type!".into());
    }

    let mut rm_in = false;
    let out_target: Option<String> = match outfile {
        None => {
            if in_std {
                None
            } else {
                let ext = fmt.ext();
                if ext.is_empty() || !infile.ends_with(ext) {
                    return Err("Input file is not a supported type!".into());
                }
                let stripped = &infile[..infile.len() - ext.len()];
                eprintln!("Decompressing to [{stripped}]");
                rm_in = true;
                Some(stripped.to_string())
            }
        }
        Some("-") => None,
        Some(p) => Some(p.to_string()),
    };

    let decompressed = decompress_bytes(fmt, &input)?;

    match out_target {
        None => {
            io::stdout()
                .write_all(&decompressed)
                .map_err(|e| format!("Failed writing stdout: {e}"))?;
        }
        Some(ref path) => {
            fs::write(path, &decompressed)
                .map_err(|e| format!("Failed writing [{path}]: {e}"))?;
        }
    }

    if rm_in {
        let _ = fs::remove_file(infile);
    }
    Ok(())
}
