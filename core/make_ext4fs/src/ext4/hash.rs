/// SHA-1, UUIDv5, and ext4 CRC-16 helpers.

pub struct Sha1 {
    state: [u32; 5],
    count: u64,
    buffer: [u8; 64],
}

impl Sha1 {
    pub fn new() -> Self {
        Self {
            state: [0x6745_2301, 0xEFCD_AB89, 0x98BA_DCFE, 0x1032_5476, 0xC3D2_E1F0],
            count: 0,
            buffer: [0u8; 64],
        }
    }

    pub fn update(&mut self, data: &[u8]) {
        let mut idx = (self.count & 63) as usize;
        self.count += data.len() as u64;

        let mut offset = 0;
        if idx > 0 {
            let take = (64 - idx).min(data.len());
            self.buffer[idx..idx + take].copy_from_slice(&data[..take]);
            idx += take;
            offset += take;
            if idx == 64 {
                let block = self.buffer;
                Self::transform(&mut self.state, &block);
                idx = 0;
            }
        }

        while offset + 64 <= data.len() {
            Self::transform(&mut self.state, &data[offset..offset + 64]);
            offset += 64;
        }

        if offset < data.len() {
            let rem = data.len() - offset;
            self.buffer[idx..idx + rem].copy_from_slice(&data[offset..]);
        }
    }

    pub fn finalize(mut self) -> [u8; 20] {
        let total_bits = self.count * 8;
        self.update(&[0x80]);
        let pad_len = (56usize.wrapping_sub((self.count & 63) as usize)) & 63;
        if pad_len > 0 {
            let zeros = [0u8; 64];
            self.update(&zeros[..pad_len]);
        }
        self.update(&total_bits.to_be_bytes());

        let mut out = [0u8; 20];
        for (i, word) in self.state.iter().enumerate() {
            out[i * 4..i * 4 + 4].copy_from_slice(&word.to_be_bytes());
        }
        out
    }

    #[inline]
    fn transform(state: &mut [u32; 5], block: &[u8]) {
        let mut w = [0u32; 80];
        for i in 0..16 {
            w[i] = u32::from_be_bytes([
                block[i * 4],
                block[i * 4 + 1],
                block[i * 4 + 2],
                block[i * 4 + 3],
            ]);
        }
        for i in 16..80 {
            w[i] = (w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16]).rotate_left(1);
        }

        let [mut a, mut b, mut c, mut d, mut e] = *state;
        for (i, &wi) in w.iter().enumerate() {
            let (f, k) = match i {
                0..=19 => ((b & c) | ((!b) & d), 0x5A82_7999u32),
                20..=39 => (b ^ c ^ d, 0x6ED9_EBA1u32),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8F1B_BCDCu32),
                _ => (b ^ c ^ d, 0xCA62_C1D6u32),
            };
            let temp = a
                .rotate_left(5)
                .wrapping_add(f)
                .wrapping_add(e)
                .wrapping_add(k)
                .wrapping_add(wi);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = temp;
        }

        state[0] = state[0].wrapping_add(a);
        state[1] = state[1].wrapping_add(b);
        state[2] = state[2].wrapping_add(c);
        state[3] = state[3].wrapping_add(d);
        state[4] = state[4].wrapping_add(e);
    }
}

/// Generates UUIDv5 from namespace and name with little-endian time fields.
pub fn generate_uuid(namespace: &str, name: &str) -> [u8; 16] {
    let mut sha1 = Sha1::new();
    sha1.update(namespace.as_bytes());
    sha1.update(name.as_bytes());
    let digest = sha1.finalize();

    let mut uuid = [0u8; 16];
    uuid.copy_from_slice(&digest[..16]);

    // Convert RFC-4122 big-endian time fields to little-endian host layout
    let time_low = u32::from_be_bytes([uuid[0], uuid[1], uuid[2], uuid[3]]);
    uuid[0..4].copy_from_slice(&time_low.to_le_bytes());

    // time_mid = ntohs(time_mid)
    let time_mid = u16::from_be_bytes([uuid[4], uuid[5]]);
    uuid[4..6].copy_from_slice(&time_mid.to_le_bytes());

    // time_hi_and_version = (ntohs(time_hi_and_version) & 0x0FFF) | (5 << 12)
    let mut time_hi = u16::from_be_bytes([uuid[6], uuid[7]]);
    time_hi = (time_hi & 0x0FFF) | (5 << 12);
    uuid[6..8].copy_from_slice(&time_hi.to_le_bytes());

    // clk_seq_hi_res
    uuid[8] = (uuid[8] & !(1 << 6)) | (1 << 7);

    uuid
}

const CRC16_TAB: [u16; 256] = [
    0x0000, 0xC0C1, 0xC181, 0x0140, 0xC301, 0x03C0, 0x0280, 0xC241, 0xC601, 0x06C0, 0x0780, 0xC741,
    0x0500, 0xC5C1, 0xC481, 0x0440, 0xCC01, 0x0CC0, 0x0D80, 0xCD41, 0x0F00, 0xCFC1, 0xCE81, 0x0E40,
    0x0A00, 0xCAC1, 0xCB81, 0x0B40, 0xC901, 0x09C0, 0x0880, 0xC841, 0xD801, 0x18C0, 0x1980, 0xD941,
    0x1B00, 0xDBC1, 0xDA81, 0x1A40, 0x1E00, 0xDEC1, 0xDF81, 0x1F40, 0xDD01, 0x1DC0, 0x1C80, 0xDC41,
    0x1400, 0xD4C1, 0xD581, 0x1540, 0xD701, 0x17C0, 0x1680, 0xD641, 0xD201, 0x12C0, 0x1380, 0xD341,
    0x1100, 0xD1C1, 0xD081, 0x1040, 0xF001, 0x30C0, 0x3180, 0xF141, 0x3300, 0xF3C1, 0xF281, 0x3240,
    0x3600, 0xF6C1, 0xF781, 0x3740, 0xF501, 0x35C0, 0x3480, 0xF441, 0x3C00, 0xFCC1, 0xFD81, 0x3D40,
    0xFF01, 0x3FC0, 0x3E80, 0xFE41, 0xFA01, 0x3AC0, 0x3B80, 0xFB41, 0x3900, 0xF9C1, 0xF881, 0x3840,
    0x2800, 0xE8C1, 0xE981, 0x2940, 0xEB01, 0x2BC0, 0x2A80, 0xEA41, 0xEE01, 0x2EC0, 0x2F80, 0xEF41,
    0x2D00, 0xEDC1, 0xEC81, 0x2C40, 0xE401, 0x24C0, 0x2580, 0xE541, 0x2700, 0xE7C1, 0xE681, 0x2640,
    0x2200, 0xE2C1, 0xE381, 0x2340, 0xE101, 0x21C0, 0x2080, 0xE041, 0xA001, 0x60C0, 0x6180, 0xA141,
    0x6300, 0xA3C1, 0xA281, 0x6240, 0x6600, 0xA6C1, 0xA781, 0x6740, 0xA501, 0x65C0, 0x6480, 0xA441,
    0x6C00, 0xACC1, 0xAD81, 0x6D40, 0xAF01, 0x6FC0, 0x6E80, 0xAE41, 0xAA01, 0x6AC0, 0x6B80, 0xAB41,
    0x6900, 0xA9C1, 0xA881, 0x6840, 0x7800, 0xB8C1, 0xB981, 0x7940, 0xBB01, 0x7BC0, 0x7A80, 0xBA41,
    0xBE01, 0x7EC0, 0x7F80, 0xBF41, 0x7D00, 0xBDC1, 0xBC81, 0x7C40, 0xB401, 0x74C0, 0x7580, 0xB541,
    0x7700, 0xB7C1, 0xB681, 0x7640, 0x7200, 0xB2C1, 0xB381, 0x7340, 0xB101, 0x71C0, 0x7080, 0xB041,
    0x5000, 0x90C1, 0x9181, 0x5140, 0x9301, 0x53C0, 0x5280, 0x9241, 0x9601, 0x56C0, 0x5780, 0x9741,
    0x5500, 0x95C1, 0x9481, 0x5440, 0x9C01, 0x5CC0, 0x5D80, 0x9D41, 0x5F00, 0x9FC1, 0x9E81, 0x5E40,
    0x5A00, 0x9AC1, 0x9B81, 0x5B40, 0x9901, 0x59C0, 0x5880, 0x9841, 0x8801, 0x48C0, 0x4980, 0x8941,
    0x4B00, 0x8BC1, 0x8A81, 0x4A40, 0x4E00, 0x8EC1, 0x8F81, 0x4F40, 0x8D01, 0x4DC0, 0x4C80, 0x8C41,
    0x4400, 0x84C1, 0x8581, 0x4540, 0x8701, 0x47C0, 0x4680, 0x8641, 0x8201, 0x42C0, 0x4380, 0x8341,
    0x4100, 0x81C1, 0x8081, 0x4040,
];

#[inline]
pub fn ext4_crc16(mut crc: u16, buf: &[u8]) -> u16 {
    for &b in buf {
        crc = CRC16_TAB[((crc ^ b as u16) & 0xFF) as usize] ^ (crc >> 8);
    }
    crc
}
