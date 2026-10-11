# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "device"
LABEL = "Device profile"

_PROP_FILES = (
    ("system", "/system/build.prop"),
    ("system", "/system/etc/build.prop"),
    ("vendor", "/vendor/build.prop"),
    ("odm", "/odm/etc/build.prop"),
    ("product", "/product/etc/build.prop"),
    ("system_ext", "/system_ext/etc/build.prop"),
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


def _parse_props(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        # build.prop boleh punya key kembar (umum di MTK) — yang pertama menang,
        # sama seperti perilaku property_service.
        out.setdefault(k.strip(), v.strip())
    return out


def run(ctx):
    findings = []
    vfs = ctx.vfs
    props = {}
    sources = []


    candidates = [vp for _p, vp in _PROP_FILES]
    for m in vfs.mounts:
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
        for k, v in _parse_props(text).items():
            props.setdefault(k, v)

    ctx.profile["props"] = props
    ctx.profile["prop_sources"] = sources
    ctx.profile["sar"] = ctx.layout.sar
    ctx.profile["partitions"] = sorted(ctx.layout.partitions)
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

    return findings
