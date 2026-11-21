#![allow(dead_code)]

pub const K_MIN_UTF8_RATIO: f64 = 0.75;

fn brotli_parse_as_utf8(symbol: &mut i32, input: &[u8]) -> usize {
    let size = input.len();
    // ASCII
    if (input[0] & 0x80) == 0 {
        *symbol = input[0] as i32;
        if *symbol > 0 {
            return 1;
        }
    }
    // 2-byte UTF8
    if size > 1 && (input[0] & 0xE0) == 0xC0 && (input[1] & 0xC0) == 0x80 {
        *symbol = (((input[0] & 0x1F) as i32) << 6) | ((input[1] & 0x3F) as i32);
        if *symbol > 0x7F {
            return 2;
        }
    }
    // 3-byte UTF8
    if size > 2
        && (input[0] & 0xF0) == 0xE0
        && (input[1] & 0xC0) == 0x80
        && (input[2] & 0xC0) == 0x80
    {
        *symbol = (((input[0] & 0x0F) as i32) << 12)
            | (((input[1] & 0x3F) as i32) << 6)
            | ((input[2] & 0x3F) as i32);
        if *symbol > 0x7FF {
            return 3;
        }
    }
    // 4-byte UTF8
    if size > 3
        && (input[0] & 0xF8) == 0xF0
        && (input[1] & 0xC0) == 0x80
        && (input[2] & 0xC0) == 0x80
        && (input[3] & 0xC0) == 0x80
    {
        *symbol = (((input[0] & 0x07) as i32) << 18)
            | (((input[1] & 0x3F) as i32) << 12)
            | (((input[2] & 0x3F) as i32) << 6)
            | ((input[3] & 0x3F) as i32);
        if *symbol > 0xFFFF && *symbol <= 0x10FFFF {
            return 4;
        }
    }
    // Not UTF8, emit a special symbol above the UTF8-code space
    *symbol = 0x110000 | (input[0] as i32);
    1
}

/// Returns true if more than `min_fraction` of the data is UTF-8 encoded.
pub fn brotli_is_mostly_utf8(
    data: &[u8],
    pos: usize,
    mask: usize,
    length: usize,
    min_fraction: f64,
) -> bool {
    let mut size_utf8 = 0usize;
    let mut i = 0usize;
    while i < length {
        let mut symbol = 0i32;
        let start = (pos + i) & mask;
        // Ringbuffer has tail/contiguous guarantee in C up to mask, or limit slice.
        let bytes_read = brotli_parse_as_utf8(&mut symbol, &data[start..start + (length - i).min(data.len() - start)]);
        i += bytes_read;
        if symbol < 0x110000 {
            size_utf8 += bytes_read;
        }
    }
    (size_utf8 as f64) > min_fraction * (length as f64)
}
