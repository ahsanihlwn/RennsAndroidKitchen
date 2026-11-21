//! Incremental CRC-16/ARC (polynomial 0xA001 reflected, initial value 0x0000).

const CRC16_ARC_POLY_REVERSED: u16 = 0xA001;

const fn build_table() -> [u16; 256] {
    let mut table = [0u16; 256];
    let mut index = 0;
    while index < 256 {
        let mut value = index as u16;
        let mut bit = 0;
        while bit < 8 {
            value = if value & 1 != 0 {
                (value >> 1) ^ CRC16_ARC_POLY_REVERSED
            } else {
                value >> 1
            };
            bit += 1;
        }
        table[index] = value;
        index += 1;
    }
    table
}

static CRC16_ARC_TABLE: [u16; 256] = build_table();

/// Stateful CRC-16/ARC checksum accumulator.
#[derive(Debug, Clone, Copy, Default)]
pub struct Crc16 {
    state: u16,
}

impl Crc16 {
    /// Creates a new accumulator initialized to `0x0000`.
    pub const fn new() -> Self {
        Self { state: 0 }
    }

    /// Feeds a byte slice into the checksum state.
    pub fn update(&mut self, bytes: &[u8]) {
        let mut crc = self.state;
        for &byte in bytes {
            let table_idx = ((crc ^ u16::from(byte)) & 0xFF) as usize;
            crc = (crc >> 8) ^ CRC16_ARC_TABLE[table_idx];
        }
        self.state = crc;
    }

    /// Returns the accumulated 16-bit checksum.
    pub const fn finish(self) -> u16 {
        self.state
    }
}

/// Computes CRC-16/ARC over a contiguous byte slice in one pass.
pub fn checksum(bytes: &[u8]) -> u16 {
    let mut hasher = Crc16::new();
    hasher.update(bytes);
    hasher.finish()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn known_arc_test_vector() {
        assert_eq!(checksum(b"123456789"), 0xBB3D);
    }

    #[test]
    fn incremental_matches_one_shot() {
        let payload = b"Spreadtrum/Unisoc PAC header & payload integrity check";
        let mut streaming = Crc16::new();
        for chunk in payload.chunks(7) {
            streaming.update(chunk);
        }
        assert_eq!(streaming.finish(), checksum(payload));
    }

    #[test]
    fn empty_input_is_zero() {
        assert_eq!(checksum(&[]), 0x0000);
    }
}
