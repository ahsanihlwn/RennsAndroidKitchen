# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from core.vdev.kernel import (
    read_kernel, module_vermagics, vermagic_matches,
)
from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "kernel"
LABEL = "Kernel & modules"

_MAX_LIST = 8


def _boot_paths(ctx):

    boot = vendor_boot = ""
    for kind, path in getattr(ctx.layout, "boot_images", ()):
        if kind == "BOOT" and not boot:
            boot = path
        elif kind == "VNDRBOOT" and not vendor_boot:
            vendor_boot = path
    return boot, vendor_boot


def run(ctx):
    findings = []
    boot, vendor_boot = _boot_paths(ctx)

    if not boot and not vendor_boot:
        return [Finding(
            KEY, "no-boot-image", INFO,
            "No boot image in this project — kernel not checked",
            "boot.img / vendor_boot.img are needed to read the kernel and the "
            "first-stage modules.",
            "Put boot.img and vendor_boot.img in the project folder.",
        )]

    # ── identitas kernel ──
    kinfo = None
    if boot:
        kinfo = read_kernel(boot)
        if kinfo.error:
            findings.append(Finding(
                KEY, "kernel-unreadable", WARN,
                "Kernel could not be read from boot.img",
                kinfo.error + ". Its version is unknown, so the module "
                "vermagic check below cannot be judged.",
                "", boot,
            ))
        else:
            findings.append(Finding(
                KEY, "kernel", INFO,
                f"Kernel {kinfo.version or '(version string not found)'}",
                f"{kinfo.arch or 'arch ?'} · page {kinfo.page_size or '?'} · "
                f"{kinfo.size / 1048576:.1f} MB uncompressed"
                + (" · embedded config present" if kinfo.has_config else ""),
                "", boot,
            ))

    # ── vermagic modul ──
    if not vendor_boot:
        return findings

    from core.vdev.bootimg import read_ramdisk
    ramdisk = read_ramdisk(vendor_boot)
    if not ramdisk:
        findings.append(Finding(
            KEY, "vendor-ramdisk-unreadable", WARN,
            "vendor_boot ramdisk could not be read",
            "First-stage kernel modules could not be inspected.",
            "", vendor_boot,
        ))
        return findings

    vermagics = module_vermagics(ramdisk)
    if not vermagics:
        findings.append(Finding(
            KEY, "no-modules", INFO,
            "No kernel modules found in the vendor_boot ramdisk",
            "This device either has no loadable first-stage modules, or they "
            "ship elsewhere. Nothing to match.",
        ))
        return findings

    total = sum(len(v["mods"]) for v in vermagics.values())
    if not kinfo or not kinfo.vermagic:
        findings.append(Finding(
            KEY, "vermagic-unknown", INFO,
            f"{total} kernel module(s) found, but the kernel vermagic is unknown",
            "Module vermagic: "
            + "; ".join(f"{k} (x{len(v['mods'])})"
                        for k, v in list(vermagics.items())[:3])
            + ". Not judged — without the kernel side there is nothing to "
            "compare against.",
        ))
        return findings

    good, bad, unknown = {}, {}, {}
    for magic, slot in vermagics.items():
        verdict = vermagic_matches(kinfo.vermagic, magic, slot["has_crcs"])
        target = unknown if verdict is None else (good if verdict else bad)
        target[magic] = slot

    if not bad:
        crc = any(v["has_crcs"] for v in vermagics.values())
        note = ("Modules carry CONFIG_MODVERSIONS symbol CRCs, so the kernel "
                "skips the version part of vermagic entirely and matches only "
                "the rest — a differing build hash is expected and fine here "
                "(this is how GKI pairs a generic kernel with vendor modules)."
                if crc else
                "vermagic matches exactly.")
        findings.append(Finding(
            KEY, "vermagic-ok", PASS,
            f"All {total} first-stage kernel modules will load",
            note,
        ))
        return findings

    bad_count = sum(len(v["mods"]) for v in bad.values())
    detail = "; ".join(
        f"{magic} (x{len(slot['mods'])}, e.g. {', '.join(slot['mods'][:3])})"
        for magic, slot in list(bad.items())[:_MAX_LIST])

    findings.append(Finding(
        KEY, "vermagic-mismatch", BLOCKER,
        f"{bad_count} of {total} kernel module(s) will be rejected",
        f"Kernel vermagic is \"{kinfo.vermagic}\", but these modules carry: "
        + detail
        + ". init_module() rejects them with ENOEXEC. These are FIRST-STAGE "
        "modules — storage drivers among them — so nothing gets mounted and "
        "the device bootloops before init can report anything."
        + (f" {sum(len(v['mods']) for v in good.values())} other module(s) "
           "do match." if good else ""),
        "Use boot.img and vendor_boot.img whose vermagic agree. Note that "
        "when modules carry MODVERSIONS CRCs only the part AFTER the version "
        "has to match, so a differing build hash alone is not the problem.",
        vendor_boot,
    ))
    return findings
