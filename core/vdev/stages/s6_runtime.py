# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "runtime"
LABEL = "Runtime bring-up"
REQUIRES = ("system",)

# Komponen inti: (path, nama, fatal?)
_CORE = (
    ("/system/bin/init", "init", True),
    ("/system/bin/linkerconfig", "linkerconfig", True),
    ("/system/bin/apexd", "apexd", True),
    ("/system/bin/servicemanager", "servicemanager", True),
    ("/system/bin/surfaceflinger", "surfaceflinger", False),
    ("/system/bin/vold", "vold", False),
    ("/system/bin/netd", "netd", False),
)

# ro.zygote -> binary app_process yang wajib ada.
_ZYGOTE_BINARIES = {
    "zygote32": ("/system/bin/app_process32",),
    "zygote64": ("/system/bin/app_process64",),
    "zygote32_64": ("/system/bin/app_process32", "/system/bin/app_process64"),
    "zygote64_32": ("/system/bin/app_process64", "/system/bin/app_process32"),
}


def _provided_by_apex(ctx, name):

    apex = getattr(ctx, "apex", None)
    if apex is None or not getattr(apex, "available", False):
        return ""
    rel = f"bin/{name}"
    for pkg, paths in apex.files.items():
        if rel in paths:
            return pkg
    return ""


def run(ctx):
    findings = []
    vfs = ctx.vfs

    # ── komponen inti ──
    missing_fatal = []
    missing_soft = []
    from_apex = []
    for path, name, fatal in _CORE:
        if vfs.is_file(path):
            continue
        pkg = _provided_by_apex(ctx, name)
        if pkg:
            from_apex.append(f"{name} (in {pkg})")
            continue
        (missing_fatal if fatal else missing_soft).append(f"{name} ({path})")

    if missing_fatal:
        findings.append(Finding(
            KEY, "core-missing", BLOCKER,
            f"{len(missing_fatal)} core boot component(s) missing",
            ", ".join(missing_fatal) + ". Without these, boot goes nowhere.",
            "Copy them from a stock system of the same version.",
        ))
    else:
        detail = "init, linkerconfig, apexd and servicemanager are present."
        if from_apex:
            detail += " Shipped inside an APEX: " + ", ".join(from_apex) + "."
        findings.append(Finding(
            KEY, "core-ok", PASS, "Core boot components are complete", detail,
        ))

    if missing_soft:
        findings.append(Finding(
            KEY, "core-soft-missing", WARN,
            f"{len(missing_soft)} system daemon(s) missing",
            ", ".join(missing_soft) + ". The device can still boot, but "
            "display, storage or networking will likely misbehave.",
        ))

    # ── zygote ──
    zygote = ctx.prop("ro.zygote")
    if not zygote:
        findings.append(Finding(
            KEY, "zygote-unknown", INFO,
            "ro.zygote is unknown",
            "This property normally lives in /vendor/build.prop. Since it "
            "could not be read, the required app_process variant is not "
            "checked.",
            "Unpack vendor.img into this project for the full check.",
        ))
    else:
        needed = _ZYGOTE_BINARIES.get(zygote)
        if needed is None:
            findings.append(Finding(
                KEY, "zygote-odd", WARN,
                f"Unrecognised ro.zygote value: {zygote}",
                "Standard variants: zygote32, zygote64, zygote32_64, "
                "zygote64_32.",
            ))
        else:
            absent = [p for p in needed if not vfs.is_file(p)]
            if absent:
                findings.append(Finding(
                    KEY, "zygote-binary-missing", BLOCKER,
                    f"ro.zygote={zygote} but {', '.join(absent)} is missing",
                    "Zygote is the parent of every Java process. Without its "
                    "binary, system_server never runs and the device stops at "
                    "the boot animation.",
                    "Point ro.zygote at a binary that actually exists, or copy "
                    "the missing app_process from the source ROM.",
                ))
            else:
                findings.append(Finding(
                    KEY, "zygote-ok", PASS,
                    f"ro.zygote={zygote} matches the available binaries",
                ))

    # ── APEX ──
    apex_files = vfs.glob("/system/apex", ".apex") if vfs.is_dir("/system/apex") else []
    apex_dirs = [n for n in (vfs.listdir("/system/apex") if vfs.is_dir("/system/apex") else [])
                 if vfs.is_dir("/system/apex/" + n)]
    if apex_files or apex_dirs:
        findings.append(Finding(
            KEY, "apex", PASS,
            f"{len(apex_files) + len(apex_dirs)} APEX present in /system/apex",
        ))
    elif vfs.is_file("/system/bin/apexd"):
        findings.append(Finding(
            KEY, "apex-empty", BLOCKER,
            "apexd exists but /system/apex is empty",
            "Modern Android keeps the runtime (libc, ART, conscrypt) inside "
            "APEX. With no APEX at all, apexd fails and boot stops.",
            "Restore the contents of /system/apex from a stock system.",
        ))

    # ── bootclasspath ──
    bcp = "/system/etc/classpaths/bootclasspath.pb"
    if vfs.is_file(bcp):
        findings.append(Finding(KEY, "bootclasspath", PASS,
                                "bootclasspath.pb is present", "", "", bcp))
    elif vfs.is_dir("/system/etc/classpaths"):
        findings.append(Finding(
            KEY, "bootclasspath-missing", WARN,
            "bootclasspath.pb is missing",
            "The classpaths folder exists but its bootclasspath manifest is "
            "gone — derive_classpath fails and zygote may never start.",
            "", bcp,
        ))

    # ── framework ──
    if vfs.is_dir("/system/framework"):
        jars = [n for n in vfs.listdir("/system/framework") if n.endswith(".jar")]
        if not jars:
            findings.append(Finding(
                KEY, "framework-empty", BLOCKER,
                "/system/framework contains no .jar at all",
                "No framework.jar or services.jar — system_server has no code "
                "to run.",
                "Restore the contents of /system/framework from a stock "
                "system.",
            ))
        else:
            findings.append(Finding(
                KEY, "framework", PASS, f"{len(jars)} jar(s) in /system/framework",
            ))

    return findings
