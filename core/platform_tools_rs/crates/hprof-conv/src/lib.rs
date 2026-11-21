use std::fs::File;
use std::io::{self, BufReader, BufWriter, Read, Write};

use thiserror::Error;

const HEADER_103: &[u8] = b"JAVA PROFILE 1.0.3";
const HEADER_102: &[u8] = b"JAVA PROFILE 1.0.2";

/// Bitmask of top-level HPROF record tags preserved verbatim (`0x01..=0x07`, `0x0a`, `0x0b`,
/// `0x0d`, `0x0e`, `0x2c`). Tags `0x0c` and `0x1c` are heap dump records processed via
/// [`filter_heap_dump`].
const ALLOWED_TOP_LEVEL_MASK: u64 = 0x1000_1000_6cfe;

/// Options controlling HPROF 1.0.3 to 1.0.2 conversion.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ConvertOptions {
    /// When `true`, exclude objects allocated in non-app heaps (`'I'` Image and `'Z'` Zygote).
    pub filter_zygote: bool,
}

/// Errors produced during HPROF conversion.
#[derive(Debug, Error)]
pub enum ConvertError {
    #[error("ERROR: failed reading input")]
    FailedReadingInput,
    #[error("ERROR: HPROF file already in 1.0.2 format.")]
    Already102,
    #[error("ERROR: expecting HPROF file format 1.0.3")]
    Expecting103,
    #[error("ERROR: read {read} of {expected} bytes")]
    ShortRead { read: usize, expected: usize },
    #[error("ERROR: write {written} of {expected} bytes")]
    ShortWrite { written: usize, expected: usize },
    #[error("ERROR: unexpected subtype 0x{tag:02x} at offset {offset}")]
    UnexpectedSubtype { tag: u8, offset: usize },
    #[error("ERROR: invalid basicType {0}")]
    InvalidBasicType(u8),
    #[error("ERROR: truncated subrecord 0x{tag:02x} at offset {offset}")]
    TruncatedSubrecord { tag: u8, offset: usize },
    #[error("ERROR: {0}")]
    Io(#[from] io::Error),
}

fn read_exact_or_eof<R: Read>(reader: &mut R, buf: &mut [u8]) -> Result<usize, ConvertError> {
    let mut total = 0;
    while total < buf.len() {
        match reader.read(&mut buf[total..]) {
            Ok(0) => break,
            Ok(n) => total += n,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(ConvertError::Io(e)),
        }
    }
    Ok(total)
}

fn read_checked<R: Read>(reader: &mut R, buf: &mut [u8]) -> Result<(), ConvertError> {
    let got = read_exact_or_eof(reader, buf)?;
    if got != buf.len() {
        return Err(ConvertError::ShortRead {
            read: got,
            expected: buf.len(),
        });
    }
    Ok(())
}

fn read_header_string<R: Read>(reader: &mut R) -> Result<Vec<u8>, ConvertError> {
    let mut header = Vec::with_capacity(32);
    let mut byte = [0u8; 1];
    loop {
        let got = read_exact_or_eof(reader, &mut byte)?;
        if got == 0 {
            return Err(ConvertError::FailedReadingInput);
        }
        if byte[0] == 0 {
            break;
        }
        header.push(byte[0]);
    }
    Ok(header)
}

fn basic_type_size(basic_type: u8) -> Option<usize> {
    match basic_type {
        2 => Some(4),  // OBJECT
        4 => Some(1),  // BOOLEAN
        5 => Some(2),  // CHAR
        6 => Some(4),  // FLOAT
        7 => Some(8),  // DOUBLE
        8 => Some(1),  // BYTE
        9 => Some(2),  // SHORT
        10 => Some(4), // INT
        11 => Some(8), // LONG
        _ => None,
    }
}

fn read_be_u16(slice: &[u8], off: usize, tag: u8, base_off: usize) -> Result<u16, ConvertError> {
    slice
        .get(off..off + 2)
        .and_then(|s| s.try_into().ok())
        .map(u16::from_be_bytes)
        .ok_or(ConvertError::TruncatedSubrecord {
            tag,
            offset: base_off + off,
        })
}

fn read_be_u32(slice: &[u8], off: usize, tag: u8, base_off: usize) -> Result<u32, ConvertError> {
    slice
        .get(off..off + 4)
        .and_then(|s| s.try_into().ok())
        .map(u32::from_be_bytes)
        .ok_or(ConvertError::TruncatedSubrecord {
            tag,
            offset: base_off + off,
        })
}

/// Filters and normalizes one `HEAP_DUMP` (`0x0c`) or `HEAP_DUMP_SEGMENT` (`0x1c`) record.
fn filter_heap_dump<W: Write>(
    header9: &[u8; 9],
    payload: &[u8],
    filter_zygote: bool,
    writer: &mut W,
) -> Result<(), ConvertError> {
    let mut out = Vec::with_capacity(9 + payload.len());
    out.extend_from_slice(header9);

    let mut pos = 0usize;
    let mut in_zygote_heap = false;

    while pos < payload.len() {
        let subtag = payload[pos];
        let rec_off = 9 + pos;

        match subtag {
            // ROOT_UNKNOWN (0xff), ROOT_THREAD_BLOCK (0x05), ROOT_MONITOR_USED (0x07): 1 + 4 bytes
            0x05 | 0x07 | 0xff => {
                let end = pos + 5;
                let slice = payload
                    .get(pos..end)
                    .ok_or(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    })?;
                out.extend_from_slice(slice);
                pos = end;
            }
            // ROOT_JNI_GLOBAL (0x01), ROOT_STICKY_CLASS (0x04), ROOT_THREAD_OBJECT (0x06): 1 + 8 bytes
            0x01 | 0x04 | 0x06 => {
                let end = pos + 9;
                let slice = payload
                    .get(pos..end)
                    .ok_or(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    })?;
                out.extend_from_slice(slice);
                pos = end;
            }
            // ROOT_JNI_LOCAL (0x02), ROOT_JAVA_FRAME (0x03), ROOT_NATIVE_STACK (0x08): 1 + 12 bytes
            0x02 | 0x03 | 0x08 => {
                let end = pos + 13;
                let slice = payload
                    .get(pos..end)
                    .ok_or(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    })?;
                out.extend_from_slice(slice);
                pos = end;
            }
            // Android root tags mapped to HPROF_GC_ROOT_UNKNOWN (0xff), keeping 4-byte object ID.
            0x89 | 0x8a | 0x8b | 0x8c | 0x8d | 0x90 => {
                let end = pos + 5;
                let slice = payload
                    .get(pos + 1..end)
                    .ok_or(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    })?;
                out.push(0xff);
                out.extend_from_slice(slice);
                pos = end;
            }
            // ROOT_JNI_MONITOR (0x8e): 13 bytes in input, emitted as 5-byte ROOT_UNKNOWN (0xff + obj_id).
            0x8e => {
                let end = pos + 13;
                if payload.len() < end {
                    return Err(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    });
                }
                out.push(0xff);
                out.extend_from_slice(&payload[pos + 1..pos + 5]);
                pos = end;
            }
            // CLASS_DUMP (0x20): always copied verbatim.
            0x20 => {
                let mut cur = pos + 0x25;
                if payload.len() < cur + 2 {
                    return Err(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    });
                }
                let cp_count = read_be_u16(payload, cur, subtag, 9)? as usize;
                cur += 2;
                for _ in 0..cp_count {
                    let bt = *payload.get(cur + 2).ok_or(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: 9 + cur,
                    })?;
                    let sz = basic_type_size(bt).ok_or(ConvertError::InvalidBasicType(bt))?;
                    cur += 3 + sz;
                    if cur > payload.len() {
                        return Err(ConvertError::TruncatedSubrecord {
                            tag: subtag,
                            offset: rec_off,
                        });
                    }
                }

                let static_count = read_be_u16(payload, cur, subtag, 9)? as usize;
                cur += 2;
                for _ in 0..static_count {
                    let bt = *payload.get(cur + 4).ok_or(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: 9 + cur,
                    })?;
                    let sz = basic_type_size(bt).ok_or(ConvertError::InvalidBasicType(bt))?;
                    cur += 5 + sz;
                    if cur > payload.len() {
                        return Err(ConvertError::TruncatedSubrecord {
                            tag: subtag,
                            offset: rec_off,
                        });
                    }
                }

                let instance_count = read_be_u16(payload, cur, subtag, 9)? as usize;
                cur += 2 + instance_count * 5;
                let slice = payload
                    .get(pos..cur)
                    .ok_or(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    })?;
                out.extend_from_slice(slice);
                pos = cur;
            }
            // INSTANCE_DUMP (0x21)
            0x21 => {
                let num_bytes = read_be_u32(payload, pos + 13, subtag, 9)? as usize;
                let end = pos
                    .checked_add(17 + num_bytes)
                    .ok_or(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    })?;
                let slice = payload
                    .get(pos..end)
                    .ok_or(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    })?;
                if !in_zygote_heap {
                    out.extend_from_slice(slice);
                }
                pos = end;
            }
            // OBJECT_ARRAY_DUMP (0x22)
            0x22 => {
                let num_elems = read_be_u32(payload, pos + 9, subtag, 9)? as usize;
                let data_bytes =
                    num_elems
                        .checked_mul(4)
                        .ok_or(ConvertError::TruncatedSubrecord {
                            tag: subtag,
                            offset: rec_off,
                        })?;
                let end = pos
                    .checked_add(17 + data_bytes)
                    .ok_or(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    })?;
                let slice = payload
                    .get(pos..end)
                    .ok_or(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    })?;
                if !in_zygote_heap {
                    out.extend_from_slice(slice);
                }
                pos = end;
            }
            // PRIMITIVE_ARRAY_DUMP (0x23)
            0x23 => {
                let num_elems = read_be_u32(payload, pos + 9, subtag, 9)? as usize;
                let elem_type =
                    *payload
                        .get(pos + 13)
                        .ok_or(ConvertError::TruncatedSubrecord {
                            tag: subtag,
                            offset: rec_off,
                        })?;
                let elem_sz = basic_type_size(elem_type)
                    .ok_or(ConvertError::InvalidBasicType(elem_type))?;
                let data_bytes =
                    num_elems
                        .checked_mul(elem_sz)
                        .ok_or(ConvertError::TruncatedSubrecord {
                            tag: subtag,
                            offset: rec_off,
                        })?;
                let end = pos
                    .checked_add(14 + data_bytes)
                    .ok_or(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    })?;
                let slice = payload
                    .get(pos..end)
                    .ok_or(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    })?;
                if !in_zygote_heap {
                    out.extend_from_slice(slice);
                }
                pos = end;
            }
            // PRIMITIVE_ARRAY_NODATA_DUMP (0xc3) -> PRIMITIVE_ARRAY_DUMP (0x23) with zeroed stack serial
            0xc3 => {
                let end = pos + 14;
                let slice = payload
                    .get(pos..end)
                    .ok_or(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    })?;
                out.push(0x23);
                out.extend_from_slice(&slice[1..5]);
                out.extend_from_slice(&[0u8; 4]);
                out.extend_from_slice(&slice[9..14]);
                pos = end;
            }
            // HEAP_DUMP_INFO (0xfe): strip from output and update active heap filter state
            0xfe => {
                let end = pos + 9;
                if payload.len() < end {
                    return Err(ConvertError::TruncatedSubrecord {
                        tag: subtag,
                        offset: rec_off,
                    });
                }
                let heap_id = read_be_u32(payload, pos + 1, subtag, 9)?;
                in_zygote_heap = filter_zygote && (heap_id == 0x49 || heap_id == 0x5a);
                pos = end;
            }
            other => {
                return Err(ConvertError::UnexpectedSubtype {
                    tag: other,
                    offset: rec_off,
                });
            }
        }
    }

    let out_payload_len = (out.len() - 9) as u32;
    out[5..9].copy_from_slice(&out_payload_len.to_be_bytes());
    writer.write_all(&out)?;
    Ok(())
}

/// Converts an Android HPROF 1.0.3 stream to a standard Java HPROF 1.0.2 stream.
pub fn convert<R: Read, W: Write>(
    reader: &mut R,
    writer: &mut W,
    opts: ConvertOptions,
) -> Result<(), ConvertError> {
    let hdr = read_header_string(reader)?;
    if hdr == HEADER_102 {
        return Err(ConvertError::Already102);
    }
    if hdr != HEADER_103 {
        return Err(ConvertError::Expecting103);
    }

    writer.write_all(HEADER_102)?;
    writer.write_all(&[0])?;

    let mut fixed_fields = [0u8; 12];
    read_checked(reader, &mut fixed_fields)?;
    writer.write_all(&fixed_fields)?;

    let mut tag_buf = [0u8; 1];
    let mut rec_rest = [0u8; 8];

    loop {
        let got = read_exact_or_eof(reader, &mut tag_buf)?;
        if got == 0 {
            break;
        }
        read_checked(reader, &mut rec_rest)?;

        let mut header9 = [0u8; 9];
        header9[0] = tag_buf[0];
        header9[1..9].copy_from_slice(&rec_rest);

        let payload_len = u32::from_be_bytes(rec_rest[4..8].try_into().unwrap()) as usize;
        let mut payload = vec![0u8; payload_len];
        if payload_len > 0 {
            read_checked(reader, &mut payload)?;
        }

        let tag = tag_buf[0];
        if (tag & 0xef) == 0x0c {
            filter_heap_dump(&header9, &payload, opts.filter_zygote, writer)?;
        } else if tag <= 0x2c && ((ALLOWED_TOP_LEVEL_MASK >> tag) & 1) != 0 {
            writer.write_all(&header9)?;
            writer.write_all(&payload)?;
        }
    }

    writer.flush()?;
    Ok(())
}

fn print_usage() {
    eprintln!(
        "\
Usage: hprof-conv [-z] infile outfile

  -z: exclude non-app heaps, such as Zygote

Specify '-' for either or both files to use stdin/stdout.

Copyright (C) 2009 The Android Open Source Project

This software is built from source code licensed under the Apache License,
Version 2.0 (the \"License\"). You may obtain a copy of the License at

     http://www.apache.org/licenses/LICENSE-2.0

See the associated NOTICE file for this software for further details."
    );
}

/// Top-level CLI runner for `hprof-conv` (expects arguments WITHOUT `argv[0]`).
pub fn run_cli(args: &[String]) -> i32 {
    let mut filter_zygote = false;
    let mut pos_start = 0usize;

    while pos_start < args.len() {
        let arg = &args[pos_start];
        if arg == "--" {
            pos_start += 1;
            break;
        }
        if arg == "-" || !arg.starts_with('-') {
            break;
        }
        for ch in arg[1..].chars() {
            if ch == 'z' {
                filter_zygote = true;
            } else {
                eprintln!("hprof-conv: invalid option -- '{ch}'");
                print_usage();
                return 2;
            }
        }
        pos_start += 1;
    }

    let positional = &args[pos_start..];
    if positional.len() != 2 {
        print_usage();
        return 2;
    }

    let infile = &positional[0];
    let outfile = &positional[1];

    let mut reader: Box<dyn Read> = if infile == "-" {
        Box::new(BufReader::new(io::stdin().lock()))
    } else {
        match File::open(infile) {
            Ok(f) => Box::new(BufReader::new(f)),
            Err(e) => {
                eprintln!("ERROR: unable to open '{}': {e}", infile);
                return 1;
            }
        }
    };

    let mut writer: Box<dyn Write> = if outfile == "-" {
        Box::new(BufWriter::new(io::stdout().lock()))
    } else {
        match File::create(outfile) {
            Ok(f) => Box::new(BufWriter::new(f)),
            Err(e) => {
                eprintln!("ERROR: unable to open '{}': {e}", outfile);
                return 1;
            }
        }
    };

    if let Err(err) = convert(&mut reader, &mut writer, ConvertOptions { filter_zygote }) {
        eprintln!("{err}");
        return 1;
    }

    0
}

#[cfg(test)]
mod tests {
    use std::io::Cursor;

    use super::*;

    fn make_rec(tag: u8, time_delta: u32, payload: &[u8]) -> Vec<u8> {
        let mut out = Vec::with_capacity(9 + payload.len());
        out.push(tag);
        out.extend_from_slice(&time_delta.to_be_bytes());
        out.extend_from_slice(&(payload.len() as u32).to_be_bytes());
        out.extend_from_slice(payload);
        out
    }

    fn base_header() -> Vec<u8> {
        let mut v = Vec::new();
        v.extend_from_slice(HEADER_103);
        v.push(0);
        v.extend_from_slice(&4u32.to_be_bytes());
        v.extend_from_slice(&0x0102_0304_0506_0708u64.to_be_bytes());
        v
    }

    #[test]
    fn test_header_errors() {
        let mut out = Vec::new();
        let err = convert(
            &mut Cursor::new(b"JAVA PROFILE 1.0.2\0\0\0\0\x04"),
            &mut out,
            ConvertOptions::default(),
        )
        .unwrap_err();
        assert!(matches!(err, ConvertError::Already102));

        let err2 = convert(
            &mut Cursor::new(b"JAVA PROFILE 1.0.1\0"),
            &mut out,
            ConvertOptions::default(),
        )
        .unwrap_err();
        assert!(matches!(err2, ConvertError::Expecting103));

        let err3 = convert(
            &mut Cursor::new(b"JAVA PROFILE"),
            &mut out,
            ConvertOptions::default(),
        )
        .unwrap_err();
        assert!(matches!(err3, ConvertError::FailedReadingInput));
    }

    #[test]
    fn test_subtags_and_zygote_filtering() {
        let mut input = base_header();
        // Unknown top-level tag 0x25 should be dropped; 0x01 kept.
        input.extend_from_slice(&make_rec(0x25, 1, b"dropme"));
        input.extend_from_slice(&make_rec(0x01, 2, b"keepme"));

        // Heap dump with 0xfe ('Z'), 0x21 (dropped under -z), 0x8e (rewritten to 0xff 5B),
        // 0xc3 (rewritten to 0x23 with zeroed serial), 0xfe ('A'), 0x21 (kept).
        let mut hd = Vec::new();
        // 0xfe heap_id = 'Z' (0x5a)
        hd.push(0xfe);
        hd.extend_from_slice(&0x5au32.to_be_bytes());
        hd.extend_from_slice(&1u32.to_be_bytes());
        // 0x21 instance dump (17 + 3 bytes)
        hd.push(0x21);
        hd.extend_from_slice(&0x1111_1111u32.to_be_bytes());
        hd.extend_from_slice(&2u32.to_be_bytes());
        hd.extend_from_slice(&0x2222_2222u32.to_be_bytes());
        hd.extend_from_slice(&3u32.to_be_bytes());
        hd.extend_from_slice(b"abc");
        // 0x8e JNI monitor (13 bytes)
        hd.push(0x8e);
        hd.extend_from_slice(&0x3333_3333u32.to_be_bytes());
        hd.extend_from_slice(&4u32.to_be_bytes());
        hd.extend_from_slice(&5u32.to_be_bytes());
        // 0xc3 primitive array nodata (14 bytes)
        hd.push(0xc3);
        hd.extend_from_slice(&0x4444_4444u32.to_be_bytes());
        hd.extend_from_slice(&0x9999_9999u32.to_be_bytes());
        hd.extend_from_slice(&7u32.to_be_bytes());
        hd.push(8); // BYTE
        // 0xfe heap_id = 'A' (0x41)
        hd.push(0xfe);
        hd.extend_from_slice(&0x41u32.to_be_bytes());
        hd.extend_from_slice(&6u32.to_be_bytes());
        // 0x21 instance dump in app heap (17 + 2 bytes)
        hd.push(0x21);
        hd.extend_from_slice(&0x5555_5555u32.to_be_bytes());
        hd.extend_from_slice(&7u32.to_be_bytes());
        hd.extend_from_slice(&0x6666_6666u32.to_be_bytes());
        hd.extend_from_slice(&2u32.to_be_bytes());
        hd.extend_from_slice(b"xy");

        input.extend_from_slice(&make_rec(0x0c, 10, &hd));

        let mut out_z = Vec::new();
        convert(
            &mut Cursor::new(&input),
            &mut out_z,
            ConvertOptions {
                filter_zygote: true,
            },
        )
        .unwrap();

        assert!(out_z.starts_with(b"JAVA PROFILE 1.0.2\0"));
        let mut expected = Vec::new();
        expected.extend_from_slice(b"JAVA PROFILE 1.0.2\0");
        expected.extend_from_slice(&4u32.to_be_bytes());
        expected.extend_from_slice(&0x0102_0304_0506_0708u64.to_be_bytes());
        expected.extend_from_slice(&make_rec(0x01, 2, b"keepme"));

        let mut hd_exp = Vec::new();
        hd_exp.push(0xff);
        hd_exp.extend_from_slice(&0x3333_3333u32.to_be_bytes());
        hd_exp.push(0x23);
        hd_exp.extend_from_slice(&0x4444_4444u32.to_be_bytes());
        hd_exp.extend_from_slice(&0u32.to_be_bytes());
        hd_exp.extend_from_slice(&7u32.to_be_bytes());
        hd_exp.push(8);
        hd_exp.push(0x21);
        hd_exp.extend_from_slice(&0x5555_5555u32.to_be_bytes());
        hd_exp.extend_from_slice(&7u32.to_be_bytes());
        hd_exp.extend_from_slice(&0x6666_6666u32.to_be_bytes());
        hd_exp.extend_from_slice(&2u32.to_be_bytes());
        hd_exp.extend_from_slice(b"xy");
        expected.extend_from_slice(&make_rec(0x0c, 10, &hd_exp));

        assert_eq!(out_z, expected);
    }

    #[test]
    fn test_unexpected_subtype_offset() {
        let mut input = base_header();
        input.extend_from_slice(&make_rec(0x0c, 0, &[0x99]));
        let mut out = Vec::new();
        let err = convert(&mut Cursor::new(&input), &mut out, ConvertOptions::default()).unwrap_err();
        assert!(matches!(
            err,
            ConvertError::UnexpectedSubtype {
                tag: 0x99,
                offset: 9
            }
        ));
    }
}
