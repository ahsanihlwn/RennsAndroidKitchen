# SPDX-License-Identifier: MPL-2.0
import hashlib
import json
import os
import shutil
import stat
from pathlib import Path

from core.binary_handler import resolve_binary
from core.app_paths import BIN_DIR
from core import magiskboot_tree as mbt
from core import cpio_patch


BIN_PATH = BIN_DIR


META_DIR = ".rak_boot"

# Component magiskboot extracts the device tree to, and where we drop the
# editable decompiled source next to ramdisk/.
DTB_COMPONENT = "dtb"
DTS_FILE = "dtb.dts"        # single-FDT case: <name>/dtb.dts
DTS_DIR = "dtb"             # multi-FDT case:  <name>/dtb/0.dts, 1.dts, ...
DTB_META = "dtb_meta.json"  # lives in .rak_boot; records layout + source hashes


HEADER_FILE = "header"

# Untouched copy of the decoded ramdisk cpio, taken at unpack. Repack always
# patches a fresh copy of this so repeated repacks stay deterministic.
PRISTINE_DEC = "ramdisk.dec.pristine"


def resolve_magiskboot():
    # Single source of truth: the kitchen's managed bin (package.json). Mirrors
    # core/magiskboot_tree.find_magiskboot; no PATH / hardcoded dev fallbacks.
    return resolve_binary(BIN_PATH, "magiskboot.exe")


def _boot_kind_from_magic(img_path):
    try:
        from core.header_magic import check_magic
        magic = check_magic(img_path)
    except Exception:
        magic = ""

    if magic == "VNDRBOOT":
        return "vndrboot"
    return "boot"


def _write_bootconf(base_dir, name, kind):
    config_dir = os.path.join(base_dir, "config")
    os.makedirs(config_dir, exist_ok=True)
    conf_path = os.path.join(config_dir, f"{name}_bootconf")
    with open(conf_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(kind + "\n")
    return conf_path


def write_boot_fs_config(base_dir: str | Path, name: str, meta_dir: Path) -> Path | None:

    config_dir = Path(base_dir) / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    fs_cfg_path = config_dir / f"{name}_fs_config"

    entries = []
    # Always include top-level container folders
    entries.append(f"{name} 0 0 0755")
    entries.append(f"{name}/ramdisk 0 0 0755")

    seen_paths = {f"{name}", f"{name}/ramdisk"}

    # Source 1: meta_dir/cpio (written by magiskboot cpio extract: <path> <mode> <uid> <gid>)
    cpio_cfg = meta_dir / mbt.CPIO_CONFIG
    if cpio_cfg.is_file():
        for line in cpio_cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.rsplit(" ", 3)
            if len(parts) == 4:
                path, mode, uid, gid = parts
                path = path.strip().replace("\\", "/")
                if path in (".", ""):
                    continue
                path = path.lstrip("/")
                try:
                    mode_str = f"{int(mode, 8):04o}"
                except Exception:
                    mode_str = mode
                full_rel = f"{name}/ramdisk/{path}"
                if full_rel not in seen_paths:
                    seen_paths.add(full_rel)
                    entries.append(f"{full_rel} {uid} {gid} {mode_str}")
    else:
        # Source 2: manifest snapshot
        manifest = cpio_patch.read_manifest(meta_dir)
        if manifest:
            for rec in manifest.get("entries", []):
                path = rec.get("path", "").strip().replace("\\", "/").lstrip("/")
                if not path or path == ".":
                    continue
                mode = rec.get("mode", "644" if rec.get("type") == "file" else "755")
                try:
                    mode_str = f"{int(str(mode), 8):04o}"
                except Exception:
                    mode_str = str(mode)
                full_rel = f"{name}/ramdisk/{path}"
                if full_rel not in seen_paths:
                    seen_paths.add(full_rel)
                    entries.append(f"{full_rel} 0 0 {mode_str}")

    # Also add visible header if present
    header_path = f"{name}/header"
    if header_path not in seen_paths:
        entries.append(f"{header_path} 0 0 0644")

    entries.sort()
    fs_cfg_path.write_text("\n".join(entries) + "\n", encoding="utf-8", newline="\n")
    return fs_cfg_path


def ensure_boot_fs_config(base_dir: str | Path, name: str) -> Path | None:

    config_dir = Path(base_dir) / "config"
    fs_cfg_path = config_dir / f"{name}_fs_config"
    if fs_cfg_path.is_file():
        return fs_cfg_path

    meta_dir = Path(base_dir) / name / META_DIR
    if meta_dir.is_dir():
        return write_boot_fs_config(base_dir, name, meta_dir)
    return None


def load_boot_desired_modes(base_dir: str | Path, name: str) -> dict[str, str]:

    fs_cfg = Path(base_dir) / "config" / f"{name}_fs_config"
    if not fs_cfg.is_file():
        return {}
    desired = {}
    prefix = f"{name}/ramdisk/"
    for line in fs_cfg.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 4:
            path = parts[0]
            mode = parts[3]
            if path.startswith(prefix):
                rel_ramdisk = path[len(prefix):]
                if rel_ramdisk:
                    try:
                        desired[rel_ramdisk] = f"{int(mode, 8):o}"
                    except Exception:
                        desired[rel_ramdisk] = mode
            elif path == f"{name}/ramdisk":
                try:
                    desired["."] = f"{int(mode, 8):o}"
                except Exception:
                    desired["."] = mode
    return desired


def is_boot_ramdisk_rel(base_dir: str | Path, rel: str) -> bool:

    rel = rel.replace("\\", "/").strip("/")
    name, separator, rest = rel.partition("/")
    if not separator or not (rest == "ramdisk" or rest.startswith("ramdisk/")):
        return False
    base = Path(base_dir)
    if (base / "config" / f"{name}_bootconf").is_file() or (
        base / name / META_DIR / mbt.META_NAME
    ).is_file():
        return True
    if (base / "neobranch" / name).is_dir():
        from core.rak_neobranch_view import ActiveProjectView, active_config_file
        view = ActiveProjectView(base)
        if Path(active_config_file(view, name, "_bootconf")).is_file():
            return True
        physical, _ = view.resolve(base / name / META_DIR / mbt.META_NAME)
        return bool(physical and Path(physical).is_file())
    return False


def _unsupported_boot_contexts(base_dir: str | Path, name: str) -> str | None:

    path = Path(base_dir) / "config" / f"{name}_file_contexts"
    if (Path(base_dir) / "neobranch" / name).is_dir():
        from core.rak_neobranch_view import ActiveProjectView, active_config_file
        path = Path(active_config_file(
            ActiveProjectView(base_dir), name, "_file_contexts"
        ))
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return (
                f"{path.name} cannot set SELinux labels in a boot/vendor_boot "
                "ramdisk. CPIO stores mode/UID/GID, not security.selinux. "
                "Inspect the policy actually loaded in this boot mode: it "
                "may be a file_contexts file inside the ramdisk (recovery) "
                "or on a mounted system/vendor partition. For surviving "
                "files, ensure restorecon covers the target path. Remove "
                "this ineffective boot context config before repacking."
            )
    return None


def _rm_readonly(func, path, _exc):

    os.chmod(path, stat.S_IWRITE)
    func(path)


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, onerror=_rm_readonly)


def _move_into(src: Path, dst: Path) -> None:

    if dst.exists():
        _rmtree(dst)
    shutil.move(str(src), str(dst))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _decompile_dtb(meta_dir: Path, out_dir: Path) -> dict | None:

    dtb_path = meta_dir / DTB_COMPONENT
    if not dtb_path.is_file() or dtb_path.stat().st_size == 0:
        return None
    try:
        from core import devicetree
        from core.devicetree import dt_image
    except Exception:
        return None

    try:
        blob = dtb_path.read_bytes()
        meta = {"component": DTB_COMPONENT, "entries": []}

        if devicetree.looks_like_container(blob):
            img = dt_image.split(blob)
            meta["kind"] = "container"
            meta["page_size"] = img.page_size
            meta["version"] = img.version
            dts_dir = out_dir / DTS_DIR
            dts_dir.mkdir(parents=True, exist_ok=True)
            for i, e in enumerate(img.entries):
                dts = devicetree.decompile(e.fdt)
                fn = f"{i}.dts"
                (dts_dir / fn).write_text(dts, encoding="utf-8", newline="\n")
                meta["entries"].append({
                    "file": f"{DTS_DIR}/{fn}", "id": e.id, "rev": e.rev,
                    "custom": list(e.custom), "orig_offset": e.orig_offset,
                    "sha": _sha(dts),
                })
        else:
            # single FDT
            dts = devicetree.decompile(blob)
            (out_dir / DTS_FILE).write_text(dts, encoding="utf-8", newline="\n")
            meta["kind"] = "single"
            meta["entries"].append({"file": DTS_FILE, "sha": _sha(dts)})

        (meta_dir / DTB_META).write_text(
            json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return meta
    except Exception:
        # not a parseable device tree (e.g. raw MTK proprietary blob) — skip
        return None


def _recompile_dtb(meta_dir: Path, out_dir: Path) -> bool:

    meta_path = meta_dir / DTB_META
    if not meta_path.is_file():
        return False
    try:
        from core import devicetree
        from core.devicetree import dt_image
    except Exception:
        return False

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return False

    # detect edits
    changed = False
    sources = []
    for ent in meta.get("entries", []):
        dts_path = out_dir / ent["file"]
        if not dts_path.is_file():
            return False  # user deleted it — leave original dtb alone
        text = dts_path.read_text(encoding="utf-8")
        sources.append(text)
        if _sha(text) != ent.get("sha"):
            changed = True
    if not changed:
        return False

    try:
        if meta.get("kind") == "container":
            entries = []
            for ent, text in zip(meta["entries"], sources):
                fdt = devicetree.compile(text)
                entries.append(dt_image.DTEntry(
                    fdt, ent.get("id", 0), ent.get("rev", 0),
                    tuple(ent.get("custom", (0, 0, 0, 0))),
                    ent.get("orig_offset")))
            img = dt_image.DTImage(entries, meta.get("page_size", 2048),
                                   meta.get("version", 0))
            blob = dt_image.build(img)
        else:
            blob = devicetree.compile(sources[0])
        (meta_dir / DTB_COMPONENT).write_bytes(blob)
        # refresh stored hashes so a later repack without new edits is a no-op
        for ent, text in zip(meta["entries"], sources):
            ent["sha"] = _sha(text)
            ent["orig_offset"] = None  # layout changed; rebuild compactly next time
        meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


def unpack_boot_image(img_path):

    img_path = os.path.abspath(os.path.normpath(img_path))
    if not os.path.isfile(img_path):
        return False, f"Image not found: {img_path}"

    try:
        magiskboot = mbt.find_magiskboot(None)
    except SystemExit:
        return False, (
            "magiskboot.exe not installed. Install it via the bin manager "
            f"(expected under {BIN_PATH})."
        )

    base_dir = os.path.dirname(img_path)
    name = os.path.splitext(os.path.basename(img_path))[0]
    kind = _boot_kind_from_magic(img_path)

    out_dir = Path(base_dir) / name
    meta_dir = out_dir / META_DIR
    visible_ramdisk = out_dir / mbt.RAMDISK_DIR

    # Fresh start: clear any previous unpack so re-unpack is clean.
    if meta_dir.exists():
        _rmtree(meta_dir)
    if visible_ramdisk.exists():
        _rmtree(visible_ramdisk)
    meta_dir.mkdir(parents=True, exist_ok=True)
    mbt.set_hidden(meta_dir)

    try:
        # All magiskboot work happens inside the hidden meta dir.
        shutil.copy2(img_path, meta_dir / mbt.SOURCE_IMAGE)
        mbt.run([magiskboot, "unpack", "-h", mbt.SOURCE_IMAGE], meta_dir)
        compression, has_ramdisk = mbt.decode_ramdisk(meta_dir, magiskboot, False)
    except mbt.ToolError as exc:
        return False, str(exc)
    except Exception as exc:
        return False, f"unpack failed: {exc}"


    if has_ramdisk and (meta_dir / mbt.RAMDISK_DIR).is_dir():
        try:
            manifest = cpio_patch.build_manifest(
                meta_dir / mbt.RAMDISK_DIR, meta_dir / mbt.CPIO_CONFIG
            )
            cpio_patch.write_manifest(meta_dir, manifest)


            shutil.copy2(meta_dir / mbt.RAMDISK_DECODED, meta_dir / PRISTINE_DEC)
        except Exception as exc:
            # Manifest is the safe-repack contract; if we can't build it, repack
            # will fall back to the legacy sync path (and warn).
            print(f"warning: ramdisk manifest snapshot failed: {exc}")
        _move_into(meta_dir / mbt.RAMDISK_DIR, visible_ramdisk)

    # Best-effort: decompile the device tree into editable .dts next to
    # ramdisk/. Failure here must not fail the unpack.
    dtb_meta = _decompile_dtb(meta_dir, out_dir)

    # Lift the editable boot header (cmdline/name) out next to ramdisk/.
    has_header = (meta_dir / HEADER_FILE).is_file()
    if has_header:
        shutil.move(str(meta_dir / HEADER_FILE), str(out_dir / HEADER_FILE))

    mbt.write_meta(meta_dir, {
        "tool": "magiskboot_tree.py",
        "source_image": str(img_path),
        "source_copy": mbt.SOURCE_IMAGE,
        "magiskboot": str(magiskboot),
        "has_ramdisk": has_ramdisk,
        "ramdisk_component": mbt.RAMDISK_COMPONENT,
        "ramdisk_decoded": mbt.RAMDISK_DECODED,
        "ramdisk_dir": mbt.RAMDISK_DIR,
        "ramdisk_compression": compression,
        "boot_kind": kind,
        "has_dtb": bool(dtb_meta),
        "dtb_kind": (dtb_meta or {}).get("kind"),
        "dtb_entries": len((dtb_meta or {}).get("entries", [])),
        "has_header": has_header,
    })
    mbt.set_hidden(meta_dir)  # re-assert (writing files can clear it)

    _write_bootconf(base_dir, name, kind)
    write_boot_fs_config(base_dir, name, meta_dir)

    return True, str(out_dir)


def repack_boot_image(folder_path):

    folder_path = os.path.abspath(os.path.normpath(folder_path))
    if not os.path.isdir(folder_path):
        return False, f"Boot folder not found: {folder_path}"

    out_dir = Path(folder_path)
    base_dir = os.path.dirname(folder_path)
    name = os.path.basename(folder_path.rstrip("/\\"))
    out_img = os.path.join(base_dir, f"{name}_new.img")
    staged_img = os.path.join(base_dir, f".{name}_new.partial.img")

    context_error = _unsupported_boot_contexts(base_dir, name)
    if context_error:
        return False, context_error

    meta_dir = out_dir / META_DIR
    source = meta_dir / mbt.SOURCE_IMAGE
    if not source.is_file():
        return False, (
            f"Tree source image not found: {source}. Re-unpack the boot image."
        )

    try:
        meta = mbt.read_meta(meta_dir)
    except SystemExit:
        return False, f"{mbt.META_NAME} missing in {meta_dir}; re-unpack the boot image."

    try:
        magiskboot = mbt.find_magiskboot(None)
    except SystemExit:
        return False, (
            "magiskboot.exe not installed. Install it via the bin manager "
            f"(expected under {BIN_PATH})."
        )

    compression = meta.get("ramdisk_compression", "raw")
    has_ramdisk = meta.get("has_ramdisk", False)

    visible_ramdisk = out_dir / mbt.RAMDISK_DIR
    inner_ramdisk = meta_dir / mbt.RAMDISK_DIR
    visible_header = out_dir / HEADER_FILE
    inner_header = meta_dir / HEADER_FILE

    manifest = cpio_patch.read_manifest(meta_dir)
    pristine = meta_dir / PRISTINE_DEC
    use_patch = bool(manifest) and pristine.is_file()

    moved = False
    header_moved = False

    try:
        # Bring the (possibly edited) header back into .rak_boot so magiskboot
        # repack picks up the cmdline/name from the cwd.
        if visible_header.is_file():
            shutil.copyfile(str(visible_header), str(inner_header))
        elif inner_header.is_file():
            shutil.copyfile(str(inner_header), str(visible_header))

        # Recompile the device tree (if edited) before packing.
        _recompile_dtb(meta_dir, out_dir)

        desired_modes = load_boot_desired_modes(base_dir, name)

        # Handle Vendor Boot Header v4 with multi-ramdisk table
        from core import v4_boot
        is_v4, _v4_info = v4_boot.is_vendor_boot_v4(source)
        if is_v4:
            if not (meta_dir / v4_boot.V4_META_NAME).is_file():
                v4_boot.prepare_v4_meta_from_source(meta_dir, magiskboot)
            if os.path.exists(staged_img):
                os.unlink(staged_img)
            ok, res = v4_boot.repack_vendor_boot_v4(
                meta_dir, visible_ramdisk, staged_img, magiskboot, manifest,
                desired_modes=desired_modes,
            )
            if not ok:
                return False, res
            os.replace(staged_img, out_img)
            return True, str(out_img)

        if has_ramdisk:
            if use_patch:


                warnings = _patch_ramdisk(
                    magiskboot, meta_dir, visible_ramdisk, manifest,
                    compression, desired_modes=desired_modes,
                )
                for w in warnings:
                    print(f"repack: {w}")
            else:
                # Legacy fallback (no manifest — tree unpacked by an older
                # build): re-sync the whole folder. Lossy on case collisions.
                print(
                    "repack: no manifest snapshot; using legacy full-sync "
                    "(case-only-colliding entries may be dropped). Re-unpack "
                    "to enable the safe patch path."
                )
                if visible_ramdisk.is_dir():
                    _move_into(visible_ramdisk, inner_ramdisk)
                    moved = True
                mbt.encode_ramdisk(meta_dir, magiskboot, compression, False)

        if os.path.exists(staged_img):
            os.unlink(staged_img)
        mbt.run([magiskboot, "repack", mbt.SOURCE_IMAGE, staged_img], meta_dir)
        if not os.path.isfile(staged_img):
            return False, f"magiskboot repack produced no output: {staged_img}"
        with source.open("rb") as original:
            is_vendor_boot = original.read(8) == b"VNDRBOOT"
        if is_vendor_boot:
            size_error = _fit_vendor_boot_image_size(source, Path(staged_img))
            if size_error:
                return False, size_error
        os.replace(staged_img, out_img)
    except mbt.ToolError as exc:
        return False, str(exc)
    except cpio_patch.CpioPatchError as exc:
        return False, f"repack failed: {exc}"
    except Exception as exc:
        return False, f"repack failed: {exc}"
    finally:
        try:
            if os.path.isfile(staged_img):
                os.unlink(staged_img)
        except OSError:
            pass
        # Always lift the editable pieces back to the visible folder.
        if moved and inner_ramdisk.is_dir():
            _move_into(inner_ramdisk, visible_ramdisk)
        if not visible_header.is_file() and inner_header.is_file():
            shutil.copyfile(str(inner_header), str(visible_header))
        mbt.set_hidden(meta_dir)

    if not os.path.isfile(out_img):
        return False, f"magiskboot repack produced no output: {out_img}"

    return True, out_img


def _fit_vendor_boot_image_size(source: Path, output: Path) -> str | None:

    target_size = source.stat().st_size
    output_size = output.stat().st_size
    if output_size > target_size:
        return (
            f"Repacked vendor_boot needs {output_size} bytes, but the original "
            f"image is {target_size} bytes. Reduce ramdisk content or "
            "compression size."
        )
    if output_size == target_size:
        return None

    with output.open("r+b") as image:
        footer = b""
        if output_size >= 64:
            image.seek(output_size - 64)
            candidate = image.read(64)
            if candidate[:4] == b"AVBf":
                import struct
                _, _, _, original_size, vbmeta_offset, vbmeta_size = \
                    struct.unpack_from(">4sIIQQQ", candidate)
                if (0 < original_size <= vbmeta_offset <= output_size - 64 and
                        0 < vbmeta_size <= output_size - 64 - vbmeta_offset):
                    footer = candidate

        if footer:
            image.truncate(output_size - 64)
            image.seek(target_size - 64)
            image.write(footer)
        else:
            if target_size >= 64:
                with source.open("rb") as original:
                    original.seek(-64, os.SEEK_END)
                    if original.read(4) == b"AVBf":
                        return "magiskboot repack did not preserve the original AVB footer"
            image.truncate(target_size)
    return None


def _patch_ramdisk(magiskboot, meta_dir, visible_ramdisk, manifest,
                   compression, desired_modes=None):

    import tempfile

    ops, warnings = cpio_patch.diff_against_manifest(
        visible_ramdisk, manifest, desired_modes=desired_modes
    )

    scratch = Path(tempfile.mkdtemp(prefix="rak_cpio_"))
    try:
        if " " in str(scratch):
            raise cpio_patch.CpioPatchError(
                f"temp dir has spaces, cannot patch cpio safely: {scratch}"
            )
        dec = scratch / mbt.RAMDISK_DECODED
        shutil.copyfile(meta_dir / PRISTINE_DEC, dec)
        cpio_patch.apply_ops(magiskboot, dec, ops, scratch)

        # Recompress the patched cpio back into the component magiskboot repack
        # reads (meta_dir/ramdisk.cpio), honoring the original format.
        component = meta_dir / mbt.RAMDISK_COMPONENT
        if compression in ("none", "raw", "unknown"):
            shutil.copyfile(dec, component)
        else:
            patched_component = scratch / mbt.RAMDISK_COMPONENT
            mbt.run(
                [magiskboot, f"compress={compression}",
                 mbt.RAMDISK_DECODED, mbt.RAMDISK_COMPONENT],
                scratch,
            )
            shutil.copyfile(patched_component, component)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    return warnings


def main(img_path):
    return unpack_boot_image(img_path)
