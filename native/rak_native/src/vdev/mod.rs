// SPDX-License-Identifier: MPL-2.0
//! Native Rust acceleration engine for `core.vdev` full-firmware analyzer & simulator.

pub mod boot_avb;
pub mod container;
pub mod dex_verify;
pub mod elf_pb;
pub mod selinux_cil;

use pyo3::prelude::*;

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(container::vdev_probe_super_json, module)?)?;
    module.add_function(wrap_pyfunction!(container::vdev_super_open_erofs, module)?)?;
    module.add_function(wrap_pyfunction!(container::vdev_super_open_ext, module)?)?;
    module.add_function(wrap_pyfunction!(container::vdev_probe_zip_json, module)?)?;
    module.add_function(wrap_pyfunction!(container::vdev_read_zip_entry_bytes, module)?)?;
    module.add_function(wrap_pyfunction!(
        container::vdev_prepare_zip_images_json,
        module
    )?)?;

    module.add_function(wrap_pyfunction!(boot_avb::vdev_inspect_boot_json, module)?)?;
    module.add_function(wrap_pyfunction!(
        boot_avb::vdev_inspect_boot_bytes_json,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        boot_avb::vdev_inspect_zip_boot_json,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(boot_avb::vdev_inspect_vbmeta_json, module)?)?;
    module.add_function(wrap_pyfunction!(
        boot_avb::vdev_inspect_vbmeta_bytes_json,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        boot_avb::vdev_inspect_zip_vbmeta_json,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        boot_avb::vdev_verify_hashtree_json,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        boot_avb::vdev_inspect_dtbo_json,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        boot_avb::vdev_inspect_zip_dtbo_json,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(
        boot_avb::vdev_verify_avb_hash_json,
        module
    )?)?;

    module.add_function(wrap_pyfunction!(elf_pb::vdev_scan_elfs_json, module)?)?;
    module.add_function(wrap_pyfunction!(elf_pb::vdev_scan_erofs_elfs_json, module)?)?;
    module.add_function(wrap_pyfunction!(elf_pb::vdev_scan_ext_elfs_json, module)?)?;
    module.add_function(wrap_pyfunction!(elf_pb::vdev_scan_elf_bytes_json, module)?)?;
    module.add_function(wrap_pyfunction!(elf_pb::vdev_scan_ko_files_json, module)?)?;
    module.add_function(wrap_pyfunction!(elf_pb::vdev_scan_erofs_kos_json, module)?)?;
    module.add_function(wrap_pyfunction!(elf_pb::vdev_scan_ext_kos_json, module)?)?;
    module.add_function(wrap_pyfunction!(elf_pb::vdev_scan_apk_files_json, module)?)?;
    module.add_function(wrap_pyfunction!(elf_pb::vdev_scan_erofs_apks_json, module)?)?;
    module.add_function(wrap_pyfunction!(elf_pb::vdev_scan_ext_apks_json, module)?)?;
    module.add_function(wrap_pyfunction!(elf_pb::vdev_index_erofs_apexes_json, module)?)?;
    module.add_function(wrap_pyfunction!(elf_pb::vdev_index_ext_apexes_json, module)?)?;
    module.add_function(wrap_pyfunction!(elf_pb::vdev_index_file_apexes_json, module)?)?;
    module.add_function(wrap_pyfunction!(
        elf_pb::vdev_parse_classpath_pb_json,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(elf_pb::vdev_walk_erofs_json, module)?)?;
    module.add_function(wrap_pyfunction!(elf_pb::vdev_walk_ext_json, module)?)?;
    module.add_function(wrap_pyfunction!(
        elf_pb::vdev_read_erofs_paths_json,
        module
    )?)?;

    module.add_class::<selinux_cil::NativeSelinuxPolicy>()?;
    module.add_class::<dex_verify::NativeDexVerifier>()?;
    module.add_function(wrap_pyfunction!(
        dex_verify::vdev_parse_arsc_bytes_json,
        module
    )?)?;
    Ok(())
}
