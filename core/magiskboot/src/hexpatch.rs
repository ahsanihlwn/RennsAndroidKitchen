use std::fs::{self, OpenOptions};
use std::io::Write;

use memchr::memmem;

fn decode_hex(hex: &str) -> Result<Vec<u8>, String> {
    if hex.len() % 2 != 0 {
        return Err(format!("Invalid hex string length: {}", hex.len()));
    }
    let bytes = hex.as_bytes();
    let mut out = Vec::with_capacity(bytes.len() / 2);
    for pair in bytes.chunks_exact(2) {
        let hi = hex_nibble(pair[0])?;
        let lo = hex_nibble(pair[1])?;
        out.push((hi << 4) | lo);
    }
    Ok(out)
}

fn hex_nibble(b: u8) -> Result<u8, String> {
    match b {
        b'0'..=b'9' => Ok(b - b'0'),
        b'a'..=b'f' => Ok(b - b'a' + 10),
        b'A'..=b'F' => Ok(b - b'A' + 10),
        _ => Err(format!("Invalid hex character: {}", b as char)),
    }
}

pub fn cmd_hexpatch(file: &str, from_hex: &str, to_hex: &str) -> Result<bool, String> {
    let pattern = decode_hex(from_hex)?;
    let patch = decode_hex(to_hex)?;
    if pattern.is_empty() {
        return Ok(false);
    }

    let mut data = fs::read(file).map_err(|e| format!("Failed reading [{file}]: {e}"))?;
    let mut patched = false;
    let mut pos = 0usize;

    while pos < data.len() {
        let Some(rel) = memmem::find(&data[pos..], &pattern) else {
            break;
        };
        let abs = pos + rel;
        eprintln!("Patch @ {abs:08X} [{from_hex}] -> [{to_hex}]");
        data[abs..abs + pattern.len()].fill(0);
        let copy_len = patch.len().min(pattern.len());
        data[abs..abs + copy_len].copy_from_slice(&patch[..copy_len]);
        patched = true;
        pos = abs + pattern.len();
    }

    if patched {
        let mut f = OpenOptions::new()
            .write(true)
            .truncate(true)
            .open(file)
            .map_err(|e| format!("Failed opening [{file}] for write: {e}"))?;
        f.write_all(&data)
            .map_err(|e| format!("Failed writing [{file}]: {e}"))?;
    }

    Ok(patched)
}
