# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile

from core.binary_handler import resolve_binary, is_available
from core.app_paths import BIN_DIR
from core.proc_util import CREATE_NO_WINDOW

BIN_PATH = BIN_DIR

JAVA_NAME = "java.exe"
APKTOOL_JAR = "apktool.jar"
APKSIGNER_JAR = "apksigner.jar"
TESTKEY_PK8 = "testkey.pk8"
TESTKEY_CERT = "testkey.x509.pem"

APKTOOL_DEC_DIR = "apktool_dec"
APKTOOL_MARKER = "apktool.yml"

_FRAMEWORK_CACHE_MARKER = ".apktool_framework_cache.json"

_SIGNATURE_EXTS = (".rsa", ".dsa", ".ec", ".sf")


class ToolError(RuntimeError):
    pass


def _find(name: str) -> str:
    # Single source of truth: the kitchen's managed bin (package.json). No
    # PATH / hardcoded dev fallback — same rule as core/magiskboot_tree.py.
    resolved = resolve_binary(BIN_PATH, name)
    if resolved and os.path.isfile(resolved):
        return resolved
    raise SystemExit(
        f"{name} not installed. Install it via the kitchen's bin manager "
        f"(expected under {BIN_PATH})."
    )


def resolve_java() -> str | None:
    managed = resolve_binary(BIN_PATH, JAVA_NAME)
    if managed and os.path.isfile(managed):
        return managed
    system = shutil.which(JAVA_NAME)
    return system if system and os.path.isfile(system) else None


def find_java() -> str:
    java = resolve_java()
    if java:
        return java
    raise SystemExit(
        "Java runtime not found. Install Java through the bin manager or add it to PATH."
    )


def find_apktool_jar() -> str:
    return _find(APKTOOL_JAR)


def find_apksigner_jar() -> str:
    return _find(APKSIGNER_JAR)


def find_testkey() -> tuple[str, str]:
    return _find(TESTKEY_PK8), _find(TESTKEY_CERT)


def is_apktool_available() -> bool:
    return bool(resolve_java()) and is_available(APKTOOL_JAR)


def is_signer_available() -> bool:
    return bool(resolve_java()) and is_available(APKSIGNER_JAR, TESTKEY_PK8, TESTKEY_CERT)


def _run_jar(jar_path: str, args: list) -> str:
    try:
        java = find_java()
    except SystemExit as exc:
        # Centralize here so every caller only ever needs to catch ToolError.
        raise ToolError(str(exc)) from exc
    cmd = [java, "-jar", jar_path, *args]
    proc = subprocess.run(
        cmd, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=CREATE_NO_WINDOW,
    )
    out = proc.stdout or ""
    if proc.returncode != 0:
        raise ToolError(out.strip() or f"command failed with exit {proc.returncode}: {' '.join(cmd)}")
    return out


# path helpers

def mirror_dir_for(base: str, apk_abs_path: str) -> str:

    rel = os.path.relpath(os.path.abspath(apk_abs_path), os.path.abspath(base))
    return os.path.join(base, APKTOOL_DEC_DIR, rel)


def is_decompiled(base: str, apk_abs_path: str) -> bool:
    mirror = mirror_dir_for(base, apk_abs_path)
    return os.path.isfile(os.path.join(mirror, APKTOOL_MARKER))


def move_apk_with_mirror(src_base: str, dst_base: str, src_apk: str,
                         dst_apk: str, move_apk) -> tuple[bool, str]:

    if not is_decompiled(src_base, src_apk):
        return move_apk()

    src_mirror = mirror_dir_for(src_base, src_apk)
    dst_mirror = mirror_dir_for(dst_base, dst_apk)
    if os.path.exists(dst_apk):
        return False, f"APK destination already exists: {dst_apk}"
    if os.path.exists(dst_mirror):
        return False, f"Decompiled destination already exists: {dst_mirror}"

    try:
        os.makedirs(os.path.dirname(dst_mirror), exist_ok=True)
        shutil.move(src_mirror, dst_mirror)
    except Exception as exc:
        return False, str(exc)

    try:
        ok, msg = move_apk()
    except Exception as exc:
        ok, msg = False, str(exc)
    if not ok:
        try:
            # A worker can fail after its physical move (for example while
            # loading config). Restore that APK before restoring its mirror.
            if not os.path.exists(src_apk) and os.path.exists(dst_apk):
                os.makedirs(os.path.dirname(src_apk), exist_ok=True)
                shutil.move(dst_apk, src_apk)
            os.makedirs(os.path.dirname(src_mirror), exist_ok=True)
            shutil.move(dst_mirror, src_mirror)
        except Exception as rollback_error:
            return False, f"{msg}; APK restore failed: {rollback_error}"
    return ok, msg


def find_rom_framework_res_candidates(base: str | None) -> list[str]:

    if not base:
        return []
    roots = ("system", "system_ext", "product", "vendor", "odm")
    found = (os.path.join(base, r, "framework", "framework-res.apk") for r in roots)
    return [p for p in found if os.path.isfile(p)]


def ensure_rom_framework_installed(base: str | None) -> tuple[bool, str]:

    candidates = find_rom_framework_res_candidates(base)
    if not candidates:
        return True, "no ROM framework-res.apk to install"

    marker = os.path.join(base, APKTOOL_DEC_DIR, _FRAMEWORK_CACHE_MARKER)
    try:
        with open(marker, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}

    try:
        jar = find_apktool_jar()
    except SystemExit as exc:
        return False, str(exc)

    installed_any = False
    for fw in candidates:
        stat = os.stat(fw)
        fingerprint = f"{stat.st_size}:{int(stat.st_mtime)}"
        if cache.get(fw) == fingerprint:
            continue
        try:
            _run_jar(jar, ["if", fw])
        except ToolError as exc:
            return False, f"{fw}: {exc}"
        cache[fw] = fingerprint
        installed_any = True

    if installed_any:
        try:
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            with open(marker, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=2)
        except OSError:
            pass  # cache write failing just means we re-install next time

    return True, f"{len(candidates)} framework(s) ready"


# operations — each returns (ok: bool, msg: str)

def decompile_apk(apk_path: str, out_dir: str, base: str | None = None) -> tuple[bool, str]:
    apk_path = os.path.abspath(apk_path)
    if not os.path.isfile(apk_path):
        return False, f"APK not found: {apk_path}"

    ok, msg = ensure_rom_framework_installed(base)
    if not ok:
        return False, f"framework install failed: {msg}"

    try:
        jar = find_apktool_jar()
    except SystemExit as exc:
        return False, str(exc)

    os.makedirs(os.path.dirname(out_dir), exist_ok=True)
    try:
        _run_jar(jar, ["d", apk_path, "-o", out_dir, "-f"])
    except ToolError as exc:
        return False, str(exc)

    if not os.path.isfile(os.path.join(out_dir, APKTOOL_MARKER)):
        return False, "apktool decompile finished without producing apktool.yml"
    return True, out_dir


def build_apk(src_dir: str, out_apk_path: str, base: str | None = None, copy_original: bool = False) -> tuple[bool, str]:

    if not os.path.isdir(src_dir):
        return False, f"Decompiled source not found: {src_dir}"

    ok, msg = ensure_rom_framework_installed(base)
    if not ok:
        return False, f"framework install failed: {msg}"

    try:
        jar = find_apktool_jar()
    except SystemExit as exc:
        return False, str(exc)

    temp_out = out_apk_path + ".new"
    if os.path.exists(temp_out):
        os.remove(temp_out)
    args = ["b", src_dir, "-o", temp_out]
    if copy_original:
        args.append("--copy-original")
    try:
        _run_jar(jar, args)
    except ToolError as exc:
        return False, str(exc)

    if not os.path.isfile(temp_out):
        return False, "apktool build produced no output"

    os.replace(temp_out, out_apk_path)
    return True, out_apk_path


def sign_apk(apk_path: str) -> tuple[bool, str]:
    if not os.path.isfile(apk_path):
        return False, f"APK not found: {apk_path}"

    try:
        jar = find_apksigner_jar()
        key, cert = find_testkey()
    except SystemExit as exc:
        return False, str(exc)

    temp_out = apk_path + ".new"
    if os.path.exists(temp_out):
        os.remove(temp_out)
    try:
        _run_jar(jar, [
            "sign", "--key", key, "--cert", cert,
            "--out", temp_out, apk_path,
        ])
    except ToolError as exc:
        return False, str(exc)

    if not os.path.isfile(temp_out):
        return False, "apksigner produced no output"

    os.replace(temp_out, apk_path)
    return True, apk_path


def unsign_apk(apk_path: str) -> tuple[bool, str]:

    if not os.path.isfile(apk_path):
        return False, f"APK not found: {apk_path}"

    temp_out = apk_path + ".new"
    if os.path.exists(temp_out):
        os.remove(temp_out)

    removed = 0
    try:
        with zipfile.ZipFile(apk_path, "r") as src, \
             zipfile.ZipFile(temp_out, "w", zipfile.ZIP_DEFLATED) as dst:
            for item in src.infolist():
                upper = item.filename.upper()
                is_sig = upper.startswith("META-INF/") and (
                    upper == "META-INF/MANIFEST.MF"
                    or os.path.splitext(upper)[1].lower() in _SIGNATURE_EXTS
                )
                if is_sig:
                    removed += 1
                    continue
                dst.writestr(item, src.read(item.filename))
    except Exception as exc:
        if os.path.exists(temp_out):
            try:
                os.remove(temp_out)
            except OSError:
                pass
        return False, str(exc)

    if removed == 0:
        os.remove(temp_out)
        return False, "APK has no signature to remove"

    os.replace(temp_out, apk_path)
    return True, apk_path
