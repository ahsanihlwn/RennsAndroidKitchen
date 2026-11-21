use std::fs;
use std::path::Path;

use brotli_rs::dec::{
    brotli_decoder_decompress, BrotliDecoderResult, BrotliDecoderState,
};

#[test]
fn test_compatibility_vectors() {
    let testdata_dir = std::env::var_os("BROTLI_TESTDATA_DIR")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| {
            Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap()
                .join("brotli/tests/testdata")
        });

    if !testdata_dir.exists() {
        eprintln!(
            "Skipping decoder_compat vectors (external testdata dir not present at {})",
            testdata_dir.display()
        );
        return;
    }

    let mut count = 0;
    let mut entries: Vec<_> = fs::read_dir(&testdata_dir)
        .expect("Failed to read testdata dir")
        .filter_map(|e| e.ok())
        .collect();
    entries.sort_by_key(|e| e.file_name());

    for entry in entries {
        let path = entry.path();
        let name = path.file_name().unwrap().to_str().unwrap();
        if let Some(base_name) = name.split(".compressed").next() {
            if !name.contains(".compressed") {
                continue;
            }
            let expected_path = testdata_dir.join(base_name);
            let compressed = fs::read(&path).unwrap();
            let expected = fs::read(&expected_path).unwrap();

            // 1. One-shot decompression
            let decoded = brotli_decoder_decompress(&compressed)
                .unwrap_or_else(|e| panic!("Failed to decompress {}: {:?}", name, e));
            assert_eq!(decoded, expected, "Mismatch for {}", name);

            // 2. Streaming decompression with tiny 7-byte input chunks and 13-byte output chunks
            let mut dec = BrotliDecoderState::new();
            let mut in_pos = 0usize;
            let mut out = Vec::new();
            let mut out_buf = [0u8; 13];
            loop {
                let in_end = (in_pos + 7).min(compressed.len());
                let mut local_in = 0usize;
                let chunk = &compressed[in_pos..in_end];
                let mut out_pos = 0usize;
                let res = dec.decompress_stream(chunk, &mut local_in, &mut out_buf, &mut out_pos, None);
                in_pos += local_in;
                if out_pos > 0 {
                    out.extend_from_slice(&out_buf[..out_pos]);
                }
                match res {
                    BrotliDecoderResult::Success => break,
                    BrotliDecoderResult::NeedsMoreInput => {
                        assert!(in_pos < compressed.len(), "Unexpected EOF on {}", name);
                    }
                    BrotliDecoderResult::NeedsMoreOutput => {}
                    BrotliDecoderResult::Error => {
                        panic!("Streaming error on {}: {:?}", name, dec.error_code());
                    }
                }
            }
            assert!(dec.is_finished());
            assert_eq!(out, expected, "Streaming mismatch for {}", name);
            count += 1;
        }
    }
    assert!(count >= 40, "Expected at least 40 compatibility files, got {}", count);
}
