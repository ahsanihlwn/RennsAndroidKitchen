# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "device"
LABEL = "Device profile"

_PROP_FILES = (
    ("system", "/system/build.prop"),
    ("system", "/system/etc/build.prop"),
    ("system_ext", "/system_ext/etc/build.prop"),
    ("system_dlkm", "/system_dlkm/etc/build.prop"),
    ("vendor", "/vendor/build.prop"),
    ("vendor", "/vendor/etc/build.prop"),
    ("vendor_dlkm", "/vendor_dlkm/etc/build.prop"),
    ("odm", "/odm/etc/build.prop"),
    ("odm_dlkm", "/odm_dlkm/etc/build.prop"),
    ("product", "/product/etc/build.prop"),
)

# Properti yang dipakai stage lain — dibaca semua, tapi ini yang ditampilkan.
_HIGHLIGHT = (
    "ro.build.version.sdk",
    "ro.build.version.release",
    "ro.product.device",
    "ro.board.platform",
    "ro.zygote",
    "ro.vndk.version",
    "ro.treble.enabled",
)


def _parse_props_detailed(text):
    """Parse build.prop matching Android init LoadPropertiesFromFile semantics:
    later lines inside the SAME file override earlier lines (e.g. MediaTek MGVI
    overrides ro.board.platform=common -> mt6855/mt6789)."""
    out = {}
    overrides = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k:
            continue
        if k in out and out[k] != v:
            overrides.append((k, out[k], v))
        out[k] = v
    return out, overrides


def _parse_props(text):
    return _parse_props_detailed(text)[0]


def _part_for_prop_path(vpath: str) -> str:
    segs = [s for s in vpath.strip("/").split("/") if s]
    return segs[0] if segs else "system"


def run(ctx):
    findings = []
    vfs = ctx.vfs
    props = {}
    prop_owner = {}  # key -> (vpath, val)
    sources = []
    prop_trace_files = []
    by_partition = {}  # part -> {k: v}
    cross_conflicts = []

    candidates = [vp for _p, vp in _PROP_FILES]
    for m in sorted(vfs.mounts, key=lambda x: x.point):
        if m.partition.startswith("apex:"):
            continue
        base = "" if m.point == "/" else m.point
        candidates.append(base + "/build.prop")
        candidates.append(base + "/etc/build.prop")

    seen_paths = set()
    for vpath in candidates:
        if vpath in seen_paths:
            continue
        seen_paths.add(vpath)
        text = vfs.read_text(vpath)
        if not text:
            continue
        sources.append(vpath)
        part = _part_for_prop_path(vpath)
        parsed, intra_overrides = _parse_props_detailed(text)
        prop_trace_files.append({
            "path": vpath,
            "partition": part,
            "count": len(parsed),
            "overrides": intra_overrides,
        })
        p_map = by_partition.setdefault(part, {})
        for k, v in parsed.items():
            p_map[k] = v
            if k not in props:
                props[k] = v
                prop_owner[k] = (vpath, v)
            elif props[k] != v:
                wf, wv = prop_owner[k]
                cross_conflicts.append({
                    "key": k,
                    "winner_file": wf,
                    "winner_val": wv,
                    "ignored_file": vpath,
                    "ignored_val": v,
                })

    # Bootloader / kernel cmdline / bootconfig / DTB properties
    boot_props = getattr(ctx.layout, "boot_props", None) or {}
    for k, v in boot_props.items():
        props.setdefault(k, v)

    ctx.profile["props"] = props
    ctx.profile["prop_sources"] = sources
    ctx.profile["prop_trace"] = {
        "files": prop_trace_files,
        "by_partition": by_partition,
        "cross_conflicts": cross_conflicts,
        "boot_props_count": len(boot_props),
    }
    ctx.profile["sar"] = ctx.layout.sar
    ctx.profile["partitions"] = sorted(ctx.layout.all_partition_names())
    ctx.profile["has_lib64"] = vfs.is_dir("/system/lib64") or vfs.is_dir("/vendor/lib64")

    if not props:


        ctx.profile["no_rom"] = True
        findings.append(Finding(
            KEY, "no-build-prop", INFO,
            "No build.prop — this folder cannot be judged",
            "No build.prop in any mounted partition, so the ROM identity is "
            "unknown. This folder may not be an unpacked ROM at all, or it "
            "holds only extra OEM partitions.",
            "Open a project that contains an unpacked system partition "
            "(ideally vendor and vendor_boot too).",
        ))
        return findings

    sdk = props.get("ro.build.version.sdk", "")
    ctx.profile["sdk"] = sdk

    label = " · ".join(
        f"{k.split('.')[-1]}={props[k]}" for k in _HIGHLIGHT if props.get(k)
    )
    findings.append(Finding(
        KEY, "profile", INFO,
        f"Android {props.get('ro.build.version.release', '?')} "
        f"(SDK {sdk or '?'}) — {props.get('ro.product.device', 'unknown device')}",
        label,
    ))

    findings.append(Finding(
        KEY, "layout", INFO,
        ("system-as-root" if ctx.layout.sar else "system mounted at /system"),
        "Unpacked partitions: " + ", ".join(ctx.profile["partitions"]),
    ))

    # ── build.prop load order & override trace ──
    total_intra = sum(len(f["overrides"]) for f in prop_trace_files)
    ro_conflicts = [c for c in cross_conflicts if c["key"].startswith("ro.")]
    prop_detail_lines = [
        f"{f['path']} ({f['count']} props"
        + (f", {len(f['overrides'])} intra-file override(s)" if f["overrides"] else "")
        + ")"
        for f in prop_trace_files
    ]
    if ro_conflicts:
        prop_detail_lines.append(
            f"Cross-partition ro.* shadows ({len(ro_conflicts)}): "
            + "; ".join(
                f"{c['key']}={c['winner_val']} ({c['winner_file']}) shadows '{c['ignored_val']}' ({c['ignored_file']})"
                for c in ro_conflicts[:12]
            )
            + (f" (+{len(ro_conflicts) - 12} more)" if len(ro_conflicts) > 12 else "")
        )
    findings.append(Finding(
        KEY, "prop-load", INFO,
        f"Loaded {len(props)} properties from {len(prop_trace_files)} build.prop file(s)"
        + (f" · {total_intra} override(s)" if total_intra else "")
        + (f" · {len(ro_conflicts)} cross-partition ro.* shadow(s)" if ro_conflicts else ""),
        "\n".join(prop_detail_lines),
    ))

    # Arsitektur: 64-bit tanpa folder lib64 = ROM tidak lengkap.
    if ctx.profile["has_lib64"]:
        findings.append(Finding(
            KEY, "arch", PASS, "64-bit architecture detected",
            "/system/lib64 or /vendor/lib64 is present.",
        ))
    else:
        findings.append(Finding(
            KEY, "arch", WARN, "No lib64 folder",
            "Treating the device as 32-bit. If this is a 64-bit ROM, the "
            "partitions are not fully unpacked.",
        ))

    aliases = getattr(ctx.layout, "compat_aliases", [])
    if aliases:
        findings.append(Finding(
            KEY, "compat-symlink-missing", WARN,
            f"{len(aliases)} compatibility symlink(s) missing from system",
            ", ".join(aliases) + ". Android provides these paths as symlinks to "
            "the separate partitions, and ROM files rely on them (<library> "
            "declarations, service paths in init.rc). The analysis continues "
            "as if they exist — otherwise one missing symlink turns into a "
            "dozen misleading 'file missing' findings.",
            "Make sure these symlinks survive the repack. If they are genuinely "
            "absent from the original image, every /system/<partition>/… path "
            "will fail on the device.",
        ))


    img_fstab = [s for s in getattr(ctx.layout, "fstab_sources", [])
                 if ".img:" in s]
    if ctx.layout.ramdisk_dir:
        findings.append(Finding(
            KEY, "ramdisk", PASS,
            f"First-stage ramdisk from {ctx.layout.ramdisk_source}",
        ))
    elif getattr(ctx.layout, "ramdisk_virtual", None) is not None:
        findings.append(Finding(
            KEY, "ramdisk", PASS,
            f"First-stage ramdisk read directly from {ctx.layout.ramdisk_source}",
            "Extracted in memory via Rust magiskboot — no disk unpack needed.",
        ))
    elif img_fstab:
        findings.append(Finding(
            KEY, "ramdisk", PASS,
            "First-stage ramdisk read directly from the boot image",
            ", ".join(img_fstab) + ". No unpack was needed.",
        ))
    else:
        findings.append(Finding(
            KEY, "no-ramdisk", WARN,
            "vendor_boot / boot not unpacked",
            "Without the first-stage ramdisk the fstab falls back to "
            "/vendor/etc — the mount map and kernel-module check are "
            "incomplete.",
            "Unpack vendor_boot.img (or boot.img on older devices) into this "
            "project, then run again.",
        ))

    # ── Security Patch Level & OS version coherence (boot / vbmeta vs build.prop) ──
    sys_spl = (props.get("ro.build.version.security_patch") or "").strip()
    vnd_spl = (props.get("ro.vendor.build.security_patch") or "").strip()
    boot_spl = ""
    boot_os = ""
    for bname, brep in (getattr(ctx.layout, "boot_inspections", None) or {}).items():
        if not isinstance(brep, dict):
            continue
        hdr = brep.get("header") or {}
        if not boot_spl and hdr.get("os_patch_level"):
            boot_spl = str(hdr["os_patch_level"]).strip()
        if not boot_os and hdr.get("os_version"):
            boot_os = str(hdr["os_version"]).strip()
    vb_spls = {}
    for vname, vrep in (getattr(ctx.layout, "vbmeta_reports", None) or {}).items():
        if not isinstance(vrep, dict):
            continue
        for desc in (vrep.get("descriptors") or []):
            if desc.get("kind") == "property" and desc.get("key_val"):
                k, v = desc["key_val"]
                if k.endswith(".security_patch") and v:
                    vb_spls[k] = str(v).strip()

    spl_info = {
        "system": sys_spl,
        "vendor": vnd_spl,
        "boot": boot_spl,
        "boot_os_version": boot_os,
        "vbmeta": vb_spls,
    }
    ctx.profile["security_patch"] = spl_info

    def _ym(val):
        parts = [p for p in val.split("-") if p.isdigit()]
        return f"{parts[0]}-{parts[1].zfill(2)}" if len(parts) >= 2 else ""

    sys_ym = _ym(sys_spl)
    boot_ym = _ym(boot_spl)
    if sys_ym and boot_ym:
        if sys_ym != boot_ym:
            findings.append(Finding(
                KEY, "spl-mismatch", WARN,
                f"Security patch drift: system={sys_spl} vs boot={boot_spl}",
                "boot.img header os_patch_level differs from "
                "ro.build.version.security_patch. On devices with strict "
                "Keymaster/KeyMint or Verified Boot binding, mismatched SPL "
                "between boot header and system/vendor can cause boot-time "
                "keystore or lockscreen failures.",
                "Align boot.img os_patch_level with the ROM's "
                "ro.build.version.security_patch when repacking boot.",
            ))
        else:
            findings.append(Finding(
                KEY, "spl-coherent", PASS,
                f"Security patch aligned ({sys_spl} / boot {boot_spl})",
            ))

    rel = (props.get("ro.build.version.release") or "").strip()
    rel_major = rel.split(".")[0] if rel else ""
    boot_major = boot_os.split(".")[0] if boot_os else ""
    if rel_major.isdigit() and boot_major.isdigit() and rel_major != boot_major:
        findings.append(Finding(
            KEY, "boot-os-mismatch", WARN,
            f"Boot header os_version ({boot_os}) differs from Android {rel}",
            "boot.img header advertises a different Android major version "
            "than ro.build.version.release. Keymaster/Gatekeeper uses "
            "os_version from the boot header.",
            "Update os_version in boot.img header to match Android " + rel + ".",
        ))

    return findings
