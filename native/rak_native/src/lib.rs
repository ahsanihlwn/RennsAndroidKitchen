// SPDX-License-Identifier: MPL-2.0
//! Read-only native operations. No Qt objects, Python callbacks, or thread pools.
//! Each entry point detaches Python while doing disk I/O and parsing.

use indexmap::IndexMap;
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyString};
use serde::Serialize;
use std::borrow::Cow;
use std::collections::{BTreeMap, HashMap};
use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, Read};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};
mod assets;
mod config_insert;
mod directory;

// Match Python's UTF-8 errors="ignore", including an incomplete final sequence.
fn decode_utf8_ignore(mut bytes: &[u8]) -> String {
    let mut output = String::new();
    loop {
        match std::str::from_utf8(bytes) {
            Ok(text) => {
                output.push_str(text);
                return output;
            }
            Err(error) => {
                let valid = error.valid_up_to();
                output.push_str(std::str::from_utf8(&bytes[..valid]).unwrap_or(""));
                match error.error_len() {
                    Some(length) => bytes = &bytes[valid + length..],
                    None => return output,
                }
            }
        }
    }
}

fn python_whitespace(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

fn parse_line(text: &str, fs_config: bool, values: &mut IndexMap<String, String>) {
    if text.is_ascii()
        && !text
            .bytes()
            .any(|byte| byte == 0x0b || (0x1c..=0x1f).contains(&byte))
    {
        parse_fields(text.split_ascii_whitespace(), fs_config, values);
    } else {
        parse_fields(
            text.split(python_whitespace)
                .filter(|field| !field.is_empty()),
            fs_config,
            values,
        );
    }
}

fn parse_fields<'a>(
    mut fields: impl Iterator<Item = &'a str>,
    fs_config: bool,
    values: &mut IndexMap<String, String>,
) {
    if let Some(key) = fields.next() {
        if key.starts_with('#') {
            return;
        }
        let value = if fs_config {
            fields.nth(2)
        } else {
            fields.next()
        };
        if let Some(value) = value {
            // Preserve raw keys, first insertion order, and last-value-wins.
            values.insert(key.to_owned(), value.to_owned());
        }
    }
}

fn parse_files(
    paths: &[PathBuf],
    fs_config: bool,
    ignore_errors: bool,
) -> io::Result<IndexMap<String, String>> {
    let mut values = IndexMap::new();
    for path in paths {
        let file = match File::open(path) {
            Ok(file) => file,
            Err(_) if ignore_errors => continue,
            Err(error) => return Err(error),
        };
        let mut reader = BufReader::with_capacity(64 * 1024, file);
        let mut bytes = Vec::new();
        loop {
            bytes.clear();
            match reader.read_until(b'\n', &mut bytes) {
                Ok(0) => break,
                Ok(_) => {
                    let text = match std::str::from_utf8(&bytes) {
                        Ok(text) => Cow::Borrowed(text),
                        Err(_) => Cow::Owned(decode_utf8_ignore(&bytes)),
                    };
                    // Python TextIO uses universal newlines, including bare CR.
                    for line in text.split(['\r', '\n']) {
                        parse_line(line, fs_config, &mut values);
                    }
                }
                Err(_) if ignore_errors => break,
                Err(error) => return Err(error),
            }
        }
    }
    Ok(values)
}

#[pyfunction]
#[pyo3(signature = (paths, kind, ignore_errors=false))]
fn parse_metadata(
    py: Python<'_>,
    paths: Vec<PathBuf>,
    kind: &str,
    ignore_errors: bool,
) -> PyResult<Py<PyDict>> {
    let fs_config = match kind {
        "contexts" => false,
        "fs_config" => true,
        _ => return Err(PyValueError::new_err("kind must be contexts or fs_config")),
    };
    let values = py.detach(move || parse_files(&paths, fs_config, ignore_errors))?;
    let result = PyDict::new(py);
    // Contexts and permission strings repeat heavily. Reuse their immutable
    // Python objects; bound the cache for unusual files with many unique values.
    let mut labels = HashMap::<String, Py<PyString>>::new();
    for (key, value) in values {
        if let Some(label) = labels.get(&value) {
            result.set_item(key, label)?;
        } else {
            let label = PyString::new(py, &value);
            result.set_item(key, &label)?;
            if labels.len() < 1024 {
                labels.insert(value, label.unbind());
            }
        }
    }
    Ok(result.unbind())
}

fn r32(bytes: &[u8], offset: usize) -> Option<u32> {
    Some(u32::from_le_bytes(
        bytes.get(offset..offset + 4)?.try_into().ok()?,
    ))
}

fn r16(bytes: &[u8], offset: usize) -> Option<u16> {
    Some(u16::from_le_bytes(
        bytes.get(offset..offset + 2)?.try_into().ok()?,
    ))
}

fn header_magic(bytes: &[u8]) -> &'static str {
    if bytes.len() < 4 {
        return "";
    }
    if bytes.starts_with(b"CrAU") {
        return "PAYLOAD";
    }
    if bytes.starts_with(b"VNDRBOOT") {
        return "VNDRBOOT";
    }
    if bytes.starts_with(b"ANDROID!") {
        return "BOOT";
    }
    if r32(bytes, 0) == Some(0xed26ff3a) {
        return "SPARSE";
    }
    let is_super = |offset| matches!(r32(bytes, offset), Some(0x414c5000 | 0x616c4467));
    if [0, 4096, 8192].into_iter().any(is_super) {
        return "SUPER";
    }
    if let Some(offset) = bytes.iter().position(|byte| *byte != 0) {
        if offset > 0 && is_super(offset) {
            return "SUPER";
        }
    }
    if [0, 512, 1024, 4096]
        .into_iter()
        .any(|offset| r32(bytes, offset) == Some(0xe0f5e1e2))
    {
        return "EROFS";
    }
    for start in [1024, 0] {
        if r16(bytes, start + 0x38) == Some(0xef53) {
            let sb = &bytes[start..];
            if sb.len() < 0x68 {
                return "EXT2";
            }
            let compat = r32(sb, 0x5c).unwrap_or(0);
            let incompat = r32(sb, 0x60).unwrap_or(0);
            let ro = r32(sb, 0x64).unwrap_or(0);
            if incompat & (0x40 | 0x80 | 0x200) != 0 || ro & (0x8 | 0x10 | 0x20 | 0x40) != 0 {
                return "EXT4";
            }
            if compat & 0x4 != 0 || incompat & (0x4 | 0x8) != 0 {
                return "EXT3";
            }
            return "EXT2";
        }
    }
    ""
}

fn read_magic(path: &Path) -> String {
    if !path.is_file() {
        return String::new();
    }
    let mut bytes = Vec::with_capacity(65536);
    match File::open(path).and_then(|file| file.take(65536).read_to_end(&mut bytes)) {
        Ok(_) => header_magic(&bytes).to_owned(),
        Err(_) => String::new(),
    }
}

#[derive(Serialize)]
struct ImageInfo {
    mtime: i64,
    size: u64,
    fs_type: String,
}

#[derive(Default, Serialize)]
struct Scan {
    folders: BTreeMap<String, bool>,
    // Full scan uses object values; fast scan retains its existing string shape.
    fscache: BTreeMap<String, ImageInfo>,
    child_snapshot: BTreeMap<String, Vec<String>>,
}

fn path_string(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

fn epoch_seconds(time: SystemTime) -> i64 {
    match time.duration_since(UNIX_EPOCH) {
        Ok(duration) => duration.as_secs() as i64,
        Err(error) => -(error.duration().as_secs() as i64),
    }
}

fn is_reparse_directory(path: &Path) -> bool {
    let metadata = match fs::symlink_metadata(path) {
        Ok(value) => value,
        Err(_) => return true,
    };
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        // Junctions must not cause recursive scans to escape the tree or loop.
        if metadata.file_attributes() & 0x400 != 0 {
            return true;
        }
    }
    false
}

fn scan_one(root: &Path, recursive: bool, full: bool, result: &mut Scan) -> Vec<PathBuf> {
    let mut directories = Vec::new();
    let mut recurse = Vec::new();
    let mut files = Vec::new();
    let entries = match fs::read_dir(root) {
        Ok(entries) => Some(entries),
        // os.walk skips unreadable/deleted directories entirely.
        Err(_) if recursive => return recurse,
        Err(_) => None,
    };
    if let Some(entries) = entries {
        for entry in entries.flatten() {
            let path = entry.path();
            // os.walk classifies directory symlinks as dirs, but does not follow
            // them. Depth-1 scandir uses follow_symlinks=False instead.
            let is_dir = entry
                .file_type()
                .map(|kind| kind.is_dir() || (recursive && kind.is_symlink() && path.is_dir()))
                .unwrap_or(false);
            if is_dir {
                directories.push(path_string(&path));
                if !recursive || !is_reparse_directory(&path) {
                    recurse.push(path);
                }
            } else {
                files.push(path);
            }
        }
    }
    let norm = path_string(root);
    result
        .folders
        .insert(norm.clone(), !directories.is_empty() || !files.is_empty());
    result.child_snapshot.insert(norm, directories);
    if full {
        for path in files {
            if !path
                .file_name()
                .is_some_and(|name| name.to_string_lossy().to_lowercase().ends_with(".img"))
            {
                continue;
            }
            let (mtime, size) = match fs::metadata(&path) {
                Ok(info) => (info.modified().map(epoch_seconds).unwrap_or(0), info.len()),
                Err(_) => (0, 0),
            };
            result.fscache.insert(
                path_string(&path),
                ImageInfo {
                    mtime,
                    size,
                    fs_type: read_magic(&path),
                },
            );
        }
    }
    recurse
}

fn scan_tree(base: &Path, recursive: bool, full: bool) -> Scan {
    let mut result = Scan::default();
    if recursive {
        let mut pending = vec![base.to_path_buf()];
        while let Some(root) = pending.pop() {
            // Iterative traversal avoids recursion-stack overflow on deep trees.
            pending.extend(scan_one(&root, true, full, &mut result));
        }
    } else {
        for child in scan_one(base, false, full, &mut result) {
            scan_one(&child, false, full, &mut result);
        }
    }
    result
}

#[pyfunction]
#[pyo3(signature = (base, recursive=false, full=true))]
fn prescan_json(py: Python<'_>, base: PathBuf, recursive: bool, full: bool) -> PyResult<String> {
    py.detach(move || serde_json::to_string(&scan_tree(&base, recursive, full)))
        .map_err(|error| PyOSError::new_err(error.to_string()))
}

#[pymodule]
fn rak_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("API_VERSION", 3)?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add(
        "BUILD_FINGERPRINT",
        option_env!("RAK_NATIVE_BUILD_FINGERPRINT").unwrap_or(""),
    )?;
    module.add(
        "__all__",
        vec![
            "API_VERSION",
            "__version__",
            "BUILD_FINGERPRINT",
            "parse_metadata",
            "prescan_json",
            "directory_entries_json",
            "directory_snapshots_json",
            "symlink_batch_json",
            "asset_index_json",
            "config_insert_lines",
            "config_insert_text",
        ],
    )?;
    module.add_function(wrap_pyfunction!(parse_metadata, module)?)?;
    module.add_function(wrap_pyfunction!(prescan_json, module)?)?;
    module.add_function(wrap_pyfunction!(directory::directory_entries_json, module)?)?;
    module.add_function(wrap_pyfunction!(
        directory::directory_snapshots_json,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(directory::symlink_batch_json, module)?)?;
    module.add_function(wrap_pyfunction!(assets::asset_index_json, module)?)?;
    module.add_function(wrap_pyfunction!(
        config_insert::config_insert_lines,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(config_insert::config_insert_text, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn malformed_utf8_is_ignored_not_replaced() {
        assert_eq!(
            decode_utf8_ignore(b"/sys\xfftem\xc3\xa9\xe2\x82"),
            "/systemé"
        );
    }

    #[test]
    fn metadata_preserves_regex_and_permission_tokens() {
        let mut values = IndexMap::new();
        parse_line(
            " /system/a\\.apk(/.*)?  u:object_r:system_file:s0 ",
            false,
            &mut values,
        );
        parse_line("# /ignored ignored", false, &mut values);
        parse_line(" /system/a\\.apk(/.*)? updated ", false, &mut values);
        assert_eq!(values.get_index(0).unwrap().0, "/system/a\\.apk(/.*)?");
        assert_eq!(values.get_index(0).unwrap().1, "updated");
        parse_line(
            "system/bin/tool 0 2000 0755 capabilities=0x0",
            true,
            &mut values,
        );
        assert_eq!(values["system/bin/tool"], "0755");
    }

    #[test]
    fn header_priority_and_ext_feature_flags() {
        let mut data = vec![0u8; 65536];
        data[1024 + 0x38..1024 + 0x3a].copy_from_slice(&0xef53u16.to_le_bytes());
        assert_eq!(header_magic(&data), "EXT2");
        data[1024 + 0x5c] = 4;
        assert_eq!(header_magic(&data), "EXT3");
        data[1024 + 0x60] = 0x40;
        assert_eq!(header_magic(&data), "EXT4");
        data[512..516].copy_from_slice(&0xe0f5e1e2u32.to_le_bytes());
        assert_eq!(header_magic(&data), "EROFS");
        data[..4].copy_from_slice(b"CrAU");
        assert_eq!(header_magic(&data), "PAYLOAD");
    }
}
