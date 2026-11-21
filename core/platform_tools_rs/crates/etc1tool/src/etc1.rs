use thiserror::Error;

/// Size in bytes of a PKM header (`"PKM 10"` + metadata).
pub const ETC1_PKM_HEADER_SIZE: usize = 16;
/// Size in bytes of one compressed 4x4 ETC1 block.
pub const ETC1_ENCODED_BLOCK_SIZE: usize = 8;
/// Size in bytes of one decoded 4x4 RGB888 block (`16 * 3`).
pub const ETC1_DECODED_BLOCK_SIZE: usize = 48;

const PKM_MAGIC: &[u8; 6] = b"PKM 10";

const MODIFIER_TABLE: [[i32; 4]; 8] = [
    [2, 8, -2, -8],
    [5, 17, -5, -17],
    [9, 29, -9, -29],
    [13, 42, -13, -42],
    [18, 60, -18, -60],
    [24, 80, -24, -80],
    [33, 106, -33, -106],
    [47, 183, -47, -183],
];

const DIFF_LOOKUP: [i32; 8] = [0, 1, 2, 3, -4, -3, -2, -1];
const Y_MASK: [u16; 5] = [0x0000, 0x000f, 0x00ff, 0x0fff, 0xffff];
const X_MASK: [u16; 5] = [0x0000, 0x1111, 0x3333, 0x7777, 0xffff];

#[derive(Debug, Error)]
pub enum Etc1Error {
    #[error("unsupported pixel_size {0} (expected 2 or 3)")]
    UnsupportedPixelSize(usize),
    #[error("stride {stride} is smaller than width * pixel_size ({min})")]
    InvalidStride { stride: usize, min: usize },
    #[error("input buffer too small: expected at least {expected} bytes, got {actual}")]
    BufferTooSmall { expected: usize, actual: usize },
}

#[inline]
fn clamp_u8(v: i32) -> i32 {
    v.clamp(0, 255)
}

#[inline]
fn convert4_to_8(b: u32) -> i32 {
    let c = b & 0xf;
    ((c << 4) | c) as i32
}

#[inline]
fn convert5_to_8(b: u32) -> i32 {
    let c = b & 0x1f;
    ((c << 3) | (c >> 2)) as i32
}

#[inline]
fn convert6_to_8(b: u32) -> i32 {
    let c = b & 0x3f;
    ((c << 2) | (c >> 4)) as i32
}

#[inline]
fn convert8_to_4(v: u8) -> u32 {
    let t = u32::from(v) * 15;
    (t + (t >> 8) + 128) >> 8
}

#[inline]
fn convert8_to_5(v: u8) -> u32 {
    let t = u32::from(v) * 31;
    (t + (t >> 8) + 128) >> 8
}

#[inline]
fn convert_diff(bit5: u32, diff3: u32) -> i32 {
    let signed = (bit5 as i32 + DIFF_LOOKUP[(diff3 & 7) as usize]) & 0x1f;
    convert5_to_8(signed as u32)
}

/// Returns the encoded ETC1 payload size in bytes (excluding PKM header) for `width x height`.
pub fn encoded_data_size(width: u32, height: u32) -> usize {
    let ew = ((width as usize) + 3) & !3;
    let eh = ((height as usize) + 3) & !3;
    (ew * eh) >> 1
}

/// Formats a 16-byte PKM 1.0 header for an ETC1 image of `width x height`.
pub fn format_pkm_header(width: u32, height: u32) -> [u8; ETC1_PKM_HEADER_SIZE] {
    let ew = ((width + 3) & !3) as u16;
    let eh = ((height + 3) & !3) as u16;
    let w = width as u16;
    let h = height as u16;
    let mut out = [0u8; ETC1_PKM_HEADER_SIZE];
    out[0..6].copy_from_slice(PKM_MAGIC);
    out[6..8].copy_from_slice(&0u16.to_be_bytes());
    out[8..10].copy_from_slice(&ew.to_be_bytes());
    out[10..12].copy_from_slice(&eh.to_be_bytes());
    out[12..14].copy_from_slice(&w.to_be_bytes());
    out[14..16].copy_from_slice(&h.to_be_bytes());
    out
}

/// Returns `true` if `header` is a valid PKM 1.0 ETC1_RGB_NO_MIPMAPS header.
pub fn is_valid_pkm_header(header: &[u8; ETC1_PKM_HEADER_SIZE]) -> bool {
    if &header[0..6] != PKM_MAGIC {
        return false;
    }
    if header[6] != 0 || header[7] != 0 {
        return false;
    }
    let ew = u16::from_be_bytes([header[8], header[9]]);
    let eh = u16::from_be_bytes([header[10], header[11]]);
    let w = u16::from_be_bytes([header[12], header[13]]);
    let h = u16::from_be_bytes([header[14], header[15]]);
    ew >= w && (ew - w) < 4 && eh >= h && (eh - h) < 4
}

/// Reads the original image width from a PKM header.
pub fn pkm_get_width(header: &[u8; ETC1_PKM_HEADER_SIZE]) -> u32 {
    u32::from(u16::from_be_bytes([header[12], header[13]]))
}

/// Reads the original image height from a PKM header.
pub fn pkm_get_height(header: &[u8; ETC1_PKM_HEADER_SIZE]) -> u32 {
    u32::from(u16::from_be_bytes([header[14], header[15]]))
}

fn average_colors_subblock(
    pixels: &[u8; ETC1_DECODED_BLOCK_SIZE],
    valid_mask: u16,
    flipped: bool,
    second: bool,
) -> [u8; 3] {
    let mut sr = 0u32;
    let mut sg = 0u32;
    let mut sb = 0u32;

    if flipped {
        let start = if second { 8 } else { 0 };
        for idx in start..start + 8 {
            if (valid_mask & (1 << idx)) != 0 {
                let p = idx * 3;
                sr += u32::from(pixels[p]);
                sg += u32::from(pixels[p + 1]);
                sb += u32::from(pixels[p + 2]);
            }
        }
    } else {
        let col_start = if second { 2 } else { 0 };
        for row in 0..4 {
            for col in col_start..col_start + 2 {
                let idx = row * 4 + col;
                if (valid_mask & (1 << idx)) != 0 {
                    let p = idx * 3;
                    sr += u32::from(pixels[p]);
                    sg += u32::from(pixels[p + 1]);
                    sb += u32::from(pixels[p + 2]);
                }
            }
        }
    }

    [
        ((sr + 4) >> 3) as u8,
        ((sg + 4) >> 3) as u8,
        ((sb + 4) >> 3) as u8,
    ]
}

#[inline]
fn choose_modifier(
    base: &[i32; 3],
    pixel: &[u8],
    pixel_bit_index: u32,
    table: &[i32; 4],
    low: &mut u32,
) -> u32 {
    let pr = i32::from(pixel[0]);
    let pg = i32::from(pixel[1]);
    let pb = i32::from(pixel[2]);

    let mut best_err = u32::MAX;
    let mut best_idx = 0u32;

    for (idx, &delta) in table.iter().enumerate() {
        let dg = clamp_u8(base[1] + delta) - pg;
        let eg = (dg * dg) as u32 * 6;
        if eg >= best_err {
            continue;
        }
        let dr = clamp_u8(base[0] + delta) - pr;
        let er = (dr * dr) as u32 * 3 + eg;
        if er >= best_err {
            continue;
        }
        let db = clamp_u8(base[2] + delta) - pb;
        let err = (db * db) as u32 + er;
        if err < best_err {
            best_err = err;
            best_idx = idx as u32;
        }
    }

    *low |= (((best_idx & 2) << 15) | (best_idx & 1)) << pixel_bit_index;
    best_err
}

#[derive(Clone, Copy)]
struct EncodedCandidate {
    high: u32,
    low: u32,
    error: u32,
}

fn encode_subblock_helper(
    pixels: &[u8; ETC1_DECODED_BLOCK_SIZE],
    valid_mask: u16,
    cand: &mut EncodedCandidate,
    flipped: bool,
    second: bool,
    base: &[i32; 3],
    table: &[i32; 4],
) {
    let mut err = cand.error;
    if flipped {
        let y_start = if second { 2 } else { 0 };
        for y in y_start..y_start + 2 {
            for x in 0..4 {
                let pixel_idx = x + y * 4;
                if (valid_mask & (1 << pixel_idx)) != 0 {
                    let bit_idx = (y + x * 4) as u32;
                    let p = pixel_idx * 3;
                    err += choose_modifier(base, &pixels[p..p + 3], bit_idx, table, &mut cand.low);
                }
            }
        }
    } else {
        let x_start = if second { 2 } else { 0 };
        for y in 0..4 {
            for x in x_start..x_start + 2 {
                let pixel_idx = x + y * 4;
                if (valid_mask & (1 << pixel_idx)) != 0 {
                    let bit_idx = (y + x * 4) as u32;
                    let p = pixel_idx * 3;
                    err += choose_modifier(base, &pixels[p..p + 3], bit_idx, table, &mut cand.low);
                }
            }
        }
    }
    cand.error = err;
}

fn encode_base_colors(
    avg0: [u8; 3],
    avg1: [u8; 3],
    flipped: bool,
) -> (u32, [i32; 3], [i32; 3]) {
    let r0_5 = convert8_to_5(avg0[0]);
    let g0_5 = convert8_to_5(avg0[1]);
    let b0_5 = convert8_to_5(avg0[2]);
    let r1_5 = convert8_to_5(avg1[0]);
    let g1_5 = convert8_to_5(avg1[1]);
    let b1_5 = convert8_to_5(avg1[2]);

    let dr = r1_5 as i32 - r0_5 as i32;
    let dg = g1_5 as i32 - g0_5 as i32;
    let db = b1_5 as i32 - b0_5 as i32;

    if (-4..=3).contains(&dr) && (-4..=3).contains(&dg) && (-4..=3).contains(&db) {
        let high = (r0_5 << 27)
            | (((dr as u32) & 0x7) << 24)
            | (g0_5 << 19)
            | (((dg as u32) & 0x7) << 16)
            | (b0_5 << 11)
            | (((db as u32) & 0x7) << 8)
            | 2
            | u32::from(flipped);
        let base0 = [
            convert5_to_8(r0_5),
            convert5_to_8(g0_5),
            convert5_to_8(b0_5),
        ];
        let base1 = [
            convert5_to_8(r1_5),
            convert5_to_8(g1_5),
            convert5_to_8(b1_5),
        ];
        (high, base0, base1)
    } else {
        let r0_4 = convert8_to_4(avg0[0]);
        let g0_4 = convert8_to_4(avg0[1]);
        let b0_4 = convert8_to_4(avg0[2]);
        let r1_4 = convert8_to_4(avg1[0]);
        let g1_4 = convert8_to_4(avg1[1]);
        let b1_4 = convert8_to_4(avg1[2]);

        let high = (r0_4 << 28)
            | (r1_4 << 24)
            | (g0_4 << 20)
            | (g1_4 << 16)
            | (b0_4 << 12)
            | (b1_4 << 8)
            | u32::from(flipped);
        let base0 = [
            convert4_to_8(r0_4),
            convert4_to_8(g0_4),
            convert4_to_8(b0_4),
        ];
        let base1 = [
            convert4_to_8(r1_4),
            convert4_to_8(g1_4),
            convert4_to_8(b1_4),
        ];
        (high, base0, base1)
    }
}

fn encode_block_helper(
    pixels: &[u8; ETC1_DECODED_BLOCK_SIZE],
    valid_mask: u16,
    avg0: [u8; 3],
    avg1: [u8; 3],
    flipped: bool,
) -> EncodedCandidate {
    let (base_high, base0, base1) = encode_base_colors(avg0, avg1, flipped);

    let mut best0 = EncodedCandidate {
        high: base_high,
        low: 0,
        error: u32::MAX,
    };

    for (t0, table) in MODIFIER_TABLE.iter().enumerate() {
        let mut cand = EncodedCandidate {
            high: base_high | ((t0 as u32) << 5),
            low: 0,
            error: 0,
        };
        encode_subblock_helper(pixels, valid_mask, &mut cand, flipped, false, &base0, table);
        if cand.error < best0.error {
            best0 = cand;
        }
    }

    let base_after_0 = best0;
    let mut best1 = base_after_0;
    encode_subblock_helper(
        pixels,
        valid_mask,
        &mut best1,
        flipped,
        true,
        &base1,
        &MODIFIER_TABLE[0],
    );

    for (t1, table) in MODIFIER_TABLE.iter().enumerate().skip(1) {
        let mut cand = EncodedCandidate {
            high: base_after_0.high | ((t1 as u32) << 2),
            low: base_after_0.low,
            error: base_after_0.error,
        };
        encode_subblock_helper(pixels, valid_mask, &mut cand, flipped, true, &base1, table);
        if cand.error < best1.error {
            best1 = cand;
        }
    }

    best1
}

/// Encodes a single 4x4 RGB888 block into an 8-byte ETC1 block.
pub fn encode_block(
    pixels: &[u8; ETC1_DECODED_BLOCK_SIZE],
    valid_pixel_mask: u16,
) -> [u8; ETC1_ENCODED_BLOCK_SIZE] {
    let avg_v0 = average_colors_subblock(pixels, valid_pixel_mask, false, false);
    let avg_v1 = average_colors_subblock(pixels, valid_pixel_mask, false, true);
    let avg_h0 = average_colors_subblock(pixels, valid_pixel_mask, true, false);
    let avg_h1 = average_colors_subblock(pixels, valid_pixel_mask, true, true);

    let mut best = encode_block_helper(pixels, valid_pixel_mask, avg_v0, avg_v1, false);
    let cand_flipped = encode_block_helper(pixels, valid_pixel_mask, avg_h0, avg_h1, true);
    if cand_flipped.error < best.error {
        best = cand_flipped;
    }

    let mut out = [0u8; ETC1_ENCODED_BLOCK_SIZE];
    out[0..4].copy_from_slice(&best.high.to_be_bytes());
    out[4..8].copy_from_slice(&best.low.to_be_bytes());
    out
}

/// Decodes an 8-byte ETC1 block into a 4x4 RGB888 block (48 bytes).
pub fn decode_block(block: &[u8; ETC1_ENCODED_BLOCK_SIZE]) -> [u8; ETC1_DECODED_BLOCK_SIZE] {
    let high = u32::from_be_bytes([block[0], block[1], block[2], block[3]]);
    let low = u32::from_be_bytes([block[4], block[5], block[6], block[7]]);

    let (r1, r2, g1, g2, b1, b2) = if (high & 2) != 0 {
        let r_base = (high >> 27) & 0x1f;
        let g_base = (high >> 19) & 0x1f;
        let b_base = (high >> 11) & 0x1f;
        (
            convert5_to_8(r_base),
            convert_diff(r_base, high >> 24),
            convert5_to_8(g_base),
            convert_diff(g_base, high >> 16),
            convert5_to_8(b_base),
            convert_diff(b_base, high >> 8),
        )
    } else {
        (
            convert4_to_8(high >> 28),
            convert4_to_8(high >> 24),
            convert4_to_8(high >> 20),
            convert4_to_8(high >> 16),
            convert4_to_8(high >> 12),
            convert4_to_8(high >> 8),
        )
    };

    let t1 = &MODIFIER_TABLE[((high >> 5) & 7) as usize];
    let t2 = &MODIFIER_TABLE[((high >> 2) & 7) as usize];
    let flipped = (high & 1) != 0;

    let mut out = [0u8; ETC1_DECODED_BLOCK_SIZE];
    if flipped {
        for y in 0..4 {
            let (br, bg, bb, table) = if y < 2 {
                (r1, g1, b1, t1)
            } else {
                (r2, g2, b2, t2)
            };
            for x in 0..4 {
                let bit_idx = y + x * 4;
                let sel = (((low >> (bit_idx + 16)) & 1) << 1) | ((low >> bit_idx) & 1);
                let delta = table[sel as usize];
                let dst = (x + y * 4) * 3;
                out[dst] = clamp_u8(br + delta) as u8;
                out[dst + 1] = clamp_u8(bg + delta) as u8;
                out[dst + 2] = clamp_u8(bb + delta) as u8;
            }
        }
    } else {
        for x in 0..4 {
            let (br, bg, bb, table) = if x < 2 {
                (r1, g1, b1, t1)
            } else {
                (r2, g2, b2, t2)
            };
            for y in 0..4 {
                let bit_idx = y + x * 4;
                let sel = (((low >> (bit_idx + 16)) & 1) << 1) | ((low >> bit_idx) & 1);
                let delta = table[sel as usize];
                let dst = (x + y * 4) * 3;
                out[dst] = clamp_u8(br + delta) as u8;
                out[dst + 1] = clamp_u8(bg + delta) as u8;
                out[dst + 2] = clamp_u8(bb + delta) as u8;
            }
        }
    }
    out
}

fn validate_image_params(
    width: u32,
    height: u32,
    pixel_size: usize,
    stride: usize,
) -> Result<(), Etc1Error> {
    if pixel_size != 2 && pixel_size != 3 {
        return Err(Etc1Error::UnsupportedPixelSize(pixel_size));
    }
    let min_stride = width as usize * pixel_size;
    if stride < min_stride {
        return Err(Etc1Error::InvalidStride {
            stride,
            min: min_stride,
        });
    }
    let _ = height;
    Ok(())
}

/// Encodes an image (`pixel_size == 3` RGB888 or `pixel_size == 2` RGB565 LE) to raw ETC1 blocks.
pub fn encode_image(
    pixels: &[u8],
    width: u32,
    height: u32,
    pixel_size: usize,
    stride: usize,
) -> Result<Vec<u8>, Etc1Error> {
    validate_image_params(width, height, pixel_size, stride)?;
    let encoded_width = (width + 3) & !3;
    let encoded_height = (height + 3) & !3;
    if height > 0 {
        let min_buf = (height as usize - 1) * stride + width as usize * pixel_size;
        if pixels.len() < min_buf {
            return Err(Etc1Error::BufferTooSmall {
                expected: min_buf,
                actual: pixels.len(),
            });
        }
    }

    let mut out = Vec::with_capacity(encoded_data_size(width, height));
    let mut block = [0u8; ETC1_DECODED_BLOCK_SIZE];

    for y in (0..encoded_height).step_by(4) {
        let y_end = (height.saturating_sub(y)).min(4) as usize;
        let y_mask = Y_MASK[y_end];
        for x in (0..encoded_width).step_by(4) {
            let x_end = (width.saturating_sub(x)).min(4) as usize;
            let mask = y_mask & X_MASK[x_end];
            if y < height && x < width {
                for cy in 0..y_end {
                    let src_row = (y as usize + cy) * stride + x as usize * pixel_size;
                    let dst_row = cy * 12;
                    if pixel_size == 3 {
                        block[dst_row..dst_row + x_end * 3]
                            .copy_from_slice(&pixels[src_row..src_row + x_end * 3]);
                    } else {
                        for cx in 0..x_end {
                            let b0 = pixels[src_row + cx * 2];
                            let b1 = pixels[src_row + cx * 2 + 1];
                            let c = u32::from(u16::from_le_bytes([b0, b1]));
                            let r = convert5_to_8(c >> 11) as u8;
                            let g = convert6_to_8(c >> 5) as u8;
                            let b = convert5_to_8(c) as u8;
                            block[dst_row + cx * 3] = r;
                            block[dst_row + cx * 3 + 1] = g;
                            block[dst_row + cx * 3 + 2] = b;
                        }
                    }
                }
            }
            out.extend_from_slice(&encode_block(&block, mask));
        }
    }

    Ok(out)
}

/// Decodes raw ETC1 blocks into an image buffer (`pixel_size == 3` RGB888 or `pixel_size == 2` RGB565 LE).
pub fn decode_image(
    encoded: &[u8],
    width: u32,
    height: u32,
    pixel_size: usize,
    stride: usize,
) -> Result<Vec<u8>, Etc1Error> {
    validate_image_params(width, height, pixel_size, stride)?;
    let expected_enc = encoded_data_size(width, height);
    if encoded.len() < expected_enc {
        return Err(Etc1Error::BufferTooSmall {
            expected: expected_enc,
            actual: encoded.len(),
        });
    }

    let encoded_width = (width + 3) & !3;
    let encoded_height = (height + 3) & !3;
    let out_len = height as usize * stride;
    let mut out = vec![0u8; out_len];
    let mut src_off = 0usize;

    for y in (0..encoded_height).step_by(4) {
        let y_end = (height.saturating_sub(y)).min(4) as usize;
        for x in (0..encoded_width).step_by(4) {
            let x_end = (width.saturating_sub(x)).min(4) as usize;
            let block: &[u8; ETC1_ENCODED_BLOCK_SIZE] = encoded
                [src_off..src_off + ETC1_ENCODED_BLOCK_SIZE]
                .try_into()
                .unwrap();
            src_off += ETC1_ENCODED_BLOCK_SIZE;
            let decoded = decode_block(block);

            for cy in 0..y_end {
                let src_row = cy * 12;
                let dst_row = (y as usize + cy) * stride + x as usize * pixel_size;
                if pixel_size == 3 {
                    out[dst_row..dst_row + x_end * 3]
                        .copy_from_slice(&decoded[src_row..src_row + x_end * 3]);
                } else {
                    for cx in 0..x_end {
                        let r = decoded[src_row + cx * 3];
                        let g = decoded[src_row + cx * 3 + 1];
                        let b = decoded[src_row + cx * 3 + 2];
                        let rgb565 = ((u16::from(r) >> 3) << 11)
                            | ((u16::from(g) >> 2) << 5)
                            | (u16::from(b) >> 3);
                        out[dst_row + cx * 2..dst_row + cx * 2 + 2]
                            .copy_from_slice(&rgb565.to_le_bytes());
                    }
                }
            }
        }
    }

    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pkm_header_roundtrip_and_validation() {
        let hdr = format_pkm_header(13, 7);
        assert!(is_valid_pkm_header(&hdr));
        assert_eq!(pkm_get_width(&hdr), 13);
        assert_eq!(pkm_get_height(&hdr), 7);
        assert_eq!(u16::from_be_bytes([hdr[8], hdr[9]]), 16);
        assert_eq!(u16::from_be_bytes([hdr[10], hdr[11]]), 8);

        let mut bad = hdr;
        bad[0] = b'X';
        assert!(!is_valid_pkm_header(&bad));
        let mut bad2 = hdr;
        bad2[7] = 1;
        assert!(!is_valid_pkm_header(&bad2));
    }

    #[test]
    fn test_encode_decode_image_roundtrip() {
        let w = 7u32;
        let h = 5u32;
        let mut rgb = vec![0u8; (w * h * 3) as usize];
        for y in 0..h {
            for x in 0..w {
                let off = ((y * w + x) * 3) as usize;
                rgb[off] = (x * 36) as u8;
                rgb[off + 1] = (y * 50) as u8;
                rgb[off + 2] = ((x + y) * 20) as u8;
            }
        }
        let enc = encode_image(&rgb, w, h, 3, (w * 3) as usize).unwrap();
        assert_eq!(enc.len(), encoded_data_size(w, h));
        let dec = decode_image(&enc, w, h, 3, (w * 3) as usize).unwrap();
        assert_eq!(dec.len(), rgb.len());
    }
}
