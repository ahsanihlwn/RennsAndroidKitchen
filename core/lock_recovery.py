# SPDX-License-Identifier: MPL-2.0
import os
import subprocess
print("[CORE STAGE] LOCK RECOVERY")


from core.proc_util import CREATE_NO_WINDOW, kill_process_tree
from core.app_paths import BIN_DIR

_DEFAULT_BIN_ROOT = BIN_DIR


def is_windows_lock_error(exc: BaseException) -> bool:

    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError):
        winerror = getattr(exc, "winerror", None)
        if winerror in (5, 32, 33):
            return True
    return False


def _managed_exe_names(bin_root=None):

    import json

    root = bin_root or _DEFAULT_BIN_ROOT
    package_json = os.path.join(root, "package.json")
    names = set()
    if not os.path.isfile(package_json):
        return names

    try:
        with open(package_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return names

    for env_data in data.values():
        for b in env_data.get("binaries", []):
            name = b.get("name", "")
            if name.lower().endswith(".exe"):
                names.add(os.path.splitext(name)[0].lower())

    return names


def force_release_managed_tools(bin_root=None, *, only_children=False):

    names = _managed_exe_names(bin_root)
    if not names:
        return []

    killed = []
    try:
        import psutil  # noqa: F401 — opsional, coba pola lebih presisi dulu
        HAS_PSUTIL = True
    except Exception:
        HAS_PSUTIL = False

    if HAS_PSUTIL:
        import psutil
        processes = (psutil.Process(os.getpid()).children(recursive=True)
                     if only_children else psutil.process_iter(["pid", "name"]))
        for proc in processes:
            try:
                pname = proc.name() if only_children else (proc.info.get("name") or "")
                stem = os.path.splitext(pname)[0].lower()
                if stem in names:
                    kill_process_tree(proc.pid)
                    killed.append(pname)
            except Exception:
                continue
        return killed

    if only_children:
        return []

    # Fallback tanpa psutil: taskkill by image name langsung, satu per satu.
    for stem in names:
        exe_name = f"{stem}.exe"
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/T", "/IM", exe_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=CREATE_NO_WINDOW,
            )
            if result.returncode == 0:
                killed.append(exe_name)
        except Exception:
            continue

    return killed
