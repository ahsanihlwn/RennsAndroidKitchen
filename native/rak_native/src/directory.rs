// SPDX-License-Identifier: MPL-2.0
//! Directory data only: no Qt handles, callbacks, or persistent caches.
use crate::{path_string, python_whitespace, read_magic};
use pyo3::prelude::*;
use rayon::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Component, Path, PathBuf};
use std::sync::{OnceLock, RwLock};
use std::time::{SystemTime, UNIX_EPOCH};

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

type TargetCache = HashMap<PathBuf, (bool, bool, bool, PathBuf)>;

const PLACEHOLDER_CACHE_LIMIT: usize = 65_536;
// Match Git's racy-index guard: never serve a cached placeholder header for a
// file modified within the coarse OS timestamp granularity window.
const RACY_WINDOW_NS: i128 = 100_000_000; // 100ms

#[derive(Clone)]
struct CachedPlaceholder {
    size: u64,
    mtime_ns: i128,
    target: Option<String>,
}

fn placeholder_cache() -> &'static RwLock<HashMap<String, CachedPlaceholder>> {
    static CACHE: OnceLock<RwLock<HashMap<String, CachedPlaceholder>>> = OnceLock::new();
    CACHE.get_or_init(|| RwLock::new(HashMap::new()))
}

fn cache_key(path: &Path) -> String {
    let text = path_string(path);
    #[cfg(windows)]
    {
        text.to_lowercase()
    }
    #[cfg(not(windows))]
    {
        text
    }
}

fn now_ns() -> i128 {
    match SystemTime::now().duration_since(UNIX_EPOCH) {
        Ok(duration) => duration.as_nanos() as i128,
        Err(error) => -(error.duration().as_nanos() as i128),
    }
}

fn read_placeholder_target_uncached(path: &Path) -> Option<String> {
    let mut file = File::open(path).ok()?;
    let mut buf = [0u8; 513];
    let mut total = 0;
    while total < buf.len() {
        match file.read(&mut buf[total..]) {
            Ok(0) => break,
            Ok(n) => total += n,
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => return None,
        }
    }
    if total > 512 {
        return None;
    }
    placeholder_target(&buf[..total])
}

fn read_placeholder_target_cached(
    path: &Path,
    known_meta: Option<(u64, i128)>,
    now_stamp_ns: i128,
    use_cache: bool,
) -> Option<String> {
    if !use_cache {
        return read_placeholder_target_uncached(path);
    }
    if let Some((size, stamp_ns)) = known_meta {
        let is_settled = now_stamp_ns.saturating_sub(stamp_ns) >= RACY_WINDOW_NS;
        let key = cache_key(path);
        if is_settled {
            if let Ok(guard) = placeholder_cache().read() {
                if let Some(entry) = guard.get(&key) {
                    if entry.size == size && entry.mtime_ns == stamp_ns {
                        return entry.target.clone();
                    }
                }
            }
        }
        let target = read_placeholder_target_uncached(path);
        if is_settled {
            if let Ok(mut guard) = placeholder_cache().write() {
                if guard.len() >= PLACEHOLDER_CACHE_LIMIT {
                    guard.clear();
                }
                guard.insert(
                    key,
                    CachedPlaceholder {
                        size,
                        mtime_ns: stamp_ns,
                        target: target.clone(),
                    },
                );
            }
        }
        return target;
    }
    read_placeholder_target_uncached(path)
}

struct LiveResolveContext<'a> {
    siblings: Option<&'a HashMap<String, (bool, bool, bool)>>,
    shared_targets: &'a RwLock<HashMap<String, (bool, bool, bool)>>,
    missing_parents: &'a RwLock<HashSet<String>>,
}

fn build_target_candidates(file_path: &Path, norm_target: &str, root: &Path) -> Vec<PathBuf> {
    let clean = norm_target.trim_start_matches('/');
    let mut raw = Vec::with_capacity(4);
    if norm_target.starts_with('/') {
        raw.push(root.join(clean));
        if let Some(sub) = clean.strip_prefix("system/") {
            if !sub.is_empty() {
                raw.push(root.join(sub));
            }
            raw.push(root.join("system").join(clean));
        } else {
            raw.push(root.join("system").join(clean));
            if let Some(parent) = root.parent().filter(|parent| *parent != root) {
                raw.push(parent.join(clean));
            }
        }
    } else {
        raw.push(file_path.parent().unwrap_or(Path::new(".")).join(norm_target));
        raw.push(root.join(norm_target));
        raw.push(root.join("system").join(norm_target));
    }
    let mut out = Vec::with_capacity(raw.len());
    for item in raw {
        let norm = normalized(&item);
        if !out.contains(&norm) {
            out.push(norm);
        }
    }
    out
}

fn inspect_node_for_chain(
    candidate: &Path,
    ctx: Option<&LiveResolveContext<'_>>,
) -> (bool, bool, bool, Option<String>) {
    if let Some(ctx) = ctx {
        if let Some(siblings) = ctx.siblings {
            let key = cache_key(candidate);
            if let Some(&(ex, is_dir, _is_file)) = siblings.get(&key) {
                if ex && is_dir {
                    return (true, true, false, None);
                }
            }
        }
    }
    let Ok(meta) = fs::symlink_metadata(candidate) else {
        return (false, false, false, None);
    };
    if meta.file_type().is_symlink() {
        let link_target = fs::read_link(candidate)
            .ok()
            .map(|p| p.to_string_lossy().into_owned())
            .filter(|s| !s.is_empty());
        return (true, false, false, link_target);
    }
    if meta.is_dir() {
        return (true, true, false, None);
    }
    if meta.is_file() {
        if meta.len() <= 512 {
            if let Some(target) = read_placeholder_target_uncached(candidate) {
                return (true, false, false, Some(target));
            }
        }
        return (true, false, true, None);
    }
    (true, false, false, None)
}

fn strip_walk_prefix(candidate: &Path, root: &Path) -> Option<(PathBuf, Vec<String>)> {
    let mut bases = vec![normalized(root)];
    if let Some(parent) = root.parent().filter(|p| *p != root) {
        bases.push(normalized(parent));
    }
    for base in bases {
        if let Ok(rel) = candidate.strip_prefix(&base) {
            let parts: Vec<String> = rel
                .components()
                .filter_map(|c| match c {
                    Component::Normal(s) => Some(s.to_string_lossy().into_owned()),
                    _ => None,
                })
                .collect();
            if parts.len() >= 2 {
                return Some((base, parts));
            }
            return None;
        }
    }
    None
}

fn resolve_candidate_chain(
    candidate: &Path,
    root: &Path,
    ctx: Option<&LiveResolveContext<'_>>,
    visited: &mut HashSet<String>,
    depth: usize,
) -> (bool, bool, bool, PathBuf) {
    let cand = normalized(candidate);
    if depth > 24 {
        return (false, false, false, cand);
    }

    let (ex, is_d, is_f, link_t) = inspect_node_for_chain(&cand, ctx);
    if ex && link_t.is_none() {
        return (true, is_d, is_f, cand);
    }

    if let Some(target) = link_t {
        let key = cache_key(&cand);
        if !visited.insert(key.clone()) {
            return (false, false, false, cand);
        }
        let norm_lt = target.replace('\\', "/");
        let next_cands = build_target_candidates(&cand, norm_lt.trim(), root);
        let mut best_missing: Option<PathBuf> = None;
        for nc in next_cands {
            if cache_key(&nc) == key {
                continue;
            }
            let (sub_ex, sub_d, sub_f, sub_res) =
                resolve_candidate_chain(&nc, root, ctx, visited, depth + 1);
            if sub_ex {
                visited.remove(&key);
                return (true, sub_d, sub_f, sub_res);
            }
            if best_missing.is_none() {
                best_missing = Some(sub_res);
            }
        }
        visited.remove(&key);
        if fs::symlink_metadata(&cand)
            .map(|m| m.file_type().is_symlink())
            .unwrap_or(false)
        {
            if let Ok(st) = fs::metadata(&cand) {
                let real = fs::canonicalize(&cand).unwrap_or_else(|_| cand.clone());
                return (true, st.is_dir(), st.is_file(), normalized(&real));
            }
        }
        return (false, false, false, best_missing.unwrap_or(cand));
    }

    // Walk parent components when direct path doesn't exist (detects intermediate symlinks).
    let Some((walk_root, parts)) = strip_walk_prefix(&cand, root) else {
        return (false, false, false, cand);
    };
    let cand_key = cache_key(&cand);
    for i in 0..parts.len().saturating_sub(1) {
        let mut cur = walk_root.clone();
        for seg in &parts[..=i] {
            cur.push(seg);
        }
        let cur = normalized(&cur);
        let (c_ex, c_dir, _c_file, c_link) = inspect_node_for_chain(&cur, ctx);
        if !c_ex {
            break;
        }
        if c_dir && c_link.is_none() {
            continue;
        }
        let Some(link_target) = c_link else {
            break;
        };
        let key = cache_key(&cur);
        if !visited.insert(key.clone()) {
            return (false, false, false, cand);
        }
        let norm_lt = link_target.replace('\\', "/");
        let next_bases = build_target_candidates(&cur, norm_lt.trim(), root);
        let tail = &parts[i + 1..];
        let mut best_missing: Option<PathBuf> = None;
        for nb in next_bases {
            let mut combined = nb;
            for seg in tail {
                combined.push(seg);
            }
            let combined = normalized(&combined);
            if cache_key(&combined) == cand_key {
                continue;
            }
            let (sub_ex, sub_d, sub_f, sub_res) =
                resolve_candidate_chain(&combined, root, ctx, visited, depth + 1);
            if sub_ex {
                visited.remove(&key);
                return (true, sub_d, sub_f, sub_res);
            }
            if best_missing.is_none() {
                best_missing = Some(sub_res);
            }
        }
        visited.remove(&key);
        return (false, false, false, best_missing.unwrap_or(cand));
    }

    (false, false, false, cand)
}

fn resolve_target_from_string(
    path: &Path,
    target: String,
    root: &Path,
    local_cache: &mut TargetCache,
    ctx: Option<&LiveResolveContext<'_>>,
) -> Link {
    let norm_target = target.replace('\\', "/");
    let clean = norm_target.trim_start_matches('/');
    let candidates = build_target_candidates(path, norm_target.trim(), root);
    let norm_file = normalized(path);
    let norm_file_key = cache_key(&norm_file);
    let mut visited = HashSet::new();
    visited.insert(norm_file_key);

    let mut resolved = None;
    let mut fallback_resolved: Option<PathBuf> = None;
    let (mut exists, mut is_dir, mut is_file) = (false, false, false);
    for candidate in &candidates {
        if *candidate == norm_file {
            continue;
        }
        let info = if let Some(cached) = local_cache.get(candidate) {
            cached.clone()
        } else {
            let computed = resolve_candidate_chain(candidate, root, ctx, &mut visited, 0);
            local_cache.insert(candidate.clone(), computed.clone());
            computed
        };
        if info.0 {
            resolved = Some(info.3);
            (exists, is_dir, is_file) = (true, info.1, info.2);
            break;
        }
        if fallback_resolved.is_none() {
            fallback_resolved = Some(info.3);
        } else if info.3 != *candidate && fallback_resolved.as_ref().is_some_and(|prev| candidates.contains(prev)) {
            fallback_resolved = Some(info.3);
        }
    }
    let resolved = resolved.unwrap_or_else(|| {
        fallback_resolved.unwrap_or_else(|| {
            candidates
                .iter()
                .find(|path| **path != norm_file)
                .or_else(|| candidates.first())
                .cloned()
                .unwrap_or_else(|| normalized(&root.join(clean)))
        })
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
    Link {
        file: path_string(path),
        target,
        resolved: path_string(&resolved),
        exists,
        is_dir,
        is_file,
    }
}

#[allow(dead_code)]
fn lookup_target_metadata(
    candidate: &Path,
    ctx: Option<&LiveResolveContext<'_>>,
) -> (bool, bool, bool) {
    if let Some(ctx) = ctx {
        let key = cache_key(candidate);
        if let Some(siblings) = ctx.siblings {
            if let Some(info) = siblings.get(&key) {
                return *info;
            }
        }
        if let Ok(guard) = ctx.shared_targets.read() {
            if let Some(info) = guard.get(&key) {
                return *info;
            }
        }
        // Negative parent pruning: if parent directory was already observed
        // missing in this scan, any child under it is also missing.
        let parent_key = candidate.parent().map(cache_key);
        if let Some(ref pkey) = parent_key {
            if let Ok(guard) = ctx.missing_parents.read() {
                if guard.contains(pkey) {
                    return (false, false, false);
                }
            }
        }
        let info = match fs::metadata(candidate) {
            Ok(meta) => (true, meta.is_dir(), meta.is_file()),
            Err(error) => {
                if error.kind() == io::ErrorKind::NotFound {
                    if let (Some(parent), Some(pkey)) = (candidate.parent(), parent_key) {
                        if fs::metadata(parent).is_err() {
                            if let Ok(mut guard) = ctx.missing_parents.write() {
                                guard.insert(pkey);
                            }
                        }
                    }
                }
                (false, false, false)
            }
        };
        if let Ok(mut guard) = ctx.shared_targets.write() {
            guard.insert(key, info);
        }
        return info;
    }
    fs::metadata(candidate)
        .map(|info| (true, info.is_dir(), info.is_file()))
        .unwrap_or((false, false, false))
}

#[allow(dead_code)]
fn resolve(path: &Path, root: &Path, cache: &mut TargetCache) -> Option<Link> {
    let target = read_placeholder_target_uncached(path)?;
    Some(resolve_target_from_string(path, target, root, cache, None))
}

#[allow(dead_code)]
fn resolve_slice(paths: &[PathBuf], root: &Path) -> Vec<Option<Link>> {
    let mut cache = TargetCache::new();
    paths
        .iter()
        .map(|path| resolve(path, root, &mut cache))
        .collect()
}

struct CandidateMeta {
    path: PathBuf,
    size_mtime: Option<(u64, i128)>,
}

fn batch_resolve_candidates(
    candidates: &[CandidateMeta],
    root: &Path,
    siblings: Option<&HashMap<String, (bool, bool, bool)>>,
    use_cache: bool,
) -> Vec<Option<Link>> {
    if candidates.is_empty() {
        return Vec::new();
    }
    let now_stamp_ns = now_ns();
    let shared_targets = RwLock::new(HashMap::new());
    let missing_parents = RwLock::new(HashSet::new());
    let ctx = LiveResolveContext {
        siblings,
        shared_targets: &shared_targets,
        missing_parents: &missing_parents,
    };
    if candidates.len() < 32 {
        let mut local_cache = TargetCache::new();
        return candidates
            .iter()
            .map(|item| {
                let target = read_placeholder_target_cached(
                    &item.path,
                    item.size_mtime,
                    now_stamp_ns,
                    use_cache,
                )?;
                Some(resolve_target_from_string(
                    &item.path,
                    target,
                    root,
                    &mut local_cache,
                    Some(&ctx),
                ))
            })
            .collect();
    }
    candidates
        .par_iter()
        .map_init(TargetCache::new, |local_cache, item| {
            let target = read_placeholder_target_cached(
                &item.path,
                item.size_mtime,
                now_stamp_ns,
                use_cache,
            )?;
            Some(resolve_target_from_string(
                &item.path,
                target,
                root,
                local_cache,
                Some(&ctx),
            ))
        })
        .collect()
}

fn batch_resolve(paths: &[PathBuf], root: &Path) -> Vec<Option<Link>> {
    let candidates: Vec<CandidateMeta> = paths
        .iter()
        .map(|path| CandidateMeta {
            path: path.clone(),
            size_mtime: None,
        })
        .collect();
    batch_resolve_candidates(&candidates, root, None, false)
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
pub(crate) struct Entry {
    name: String,
    path: String,
    is_dir: bool,
    size: Option<u64>,
    mtime_ns: Option<i128>,
    symlink: Option<Link>,
    magic: String,
    read_only: bool,
}

pub(crate) struct RawEntry {
    pub path: PathBuf,
    pub is_dir: bool,
    pub is_native_link: bool,
    pub name: String,
    pub size: Option<u64>,
    pub mtime_ns: Option<i128>,
    pub read_only: bool,
}

pub(crate) fn entry_mtime_ns(info: &fs::Metadata) -> Option<i128> {
    mtime_ns(info)
}

fn entries(path: &Path, root: &Path, include_magic: bool) -> io::Result<Vec<Entry>> {
    let mut raw = Vec::new();
    for entry in fs::read_dir(path)? {
        let entry = entry?;
        let entry_path = entry.path();
        let kind = entry.file_type().ok();
        let is_dir = kind.as_ref().map(fs::FileType::is_dir).unwrap_or(false);
        let is_native_link = kind.as_ref().map(fs::FileType::is_symlink).unwrap_or(false);
        let name = entry.file_name().to_string_lossy().into_owned();
        let (size, stamp, read_only) = if is_dir {
            (Some(0), Some(0), false)
        } else {
            // On Windows, DirEntry::metadata() reads directly from WIN32_FIND_DATAW
            // returned by FindFirstFileW/FindNextFileW with zero extra syscalls.
            let info = entry.metadata().ok();
            let ro = info.as_ref().map(|m| m.permissions().readonly()).unwrap_or(false);
            (info.as_ref().map(fs::Metadata::len), info.as_ref().and_then(mtime_ns), ro)
        };
        raw.push(RawEntry {
            path: entry_path,
            is_dir,
            is_native_link,
            name,
            size,
            mtime_ns: stamp,
            read_only,
        });
    }
    enrich_raw(raw, root, include_magic)
}

pub(crate) fn enrich_raw(
    raw: impl IntoIterator<Item = RawEntry>,
    root: &Path,
    include_magic: bool,
) -> io::Result<Vec<Entry>> {
    let mut output = Vec::new();
    let mut sibling_map = HashMap::new();
    let mut candidate_indices = Vec::new();
    let mut candidates = Vec::new();
    for item in raw {
        let RawEntry {
            path: entry_path,
            is_dir,
            is_native_link,
            name,
            size,
            mtime_ns: stamp,
            read_only,
        } = item;
        if !is_native_link {
            let norm_key = cache_key(&normalized(&entry_path));
            sibling_map.insert(norm_key, (true, is_dir, !is_dir));
        }
        let magic = if include_magic && !is_dir {
            let low = name.to_lowercase();
            if low.ends_with(".img")
                || low.ends_with(".bin")
                || low.ends_with(".ozip")
                || low.ends_with(".ofp")
                || low.ends_with(".ops")
                || low.ends_with(".kdz")
                || low.ends_with(".dz")
                || low.ends_with(".app")
                || low.ends_with(".pac")
                || low.ends_with(".cpb")
                || low.ends_with(".romfs")
                || low.ends_with(".squashfs")
                || low.ends_with(".sqsh")
            {
                read_magic(&entry_path)
            } else {
                String::new()
            }
        } else {
            String::new()
        };
        if !is_dir && size.is_some_and(|s| s <= 512) {
            candidate_indices.push(output.len());
            candidates.push(CandidateMeta {
                path: entry_path.clone(),
                size_mtime: size.zip(stamp),
            });
        }
        output.push(Entry {
            name,
            path: path_string(&entry_path),
            is_dir,
            size,
            mtime_ns: if is_dir { Some(0) } else { stamp },
            symlink: None,
            magic,
            read_only,
        });
    }
    let links = batch_resolve_candidates(&candidates, root, Some(&sibling_map), true);
    for (idx, link) in candidate_indices.into_iter().zip(links) {
        output[idx].symlink = link;
    }
    output.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(output)
}

#[allow(dead_code)]
pub(crate) fn enrich_paths(paths: impl IntoIterator<Item=(PathBuf, bool, String)>, root: &Path, include_magic: bool) -> io::Result<Vec<Entry>> {
    let raw = paths.into_iter().map(|(entry_path, is_dir, name)| {
        let info = if is_dir { None } else { fs::symlink_metadata(&entry_path).ok() };
        let is_native_link = info.as_ref().map(|m| m.file_type().is_symlink()).unwrap_or(false);
        let size = if is_dir { Some(0) } else { info.as_ref().map(fs::Metadata::len) };
        let stamp = if is_dir { Some(0) } else { info.as_ref().and_then(mtime_ns) };
        let read_only = if is_dir { false } else { info.as_ref().map(|m| m.permissions().readonly()).unwrap_or(false) };
        RawEntry {
            path: entry_path,
            is_dir,
            is_native_link,
            name,
            size,
            mtime_ns: stamp,
            read_only,
        }
    });
    enrich_raw(raw, root, include_magic)
}

#[pyfunction]
#[pyo3(signature = (path, root, include_magic = true))]
pub(crate) fn directory_entries_json(
    py: Python<'_>,
    path: PathBuf,
    root: PathBuf,
    include_magic: bool,
) -> PyResult<String> {
    let output = py.detach(move || entries(&path, &root, include_magic))?;
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

    #[test]
    fn entries_golden_parity_and_live_mutations() {
        let unique = format!(
            "rak_parity_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let test_dir = std::env::temp_dir().join(unique);
        let root = test_dir.join("root");
        let bin = root.join("system").join("bin");
        let etc = root.join("vendor").join("etc");
        fs::create_dir_all(&bin).unwrap();
        fs::create_dir_all(&etc).unwrap();

        let toybox = bin.join("toybox");
        fs::write(&toybox, vec![b'X'; 4096]).unwrap();
        let sub_dir = bin.join("sub");
        fs::create_dir_all(&sub_dir).unwrap();
        let prop = etc.join("build.prop");
        fs::write(&prop, b"ro.test=1\n").unwrap();

        // Populate a realistic Android /system/bin mix: sibling file links,
        // sibling folder links, cross-folder SAR links, broken file/dir links,
        // plain small files, and large files.
        for i in 0..80 {
            let p = bin.join(format!("item_{i:03}"));
            match i % 6 {
                0 => fs::write(&p, b"!<symlink>toybox\0").unwrap(),
                1 => fs::write(&p, b"!<symlink>sub\0").unwrap(),
                2 => fs::write(&p, b"!<symlink>/vendor/etc/build.prop\0").unwrap(),
                3 => fs::write(&p, b"!<symlink>/vendor/etc\0").unwrap(),
                4 => fs::write(&p, b"!<symlink>/missing/bin/tool\0").unwrap(),
                _ => fs::write(&p, b"plain small text file").unwrap(),
            }
        }
        let broken_dir = bin.join("broken_dir_link");
        fs::write(&broken_dir, b"!<symlink>/missing_dir/\0").unwrap();

        // Compare optimized entries() against reference sequential resolve_slice()
        let first = entries(&bin, &root, false).unwrap();
        let second = entries(&bin, &root, false).unwrap();
        assert_eq!(
            serde_json::to_string(&first).unwrap(),
            serde_json::to_string(&second).unwrap()
        );

        let file_paths: Vec<PathBuf> = first
            .iter()
            .filter(|e| !e.is_dir && e.size.is_some_and(|s| s <= 512))
            .map(|e| PathBuf::from(&e.path))
            .collect();
        let reference = resolve_slice(&file_paths, &root);
        let actual: Vec<Option<Link>> = first
            .iter()
            .filter(|e| !e.is_dir && e.size.is_some_and(|s| s <= 512))
            .map(|e| e.symlink.clone())
            .collect();
        assert_eq!(
            serde_json::to_string(&actual).unwrap(),
            serde_json::to_string(&reference).unwrap()
        );

        // Live target deletion must immediately flip exists -> false
        fs::remove_file(&prop).unwrap();
        let after_delete = entries(&bin, &root, false).unwrap();
        let cross_link = after_delete.iter().find(|e| e.name == "item_002").unwrap();
        assert!(!cross_link.symlink.as_ref().unwrap().exists);

        // Rapid same-size retarget within racy window must not serve stale target
        let retarget = bin.join("item_000");
        fs::write(&retarget, b"!<symlink>sub___\0").unwrap();
        let after_retarget = entries(&bin, &root, false).unwrap();
        let updated = after_retarget.iter().find(|e| e.name == "item_000").unwrap();
        assert_eq!(updated.symlink.as_ref().unwrap().target, "sub___");

        let _ = fs::remove_dir_all(&test_dir);
    }

    #[test]
    fn chained_and_intermediate_symlink_resolution_and_cycle_guard() {
        let unique = format!(
            "rak_chain_{}_{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let test_dir = std::env::temp_dir().join(unique);
        let root = test_dir.join("root");
        let sys_odm = root.join("system").join("odm");
        let vendor_dir = root.join("vendor");
        let real_odm_app = root.join("odm").join("app");
        let real_odm_bin = root.join("odm").join("bin");
        fs::create_dir_all(&sys_odm).unwrap();
        fs::create_dir_all(&vendor_dir).unwrap();
        fs::create_dir_all(&real_odm_app).unwrap();
        fs::create_dir_all(&real_odm_bin).unwrap();
        fs::write(real_odm_app.join("App.apk"), b"APK").unwrap();
        let real_toybox = real_odm_bin.join("toybox");
        fs::write(&real_toybox, vec![b'T'; 1024]).unwrap();

        // vendor/odm -> /odm, system/odm/app -> /vendor/odm/app (intermediate symlink hop!)
        fs::write(vendor_dir.join("odm"), b"!<symlink>/odm\0").unwrap();
        let app_link = sys_odm.join("app");
        fs::write(&app_link, b"!<symlink>/vendor/odm/app\0").unwrap();

        // Direct symlink-to-symlink file chain:
        // system/sh -> /vendor/bin_link, vendor/bin_link -> /odm/bin/toolbox, odm/bin/toolbox -> toybox
        fs::write(vendor_dir.join("bin_link"), b"!<symlink>/odm/bin/toolbox\0").unwrap();
        fs::write(real_odm_bin.join("toolbox"), b"!<symlink>toybox\0").unwrap();
        let sh_link = root.join("system").join("sh");
        fs::write(&sh_link, b"!<symlink>/vendor/bin_link\0").unwrap();

        // Symlink cycle guard:
        fs::write(root.join("system").join("loop_a"), b"!<symlink>/vendor/loop_b\0").unwrap();
        fs::write(vendor_dir.join("loop_b"), b"!<symlink>/system/loop_a\0").unwrap();

        let sys_odm_entries = entries(&sys_odm, &root, false).unwrap();
        let app_entry = sys_odm_entries.iter().find(|e| e.name == "app").unwrap();
        let app_sym = app_entry.symlink.as_ref().unwrap();
        assert!(app_sym.exists);
        assert!(app_sym.is_dir);
        assert!(!app_sym.is_file);
        assert_eq!(PathBuf::from(&app_sym.resolved), normalized(&real_odm_app));

        let sys_entries = entries(&root.join("system"), &root, false).unwrap();
        let sh_sym = sys_entries.iter().find(|e| e.name == "sh").unwrap().symlink.as_ref().unwrap();
        assert!(sh_sym.exists);
        assert!(sh_sym.is_file);
        assert!(!sh_sym.is_dir);
        assert_eq!(PathBuf::from(&sh_sym.resolved), normalized(&real_toybox));

        let loop_sym = sys_entries.iter().find(|e| e.name == "loop_a").unwrap().symlink.as_ref().unwrap();
        assert!(!loop_sym.exists);

        let _ = fs::remove_dir_all(&test_dir);
    }
}
