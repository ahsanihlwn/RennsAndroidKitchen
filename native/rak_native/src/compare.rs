// SPDX-License-Identifier: MPL-2.0
//! Compare engine for Compare FileSystem.
//!
//! * `CompareJob.scan` walks one side's *logical* tree (source directory plus the
//!   active NeoBranch overlay, minus deleted paths) on a bounded rayon pool.
//! * `CompareJob.content_diff` compares the bytes of files present on both sides.
//! * `diff_lines` produces Python-compatible opcodes with the `similar` crate
//!   (Myers, with a deadline) instead of `difflib.SequenceMatcher`.
//!
//! Python is detached for every disk or CPU heavy call. Progress is published
//! through atomics, so the GUI thread can poll without any callback into Python.

use pyo3::exceptions::{PyInterruptedError, PyOSError, PyValueError};
use pyo3::prelude::*;
use rayon::prelude::*;
use serde::Deserialize;
use similar::{capture_diff_slices_deadline, Algorithm, DiffOp};
use std::collections::{HashMap, HashSet};
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

const DIFF_DEADLINE: Duration = Duration::from_secs(4);
const COMPARE_BUFFER: usize = 1024 * 1024;

fn pool() -> &'static rayon::ThreadPool {
    static POOL: OnceLock<rayon::ThreadPool> = OnceLock::new();
    POOL.get_or_init(|| {
        let cpus = std::thread::available_parallelism().map_or(4, |n| n.get());
        rayon::ThreadPoolBuilder::new()
            .num_threads(cpus.clamp(2, 16))
            .thread_name(|index| format!("rak-compare-{index}"))
            .build()
            .expect("compare thread pool")
    })
}

#[derive(Default)]
struct Shared {
    cancel: AtomicBool,
    scanned: [AtomicU64; 2],
    done: AtomicU64,
    total: AtomicU64,
}

#[derive(Clone)]
struct Node {
    /// Path relative to the compared directory. Directories end with `/`.
    rel: String,
    is_dir: bool,
    size: i64,
    /// Physical location of the winning layer (overlay first, then source).
    phys: PathBuf,
}

#[derive(Deserialize)]
struct SideSpec {
    /// Logical directory that is being compared (source layer).
    base: PathBuf,
    /// Matching directory inside the active branch, when a sparse overlay is active.
    #[serde(default)]
    overlay: Option<PathBuf>,
    /// Project-relative logical path of `base`, `/` separated.
    #[serde(default)]
    rel: String,
    /// Normalised project-relative deleted paths of the active branch.
    #[serde(default)]
    deleted: Vec<String>,
    #[serde(default)]
    casefold: bool,
}

struct Ctx {
    deleted: HashSet<String>,
    casefold: bool,
}

impl Ctx {
    fn fold(&self, text: &str) -> String {
        if self.casefold {
            text.to_lowercase()
        } else {
            text.to_owned()
        }
    }

    /// Same rule as `NeoBranchManager.is_deleted_in_set`: equal to a deleted path or below it.
    fn is_deleted(&self, rel: &str) -> bool {
        if self.deleted.is_empty() || rel.is_empty() {
            return false;
        }
        let rel = self.fold(rel);
        if self.deleted.contains(&rel) {
            return true;
        }
        rel.match_indices('/')
            .any(|(index, _)| self.deleted.contains(&rel[..index]))
    }
}

struct Row {
    name: String,
    is_dir: bool,
    size: i64,
    path: PathBuf,
}

fn list(dir: &Path) -> Option<Vec<Row>> {
    let reader = fs::read_dir(dir).ok()?;
    let mut rows = Vec::new();
    for entry in reader.flatten() {
        let is_dir = entry.file_type().map(|kind| kind.is_dir()).unwrap_or(false);
        let size = if is_dir {
            0
        } else {
            entry.metadata().map_or(-1, |meta| meta.len() as i64)
        };
        rows.push(Row {
            name: entry.file_name().to_string_lossy().into_owned(),
            is_dir,
            size,
            path: entry.path(),
        });
    }
    Some(rows)
}

struct Task {
    source: Option<PathBuf>,
    overlay: Option<PathBuf>,
    rel_project: String,
    prefix: String,
}

fn join_rel(parent: &str, name: &str) -> String {
    if parent.is_empty() {
        name.to_owned()
    } else {
        format!("{parent}/{name}")
    }
}

fn scan_dir(
    ctx: &Ctx,
    task: Task,
    out: &Mutex<Vec<Node>>,
    shared: &Shared,
    side: usize,
) {
    if shared.cancel.load(Ordering::Relaxed) {
        return;
    }
    let source_rows = match &task.source {
        Some(path) if !ctx.is_deleted(&task.rel_project) => list(path),
        _ => None,
    };
    let overlay_rows = task.overlay.as_deref().and_then(list);

    let mut merged: HashMap<String, (Option<Row>, Option<Row>)> = HashMap::new();
    for row in source_rows.into_iter().flatten() {
        if ctx.is_deleted(&join_rel(&task.rel_project, &row.name)) {
            continue;
        }
        let key = ctx.fold(&row.name);
        merged.entry(key).or_default().0 = Some(row);
    }
    for row in overlay_rows.into_iter().flatten() {
        let key = ctx.fold(&row.name);
        merged.entry(key).or_default().1 = Some(row);
    }

    let mut local = Vec::with_capacity(merged.len());
    let mut children = Vec::new();
    for (_key, (source, overlay)) in merged {
        let winner = match (&overlay, &source) {
            (Some(row), _) | (None, Some(row)) => row,
            (None, None) => continue,
        };
        let rel = format!("{}{}", task.prefix, winner.name);
        if winner.is_dir {
            local.push(Node {
                rel: format!("{rel}/"),
                is_dir: true,
                size: 0,
                phys: winner.path.clone(),
            });
            children.push(Task {
                source: source.as_ref().filter(|row| row.is_dir).map(|row| row.path.clone()),
                overlay: overlay.as_ref().filter(|row| row.is_dir).map(|row| row.path.clone()),
                rel_project: join_rel(&task.rel_project, &winner.name),
                prefix: format!("{rel}/"),
            });
        } else {
            local.push(Node {
                rel,
                is_dir: false,
                size: winner.size,
                phys: winner.path.clone(),
            });
        }
    }
    shared.scanned[side].fetch_add(local.len() as u64, Ordering::Relaxed);
    out.lock().unwrap().extend(local);
    children
        .into_par_iter()
        .for_each(|child| scan_dir(ctx, child, out, shared, side));
}

fn scan_side(spec: SideSpec, shared: &Shared, side: usize) -> io::Result<Vec<Node>> {
    if !spec.base.is_dir() && spec.overlay.as_deref().is_none_or(|path| !path.is_dir()) {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            format!("not a directory: {}", spec.base.display()),
        ));
    }
    let ctx = Ctx {
        deleted: spec
            .deleted
            .iter()
            .map(|path| {
                let path = path.replace('\\', "/");
                if spec.casefold {
                    path.to_lowercase()
                } else {
                    path
                }
            })
            .collect(),
        casefold: spec.casefold,
    };
    let out = Mutex::new(Vec::new());
    let task = Task {
        source: Some(spec.base),
        overlay: spec.overlay,
        rel_project: spec.rel.trim_matches('/').to_owned(),
        prefix: String::new(),
    };
    pool().install(|| scan_dir(&ctx, task, &out, shared, side));
    Ok(out.into_inner().unwrap())
}

fn read_full(file: &mut File, buffer: &mut [u8]) -> io::Result<usize> {
    let mut filled = 0;
    while filled < buffer.len() {
        match file.read(&mut buffer[filled..])? {
            0 => break,
            count => filled += count,
        }
    }
    Ok(filled)
}

fn files_differ(a: &Path, b: &Path, size: i64, cancel: &AtomicBool) -> bool {
    if a == b || size == 0 {
        return false;
    }
    let (mut file_a, mut file_b) = match (File::open(a), File::open(b)) {
        (Ok(file_a), Ok(file_b)) => (file_a, file_b),
        // An unreadable file cannot be proven identical.
        _ => return true,
    };
    let length = (size.max(1) as usize).min(COMPARE_BUFFER).max(4096);
    let (mut block_a, mut block_b) = (vec![0u8; length], vec![0u8; length]);
    loop {
        if cancel.load(Ordering::Relaxed) {
            return false;
        }
        let (count_a, count_b) = match (
            read_full(&mut file_a, &mut block_a),
            read_full(&mut file_b, &mut block_b),
        ) {
            (Ok(count_a), Ok(count_b)) => (count_a, count_b),
            _ => return true,
        };
        if count_a != count_b || block_a[..count_a] != block_b[..count_b] {
            return true;
        }
        if count_a == 0 {
            return false;
        }
    }
}

fn shared_differences(
    sides: &[Vec<Node>; 2],
    shared: &Shared,
) -> Vec<(String, i64, i64)> {
    let other: HashMap<&str, &Node> = sides[1]
        .iter()
        .filter(|node| !node.is_dir)
        .map(|node| (node.rel.as_str(), node))
        .collect();
    let pairs: Vec<(&Node, &Node)> = sides[0]
        .iter()
        .filter(|node| !node.is_dir)
        .filter_map(|node| other.get(node.rel.as_str()).map(|peer| (node, *peer)))
        .collect();
    shared.done.store(0, Ordering::Relaxed);
    shared.total.store(pairs.len() as u64, Ordering::Relaxed);
    let mut found: Vec<(String, i64, i64)> = pool().install(|| {
        pairs
            .par_iter()
            .filter_map(|(a, b)| {
                if shared.cancel.load(Ordering::Relaxed) {
                    return None;
                }
                let differs = a.size != b.size || files_differ(&a.phys, &b.phys, a.size, &shared.cancel);
                shared.done.fetch_add(1, Ordering::Relaxed);
                differs.then(|| (a.rel.clone(), a.size, b.size))
            })
            .collect()
    });
    found.sort_by(|left, right| left.0.cmp(&right.0));
    found
}

#[pyclass(frozen)]
pub struct CompareJob {
    shared: Arc<Shared>,
    sides: Mutex<[Vec<Node>; 2]>,
}

#[pymethods]
impl CompareJob {
    #[new]
    fn new() -> Self {
        Self {
            shared: Arc::new(Shared::default()),
            sides: Mutex::new([Vec::new(), Vec::new()]),
        }
    }

    fn cancel(&self) {
        self.shared.cancel.store(true, Ordering::Relaxed);
    }

    /// `(scanned_a, scanned_b, done, total)`; safe to call from any thread at any time.
    fn progress(&self) -> (u64, u64, u64, u64) {
        (
            self.shared.scanned[0].load(Ordering::Relaxed),
            self.shared.scanned[1].load(Ordering::Relaxed),
            self.shared.done.load(Ordering::Relaxed),
            self.shared.total.load(Ordering::Relaxed),
        )
    }

    /// Scan one side (0 = A, 1 = B). Returns `(rel, is_dir, size)` tuples.
    fn scan(&self, py: Python<'_>, side: usize, spec: &str) -> PyResult<Vec<(String, bool, i64)>> {
        if side > 1 {
            return Err(PyValueError::new_err("side must be 0 or 1"));
        }
        let spec: SideSpec = serde_json::from_str(spec)
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
        let shared = Arc::clone(&self.shared);
        let nodes = py
            .detach(move || scan_side(spec, &shared, side))
            .map_err(|error| PyOSError::new_err(error.to_string()))?;
        if self.shared.cancel.load(Ordering::Relaxed) {
            return Err(PyInterruptedError::new_err("Compare cancelled"));
        }
        let rows = nodes
            .iter()
            .map(|node| (node.rel.clone(), node.is_dir, node.size))
            .collect();
        self.sides.lock().unwrap()[side] = nodes;
        Ok(rows)
    }

    /// Files present on both sides whose size or bytes differ: `(rel, size_a, size_b)`.
    fn content_diff(&self, py: Python<'_>) -> PyResult<Vec<(String, i64, i64)>> {
        let shared = Arc::clone(&self.shared);
        let result = py.detach(|| {
            let sides = self.sides.lock().unwrap();
            shared_differences(&sides, &shared)
        });
        if self.shared.cancel.load(Ordering::Relaxed) {
            return Err(PyInterruptedError::new_err("Compare cancelled"));
        }
        Ok(result)
    }

    /// Physical path used for `rel` on a side (overlay first), or `None`.
    fn physical(&self, side: usize, rel: &str) -> Option<String> {
        let sides = self.sides.lock().unwrap();
        sides
            .get(side)?
            .iter()
            .find(|node| node.rel == rel)
            .map(|node| node.phys.to_string_lossy().into_owned())
    }
}

/// Python `readlines()` semantics: universal newlines, kept as `\n`, invalid UTF-8 replaced.
fn split_lines(bytes: &[u8]) -> Vec<String> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut index = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'\n' => {
                lines.push(finish_line(&bytes[start..index]));
                index += 1;
                start = index;
            }
            b'\r' => {
                lines.push(finish_line(&bytes[start..index]));
                index += if bytes.get(index + 1) == Some(&b'\n') { 2 } else { 1 };
                start = index;
            }
            _ => index += 1,
        }
    }
    if start < bytes.len() {
        lines.push(String::from_utf8_lossy(&bytes[start..]).into_owned());
    }
    lines
}

fn finish_line(bytes: &[u8]) -> String {
    let mut line = String::from_utf8_lossy(bytes).into_owned();
    line.push('\n');
    line
}

type Opcode = (&'static str, usize, usize, usize, usize);

fn opcodes(old: &[String], new: &[String]) -> Vec<Opcode> {
    let old_refs: Vec<&str> = old.iter().map(String::as_str).collect();
    let new_refs: Vec<&str> = new.iter().map(String::as_str).collect();
    capture_diff_slices_deadline(
        Algorithm::Myers,
        &old_refs,
        &new_refs,
        Some(Instant::now() + DIFF_DEADLINE),
    )
    .into_iter()
    .map(|op| match op {
        DiffOp::Equal { old_index, new_index, len } => {
            ("equal", old_index, old_index + len, new_index, new_index + len)
        }
        DiffOp::Delete { old_index, old_len, new_index } => {
            ("delete", old_index, old_index + old_len, new_index, new_index)
        }
        DiffOp::Insert { old_index, new_index, new_len } => {
            ("insert", old_index, old_index, new_index, new_index + new_len)
        }
        DiffOp::Replace { old_index, old_len, new_index, new_len } => (
            "replace",
            old_index,
            old_index + old_len,
            new_index,
            new_index + new_len,
        ),
    })
    .collect()
}

/// Read two text files and diff them: `(lines_a, lines_b, opcodes)`.
/// A missing or unreadable file counts as empty, like the Python implementation.
#[pyfunction]
pub fn diff_lines(
    py: Python<'_>,
    path_a: PathBuf,
    path_b: PathBuf,
) -> (Vec<String>, Vec<String>, Vec<Opcode>) {
    py.detach(move || {
        let lines_a = split_lines(&fs::read(&path_a).unwrap_or_default());
        let lines_b = split_lines(&fs::read(&path_b).unwrap_or_default());
        let ops = opcodes(&lines_a, &lines_b);
        (lines_a, lines_b, ops)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;

    fn scratch(label: &str) -> PathBuf {
        static COUNTER: AtomicUsize = AtomicUsize::new(0);
        let path = std::env::temp_dir().join(format!(
            "rak-compare-{label}-{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, Ordering::Relaxed)
        ));
        let _ = fs::remove_dir_all(&path);
        fs::create_dir_all(&path).unwrap();
        path
    }

    fn write(path: &Path, text: &str) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }

    fn scan(base: &Path, overlay: Option<&Path>, rel: &str, deleted: &[&str]) -> HashMap<String, (bool, i64)> {
        let spec = SideSpec {
            base: base.to_path_buf(),
            overlay: overlay.map(Path::to_path_buf),
            rel: rel.into(),
            deleted: deleted.iter().map(|path| path.to_string()).collect(),
            casefold: true,
        };
        scan_side(spec, &Shared::default(), 0)
            .unwrap()
            .into_iter()
            .map(|node| (node.rel, (node.is_dir, node.size)))
            .collect()
    }

    #[test]
    fn plain_scan_lists_files_and_folders_with_sizes() {
        let root = scratch("plain");
        write(&root.join("etc/a.prop"), "abc");
        write(&root.join("bin/tool"), "12345");
        let rows = scan(&root, None, "system", &[]);
        assert_eq!(rows["etc/"], (true, 0));
        assert_eq!(rows["etc/a.prop"], (false, 3));
        assert_eq!(rows["bin/tool"], (false, 5));
        assert_eq!(rows.len(), 4);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn overlay_wins_deleted_paths_hide_source_and_new_files_appear() {
        let root = scratch("overlay");
        let source = root.join("source");
        let branch = root.join("branch");
        write(&source.join("etc/keep.prop"), "keep");
        write(&source.join("etc/changed.prop"), "old");
        write(&source.join("etc/gone.prop"), "gone");
        write(&source.join("lib/libgone.so"), "x");
        write(&branch.join("etc/changed.prop"), "newer!");
        write(&branch.join("etc/added.prop"), "add");
        let rows = scan(&source, Some(&branch), "system", &["system/etc/gone.prop", "SYSTEM/lib"]);
        assert_eq!(rows["etc/changed.prop"], (false, 6));
        assert_eq!(rows["etc/added.prop"], (false, 3));
        assert!(rows.contains_key("etc/keep.prop"));
        assert!(!rows.contains_key("etc/gone.prop"));
        assert!(!rows.contains_key("lib/libgone.so"));
        assert!(!rows.contains_key("lib/"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn overlay_file_replaces_source_directory_without_leaking_children() {
        let root = scratch("replace");
        let source = root.join("source");
        let branch = root.join("branch");
        write(&source.join("data/inner.txt"), "inner");
        write(&branch.join("data"), "now a file");
        let rows = scan(&source, Some(&branch), "system", &[]);
        assert_eq!(rows["data"], (false, 10));
        assert!(!rows.contains_key("data/"));
        assert!(!rows.contains_key("data/inner.txt"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn content_comparison_reports_size_and_byte_differences_only() {
        let root = scratch("content");
        let (a, b) = (root.join("a"), root.join("b"));
        write(&a.join("same.txt"), "same");
        write(&b.join("same.txt"), "same");
        write(&a.join("bytes.txt"), "aaaa");
        write(&b.join("bytes.txt"), "aaab");
        write(&a.join("size.txt"), "short");
        write(&b.join("size.txt"), "longer");
        write(&a.join("only_a.txt"), "x");
        let shared = Shared::default();
        let make = |base: &Path, side| scan_side(
            SideSpec { base: base.into(), overlay: None, rel: "p".into(), deleted: vec![], casefold: false },
            &shared,
            side,
        ).unwrap();
        let sides = [make(&a, 0), make(&b, 1)];
        let found = shared_differences(&sides, &shared);
        let names: Vec<&str> = found.iter().map(|row| row.0.as_str()).collect();
        assert_eq!(names, ["bytes.txt", "size.txt"]);
        assert_eq!(shared.total.load(Ordering::Relaxed), 3);
        assert_eq!(shared.done.load(Ordering::Relaxed), 3);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn cancelled_scan_stops_early() {
        let root = scratch("cancel");
        write(&root.join("a/b.txt"), "x");
        let shared = Shared::default();
        shared.cancel.store(true, Ordering::Relaxed);
        let spec = SideSpec { base: root.clone(), overlay: None, rel: String::new(), deleted: vec![], casefold: false };
        assert!(scan_side(spec, &shared, 0).unwrap().is_empty());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn lines_follow_python_universal_newlines() {
        assert_eq!(split_lines(b"a\r\nb\rc\nd"), ["a\n", "b\n", "c\n", "d"]);
        assert_eq!(split_lines(b""), Vec::<String>::new());
        assert_eq!(split_lines(b"x\n"), ["x\n"]);
        assert_eq!(split_lines(b"\xffz\n"), ["\u{fffd}z\n"]);
    }

    #[test]
    fn opcodes_match_difflib_shape_and_cover_both_sequences() {
        let old: Vec<String> = ["a\n", "b\n", "c\n", "d\n"].map(String::from).to_vec();
        let new: Vec<String> = ["a\n", "B\n", "x\n", "d\n", "e\n"].map(String::from).to_vec();
        let ops = opcodes(&old, &new);
        assert_eq!(ops[0], ("equal", 0, 1, 0, 1));
        assert!(ops.contains(&("replace", 1, 3, 1, 3)));
        assert_eq!(ops.last().unwrap(), &("insert", 4, 4, 4, 5));
        let (mut i, mut j) = (0, 0);
        for (_, i1, i2, j1, j2) in &ops {
            assert_eq!((*i1, *j1), (i, j));
            (i, j) = (*i2, *j2);
        }
        assert_eq!((i, j), (old.len(), new.len()));
    }

    #[test]
    fn identical_and_empty_inputs_diff_cleanly() {
        assert!(opcodes(&[], &[]).is_empty());
        let same: Vec<String> = ["x\n", "y\n"].map(String::from).to_vec();
        assert_eq!(opcodes(&same, &same), [("equal", 0, 2, 0, 2)]);
    }

    #[test]
    fn large_diff_is_fast() {
        let old: Vec<String> = (0..100_000).map(|i| format!("key_{i}=value_{i}\n")).collect();
        let mut new = old.clone();
        for i in (0..100_000).step_by(997) {
            new[i] = format!("key_{i}=CHANGED\n");
        }
        let started = Instant::now();
        let ops = opcodes(&old, &new);
        assert!(started.elapsed() < Duration::from_secs(2));
        assert!(ops.iter().any(|op| op.0 == "replace"));
    }
}
