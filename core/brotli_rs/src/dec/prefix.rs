//! Lookup tables to map command prefix codes to value ranges.

use crate::common::BROTLI_NUM_COMMAND_SYMBOLS;

#[derive(Debug, Clone, Copy)]
pub struct CmdLutElement {
    pub insert_len_extra_bits: u8,
    pub copy_len_extra_bits: u8,
    pub distance_code: i8,
    pub context: u8,
    pub insert_len_offset: u16,
    pub copy_len_offset: u16,
}

const fn init_cmd_lut() -> [CmdLutElement; BROTLI_NUM_COMMAND_SYMBOLS] {
    const INSERT_LENGTH_EXTRA_BITS: [u8; 24] = [
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01, 0x02, 0x02, 0x03, 0x03,
        0x04, 0x04, 0x05, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0C, 0x0E, 0x18,
    ];
    const COPY_LENGTH_EXTRA_BITS: [u8; 24] = [
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x01, 0x02, 0x02,
        0x03, 0x03, 0x04, 0x04, 0x05, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x18,
    ];
    const CELL_POS: [u8; 11] = [0, 1, 0, 1, 8, 9, 2, 16, 10, 17, 18];

    let mut insert_length_offsets = [0u16; 24];
    let mut copy_length_offsets = [0u16; 24];
    insert_length_offsets[0] = 0;
    copy_length_offsets[0] = 2;
    let mut i = 0;
    while i < 23 {
        insert_length_offsets[i + 1] =
            insert_length_offsets[i] + (1u16 << INSERT_LENGTH_EXTRA_BITS[i]);
        copy_length_offsets[i + 1] =
            copy_length_offsets[i] + (1u16 << COPY_LENGTH_EXTRA_BITS[i]);
        i += 1;
    }

    let mut items = [CmdLutElement {
        insert_len_extra_bits: 0,
        copy_len_extra_bits: 0,
        distance_code: 0,
        context: 0,
        insert_len_offset: 0,
        copy_len_offset: 0,
    }; BROTLI_NUM_COMMAND_SYMBOLS];

    let mut symbol = 0;
    while symbol < BROTLI_NUM_COMMAND_SYMBOLS {
        let cell_idx = symbol >> 6;
        let cell_pos = CELL_POS[cell_idx] as usize;
        let copy_code = ((cell_pos << 3) & 0x18) + (symbol & 0x7);
        let copy_len_offset = copy_length_offsets[copy_code];
        let insert_code = (cell_pos & 0x18) + ((symbol >> 3) & 0x7);
        items[symbol] = CmdLutElement {
            insert_len_extra_bits: INSERT_LENGTH_EXTRA_BITS[insert_code],
            copy_len_extra_bits: COPY_LENGTH_EXTRA_BITS[copy_code],
            distance_code: if cell_idx >= 2 { -1 } else { 0 },
            context: if copy_len_offset > 4 {
                3
            } else {
                (copy_len_offset - 2) as u8
            },
            insert_len_offset: insert_length_offsets[insert_code],
            copy_len_offset,
        };
        symbol += 1;
    }
    items
}

pub static K_CMD_LUT: [CmdLutElement; BROTLI_NUM_COMMAND_SYMBOLS] = init_cmd_lut();
