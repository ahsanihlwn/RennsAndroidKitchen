// SPDX-License-Identifier: MIT

//! Compression and decompression engines for EROFS pclusters:
//! LZ4 / LZ4HC (with 0-padding & partial decode), DEFLATE, MicroLZMA, ZSTD, Shifted, and Interlaced.

use crate::error::{ErofsError, Result};
use crate::format::{COMPR_ALG_DEFLATE, COMPR_ALG_LZ4, COMPR_ALG_LZMA, COMPR_ALG_ZSTD};
use flate2::Compression;
use flate2::read::DeflateDecoder;
use flate2::write::DeflateEncoder;
use std::io::{Read, Write};

/// Compression algorithm selector for packing and decoding.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CompressionAlg {
    None,
    Lz4,
    Lz4Hc,
    Lzma,
    Deflate,
    Zstd,
}

impl CompressionAlg {
    pub fn parse(s: &str) -> Result<Self> {
        match s.to_ascii_lowercase().as_str() {
            "none" | "raw" | "uncompressed" => Ok(Self::None),
            "lz4" => Ok(Self::Lz4),
            "lz4hc" => Ok(Self::Lz4Hc),
            "lzma" => Ok(Self::Lzma),
            "deflate" | "zlib" => Ok(Self::Deflate),
            "zstd" => Ok(Self::Zstd),
            other => Err(ErofsError::InvalidArg(format!(
                "unsupported compression algorithm '{other}' (expected none|lz4|lz4hc|deflate|zstd|lzma)"
            ))),
        }
    }

    pub fn on_disk_id(&self) -> Option<u8> {
        match self {
            Self::None => None,
            Self::Lz4 | Self::Lz4Hc => Some(COMPR_ALG_LZ4),
            Self::Lzma => Some(COMPR_ALG_LZMA),
            Self::Deflate => Some(COMPR_ALG_DEFLATE),
            Self::Zstd => Some(COMPR_ALG_ZSTD),
        }
    }

    pub fn name(&self) -> &'static str {
        match self {
            Self::None => "none",
            Self::Lz4 => "lz4",
            Self::Lz4Hc => "lz4hc",
            Self::Lzma => "lzma",
            Self::Deflate => "deflate",
            Self::Zstd => "zstd",
        }
    }
}

/// Decompression mode for a single pcluster.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecodeAlg {
    Shifted,
    Interlaced { offset: usize },
    Lz4,
    Lzma,
    Deflate,
    Zstd,
}

impl DecodeAlg {
    pub fn from_id(id: u8) -> Result<Self> {
        match id {
            COMPR_ALG_LZ4 => Ok(Self::Lz4),
            COMPR_ALG_LZMA => Ok(Self::Lzma),
            COMPR_ALG_DEFLATE => Ok(Self::Deflate),
            COMPR_ALG_ZSTD => Ok(Self::Zstd),
            other => Err(ErofsError::Unsupported(format!(
                "unknown EROFS compression algorithm id {other}"
            ))),
        }
    }
}

#[derive(Debug)]
pub struct DecompressRequest<'a> {
    pub alg: DecodeAlg,
    pub input: &'a [u8],
    pub decoded_len: usize,
    pub decoded_skip: usize,
    pub partial: bool,
    pub block_size: usize,
}

/// Skip EROFS leading zero padding (used by `LZ4_0PADDING` and aligned compressed streams).
fn skip_leading_zeros(data: &[u8]) -> &[u8] {
    let pos = data.iter().position(|&b| b != 0).unwrap_or(data.len());
    &data[pos..]
}

/// Decompress one EROFS physical cluster according to `req`.
pub fn decompress_pcluster(req: &DecompressRequest<'_>) -> Result<Vec<u8>> {
    if req.decoded_skip > req.decoded_len {
        return Err(ErofsError::Corrupted(
            "decoded_skip exceeds decoded_len".into(),
        ));
    }
    let out_len = req.decoded_len - req.decoded_skip;
    if out_len == 0 {
        return Ok(Vec::new());
    }

    match req.alg {
        DecodeAlg::Shifted => {
            if req.decoded_len > req.input.len() {
                return Err(ErofsError::Corrupted(format!(
                    "shifted pcluster too small: input {} < decoded {}",
                    req.input.len(),
                    req.decoded_len
                )));
            }
            Ok(req.input[req.decoded_skip..req.decoded_len].to_vec())
        }
        DecodeAlg::Interlaced { offset } => {
            if req.decoded_len > req.input.len() || req.input.len() > req.block_size {
                return Err(ErofsError::Corrupted(
                    "invalid interlaced pcluster dimensions".into(),
                ));
            }
            let bs = req.block_size;
            let skip = (offset + req.decoded_skip) % bs;
            let right = (bs - skip).min(out_len);
            let mut out = vec![0u8; out_len];
            out[..right].copy_from_slice(&req.input[skip..skip + right]);
            if out_len > right {
                out[right..].copy_from_slice(&req.input[..out_len - right]);
            }
            Ok(out)
        }
        DecodeAlg::Lz4 => {
            let src = skip_leading_zeros(req.input);
            if src.is_empty() {
                return Err(ErofsError::Corrupted("empty LZ4 pcluster after 0-padding".into()));
            }
            if !req.partial {
                let mut out = vec![0u8; req.decoded_len];
                if let Ok(n) = lz4_flex::block::decompress_into(src, &mut out) {
                    if n == req.decoded_len {
                        if req.decoded_skip == 0 {
                            return Ok(out);
                        }
                        return Ok(out[req.decoded_skip..req.decoded_len].to_vec());
                    }
                }
            }
            let decoded = lz4_decompress_block(src, req.decoded_len, req.partial)?;
            Ok(decoded[req.decoded_skip..req.decoded_len].to_vec())
        }
        DecodeAlg::Deflate => {
            let src = skip_leading_zeros(req.input);
            if src.is_empty() {
                return Err(ErofsError::Corrupted(
                    "empty DEFLATE pcluster after 0-padding".into(),
                ));
            }
            let mut dec = DeflateDecoder::new(src);
            let mut buf = vec![0u8; req.decoded_len];
            let mut filled = 0usize;
            while filled < req.decoded_len {
                match dec.read(&mut buf[filled..]) {
                    Ok(0) => break,
                    Ok(n) => filled += n,
                    Err(e) => {
                        return Err(ErofsError::Compression(format!("DEFLATE decode error: {e}")));
                    }
                }
            }
            if filled != req.decoded_len {
                return Err(ErofsError::Compression(format!(
                    "DEFLATE length mismatch: got {filled}, expected {}",
                    req.decoded_len
                )));
            }
            Ok(buf[req.decoded_skip..req.decoded_len].to_vec())
        }
        DecodeAlg::Zstd => {
            let src = skip_leading_zeros(req.input);
            if src.is_empty() {
                return Err(ErofsError::Corrupted("empty ZSTD pcluster".into()));
            }
            let mut dec = zstd::stream::read::Decoder::new(src)
                .map_err(|e| ErofsError::Compression(format!("ZSTD init error: {e}")))?;
            let mut buf = vec![0u8; req.decoded_len];
            let mut filled = 0usize;
            while filled < req.decoded_len {
                match dec.read(&mut buf[filled..]) {
                    Ok(0) => break,
                    Ok(n) => filled += n,
                    Err(e) => {
                        return Err(ErofsError::Compression(format!("ZSTD decode error: {e}")));
                    }
                }
            }
            if filled != req.decoded_len {
                return Err(ErofsError::Compression(format!(
                    "ZSTD length mismatch: got {filled}, expected {}",
                    req.decoded_len
                )));
            }
            Ok(buf[req.decoded_skip..req.decoded_len].to_vec())
        }
        DecodeAlg::Lzma => {
            let src = skip_leading_zeros(req.input);
            if src.len() < 5 {
                return Err(ErofsError::Corrupted("MicroLZMA pcluster too short".into()));
            }
            let decoded = decompress_microlzma(src, req.decoded_len)?;
            Ok(decoded[req.decoded_skip..req.decoded_len].to_vec())
        }
    }
}

/// Pure-Rust LZ4 block decompressor supporting both full and partial decoding.
/// Matches LZ4 block format specification and Linux kernel EROFS `LZ4_decompress_safe(_partial)`.
pub fn lz4_decompress_block(src: &[u8], target_len: usize, partial: bool) -> Result<Vec<u8>> {
    let mut dst = Vec::with_capacity(target_len);
    let mut ip = 0usize;
    let src_len = src.len();

    while ip < src_len {
        let token = src[ip];
        ip += 1;

        // 1. Literal length
        let mut lit_len = (token >> 4) as usize;
        if lit_len == 15 {
            loop {
                if ip >= src_len {
                    return Err(ErofsError::Compression(
                        "LZ4 truncated literal length".into(),
                    ));
                }
                let b = src[ip] as usize;
                ip += 1;
                lit_len += b;
                if b != 255 {
                    break;
                }
            }
        }

        if ip + lit_len > src_len {
            return Err(ErofsError::Compression("LZ4 literal out of bounds".into()));
        }

        if partial && dst.len() + lit_len >= target_len {
            let need = target_len - dst.len();
            dst.extend_from_slice(&src[ip..ip + need]);
            return Ok(dst);
        }

        if dst.len() + lit_len > target_len {
            return Err(ErofsError::Compression(format!(
                "LZ4 output overflow on literal: {} + {} > {target_len}",
                dst.len(),
                lit_len
            )));
        }

        dst.extend_from_slice(&src[ip..ip + lit_len]);
        ip += lit_len;

        // End of block check (last sequence has literals only, no match)
        if ip >= src_len {
            break;
        }
        if partial && dst.len() >= target_len {
            return Ok(dst);
        }

        // 2. Match offset (little-endian u16)
        if ip + 2 > src_len {
            return Err(ErofsError::Compression("LZ4 truncated match offset".into()));
        }
        let offset = u16::from_le_bytes([src[ip], src[ip + 1]]) as usize;
        ip += 2;
        if offset == 0 || offset > dst.len() {
            return Err(ErofsError::Compression(format!(
                "LZ4 invalid match offset {offset} at out_pos {}",
                dst.len()
            )));
        }

        // 3. Match length
        let mut match_len = ((token & 0x0F) as usize) + 4;
        if (token & 0x0F) == 15 {
            loop {
                if ip >= src_len {
                    return Err(ErofsError::Compression("LZ4 truncated match length".into()));
                }
                let b = src[ip] as usize;
                ip += 1;
                match_len += b;
                if b != 255 {
                    break;
                }
            }
        }

        let copy_len = if partial {
            match_len.min(target_len - dst.len())
        } else {
            if dst.len() + match_len > target_len {
                return Err(ErofsError::Compression(format!(
                    "LZ4 output overflow on match: {} + {} > {target_len}",
                    dst.len(),
                    match_len
                )));
            }
            match_len
        };

        // Byte-by-byte copy handles overlapping RLE matches (offset < copy_len) cleanly.
        let start = dst.len() - offset;
        for i in 0..copy_len {
            let b = dst[start + i];
            dst.push(b);
        }

        if partial && dst.len() >= target_len {
            return Ok(dst);
        }
    }

    if dst.len() != target_len {
        return Err(ErofsError::Compression(format!(
            "LZ4 decoded length mismatch: got {}, expected {target_len}",
            dst.len()
        )));
    }
    Ok(dst)
}

/// Decompress an EROFS MicroLZMA stream.
/// MicroLZMA stores `~props` in byte 0 (so it is never 0x00), followed immediately by the
/// 4 initial range-coder bytes (standard LZMA expects a leading 0x00 dummy byte before those 4).
fn decompress_microlzma(src: &[u8], decoded_len: usize) -> Result<Vec<u8>> {
    let props = !src[0];
    if props >= 225 {
        return Err(ErofsError::Compression(format!(
            "invalid MicroLZMA props byte 0x{props:02x}"
        )));
    }
    // Reconstruct standard raw LZMA stream: prepend 0x00 range-coder byte before src[1..].
    let mut raw_stream = Vec::with_capacity(src.len());
    raw_stream.push(0u8);
    raw_stream.extend_from_slice(&src[1..]);

    let dict_size = (8 * 1024 * 1024u32).min((decoded_len as u32).next_power_of_two().max(4096));
    let mut reader = lzma_rust2::LzmaReader::new_with_props(
        &raw_stream[..],
        decoded_len as u64,
        props,
        dict_size,
        None,
    )
    .map_err(|e| ErofsError::Compression(format!("MicroLZMA init failed: {e}")))?;

    let mut out = vec![0u8; decoded_len];
    reader
        .read_exact(&mut out)
        .map_err(|e| ErofsError::Compression(format!("MicroLZMA decode failed: {e}")))?;
    Ok(out)
}

/// Compress a chunk of input bytes into an EROFS pcluster of size `pcluster_size`.
///
/// Uses fixed-size-output compression semantics: packs as many input bytes as possible
/// into at most `pcluster_size` bytes. Returns `Some((consumed_input_bytes, padded_pcluster))`
/// when compression saves at least one block (or fits within `pcluster_size` with `consumed > pcluster_size`),
/// or `None` if this slice should be stored uncompressed.
pub fn compress_fixed_output(
    alg: CompressionAlg,
    level: Option<i32>,
    input: &[u8],
    pcluster_size: usize,
    block_size: usize,
    zero_pad: bool,
) -> Result<Option<(usize, Vec<u8>)>> {
    if alg == CompressionAlg::None || input.is_empty() {
        return Ok(None);
    }

    match alg {
        CompressionAlg::None => Ok(None),
        CompressionAlg::Lz4 | CompressionAlg::Lz4Hc => {
            let hc = alg == CompressionAlg::Lz4Hc;
            let lvl = level.unwrap_or(if hc { 9 } else { 0 }).clamp(0, 12) as u32;
            let (consumed, compressed) = lz4_compress_dest_size(input, pcluster_size, hc, lvl);
            if consumed == 0 || compressed.is_empty() {
                return Ok(None);
            }
            // Only worth compressing if compressed pcluster uses fewer blocks than raw consumed bytes
            // (or consumes strictly more bytes than the pcluster size).
            let raw_blocks = (consumed + block_size - 1) / block_size;
            let comp_blocks = (compressed.len() + block_size - 1) / block_size;
            if comp_blocks >= raw_blocks && consumed <= pcluster_size {
                return Ok(None);
            }
            let target_bytes = comp_blocks * block_size;
            let mut out = vec![0u8; target_bytes];
            if zero_pad {
                let start = target_bytes - compressed.len();
                out[start..].copy_from_slice(&compressed);
            } else {
                out[..compressed.len()].copy_from_slice(&compressed);
            }
            Ok(Some((consumed, out)))
        }
        CompressionAlg::Deflate | CompressionAlg::Zstd | CompressionAlg::Lzma => {
            // Binary-search maximum input slice that fits in `pcluster_size`, bounded up to 8x pcluster_size.
            let max_in = input.len().min(pcluster_size * 8);
            let compress_once = |slice: &[u8]| -> Result<Vec<u8>> {
                match alg {
                    CompressionAlg::Deflate => {
                        let lvl = level.unwrap_or(6).clamp(0, 9) as u32;
                        let mut enc = DeflateEncoder::new(Vec::new(), Compression::new(lvl));
                        enc.write_all(slice)
                            .map_err(|e| ErofsError::Compression(e.to_string()))?;
                        enc.finish()
                            .map_err(|e| ErofsError::Compression(e.to_string()))
                    }
                    CompressionAlg::Zstd => {
                        let lvl = level.unwrap_or(3).clamp(1, 22);
                        zstd::bulk::compress(slice, lvl)
                            .map_err(|e| ErofsError::Compression(e.to_string()))
                    }
                    CompressionAlg::Lzma => compress_microlzma(slice, level.unwrap_or(6) as u32),
                    _ => unreachable!(),
                }
            };

            // Quick attempt on full max_in first.
            let mut best: Option<(usize, Vec<u8>)> = None;
            let full_c = compress_once(&input[..max_in])?;
            if full_c.len() <= pcluster_size {
                best = Some((max_in, full_c));
            } else {
                let mut lo = block_size.min(max_in);
                let mut hi = max_in;
                while lo <= hi {
                    let mid = lo + (hi - lo) / 2;
                    let c = compress_once(&input[..mid])?;
                    if c.len() <= pcluster_size {
                        best = Some((mid, c));
                        lo = mid + 1;
                    } else if mid == 0 {
                        break;
                    } else {
                        hi = mid - 1;
                    }
                }
            }

            let Some((consumed, compressed)) = best else {
                return Ok(None);
            };
            let raw_blocks = (consumed + block_size - 1) / block_size;
            let comp_blocks = ((compressed.len() + block_size - 1) / block_size).max(1);
            if comp_blocks >= raw_blocks && consumed <= pcluster_size {
                return Ok(None);
            }
            let target_bytes = comp_blocks * block_size;
            let mut out = vec![0u8; target_bytes];
            if zero_pad {
                let start = target_bytes - compressed.len();
                out[start..].copy_from_slice(&compressed);
            } else {
                out[..compressed.len()].copy_from_slice(&compressed);
            }
            Ok(Some((consumed, out)))
        }
    }
}

fn compress_microlzma(slice: &[u8], level: u32) -> Result<Vec<u8>> {
    let mut opts = lzma_rust2::LzmaOptions::with_preset(level.clamp(0, 9));
    opts.dict_size = opts.dict_size.min(1024 * 1024).max(4096);
    let props = opts.get_props();
    let mut raw = Vec::new();
    {
        let mut writer = lzma_rust2::LzmaWriter::new_no_header(&mut raw, &opts, false)
            .map_err(|e| ErofsError::Compression(format!("MicroLZMA encode init: {e}")))?;
        writer
            .write_all(slice)
            .map_err(|e| ErofsError::Compression(format!("MicroLZMA encode write: {e}")))?;
        writer
            .finish()
            .map_err(|e| ErofsError::Compression(format!("MicroLZMA encode finish: {e}")))?;
    }
    if raw.is_empty() || raw[0] != 0 {
        return Err(ErofsError::Compression(
            "unexpected LZMA range coder leading byte".into(),
        ));
    }
    raw[0] = !props;
    Ok(raw)
}

/// Clean-room LZ4 / LZ4HC fixed-size-output block encoder.
/// Packs as many input bytes as possible such that the resulting valid LZ4 block
/// fits strictly within `max_dst_size` bytes.
fn lz4_compress_dest_size(
    src: &[u8],
    max_dst_size: usize,
    hc: bool,
    level: u32,
) -> (usize, Vec<u8>) {
    if src.is_empty() || max_dst_size < 2 {
        return (0, Vec::new());
    }

    const HASH_BITS: usize = 15;
    const HASH_SIZE: usize = 1 << HASH_BITS;
    const WINDOW_SIZE: usize = 65535;
    const MIN_MATCH: usize = 4;
    const MFLIMIT: usize = 12;

    let max_chain = if hc {
        match level {
            0..=3 => 8,
            4..=6 => 24,
            7..=9 => 64,
            _ => 128,
        }
    } else {
        1
    };

    let hash4 = |s: &[u8], p: usize| -> usize {
        let v = u32::from_le_bytes([s[p], s[p + 1], s[p + 2], s[p + 3]]);
        ((v.wrapping_mul(2654435761)) >> (32 - HASH_BITS)) as usize
    };

    let mut head = vec![u32::MAX; HASH_SIZE];
    let mut prev = if hc {
        vec![u32::MAX; src.len().min(WINDOW_SIZE * 16 + 4096)]
    } else {
        Vec::new()
    };

    let mut dst = Vec::with_capacity(max_dst_size);
    let mut anchor = 0usize;
    let mut ip = 0usize;
    let limit = src.len();

    let extra_len_bytes = |n: usize| -> usize {
        if n < 15 { 0 } else { 1 + (n - 15) / 255 }
    };

    let write_len_ext = |out: &mut Vec<u8>, mut rem: usize| {
        while rem >= 255 {
            out.push(255);
            rem -= 255;
        }
        out.push(rem as u8);
    };

    while ip + MFLIMIT <= limit {
        let h = hash4(src, ip);
        let mut best_len = 0usize;
        let mut best_off = 0usize;

        let mut cand = head[h];
        head[h] = ip as u32;
        if hc && ip < prev.len() {
            prev[ip] = cand;
        }

        let mut steps = 0usize;
        while cand != u32::MAX && steps < max_chain {
            let cpos = cand as usize;
            let off = ip.saturating_sub(cpos);
            if off == 0 || off > WINDOW_SIZE {
                break;
            }
            if src[cpos..cpos + 4] == src[ip..ip + 4] {
                let mut mlen = 4usize;
                let max_m = limit - ip;
                while mlen < max_m && src[cpos + mlen] == src[ip + mlen] {
                    mlen += 1;
                }
                if mlen > best_len {
                    best_len = mlen;
                    best_off = off;
                    if best_len >= 128 {
                        break;
                    }
                }
            }
            if !hc || cpos >= prev.len() {
                break;
            }
            cand = prev[cpos];
            steps += 1;
        }

        if best_len >= MIN_MATCH {
            let lit_len = ip - anchor;
            let match_extra = best_len - MIN_MATCH;
            let seq_bytes =
                1 + extra_len_bytes(lit_len) + lit_len + 2 + extra_len_bytes(match_extra);
            // Leave room for a trailing last-literal token (at least 1 byte if we stop after match,
            // or ensure we don't overflow max_dst_size when finalizing).
            if dst.len() + seq_bytes + 1 > max_dst_size {
                break;
            }

            let lit_tok = lit_len.min(15) as u8;
            let mat_tok = match_extra.min(15) as u8;
            dst.push((lit_tok << 4) | mat_tok);
            if lit_len >= 15 {
                write_len_ext(&mut dst, lit_len - 15);
            }
            dst.extend_from_slice(&src[anchor..ip]);
            dst.extend_from_slice(&(best_off as u16).to_le_bytes());
            if match_extra >= 15 {
                write_len_ext(&mut dst, match_extra - 15);
            }

            // Insert intermediate positions for HC
            if hc {
                let end_fill = (ip + best_len).min(limit.saturating_sub(4));
                for k in (ip + 1)..end_fill {
                    let hk = hash4(src, k);
                    if k < prev.len() {
                        prev[k] = head[hk];
                    }
                    head[hk] = k as u32;
                }
            }

            ip += best_len;
            anchor = ip;
        } else {
            ip += 1;
            // If pending literals already approach remaining destination space, stop early.
            let pending = ip - anchor;
            if dst.len() + 1 + extra_len_bytes(pending) + pending >= max_dst_size {
                break;
            }
        }
    }

    // Emit final literals up to whatever fits in `max_dst_size`.
    let rem_src = limit - anchor;
    let rem_dst = max_dst_size.saturating_sub(dst.len());
    if rem_src > 0 && rem_dst >= 2 {
        // Find max literal count `take` such that 1 + extra_len_bytes(take) + take <= rem_dst
        let mut take = rem_src.min(rem_dst - 1);
        while take > 0 && 1 + extra_len_bytes(take) + take > rem_dst {
            take -= 1;
        }
        if take > 0 {
            let tok = (take.min(15) as u8) << 4;
            dst.push(tok);
            if take >= 15 {
                write_len_ext(&mut dst, take - 15);
            }
            dst.extend_from_slice(&src[anchor..anchor + take]);
            anchor += take;
        }
    } else if rem_src == 0 && !dst.is_empty() && rem_dst >= 1 {
        // Last sequence in LZ4 MUST end with literals (even 0-byte literal token if ended on a match).
        dst.push(0u8);
    }

    (anchor, dst)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn lz4_roundtrip_fixed_output() {
        let mut data = Vec::new();
        for i in 0..300 {
            data.extend_from_slice(format!("hello erofs-rs block {i:04} repetitive data! ").as_bytes());
        }
        let (consumed, c) = lz4_compress_dest_size(&data, 4096, true, 9);
        assert!(consumed > 4096);
        assert!(c.len() <= 4096);
        let dec = lz4_decompress_block(&c, consumed, false).unwrap();
        assert_eq!(dec, data[..consumed]);

        // Also test partial decoding
        let part = lz4_decompress_block(&c, 1234, true).unwrap();
        assert_eq!(part, data[..1234]);
    }
}
