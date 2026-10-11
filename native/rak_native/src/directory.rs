// SPDX-License-Identifier: MPL-2.0
//! Directory data only: no Qt handles, callbacks, or persistent caches.
use crate::{path_string, python_whitespace, read_magic};
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Component, Path, PathBuf};
use std::thread::{self, Builder};
use std::time::UNIX_EPOCH;

fn normalized(path: &Path) -> PathBuf {
    let mut output = PathBuf::new();
    for part in path.components() {
        match part {
            Component::CurDir => {}
            Component::ParentDir => {
                if matches!(output.components().next_back(), Some(Component::Normal(_))) {
                    output.pop();
                } else if !output.has_root() {
                    output.push(part);
                }
            }
            _ => output.push(part),
        }
    }
    if output.as_os_str().is_empty() {
        output.push(".");
    }
    output
}

fn utf16_ignore(bytes: &[u8], big_endian: bool) -> String {
    char::decode_utf16(bytes.chunks_exact(2).map(|pair| {
        if big_endian {
            u16::from_be_bytes([pair[0], pair[1]])
        } else {
            u16::from_le_bytes([pair[0], pair[1]])
        }
    }))
    .filter_map(Result::ok)
    .collect()
}

fn placeholder_target(raw: &[u8]) -> Option<String> {
    let header = b"!<symlink>";
    let wide = b"!\0<\0s\0y\0m\0l\0i\0n\0k\0>\0";
    let rest = if raw.starts_with(header) {
        &raw[header.len()..]
    } else if raw.starts_with(b"\xff\xfe") {
        let start = raw.windows(header.len()).position(|part| part == header)?;
        &raw[start + header.len()..]
    } else if raw.starts_with(wide) {
        &raw[wide.len()..]
    } else {
        return None;
    };
    let target = if rest.starts_with(b"\xff\xfe") {
        utf16_ignore(&rest[2..], false)
    } else if rest.starts_with(b"\xfe\xff") {
        utf16_ignore(&rest[2..], true)
    } else if rest.starts_with(b"\xef\xbb\xbf") {
        crate::decode_utf8_ignore(&rest[3..])
    } else if rest.get(1) == Some(&0) {
        utf16_ignore(rest, false)
    } else {
        match std::str::from_utf8(rest) {
            Ok(text) => text.to_owned(),
            Err(_) => utf16_ignore(rest, false),
        }
    };
    let target = target.replace('\0', "");
    let target = target.trim_matches(python_whitespace);
    (!target.is_empty()).then(|| target.to_owned())
}

#[derive(Clone, Serialize)]
pub(crate) struct Link {
    file: String,
    target: String,
    resolved: String,
    exists: bool,
    is_dir: bool,
    is_file: bool,
}

type TargetCache = HashMap<PathBuf, (bool, bool, bool)>;

fn resolve(path: &Path, root: &Path, cache: &mut TargetCache) -> Option<Link> {
    let mut bytes = Vec::with_capacity(513);
    File::open(path)
        .ok()?
        .take(513)
        .read_to_end(&mut bytes)
        .ok()?;
    if bytes.len() > 512 {
        return None;
    }
    let target = placeholder_target(&bytes)?;
    let norm_target = target.replace('\\', "/");
    let clean = norm_target.trim_start_matches('/');
    let mut candidates = Vec::new();
    if norm_target.starts_with('/') {
        candidates.push(root.join(clean));
        if let Some(sub) = clean.strip_prefix("system/") {
            if !sub.is_empty() {
                candidates.push(root.join(sub));
            }
            candidates.push(root.join("system").join(clean));
        } else {
            candidates.push(root.join("system").join(clean));
            if let Some(parent) = root.parent().filter(|parent| *parent != root) {
                candidates.push(parent.join(clean));
            }
        }
    } else {
        candidates.push(path.parent().unwrap_or(Path::new(".")).join(&norm_target));
        candidates.push(root.join(&norm_target));
        candidates.push(root.join("system").join(&norm_target));
    }
    let candidates: Vec<_> = candidates.iter().map(|path| normalized(path)).collect();
    let norm_file = normalized(path);
    let mut resolved = None;
    let (mut exists, mut is_dir, mut is_file) = (false, false, false);
    for candidate in &candidates {
        if *candidate == norm_file {
            continue;
        }
        let info = cache.entry(candidate.clone()).or_insert_with(|| {
            fs::metadata(candidate)
                .map(|info| (true, info.is_dir(), info.is_file()))
                .unwrap_or((false, false, false))
        });
        if info.0 {
            resolved = Some(candidate.clone());
            (exists, is_dir, is_file) = *info;
            break;
        }
    }
    let resolved = resolved.unwrap_or_else(|| {
        candidates
            .iter()
            .find(|path| **path != norm_file)
            .or_else(|| candidates.first())
            .cloned()
            .unwrap_or_else(|| normalized(&root.join(clean)))
    });
    if !exists {
        let lower = norm_target.to_lowercase();
        let last = lower.trim_end_matches('/').rsplit('/').next().unwrap_or("");
        let name = path.file_name().unwrap_or_default().to_string_lossy();
        if norm_target.ends_with('/') {
            is_dir = true;
        } else if last.contains('.')
            || name.contains('.')
            || ["/bin/", "/xbin/", "/sbin/"]
                .iter()
                .any(|part| lower.contains(part))
        {
            is_file = true;
        } else {
            is_dir = true;
        }
    }
    Some(Link {
        file: path_string(path),
        target,
        resolved: path_string(&resolved),
        exists,
        is_dir,
        is_file,
    })
}

fn resolve_slice(paths: &[PathBuf], root: &Path) -> Vec<Option<Link>> {
    let mut cache = TargetCache::new();
    paths
        .iter()
        .map(|path| resolve(path, root, &mut cache))
        .collect()
}

fn batch_resolve(paths: &[PathBuf], root: &Path) -> Vec<Option<Link>> {
    if paths.len() < 64 {
        return resolve_slice(paths, root);
    }
    let chunk_size = paths.len().div_ceil(4);
    let chunks: Vec<&[PathBuf]> = paths.chunks(chunk_size).collect();
    let res = thread::scope(|s| {
        let mut handles = Vec::new();
        let mut spawn_failed = false;
        for chunk in &chunks {
            match Builder::new().spawn_scoped(s, move || {
                let mut cache = TargetCache::new();
                chunk
                    .iter()
                    .map(|path| resolve(path, root, &mut cache))
                    .collect::<Vec<_>>()
            }) {
                Ok(handle) => handles.push(handle),
                Err(_) => {
                    spawn_failed = true;
                    break;
                }
            }
        }
        let mut chunk_results = Vec::new();
        let mut thread_panicked = false;
        for handle in handles {
            match handle.join() {
                Ok(res) => chunk_results.push(res),
                Err(_) => thread_panicked = true,
            }
        }
        if spawn_failed || thread_panicked {
            None
        } else {
            let mut results = Vec::with_capacity(paths.len());
            for part in chunk_results {
                results.extend(part);
            }
            Some(results)
        }
    });
    res.unwrap_or_else(|| resolve_slice(paths, root))
}

#[pyfunction]
pub(crate) fn symlink_batch_json(
    py: Python<'_>,
    paths: Vec<PathBuf>,
    root: PathBuf,
) -> PyResult<String> {
    py.detach(move || {
        let links = batch_resolve(&paths, &root);
        let values: BTreeMap<_, _> = paths
            .iter()
            .zip(links)
            .map(|(path, link)| (path_string(&normalized(path)), link))
            .collect();
        serde_json::to_string(&values)
    })
    .map_err(|error| pyo3::exceptions::PyOSError::new_err(error.to_string()))
}

fn mtime_ns(info: &fs::Metadata) -> Option<i128> {
    info.modified()
        .ok()
        .map(|time| match time.duration_since(UNIX_EPOCH) {
            Ok(duration) => duration.as_nanos() as i128,
            Err(error) => -(error.duration().as_nanos() as i128),
        })
}

#[derive(Serialize)]
struct Entry {
    name: String,
    path: String,
    is_dir: bool,
    size: Option<u64>,
    mtime_ns: Option<i128>,
    symlink: Option<Link>,
    magic: String,
}

fn entries(path: &Path, root: &Path) -> io::Result<Vec<Entry>> {
    let mut output = Vec::new();
    let mut candidate_indices = Vec::new();
    let mut candidate_paths = Vec::new();
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let entry_path = entry.path();
        let is_dir = entry.file_type().map(|kind| kind.is_dir()).unwrap_or(false);
        let info = if is_dir { None } else { entry.metadata().ok() };
        let size = if is_dir {
            Some(0)
        } else {
            info.as_ref().map(fs::Metadata::len)
        };
        let name = entry.file_name().to_string_lossy().into_owned();
        let low = name.to_lowercase();
        let magic = if !is_dir && (low.ends_with(".img") || low.ends_with(".bin")) {
            read_magic(&entry_path)
        } else {
            String::new()
        };
        if !is_dir && size.is_some_and(|s| s <= 512) {
            candidate_indices.push(output.len());
            candidate_paths.push(entry_path.clone());
        }
        output.push(Entry {
            name,
            path: path_string(&entry_path),
            is_dir,
            size,
            mtime_ns: if is_dir {
                Some(0)
            } else {
                info.as_ref().and_then(mtime_ns)
            },
            symlink: None,
            magic,
        });
    }
    let links = batch_resolve(&candidate_paths, root);
    for (idx, link) in candidate_indices.into_iter().zip(links) {
        output[idx].symlink = link;
    }
    output.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(output)
}

#[pyfunction]
pub(crate) fn directory_entries_json(
    py: Python<'_>,
    path: PathBuf,
    root: PathBuf,
) -> PyResult<String> {
    let output = py.detach(move || entries(&path, &root))?;
    py.detach(move || serde_json::to_string(&output))
        .map_err(|error| pyo3::exceptions::PyOSError::new_err(error.to_string()))
}

// File signatures use nanoseconds; directory mtime is intentionally ignored.
// Writes below a child directory must not rebuild its parent's rows.
type Signature = Vec<(String, bool, u64, i128)>;

fn snapshot(path: &Path) -> io::Result<Signature> {
    let mut output = Vec::new();
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let is_dir = entry.file_type()?.is_dir();
        let (size, stamp) = if is_dir {
            (0, 0)
        } else {
            let info = entry.metadata()?;
            (info.len(), mtime_ns(&info).unwrap_or(0))
        };
        output.push((
            entry.file_name().to_string_lossy().into_owned(),
            is_dir,
            size,
            stamp,
        ));
    }
    output.sort();
    Ok(output)
}

#[derive(Serialize, Deserialize, Default)]
struct Snapshots {
    snapshots: BTreeMap<String, Signature>,
    changed: Vec<String>,
    missing: Vec<String>,
}

#[pyfunction]
pub(crate) fn directory_snapshots_json(
    py: Python<'_>,
    paths: Vec<PathBuf>,
    previous: String,
) -> PyResult<String> {
    py.detach(move || {
        let previous: BTreeMap<String, Signature> = serde_json::from_str(&previous)?;
        let mut output = Snapshots::default();
        for path in paths {
            let key = path_string(&path);
            match snapshot(&path) {
                Ok(signature) => {
                    if previous.get(&key) != Some(&signature) {
                        output.changed.push(key.clone());
                    }
                    output.snapshots.insert(key, signature);
                }
                Err(_) => output.missing.push(key),
            }
        }
        serde_json::to_string(&output)
    })
    .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn placeholder_encodings_and_malformed_values() {
        assert_eq!(
            placeholder_target(b"!<symlink>/system/bin/toybox\0\n"),
            Some("/system/bin/toybox".into())
        );
        assert_eq!(
            placeholder_target(b"!<symlink>\xff\xfea\0\0\xd8b\0"),
            Some("ab".into())
        );
        assert_eq!(
            placeholder_target(b"!<symlink>\xfe\xff\0a\0b"),
            Some("ab".into())
        );
        assert_eq!(
            placeholder_target(b"!\0<\0s\0y\0m\0l\0i\0n\0k\0>\0a\0"),
            Some("a".into())
        );
        assert_eq!(placeholder_target(b"not a placeholder"), None);
        assert_eq!(placeholder_target(b"!<symlink>\0 \n"), None);
    }

    #[test]
    fn lexical_normalization_retains_relative_parents() {
        assert_eq!(normalized(Path::new("a/../b/./c")), PathBuf::from("b/c"));
        assert_eq!(normalized(Path::new("../../a")), PathBuf::from("../../a"));
    }

    #[test]
    fn batch_resolve_parity_and_target_removal() {
        let unique = format!(
            "rak_test_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let test_dir = std::env::temp_dir().join(unique);
        let root = test_dir.join("root");
        let folder = root.join("system").join("bin");
        fs::create_dir_all(&folder).unwrap();
        let target_dir = root.join("system").join("target_dir");
        fs::create_dir_all(&target_dir).unwrap();
        let target_file = folder.join("real_target");
        fs::write(&target_file, b"content").unwrap();

        let mut paths = Vec::new();
        for i in 0..130 {
            let p = folder.join(format!("item_{}", i));
            match i % 4 {
                0 => fs::write(&p, b"!<symlink>/system/bin/real_target\0").unwrap(),
                1 => fs::write(&p, b"!<symlink>/system/target_dir\0").unwrap(),
                2 => fs::write(&p, b"!<symlink>/system/broken_target\0").unwrap(),
                _ => fs::write(&p, b"plain non-symlink tiny content").unwrap(),
            }
            paths.push(p);
        }

        let batched = batch_resolve(&paths, &root);
        let sequential = resolve_slice(&paths, &root);
        let batched_json = serde_json::to_string(&batched).unwrap();
        let seq_json = serde_json::to_string(&sequential).unwrap();
        assert_eq!(batched_json, seq_json);

        assert!(batched[0].as_ref().unwrap().exists);
        assert!(batched[0].as_ref().unwrap().is_file);
        assert!(batched[1].as_ref().unwrap().exists);
        assert!(batched[1].as_ref().unwrap().is_dir);
        assert!(!batched[2].as_ref().unwrap().exists);
        assert!(batched[3].is_none());

        fs::remove_file(&target_file).unwrap();
        let fresh_batched = batch_resolve(&paths, &root);
        assert!(!fresh_batched[0].as_ref().unwrap().exists);

        let _ = fs::remove_dir_all(&test_dir);
    }

    #[test]
    fn boundary_and_invalid_candidates() {
        let unique = format!(
            "rak_boundary_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let test_dir = std::env::temp_dir().join(unique);
        fs::create_dir_all(&test_dir).unwrap();
        let mut cache = TargetCache::new();

        let f512 = test_dir.join("f512");
        let mut b512 = b"!<symlink>target".to_vec();
        b512.resize(512, b' ');
        fs::write(&f512, &b512).unwrap();
        let res512 = resolve(&f512, &test_dir, &mut cache);
        assert!(res512.is_some());
        assert_eq!(res512.unwrap().target, "target");

        let f513 = test_dir.join("f513");
        let mut b513 = b"!<symlink>target".to_vec();
        b513.resize(513, b' ');
        fs::write(&f513, &b513).unwrap();
        assert!(resolve(&f513, &test_dir, &mut cache).is_none());

        let empty = test_dir.join("empty");
        fs::write(&empty, b"").unwrap();
        assert!(resolve(&empty, &test_dir, &mut cache).is_none());

        let invalid = test_dir.join("invalid");
        fs::write(&invalid, b"invalid placeholder content").unwrap();
        assert!(resolve(&invalid, &test_dir, &mut cache).is_none());

        let _ = fs::remove_dir_all(&test_dir);
    }
}
