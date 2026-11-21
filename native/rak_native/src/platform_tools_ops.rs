// SPDX-License-Identifier: MPL-2.0
//! Native in-process bindings for MIT `platform-tools-rs` (`adb`, `fastboot`, `mke2fs`,
//! `make_f2fs`, `make_f2fs_casefold`, `etc1tool`, `hprof-conv`, `sqlite3`).

use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use serde::Serialize;
use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, OnceLock};

static PT_RUNTIME: OnceLock<tokio::runtime::Runtime> = OnceLock::new();
static EMBEDDED_INIT: OnceLock<()> = OnceLock::new();

fn ensure_embedded() {
    EMBEDDED_INIT.get_or_init(|| {
        adb::set_embedded_mode(true);
        fastboot::set_embedded_mode(true);
    });
}

fn pt_runtime() -> PyResult<&'static tokio::runtime::Runtime> {
    ensure_embedded();
    if let Some(rt) = PT_RUNTIME.get() {
        return Ok(rt);
    }
    let rt = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .thread_name("rak-pt")
        .enable_all()
        .build()
        .map_err(|e| PyOSError::new_err(format!("failed to create platform-tools runtime: {e}")))?;
    Ok(PT_RUNTIME.get_or_init(|| rt))
}

/// Cooperative cancellation handle shared across Python threads and Rust `adb`/`fastboot` loops.
#[pyclass]
pub struct NativeCancelToken {
    flag: Arc<AtomicBool>,
}

impl NativeCancelToken {
    fn flag_clone(&self) -> Arc<AtomicBool> {
        Arc::clone(&self.flag)
    }
}

#[pymethods]
impl NativeCancelToken {
    #[new]
    fn new() -> Self {
        Self {
            flag: Arc::new(AtomicBool::new(false)),
        }
    }

    fn cancel(&self) {
        self.flag.store(true, Ordering::Relaxed);
    }

    fn is_cancelled(&self) -> bool {
        self.flag.load(Ordering::Relaxed)
    }
}

#[derive(Serialize)]
struct RunResultJson {
    exit_code: i32,
    stdout: String,
    stderr: String,
    output: String,
}

#[derive(Serialize)]
struct FastbootDeviceJson {
    serial: String,
    mode: String,
    device_path: String,
}

#[derive(Serialize)]
struct PlatformToolsVersionJson {
    version: &'static str,
    version_full: &'static str,
    build_id: &'static str,
    adb_protocol: &'static str,
}

/// Starts the in-process Rust ADB host server if not already listening on `host:port`.
#[pyfunction]
#[pyo3(signature = (host="127.0.0.1", port=5037))]
pub fn adb_ensure_server(py: Python<'_>, host: &str, port: u16) -> PyResult<bool> {
    ensure_embedded();
    let host_owned = if host == "localhost" {
        "127.0.0.1".to_string()
    } else {
        host.to_string()
    };
    let addr: SocketAddr = format!("{host_owned}:{port}")
        .parse()
        .map_err(|e| PyValueError::new_err(format!("invalid bind address: {e}")))?;
    py.detach(move || adb::ensure_in_process_server(addr))
        .map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Stops all in-process Rust ADB host servers started by this process.
#[pyfunction]
pub fn adb_shutdown_servers(py: Python<'_>) -> PyResult<()> {
    py.detach(|| {
        adb::shutdown_in_process_servers();
    });
    Ok(())
}

/// Runs an `adb` CLI command completely in-process with captured output, cancel & progress hooks.
#[pyfunction]
#[pyo3(signature = (args, cwd=None, timeout_ms=None, cancel=None, on_output=None, on_progress=None))]
pub fn adb_run_json(
    py: Python<'_>,
    args: Vec<String>,
    cwd: Option<PathBuf>,
    timeout_ms: Option<u64>,
    cancel: Option<&Bound<'_, NativeCancelToken>>,
    on_output: Option<Py<PyAny>>,
    on_progress: Option<Py<PyAny>>,
) -> PyResult<String> {
    let rt = pt_runtime()?;
    let cancel_flag = cancel.map(|c| c.borrow().flag_clone());

    let cb: Option<adb::AdbEventCallback> = if on_output.is_some() || on_progress.is_some() {
        let out_cb = on_output;
        let prog_cb = on_progress;
        Some(Arc::new(move |ev: adb::AdbEvent| match ev {
            adb::AdbEvent::Output { text, is_stderr } => {
                if let Some(ref f) = out_cb {
                    Python::attach(|py| {
                        let _ = f.call1(py, (text, is_stderr));
                    });
                }
            }
            adb::AdbEvent::Progress {
                stage,
                current,
                total,
                percent,
                detail,
            } => {
                if let Some(ref f) = prog_cb {
                    Python::attach(|py| {
                        let d = PyDict::new(py);
                        let _ = d.set_item("stage", stage);
                        let _ = d.set_item("current", current);
                        let _ = d.set_item("total", total);
                        let _ = d.set_item("percent", percent);
                        let _ = d.set_item("detail", detail);
                        let _ = f.call1(py, (d,));
                    });
                }
            }
        }))
    } else {
        None
    };

    let res = py.detach(move || {
        rt.block_on(adb::run_cli_captured(
            &args,
            cwd.as_deref(),
            timeout_ms,
            cancel_flag,
            cb,
        ))
    });

    serde_json::to_string(&RunResultJson {
        exit_code: res.exit_code,
        stdout: res.stdout,
        stderr: res.stderr,
        output: res.output,
    })
    .map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Runs an `adb` CLI command interactively via host process stdio.
#[pyfunction]
pub fn adb_run_stdio(py: Python<'_>, args: Vec<String>) -> PyResult<i32> {
    let rt = pt_runtime()?;
    Ok(py.detach(move || rt.block_on(adb::run_cli(&args))))
}

/// Lists connected USB fastboot devices (`0xFF/0x42/0x03`) directly via `nusb`.
#[pyfunction]
pub fn fastboot_list_devices_json(py: Python<'_>) -> PyResult<String> {
    let rt = pt_runtime()?;
    let devs = py
        .detach(move || rt.block_on(fastboot::transport::list_usb_devices()))
        .map_err(|e| PyOSError::new_err(e.to_string()))?;
    let rows: Vec<FastbootDeviceJson> = devs
        .into_iter()
        .map(|d| FastbootDeviceJson {
            serial: d.serial,
            mode: d.mode,
            device_path: d.device_path,
        })
        .collect();
    serde_json::to_string(&rows).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Runs a `fastboot` command completely in-process with smooth chunk/byte progress & cancel hooks.
#[pyfunction]
#[pyo3(signature = (args, cwd=None, timeout_ms=None, cancel=None, on_output=None, on_progress=None))]
pub fn fastboot_run_json(
    py: Python<'_>,
    args: Vec<String>,
    cwd: Option<PathBuf>,
    timeout_ms: Option<u64>,
    cancel: Option<&Bound<'_, NativeCancelToken>>,
    on_output: Option<Py<PyAny>>,
    on_progress: Option<Py<PyAny>>,
) -> PyResult<String> {
    let rt = pt_runtime()?;
    let cancel_flag = cancel.map(|c| c.borrow().flag_clone());

    let cb: Option<fastboot::FastbootEventCallback> =
        if on_output.is_some() || on_progress.is_some() {
            let out_cb = on_output;
            let prog_cb = on_progress;
            Some(Arc::new(move |ev: fastboot::FastbootEvent| match ev {
                fastboot::FastbootEvent::Output { text, is_stderr } => {
                    if let Some(ref f) = out_cb {
                        Python::attach(|py| {
                            let _ = f.call1(py, (text, is_stderr));
                        });
                    }
                }
                fastboot::FastbootEvent::Progress {
                    partition,
                    stage,
                    chunk_index,
                    chunk_total,
                    bytes_sent,
                    bytes_total,
                    percent,
                    speed_bps,
                    detail,
                } => {
                    if let Some(ref f) = prog_cb {
                        Python::attach(|py| {
                            let d = PyDict::new(py);
                            let _ = d.set_item("partition", partition);
                            let _ = d.set_item("stage", stage);
                            let _ = d.set_item("chunk_index", chunk_index);
                            let _ = d.set_item("chunk_total", chunk_total);
                            let _ = d.set_item("bytes_sent", bytes_sent);
                            let _ = d.set_item("bytes_total", bytes_total);
                            let _ = d.set_item("percent", percent);
                            let _ = d.set_item("speed_bps", speed_bps);
                            let _ = d.set_item("detail", detail);
                            let _ = f.call1(py, (d,));
                        });
                    }
                }
            }))
        } else {
            None
        };

    let res = py.detach(move || {
        rt.block_on(fastboot::run_cli_captured(
            &args,
            cwd.as_deref(),
            timeout_ms,
            cancel_flag,
            cb,
        ))
    });

    serde_json::to_string(&RunResultJson {
        exit_code: res.exit_code,
        stdout: res.stdout,
        stderr: res.stderr,
        output: res.output,
    })
    .map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Runs a `fastboot` CLI command interactively via host process stdio.
#[pyfunction]
pub fn fastboot_run_stdio(py: Python<'_>, args: Vec<String>) -> PyResult<i32> {
    let rt = pt_runtime()?;
    Ok(py.detach(move || rt.block_on(fastboot::run_cli(&args))))
}

/// Dispatches any of the 8 `platform-tools-rs` tools directly in-process via stdio.
#[pyfunction]
pub fn platform_tool_run_stdio(py: Python<'_>, tool: String, args: Vec<String>) -> PyResult<i32> {
    let norm = tool.trim().to_ascii_lowercase();
    let stem = norm.strip_suffix(".exe").unwrap_or(&norm);
    match stem {
        "adb" => {
            let rt = pt_runtime()?;
            Ok(py.detach(move || rt.block_on(adb::run_cli(&args))))
        }
        "fastboot" => {
            let rt = pt_runtime()?;
            Ok(py.detach(move || rt.block_on(fastboot::run_cli(&args))))
        }
        "mke2fs" => {
            ensure_embedded();
            Ok(py.detach(move || extfs_mkfs::run_cli(&args)))
        }
        "make_f2fs" => {
            ensure_embedded();
            Ok(py.detach(move || {
                let mut full = Vec::with_capacity(args.len() + 1);
                full.push("make_f2fs".to_string());
                full.extend(args);
                f2fs_mkfs::run_cli(&full, false)
            }))
        }
        "make_f2fs_casefold" => {
            ensure_embedded();
            Ok(py.detach(move || {
                let mut full = Vec::with_capacity(args.len() + 1);
                full.push("make_f2fs_casefold".to_string());
                full.extend(args);
                f2fs_mkfs::run_cli(&full, true)
            }))
        }
        "etc1tool" => {
            ensure_embedded();
            Ok(py.detach(move || etc1tool::run_cli(&args)))
        }
        "hprof-conv" | "hprof_conv" => {
            ensure_embedded();
            Ok(py.detach(move || hprof_conv::run_cli(&args)))
        }
        "sqlite3" => {
            ensure_embedded();
            Ok(py.detach(move || sqlite3::run_cli(&args)))
        }
        other => Err(PyValueError::new_err(format!(
            "unknown platform tool '{other}'"
        ))),
    }
}

/// Returns baseline version metadata for the embedded `platform-tools-rs` suite.
#[pyfunction]
pub fn platform_tools_version_json() -> PyResult<String> {
    ensure_embedded();
    serde_json::to_string(&PlatformToolsVersionJson {
        version: pt_common::PLATFORM_TOOLS_VERSION,
        version_full: pt_common::PLATFORM_TOOLS_VERSION_FULL,
        build_id: pt_common::PLATFORM_TOOLS_BUILD_ID,
        adb_protocol: pt_common::ADB_PROTOCOL_VERSION_STR,
    })
    .map_err(|e| PyOSError::new_err(e.to_string()))
}
