// SPDX-License-Identifier: MPL-2.0
//! Native in-process bindings for SUPER (lp_tools), EXT2/3/4 (ext_cli, make_ext4fs_rs),
//! Android sparse conversions, PAC extraction, and boot image manipulation (magiskboot).

use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use ext_cli::check::inspect_ext;
use ext_cli::unpack::unpack_image_with_progress;
use lp_tools::lp::{
    inspect_super_image, unpack_super_image_with_progress, unsparse_super_image_with_progress,
    write_empty_image_file, write_to_image_file_with_progress, BlockDeviceInfo, MetadataBuilder,
    DEFAULT_BLOCK_SIZE, DEFAULT_GROUP, DEFAULT_PARTITION_ALIGNMENT, LP_PARTITION_ATTR_NONE,
    LP_PARTITION_ATTR_READONLY,
};
use lp_tools::sparse::ImageSource;
use make_ext4fs_rs::config::fs_config::{CannedFsConfig, FsConfigProvider};
use make_ext4fs_rs::config::selinux::FileContexts;
use make_ext4fs_rs::ext4::fs::{make_ext4fs_with_progress, Ext4Options, OutputTarget};
use make_ext4fs_rs::sparse::SparseFile;

fn emit_py_progress(cb: Option<&Py<PyAny>>, pct: u8, stage: &str) {
    let Some(func) = cb else {
        return;
    };
    Python::attach(|py| {
        let _ = func.call1(py, (pct, stage));
    });
}
use pac_extractor::pac::{open_pac_file, prepare_output_dir};
use std::collections::HashSet;
use std::sync::{Mutex, MutexGuard};
use magiskboot::compress::{compress_bytes, decompress_bytes};
use magiskboot::dtb::dtb_patch;
use magiskboot::format::{Format, detect_format};
use magiskboot::inspect_boot_image;

#[derive(Serialize)]
struct SuperInfoJson {
    raw: String,
    partitions: Vec<String>,
    groups: Vec<String>,
    active_slot: Option<String>,
    is_empty: bool,
}

#[derive(Serialize)]
struct SuperExtractJson {
    out_dir: String,
    outputs: Vec<String>,
}

#[derive(Serialize)]
struct ExtInspectJson {
    fs_type: String,
    has_journal: bool,
    has_extents: bool,
    is_64bit: bool,
    sparse_super2: bool,
    block_size: u64,
    block_count: u64,
    inode_size: u16,
    volume_name: String,
    last_mounted: String,
    uuid: String,
}

#[pyfunction]
#[pyo3(signature = (img_path, slot=0))]
pub fn super_info_json(py: Python<'_>, img_path: PathBuf, slot: u32) -> PyResult<String> {
    let info = py
        .detach(move || inspect_super_image(&img_path, slot))
        .map_err(|e| PyOSError::new_err(e.to_string()))?;
    let payload = SuperInfoJson {
        raw: info.raw_text,
        partitions: info.partitions,
        groups: info.groups,
        active_slot: info.active_slot,
        is_empty: info.is_empty,
    };
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}



#[pyfunction]
#[pyo3(signature = (img_path, out_dir, partitions=None, slot=0, normalize=false, progress=None))]
pub fn super_extract_json(
    py: Python<'_>,
    img_path: PathBuf,
    out_dir: PathBuf,
    partitions: Option<Vec<String>>,
    slot: u32,
    normalize: bool,
    progress: Option<Py<PyAny>>,
) -> PyResult<String> {
    let requested = partitions.unwrap_or_default();
    let out_dir_str = out_dir.to_string_lossy().into_owned();
    let cb = progress.as_ref();
    let outputs = py
        .detach(move || {
            unpack_super_image_with_progress(&img_path, &out_dir, &requested, slot, normalize, |pct, stage| {
                emit_py_progress(cb, pct, stage);
            })
        })
        .map_err(|e| PyOSError::new_err(e.to_string()))?;
    let payload = SuperExtractJson {
        out_dir: out_dir_str,
        outputs: outputs
            .into_iter()
            .map(|p| p.to_string_lossy().into_owned())
            .collect(),
    };
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}



#[derive(Deserialize)]
struct SuperMakeGroupSpec {
    name: String,
    max_size: u64,
}

#[derive(Deserialize)]
struct SuperMakePartitionSpec {
    name: String,
    #[serde(default = "default_readonly_attr")]
    attr: String,
    #[serde(default)]
    size: u64,
    #[serde(default)]
    group: String,
    #[serde(default)]
    image: Option<PathBuf>,
}

fn default_readonly_attr() -> String {
    "readonly".to_string()
}

#[derive(Serialize)]
struct SuperMakeResultJson {
    output_path: String,
    device_size: u64,
    sparse: bool,
    virtual_ab: bool,
    partitions: Vec<String>,
    bytes_written: u64,
}

#[pyfunction]
pub fn image_logical_size(py: Python<'_>, img_path: PathBuf) -> PyResult<u64> {
    py.detach(move || {
        let file = File::open(&img_path)?;
        let src = ImageSource::open(file).map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e.to_string()))?;
        Ok::<u64, std::io::Error>(src.logical_size())
    })
    .map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (
    output_path,
    device_size,
    groups_json,
    partitions_json,
    metadata_size=65536,
    metadata_slots=2,
    block_size=DEFAULT_BLOCK_SIZE,
    alignment=DEFAULT_PARTITION_ALIGNMENT,
    alignment_offset=0,
    super_name="super".to_string(),
    sparse=false,
    virtual_ab=false,
    auto_slot_suffixing=false,
    force_full_image=true,
    progress=None
))]
#[allow(clippy::too_many_arguments)]
pub fn super_make_json(
    py: Python<'_>,
    output_path: PathBuf,
    device_size: u64,
    groups_json: String,
    partitions_json: String,
    metadata_size: u32,
    metadata_slots: u32,
    block_size: u32,
    alignment: u32,
    alignment_offset: u32,
    super_name: String,
    sparse: bool,
    virtual_ab: bool,
    auto_slot_suffixing: bool,
    force_full_image: bool,
    progress: Option<Py<PyAny>>,
) -> PyResult<String> {
    let groups: Vec<SuperMakeGroupSpec> = serde_json::from_str(&groups_json)
        .map_err(|e| PyValueError::new_err(format!("Invalid groups_json: {e}")))?;
    let partitions: Vec<SuperMakePartitionSpec> = serde_json::from_str(&partitions_json)
        .map_err(|e| PyValueError::new_err(format!("Invalid partitions_json: {e}")))?;
    if partitions.is_empty() {
        return Err(PyValueError::new_err("Partition list cannot be empty."));
    }
    let cb = progress.as_ref();

    let payload = py
        .detach(move || -> Result<SuperMakeResultJson, String> {
            let dev = BlockDeviceInfo::new(
                &super_name,
                device_size,
                alignment,
                alignment_offset,
                block_size,
            )
            .map_err(|e| e.to_string())?;
            let mut builder = MetadataBuilder::new(
                &[dev],
                &super_name,
                metadata_size,
                metadata_slots,
            )
            .map_err(|e| format!("Invalid metadata parameters: {e}"))?;

            if auto_slot_suffixing {
                builder.set_auto_slot_suffixing();
            }
            if virtual_ab {
                builder.set_virtual_ab_device_flag();
            }

            for g in &groups {
                if g.name.is_empty() {
                    return Err("Partition group must have a valid name.".into());
                }
                if g.name != DEFAULT_GROUP {
                    builder
                        .add_group(&g.name, g.max_size)
                        .map_err(|e| e.to_string())?;
                }
            }

            let mut images: BTreeMap<String, PathBuf> = BTreeMap::new();
            let mut part_names: Vec<String> = Vec::with_capacity(partitions.len());
            for p in &partitions {
                if p.name.is_empty() {
                    return Err("Partition must have a valid name.".into());
                }
                let attr_flags = match p.attr.as_str() {
                    "readonly" => LP_PARTITION_ATTR_READONLY,
                    "none" => LP_PARTITION_ATTR_NONE,
                    other => return Err(format!("Attribute not recognized: {other}")),
                };
                let group_name = if p.group.is_empty() {
                    DEFAULT_GROUP
                } else {
                    p.group.as_str()
                };
                let mut part_size = p.size;
                if let Some(img_path) = &p.image {
                    if !img_path.as_os_str().is_empty() {
                        if part_size == 0 {
                            let file = File::open(img_path).map_err(|e| {
                                format!("Could not open image '{}': {e}", img_path.display())
                            })?;
                            let src = ImageSource::open(file).map_err(|e| e.to_string())?;
                            part_size = src.logical_size();
                        }
                        images.insert(p.name.clone(), img_path.clone());
                    }
                }
                let part_idx = builder
                    .add_partition(&p.name, group_name, attr_flags)
                    .map_err(|e| format!("Could not add partition {}: {e}", p.name))?;
                builder
                    .resize_partition(part_idx, part_size, &[])
                    .map_err(|e| e.to_string())?;
                part_names.push(p.name.clone());
            }

            let metadata = builder.export().map_err(|e| e.to_string())?;
            if let Some(parent) = output_path.parent() {
                if !parent.as_os_str().is_empty() && !parent.exists() {
                    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
                }
            }

            if !images.is_empty() || force_full_image {
                write_to_image_file_with_progress(
                    &output_path,
                    &metadata,
                    block_size,
                    &images,
                    sparse,
                    |pct, stage| emit_py_progress(cb, pct, stage),
                )
                .map_err(|e| e.to_string())?;
            } else {
                write_empty_image_file(&output_path, &metadata).map_err(|e| e.to_string())?;
                emit_py_progress(cb, 100, "Done");
            }

            let bytes_written = fs::metadata(&output_path).map(|m| m.len()).unwrap_or(0);
            Ok(SuperMakeResultJson {
                output_path: output_path.to_string_lossy().into_owned(),
                device_size,
                sparse,
                virtual_ab,
                partitions: part_names,
                bytes_written,
            })
        })
        .map_err(PyOSError::new_err)?;

    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (img_path, out_dir=None, progress=None))]
pub fn super_unsparse(
    py: Python<'_>,
    img_path: PathBuf,
    out_dir: Option<PathBuf>,
    progress: Option<Py<PyAny>>,
) -> PyResult<String> {
    let cb = progress.as_ref();
    let out = py
        .detach(move || {
            unsparse_super_image_with_progress(&img_path, out_dir.as_deref(), |pct, stage| {
                emit_py_progress(cb, pct, stage);
            })
        })
        .map_err(|e| PyOSError::new_err(e.to_string()))?;
    Ok(out.to_string_lossy().into_owned())
}

#[pyfunction]
pub fn ext_inspect_json(py: Python<'_>, img_path: PathBuf) -> PyResult<String> {
    let info = py
        .detach(move || inspect_ext(&img_path))
        .map_err(PyOSError::new_err)?;
    let payload = ExtInspectJson {
        fs_type: info.fs_type.to_string(),
        has_journal: info.has_journal,
        has_extents: info.has_extents,
        is_64bit: info.is_64bit,
        sparse_super2: info.sparse_super2,
        block_size: info.block_size,
        block_count: info.block_count,
        inode_size: info.inode_size,
        volume_name: info.volume_name,
        last_mounted: info.last_mounted,
        uuid: info.uuid,
    };
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}



#[pyfunction]
#[pyo3(signature = (img_path, progress=None))]
pub fn ext_unpack(
    py: Python<'_>,
    img_path: PathBuf,
    progress: Option<Py<PyAny>>,
) -> PyResult<String> {
    let cb = progress.as_ref();
    let out_dir = py
        .detach(move || unpack_image_with_progress(&img_path, |pct, stage| emit_py_progress(cb, pct, stage)))
        .map_err(|e| PyOSError::new_err(e.to_string()))?;
    Ok(out_dir.to_string_lossy().into_owned())
}

#[derive(Serialize)]
struct ExtNodeJson {
    path: String,
    ino: u32,
    size: u64,
    mode: u16,
    perm: String,
    is_dir: bool,
    is_link: bool,
    target: Option<String>,
    selinux: Option<String>,
}

impl From<ext_cli::extfs::ExtFileNode> for ExtNodeJson {
    fn from(n: ext_cli::extfs::ExtFileNode) -> Self {
        Self {
            path: n.path,
            ino: n.ino,
            size: n.size,
            mode: n.mode,
            perm: n.perm,
            is_dir: n.is_dir,
            is_link: n.is_link,
            target: n.target,
            selinux: n.selinux,
        }
    }
}

#[derive(Serialize)]
struct ExtChildEntryJson {
    ino: u32,
    name: String,
    is_dir: bool,
    is_link: bool,
    size: u64,
}

#[derive(Serialize)]
struct ExtUsageJson {
    fs: String,
    block_size: u64,
    file_size: u64,
    total_blocks: u64,
    meta_bytes: u64,
    used_blocks: u64,
    used_bytes: u64,
    wasted_blocks: u64,
    wasted_bytes: u64,
    free_blocks: u64,
    free_bytes: u64,
    logical_bytes: u64,
    reclaimable_bytes: u64,
    files: usize,
    dirs: usize,
    links: usize,
}

#[derive(Serialize)]
struct ExtWritePlanJson {
    mode: String,
    old_size: u64,
    new_size: u64,
    grow_bytes: u64,
}

#[derive(Serialize)]
struct ExtWriteResultJson {
    path: String,
    mode: String,
    old_size: u64,
    new_size: u64,
    blocks_freed: u64,
}

#[derive(Serialize)]
struct ExtUnlinkResultJson {
    removed: String,
    was_dir: bool,
    entries_removed: usize,
    blocks_freed: u64,
    mode: String,
}

/// Persistent native EXT2/3/4 reader handle for mounting, browsing, reading, and APEX payloads.
#[pyclass]
pub struct NativeExtReader {
    vol: Mutex<Option<ext_cli::extfs::Volume>>,
}

impl NativeExtReader {
    pub(crate) fn from_volume(vol: ext_cli::extfs::Volume) -> Self {
        Self {
            vol: Mutex::new(Some(vol)),
        }
    }

    pub(crate) fn with_vol<T, F>(&self, f: F) -> PyResult<T>
    where
        F: FnOnce(&mut ext_cli::extfs::Volume) -> Result<T, String>,
    {
        let mut guard = self
            .vol
            .lock()
            .map_err(|_| PyOSError::new_err("NativeExtReader mutex poisoned"))?;
        let vol = guard
            .as_mut()
            .ok_or_else(|| PyValueError::new_err("NativeExtReader is closed"))?;
        f(vol).map_err(PyOSError::new_err)
    }
}

#[pymethods]
impl NativeExtReader {
    #[new]
    fn new(py: Python<'_>, path: PathBuf) -> PyResult<Self> {
        let vol = py
            .detach(move || -> Result<ext_cli::extfs::Volume, String> {
                let abs = abs_path(&path)?;
                let file = File::open(&abs).map_err(|e| e.to_string())?;
                ext_cli::extfs::Volume::open(file).map_err(|e| e.to_string())
            })
            .map_err(PyOSError::new_err)?;
        Ok(Self {
            vol: Mutex::new(Some(vol)),
        })
    }

    #[staticmethod]
    fn from_bytes(py: Python<'_>, data: &[u8]) -> PyResult<Self> {
        let owned = data.to_vec();
        let vol = py
            .detach(move || -> Result<ext_cli::extfs::Volume, String> {
                ext_cli::extfs::Volume::open_bytes(owned).map_err(|e| e.to_string())
            })
            .map_err(PyOSError::new_err)?;
        Ok(Self {
            vol: Mutex::new(Some(vol)),
        })
    }

    #[getter]
    fn root_ino(&self) -> PyResult<u32> {
        Ok(ext_cli::extfs::EXT4_ROOT_INO)
    }

    #[getter]
    fn is_sparse(&self) -> PyResult<bool> {
        self.with_vol(|v| Ok(v.is_sparse()))
    }

    #[getter]
    fn block_size(&self) -> PyResult<u64> {
        self.with_vol(|v| Ok(v.block_size))
    }

    #[getter]
    fn has_64bit(&self) -> PyResult<bool> {
        self.with_vol(|v| Ok(v.superblock.is_64bit()))
    }

    fn walk_json(&self, py: Python<'_>) -> PyResult<String> {
        py.detach(|| {
            self.with_vol(|v| {
                let nodes: Vec<ExtNodeJson> = v
                    .walk_nodes()
                    .map_err(|e| e.to_string())?
                    .into_iter()
                    .map(ExtNodeJson::from)
                    .collect();
                serde_json::to_string(&nodes).map_err(|e| e.to_string())
            })
        })
    }

    fn children_json(&self, py: Python<'_>, ino: u32) -> PyResult<String> {
        py.detach(|| {
            self.with_vol(|v| {
                let rows: Vec<ExtChildEntryJson> = v
                    .children_of(ino)
                    .map_err(|e| e.to_string())?
                    .into_iter()
                    .map(|c| ExtChildEntryJson {
                        ino: c.ino,
                        name: c.name,
                        is_dir: c.is_dir,
                        is_link: c.is_link,
                        size: c.size,
                    })
                    .collect();
                serde_json::to_string(&rows).map_err(|e| e.to_string())
            })
        })
    }

    fn stat_ino_json(&self, py: Python<'_>, ino: u32) -> PyResult<String> {
        py.detach(|| {
            self.with_vol(|v| {
                let node: ExtNodeJson = v.stat_ino(ino).map_err(|e| e.to_string())?.into();
                serde_json::to_string(&node).map_err(|e| e.to_string())
            })
        })
    }

    fn stat_path_json(&self, py: Python<'_>, internal_path: String) -> PyResult<String> {
        py.detach(|| {
            self.with_vol(|v| {
                let node: ExtNodeJson = v
                    .lookup_path(&internal_path)
                    .map_err(|e| e.to_string())?
                    .into();
                serde_json::to_string(&node).map_err(|e| e.to_string())
            })
        })
    }

    fn read_file(&self, py: Python<'_>, ino: u32) -> PyResult<Py<PyBytes>> {
        let data = py.detach(|| {
            self.with_vol(|v| {
                let inode = v.read_inode(ino, 0).map_err(|e| e.to_string())?;
                v.read_inode_bytes(&inode).map_err(|e| e.to_string())
            })
        })?;
        Ok(PyBytes::new(py, &data).unbind())
    }

    fn read_path(&self, py: Python<'_>, internal_path: String) -> PyResult<Py<PyBytes>> {
        let data = py.detach(|| {
            self.with_vol(|v| {
                let (bytes, _node) = v.read_path(&internal_path).map_err(|e| e.to_string())?;
                Ok(bytes)
            })
        })?;
        Ok(PyBytes::new(py, &data).unbind())
    }

    fn close(&self) {
        if let Ok(mut guard) = self.vol.lock() {
            *guard = None;
        }
    }

    fn __enter__(slf: Py<Self>) -> Py<Self> {
        slf
    }

    #[pyo3(signature = (_exc_type=None, _exc_val=None, _exc_tb=None))]
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

#[pyfunction]
pub fn ext_usage_json(py: Python<'_>, image: PathBuf) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<ExtUsageJson, String> {
            let abs = abs_path(&image)?;
            let file_size = fs::metadata(&abs).map(|m| m.len()).unwrap_or(0);
            let file = File::open(&abs).map_err(|e| e.to_string())?;
            let mut vol = ext_cli::extfs::Volume::open(file).map_err(|e| e.to_string())?;
            let rep = vol.usage_report(file_size).map_err(|e| e.to_string())?;
            Ok(ExtUsageJson {
                fs: rep.fs,
                block_size: rep.block_size,
                file_size: rep.file_size,
                total_blocks: rep.total_blocks,
                meta_bytes: rep.meta_bytes,
                used_blocks: rep.used_blocks,
                used_bytes: rep.used_bytes,
                wasted_blocks: rep.wasted_blocks,
                wasted_bytes: rep.wasted_bytes,
                free_blocks: rep.free_blocks,
                free_bytes: rep.free_bytes,
                logical_bytes: rep.logical_bytes,
                reclaimable_bytes: rep.reclaimable_bytes,
                files: rep.files,
                dirs: rep.dirs,
                links: rep.links,
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
pub fn ext_edit_plan_json(
    py: Python<'_>,
    image: PathBuf,
    internal_path: String,
    new_len: u64,
) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<ExtWritePlanJson, String> {
            let abs = abs_path(&image)?;
            let plan = ext_cli::edit::plan_path_write(&abs, &internal_path, new_len)?;
            Ok(ExtWritePlanJson {
                mode: plan.mode,
                old_size: plan.old_size,
                new_size: plan.new_size,
                grow_bytes: plan.grow_bytes,
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
pub fn ext_edit_write(
    py: Python<'_>,
    image: PathBuf,
    internal_path: String,
    data: Vec<u8>,
) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<ExtWriteResultJson, String> {
            let abs = abs_path(&image)?;
            let res = ext_cli::edit::write_path_data(&abs, &internal_path, &data)?;
            Ok(ExtWriteResultJson {
                path: res.path,
                mode: res.mode,
                old_size: res.old_size,
                new_size: res.new_size,
                blocks_freed: res.blocks_freed,
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
pub fn ext_edit_chmod(
    py: Python<'_>,
    image: PathBuf,
    internal_path: String,
    perm_octal: u16,
) -> PyResult<u16> {
    py.detach(move || -> Result<u16, String> {
        let abs = abs_path(&image)?;
        ext_cli::edit::write_path_mode(&abs, &internal_path, perm_octal)
    })
    .map_err(PyOSError::new_err)
}

#[pyfunction]
pub fn ext_edit_selinux(
    py: Python<'_>,
    image: PathBuf,
    internal_path: String,
    new_ctx: String,
) -> PyResult<String> {
    py.detach(move || -> Result<String, String> {
        let abs = abs_path(&image)?;
        ext_cli::edit::write_path_selinux(&abs, &internal_path, &new_ctx)
    })
    .map_err(PyOSError::new_err)
}

#[pyfunction]
#[pyo3(signature = (image, internal_path, recursive=true))]
pub fn ext_edit_rm(
    py: Python<'_>,
    image: PathBuf,
    internal_path: String,
    recursive: bool,
) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<ExtUnlinkResultJson, String> {
            let abs = abs_path(&image)?;
            let res = ext_cli::edit::unlink_path(&abs, &internal_path, recursive)?;
            Ok(ExtUnlinkResultJson {
                removed: res.removed,
                was_dir: res.was_dir,
                entries_removed: res.entries_removed,
                blocks_freed: res.blocks_freed,
                mode: res.mode,
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[pyfunction]
#[pyo3(signature = (out_img, src_dir=None, size_bytes=0, mount_point=None, file_contexts=None, fs_config=None, block_size=4096, sparse=false, label=None, progress=None))]
#[allow(clippy::too_many_arguments)]
pub fn ext_make_fs(
    py: Python<'_>,
    out_img: PathBuf,
    src_dir: Option<PathBuf>,
    size_bytes: i64,
    mount_point: Option<String>,
    file_contexts: Option<PathBuf>,
    fs_config: Option<PathBuf>,
    block_size: u32,
    sparse: bool,
    label: Option<String>,
    progress: Option<Py<PyAny>>,
) -> PyResult<String> {
    let out_str = out_img.to_string_lossy().into_owned();
    let cb = progress.as_ref();
    py.detach(move || -> Result<(), String> {
        let sehnd = if let Some(ref fc_path) = file_contexts {
            Some(FileContexts::open(fc_path).map_err(|e| format!("{}: {e}", fc_path.display()))?)
        } else {
            None
        };

        let cfg = if let Some(ref cfg_path) = fs_config {
            FsConfigProvider::Canned(
                CannedFsConfig::load(cfg_path)
                    .map_err(|e| format!("failed to load {}: {e}", cfg_path.display()))?,
            )
        } else if mount_point.is_some() {
            FsConfigProvider::Default
        } else {
            FsConfigProvider::None
        };

        if let Some(parent) = out_img.parent() {
            if !parent.as_os_str().is_empty() && !parent.exists() {
                fs::create_dir_all(parent).map_err(|e| e.to_string())?;
            }
        }

        let out_file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&out_img)
            .map_err(|e| format!("open {}: {e}", out_img.display()))?;

        let opts = Ext4Options {
            len: size_bytes,
            block_size,
            sparse,
            label: label.unwrap_or_default(),
            ..Ext4Options::default()
        };

        let src_string = src_dir.map(|p| p.to_string_lossy().into_owned());
        let result = make_ext4fs_with_progress(
            OutputTarget::File(out_file),
            src_string.as_deref(),
            mount_point.as_deref(),
            &cfg,
            sehnd.as_ref(),
            &opts,
            None,
            |pct, stage| emit_py_progress(cb, pct, stage),
        );
        if let Err(err) = result {
            let _ = fs::remove_file(&out_img);
            return Err(err);
        }
        Ok(())
    })
    .map_err(PyOSError::new_err)?;
    Ok(out_str)
}

#[pyfunction]
#[pyo3(signature = (src_path, dst_path, block_size=4096, progress=None))]
pub fn sparse_img2simg(
    py: Python<'_>,
    src_path: PathBuf,
    dst_path: PathBuf,
    block_size: u32,
    progress: Option<Py<PyAny>>,
) -> PyResult<String> {
    if block_size < 1024 || block_size % 4 != 0 {
        return Err(PyValueError::new_err(format!(
            "block_size must be >= 1024 and divisible by 4 (got {block_size})"
        )));
    }
    let dst_str = dst_path.to_string_lossy().into_owned();
    let cb = progress.as_ref();
    py.detach(move || -> Result<(), String> {
        emit_py_progress(cb, 5, "Reading raw image");
        let in_file = File::open(&src_path)
            .map_err(|e| format!("Cannot open input file {}: {e}", src_path.display()))?;
        let mut sf = SparseFile::read_normal_file(in_file, block_size)
            .map_err(|e| format!("Failed to read raw file {}: {e}", src_path.display()))?;
        if let Some(parent) = dst_path.parent() {
            if !parent.as_os_str().is_empty() && !parent.exists() {
                fs::create_dir_all(parent).map_err(|e| e.to_string())?;
            }
        }
        let out_file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&dst_path)
            .map_err(|e| format!("Cannot open output file {}: {e}", dst_path.display()))?;
        if let Err(e) = sf.write_to_file_with_progress(out_file, false, true, false, |pct| {
            let mapped = 15 + ((pct as usize * 85) / 100).min(85) as u8;
            emit_py_progress(cb, mapped, "Writing sparse image");
        }) {
            let _ = fs::remove_file(&dst_path);
            return Err(format!("Failed to write sparse file: {e}"));
        }
        Ok(())
    })
    .map_err(PyOSError::new_err)?;
    Ok(dst_str)
}

#[pyfunction]
#[pyo3(signature = (src_path, dst_path, progress=None))]
pub fn sparse_simg2img(
    py: Python<'_>,
    src_path: PathBuf,
    dst_path: PathBuf,
    progress: Option<Py<PyAny>>,
) -> PyResult<String> {
    let dst_str = dst_path.to_string_lossy().into_owned();
    let cb = progress.as_ref();
    py.detach(move || -> Result<(), String> {
        emit_py_progress(cb, 5, "Reading sparse image");
        let in_file = File::open(&src_path)
            .map_err(|e| format!("Cannot open input file {}: {e}", src_path.display()))?;
        let mut sf = SparseFile::import_file(in_file, true, false)
            .map_err(|e| format!("Failed to read sparse file {}: {e}", src_path.display()))?;
        if let Some(parent) = dst_path.parent() {
            if !parent.as_os_str().is_empty() && !parent.exists() {
                fs::create_dir_all(parent).map_err(|e| e.to_string())?;
            }
        }
        let out_file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&dst_path)
            .map_err(|e| format!("Cannot open output file {}: {e}", dst_path.display()))?;
        if let Err(e) = sf.write_to_file_with_progress(out_file, false, false, false, |pct| {
            let mapped = 15 + ((pct as usize * 85) / 100).min(85) as u8;
            emit_py_progress(cb, mapped, "Writing raw image");
        }) {
            let _ = fs::remove_file(&dst_path);
            return Err(format!("Cannot write raw image {}: {e}", Path::new(&dst_path).display()));
        }
        Ok(())
    })
    .map_err(PyOSError::new_err)?;
    Ok(dst_str)
}

#[derive(Serialize)]
struct PacHeaderJson {
    version: String,
    size: u64,
    actual_size: u64,
    product_name: String,
    firmware_name: String,
    partition_count: u32,
    partitions_list_start: u32,
    mode: u32,
    flash_type: u32,
    nand_strategy: u32,
    is_nv_backup: u32,
    nand_page_type: u32,
    product_alias: String,
    oma_dm_product_flag: u32,
    is_oma_dm: u32,
    is_preload: u32,
    magic: u32,
    crc1: u16,
    crc2: u16,
}

#[derive(Serialize)]
struct PacEntryJson {
    index: usize,
    partition_name: String,
    file_name: String,
    reserved_file_name: String,
    size: u64,
    data_offset: u64,
    file_flag: u32,
    check_flag: u32,
    can_omit_flag: u32,
    address_count: u32,
    address: u32,
    header_length: u32,
}

#[derive(Serialize)]
struct PacListJson {
    header: PacHeaderJson,
    entries: Vec<PacEntryJson>,
}

#[derive(Serialize)]
struct PacCrcJson {
    ok: bool,
    header_crc_checked: bool,
    crc1_computed: Option<u16>,
    crc1_expected: Option<u16>,
    crc2_computed: u16,
    crc2_expected: u16,
}

#[derive(Serialize)]
struct PacExtractJson {
    out_dir: String,
    extracted: Vec<String>,
}

#[pyfunction]
pub fn pac_list_json(py: Python<'_>, path: PathBuf) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<PacListJson, String> {
            let actual_size = fs::metadata(&path).map_err(|e| e.to_string())?.len();
            let mut reader = open_pac_file(&path).map_err(|e| e.to_string())?;
            let entries = reader.read_partitions().map_err(|e| e.to_string())?;
            let h = reader.header();
            Ok(PacListJson {
                header: PacHeaderJson {
                    version: h.version.clone(),
                    size: h.total_size(),
                    actual_size,
                    product_name: h.product_name.clone(),
                    firmware_name: h.firmware_name.clone(),
                    partition_count: h.partition_count,
                    partitions_list_start: h.partitions_list_start,
                    mode: h.mode,
                    flash_type: h.flash_type,
                    nand_strategy: h.nand_strategy,
                    is_nv_backup: h.is_nv_backup,
                    nand_page_type: h.nand_page_type,
                    product_alias: h.product_alias.clone(),
                    oma_dm_product_flag: h.oma_dm_product_flag,
                    is_oma_dm: h.is_oma_dm,
                    is_preload: h.is_preload,
                    magic: h.magic,
                    crc1: h.crc1,
                    crc2: h.crc2,
                },
                entries: entries
                    .into_iter()
                    .enumerate()
                    .map(|(index, entry)| PacEntryJson {
                        index,
                        size: entry.partition_size(),
                        data_offset: entry.data_offset(),
                        partition_name: entry.partition_id,
                        file_name: entry.file_name,
                        reserved_file_name: entry.reserved_file_name,
                        file_flag: entry.file_flag,
                        check_flag: entry.check_flag,
                        can_omit_flag: entry.can_omit_flag,
                        address_count: entry.addr_num,
                        address: entry.addrs[0],
                        header_length: entry.struct_size,
                    })
                    .collect(),
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}



#[pyfunction]
pub fn pac_verify_crc_json(py: Python<'_>, path: PathBuf) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<PacCrcJson, String> {
            let mut reader = open_pac_file(&path).map_err(|e| e.to_string())?;
            let summary = reader.verify_crc(|_, _| {}).map_err(|e| e.to_string())?;
            Ok(PacCrcJson {
                ok: true,
                header_crc_checked: summary.header_crc.is_some(),
                crc1_computed: summary.header_crc.map(|(c, _)| c),
                crc1_expected: summary.header_crc.map(|(_, e)| e),
                crc2_computed: summary.payload_crc.0,
                crc2_expected: summary.payload_crc.1,
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}



#[pyfunction]
#[pyo3(signature = (path, out_dir, selected=None, verify_crc=false))]
pub fn pac_extract_json(
    py: Python<'_>,
    path: PathBuf,
    out_dir: PathBuf,
    selected: Option<Vec<String>>,
    verify_crc: bool,
) -> PyResult<String> {
    let out_dir_str = out_dir.to_string_lossy().into_owned();
    let payload = py
        .detach(move || -> Result<PacExtractJson, String> {
            let mut reader = open_pac_file(&path).map_err(|e| e.to_string())?;
            if verify_crc {
                reader.verify_crc(|_, _| {}).map_err(|e| e.to_string())?;
            }
            let entries = reader.read_partitions().map_err(|e| e.to_string())?;
            prepare_output_dir(&out_dir).map_err(|e| e.to_string())?;
            let sel_set: Option<HashSet<String>> = selected
                .filter(|v| !v.is_empty())
                .map(|v| v.into_iter().collect());
            let mut extracted = Vec::new();
            for entry in entries {
                if entry.partition_size() == 0 || entry.file_name.is_empty() {
                    continue;
                }
                let leaf = entry
                    .file_name
                    .rsplit(['/', '\\'])
                    .find(|segment| !segment.is_empty())
                    .unwrap_or(&entry.file_name);
                if let Some(ref sel) = sel_set {
                    if !sel.contains(&entry.file_name) && !sel.contains(leaf) {
                        continue;
                    }
                }
                if let Some(dest) = reader
                    .extract_entry(&entry, &out_dir, |_, _| {})
                    .map_err(|e| e.to_string())?
                {
                    extracted.push(dest.to_string_lossy().into_owned());
                }
            }
            Ok(PacExtractJson {
                out_dir: out_dir_str,
                extracted,
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}



// ─── Boot image manipulation (magiskboot) ────────────────────────────────────

#[derive(Serialize)]
struct BootUnpackJson {
    out_dir: String,
    header_version: u32,
    is_vendor: bool,
    v4_entries: usize,
    files: Vec<String>,
}

#[derive(Serialize)]
struct BootRepackJson {
    out_img: String,
    size: u64,
}

#[derive(Serialize)]
struct BootCompressJson {
    output: String,
    format: String,
    input_size: u64,
    output_size: u64,
}

#[derive(Serialize)]
struct BootDecompressJson {
    output: String,
    format: String,
    input_size: u64,
    output_size: u64,
}

#[derive(Serialize)]
struct CpioOpsJson {
    ok: bool,
    exit_code: i32,
}

#[derive(Serialize)]
struct DtbPatchJson {
    patched: bool,
}

// Serializes working-directory swaps required by magiskboot's CWD-relative CLI
// helpers and restores the original CWD on drop even if an operation errors.
static MAGISKBOOT_CWD_LOCK: Mutex<()> = Mutex::new(());

struct CwdGuard<'a> {
    _lock: MutexGuard<'a, ()>,
    prev: Option<PathBuf>,
}

impl<'a> CwdGuard<'a> {
    fn enter(dir: &Path) -> Result<Self, String> {
        let lock = MAGISKBOOT_CWD_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let prev = std::env::current_dir().ok();
        std::env::set_current_dir(dir)
            .map_err(|e| format!("cannot cd to {}: {e}", dir.display()))?;
        Ok(Self { _lock: lock, prev })
    }
}

impl Drop for CwdGuard<'_> {
    fn drop(&mut self) {
        if let Some(ref prev) = self.prev {
            let _ = std::env::set_current_dir(prev);
        }
    }
}

fn abs_path(path: &Path) -> Result<PathBuf, String> {
    std::path::absolute(path).map_err(|e| format!("cannot resolve path {}: {e}", path.display()))
}

/// Inspect a boot / vendor_boot image header without extracting components.
#[pyfunction]
pub fn boot_inspect_json(py: Python<'_>, image: PathBuf) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<magiskboot::BootHeaderInfo, String> {
            let abs = abs_path(&image)?;
            inspect_boot_image(&abs.to_string_lossy())
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Unpack a boot / vendor_boot image into `out_dir`, writing component files.
/// Returns JSON: { out_dir, header_version, is_vendor, v4_entries, files }.
#[pyfunction]
#[pyo3(signature = (image, out_dir, skip_decomp=false, dump_hdr=true))]
pub fn boot_unpack_json(
    py: Python<'_>,
    image: PathBuf,
    out_dir: PathBuf,
    skip_decomp: bool,
    dump_hdr: bool,
) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<BootUnpackJson, String> {
            let image_abs = abs_path(&image)?;
            let out_abs = abs_path(&out_dir)?;
            let out_dir_str = out_abs.to_string_lossy().into_owned();
            let image_str = image_abs.to_string_lossy().into_owned();
            let info = inspect_boot_image(&image_str)?;
            fs::create_dir_all(&out_abs)
                .map_err(|e| format!("cannot create out_dir: {e}"))?;
            {
                let _cwd = CwdGuard::enter(&out_abs)?;
                let _exit = magiskboot::cmd_unpack(&image_str, skip_decomp, dump_hdr)?;
            }
            let files: Vec<String> = fs::read_dir(&out_abs)
                .map(|rd| {
                    rd.flatten()
                        .map(|e| e.file_name().to_string_lossy().into_owned())
                        .collect()
                })
                .unwrap_or_default();
            Ok(BootUnpackJson {
                out_dir: out_dir_str,
                header_version: info.header_version,
                is_vendor: info.is_vendor,
                v4_entries: info.v4_entries,
                files,
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Repack component files in `meta_dir` using `orig_img` as the reference image.
/// Writes to `out_img`. Returns JSON: { out_img, size }.
#[pyfunction]
#[pyo3(signature = (meta_dir, orig_img, out_img, skip_comp=false))]
pub fn boot_repack_json(
    py: Python<'_>,
    meta_dir: PathBuf,
    orig_img: PathBuf,
    out_img: PathBuf,
    skip_comp: bool,
) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<BootRepackJson, String> {
            let meta_abs = abs_path(&meta_dir)?;
            let orig_abs = abs_path(&orig_img)?;
            let out_abs = abs_path(&out_img)?;
            let orig_str = orig_abs.to_string_lossy().into_owned();
            let out_str = out_abs.to_string_lossy().into_owned();
            {
                let _cwd = CwdGuard::enter(&meta_abs)?;
                magiskboot::cmd_repack(&orig_str, &out_str, skip_comp)?;
            }
            let size = fs::metadata(&out_abs).map(|m| m.len()).unwrap_or(0);
            Ok(BootRepackJson {
                out_img: out_str,
                size,
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Patch DTB fstab flags in-place using magiskboot's native DTB patcher.
#[pyfunction]
pub fn boot_dtb_patch_json(py: Python<'_>, dtb_path: PathBuf) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<DtbPatchJson, String> {
            let abs = abs_path(&dtb_path)?;
            let patched = dtb_patch(&abs.to_string_lossy())?;
            Ok(DtbPatchJson { patched })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Compress a file using the specified format.
/// `fmt` is one of: gzip, zopfli, xz, lzma, bzip2, lz4, lz4_legacy, lz4_lg, zstd.
/// Returns JSON: { output, format, input_size, output_size }.
#[pyfunction]
#[pyo3(signature = (input, output, fmt))]
pub fn boot_compress_json(
    py: Python<'_>,
    input: PathBuf,
    output: PathBuf,
    fmt: String,
) -> PyResult<String> {
    let output_str = output.to_string_lossy().into_owned();
    let payload = py
        .detach(move || -> Result<BootCompressJson, String> {
            let data = fs::read(&input).map_err(|e| format!("read input: {e}"))?;
            let input_size = data.len() as u64;
            let format = Format::from_name(&fmt);
            if format == Format::Unknown {
                return Err(format!("unknown compression format: {fmt}"));
            }
            let compressed = compress_bytes(format, &data)?;
            let output_size = compressed.len() as u64;
            fs::write(&output, &compressed).map_err(|e| format!("write output: {e}"))?;
            Ok(BootCompressJson {
                output: output_str,
                format: fmt,
                input_size,
                output_size,
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Auto-detect and decompress a file.
/// Returns JSON: { output, format, input_size, output_size }.
#[pyfunction]
pub fn boot_decompress_json(
    py: Python<'_>,
    input: PathBuf,
    output: PathBuf,
) -> PyResult<String> {
    let output_str = output.to_string_lossy().into_owned();
    let payload = py
        .detach(move || -> Result<BootDecompressJson, String> {
            let data = fs::read(&input).map_err(|e| format!("read input: {e}"))?;
            let input_size = data.len() as u64;
            let format = detect_format(&data);
            if !format.is_compressed() {
                return Err(format!(
                    "file is not a supported compressed type (detected: {})",
                    format.name()
                ));
            }
            let decompressed = decompress_bytes(format, &data)?;
            let output_size = decompressed.len() as u64;
            fs::write(&output, &decompressed).map_err(|e| format!("write output: {e}"))?;
            Ok(BootDecompressJson {
                output: output_str,
                format: format.name().to_owned(),
                input_size,
                output_size,
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Detect compression format of a file. Returns format name string (e.g. "gzip", "raw").
#[pyfunction]
pub fn boot_detect_format(py: Python<'_>, path: PathBuf) -> PyResult<String> {
    py.detach(move || -> Result<String, String> {
        let data = fs::read(&path).map_err(|e| format!("read: {e}"))?;
        Ok(detect_format(&data).name().to_owned())
    })
    .map_err(PyOSError::new_err)
}

/// Run magiskboot cpio commands on a cpio archive file.
/// `commands` is a list of cpio sub-commands e.g. ["extract", "add 0644 init /path/to/init"].
/// Returns JSON: { ok, exit_code }.
#[pyfunction]
pub fn boot_cpio_json(
    py: Python<'_>,
    cpio: PathBuf,
    commands: Vec<String>,
) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<CpioOpsJson, String> {
            let cpio_abs = abs_path(&cpio)?;
            let parent = cpio_abs
                .parent()
                .ok_or_else(|| format!("cpio path has no parent: {}", cpio_abs.display()))?;
            let cpio_name = cpio_abs
                .file_name()
                .map(|f| f.to_string_lossy().into_owned())
                .ok_or_else(|| format!("invalid cpio file name: {}", cpio_abs.display()))?;
            let mut args: Vec<String> = vec![cpio_name];
            args.extend(commands);
            let exit_code = {
                let _cwd = CwdGuard::enter(parent)?;
                magiskboot::cmd_cpio(&args)?
            };
            Ok(CpioOpsJson {
                ok: exit_code == 0,
                exit_code,
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

// =========================================================================
// EROFS (erofs-rs) in-process bindings
// =========================================================================

#[derive(Serialize)]
struct ErofsDetectJson {
    is_erofs: bool,
    is_sparse: bool,
    error: Option<String>,
}

/// Detect if path is a valid EROFS filesystem (and/or Android sparse file).
#[pyfunction]
#[pyo3(signature = (path, offset=None))]
pub fn erofs_detect_json(py: Python<'_>, path: PathBuf, offset: Option<u64>) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<ErofsDetectJson, String> {
            let abs = abs_path(&path)?;
            let is_sparse = erofs_rs::sparse::is_sparse_file(&abs);
            let off = offset.unwrap_or(0);
            match erofs_rs::reader::ErofsImage::open(&abs, off) {
                Ok(_img) => Ok(ErofsDetectJson {
                    is_erofs: true,
                    is_sparse,
                    error: None,
                }),
                Err(e) => Ok(ErofsDetectJson {
                    is_erofs: false,
                    is_sparse,
                    error: Some(e.to_string()),
                }),
            }
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Inspect EROFS superblock, features, block counts, and UUID.
#[pyfunction]
#[pyo3(signature = (path, offset=None))]
pub fn erofs_info_json(py: Python<'_>, path: PathBuf, offset: Option<u64>) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<erofs_rs::reader::ImageSummary, String> {
            let abs = abs_path(&path)?;
            let img = erofs_rs::reader::ErofsImage::open(&abs, offset.unwrap_or(0))
                .map_err(|e| e.to_string())?;
            Ok(img.summary())
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// List files and directories within an EROFS image.
#[pyfunction]
#[pyo3(signature = (path, target=None, recursive=true, offset=None))]
pub fn erofs_list_json(
    py: Python<'_>,
    path: PathBuf,
    target: Option<String>,
    recursive: bool,
    offset: Option<u64>,
) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<Vec<erofs_rs::reader::FileNode>, String> {
            let abs = abs_path(&path)?;
            let img = erofs_rs::reader::ErofsImage::open(&abs, offset.unwrap_or(0))
                .map_err(|e| e.to_string())?;
            let nodes = match target {
                Some(ref t) => img.walk_path(t, recursive),
                None => img.walk(true),
            }
            .map_err(|e| e.to_string())?;
            Ok(nodes)
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Stat an individual file/folder inside an EROFS image without unpacking.
#[pyfunction]
#[pyo3(signature = (path, internal_path, offset=None))]
pub fn erofs_stat_json(
    py: Python<'_>,
    path: PathBuf,
    internal_path: String,
    offset: Option<u64>,
) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<erofs_rs::reader::FileNode, String> {
            let abs = abs_path(&path)?;
            let img = erofs_rs::reader::ErofsImage::open(&abs, offset.unwrap_or(0))
                .map_err(|e| e.to_string())?;
            let inode = img.lookup(&internal_path).map_err(|e| e.to_string())?;
            img.build_node(internal_path, &inode).map_err(|e| e.to_string())
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Read file bytes directly from an EROFS image without unpacking.
#[pyfunction]
#[pyo3(signature = (path, internal_path, offset=None))]
pub fn erofs_read_bytes(
    py: Python<'_>,
    path: PathBuf,
    internal_path: String,
    offset: Option<u64>,
) -> PyResult<Vec<u8>> {
    py.detach(move || -> Result<Vec<u8>, String> {
        let abs = abs_path(&path)?;
        let img = erofs_rs::reader::ErofsImage::open(&abs, offset.unwrap_or(0))
            .map_err(|e| e.to_string())?;
        let (data, _node) = img.read_path(&internal_path).map_err(|e| e.to_string())?;
        Ok(data)
    })
    .map_err(PyOSError::new_err)
}

/// Unpack an EROFS image into an output directory with optional Android config generation.
#[pyfunction]
#[pyo3(signature = (
    image,
    out_dir,
    image_base_name=None,
    targets=None,
    recursive=true,
    config_only=false,
    write_configs=true,
    overwrite=true,
    threads=0,
    offset=None,
    symlink_mode=None,
    progress=None
))]
#[allow(clippy::too_many_arguments)]
pub fn erofs_unpack(
    py: Python<'_>,
    image: PathBuf,
    out_dir: PathBuf,
    image_base_name: Option<String>,
    targets: Option<Vec<String>>,
    recursive: bool,
    config_only: bool,
    write_configs: bool,
    overwrite: bool,
    threads: usize,
    offset: Option<u64>,
    symlink_mode: Option<String>,
    progress: Option<Py<PyAny>>,
) -> PyResult<String> {
    let cb = progress.as_ref();
    let payload = py
        .detach(move || -> Result<erofs_rs::extract::ExtractReport, String> {
            let img_abs = abs_path(&image)?;
            let out_abs = abs_path(&out_dir)?;
            let parsed_symlink_mode = match symlink_mode.as_deref() {
                Some(s) if !s.trim().is_empty() => {
                    erofs_rs::extract::SymlinkMode::parse(s).map_err(|e| e.to_string())?
                }
                _ => erofs_rs::extract::SymlinkMode::Auto,
            };
            let base_name = image_base_name.unwrap_or_else(|| {
                image
                    .file_stem()
                    .map(|s| s.to_string_lossy().into_owned())
                    .unwrap_or_else(|| "image".to_string())
            });
            let img = erofs_rs::reader::ErofsImage::open(&img_abs, offset.unwrap_or(0))
                .map_err(|e| e.to_string())?;
            let opts = erofs_rs::extract::ExtractOptions {
                out_dir: out_abs,
                image_base_name: base_name,
                targets: targets.unwrap_or_default(),
                recursive,
                config_only,
                write_configs,
                overwrite,
                verify_digest: false,
                symlink_mode: parsed_symlink_mode,
                threads,
                quiet: true,
            };
            erofs_rs::extract::extract_image_with_progress(&img, &opts, |pct, stage| {
                emit_py_progress(cb, pct, stage);
            })
            .map_err(|e| e.to_string())
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Pack a directory into a standard Linux/Android EROFS image.
#[pyfunction]
#[pyo3(signature = (
    source_dir,
    output_image,
    algo=None,
    level=None,
    pcluster_size=None,
    mount_point=None,
    fs_config=None,
    file_contexts=None,
    uuid=None,
    timestamp=None,
    block_size=None,
    inline_data=None,
    inode_format=None,
    force_uid=None,
    force_gid=None,
    all_root=None,
    enable_crc=None,
    threads=0,
    progress=None
))]
#[allow(clippy::too_many_arguments)]
pub fn erofs_pack(
    py: Python<'_>,
    source_dir: PathBuf,
    output_image: PathBuf,
    algo: Option<String>,
    level: Option<i32>,
    pcluster_size: Option<u32>,
    mount_point: Option<String>,
    fs_config: Option<PathBuf>,
    file_contexts: Option<PathBuf>,
    uuid: Option<String>,
    timestamp: Option<u64>,
    block_size: Option<u32>,
    inline_data: Option<bool>,
    inode_format: Option<String>,
    force_uid: Option<u32>,
    force_gid: Option<u32>,
    all_root: Option<bool>,
    enable_crc: Option<bool>,
    threads: usize,
    progress: Option<Py<PyAny>>,
) -> PyResult<String> {
    let cb = progress.as_ref();
    let payload = py
        .detach(move || -> Result<erofs_rs::pack::PackReport, String> {
            let src_abs = abs_path(&source_dir)?;
            let out_abs = abs_path(&output_image)?;
            let mut opts = erofs_rs::pack::PackOptions::default();
            opts.source_dir = src_abs;
            opts.output_image = out_abs;

            if let Some(ref a) = algo {
                let clean_a = a.trim_start_matches("-z").trim();
                let (alg_name, alg_lvl) = match clean_a.split_once(',') {
                    Some((n, l)) => (n, l.parse::<i32>().ok()),
                    None => (clean_a, None),
                };
                opts.algorithm = erofs_rs::compress::CompressionAlg::parse(alg_name)
                    .map_err(|e| e.to_string())?;
                if let Some(l) = alg_lvl {
                    opts.level = Some(l);
                }
            }
            if let Some(l) = level {
                opts.level = Some(l);
            }
            if let Some(pc) = pcluster_size {
                opts.pcluster_size = pc;
            }
            if let Some(mp) = mount_point {
                opts.mount_point = if mp.starts_with('/') {
                    mp
                } else {
                    format!("/{mp}")
                };
            }
            if let Some(fc) = fs_config {
                opts.fs_config = Some(abs_path(&fc)?);
            }
            if let Some(sx) = file_contexts {
                opts.file_contexts = Some(abs_path(&sx)?);
            }
            opts.uuid = uuid;
            opts.timestamp = timestamp;
            if let Some(bs) = block_size {
                opts.block_size = bs;
            }
            if let Some(inline) = inline_data {
                opts.inline_data = inline;
            }
            if let Some(ref fmt) = inode_format {
                opts.inode_format = erofs_rs::pack::InodeFormatMode::parse(fmt)
                    .map_err(|e| e.to_string())?;
            }
            opts.force_uid = force_uid;
            opts.force_gid = force_gid;
            if let Some(ar) = all_root {
                opts.all_root = ar;
            }
            if let Some(crc) = enable_crc {
                opts.enable_crc = crc;
            }
            opts.threads = threads;

            erofs_rs::pack::pack_directory_with_progress(&opts, |pct, stage| {
                emit_py_progress(cb, pct, stage);
            })
            .map_err(|e| e.to_string())
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Verify integrity of an EROFS filesystem image (fsck equivalent).
#[pyfunction]
#[pyo3(signature = (image, check_data=true, verify_digest=false, threads=0, offset=None))]
pub fn erofs_verify_json(
    py: Python<'_>,
    image: PathBuf,
    check_data: bool,
    verify_digest: bool,
    threads: usize,
    offset: Option<u64>,
) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<erofs_rs::verify::VerifyReport, String> {
            let abs = abs_path(&image)?;
            let img = erofs_rs::reader::ErofsImage::open(&abs, offset.unwrap_or(0))
                .map_err(|e| e.to_string())?;
            erofs_rs::verify::verify_image(&img, check_data, verify_digest, threads)
                .map_err(|e| e.to_string())
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Direct edit: write file data in-place or append without unpacking.
#[pyfunction]
#[pyo3(signature = (image, internal_path, data, allow_grow=true, strip_avb=true))]
pub fn erofs_edit_write(
    py: Python<'_>,
    image: PathBuf,
    internal_path: String,
    data: Vec<u8>,
    allow_grow: bool,
    strip_avb: bool,
) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<erofs_rs::edit::WriteResult, String> {
            let abs = abs_path(&image)?;
            erofs_rs::edit::write_path_data(&abs, &internal_path, &data, allow_grow, strip_avb)
                .map_err(|e| e.to_string())
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Direct edit: update inode mode/permissions in-place.
#[pyfunction]
pub fn erofs_edit_chmod(
    py: Python<'_>,
    image: PathBuf,
    internal_path: String,
    perm_octal: u16,
) -> PyResult<u16> {
    py.detach(move || -> Result<u16, String> {
        let abs = abs_path(&image)?;
        erofs_rs::edit::write_path_mode(&abs, &internal_path, perm_octal)
            .map_err(|e| e.to_string())
    })
    .map_err(PyOSError::new_err)
}

/// Direct edit: update SELinux security context in-place.
#[pyfunction]
pub fn erofs_edit_selinux(
    py: Python<'_>,
    image: PathBuf,
    internal_path: String,
    new_ctx: String,
) -> PyResult<String> {
    py.detach(move || -> Result<String, String> {
        let abs = abs_path(&image)?;
        erofs_rs::edit::write_path_selinux(&abs, &internal_path, &new_ctx)
            .map_err(|e| e.to_string())
    })
    .map_err(PyOSError::new_err)
}

/// Direct edit: remove (unlink) a file or directory in-place.
#[pyfunction]
#[pyo3(signature = (image, internal_path, recursive=false, strip_avb=true))]
pub fn erofs_edit_rm(
    py: Python<'_>,
    image: PathBuf,
    internal_path: String,
    recursive: bool,
    strip_avb: bool,
) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<erofs_rs::edit::UnlinkResult, String> {
            let abs = abs_path(&image)?;
            erofs_rs::edit::unlink_path(&abs, &internal_path, recursive, strip_avb)
                .map_err(|e| e.to_string())
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// In-place compaction: reclaim holes and relocate metadata.
#[pyfunction]
#[pyo3(signature = (image, dry_run=false, relocate_meta=true))]
pub fn erofs_compact(
    py: Python<'_>,
    image: PathBuf,
    dry_run: bool,
    relocate_meta: bool,
) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<erofs_rs::compact::CompactReport, String> {
            let abs = abs_path(&image)?;
            erofs_rs::compact::compact_image(&abs, dry_run, relocate_meta)
                .map_err(|e| e.to_string())
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Space usage breakdown of an EROFS image.
#[pyfunction]
#[pyo3(signature = (image, offset=None))]
pub fn erofs_usage_json(py: Python<'_>, image: PathBuf, offset: Option<u64>) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<erofs_rs::edit::UsageReport, String> {
            let abs = abs_path(&image)?;
            erofs_rs::edit::usage_report(&abs, offset.unwrap_or(0))
                .map_err(|e| e.to_string())
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Inspect AVB footer on an EROFS image file.
#[pyfunction]
pub fn erofs_avb_inspect_json(py: Python<'_>, image: PathBuf) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<Option<erofs_rs::avb::AvbFooter>, String> {
            let abs = abs_path(&image)?;
            let mut f = File::open(&abs).map_err(|e| e.to_string())?;
            erofs_rs::avb::read_avb_footer(&mut f).map_err(|e| e.to_string())
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Strip AVB footer from an EROFS image file.
#[pyfunction]
pub fn erofs_avb_strip_json(py: Python<'_>, image: PathBuf) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<erofs_rs::avb::AvbFooter, String> {
            let abs = abs_path(&image)?;
            let mut f = OpenOptions::new()
                .read(true)
                .write(true)
                .open(&abs)
                .map_err(|e| e.to_string())?;
            let sb = erofs_rs::edit::read_sb_file(&mut f).map_err(|e| e.to_string())?;
            erofs_rs::avb::strip_avb_footer(&mut f, &sb).map_err(|e| e.to_string())
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Plan an in-place/inline/append file write inside an EROFS image without mutating it.
#[pyfunction]
#[pyo3(signature = (image, internal_path, new_len, strip_avb=true))]
pub fn erofs_edit_plan_json(
    py: Python<'_>,
    image: PathBuf,
    internal_path: String,
    new_len: u64,
    strip_avb: bool,
) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<erofs_rs::edit::WritePlan, String> {
            let abs = abs_path(&image)?;
            erofs_rs::edit::plan_path_write(&abs, &internal_path, new_len, strip_avb)
                .map_err(|e| e.to_string())
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

#[derive(Serialize)]
struct ErofsChildEntry {
    nid: u64,
    name: String,
    is_dir: bool,
    is_link: bool,
    size: u64,
}

/// Persistent native EROFS reader handle for browsing/mounting/APEX payloads.
#[pyclass]
pub struct NativeErofsReader {
    img: Mutex<Option<Arc<erofs_rs::reader::ErofsImage>>>,
}

impl NativeErofsReader {
    pub(crate) fn from_erofs(img: erofs_rs::reader::ErofsImage) -> Self {
        Self {
            img: Mutex::new(Some(Arc::new(img))),
        }
    }

    pub(crate) fn get_image(&self) -> PyResult<Arc<erofs_rs::reader::ErofsImage>> {
        let guard = self
            .img
            .lock()
            .map_err(|_| PyOSError::new_err("NativeErofsReader mutex poisoned"))?;
        guard
            .as_ref()
            .cloned()
            .ok_or_else(|| PyValueError::new_err("NativeErofsReader is closed"))
    }
}

#[pymethods]
impl NativeErofsReader {
    #[new]
    #[pyo3(signature = (path, offset=0))]
    fn new(py: Python<'_>, path: PathBuf, offset: u64) -> PyResult<Self> {
        let img = py
            .detach(move || -> Result<erofs_rs::reader::ErofsImage, String> {
                let abs = abs_path(&path)?;
                erofs_rs::reader::ErofsImage::open(&abs, offset).map_err(|e| e.to_string())
            })
            .map_err(PyOSError::new_err)?;
        Ok(Self {
            img: Mutex::new(Some(Arc::new(img))),
        })
    }

    #[staticmethod]
    fn from_bytes(py: Python<'_>, data: &[u8]) -> PyResult<Self> {
        let owned = data.to_vec();
        let img = py
            .detach(move || -> Result<erofs_rs::reader::ErofsImage, String> {
                erofs_rs::reader::ErofsImage::open_bytes(owned).map_err(|e| e.to_string())
            })
            .map_err(PyOSError::new_err)?;
        Ok(Self {
            img: Mutex::new(Some(Arc::new(img))),
        })
    }

    #[getter]
    fn root_nid(&self) -> PyResult<u64> {
        Ok(self.get_image()?.sb.root_nid)
    }

    #[getter]
    fn is_sparse(&self) -> PyResult<bool> {
        Ok(self.get_image()?.source.is_sparse())
    }

    #[pyo3(signature = (include_root=false))]
    fn walk_json(&self, py: Python<'_>, include_root: bool) -> PyResult<String> {
        let img = self.get_image()?;
        let nodes = py
            .detach(move || img.walk(include_root).map_err(|e| e.to_string()))
            .map_err(PyOSError::new_err)?;
        serde_json::to_string(&nodes).map_err(|e| PyOSError::new_err(e.to_string()))
    }

    fn children_json(&self, py: Python<'_>, nid: u64) -> PyResult<String> {
        let img = self.get_image()?;
        let entries = py
            .detach(move || -> Result<Vec<ErofsChildEntry>, String> {
                let inode = img.read_inode(nid).map_err(|e| e.to_string())?;
                let ents = img.read_dir(&inode, false).map_err(|e| e.to_string())?;
                let packed_nid = img.sb.packed_nid;
                let metabox_nid = img.sb.metabox_nid;
                let mut out = Vec::with_capacity(ents.len());
                for ent in ents {
                    if (packed_nid != 0 && ent.nid == packed_nid)
                        || (metabox_nid != 0 && ent.nid == metabox_nid)
                    {
                        continue;
                    }
                    let child = img.read_inode(ent.nid).map_err(|e| e.to_string())?;
                    let is_dir = ent.file_type == erofs_rs::format::FT_DIR
                        || (child.mode & erofs_rs::format::S_IFMT) == erofs_rs::format::S_IFDIR;
                    let is_link = ent.file_type == erofs_rs::format::FT_SYMLINK
                        || (child.mode & erofs_rs::format::S_IFMT) == erofs_rs::format::S_IFLNK;
                    out.push(ErofsChildEntry {
                        nid: ent.nid,
                        name: ent.name,
                        is_dir,
                        is_link,
                        size: child.size,
                    });
                }
                Ok(out)
            })
            .map_err(PyOSError::new_err)?;
        serde_json::to_string(&entries).map_err(|e| PyOSError::new_err(e.to_string()))
    }

    fn stat_nid_json(&self, py: Python<'_>, nid: u64) -> PyResult<String> {
        let img = self.get_image()?;
        let node = py
            .detach(move || -> Result<erofs_rs::reader::FileNode, String> {
                let inode = img.read_inode(nid).map_err(|e| e.to_string())?;
                img.build_node("/".to_string(), &inode)
                    .map_err(|e| e.to_string())
            })
            .map_err(PyOSError::new_err)?;
        serde_json::to_string(&node).map_err(|e| PyOSError::new_err(e.to_string()))
    }

    fn stat_path_json(&self, py: Python<'_>, internal_path: String) -> PyResult<String> {
        let img = self.get_image()?;
        let node = py
            .detach(move || -> Result<erofs_rs::reader::FileNode, String> {
                let inode = img.lookup(&internal_path).map_err(|e| e.to_string())?;
                let norm = format!("/{}", internal_path.trim_start_matches('/'));
                img.build_node(norm, &inode).map_err(|e| e.to_string())
            })
            .map_err(PyOSError::new_err)?;
        serde_json::to_string(&node).map_err(|e| PyOSError::new_err(e.to_string()))
    }

    fn read_file(&self, py: Python<'_>, nid: u64) -> PyResult<Py<PyBytes>> {
        let img = self.get_image()?;
        let data = py
            .detach(move || -> Result<Vec<u8>, String> {
                let inode = img.read_inode(nid).map_err(|e| e.to_string())?;
                img.read_inode_bytes(&inode).map_err(|e| e.to_string())
            })
            .map_err(PyOSError::new_err)?;
        Ok(PyBytes::new(py, &data).unbind())
    }

    fn read_path(&self, py: Python<'_>, internal_path: String) -> PyResult<Py<PyBytes>> {
        let img = self.get_image()?;
        let data = py
            .detach(move || -> Result<Vec<u8>, String> {
                let (bytes, _node) = img.read_path(&internal_path).map_err(|e| e.to_string())?;
                Ok(bytes)
            })
            .map_err(PyOSError::new_err)?;
        Ok(PyBytes::new(py, &data).unbind())
    }

    fn close(&self) {
        if let Ok(mut guard) = self.img.lock() {
            *guard = None;
        }
    }

    fn __enter__(slf: Py<Self>) -> Py<Self> {
        slf
    }

    #[pyo3(signature = (_exc_type=None, _exc_val=None, _exc_tb=None))]
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

// =========================================================================
// sdatimg (sdat2img & img2sdat) in-process bindings
// =========================================================================

#[derive(Serialize)]
struct TransferListInfoJson {
    version: u32,
    new_blocks: u64,
    stash_slots: Option<u64>,
    max_stashed_blocks: Option<u64>,
    commands_count: usize,
    total_bytes: u64,
}

/// Inspect an Android OTA transfer.list file.
#[pyfunction]
pub fn sdat_transfer_list_info_json(py: Python<'_>, path: PathBuf) -> PyResult<String> {
    let payload = py
        .detach(move || -> Result<TransferListInfoJson, String> {
            let abs = abs_path(&path)?;
            let tl = sdatimg::parse_transfer_list_file(&abs).map_err(|e| e.to_string())?;
            let max_end_block = tl
                .commands
                .iter()
                .flat_map(|c| c.ranges.iter().map(|&(_, end)| end))
                .max()
                .unwrap_or(0);
            Ok(TransferListInfoJson {
                version: tl.version,
                new_blocks: tl.new_blocks,
                stash_slots: tl.stash_slots,
                max_stashed_blocks: tl.max_stashed_blocks,
                commands_count: tl.commands.len(),
                total_bytes: max_end_block * 4096,
            })
        })
        .map_err(PyOSError::new_err)?;
    serde_json::to_string(&payload).map_err(|e| PyOSError::new_err(e.to_string()))
}

/// Convert Android block-based OTA files (`transfer.list` + `new.dat`) to partition image (`.img`).
#[pyfunction]
#[pyo3(signature = (
    transfer_list,
    new_dat,
    output_image,
    sparse_output=false,
    no_clobber=false,
    quiet=true
))]
pub fn sdat_sdat2img(
    py: Python<'_>,
    transfer_list: PathBuf,
    new_dat: PathBuf,
    output_image: PathBuf,
    sparse_output: bool,
    no_clobber: bool,
    quiet: bool,
) -> PyResult<String> {
    let output_str = py
        .detach(move || -> Result<String, String> {
            let tl_abs = abs_path(&transfer_list)?;
            let dat_abs = abs_path(&new_dat)?;
            let out_abs = abs_path(&output_image)?;

            if let Some(parent) = out_abs.parent() {
                fs::create_dir_all(parent)
                    .map_err(|e| format!("cannot create output directory: {e}"))?;
            }

            let opts = sdatimg::Sdat2ImgOptions {
                sparse_output,
                no_clobber,
                quiet,
            };

            sdatimg::sdat2img(&tl_abs, &dat_abs, &out_abs, &opts).map_err(|e| e.to_string())?;
            Ok(out_abs.to_string_lossy().into_owned())
        })
        .map_err(PyOSError::new_err)?;
    Ok(output_str)
}

/// Convert partition image (sparse or raw) to Android block-based OTA files (`.new.dat`, `.transfer.list`, `.patch.dat`).
#[pyfunction]
#[pyo3(signature = (
    input_image,
    outdir,
    version=4,
    prefix=None,
    input_format=None,
    file_map=None,
    clobbered_blocks=None,
    src_image=None,
    threads=None,
    quiet=true
))]
pub fn sdat_img2sdat(
    py: Python<'_>,
    input_image: PathBuf,
    outdir: PathBuf,
    version: u8,
    prefix: Option<String>,
    input_format: Option<String>,
    file_map: Option<PathBuf>,
    clobbered_blocks: Option<String>,
    src_image: Option<PathBuf>,
    threads: Option<usize>,
    quiet: bool,
) -> PyResult<String> {
    let prefix_out = py
        .detach(move || -> Result<String, String> {
            let img_abs = abs_path(&input_image)?;
            let out_abs = abs_path(&outdir)?;

            fs::create_dir_all(&out_abs)
                .map_err(|e| format!("cannot create output directory: {e}"))?;

            let pfx = prefix.unwrap_or_else(|| {
                input_image
                    .file_stem()
                    .map(|s| s.to_string_lossy().into_owned())
                    .unwrap_or_else(|| "system".to_string())
            });

            let fmt = match input_format.as_deref() {
                Some("sparse") => sdatimg::InputFormat::Sparse,
                Some("raw") => sdatimg::InputFormat::Raw,
                _ => sdatimg::InputFormat::Auto,
            };

            let f_map = match file_map {
                Some(p) => Some(abs_path(&p)?),
                None => None,
            };
            let s_img = match src_image {
                Some(p) => Some(abs_path(&p)?),
                None => None,
            };

            let opts = sdatimg::Img2SdatOptions {
                outdir: out_abs,
                version,
                prefix: pfx,
                input_format: fmt,
                file_map: f_map,
                clobbered_blocks,
                src_image: s_img,
                src_file_map: None,
                cache_size: None,
                stash_threshold: 0.8,
                threads,
                disable_imgdiff: false,
                quiet,
            };

            let out_prefix = sdatimg::img2sdat(&img_abs, &opts).map_err(|e| e.to_string())?;
            Ok(out_prefix.to_string_lossy().into_owned())
        })
        .map_err(PyOSError::new_err)?;
    Ok(prefix_out)
}
