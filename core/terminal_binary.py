# SPDX-License-Identifier: MPL-2.0
import os
import json

def resolve_terminal_binary(bin_root: str, command_name: str):


    if not bin_root:
        return None

    pkg = os.path.join(bin_root, "package.json")
    if not os.path.isfile(pkg):
        return None

    try:
        with open(pkg, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    cmd = command_name.lower()

    for folder, meta in data.items():
        for b in meta.get("binaries", []):
            name = b.get("name", "")
            base = os.path.splitext(name)[0].lower()

            if base == cmd:
                real = os.path.join(bin_root, folder, name)
                if os.path.isfile(real):
                    return real

    return None


def get_terminal_binary_directories(bin_root: str) -> list[str]:

    if not bin_root or not os.path.isdir(bin_root):
        return []

    dirs = [bin_root]
    pkg = os.path.join(bin_root, "package.json")
    if os.path.isfile(pkg):
        try:
            with open(pkg, "r", encoding="utf-8") as f:
                data = json.load(f)
            for folder in data.keys():
                sub = os.path.join(bin_root, folder)
                if os.path.isdir(sub) and sub not in dirs:
                    dirs.append(sub)
        except Exception:
            pass

    return dirs


def get_external_cli_directories() -> list[str]:

    local_appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    agy_dir = os.path.join(local_appdata, "agy", "bin")
    agy_exe = os.path.join(agy_dir, "agy.exe")
    return [agy_dir] if os.path.isfile(agy_exe) else []


def get_windows_path_directories() -> list[str]:

    if os.name != "nt":
        return []

    try:
        import winreg
    except ImportError:
        return []

    registry_paths = (
        (winreg.HKEY_LOCAL_MACHINE,
         r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    )
    directories = []
    seen = set()
    for hive, key_path in registry_paths:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _ = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue

        if not isinstance(value, str):
            continue
        for directory in os.path.expandvars(value).split(os.pathsep):
            directory = directory.strip().strip('"')
            if not directory:
                continue
            identity = os.path.normcase(os.path.normpath(directory))
            if identity not in seen:
                seen.add(identity)
                directories.append(directory)
    return directories
