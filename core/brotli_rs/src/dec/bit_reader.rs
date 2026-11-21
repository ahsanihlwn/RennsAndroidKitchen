//! Bit reading helpers.

use crate::common::BROTLI_LARGE_MAX_WBITS;

pub const BROTLI_SHORT_FILL_BIT_WINDOW_READ: usize = 4; // sizeof(u64) >> 1
pub const BROTLI_FAST_INPUT_SLACK: usize = 28; // 162 bits + 7 bytes

pub static K_BROTLI_BIT_MASK: [u64; 33] = [
    0x00000000, 0x00000001, 0x00000003, 0x00000007, 0x0000000F, 0x0000001F, 0x0000003F,
    0x0000007F, 0x000000FF, 0x000001FF, 0x000003FF, 0x000007FF, 0x00000FFF, 0x00001FFF,
    0x00003FFF, 0x00007FFF, 0x0000FFFF, 0x0001FFFF, 0x0003FFFF, 0x0007FFFF, 0x000FFFFF,
    0x001FFFFF, 0x003FFFFF, 0x007FFFFF, 0x00FFFFFF, 0x01FFFFFF, 0x03FFFFFF, 0x07FFFFFF,
    0x0FFFFFFF, 0x1FFFFFFF, 0x3FFFFFFF, 0x7FFFFFFF, 0xFFFFFFFF,
];

#[inline(always)]
pub fn bit_mask(n: u32) -> u64 {
    K_BROTLI_BIT_MASK[n as usize]
}

#[derive(Debug, Clone, Copy, Default)]
pub struct BrotliBitReader {
    pub val: u64,
    pub bit_pos: u32,
    pub next_in: usize,
    pub guard_in: usize,
    pub last_in: usize,
}

#[derive(Debug, Clone, Copy)]
pub struct BrotliBitReaderState {
    pub val: u64,
    pub bit_pos: u32,
    pub next_in: usize,
    pub avail_in: usize,
}

impl BrotliBitReader {
    #[inline]
    pub fn init(&mut self) {
        self.val = 0;
        self.bit_pos = 0;
        self.next_in = 0;
        self.guard_in = 0;
        self.last_in = 0;
    }

    #[inline(always)]
    pub fn get_avail_in(&self) -> usize {
        self.last_in - self.next_in
    }

    #[inline]
    pub fn save_state(&self) -> BrotliBitReaderState {
        BrotliBitReaderState {
            val: self.val,
            bit_pos: self.bit_pos,
            next_in: self.next_in,
            avail_in: self.get_avail_in(),
        }
    }

    #[inline]
    pub fn set_input(&mut self, offset: usize, avail_in: usize) {
        self.next_in = offset;
        self.last_in = offset + avail_in;
        if avail_in + 1 > BROTLI_FAST_INPUT_SLACK {
            self.guard_in = offset + (avail_in + 1 - BROTLI_FAST_INPUT_SLACK);
        } else {
            self.guard_in = offset;
        }
    }

    #[inline]
    pub fn restore_state(&mut self, from: &BrotliBitReaderState) {
        self.val = from.val;
        self.bit_pos = from.bit_pos;
        self.set_input(from.next_in, from.avail_in);
    }

    #[inline(always)]
    pub fn get_available_bits(&self) -> u32 {
        self.bit_pos
    }

    #[inline]
    pub fn get_remaining_bytes(&self) -> usize {
        const CAP: usize = 1usize << BROTLI_LARGE_MAX_WBITS;
        let avail_in = self.get_avail_in();
        if avail_in > CAP {
            return CAP;
        }
        avail_in + ((self.get_available_bits() >> 3) as usize)
    }

    #[inline(always)]
    pub fn check_input_amount(&self) -> bool {
        self.next_in < self.guard_in
    }

    #[inline(always)]
    pub fn fill_bit_window(&mut self, input: &[u8], n_bits: u32) {
        if n_bits <= 8 {
            if self.bit_pos <= 8 {
                let bytes = &input[self.next_in..self.next_in + 8];
                let new_bits = u64::from_le_bytes(bytes.try_into().unwrap());
                self.val |= new_bits << self.bit_pos;
                self.bit_pos += 56;
                self.next_in += 7;
            }
        } else if n_bits <= 16 {
            if self.bit_pos <= 16 {
                let bytes = &input[self.next_in..self.next_in + 8];
                let new_bits = u64::from_le_bytes(bytes.try_into().unwrap());
                self.val |= new_bits << self.bit_pos;
                self.bit_pos += 48;
                self.next_in += 6;
            }
        } else if self.bit_pos <= 32 {
            let bytes = &input[self.next_in..self.next_in + 4];
            let new_bits = u32::from_le_bytes(bytes.try_into().unwrap()) as u64;
            self.val |= new_bits << self.bit_pos;
            self.bit_pos += 32;
            self.next_in += BROTLI_SHORT_FILL_BIT_WINDOW_READ;
        }
    }

    #[inline(always)]
    pub fn fill_bit_window_16(&mut self, input: &[u8]) {
        self.fill_bit_window(input, 17);
    }

    #[inline(always)]
    pub fn pull_byte(&mut self, input: &[u8]) -> bool {
        if self.next_in == self.last_in {
            return false;
        }
        self.val |= (input[self.next_in] as u64) << self.bit_pos;
        self.bit_pos += 8;
        self.next_in += 1;
        true
    }

    #[inline]
    pub fn warmup(&mut self, input: &[u8]) -> bool {
        if self.get_available_bits() == 0 {
            self.val = 0;
            if !self.pull_byte(input) {
                return false;
            }
        }
        true
    }

    #[inline(always)]
    pub fn get_bits_unmasked(&self) -> u64 {
        self.val
    }

    #[inline(always)]
    pub fn get_16_bits_unmasked(&mut self, input: &[u8]) -> u64 {
        self.fill_bit_window(input, 16);
        self.val
    }

    #[inline(always)]
    pub fn get_bits(&mut self, input: &[u8], n_bits: u32) -> u64 {
        self.fill_bit_window(input, n_bits);
        self.val & bit_mask(n_bits)
    }

    #[inline(always)]
    pub fn safe_get_bits(&mut self, input: &[u8], n_bits: u32, val: &mut u64) -> bool {
        while self.get_available_bits() < n_bits {
            if !self.pull_byte(input) {
                return false;
            }
        }
        *val = self.val & bit_mask(n_bits);
        true
    }

    #[inline(always)]
    pub fn drop_bits(&mut self, n_bits: u32) {
        self.bit_pos -= n_bits;
        self.val >>= n_bits;
    }

    #[inline(always)]
    pub fn normalize(&mut self) {
        if self.bit_pos < 64 {
            self.val &= (1u64 << self.bit_pos) - 1;
        }
    }

    #[inline]
    pub fn unload(&mut self) {
        let unused_bytes = (self.get_available_bits() >> 3) as usize;
        let unused_bits = (unused_bytes << 3) as u32;
        if unused_bytes != 0 {
            self.next_in -= unused_bytes;
        }
        self.bit_pos -= unused_bits;
        self.normalize();
    }

    #[inline(always)]
    pub fn take_bits(&mut self, n_bits: u32) -> u64 {
        let v = self.val & bit_mask(n_bits);
        self.drop_bits(n_bits);
        v
    }

    #[inline(always)]
    pub fn read_bits_24(&mut self, input: &[u8], n_bits: u32) -> u64 {
        debug_assert!(n_bits <= 24);
        self.fill_bit_window(input, n_bits);
        self.take_bits(n_bits)
    }

    #[inline(always)]
    pub fn read_bits_32(&mut self, input: &[u8], n_bits: u32) -> u64 {
        debug_assert!(n_bits <= 32);
        self.fill_bit_window(input, n_bits);
        self.take_bits(n_bits)
    }

    #[inline(always)]
    pub fn safe_read_bits(&mut self, input: &[u8], n_bits: u32, val: &mut u64) -> bool {
        debug_assert!(n_bits <= 24);
        while self.get_available_bits() < n_bits {
            if !self.pull_byte(input) {
                return false;
            }
        }
        *val = self.take_bits(n_bits);
        true
    }

    #[inline(always)]
    pub fn safe_read_bits_32(&mut self, input: &[u8], n_bits: u32, val: &mut u64) -> bool {
        debug_assert!(n_bits <= 32);
        while self.get_available_bits() < n_bits {
            if !self.pull_byte(input) {
                return false;
            }
        }
        *val = self.take_bits(n_bits);
        true
    }

    #[inline]
    pub fn jump_to_byte_boundary(&mut self) -> bool {
        let pad_bits_count = self.get_available_bits() & 0x7;
        let pad_bits = if pad_bits_count != 0 {
            self.take_bits(pad_bits_count)
        } else {
            0
        };
        self.normalize();
        pad_bits == 0
    }

    #[inline]
    pub fn drop_bytes(&mut self, num: usize) {
        debug_assert!(self.bit_pos == 0);
        debug_assert!(self.val == 0);
        self.next_in += num;
    }

    #[inline]
    pub fn copy_bytes(&mut self, input: &[u8], dest: &mut [u8]) {
        let mut idx = 0usize;
        let mut rem = dest.len();
        while self.get_available_bits() >= 8 && rem > 0 {
            dest[idx] = self.val as u8;
            self.drop_bits(8);
            idx += 1;
            rem -= 1;
        }
        self.normalize();
        if rem > 0 {
            dest[idx..idx + rem].copy_from_slice(&input[self.next_in..self.next_in + rem]);
            self.drop_bytes(rem);
        }
    }
}
