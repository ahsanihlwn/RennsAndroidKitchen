# SPDX-License-Identifier: MPL-2.0
"""Clean-room APK inventory scanner and non-destructive debloat/quarantine manager."""
from __future__ import annotations

import base64
import json
import os
import shutil
import time
from pathlib import Path

from core.native_backend import read_apk_icon, scan_apks

QUARANTINE_DIRNAME = ".rak_quarantine"
APKS_SUBDIR = "apks"
MANIFEST_FILE = "manifest.json"


def _quarantine_root(base_dir: str) -> Path:
    return Path(base_dir).resolve() / QUARANTINE_DIRNAME / APKS_SUBDIR


def _safe_rel(base_dir: str, rel_path: str) -> tuple[Path, Path]:
    base = Path(base_dir).resolve()
    rel = rel_path.replace("\\", "/").strip("/")
    if not rel or ".." in Path(rel).parts:
        raise ValueError(f"Unsafe relative APK path: {rel_path}")
    live = (base / rel).resolve()
    if not live.is_relative_to(base):
        raise ValueError(f"Path escapes project tree: {rel_path}")
    qroot = _quarantine_root(base_dir).resolve()
    quarantined = (qroot / rel).resolve()
    if not quarantined.is_relative_to(qroot):
        raise ValueError(f"Quarantine path escapes project tree: {rel_path}")
    return live, quarantined


def _load_manifest(base_dir: str) -> dict:
    mpath = _quarantine_root(base_dir) / MANIFEST_FILE
    if not mpath.is_file():
        return {"version": 1, "items": []}
    try:
        data = json.loads(mpath.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "items": []}


def _save_manifest(base_dir: str, data: dict) -> None:
    qroot = _quarantine_root(base_dir)
    qroot.mkdir(parents=True, exist_ok=True)
    mpath = qroot / MANIFEST_FILE
    tmp = qroot / f".{MANIFEST_FILE}.tmp"
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, mpath)


def scan_project_apks(base_dir: str) -> list[dict]:
    if not base_dir or not os.path.isdir(base_dir):
        return []
    return scan_apks(base_dir)


def load_apk_icon_data_url(apk_path: str, entry_name: str) -> str:
    if not apk_path or not entry_name or not os.path.isfile(apk_path):
        return ""
    try:
        raw = read_apk_icon(apk_path, entry_name)
    except Exception:
        return ""
    if not raw:
        return ""
    lower = entry_name.lower()
    mime = "image/webp" if lower.endswith(".webp") else "image/jpeg" if lower.endswith((".jpg", ".jpeg")) else "image/png"
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{mime};base64,{b64}"


def import_debloat_list(list_path: str) -> list[str]:
    text = Path(list_path).read_text(encoding="utf-8", errors="replace")
    stripped = text.strip()
    if stripped.startswith("[") or stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
            if isinstance(parsed, dict):
                items = parsed.get("packages") or parsed.get("apks") or []
                return [str(x).strip() for x in items if str(x).strip()]
        except ValueError:
            pass
    out = []
    for line in text.splitlines():
        token = line.split("#", 1)[0].strip()
        if token:
            out.append(token)
    return list(dict.fromkeys(out))


def export_debloat_list(entries: list[str], out_path: str) -> str:
    clean = [str(e).strip() for e in entries if str(e).strip()]
    Path(out_path).write_text("\n".join(clean) + ("\n" if clean else ""), encoding="utf-8")
    return out_path


def remove_apks(base_dir: str, records: list[dict]) -> dict:
    """Move selected APKs into lossless project quarantine (never destructive delete)."""
    manifest = _load_manifest(base_dir)
    existing = {row.get("rel_path"): row for row in manifest["items"] if isinstance(row, dict)}
    moved = []
    skipped = []

    for rec in records:
        rel = rec.get("rel_path") or ""
        try:
            live, quarantined = _safe_rel(base_dir, rel)
        except ValueError:
            skipped.append(rel)
            continue
        if not live.is_file():
            skipped.append(rel)
            continue
        quarantined.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(live), str(quarantined))
        existing[rel] = {
            "rel_path": rel,
            "package": rec.get("package", ""),
            "name": rec.get("name", os.path.basename(rel)),
            "partition": rec.get("partition", ""),
            "removed_at": int(time.time()),
        }
        moved.append(rel)

    manifest["items"] = sorted(existing.values(), key=lambda r: r.get("rel_path", ""))
    _save_manifest(base_dir, manifest)
    return {"moved": moved, "skipped": skipped, "quarantined_count": len(manifest["items"])}


def list_quarantined_apks(base_dir: str) -> list[dict]:
    if not base_dir or not os.path.isdir(base_dir):
        return []
    manifest = _load_manifest(base_dir)
    out = []
    for row in manifest["items"]:
        rel = row.get("rel_path", "")
        try:
            _, qpath = _safe_rel(base_dir, rel)
        except ValueError:
            continue
        if qpath.is_file():
            item = dict(row)
            item["quarantine_path"] = str(qpath)
            out.append(item)
    return out


def restore_apks(base_dir: str, rel_paths: list[str] | None = None) -> dict:
    manifest = _load_manifest(base_dir)
    target_set = set(rel_paths) if rel_paths else None
    restored = []
    remaining = []

    for row in manifest["items"]:
        rel = row.get("rel_path", "")
        if target_set is not None and rel not in target_set:
            remaining.append(row)
            continue
        try:
            live, quarantined = _safe_rel(base_dir, rel)
        except ValueError:
            remaining.append(row)
            continue
        if quarantined.is_file():
            live.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(quarantined), str(live))
            restored.append(rel)
        # Drop stale entry if file already gone.

    manifest["items"] = remaining
    _save_manifest(base_dir, manifest)
    return {"restored": restored, "quarantined_count": len(remaining)}
