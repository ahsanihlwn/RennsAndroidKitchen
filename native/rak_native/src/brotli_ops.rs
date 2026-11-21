// SPDX-License-Identifier: MPL-2.0
//! Native in-process Brotli compression, decompression, and streaming reader backed by `brotli_rs`.

use std::fs::{self, File};
use std::io::{self, BufReader, BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use brotli_rs::common::{
    BROTLI_LARGE_MAX_WBITS, BROTLI_MAX_QUALITY, BROTLI_MAX_WINDOW_BITS, BROTLI_MIN_QUALITY,
    BROTLI_MIN_WINDOW_BITS, brotli_max_backward_limit,
};
use brotli_rs::dec::{BrotliDecoderParameter, BrotliDecoderResult, BrotliDecoderState};
use brotli_rs::enc::{
    BrotliEncoderMode, BrotliEncoderOperation, BrotliEncoderParameter, BrotliEncoderState,
};
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use serde::Serialize;

const FILE_BUFFER_SIZE: usize = 1 << 19; // 512 KiB
const READER_IN_BUFFER_SIZE: usize = 1 << 18; // 256 KiB
const READER_OUT_CHUNK_SIZE: usize = 1 << 18; // 256 KiB
const DEFAULT_LGWIN: i32 = 24;

#[derive(Serialize)]
struct BrotliFileStatsJson {
    input_path: String,
    output_path: String,
    bytes_in: u64,
    bytes_out: u64,
}

fn abs_path(path: &Path) -> Result<PathBuf, String> {
    std::path::absolute(path).map_err(|e| format!("cannot resolve path {}: {e}", path.display()))
}

fn paths_refer_to_same_file(a: &Path, b: &Path) -> bool {
    if let (Ok(ca), Ok(cb)) = (fs::canonicalize(a), fs::canonicalize(b)) {
        return ca == cb;
    }
    #[cfg(windows)]
    {
        a.to_string_lossy()
            .eq_ignore_ascii_case(&b.to_string_lossy())
    }
    #[cfg(not(windows))]
    {
        a == b
    }
}

fn parse_mode(mode: &str) -> Result<BrotliEncoderMode, String> {
    match mode.trim().to_ascii_lowercase().as_str() {
        "generic" => Ok(BrotliEncoderMode::Generic),
        "text" => Ok(BrotliEncoderMode::Text),
        "font" => Ok(BrotliEncoderMode::Font),
        other => Err(format!(
            "invalid brotli mode '{other}'; expected generic, text, or font"
        )),
    }
}

fn validate_quality(quality: i32) -> Result<u32, String> {
    if !(BROTLI_MIN_QUALITY..=BROTLI_MAX_QUALITY).contains(&quality) {
        return Err(format!(
            "invalid brotli quality {quality}; expected {BROTLI_MIN_QUALITY}..={BROTLI_MAX_QUALITY}"
        ));
    }
    Ok(quality as u32)
}

fn resolve_lgwin(lgwin: Option<i32>, size_hint: Option<u64>) -> Result<(u32, bool), String> {
    match lgwin {
        Some(w) => {
            let max_w = BROTLI_LARGE_MAX_WBITS as i32;
            if !(BROTLI_MIN_WINDOW_BITS..=max_w).contains(&w) {
                return Err(format!(
                    "invalid brotli lgwin {w}; expected {BROTLI_MIN_WINDOW_BITS}..={max_w}"
                ));
            }
            Ok((w as u32, w > BROTLI_MAX_WINDOW_BITS))
        }
        None => {
            let mut w = DEFAULT_LGWIN as u32;
            if let Some(len) = size_hint {
                w = BROTLI_MIN_WINDOW_BITS as u32;
                while (brotli_max_backward_limit(w) as u64) < len {
                    w += 1;
                    if w == BROTLI_MAX_WINDOW_BITS as u32 {
                        break;
                    }
                }
            }
            Ok((w, false))
        }
    }
}

fn build_encoder(
    quality: u32,
    lgwin: u32,
    large_window: bool,
    mode: BrotliEncoderMode,
    size_hint: Option<u64>,
) -> BrotliEncoderState {
    let mut state = BrotliEncoderState::new();
    state.set_parameter(BrotliEncoderParameter::Mode, mode as u32);
    state.set_parameter(BrotliEncoderParameter::Quality, quality);
    if large_window {
        state.set_parameter(BrotliEncoderParameter::LargeWindow, 1);
    }
    state.set_parameter(BrotliEncoderParameter::Lgwin, lgwin);
    if let Some(len) = size_hint {
        if len > 0 {
            let hint = len.min(1u64 << 30) as u32;
            state.set_parameter(BrotliEncoderParameter::SizeHint, hint);
        }
    }
    state
}

fn make_decoder() -> BrotliDecoderState {
    let mut state = BrotliDecoderState::new();
    state.set_parameter(BrotliDecoderParameter::LargeWindow, 1);
    state
}

fn compress_stream_to_writer<R: Read, W: Write>(
    mut reader: R,
    mut writer: W,
    quality: u32,
    lgwin: u32,
    large_window: bool,
    mode: BrotliEncoderMode,
    size_hint: Option<u64>,
) -> Result<(u64, u64), String> {
    let mut state = build_encoder(quality, lgwin, large_window, mode, size_hint);
    let mut in_buf = vec![0u8; FILE_BUFFER_SIZE];
    let mut out_buf = vec![0u8; FILE_BUFFER_SIZE];
    let mut in_pos = 0usize;
    let mut in_len = 0usize;
    let mut total_in = 0u64;
    let mut total_out = 0u64;
    let mut is_eof = false;

    loop {
        if in_pos >= in_len && !is_eof {
            match reader.read(&mut in_buf) {
                Ok(0) => {
                    is_eof = true;
                    in_pos = 0;
                    in_len = 0;
                }
                Ok(n) => {
                    in_pos = 0;
                    in_len = n;
                    total_in += n as u64;
                }
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(format!("failed to read brotli input: {e}")),
            }
        }

        let op = if is_eof {
            BrotliEncoderOperation::Finish
        } else {
            BrotliEncoderOperation::Process
        };
        let mut out_pos = 0usize;
        if !state.compress_stream(
            op,
            &in_buf[..in_len],
            &mut in_pos,
            &mut out_buf,
            &mut out_pos,
            None,
        ) {
            return Err("brotli compression failed".to_string());
        }
        if out_pos > 0 {
            total_out += out_pos as u64;
            writer
                .write_all(&out_buf[..out_pos])
                .map_err(|e| format!("failed to write brotli output: {e}"))?;
        }
        if state.is_finished() {
            writer
                .flush()
                .map_err(|e| format!("failed to flush brotli output: {e}"))?;
            return Ok((total_in, total_out));
        }
    }
}

fn decompress_stream_with_sink<R: Read, F: FnMut(&[u8]) -> Result<(), String>>(
    mut reader: R,
    allow_concatenated: bool,
    mut sink: F,
) -> Result<(u64, u64), String> {
    let mut state = make_decoder();
    let mut in_buf = vec![0u8; FILE_BUFFER_SIZE];
    let mut out_buf = vec![0u8; FILE_BUFFER_SIZE];
    let mut in_pos = 0usize;
    let mut in_len = 0usize;
    let mut total_in = 0u64;
    let mut total_out = 0u64;
    let mut eof = false;
    let mut result = BrotliDecoderResult::NeedsMoreInput;

    loop {
        match result {
            BrotliDecoderResult::NeedsMoreInput => {
                if eof {
                    return Err("corrupt brotli input: truncated stream".to_string());
                }
                if in_pos < in_len {
                    let rem = in_len - in_pos;
                    in_buf.copy_within(in_pos..in_len, 0);
                    in_pos = 0;
                    in_len = rem;
                } else {
                    in_pos = 0;
                    in_len = 0;
                }
                loop {
                    match reader.read(&mut in_buf[in_len..]) {
                        Ok(0) => {
                            if in_len == 0 {
                                return Err("corrupt brotli input: truncated stream".to_string());
                            }
                            eof = true;
                            break;
                        }
                        Ok(n) => {
                            in_len += n;
                            total_in += n as u64;
                            break;
                        }
                        Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                        Err(e) => return Err(format!("failed to read brotli input: {e}")),
                    }
                }
            }
            BrotliDecoderResult::NeedsMoreOutput => {}
            BrotliDecoderResult::Success => {
                let mut has_more_input = in_pos < in_len;
                if !has_more_input && !eof {
                    loop {
                        match reader.read(&mut in_buf[..1]) {
                            Ok(0) => {
                                eof = true;
                                break;
                            }
                            Ok(n) => {
                                in_pos = 0;
                                in_len = n;
                                total_in += n as u64;
                                has_more_input = true;
                                break;
                            }
                            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                            Err(e) => return Err(format!("failed to read brotli input: {e}")),
                        }
                    }
                }
                if has_more_input {
                    if allow_concatenated {
                        state = make_decoder();
                    } else {
                        return Err("corrupt brotli input: extra trailing data".to_string());
                    }
                } else {
                    return Ok((total_in, total_out));
                }
            }
            BrotliDecoderResult::Error => {
                return Err(format!(
                    "corrupt brotli input: {}",
                    state.error_code.as_str()
                ));
            }
        }

        let mut out_pos = 0usize;
        result = state.decompress_stream(
            &in_buf[..in_len],
            &mut in_pos,
            &mut out_buf,
            &mut out_pos,
            None,
        );
        if out_pos > 0 {
            total_out += out_pos as u64;
            sink(&out_buf[..out_pos])?;
        }
    }
}

/// Compress `input_path` to `output_path` using native pure-Rust Brotli.
#[pyfunction]
#[pyo3(signature = (input_path, output_path, quality=1, lgwin=None, mode="generic"))]
pub fn brotli_compress_file_json(
    py: Python<'_>,
    input_path: PathBuf,
    output_path: PathBuf,
    quality: i32,
    lgwin: Option<i32>,
    mode: &str,
) -> PyResult<String> {
    let quality_u32 = validate_quality(quality).map_err(PyValueError::new_err)?;
    let enc_mode = parse_mode(mode).map_err(PyValueError::new_err)?;
    let _ = resolve_lgwin(lgwin, None).map_err(PyValueError::new_err)?;

    let payload = py
        .detach(move || -> Result<BrotliFileStatsJson, String> {
            let in_abs = abs_path(&input_path)?;
            let out_abs = abs_path(&output_path)?;
            if !in_abs.is_file() {
                return Err(format!("Source not found: {}", in_abs.display()));
            }
            if paths_refer_to_same_file(&in_abs, &out_abs) {
                return Err(format!(
                    "output path must differ from input path: {}",
                    out_abs.display()
                ));
            }
            if let Some(parent) = out_abs.parent() {
                if !parent.as_os_str().is_empty() {
                    fs::create_dir_all(parent).map_err(|e| {
                        format!(
                            "cannot create output directory {}: {e}",
                            parent.display()
                        )
                    })?;
                }
            }

            let file_in = File::open(&in_abs)
                .map_err(|e| format!("cannot open {}: {e}", in_abs.display()))?;
            let file_len = file_in.metadata().ok().map(|m| m.len());
            let (eff_lgwin, large_window) = resolve_lgwin(lgwin, file_len)?;

            let file_out = File::create(&out_abs)
                .map_err(|e| format!("cannot create {}: {e}", out_abs.display()))?;

            let res = {
                let reader = BufReader::with_capacity(FILE_BUFFER_SIZE, file_in);
                let mut writer = BufWriter::with_capacity(FILE_BUFFER_SIZE, file_out);
                let inner_res = compress_stream_to_writer(
                    reader,
                    &mut writer,
                    quality_u32,
                    eff_lgwin,
                    large_window,
                    enc_mode,
                    file_len,
                );
                if inner_res.is_ok() {
                    writer
                        .flush()
                        .map_err(|e| format!("failed to flush brotli output: {e}"))?;
                }
                inner_res
            };

            match res {
                Ok((bytes_in, bytes_out)) => Ok(BrotliFileStatsJson {
                    input_path: in_abs.to_string_lossy().into_owned(),
                    output_path: out_abs.to_string_lossy().into_owned(),
                    bytes_in,
                    bytes_out,
                }),
                Err(err) => {
                    let _ = fs::remove_file(&out_abs);
                    Err(err)
                }
            }
        })
        .map_err(PyOSError::new_err)?;

    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Decompress `input_path` to `output_path` using native pure-Rust Brotli.
#[pyfunction]
#[pyo3(signature = (input_path, output_path, allow_concatenated=false))]
pub fn brotli_decompress_file_json(
    py: Python<'_>,
    input_path: PathBuf,
    output_path: PathBuf,
    allow_concatenated: bool,
) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<BrotliFileStatsJson, String> {
            let in_abs = abs_path(&input_path)?;
            let out_abs = abs_path(&output_path)?;
            if !in_abs.is_file() {
                return Err(format!("Source not found: {}", in_abs.display()));
            }
            if paths_refer_to_same_file(&in_abs, &out_abs) {
                return Err(format!(
                    "output path must differ from input path: {}",
                    out_abs.display()
                ));
            }
            if let Some(parent) = out_abs.parent() {
                if !parent.as_os_str().is_empty() {
                    fs::create_dir_all(parent).map_err(|e| {
                        format!(
                            "cannot create output directory {}: {e}",
                            parent.display()
                        )
                    })?;
                }
            }

            let file_in = File::open(&in_abs)
                .map_err(|e| format!("cannot open {}: {e}", in_abs.display()))?;
            let file_out = File::create(&out_abs)
                .map_err(|e| format!("cannot create {}: {e}", out_abs.display()))?;

            let res = {
                let reader = BufReader::with_capacity(FILE_BUFFER_SIZE, file_in);
                let mut writer = BufWriter::with_capacity(FILE_BUFFER_SIZE, file_out);
                let inner_res = decompress_stream_with_sink(reader, allow_concatenated, |chunk| {
                    writer
                        .write_all(chunk)
                        .map_err(|e| format!("failed to write brotli output: {e}"))
                });
                if inner_res.is_ok() {
                    writer
                        .flush()
                        .map_err(|e| format!("failed to flush brotli output: {e}"))?;
                }
                inner_res
            };

            match res {
                Ok((bytes_in, bytes_out)) => Ok(BrotliFileStatsJson {
                    input_path: in_abs.to_string_lossy().into_owned(),
                    output_path: out_abs.to_string_lossy().into_owned(),
                    bytes_in,
                    bytes_out,
                }),
                Err(err) => {
                    let _ = fs::remove_file(&out_abs);
                    Err(err)
                }
            }
        })
        .map_err(PyOSError::new_err)?;

    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Measure exact uncompressed size in bytes of a `.br` file without writing output to disk.
#[pyfunction]
#[pyo3(signature = (input_path, allow_concatenated=false))]
pub fn brotli_decompressed_size(
    py: Python<'_>,
    input_path: PathBuf,
    allow_concatenated: bool,
) -> PyResult<u64> {
    py.detach(move || -> Result<u64, String> {
        let in_abs = abs_path(&input_path)?;
        if !in_abs.is_file() {
            return Err(format!("Source not found: {}", in_abs.display()));
        }
        let file_in = File::open(&in_abs)
            .map_err(|e| format!("cannot open {}: {e}", in_abs.display()))?;
        let reader = BufReader::with_capacity(FILE_BUFFER_SIZE, file_in);
        let (_, bytes_out) = decompress_stream_with_sink(reader, allow_concatenated, |_| Ok(()))?;
        Ok(bytes_out)
    })
    .map_err(PyOSError::new_err)
}

/// Compress an in-memory byte buffer using native pure-Rust Brotli.
#[pyfunction]
#[pyo3(signature = (data, quality=1, lgwin=None, mode="generic"))]
pub fn brotli_compress_bytes(
    py: Python<'_>,
    data: &[u8],
    quality: i32,
    lgwin: Option<i32>,
    mode: &str,
) -> PyResult<Py<PyBytes>> {
    let quality_u32 = validate_quality(quality).map_err(PyValueError::new_err)?;
    let enc_mode = parse_mode(mode).map_err(PyValueError::new_err)?;
    let (eff_lgwin, large_window) =
        resolve_lgwin(lgwin, Some(data.len() as u64)).map_err(PyValueError::new_err)?;

    let owned = data.to_vec();
    let compressed = py
        .detach(move || -> Result<Vec<u8>, String> {
            let mut out = Vec::new();
            compress_stream_to_writer(
                owned.as_slice(),
                &mut out,
                quality_u32,
                eff_lgwin,
                large_window,
                enc_mode,
                Some(owned.len() as u64),
            )?;
            Ok(out)
        })
        .map_err(PyOSError::new_err)?;

    Ok(PyBytes::new(py, &compressed).unbind())
}

/// Decompress an in-memory Brotli byte buffer using native pure-Rust Brotli.
#[pyfunction]
#[pyo3(signature = (data, allow_concatenated=false))]
pub fn brotli_decompress_bytes(
    py: Python<'_>,
    data: &[u8],
    allow_concatenated: bool,
) -> PyResult<Py<PyBytes>> {
    let owned = data.to_vec();
    let decompressed = py
        .detach(move || -> Result<Vec<u8>, String> {
            let mut out = Vec::new();
            decompress_stream_with_sink(owned.as_slice(), allow_concatenated, |chunk| {
                out.extend_from_slice(chunk);
                Ok(())
            })?;
            Ok(out)
        })
        .map_err(PyOSError::new_err)?;

    Ok(PyBytes::new(py, &decompressed).unbind())
}

struct BrotliReaderInner {
    file: File,
    state: BrotliDecoderState,
    in_buf: Vec<u8>,
    in_pos: usize,
    in_len: usize,
    eof_in: bool,
    finished: bool,
}

impl BrotliReaderInner {
    fn read_more_input(&mut self) -> Result<bool, String> {
        if self.eof_in {
            return Ok(false);
        }
        if self.in_pos < self.in_len {
            let rem = self.in_len - self.in_pos;
            self.in_buf.copy_within(self.in_pos..self.in_len, 0);
            self.in_pos = 0;
            self.in_len = rem;
        } else {
            self.in_pos = 0;
            self.in_len = 0;
        }
        loop {
            match self.file.read(&mut self.in_buf[self.in_len..]) {
                Ok(0) => {
                    self.eof_in = true;
                    return Ok(false);
                }
                Ok(n) => {
                    self.in_len += n;
                    return Ok(true);
                }
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(e) => return Err(format!("failed to read brotli input: {e}")),
            }
        }
    }

    fn read_chunk(&mut self, size: isize) -> Result<Vec<u8>, String> {
        if self.finished || size == 0 {
            return Ok(Vec::new());
        }

        let target = if size < 0 { None } else { Some(size as usize) };
        let initial_cap = target.map_or(65536, |t| t.min(1 << 20));
        let mut out = Vec::with_capacity(initial_cap);
        let mut result = BrotliDecoderResult::NeedsMoreOutput;

        loop {
            if let Some(t) = target {
                if out.len() >= t {
                    break;
                }
            }

            if result == BrotliDecoderResult::NeedsMoreInput {
                if !self.read_more_input()? && self.in_pos >= self.in_len {
                    return Err("corrupt brotli input: truncated stream".to_string());
                }
            }

            let step = match target {
                Some(t) => (t - out.len()).min(READER_OUT_CHUNK_SIZE),
                None => READER_OUT_CHUNK_SIZE,
            };
            let old_len = out.len();
            out.resize(old_len + step, 0);
            let mut out_pos = 0usize;

            result = self.state.decompress_stream(
                &self.in_buf[..self.in_len],
                &mut self.in_pos,
                &mut out[old_len..],
                &mut out_pos,
                None,
            );
            out.truncate(old_len + out_pos);

            match result {
                BrotliDecoderResult::Success => {
                    let has_more_input = if self.in_pos < self.in_len {
                        true
                    } else if !self.eof_in {
                        self.read_more_input()?
                    } else {
                        false
                    };
                    if has_more_input {
                        return Err("corrupt brotli input: extra trailing data".to_string());
                    }
                    self.finished = true;
                    break;
                }
                BrotliDecoderResult::NeedsMoreInput => continue,
                BrotliDecoderResult::NeedsMoreOutput => continue,
                BrotliDecoderResult::Error => {
                    return Err(format!(
                        "corrupt brotli input: {}",
                        self.state.error_code.as_str()
                    ));
                }
            }
        }

        Ok(out)
    }
}

/// Streaming file-like Brotli decompressor backed by `brotli_rs::dec::BrotliDecoderState`.
#[pyclass]
pub struct BrotliFileReader {
    inner: Mutex<Option<BrotliReaderInner>>,
}

#[pymethods]
impl BrotliFileReader {
    #[new]
    fn new(py: Python<'_>, path: PathBuf) -> PyResult<Self> {
        let inner = py
            .detach(move || -> Result<BrotliReaderInner, String> {
                let in_abs = abs_path(&path)?;
                if !in_abs.is_file() {
                    return Err(format!("Source not found: {}", in_abs.display()));
                }
                let file = File::open(&in_abs)
                    .map_err(|e| format!("cannot open {}: {e}", in_abs.display()))?;
                Ok(BrotliReaderInner {
                    file,
                    state: make_decoder(),
                    in_buf: vec![0u8; READER_IN_BUFFER_SIZE],
                    in_pos: 0,
                    in_len: 0,
                    eof_in: false,
                    finished: false,
                })
            })
            .map_err(PyOSError::new_err)?;
        Ok(Self {
            inner: Mutex::new(Some(inner)),
        })
    }

    #[pyo3(signature = (size=None))]
    fn read(&self, py: Python<'_>, size: Option<isize>) -> PyResult<Py<PyBytes>> {
        let size_val = size.unwrap_or(-1);
        let data = py
            .detach(move || -> Result<Vec<u8>, String> {
                let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
                let Some(ref mut inner) = *guard else {
                    return Ok(Vec::new());
                };
                inner.read_chunk(size_val)
            })
            .map_err(PyOSError::new_err)?;
        Ok(PyBytes::new(py, &data).unbind())
    }

    fn close(&self) {
        let mut guard = self.inner.lock().unwrap_or_else(|e| e.into_inner());
        *guard = None;
    }

    fn __enter__(slf: Py<Self>) -> Py<Self> {
        slf
    }

    fn __exit__(
        &self,
        _exc_type: Option<&Bound<'_, PyAny>>,
        _exc_val: Option<&Bound<'_, PyAny>>,
        _exc_tb: Option<&Bound<'_, PyAny>>,
    ) -> bool {
        self.close();
        false
    }
}
