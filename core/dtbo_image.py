# SPDX-License-Identifier: MPL-2.0
"""Native Rust-backed DTBO unpack and repack helpers."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile

from core import native_backend
from core.command_context import OperationError
from core.threader_control import get_progress_reporter

MANIFEST_FILENAME = "dtbo_manifest.json"
DTB_DIR = "dtb"
DTS_DIR = "dts"


def _natural_sort_key(text: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def is_dtbo_folder(folder_path: str | Path) -> bool:
    folder = Path(folder_path)
    if not folder.is_dir():
        return False
    if (folder / MANIFEST_FILENAME).is_file():
        return True
    dts_dir = folder / DTS_DIR
    if dts_dir.is_dir() and any(
        p.is_file() and (p.name.lower().endswith(".dts") or p.name.lower().startswith("dts."))
        for p in dts_dir.iterdir()
    ):
        return True
    dtb_dir = folder / DTB_DIR
    if dtb_dir.is_dir() and any(
        p.is_file() and (p.name.lower().endswith(".dtb") or p.name.lower().startswith("dtbo."))
        for p in dtb_dir.iterdir()
    ):
        return True
    return False


def _write_dtboconf(base_dir: Path, name: str, meta: dict) -> None:
    cfg_dir = base_dir / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    conf_path = cfg_dir / f"{name}_dtboconf"
    payload = {
        "kind": "dtbo",
        "page_size": int(meta.get("page_size", 2048)),
        "version": int(meta.get("version", 0)),
        "entry_count": int(meta.get("entry_count", 0)),
    }
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=cfg_dir, mode="w", encoding="utf-8", newline="\n", delete=False
        ) as handle:
            tmp = Path(handle.name)
            json.dump(payload, handle, indent=2)
        tmp.replace(conf_path)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink(missing_ok=True)


def inspect_dtbo_image(img_path: str | Path) -> dict:
    path = Path(img_path)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    try:
        return native_backend.dtbo_inspect(str(path))
    except OSError as exc:
        raise OperationError(str(exc)) from exc


def unpack_dtbo_image(img_path: str | Path, out_dir: str | Path | None = None):
    src = Path(os.path.abspath(os.fspath(img_path)))
    if not src.is_file():
        return False, f"File not found: {src}"

    dest = Path(os.path.abspath(os.fspath(out_dir))) if out_dir else src.with_suffix("")
    if dest == src:
        dest = src.parent / (src.name + "_unpacked")

    try:
        res = native_backend.dtbo_unpack(
            str(src),
            str(dest),
            decompile_dts=True,
            progress=get_progress_reporter(),
        )
        _write_dtboconf(dest.parent, dest.name, res)
        return True, str(dest)
    except Exception as exc:
        return False, str(exc)


def _compile_dts_to_dtb(dts_path: Path, dtb_path: Path) -> None:
    from core.devicetree import compile as dt_compile

    dts_text = dts_path.read_text(encoding="utf-8", errors="replace")
    compiled = dt_compile(dts_text, base_dir=str(dts_path.parent))
    dtb_path.parent.mkdir(parents=True, exist_ok=True)
    dtb_path.write_bytes(compiled)


def repack_dtbo_image(folder_path: str | Path, output_path: str | Path | None = None):
    folder = Path(os.path.abspath(os.fspath(folder_path)))
    if not folder.is_dir():
        return False, f"Folder not found: {folder}"

    out_img = (
        Path(os.path.abspath(os.fspath(output_path)))
        if output_path
        else Path(str(folder).rstrip("/\\") + "_new.img")
    )

    manifest_path = folder / MANIFEST_FILENAME
    manifest = {}
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"Invalid {MANIFEST_FILENAME}: {exc}"

    dt_type = str(manifest.get("dt_type") or "dtb")
    page_size = int(manifest.get("page_size") or 2048)
    version = int(manifest.get("version") or 0)
    manifest_entries = list(manifest.get("entries") or [])

    dts_dir = folder / DTS_DIR
    dtb_dir = folder / DTB_DIR
    pack_entries = []
    seen_dts: set[Path] = set()
    seen_dtb: set[Path] = set()

    report = get_progress_reporter()
    if report:
        report(10, "Compiling DTS entries")

    for idx, entry in enumerate(manifest_entries):
        dts_rel = entry.get("dts_file")
        dtb_rel = entry.get("dtb_file") or f"{DTB_DIR}/dtbo.{idx}.dtb"
        dts_path = (folder / dts_rel) if dts_rel else None
        dtb_path = folder / dtb_rel

        if dts_path and dts_path.is_file():
            seen_dts.add(dts_path.resolve())
            _compile_dts_to_dtb(dts_path, dtb_path)
        elif not dtb_path.is_file():
            return False, f"Missing DTBO entry source for index {idx}: {dtb_path}"

        seen_dtb.add(dtb_path.resolve())
        pack_entries.append({
            "dtb_path": str(dtb_path),
            "id": int(entry.get("id", 0)),
            "rev": int(entry.get("rev", 0)),
            "flags": int(entry.get("flags", 0)),
            "custom": [int(x) for x in (entry.get("custom") or [0, 0, 0, 0])[:4]],
            "compression": str(entry.get("compression") or "none"),
        })

    # Discover any newly added .dts files (not in manifest)
    if dts_dir.is_dir():
        extra_dts = sorted(
            [
                p
                for p in dts_dir.iterdir()
                if p.is_file()
                and (p.name.lower().endswith(".dts") or p.name.lower().startswith("dts."))
                and p.resolve() not in seen_dts
            ],
            key=lambda p: _natural_sort_key(p.name),
        )
        for dts_path in extra_dts:
            stem = dts_path.stem if dts_path.suffix.lower() == ".dts" else dts_path.name
            dtb_path = dtb_dir / f"{stem}.dtb"
            _compile_dts_to_dtb(dts_path, dtb_path)
            seen_dtb.add(dtb_path.resolve())
            pack_entries.append({
                "dtb_path": str(dtb_path),
                "id": 0,
                "rev": 0,
                "flags": 0,
                "custom": [0, 0, 0, 0],
                "compression": "none",
            })

    # Fallback: raw .dtb files when no manifest / no .dts files exist
    if not pack_entries and dtb_dir.is_dir():
        raw_dtbs = sorted(
            [
                p
                for p in dtb_dir.iterdir()
                if p.is_file()
                and (p.name.lower().endswith(".dtb") or p.name.lower().startswith("dtbo."))
                and p.resolve() not in seen_dtb
            ],
            key=lambda p: _natural_sort_key(p.name),
        )
        for dtb_path in raw_dtbs:
            pack_entries.append({
                "dtb_path": str(dtb_path),
                "id": 0,
                "rev": 0,
                "flags": 0,
                "custom": [0, 0, 0, 0],
                "compression": "none",
            })

    if not pack_entries:
        return False, f"No DTBO entries (.dts or .dtb) found in {folder}"

    spec = {
        "dt_type": dt_type,
        "page_size": page_size,
        "version": version,
        "entries": pack_entries,
    }

    tmp_img = out_img.with_name(out_img.name + ".rak-dtbo.tmp")
    try:
        native_backend.dtbo_pack(str(tmp_img), spec, progress=report)
        if not tmp_img.is_file():
            return False, f"DTBO repack produced no output: {out_img}"
        os.replace(tmp_img, out_img)
        return True, str(out_img)
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            if tmp_img.is_file():
                tmp_img.unlink()
        except OSError:
            pass
