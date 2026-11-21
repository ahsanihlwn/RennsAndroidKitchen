#![allow(dead_code)]

use crate::enc::params::BrotliEncoderParams;
use crate::enc::quality::compute_rb_bits;

const K_SLACK_FOR_EIGHT_BYTE_HASHING_EVERYWHERE: usize = 7;
/// In Rust, `data_` holds 2 prefix slack bytes at indices 0..2, so `buffer_[0]` is `data_[2]`.
pub const RINGBUFFER_OFFSET: usize = 2;

#[derive(Debug, Clone)]
pub struct RingBuffer {
    pub size_: u32,
    pub mask_: u32,
    pub tail_size_: u32,
    pub total_size_: u32,
    pub cur_size_: u32,
    pub pos_: u32,
    pub data_: Vec<u8>,
}

impl Default for RingBuffer {
    fn default() -> Self {
        Self::new()
    }
}

impl RingBuffer {
    pub fn new() -> Self {
        Self {
            size_: 0,
            mask_: 0,
            tail_size_: 0,
            total_size_: 0,
            cur_size_: 0,
            pos_: 0,
            data_: Vec::new(),
        }
    }

    pub fn setup(&mut self, params: &BrotliEncoderParams) {
        let window_bits = compute_rb_bits(params);
        let tail_bits = params.lgblock;
        self.size_ = 1u32 << window_bits;
        self.mask_ = self.size_ - 1;
        self.tail_size_ = 1u32 << tail_bits;
        self.total_size_ = self.size_ + self.tail_size_;
    }

    /// Returns slice starting at logical `buffer_[0]` (i.e. `data_[2..]`).
    #[inline(always)]
    pub fn buffer(&self) -> &[u8] {
        if self.data_.is_empty() {
            &[]
        } else {
            &self.data_[RINGBUFFER_OFFSET..]
        }
    }

    /// Returns mutable slice starting at logical `buffer_[0]` (i.e. `data_[2..]`).
    #[inline(always)]
    pub fn buffer_mut(&mut self) -> &mut [u8] {
        if self.data_.is_empty() {
            &mut []
        } else {
            &mut self.data_[RINGBUFFER_OFFSET..]
        }
    }

    /// Returns slice starting at logical `buffer_[-2]`.
    #[inline(always)]
    pub fn data(&self) -> &[u8] {
        &self.data_
    }

    fn init_buffer(&mut self, buflen: u32) {
        let new_len = RINGBUFFER_OFFSET + (buflen as usize) + K_SLACK_FOR_EIGHT_BYTE_HASHING_EVERYWHERE;
        let mut new_data = vec![0u8; new_len];
        if !self.data_.is_empty() {
            let copy_len = RINGBUFFER_OFFSET
                + (self.cur_size_ as usize)
                + K_SLACK_FOR_EIGHT_BYTE_HASHING_EVERYWHERE;
            let n = copy_len.min(new_data.len()).min(self.data_.len());
            new_data[..n].copy_from_slice(&self.data_[..n]);
        }
        self.data_ = new_data;
        self.cur_size_ = buflen;
        self.data_[0] = 0;
        self.data_[1] = 0;
        let tail_start = RINGBUFFER_OFFSET + (self.cur_size_ as usize);
        for i in 0..K_SLACK_FOR_EIGHT_BYTE_HASHING_EVERYWHERE {
            self.data_[tail_start + i] = 0;
        }
    }

    fn write_tail(&mut self, bytes: &[u8]) {
        let masked_pos = (self.pos_ & self.mask_) as usize;
        if masked_pos < self.tail_size_ as usize {
            let p = (self.size_ as usize) + masked_pos;
            let count = bytes.len().min((self.tail_size_ as usize) - masked_pos);
            let buf = self.buffer_mut();
            buf[p..p + count].copy_from_slice(&bytes[..count]);
        }
    }

    pub fn write(&mut self, bytes: &[u8]) {
        let n = bytes.len();
        if self.pos_ == 0 && n < self.tail_size_ as usize {
            self.pos_ = n as u32;
            self.init_buffer(self.pos_);
            let buf = self.buffer_mut();
            buf[..n].copy_from_slice(bytes);
            return;
        }
        if self.cur_size_ < self.total_size_ {
            self.init_buffer(self.total_size_);
            let size = self.size_ as usize;
            let pos = self.pos_ as usize;
            let tail_size = self.tail_size_ as usize;
            let buf = self.buffer_mut();
            buf[size - 2] = 0;
            buf[size - 1] = 0;
            let copy_len = pos.min(tail_size);
            buf.copy_within(0..copy_len, size);
        }
        {
            let masked_pos = (self.pos_ & self.mask_) as usize;
            let size = self.size_ as usize;
            let total_size = self.total_size_ as usize;
            self.write_tail(bytes);
            let buf = self.buffer_mut();
            if masked_pos + n <= size {
                buf[masked_pos..masked_pos + n].copy_from_slice(bytes);
            } else {
                let first = n.min(total_size - masked_pos);
                buf[masked_pos..masked_pos + first].copy_from_slice(&bytes[..first]);
                let rem = n - (size - masked_pos);
                buf[..rem].copy_from_slice(&bytes[size - masked_pos..size - masked_pos + rem]);
            }
        }
        {
            let not_first_lap = (self.pos_ & (1u32 << 31)) != 0;
            let rb_pos_mask = (1u32 << 31) - 1;
            let size = self.size_ as usize;
            let b_m2 = self.data_[RINGBUFFER_OFFSET + size - 2];
            let b_m1 = self.data_[RINGBUFFER_OFFSET + size - 1];
            self.data_[0] = b_m2;
            self.data_[1] = b_m1;
            self.pos_ = (self.pos_ & rb_pos_mask).wrapping_add((n as u32) & rb_pos_mask);
            if not_first_lap {
                self.pos_ |= 1u32 << 31;
            }
        }
    }
}
