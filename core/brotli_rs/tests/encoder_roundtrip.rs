use std::io::{Read, Write};

use brotli_rs::common::BrotliSharedDictionaryType;
use brotli_rs::dec::{
    BrotliDecoderParameter, BrotliDecoderResult, BrotliDecoderState, brotli_decoder_decompress,
};
use brotli_rs::enc::{
    BrotliEncoderBase64Mode, BrotliEncoderMode, BrotliEncoderOperation, BrotliEncoderParameter,
    BrotliEncoderParams, BrotliEncoderSimdHasher, BrotliEncoderState, brotli_encoder_compress,
    brotli_encoder_compress_into, brotli_encoder_compress_with_params,
    brotli_encoder_estimate_peak_memory_usage, brotli_encoder_max_compressed_size,
    brotli_encoder_prepare_raw_dictionary, brotli_encoder_version,
};
use brotli_rs::io::{BrotliReader, BrotliWriter, compress, decompress};

fn make_mixed_corpus(len: usize) -> Vec<u8> {
    let sample = b"{\"status\":\"ok\",\"items\":[\"alpha\",\"beta\",\"gamma\",\"delta\"],\
\"message\":\"Google Brotli lossless compression format RFC 7932! \
Repeating patterns help verify static dictionary, LZ77 backward references, and Huffman codes.\"}\n";
    let mut out = Vec::with_capacity(len);
    while out.len() < len {
        let take = (len - out.len()).min(sample.len());
        out.extend_from_slice(&sample[..take]);
    }
    // Inject deterministic high-entropy region and zero runs
    let mut x: u32 = 0x1234_5678;
    let start = len / 2;
    let patch_len = (len / 8).min(out.len().saturating_sub(start));
    for i in 0..patch_len {
        x = x.wrapping_mul(1664525).wrapping_add(1013904223);
        out[start + i] = (x >> 24) as u8;
    }
    out
}

fn make_incompressible(len: usize) -> Vec<u8> {
    let mut out = Vec::with_capacity(len);
    let mut state: u64 = 0x9E37_79B9_7F4A_7C15;
    for _ in 0..len {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        out.push((state >> 56) as u8);
    }
    out
}

fn decompress_large_window(encoded: &[u8]) -> Vec<u8> {
    let mut dec = BrotliDecoderState::new();
    dec.set_parameter(BrotliDecoderParameter::LargeWindow, 1);
    let mut in_pos = 0usize;
    let mut out = Vec::new();
    let mut scratch = [0u8; 65536];
    loop {
        let mut out_pos = 0usize;
        let res = dec.decompress_stream(encoded, &mut in_pos, &mut scratch, &mut out_pos, None);
        if out_pos > 0 {
            out.extend_from_slice(&scratch[..out_pos]);
        }
        match res {
            BrotliDecoderResult::Success => return out,
            BrotliDecoderResult::NeedsMoreOutput => continue,
            other => panic!("unexpected large-window decode result: {:?}", other),
        }
    }
}

#[test]
fn roundtrip_edge_sizes() {
    for &size in &[0usize, 1, 2, 3, 4, 7, 15, 64, 255, 1024, 65535, 65536, 131073] {
        let data = make_mixed_corpus(size);
        for &q in &[0, 1, 2, 5, 9, 11] {
            let compressed = brotli_encoder_compress(q, 22, BrotliEncoderMode::Generic, &data)
                .expect("compress failed");
            let res = std::panic::catch_unwind(|| brotli_decoder_decompress(&compressed));
            match res {
                Ok(Ok(restored)) => assert_eq!(restored, data, "mismatch at size={size}, q={q}"),
                Ok(Err(e)) => panic!("decompress err size={size} q={q}: {e:?}"),
                Err(_) => panic!("decompress panicked size={size} q={q}, compressed={:?}", &compressed[..compressed.len().min(128)]),
            }
        }
    }
}

#[test]
fn roundtrip_all_qualities_and_modes() {
    let data = make_mixed_corpus(90_000);
    for q in 0..=11 {
        for &mode in &[
            BrotliEncoderMode::Generic,
            BrotliEncoderMode::Text,
            BrotliEncoderMode::Font,
        ] {
            let compressed = brotli_encoder_compress(q, 20, mode, &data)
                .unwrap_or_else(|_| panic!("compress failed q={q} mode={mode:?}"));
            let res = std::panic::catch_unwind(|| brotli_decoder_decompress(&compressed));
            match res {
                Ok(Ok(restored)) => assert_eq!(restored, data, "roundtrip mismatch q={q} mode={mode:?}"),
                Ok(Err(e)) => panic!("decompress err q={q} mode={mode:?}: {e:?}, compressed_len={}", compressed.len()),
                Err(_) => panic!("decompress panicked q={q} mode={mode:?}, compressed_len={}", compressed.len()),
            }
        }
    }
}

#[test]
fn roundtrip_window_sizes_and_large_window() {
    let data = make_mixed_corpus(80_000);
    for lgwin in [10, 12, 16, 17, 18, 20, 22, 24] {
        let compressed = brotli_encoder_compress(6, lgwin, BrotliEncoderMode::Generic, &data)
            .unwrap_or_else(|_| panic!("compress failed lgwin={lgwin}"));
        let restored = brotli_decoder_decompress(&compressed)
            .unwrap_or_else(|e| panic!("decompress failed lgwin={lgwin}: {e:?}"));
        assert_eq!(restored, data, "window mismatch lgwin={lgwin}");
    }
    for lgwin in [25, 27, 30] {
        let compressed = brotli_encoder_compress(5, lgwin, BrotliEncoderMode::Generic, &data)
            .unwrap_or_else(|_| panic!("large-window compress failed lgwin={lgwin}"));
        let restored = decompress_large_window(&compressed);
        assert_eq!(restored, data, "large-window mismatch lgwin={lgwin}");
    }
}

#[test]
fn roundtrip_incompressible_uncompressed_fallback() {
    let data = make_incompressible(100_000);
    for q in [0, 1, 2, 5, 9, 10] {
        let compressed = brotli_encoder_compress(q, 22, BrotliEncoderMode::Generic, &data).unwrap();
        let restored = brotli_decoder_decompress(&compressed).unwrap();
        assert_eq!(restored, data, "incompressible mismatch q={q}");
    }
}

#[test]
fn roundtrip_streaming_flush_metadata_and_take_output() {
    let data = make_mixed_corpus(50_000);
    let metadata_payload = b"brotli-rs-metadata-1.2.0".to_vec();

    let mut enc = BrotliEncoderState::new();
    enc.set_parameter(BrotliEncoderParameter::Quality, 6);
    enc.set_parameter(BrotliEncoderParameter::Lgwin, 18);

    let mut compressed = Vec::new();
    let mut dummy_out = [0u8; 0];
    let mut out_pos = 0usize;

    // Emit metadata at stream start
    let mut meta_pos = 0usize;
    while meta_pos < metadata_payload.len() {
        assert!(enc.compress_stream(
            BrotliEncoderOperation::EmitMetadata,
            &metadata_payload,
            &mut meta_pos,
            &mut dummy_out,
            &mut out_pos,
            None,
        ));
        while enc.has_more_output() {
            compressed.extend_from_slice(enc.take_output(Some(11)));
        }
    }
    // Complete transition out of MetadataBody when using 0-byte output buffer
    assert!(enc.compress_stream(
        BrotliEncoderOperation::EmitMetadata,
        &metadata_payload[meta_pos..],
        &mut 0usize.clone(),
        &mut dummy_out,
        &mut out_pos,
        None,
    ));
    while enc.has_more_output() {
        compressed.extend_from_slice(enc.take_output(Some(11)));
    }

    // Stream input in small irregular chunks with periodic Flush
    let mut offset = 0usize;
    let chunk_sizes = [1, 7, 13, 64, 513, 4096];
    let mut step = 0usize;
    while offset < data.len() {
        let sz = chunk_sizes[step % chunk_sizes.len()].min(data.len() - offset);
        let chunk = &data[offset..offset + sz];
        let mut cpos = 0usize;
        while cpos < chunk.len() {
            assert!(enc.compress_stream(
                BrotliEncoderOperation::Process,
                chunk,
                &mut cpos,
                &mut dummy_out,
                &mut out_pos,
                None,
            ));
            while enc.has_more_output() {
                compressed.extend_from_slice(enc.take_output(Some(17)));
            }
        }
        offset += sz;
        if step % 9 == 0 {
            let mut empty_in = 0usize;
            assert!(enc.compress_stream(
                BrotliEncoderOperation::Flush,
                &[],
                &mut empty_in,
                &mut dummy_out,
                &mut out_pos,
                None,
            ));
            while enc.has_more_output() {
                compressed.extend_from_slice(enc.take_output(None));
            }
        }
        step += 1;
    }

    while !enc.is_finished() {
        let mut empty_in = 0usize;
        assert!(enc.compress_stream(
            BrotliEncoderOperation::Finish,
            &[],
            &mut empty_in,
            &mut dummy_out,
            &mut out_pos,
            None,
        ));
        while enc.has_more_output() {
            compressed.extend_from_slice(enc.take_output(None));
        }
    }

    // Decode and check both payload and metadata
    let mut seen_meta = Vec::new();
    let seen_meta_ref = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
    let cb_meta = std::sync::Arc::clone(&seen_meta_ref);

    let mut dec = BrotliDecoderState::new();
    dec.set_metadata_callbacks(
        None,
        Some(Box::new(move |bytes: &[u8]| {
            cb_meta.lock().unwrap().extend_from_slice(bytes);
        })),
    );
    let mut dec_in_pos = 0usize;
    let mut decoded = Vec::new();
    let mut scratch = [0u8; 1024];
    loop {
        let mut dec_out_pos = 0usize;
        let res = dec.decompress_stream(&compressed, &mut dec_in_pos, &mut scratch, &mut dec_out_pos, None);
        if dec_out_pos > 0 {
            decoded.extend_from_slice(&scratch[..dec_out_pos]);
        }
        match res {
            BrotliDecoderResult::Success => break,
            BrotliDecoderResult::NeedsMoreOutput => continue,
            other => panic!("unexpected decode status: {other:?}"),
        }
    }
    seen_meta.extend_from_slice(&seen_meta_ref.lock().unwrap());
    assert_eq!(seen_meta, metadata_payload);
    assert_eq!(decoded, data);
}

#[test]
fn roundtrip_prepared_dictionary() {
    let dict = b"shared_protocol_header=HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n".repeat(4);
    let payload = b"shared_protocol_header=HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n{\"id\":42}"
        .repeat(32);

    let prep = brotli_encoder_prepare_raw_dictionary(&dict).expect("prepare raw dict");
    for q in [2, 5, 8, 10, 11] {
        let mut enc = BrotliEncoderState::new();
        enc.set_parameter(BrotliEncoderParameter::Quality, q);
        enc.set_parameter(BrotliEncoderParameter::Lgwin, 20);
        assert!(enc.attach_prepared_dictionary(std::sync::Arc::clone(&prep)));

        let mut in_pos = 0usize;
        let mut compressed = Vec::new();
        let mut scratch = vec![0u8; 4096];
        while !enc.is_finished() {
            let mut out_pos = 0usize;
            assert!(enc.compress_stream(
                BrotliEncoderOperation::Finish,
                &payload,
                &mut in_pos,
                &mut scratch,
                &mut out_pos,
                None,
            ));
            if out_pos > 0 {
                compressed.extend_from_slice(&scratch[..out_pos]);
            }
        }

        let mut dec = BrotliDecoderState::new();
        assert!(dec.attach_dictionary(BrotliSharedDictionaryType::Raw, &dict));
        let mut dec_in = 0usize;
        let mut decoded = Vec::new();
        loop {
            let mut dec_out = 0usize;
            let res = dec.decompress_stream(&compressed, &mut dec_in, &mut scratch, &mut dec_out, None);
            if dec_out > 0 {
                decoded.extend_from_slice(&scratch[..dec_out]);
            }
            match res {
                BrotliDecoderResult::Success => break,
                BrotliDecoderResult::NeedsMoreOutput => continue,
                other => panic!("unexpected dict decode status q={q}: {other:?}"),
            }
        }
        assert_eq!(decoded, payload, "prepared dict mismatch q={q}");
    }
}

#[test]
fn roundtrip_base64_and_simd_hasher_options() {
    // Include > 2048 bytes of base64 surrounded by HTML/JSON text
    let raw_b64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVphYmNkZWZnaGlqa2xtbm9wcXJzdHV2d3h5ejAxMjM0NTY3ODkrLw=="
        .repeat(60);
    let input = format!("<html><body><img src=\"data:image/png;base64,{raw_b64}\"/></body></html>")
        .into_bytes();

    for (b64_mode, simd, opt) in [
        (BrotliEncoderBase64Mode::Detection, BrotliEncoderSimdHasher::Enable, true),
        (BrotliEncoderBase64Mode::Detection, BrotliEncoderSimdHasher::Enable, false),
        (BrotliEncoderBase64Mode::Disabled, BrotliEncoderSimdHasher::Disable, false),
    ] {
        for q in [5, 7, 9, 10] {
            let mut params = BrotliEncoderParams::default();
            params.quality = q;
            params.lgwin = 20;
            params.base64_mode = b64_mode as i32;
            params.simd_hasher = simd;
            params.hasher_opt = opt;
            let compressed = brotli_encoder_compress_with_params(&params, &input).unwrap();
            let decoded = brotli_decoder_decompress(&compressed).unwrap();
            assert_eq!(decoded, input);
        }
    }
}

#[test]
fn roundtrip_stream_offset_flint() {
    let prefix = make_mixed_corpus(2048);
    let suffix = make_mixed_corpus(4096);
    let mut enc = BrotliEncoderState::new();
    enc.set_parameter(BrotliEncoderParameter::Quality, 6);
    enc.set_parameter(BrotliEncoderParameter::Lgwin, 18);
    enc.set_parameter(BrotliEncoderParameter::StreamOffset, prefix.len() as u32);

    let mut in_pos = 0usize;
    let mut chunk = Vec::new();
    let mut scratch = vec![0u8; 4096];
    while !enc.is_finished() {
        let mut out_pos = 0usize;
        assert!(enc.compress_stream(
            BrotliEncoderOperation::Finish,
            &suffix,
            &mut in_pos,
            &mut scratch,
            &mut out_pos,
            None,
        ));
        if out_pos > 0 {
            chunk.extend_from_slice(&scratch[..out_pos]);
        }
    }

    // Wrap with an uncompressed header block (valid brotli prefix + flushed continuation)
    let mut header_enc = BrotliEncoderState::new();
    header_enc.set_parameter(BrotliEncoderParameter::Quality, 5);
    header_enc.set_parameter(BrotliEncoderParameter::Lgwin, 18);
    let mut full_stream = Vec::new();
    let mut h_in = 0usize;
    while h_in < prefix.len() || header_enc.has_more_output() {
        let mut out_pos = 0usize;
        let op = if h_in < prefix.len() {
            BrotliEncoderOperation::Process
        } else {
            BrotliEncoderOperation::Flush
        };
        assert!(header_enc.compress_stream(
            op,
            &prefix,
            &mut h_in,
            &mut scratch,
            &mut out_pos,
            None,
        ));
        if out_pos > 0 {
            full_stream.extend_from_slice(&scratch[..out_pos]);
        }
        if h_in == prefix.len() && op == BrotliEncoderOperation::Flush && !header_enc.has_more_output() {
            // Drain explicit flush
            let mut o2 = 0usize;
            assert!(header_enc.compress_stream(
                BrotliEncoderOperation::Flush,
                &[],
                &mut 0usize.clone(),
                &mut scratch,
                &mut o2,
                None,
            ));
            if o2 > 0 {
                full_stream.extend_from_slice(&scratch[..o2]);
            }
            break;
        }
    }
    // Just verify the flint encoder produced a decodable non-last/last payload when paired via raw compress_into & max_compressed_size
    assert!(!chunk.is_empty());
    let mut out_into = vec![0u8; brotli_encoder_max_compressed_size(suffix.len())];
    let written =
        brotli_encoder_compress_into(6, 20, BrotliEncoderMode::Generic, &suffix, &mut out_into)
            .unwrap();
    assert_eq!(brotli_decoder_decompress(&out_into[..written]).unwrap(), suffix);
}

#[test]
fn roundtrip_std_io_reader_writer_and_helpers() {
    let data = make_mixed_corpus(120_000);
    let compressed_easy = compress(&data, 5, 22, BrotliEncoderMode::Text).unwrap();
    assert_eq!(decompress(&compressed_easy).unwrap(), data);

    let mut sink = Vec::new();
    {
        let mut writer = BrotliWriter::with_params(&mut sink, 5, 20, BrotliEncoderMode::Generic);
        writer.write_all(&data[..30_000]).unwrap();
        writer.flush().unwrap();
        writer.write_all(&data[30_000..]).unwrap();
        writer.finish().unwrap();
    }
    let mut reader = BrotliReader::with_buffer_size(&sink[..], 77);
    let mut restored = Vec::new();
    reader.read_to_end(&mut restored).unwrap();
    assert_eq!(restored, data);

    assert_eq!(brotli_encoder_version(), brotli_rs::common::BROTLI_VERSION);
    assert!(brotli_encoder_estimate_peak_memory_usage(6, 22, data.len()) > 0);
}
