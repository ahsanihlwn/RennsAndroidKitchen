# SPDX-License-Identifier: MPL-2.0
"""Clean-room one-click modding wizards: fstab AVB/encryption, vbmeta, SELinux audit2allow, Magisk boot."""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from core.command_context import OperationError
from core.native_backend import (
    inspect_vbmeta as _native_inspect_vbmeta,
    patch_fstab as _native_patch_fstab,
    patch_props as _native_patch_props,
    patch_vbmeta as _native_patch_vbmeta,
    selinux_audit_allow as _native_selinux_audit_allow,
)

_SKIP_DIRS = frozenset({".git", "neobranch", ".rak_quarantine", "automation", "neocat_config"})


def discover_fstabs(base_dir: str) -> list[dict]:
    if not base_dir or not os.path.isdir(base_dir):
        return []
    found = []
    base = Path(base_dir).resolve()
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(
            d for d in dirs
            if d not in _SKIP_DIRS and not d.startswith(".")
            and not os.path.islink(os.path.join(root, d))
        )
        for fname in sorted(files):
            lower = fname.lower()
            if lower == "fstab" or lower.startswith("fstab."):
                full = Path(root) / fname
                if full.is_symlink():
                    continue
                try:
                    rel = full.relative_to(base).as_posix()
                except ValueError:
                    continue
                found.append({"path": str(full), "rel_path": rel, "name": fname})
    return found


def discover_vbmetas(base_dir: str) -> list[dict]:
    if not base_dir or not os.path.isdir(base_dir):
        return []
    found = []
    base = Path(base_dir).resolve()
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(
            d for d in dirs
            if d not in _SKIP_DIRS and not d.startswith(".")
            and not os.path.islink(os.path.join(root, d))
        )
        for fname in sorted(files):
            lower = fname.lower()
            if lower.startswith("vbmeta") and lower.endswith(".img"):
                full = Path(root) / fname
                try:
                    info = _native_inspect_vbmeta(str(full))
                except Exception:
                    continue
                if info.get("valid"):
                    info["path"] = str(full)
                    info["rel_path"] = full.relative_to(base).as_posix()
                    info["name"] = fname
                    found.append(info)
    return found


def _read_text_preserve_encoding(path: str) -> tuple[str, str]:
    raw = Path(path).read_bytes()
    try:
        return raw.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("latin-1"), "latin-1"


def _write_text_atomic(path: str, text: str, encoding: str) -> None:
    target = Path(path)
    fd, tmp_name = tempfile.mkstemp(prefix=".rak-mod-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as stream:
            stream.write(text)
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def preview_fstab_patch(path: str, *, disable_avb: bool = True, disable_encryption: bool = True) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    text, _enc = _read_text_preserve_encoding(path)
    res = _native_patch_fstab(text, disable_avb=disable_avb, disable_encryption=disable_encryption)
    res["path"] = path
    return res


def apply_fstab_patch(path: str, *, disable_avb: bool = True, disable_encryption: bool = True) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    text, enc = _read_text_preserve_encoding(path)
    res = _native_patch_fstab(text, disable_avb=disable_avb, disable_encryption=disable_encryption)
    if res.get("changed"):
        _write_text_atomic(path, res["modified_text"], enc)
    res["path"] = path
    return res


def preview_prop_patch(path: str, updates: list[tuple[str, str]], removals: list[str] = ()) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    text, _enc = _read_text_preserve_encoding(path)
    res = _native_patch_props(text, updates, removals)
    res["path"] = path
    return res


def apply_prop_patch(path: str, updates: list[tuple[str, str]], removals: list[str] = ()) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    text, enc = _read_text_preserve_encoding(path)
    res = _native_patch_props(text, updates, removals)
    if res.get("changed"):
        _write_text_atomic(path, res["modified_text"], enc)
    res["path"] = path
    return res


def inspect_vbmeta(path: str) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    return _native_inspect_vbmeta(path)


def apply_vbmeta_patch(
    path: str,
    *,
    disable_hashtree: bool = True,
    disable_verification: bool = True,
) -> dict:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    return _native_patch_vbmeta(
        path,
        disable_hashtree=disable_hashtree,
        disable_verification=disable_verification,
    )


def find_selinux_dir(folder: str) -> str:
    """Return `<folder>/etc/selinux` or `<folder>/system/etc/selinux` if present."""
    if not folder or not os.path.isdir(folder):
        return ""
    for rel in (("etc", "selinux"), ("system", "etc", "selinux")):
        candidate = os.path.join(folder, *rel)
        if os.path.isdir(candidate):
            return candidate
    return ""


def append_cil_rules(cil_path: str, cil_rules_text: str) -> dict:
    """Atomically append deduplicated CIL rules into a target .cil file."""
    lines = [ln.strip() for ln in (cil_rules_text or "").splitlines() if ln.strip()]
    if not lines:
        return {"added": 0, "skipped": 0, "path": cil_path}
    target = Path(cil_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file():
        existing_text, enc = _read_text_preserve_encoding(str(target))
    else:
        existing_text, enc = "", "utf-8"
    existing_set = {ln.strip() for ln in existing_text.splitlines() if ln.strip()}
    to_add = [ln for ln in lines if ln not in existing_set]
    if to_add:
        sep = "\n" if (existing_text and not existing_text.endswith("\n")) else ""
        updated = f"{existing_text}{sep}" + "\n".join(to_add) + "\n"
        _write_text_atomic(str(target), updated, enc)
    return {"added": len(to_add), "skipped": len(lines) - len(to_add), "path": str(target)}


def generate_selinux_allow(log_text: str, *, include_untrusted: bool = False) -> dict:
    return _native_selinux_audit_allow(log_text, include_untrusted=include_untrusted)


def _extract_magisk_payload(magisk_apk: str, arch: str, work: Path) -> dict[str, Path]:
    abi_map = {
        "arm64-v8a": ("arm64-v8a", "armeabi-v7a"),
        "armeabi-v7a": ("armeabi-v7a",),
        "x86_64": ("x86_64", "x86"),
        "x86": ("x86",),
    }
    abis = abi_map.get(arch, (arch,))
    out: dict[str, Path] = {}
    with zipfile.ZipFile(magisk_apk, "r") as zf:
        names = set(zf.namelist())
        for abi in abis:
            for key, so_name in (("magiskinit", "libmagiskinit.so"), ("magisk32", "libmagisk32.so"), ("magisk64", "libmagisk64.so")):
                if key in out:
                    continue
                cand = f"lib/{abi}/{so_name}"
                if cand in names:
                    dst = work / f"{key}.bin"
                    dst.write_bytes(zf.read(cand))
                    out[key] = dst
        if "assets/stub.apk" in names:
            dst = work / "stub.apk"
            dst.write_bytes(zf.read("assets/stub.apk"))
            out["stub"] = dst
    if "magiskinit" not in out:
        raise OperationError(f"libmagiskinit.so for {arch} not found inside {os.path.basename(magisk_apk)}")
    return out


def patch_boot_with_magisk(
    boot_img: str,
    magisk_apk: str,
    *,
    arch: str = "arm64-v8a",
    keep_verity: bool = False,
    keep_forceencrypt: bool = False,
    out_img: str | None = None,
) -> dict:
    if not os.path.isfile(boot_img):
        raise FileNotFoundError(boot_img)
    if not os.path.isfile(magisk_apk):
        raise FileNotFoundError(magisk_apk)

    from core import native_backend as _nb

    def _boot_unpack(image: Path, work: Path) -> None:
        _nb.boot_unpack(str(image), str(work), skip_decomp=False, dump_hdr=True)

    def _boot_cpio(cpio_file: Path, cmds: list[str], check: bool = True) -> int:
        result = _nb.boot_cpio(str(cpio_file), cmds)
        code = result.get("exit_code", 1)
        if check and not result.get("ok", False):
            raise OperationError(f"magiskboot cpio failed (exit {code})")
        return code

    def _boot_compress(fmt: str, src: Path, dst: Path) -> None:
        _nb.boot_compress(str(src), str(dst), fmt)

    with tempfile.TemporaryDirectory(prefix="rak_magisk_") as td:
        work = Path(td)
        local_boot = work / "boot.img"
        shutil.copy2(boot_img, local_boot)
        bins = _extract_magisk_payload(magisk_apk, arch, work)

        _boot_unpack(local_boot, work)
        ramdisk = work / "ramdisk.cpio"
        has_ramdisk = ramdisk.is_file()
        if has_ramdisk:
            status_code = _boot_cpio(ramdisk, ["test"], check=False)
            if status_code == 1:
                raise OperationError("Boot image is already patched by an unsupported custom ramdisk.")
            if status_code == 2:
                _boot_cpio(ramdisk, ["restore"])
            # SHA1 of the original boot image.
            import hashlib
            sha1 = hashlib.sha1(local_boot.read_bytes()).hexdigest()
            cfg_file = work / "config"
            cfg_file.write_text(
                f"KEEPVERITY={'true' if keep_verity else 'false'}\n"
                f"KEEPFORCEENCRYPT={'true' if keep_forceencrypt else 'false'}\n"
                f"SHA1={sha1}\n",
                encoding="utf-8",
            )
            cpio_cmds = [
                "mkdir 000 .backup",
                f"add 0750 init {bins['magiskinit'].as_posix()}",
                f"add 000 .backup/.magisk {cfg_file.as_posix()}",
            ]
            if "magisk32" in bins:
                _boot_compress("xz", bins["magisk32"], work / "magisk32.xz")
                cpio_cmds.append(f"add 0644 .backup/magisk32.xz {(work / 'magisk32.xz').as_posix()}")
            if "magisk64" in bins:
                _boot_compress("xz", bins["magisk64"], work / "magisk64.xz")
                cpio_cmds.append(f"add 0644 .backup/magisk64.xz {(work / 'magisk64.xz').as_posix()}")
            if "stub" in bins:
                _boot_compress("xz", bins["stub"], work / "stub.xz")
                cpio_cmds.append(f"add 0644 .backup/stub.xz {(work / 'stub.xz').as_posix()}")
            _boot_cpio(ramdisk, cpio_cmds)

        for dtb_name in ("dtb", "kernel_dtb", "extra"):
            dtb_file = work / dtb_name
            if dtb_file.is_file():
                try:
                    _nb.boot_dtb_patch(str(dtb_file))
                except Exception:
                    pass

        new_boot = work / "new-boot.img"
        result = _nb.boot_repack(str(work), str(local_boot), str(new_boot))
        if not new_boot.is_file():
            raise OperationError("magiskboot repack did not produce new-boot.img")

        if out_img is None:
            p = Path(boot_img)
            out_img = str(p.with_name(f"{p.stem}_magisk{p.suffix}"))
        shutil.copy2(new_boot, out_img)
        return {"ok": True, "output": out_img, "arch": arch, "ramdisk_patched": has_ramdisk}
