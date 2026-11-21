//! Word transformations for Brotli static and custom dictionaries.

use std::borrow::Cow;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
pub enum BrotliWordTransformType {
    Identity = 0,
    OmitLast1 = 1,
    OmitLast2 = 2,
    OmitLast3 = 3,
    OmitLast4 = 4,
    OmitLast5 = 5,
    OmitLast6 = 6,
    OmitLast7 = 7,
    OmitLast8 = 8,
    OmitLast9 = 9,
    UppercaseFirst = 10,
    UppercaseAll = 11,
    OmitFirst1 = 12,
    OmitFirst2 = 13,
    OmitFirst3 = 14,
    OmitFirst4 = 15,
    OmitFirst5 = 16,
    OmitFirst6 = 17,
    OmitFirst7 = 18,
    OmitFirst8 = 19,
    OmitFirst9 = 20,
    ShiftFirst = 21,
    ShiftAll = 22,
}

pub const BROTLI_NUM_TRANSFORM_TYPES: u8 = 23;
pub const BROTLI_TRANSFORMS_MAX_CUT_OFF: usize = 9;

#[derive(Debug, Clone)]
pub struct BrotliTransforms {
    pub prefix_suffix_size: u16,
    pub prefix_suffix: Cow<'static, [u8]>,
    pub prefix_suffix_map: Cow<'static, [u16]>,
    pub num_transforms: u32,
    pub transforms: Cow<'static, [u8]>,
    pub params: Option<Cow<'static, [u8]>>,
    pub cut_off_transforms: [i16; BROTLI_TRANSFORMS_MAX_CUT_OFF + 1],
}

impl BrotliTransforms {
    #[inline]
    pub fn prefix_id(&self, i: usize) -> u8 {
        self.transforms[i * 3]
    }

    #[inline]
    pub fn transform_type(&self, i: usize) -> u8 {
        self.transforms[i * 3 + 1]
    }

    #[inline]
    pub fn suffix_id(&self, i: usize) -> u8 {
        self.transforms[i * 3 + 2]
    }

    #[inline]
    pub fn prefix(&self, i: usize) -> &[u8] {
        let off = self.prefix_suffix_map[self.prefix_id(i) as usize] as usize;
        &self.prefix_suffix[off..]
    }

    #[inline]
    pub fn suffix(&self, i: usize) -> &[u8] {
        let off = self.prefix_suffix_map[self.suffix_id(i) as usize] as usize;
        &self.prefix_suffix[off..]
    }
}

pub static K_PREFIX_SUFFIX: &[u8; 217] =
    b"\x01 \x02, \x08 of the \x04 of \x02s \x01.\x05 and \x04 in \x01\"\x04 to \x02\">\x01\n\x02. \x01]\x05 for \x03 a \x06 that \x01'\x06 with \x06 from \x04 by \x01(\x06. The \x04 on \x04 as \x04 is \x04ing \x02\n\t\x01:\x03ed \x02=\"\x04 at \x03ly \x01,\x02='\x05.com/\x07. This \x05 not \x03er \x03al \x04ful \x04ive \x05less \x04est \x04ize \x02\xc2\xa0\x04ous \x05 the \x02e \0";

pub static K_PREFIX_SUFFIX_MAP: [u16; 50] = [
    0x00, 0x02, 0x05, 0x0E, 0x13, 0x16, 0x18, 0x1E, 0x23, 0x25,
    0x2A, 0x2D, 0x2F, 0x32, 0x34, 0x3A, 0x3E, 0x45, 0x47, 0x4E,
    0x55, 0x5A, 0x5C, 0x63, 0x68, 0x6D, 0x72, 0x77, 0x7A, 0x7C,
    0x80, 0x83, 0x88, 0x8C, 0x8E, 0x91, 0x97, 0x9F, 0xA5, 0xA9,
    0xAD, 0xB2, 0xB7, 0xBD, 0xC2, 0xC7, 0xCA, 0xCF, 0xD5, 0xD8,
];

#[rustfmt::skip]
pub static K_TRANSFORMS_DATA: [u8; 121 * 3] = [
  49, 0, 49,
  49, 0, 0,
   0, 0, 0,
  49, 12, 49,
  49, 10, 0,
  49, 0, 47,
   0, 0, 49,
   4, 0, 0,
  49, 0, 3,
  49, 10, 49,
  49, 0, 6,
  49, 13, 49,
  49, 1, 49,
   1, 0, 0,
  49, 0, 1,
   0, 10, 0,
  49, 0, 7,
  49, 0, 9,
  48, 0, 0,
  49, 0, 8,
  49, 0, 5,
  49, 0, 10,
  49, 0, 11,
  49, 3, 49,
  49, 0, 13,
  49, 0, 14,
  49, 14, 49,
  49, 2, 49,
  49, 0, 15,
  49, 0, 16,
   0, 10, 49,
  49, 0, 12,
   5, 0, 49,
   0, 0, 1,
  49, 15, 49,
  49, 0, 18,
  49, 0, 17,
  49, 0, 19,
  49, 0, 20,
  49, 16, 49,
  49, 17, 49,
  47, 0, 49,
  49, 4, 49,
  49, 0, 22,
  49, 11, 49,
  49, 0, 23,
  49, 0, 24,
  49, 0, 25,
  49, 7, 49,
  49, 1, 26,
  49, 0, 27,
  49, 0, 28,
   0, 0, 12,
  49, 0, 29,
  49, 20, 49,
  49, 18, 49,
  49, 6, 49,
  49, 0, 21,
  49, 10, 1,
  49, 8, 49,
  49, 0, 31,
  49, 0, 32,
  47, 0, 3,
  49, 5, 49,
  49, 9, 49,
   0, 10, 1,
  49, 10, 8,
   5, 0, 21,
  49, 11, 0,
  49, 10, 10,
  49, 0, 30,
   0, 0, 5,
  35, 0, 49,
  47, 0, 2,
  49, 10, 17,
  49, 0, 36,
  49, 0, 33,
   5, 0, 0,
  49, 10, 21,
  49, 10, 5,
  49, 0, 37,
   0, 0, 30,
  49, 0, 38,
   0, 11, 0,
  49, 0, 39,
   0, 11, 49,
  49, 0, 34,
  49, 11, 8,
  49, 10, 12,
   0, 0, 21,
  49, 0, 40,
   0, 10, 12,
  49, 0, 41,
  49, 0, 42,
  49, 11, 17,
  49, 0, 43,
   0, 10, 5,
  49, 11, 10,
   0, 0, 34,
  49, 10, 33,
  49, 0, 44,
   0 + 49, 11, 5,
  45, 0, 49,
   0, 0, 33,
  49, 10, 30,
  49, 11, 30,
  49, 0, 46,
  49, 11, 1,
  49, 10, 34,
   0, 10, 33,
   0, 11, 30,
   0, 11, 1,
  49, 11, 33,
  49, 11, 21,
  49, 11, 12,
   0, 11, 5,
  49, 11, 34,
   0, 11, 12,
   0, 10, 30,
   0, 11, 34,
   0, 10, 34,
];

pub static DEFAULT_TRANSFORMS: BrotliTransforms = BrotliTransforms {
    prefix_suffix_size: 217,
    prefix_suffix: Cow::Borrowed(K_PREFIX_SUFFIX.as_slice()),
    prefix_suffix_map: Cow::Borrowed(K_PREFIX_SUFFIX_MAP.as_slice()),
    num_transforms: 121,
    transforms: Cow::Borrowed(K_TRANSFORMS_DATA.as_slice()),
    params: None,
    cut_off_transforms: [0, 12, 27, 23, 42, 63, 56, 48, 59, 64],
};

#[inline]
pub fn brotli_get_transforms() -> &'static BrotliTransforms {
    &DEFAULT_TRANSFORMS
}

#[inline]
fn to_upper_case(p: &mut [u8]) -> usize {
    if p[0] < 0xC0 {
        if p[0] >= b'a' && p[0] <= b'z' {
            p[0] ^= 32;
        }
        return 1;
    }
    if p[0] < 0xE0 {
        if p.len() > 1 {
            p[1] ^= 32;
        }
        return 2;
    }
    if p.len() > 2 {
        p[2] ^= 5;
    }
    3
}

#[inline]
fn shift_utf8(word: &mut [u8], parameter: u16) -> usize {
    let word_len = word.len();
    let mut scalar: u32 =
        ((parameter & 0x7FFF) as u32) + (0x1000000u32 - ((parameter & 0x8000) as u32));
    if word[0] < 0x80 {
        scalar = scalar.wrapping_add(word[0] as u32);
        word[0] = (scalar & 0x7F) as u8;
        1
    } else if word[0] < 0xC0 {
        1
    } else if word[0] < 0xE0 {
        if word_len < 2 {
            return 1;
        }
        scalar = scalar.wrapping_add(((word[1] & 0x3F) as u32) | (((word[0] & 0x1F) as u32) << 6));
        word[0] = (0xC0 | ((scalar >> 6) & 0x1F)) as u8;
        word[1] = (word[1] & 0xC0) | ((scalar & 0x3F) as u8);
        2
    } else if word[0] < 0xF0 {
        if word_len < 3 {
            return word_len;
        }
        scalar = scalar.wrapping_add(
            ((word[2] & 0x3F) as u32)
                | (((word[1] & 0x3F) as u32) << 6)
                | (((word[0] & 0x0F) as u32) << 12),
        );
        word[0] = (0xE0 | ((scalar >> 12) & 0x0F)) as u8;
        word[1] = (word[1] & 0xC0) | (((scalar >> 6) & 0x3F) as u8);
        word[2] = (word[2] & 0xC0) | ((scalar & 0x3F) as u8);
        3
    } else if word[0] < 0xF8 {
        if word_len < 4 {
            return word_len;
        }
        scalar = scalar.wrapping_add(
            ((word[3] & 0x3F) as u32)
                | (((word[2] & 0x3F) as u32) << 6)
                | (((word[1] & 0x3F) as u32) << 12)
                | (((word[0] & 0x07) as u32) << 18),
        );
        word[0] = (0xF0 | ((scalar >> 18) & 0x07)) as u8;
        word[1] = (word[1] & 0xC0) | (((scalar >> 12) & 0x3F) as u8);
        word[2] = (word[2] & 0xC0) | (((scalar >> 6) & 0x3F) as u8);
        word[3] = (word[3] & 0xC0) | ((scalar & 0x3F) as u8);
        4
    } else {
        1
    }
}

pub fn brotli_transform_dictionary_word(
    dst: &mut [u8],
    word: &[u8],
    mut len: i32,
    transforms: &BrotliTransforms,
    transform_idx: usize,
) -> usize {
    let mut idx = 0usize;
    let prefix = transforms.prefix(transform_idx);
    let t = transforms.transform_type(transform_idx);
    let suffix = transforms.suffix(transform_idx);

    let prefix_len = prefix[0] as usize;
    if prefix_len > 0 {
        dst[idx..idx + prefix_len].copy_from_slice(&prefix[1..1 + prefix_len]);
        idx += prefix_len;
    }

    let mut word_start = 0usize;
    if t <= BrotliWordTransformType::OmitLast9 as u8 {
        len -= t as i32;
    } else if t >= BrotliWordTransformType::OmitFirst1 as u8
        && t <= BrotliWordTransformType::OmitFirst9 as u8
    {
        let skip = (t - (BrotliWordTransformType::OmitFirst1 as u8 - 1)) as usize;
        word_start += skip;
        len -= skip as i32;
    }

    let copy_len = if len > 0 { len as usize } else { 0 };
    if copy_len > 0 {
        dst[idx..idx + copy_len].copy_from_slice(&word[word_start..word_start + copy_len]);
        idx += copy_len;
    }

    if t == BrotliWordTransformType::UppercaseFirst as u8 {
        if copy_len > 0 {
            to_upper_case(&mut dst[idx - copy_len..idx]);
        }
    } else if t == BrotliWordTransformType::UppercaseAll as u8 {
        let mut off = idx - copy_len;
        let mut rem = copy_len as isize;
        while rem > 0 {
            let step = to_upper_case(&mut dst[off..idx]);
            off += step;
            rem -= step as isize;
        }
    } else if t == BrotliWordTransformType::ShiftFirst as u8 {
        if let Some(params) = &transforms.params {
            let param =
                (params[transform_idx * 2] as u16) + ((params[transform_idx * 2 + 1] as u16) << 8);
            if copy_len > 0 {
                shift_utf8(&mut dst[idx - copy_len..idx], param);
            }
        }
    } else if t == BrotliWordTransformType::ShiftAll as u8 {
        if let Some(params) = &transforms.params {
            let param =
                (params[transform_idx * 2] as u16) + ((params[transform_idx * 2 + 1] as u16) << 8);
            let mut off = idx - copy_len;
            let mut rem = copy_len as isize;
            while rem > 0 {
                let step = shift_utf8(&mut dst[off..idx], param);
                off += step;
                rem -= step as isize;
            }
        }
    }

    let suffix_len = suffix[0] as usize;
    if suffix_len > 0 {
        dst[idx..idx + suffix_len].copy_from_slice(&suffix[1..1 + suffix_len]);
        idx += suffix_len;
    }

    idx
}
