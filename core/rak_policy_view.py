# SPDX-License-Identifier: MPL-2.0
import json
import os
import shutil
import tempfile

from core.app_paths import neocat_cache_dir
from core.neocat_selinux_engine import DeviceSelinuxPatchEngine
from core.project_view_work import _branch_signature, _copy_view
from core.rak_neobranch_view import ActiveProjectView, active_branch_for_path, project_root
from main_ext.class_NeoBranchManager import _metadata_lock


_POLICY_PARTITIONS = {
    "system", "system_ext", "product", "vendor", "odm", "vendor_dlkm", "odm_dlkm",
}


def _partition_name(fs_root):
    name = fs_root[:-2] if fs_root.endswith(("_a", "_b")) else fs_root
    return name if name in _POLICY_PARTITIONS else ""


def _write_report(plan, destination):
    with open(destination, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(plan.to_dict(), handle, indent=2, sort_keys=True)
        handle.write("\n")


def run_policy_in_view(project_path, fs_root, *, apply=False, json_path=""):

    try:
        root = project_root(os.path.abspath(project_path))
    except ValueError:
        root = os.path.abspath(project_path)
    if not fs_root or os.path.basename(fs_root) != fs_root or fs_root in (".", ".."):
        return False, f"Nama partisi policy tidak valid: {fs_root}"

    view = ActiveProjectView(root)
    branches = view.active_branches()
    if branches and not _partition_name(fs_root):
        return False, f"Partisi policy NeoBranch belum didukung: {fs_root}"
    unsupported = sorted(fs for fs in branches if not _partition_name(fs))
    if unsupported:
        return False, ("Policy belum dapat memakai branch pada partisi: "
                       + ", ".join(unsupported))
    if apply and branches and set(branches) != {fs_root}:
        return False, ("Untuk --apply, hanya branch partisi target yang boleh aktif; "
                       "cache NeoCat tersimpan per branch target")

    output = ""
    if json_path:
        output = os.path.abspath(json_path if os.path.isabs(json_path)
                                 else os.path.join(project_path, json_path))
        branch = active_branch_for_path(output)
        if branch:
            return False, (f"Report JSON berada dalam NeoBranch aktif '{branch}'; "
                           "simpan di luar folder partisi")

    if not branches:
        planner = DeviceSelinuxPatchEngine(root)
        plan = planner.plan(fs_root)
        if output:
            _write_report(plan, output)
        if not plan.ready:
            return False, plan.summary()
        saved = planner.commit(plan) if apply else 0
    else:
        manager = view.manager
        signatures = {fs: _branch_signature(manager, fs, branch)
                      for fs, branch in branches.items()}
        with tempfile.TemporaryDirectory(prefix="rak-policy-") as scratch:
            staged = os.path.join(scratch, "project")
            staged_cfg = os.path.join(staged, "config")
            os.makedirs(staged_cfg)

            source_cfg = os.path.join(root, "config")
            if os.path.isdir(source_cfg):
                for name in os.listdir(source_cfg):
                    source = os.path.join(source_cfg, name)
                    if os.path.isfile(source):
                        shutil.copy2(source, os.path.join(staged_cfg, name))
            for fs, branch in branches.items():
                branch_cfg = manager._branch_config_dir(fs, branch)
                if os.path.isdir(branch_cfg):
                    for name in os.listdir(branch_cfg):
                        source = os.path.join(branch_cfg, name)
                        if name.startswith(fs + "_") and os.path.isfile(source):
                            shutil.copy2(source, os.path.join(staged_cfg, name))

            overrides = sorted(set(branches) | {fs_root})
            for fs in overrides:
                logical = os.path.join(root, fs)
                physical, _ = view.resolve(logical)
                if not physical or not os.path.isdir(physical):
                    return False, f"Folder partisi tidak ditemukan di view aktif: {fs}"
                _copy_view(view, logical, os.path.join(staged, fs))

            target_branch = branches.get(fs_root)
            cache_dir = neocat_cache_dir(root, fs_root, target_branch,
                                         migrate_legacy=False) if target_branch else \
                        neocat_cache_dir(root, migrate_legacy=False)
            planner = DeviceSelinuxPatchEngine(
                root, target_base=staged, cache_dir=cache_dir,
                partition_overrides=overrides,
            )
            plan = planner.plan(fs_root)
            plan.target_base = root
            plan.active_branches = dict(branches)
            with _metadata_lock:
                if ActiveProjectView(root).active_branches() != branches or any(
                    _branch_signature(manager, fs, branch) != signatures[fs]
                    for fs, branch in branches.items()
                ):
                    return False, "NeoBranch berubah selama policy plan; jalankan ulang"
                if output:
                    _write_report(plan, output)
                if not plan.ready:
                    return False, plan.summary()
                saved = planner.commit(plan) if apply else 0

    message = plan.summary()
    if apply:
        destination = (f"branch '{branches[fs_root]}'" if fs_root in branches else "source")
        message += f"; committed {saved} verified context(s) to {destination} cache"
    elif output:
        message += f"; report written to {output}"
    return True, message
