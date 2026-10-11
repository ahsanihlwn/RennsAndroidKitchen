# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET

from core.vdev.report import Finding, WARN, INFO, PASS

KEY = "vintf"
LABEL = "VINTF / HAL"

_VND_VINTF = "/vendor/etc/vintf"
_SYS_VINTF = "/system/etc/vintf"
_MAX_LIST = 15


def _parse(vfs, vpath):
    raw = vfs.read_bytes(vpath)
    if not raw:
        return None
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        return None


def _hal_key(hal):
    name_el = hal.find("name")
    return (name_el.text or "").strip() if name_el is not None else ""


def _collect_manifest(vfs):

    hals = set()
    target_level = ""
    paths = [_VND_VINTF + "/manifest.xml"]
    frag_dir = _VND_VINTF + "/manifest"
    if vfs.is_dir(frag_dir):
        paths.extend(vfs.glob(frag_dir, ".xml"))

    found_any = False
    for p in paths:
        root = _parse(vfs, p)
        if root is None:
            continue
        found_any = True
        lvl = (root.get("target-level") or "").strip()
        if lvl and not target_level:
            target_level = lvl
        for hal in root.iter("hal"):
            name = _hal_key(hal)
            if name:
                hals.add(name)
    return (hals, target_level) if found_any else (None, "")


def _required_hals(vfs, level):

    candidates = []
    if level:
        candidates.append(f"{_SYS_VINTF}/compatibility_matrix.{level}.xml")


    for p in candidates:
        root = _parse(vfs, p)
        if root is not None:
            return _extract_required(root), p

    if not vfs.is_dir(_SYS_VINTF):
        return None, ""
    for p in vfs.glob(_SYS_VINTF, ".xml"):
        if "compatibility_matrix" not in posixpath.basename(p):
            continue
        root = _parse(vfs, p)
        if root is None:
            continue
        if root.get("type") != "framework":
            continue
        if level and (root.get("level") or "").strip() != str(level):
            continue
        return _extract_required(root), p
    return None, ""


def _extract_required(root):
    out = set()
    for hal in root.iter("hal"):
        # Default atribut optional di compatibility matrix adalah "false",
        # jadi HAL tanpa atribut itu WAJIB.
        if (hal.get("optional") or "false").strip().lower() == "true":
            continue
        name = _hal_key(hal)
        if name:
            out.add(name)
    return out


def run(ctx):
    findings = []
    vfs = ctx.vfs

    if not vfs.is_dir(_VND_VINTF):
        return [Finding(
            KEY, "no-vendor-vintf", INFO,
            "No vendor in this project — VINTF check skipped",
            "HAL declarations can only be judged when vendor is unpacked.",
        )]

    provided, target_level = _collect_manifest(vfs)
    if provided is None:
        return [Finding(
            KEY, "no-manifest", WARN,
            "Vendor manifest.xml is missing or unreadable",
            "Without a manifest, hwservicemanager does not know which HALs "
            "exist and the framework assumes the device provides nothing.",
            "Take /vendor/etc/vintf/manifest.xml again from a stock vendor.",
            _VND_VINTF + "/manifest.xml",
        )]

    findings.append(Finding(
        KEY, "manifest", INFO,
        f"Vendor declares {len(provided)} HAL(s)"
        + (f" · target-level {target_level}" if target_level else ""),
        "", "", _VND_VINTF + "/manifest.xml",
    ))

    if not target_level:
        findings.append(Finding(
            KEY, "no-target-level", WARN,
            "Vendor manifest has no target-level",
            "Without target-level there is no way to tell which framework "
            "matrix applies, so HAL matching is not judged.",
        ))
        return findings

    required, matrix_path = _required_hals(vfs, target_level)
    if required is None:
        findings.append(Finding(
            KEY, "no-matrix", WARN,
            f"No framework compatibility matrix for level {target_level}",
            "This system ships no matrix matching the vendor target-level. "
            "That usually means system and vendor are far apart in version.",
            "Use a system/vendor pair with a reasonable version gap.",
        ))
        return findings

    missing = sorted(required - provided)
    if not missing:
        findings.append(Finding(
            KEY, "hal-ok", PASS,
            f"All {len(required)} required HALs for level {target_level} are present",
            "", "", matrix_path,
        ))
        return findings

    shown = ", ".join(missing[:_MAX_LIST])
    if len(missing) > _MAX_LIST:
        shown += f" (+{len(missing) - _MAX_LIST} more)"

    findings.append(Finding(
        KEY, "hal-missing", WARN,
        f"{len(missing)} required HAL(s) not declared by vendor",
        f"According to {posixpath.basename(matrix_path)} (level {target_level}), "
        f"the framework requires these HALs but the vendor manifest does not "
        f"list them: {shown}. "
        "The usual effect is a dead subsystem (radio/camera/wifi) or a "
        "system_server crash-loop — not always a fully dark screen.",
        "Match vendor to the system version, or add manifest fragments for "
        "HALs whose implementation actually exists in vendor.",
        matrix_path,
    ))
    return findings
