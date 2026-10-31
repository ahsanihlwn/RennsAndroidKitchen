// SPDX-License-Identifier: MPL-2.0
//! Session path map. Background work never opens file contents or follows links.
use crate::{directory, path_string};
use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;
use serde::Serialize;
use std::collections::{HashMap, HashSet, VecDeque};
use std::fs::{self, ReadDir};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

#[derive(Clone, Serialize)]
pub(crate) struct PathRow {
    pub name: String,
    pub path: PathBuf,
    pub is_dir: bool,
    pub is_link: bool,
}

struct Snapshot {
    rows: Arc<Vec<PathRow>>,
    error: Option<String>,
    stamp: Option<SystemTime>,
}

struct Cursor {
    path: PathBuf,
    generation: u64,
    iterator: Option<ReadDir>,
    rows: Vec<PathRow>,
    descendants: Vec<PathBuf>,
}

impl Cursor {
    fn new(path: PathBuf, generation: u64) -> Self {
        Self { path, generation, iterator: None, rows: Vec::new(), descendants: Vec::new() }
    }

    fn advance(&mut self) -> std::io::Result<bool> {
        if self.iterator.is_none() { self.iterator = Some(fs::read_dir(&self.path)?); }
        let deadline = Instant::now() + Duration::from_millis(4);
        for _ in 0..128 {
            let Some(entry) = self.iterator.as_mut().unwrap().next() else { return Ok(true); };
            let entry = entry?;
            let path = entry.path();
            let kind = entry.file_type()?;
            let is_link = kind.is_symlink() || is_reparse(&entry);
            let is_dir = kind.is_dir();
            if is_dir && !is_link { self.descendants.push(path.clone()); }
            self.rows.push(PathRow { name: entry.file_name().to_string_lossy().into_owned(), path, is_dir, is_link });
            if Instant::now() >= deadline { break; }
        }
        Ok(false)
    }
}

#[cfg(windows)]
fn is_reparse(entry: &fs::DirEntry) -> bool {
    use std::os::windows::fs::FileTypeExt;
    // file_type comes from FindFirst/NextFile; no target resolution or file open.
    entry.file_type().map(|kind| kind.is_symlink_dir() || kind.is_symlink_file()).unwrap_or(true)
        || entry.metadata().map(|info| {
            use std::os::windows::fs::MetadataExt;
            info.file_attributes() & 0x400 != 0
        }).unwrap_or(true)
}

#[cfg(not(windows))]
fn is_reparse(_: &fs::DirEntry) -> bool { false }

#[derive(Serialize)]
struct Completion { path: String, foreground: bool }

struct State {
    snapshots: HashMap<PathBuf, Snapshot>,
    generations: HashMap<PathBuf, u64>,
    cursors: HashMap<PathBuf, Cursor>,
    active: HashSet<PathBuf>,
    priority: HashSet<PathBuf>,
    urgent: VecDeque<PathBuf>,
    background: VecDeque<PathBuf>,
    stopped: bool,
    workers: usize,
    completions: VecDeque<Completion>,
    entries: usize,
}

impl State {
    fn enqueue(&mut self, path: PathBuf, urgent: bool, refresh: bool) {
        if self.stopped || (!refresh && self.snapshots.contains_key(&path)) { return; }
        if refresh || (!self.cursors.contains_key(&path) && !self.active.contains(&path)) {
            self.snapshots.remove(&path);
            let generation = self.generations.entry(path.clone()).or_default();
            *generation += 1;
            self.cursors.insert(path.clone(), Cursor::new(path.clone(), *generation));
        }
        if urgent {
            self.priority.insert(path.clone());
            if !self.urgent.contains(&path) { self.urgent.push_front(path); }
        } else { self.background.push_back(path); }
    }

    fn take(&mut self, foreground: bool) -> Option<Cursor> {
        if !foreground && !self.priority.is_empty() { return None; }
        let queue = if foreground { &mut self.urgent } else { &mut self.background };
        let length = queue.len();
        for _ in 0..length {
            let path = queue.pop_front()?;
            if self.active.contains(&path) { queue.push_back(path); continue; }
            if let Some(cursor) = self.cursors.remove(&path) {
                self.active.insert(path);
                return Some(cursor);
            }
        }
        None
    }
}

struct Shared { state: Mutex<State>, wake: Condvar }

#[cfg(windows)]
fn background_priority() {
    // Windows background mode also lowers I/O priority. Foreground requests
    // run on their own normal-priority worker, without changing the UI thread.
    #[link(name = "kernel32")]
    extern "system" {
        fn GetCurrentThread() -> *mut std::ffi::c_void;
        fn SetThreadPriority(thread: *mut std::ffi::c_void, priority: i32) -> i32;
    }
    unsafe { SetThreadPriority(GetCurrentThread(), 0x00010000); }
}

#[cfg(not(windows))]
fn background_priority() {}

fn worker(shared: Arc<Shared>, foreground: bool) {
    if !foreground { background_priority(); }
    loop {
        let mut state = shared.state.lock().unwrap();
        let mut cursor = loop {
            if state.stopped { state.workers -= 1; shared.wake.notify_all(); return; }
            if let Some(cursor) = state.take(foreground) { break cursor; }
            state = shared.wake.wait(state).unwrap();
        };
        drop(state);
        let result = cursor.advance();
        let stamp = if !matches!(result, Ok(false)) { fs::metadata(&cursor.path).and_then(|info| info.modified()).ok() } else { None };
        let mut state = shared.state.lock().unwrap();
        state.active.remove(&cursor.path);
        if !state.stopped && state.generations.get(&cursor.path) == Some(&cursor.generation) {
            if !matches!(result, Ok(false)) {
                let urgent = state.priority.remove(&cursor.path);
                let path = cursor.path.clone();
                state.entries += cursor.rows.len();
                state.snapshots.insert(path.clone(), Snapshot {
                    rows: Arc::new(cursor.rows), error: result.err().map(|error| error.to_string()),
                    stamp,
                });
                state.completions.push_back(Completion { path: path_string(&path), foreground: urgent });
                if state.completions.len() > 256 { state.completions.pop_front(); }
                for child in cursor.descendants { state.enqueue(child, false, false); }
            } else {
                let path = cursor.path.clone();
                state.cursors.insert(path.clone(), cursor);
                if state.priority.contains(&path) {
                    if !state.urgent.contains(&path) { state.urgent.push_back(path); }
                } else { state.background.push_front(path); }
            }
        }
        shared.wake.notify_all();
    }
}

#[pyclass(frozen)]
pub(crate) struct PathIndex { shared: Arc<Shared> }

impl PathIndex {
    fn rows(&self, path: &Path, refresh: bool) -> std::io::Result<Arc<Vec<PathRow>>> {
        // One directory timestamp validates names cached before an unwatched edit.
        let stamp = fs::metadata(path).and_then(|info| info.modified()).ok();
        let mut state = self.shared.state.lock().unwrap();
        let changed = state.snapshots.get(path).is_some_and(|snapshot| snapshot.stamp != stamp);
        state.enqueue(path.to_owned(), true, refresh || changed);
        self.shared.wake.notify_all();
        loop {
            if state.stopped { return Err(std::io::Error::other("Path index closed")); }
            if let Some(snapshot) = state.snapshots.get(path) {
                if let Some(error) = &snapshot.error { return Err(std::io::Error::other(error.clone())); }
                return Ok(snapshot.rows.clone());
            }
            state = self.shared.wake.wait(state).unwrap();
        }
    }
}

#[pymethods]
impl PathIndex {
    #[new]
    #[pyo3(signature = (roots, background_workers=2))]
    fn new(roots: Vec<PathBuf>, background_workers: usize) -> PyResult<Self> {
        let shared = Arc::new(Shared { state: Mutex::new(State {
            snapshots: HashMap::new(), generations: HashMap::new(), cursors: HashMap::new(),
            active: HashSet::new(), priority: HashSet::new(), urgent: VecDeque::new(), background: VecDeque::new(),
            stopped: false, workers: 0, completions: VecDeque::new(), entries: 0,
        }), wake: Condvar::new() });
        for root in roots { shared.state.lock().unwrap().enqueue(root, false, false); }
        for index in 0..=background_workers.min(3) {
            shared.state.lock().unwrap().workers += 1;
            let state = shared.clone();
            if let Err(error) = thread::Builder::new().name(format!("rak-path-map-{index}")).spawn(move || worker(state, index == 0)) {
                let mut state = shared.state.lock().unwrap();
                state.workers -= 1; state.stopped = true; shared.wake.notify_all();
                return Err(PyOSError::new_err(error.to_string()));
            }
        }
        Ok(Self { shared })
    }

    #[pyo3(signature = (path, refresh=false))]
    fn prioritize(&self, path: PathBuf, refresh: bool) {
        self.shared.state.lock().unwrap().enqueue(path, true, refresh);
        self.shared.wake.notify_all();
    }

    #[pyo3(signature = (path, root, refresh=false))]
    fn entries_json(&self, py: Python<'_>, path: PathBuf, root: PathBuf, refresh: bool) -> PyResult<String> {
        py.detach(|| {
            let rows = self.rows(&path, refresh)?;
            let entries = directory::enrich_paths(rows.iter().map(|row| (row.path.clone(), row.is_dir, row.name.clone())), &root)?;
            serde_json::to_string(&entries).map_err(std::io::Error::other)
        }).map_err(|error| PyOSError::new_err(error.to_string()))
    }

    #[pyo3(signature = (path, refresh=false))]
    fn paths_json(&self, py: Python<'_>, path: PathBuf, refresh: bool) -> PyResult<String> {
        py.detach(|| self.rows(&path, refresh).and_then(|rows| serde_json::to_string(&*rows).map_err(std::io::Error::other)))
            .map_err(|error| PyOSError::new_err(error.to_string()))
    }

    fn stats_json(&self) -> String {
        let state = self.shared.state.lock().unwrap();
        serde_json::json!({"directories":state.snapshots.len(),"entries":state.entries,
            "pending":state.cursors.len()+state.active.len(),"priority":state.priority.len(),
            "complete":state.cursors.is_empty() && state.active.is_empty(),"closed":state.stopped,
            "workers":state.workers,"recent":state.completions}).to_string()
    }

    fn close(&self) {
        let mut state = self.shared.state.lock().unwrap();
        state.stopped = true; state.cursors.clear(); state.background.clear(); state.urgent.clear();
        self.shared.wake.notify_all();
    }

    #[pyo3(signature = (timeout_ms=1000))]
    fn wait_closed(&self, py: Python<'_>, timeout_ms: u64) -> bool {
        py.detach(|| {
            let state = self.shared.state.lock().unwrap();
            let (state, _) = self.shared.wake.wait_timeout_while(state, Duration::from_millis(timeout_ms), |state| state.workers != 0).unwrap();
            state.workers == 0
        })
    }
}

impl Drop for PathIndex { fn drop(&mut self) { self.close(); } }
