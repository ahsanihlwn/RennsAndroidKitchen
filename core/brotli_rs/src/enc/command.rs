#![allow(dead_code)]

use crate::common::constants::*;
use crate::enc::fast_log::log2_floor_non_zero;
use crate::enc::params::BrotliDistanceParams;

pub static K_BROTLI_INS_BASE: [u32; BROTLI_NUM_INS_COPY_CODES] = [
    0, 1, 2, 3, 4, 5, 6, 8, 10, 14, 18, 26,
    34, 50, 66, 98, 130, 194, 322, 578, 1090, 2114, 6210, 22594,
];

pub static K_BROTLI_INS_EXTRA: [u32; BROTLI_NUM_INS_COPY_CODES] = [
    0, 0, 0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 7, 8, 9, 10, 12, 14, 24,
];

pub static K_BROTLI_COPY_BASE: [u32; BROTLI_NUM_INS_COPY_CODES] = [
    2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 14, 18,
    22, 30, 38, 54, 70, 102, 134, 198, 326, 582, 1094, 2118,
];

pub static K_BROTLI_COPY_EXTRA: [u32; BROTLI_NUM_INS_COPY_CODES] = [
    0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 7, 8, 9, 10, 24,
];

#[inline(always)]
pub fn get_insert_length_code(insertlen: usize) -> u16 {
    if insertlen < 6 {
        insertlen as u16
    } else if insertlen < 130 {
        let nbits = log2_floor_non_zero(insertlen - 2) - 1;
        ((nbits << 1) + (((insertlen - 2) >> nbits) as u32) + 2) as u16
    } else if insertlen < 2114 {
        (log2_floor_non_zero(insertlen - 66) + 10) as u16
    } else if insertlen < 6210 {
        21
    } else if insertlen < 22594 {
        22
    } else {
        23
    }
}

#[inline(always)]
pub fn get_copy_length_code(copylen: usize) -> u16 {
    if copylen < 10 {
        (copylen - 2) as u16
    } else if copylen < 134 {
        let nbits = log2_floor_non_zero(copylen - 6) - 1;
        ((nbits << 1) + (((copylen - 6) >> nbits) as u32) + 4) as u16
    } else if copylen < 2118 {
        (log2_floor_non_zero(copylen - 70) + 12) as u16
    } else {
        23
    }
}

#[inline(always)]
pub fn combine_length_codes(inscode: u16, copycode: u16, use_last_distance: bool) -> u16 {
    let bits64 = (copycode & 0x7) | ((inscode & 0x7) << 3);
    if use_last_distance && inscode < 8 && copycode < 16 {
        if copycode < 8 { bits64 } else { bits64 | 64 }
    } else {
        let mut offset = 2u32 * (((copycode >> 3) as u32) + 3 * ((inscode >> 3) as u32));
        offset = (offset << 5) + 0x40 + ((0x520D40u32 >> offset) & 0xC0);
        (offset as u16) | bits64
    }
}

#[inline(always)]
pub fn prefix_encode_copy_distance(
    distance_code: usize,
    num_direct_codes: usize,
    postfix_bits: usize,
) -> (u16, u32) {
    if distance_code < BROTLI_NUM_DISTANCE_SHORT_CODES + num_direct_codes {
        (distance_code as u16, 0)
    } else {
        let dist = (1usize << (postfix_bits + 2))
            + (distance_code - BROTLI_NUM_DISTANCE_SHORT_CODES - num_direct_codes);
        let bucket = log2_floor_non_zero(dist) as usize - 1;
        let postfix_mask = (1usize << postfix_bits) - 1;
        let postfix = dist & postfix_mask;
        let prefix = (dist >> bucket) & 1;
        let offset = (2 + prefix) << bucket;
        let nbits = bucket - postfix_bits;
        let code = (nbits << 10)
            | (BROTLI_NUM_DISTANCE_SHORT_CODES
                + num_direct_codes
                + ((2 * (nbits - 1) + prefix) << postfix_bits)
                + postfix);
        let extra_bits = ((dist - offset) >> postfix_bits) as u32;
        (code as u16, extra_bits)
    }
}

#[inline(always)]
pub fn get_length_code(insertlen: usize, copylen: usize, use_last_distance: bool) -> u16 {
    let inscode = get_insert_length_code(insertlen);
    let copycode = get_copy_length_code(copylen);
    combine_length_codes(inscode, copycode, use_last_distance)
}

#[inline(always)]
pub fn get_insert_base(inscode: u16) -> u32 {
    K_BROTLI_INS_BASE[inscode as usize]
}

#[inline(always)]
pub fn get_insert_extra(inscode: u16) -> u32 {
    K_BROTLI_INS_EXTRA[inscode as usize]
}

#[inline(always)]
pub fn get_copy_base(copycode: u16) -> u32 {
    K_BROTLI_COPY_BASE[copycode as usize]
}

#[inline(always)]
pub fn get_copy_extra(copycode: u16) -> u32 {
    K_BROTLI_COPY_EXTRA[copycode as usize]
}

#[derive(Debug, Clone, Copy, Default)]
pub struct Command {
    pub insert_len_: u32,
    pub copy_len_: u32,
    pub dist_extra_: u32,
    pub cmd_prefix_: u16,
    pub dist_prefix_: u16,
}

impl Command {
    #[inline(always)]
    pub fn init(
        &mut self,
        dist: &BrotliDistanceParams,
        insertlen: usize,
        copylen: usize,
        copylen_code_delta: i32,
        distance_code: usize,
    ) {
        let delta = (copylen_code_delta as i8) as u8 as u32;
        self.insert_len_ = insertlen as u32;
        self.copy_len_ = (copylen as u32) | (delta << 25);
        let (dp, de) = prefix_encode_copy_distance(
            distance_code,
            dist.num_direct_distance_codes as usize,
            dist.distance_postfix_bits as usize,
        );
        self.dist_prefix_ = dp;
        self.dist_extra_ = de;
        self.cmd_prefix_ = get_length_code(
            insertlen,
            ((copylen as isize) + (copylen_code_delta as isize)) as usize,
            (self.dist_prefix_ & 0x3FF) == 0,
        );
    }

    #[inline(always)]
    pub fn new(
        dist: &BrotliDistanceParams,
        insertlen: usize,
        copylen: usize,
        copylen_code_delta: i32,
        distance_code: usize,
    ) -> Self {
        let mut c = Self::default();
        c.init(dist, insertlen, copylen, copylen_code_delta, distance_code);
        c
    }

    #[inline(always)]
    pub fn new_insert(insertlen: usize) -> Self {
        Self {
            insert_len_: insertlen as u32,
            copy_len_: 4 << 25,
            dist_extra_: 0,
            dist_prefix_: BROTLI_NUM_DISTANCE_SHORT_CODES as u16,
            cmd_prefix_: get_length_code(insertlen, 4, false),
        }
    }

    #[inline(always)]
    pub fn restore_distance_code(&self, dist: &BrotliDistanceParams) -> u32 {
        let dcode = (self.dist_prefix_ & 0x3FF) as u32;
        if dcode < (BROTLI_NUM_DISTANCE_SHORT_CODES as u32) + dist.num_direct_distance_codes {
            dcode
        } else {
            let nbits = (self.dist_prefix_ >> 10) as u32;
            let extra = self.dist_extra_;
            let postfix_mask = (1u32 << dist.distance_postfix_bits) - 1;
            let base = dist.num_direct_distance_codes + (BROTLI_NUM_DISTANCE_SHORT_CODES as u32);
            let hcode = (dcode - base) >> dist.distance_postfix_bits;
            let lcode = (dcode - base) & postfix_mask;
            let offset = ((2 + (hcode & 1)) << nbits) - 4;
            ((offset + extra) << dist.distance_postfix_bits) + lcode + base
        }
    }

    #[inline(always)]
    pub fn distance_context(&self) -> u32 {
        let r = self.cmd_prefix_ >> 6;
        let c = self.cmd_prefix_ & 7;
        if (r == 0 || r == 2 || r == 4 || r == 7) && c <= 2 {
            c as u32
        } else {
            3
        }
    }

    #[inline(always)]
    pub fn copy_len(&self) -> u32 {
        self.copy_len_ & 0x1FFFFFF
    }

    #[inline(always)]
    pub fn copy_len_code(&self) -> u32 {
        let modifier = self.copy_len_ >> 25;
        let delta = ((modifier | ((modifier & 0x40) << 1)) as u8) as i8 as i32;
        ((self.copy_len_ & 0x1FFFFFF) as i32 + delta) as u32
    }
}
