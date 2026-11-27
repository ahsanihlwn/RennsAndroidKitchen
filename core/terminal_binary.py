# SPDX-License-Identifier: MPL-2.0
import os
import json

def get_native_cli_directory(bin_root: str) -> str:
    if not bin_root:
        return ""
    return os.path.join(bin_root, "native_cli")


def resolve_terminal_binary(bin_root: str, command_name: str):
    if not bin_root:
        return None

    from core.native_tools import NATIVE_BUILTIN_PACKAGES, NATIVE_PLATFORM_TOOLS, is_native_tool

    cmd = command_name.lower()
    cmd_stem = os.path.splitext(cmd)[0]
    native_dir = get_native_cli_directory(bin_root)
    if cmd_stem in NATIVE_PLATFORM_TOOLS and native_dir and os.path.isdir(native_dir):
        for ext in (".cmd", ".bat"):
            candidate = os.path.join(native_dir, f"{cmd_stem}{ext}")
            if os.path.isfile(candidate):
                return candidate

    # Never fall back to legacy .exe files for tools backed natively by rak_native.
    if is_native_tool(cmd_stem):
        return None

    pkg = os.path.join(bin_root, "package.json")
    if not os.path.isfile(pkg):
        return None

    try:
        with open(pkg, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    for folder, meta in data.items():
        if folder in NATIVE_BUILTIN_PACKAGES or folder == "erofs_env":
            continue
        for b in meta.get("binaries", []):
            name = b.get("name", "")
            if is_native_tool(name):
                continue
            base = os.path.splitext(name)[0].lower()

            if base == cmd_stem:
                real = os.path.join(bin_root, folder, name)
                if os.path.isfile(real):
                    return real

    return None


def get_terminal_binary_directories(bin_root: str) -> list[str]:
    if not bin_root:
        return []

    from core.native_tools import NATIVE_BUILTIN_PACKAGES

    native_dir = get_native_cli_directory(bin_root)
    if not os.path.isdir(bin_root) and not os.path.isdir(native_dir):
        return []

    dirs = []
    if native_dir and os.path.isdir(native_dir):
        dirs.append(native_dir)
    if os.path.isdir(bin_root) and bin_root not in dirs:
        dirs.append(bin_root)

    pkg = os.path.join(bin_root, "package.json")
    if os.path.isfile(pkg):
        try:
            with open(pkg, "r", encoding="utf-8") as f:
                data = json.load(f)
            for folder in data.keys():
                if folder in NATIVE_BUILTIN_PACKAGES or folder == "erofs_env":
                    continue
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
