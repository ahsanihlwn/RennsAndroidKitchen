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

# Boot-essential HAL families on standard Android phones/tablets.
_ESSENTIAL_HAL_FAMILIES = (
    ("graphics composer", ("android.hardware.graphics.composer", "android.hardware.graphics.composer3")),
    ("graphics allocator", ("android.hardware.graphics.allocator",)),
    ("graphics mapper", ("android.hardware.graphics.mapper", "mapper")),
    ("power", ("android.hardware.power",)),
    ("health", ("android.hardware.health",)),
    ("gatekeeper", ("android.hardware.gatekeeper",)),
    ("keystore (KeyMint/Keymaster)", ("android.hardware.security.keymint", "android.hardware.keymaster")),
)

_FORM_FACTOR_HAL_PREFIXES = (
    ("android.hardware.automotive.", "android.hardware.type.automotive"),
    ("android.hardware.tv.", "android.software.leanback"),
    ("android.hardware.broadcastradio", "android.hardware.type.automotive"),
)


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


def _hal_versions(hal):
    out = set()
    for v in hal.findall("version"):
        t = (v.text or "").strip()
        if t:
            out.add(t)
    for fq in hal.findall("fqname"):
        t = (fq.text or "").strip()
        if t.startswith("@") and "::" in t:
            out.add(t[1:].split("::", 1)[0].strip())
    return out


def _hal_fqnames(hal):
    out = set()
    for fq in hal.findall("fqname"):
        t = (fq.text or "").strip()
        if t:
            out.add(t)
    for iface in hal.findall("interface"):
        iname = (iface.findtext("name") or "").strip()
        for inst in iface.findall("instance"):
            iv = (inst.text or "").strip()
            if iname and iv:
                out.add(f"{iname}/{iv}")
    return out


def _device_features(vfs):
    feats = set()
    for base in ("/vendor/etc/permissions", "/odm/etc/permissions", "/product/etc/permissions", "/system/etc/permissions"):
        if not vfs.is_dir(base):
            continue
        for p in vfs.glob(base, ".xml"):
            raw = vfs.read_bytes(p)
            if not raw or b"<feature" not in raw:
                continue
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                continue
            for el in root.iter("feature"):
                nm = (el.get("name") or "").strip()
                if nm:
                    feats.add(nm)
    return feats


def _collect_manifest(vfs):

    hals = {}  # name -> {"formats": set, "versions": set, "fqnames": set, "sources": set}
    target_level = ""
    paths = []
    for base in (_VND_VINTF, "/odm/etc/vintf"):
        paths.append(base + "/manifest.xml")
        frag_dir = base + "/manifest"
        if vfs.is_dir(frag_dir):
            paths.extend(vfs.glob(frag_dir, ".xml"))
    # Include Vendor/Device APEX VINTF manifests (updatable-via-apex HALs on Android 14-16)
    if vfs.is_dir("/apex"):
        for pkg in vfs.listdir("/apex"):
            ap_vintf = f"/apex/{pkg}/etc/vintf"
            if vfs.is_dir(ap_vintf):
                paths.extend(vfs.glob(ap_vintf, ".xml"))

    found_any = False
    for p in paths:
        root = _parse(vfs, p)
        if root is None:
            continue
        if p.startswith("/apex/") and (root.get("type") or "").strip().lower() != "device":
            continue
        found_any = True
        lvl = (root.get("target-level") or "").strip()
        if lvl and not target_level:
            target_level = lvl
        for hal in root.iter("hal"):
            name = _hal_key(hal)
            if not name:
                continue
            slot = hals.setdefault(name, {"formats": set(), "versions": set(), "fqnames": set(), "sources": set()})
            fmt = (hal.get("format") or "hidl").strip().lower()
            slot["formats"].add(fmt)
            slot["versions"] |= _hal_versions(hal)
            slot["fqnames"] |= _hal_fqnames(hal)
            slot["sources"].add(p)
    return (hals, target_level) if found_any else (None, "")


def _required_hals(vfs, level):

    candidates = []
    if level:
        for base in (_SYS_VINTF, "/system_ext/etc/vintf", "/product/etc/vintf"):
            candidates.append(f"{base}/compatibility_matrix.{level}.xml")

    for p in candidates:
        root = _parse(vfs, p)
        if root is not None:
            req, is_v9 = _extract_required(root)
            return req, p, is_v9

    if not vfs.is_dir(_SYS_VINTF):
        return None, "", False
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
        req, is_v9 = _extract_required(root)
        return req, p, is_v9
    return None, "", False


def _extract_required(root):
    ver_s = (root.get("version") or "1.0").strip()
    try:
        schema_ver = float(ver_s.split(".", 1)[0] + "." + (ver_s.split(".", 1)[1].split(".")[0] if "." in ver_s else "0"))
    except ValueError:
        schema_ver = 1.0
    # Android 15/16 VINTF schema v9.0 removed `optional` from compatibility matrices:
    # every <hal> entry in compatibility_matrix.202404+/202504+ is an interface catalog entry
    # whose version is enforced if declared by the device, rather than mandatory for all devices.
    has_optional_attr = any(h.get("optional") is not None for h in root.iter("hal"))
    is_catalog_v9 = schema_ver >= 9.0 and not has_optional_attr

    out = {}  # name -> {"formats": set, "versions": set, "fqnames": set}
    for hal in root.iter("hal"):
        if not is_catalog_v9 and (hal.get("optional") or "false").strip().lower() == "true":
            continue
        name = _hal_key(hal)
        if not name:
            continue
        slot = out.setdefault(name, {"formats": set(), "versions": set(), "fqnames": set()})
        fmt = (hal.get("format") or "hidl").strip().lower()
        slot["formats"].add(fmt)
        slot["versions"] |= _hal_versions(hal)
        slot["fqnames"] |= _hal_fqnames(hal)
    return out, is_catalog_v9


def _version_satisfies(req_spec: str, prov_versions: set[str]) -> bool:
    """Check Android VINTF version requirement (`major.minor` or `major.min-max` or AIDL `N` or `N-M`)."""
    if not prov_versions:
        return True  # Some AIDL v1 declarations omit explicit <version>
    req_spec = req_spec.strip()
    if not req_spec:
        return True
    try:
        if "." in req_spec:
            maj_s, min_part = req_spec.split(".", 1)
            maj = int(maj_s)
            if "-" in min_part:
                lo_s, hi_s = min_part.split("-", 1)
                lo, hi = int(lo_s), int(hi_s)
            else:
                lo, hi = int(min_part), 9999
            for pv in prov_versions:
                if "." not in pv:
                    continue
                pm_s, pn_s = pv.split(".", 1)
                if int(pm_s) == maj and lo <= int(pn_s.split("-", 1)[0]) <= hi:
                    return True
            return False
        else:
            if "-" in req_spec:
                lo_s, hi_s = req_spec.split("-", 1)
                lo, hi = int(lo_s), int(hi_s)
            else:
                lo, hi = int(req_spec), 9999
            for pv in prov_versions:
                if "." in pv:
                    continue
                v = int(pv.split("-", 1)[0])
                if lo <= v <= hi:
                    return True
            return False
    except ValueError:
        return True


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

    required, matrix_path, is_catalog_v9 = _required_hals(vfs, target_level)
    if required is None:
        findings.append(Finding(
            KEY, "no-matrix", WARN,
            f"No framework compatibility matrix for level {target_level}",
            "This system ships no matrix matching the vendor target-level. "
            "That usually means system and vendor are far apart in version.",
            "Use a system/vendor pair with a reasonable version gap.",
        ))
        ctx.shared["vintf"] = {
            "target_level": target_level,
            "provided_count": len(provided),
            "provided_hals": {
                k: {
                    "formats": sorted(v["formats"]),
                    "versions": sorted(v["versions"]),
                    "fqnames": sorted(v["fqnames"]),
                    "sources": sorted(v["sources"]),
                }
                for k, v in provided.items()
            },
        }
        return findings

    feats = _device_features(vfs)
    ver_mismatch = []
    matched_count = 0
    for hname, rinfo in sorted(required.items()):
        if hname not in provided:
            continue
        matched_count += 1
        pvers = provided[hname]["versions"]
        rvers = rinfo["versions"]
        if rvers and pvers and not any(_version_satisfies(rv, pvers) for rv in rvers):
            ver_mismatch.append(f"{hname} (need {'/'.join(sorted(rvers))}, vendor has {'/'.join(sorted(pvers))})")

    missing_essential = []
    if is_catalog_v9:
        # VINTF v9.0 matrix is a full interface catalog (including automotive/TV/radio);
        # check version compatibility of all declared HALs plus boot-essential HAL families.
        for fam_label, prefixes in _ESSENTIAL_HAL_FAMILIES:
            if not any(any(h == p or h.startswith(p + ".") for p in prefixes) for h in provided):
                # Only flag if at least one HAL in this family is listed in the catalog
                if any(any(ch == p or ch.startswith(p + ".") for p in prefixes) for ch in required):
                    missing_essential.append(fam_label)
        missing = missing_essential
        optional_unimpl = sorted(set(required) - set(provided))
    else:
        raw_missing = sorted(set(required) - set(provided))
        missing = []
        optional_unimpl = []
        for hname in raw_missing:
            skipped_ff = False
            for pref, req_feat in _FORM_FACTOR_HAL_PREFIXES:
                if hname.startswith(pref) and req_feat not in feats:
                    skipped_ff = True
                    break
            if skipped_ff:
                optional_unimpl.append(hname)
            else:
                missing.append(hname)

    ctx.shared["vintf"] = {
        "target_level": target_level,
        "schema_v9_catalog": is_catalog_v9,
        "provided_count": len(provided),
        "required_count": len(required),
        "matched_count": matched_count,
        "missing_hals": missing,
        "optional_unimplemented": len(optional_unimpl),
        "version_mismatches": ver_mismatch,
        "matrix_path": matrix_path,
        "provided_hals": {
            k: {
                "formats": sorted(v["formats"]),
                "versions": sorted(v["versions"]),
                "fqnames": sorted(v["fqnames"]),
                "sources": sorted(v["sources"]),
            }
            for k, v in provided.items()
        },
    }

    if not missing and not ver_mismatch:
        if is_catalog_v9:
            findings.append(Finding(
                KEY, "hal-ok", PASS,
                f"VINTF v9.0 level {target_level} coherent: {matched_count} declared HAL(s) match matrix versions & all core boot HALs present",
                f"Checked {len(provided)} device HAL(s) (including APEX manifests) against {posixpath.basename(matrix_path)}; "
                f"{len(optional_unimpl)} optional/non-phone catalog HAL(s) omitted as expected.",
                "", matrix_path,
            ))
        else:
            findings.append(Finding(
                KEY, "hal-ok", PASS,
                f"All {len(required)} required HALs (and versions) for level {target_level} are satisfied",
                "", "", matrix_path,
            ))
        return findings

    if missing:
        shown = ", ".join(missing)
        findings.append(Finding(
            KEY, "hal-missing", WARN,
            f"{len(missing)} {'core boot HAL famil(ies)' if is_catalog_v9 else 'required HAL(s)'} not declared by vendor",
            f"According to {posixpath.basename(matrix_path)} (level {target_level}), "
            f"these HALs are missing from the vendor/odm/apex VINTF manifests: {shown}. "
            "The usual effect is a dead subsystem (display/keystore/power/audio) or a "
            "system_server crash-loop — not always a fully dark screen.",
            "Match vendor to the system version, or add manifest fragments for "
            "HALs whose implementation actually exists in vendor.",
            matrix_path,
        ))

    if ver_mismatch:
        shown_v = "; ".join(ver_mismatch)
        findings.append(Finding(
            KEY, "hal-version-mismatch", WARN,
            f"{len(ver_mismatch)} HAL(s) declared by vendor have incompatible version",
            shown_v + f" against {posixpath.basename(matrix_path)} (level {target_level}).",
            "Align vendor HAL versions or framework compatibility_matrix.",
            matrix_path,
        ))
    return findings
