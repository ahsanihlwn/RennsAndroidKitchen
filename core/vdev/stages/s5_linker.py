# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import posixpath

from core.vdev.elfdeps import read_needed as _read_needed, backend_name
from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "linker"
LABEL = "Linker & library"

_MAX_BINARIES = 1200
_MAX_LIST = 10

# Akar folder library per partisi. Ditelusuri rekursif (hw/, egl/, vndk-sp/, …).
_LIB_ROOTS = (
    "/system/lib64", "/system/lib",
    "/system_ext/lib64", "/system_ext/lib",
    "/product/lib64", "/product/lib",
    "/vendor/lib64", "/vendor/lib",
    "/odm/lib64", "/odm/lib",
)

_SYSTEM_SIDE = ("system", "system_ext", "product")
_VENDOR_SIDE = ("vendor", "odm")


_PUBLIC_LIST_DIRS = (
    ("/system/etc", "llndk.libraries"),
    ("/system/etc", "public.libraries"),
    ("/system/etc", "vndksp.libraries"),
    ("/vendor/etc", "public.libraries"),
    ("/odm/etc", "public.libraries"),
)


def _public_libs(vfs):

    out = set()
    for vdir, prefix in _PUBLIC_LIST_DIRS:
        if not vfs.is_dir(vdir):
            continue
        for name in vfs.listdir(vdir):
            if not name.startswith(prefix) or not name.endswith(".txt"):
                continue
            text = vfs.read_text(posixpath.join(vdir, name), 65536)
            if not text:
                continue
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                # Format public.libraries boleh "soname [arch]" — ambil kolom 1.
                if line.endswith(".so"):
                    out.add(line)
                elif line:
                    first = line.split()[0]
                    if first.endswith(".so"):
                        out.add(first)
    return out


def _side_of(vpath):
    part = vpath.strip("/").split("/", 1)[0]
    if part in _VENDOR_SIDE:
        return "vendor"
    return "system"


def _build_lib_index(ctx):

    index = {}
    for root in _LIB_ROOTS:
        if not ctx.vfs.is_dir(root):
            continue
        for vp in ctx.vfs.walk_files(root, limit=40000):
            if vp.endswith(".so"):
                index.setdefault(posixpath.basename(vp), []).append(vp)
    return index


def _search_dirs(ctx, side):

    bits = "64" if ctx.is64 else ""
    vndk = ctx.prop("ro.vndk.version")
    dirs = []
    if side == "vendor":
        dirs += [f"/vendor/lib{bits}", f"/vendor/lib{bits}/hw",
                 f"/vendor/lib{bits}/egl", f"/vendor/lib{bits}/vndk-sp",
                 f"/odm/lib{bits}", f"/odm/lib{bits}/hw"]
        if vndk:
            dirs += [f"/system/lib{bits}/vndk-{vndk}",
                     f"/system/lib{bits}/vndk-sp-{vndk}",
                     f"/apex/com.android.vndk.v{vndk}/lib{bits}"]
    else:
        dirs += [f"/system/lib{bits}", f"/system/lib{bits}/hw",
                 f"/system_ext/lib{bits}", f"/system_ext/lib{bits}/hw",
                 f"/product/lib{bits}"]
    if bits:
        dirs += [d.replace("lib64", "lib") for d in list(dirs)]
    return dirs


def _seed_binaries(ctx):

    model = ctx.shared.get("init", {}).get("model")
    services = model.started_services if model is not None else []

    seeds = []
    seen = set()
    for s in services:
        p = s.path
        if not p.startswith("/") or p.startswith("/apex/") or p in seen:
            continue
        node = ctx.vfs.lookup(p)
        if node is not None and node.is_file:
            seen.add(p)
            seeds.append(p)
    return seeds[:_MAX_BINARIES]


def _unstarted_binaries(ctx, limit=40):

    model = ctx.shared.get("init", {}).get("model")
    started = {s.path for s in (model.started_services if model is not None else [])}
    out = []
    for hwdir in ("/vendor/bin/hw", "/system/bin/hw", "/odm/bin/hw"):
        if not ctx.vfs.is_dir(hwdir):
            continue
        for vp in ctx.vfs.glob(hwdir):
            if vp not in started:
                out.append(vp)
                if len(out) >= limit:
                    return out
    return out


def run(ctx):
    findings = []
    vfs = ctx.vfs

    if not ctx.deep_linker:
        return [Finding(KEY, "skipped", INFO, "Linker scan disabled in options")]

    seeds = _seed_binaries(ctx)
    if not seeds:
        return [Finding(
            KEY, "no-binaries", INFO,
            "No service/HAL binary to walk",
            "The init stage found no service with a real binary, and there is "
            "no bin/hw folder. Library analysis skipped.",
        )]

    ctx.note("Indexing libraries…")
    lib_index = _build_lib_index(ctx)
    apex = ctx.apex
    # Daftar publik bisa datang dari /system/etc ATAU dari dalam VNDK apex —
    # tergantung versi Android. Gabungkan keduanya.
    public = _public_libs(vfs) | apex.public_libs

    bits = "64" if ctx.is64 else ""
    cross_dirs = [f"/system/lib{bits}", "/system/lib"]

    # ── VNDK ──
    vndk = ctx.prop("ro.vndk.version")
    if vndk and not ctx.has_system:
        findings.append(Finding(
            KEY, "vndk-unknown", INFO,
            f"Vendor asks for VNDK {vndk} — availability cannot be checked",
            "VNDK is provided by the system partition, and system is not in "
            "this project.",
            "Unpack system.img into this project to check the VNDK match.",
        ))
    elif vndk:
        bits = "64" if ctx.is64 else ""
        vndk_dirs = [f"/system/lib{bits}/vndk-{vndk}",
                     f"/apex/com.android.vndk.v{vndk}/lib{bits}"]


        vndk_apex = f"com.android.vndk.v{vndk}"
        from_apex = bool(apex.files.get(vndk_apex))
        if any(vfs.is_dir(d) for d in vndk_dirs) or from_apex:
            where = (f"shipped as {vndk_apex}.apex" if from_apex
                     else "present in system")
            findings.append(Finding(
                KEY, "vndk-ok", PASS,
                f"VNDK {vndk} is available in system", where,
            ))
        elif not apex.available:
            # Indeks APEX gagal dibangun — tidak boleh menuduh berdasar data
            # yang tidak lengkap.
            findings.append(Finding(
                KEY, "vndk-unknown", INFO,
                f"Vendor asks for VNDK {vndk} — availability cannot be checked",
                "The APEX index could not be built, and modern VNDK ships "
                "inside an APEX.",
                "Check that the APEX files in /system/apex are readable.",
            ))
        else:
            findings.append(Finding(
                KEY, "vndk-missing", BLOCKER,
                f"Vendor asks for VNDK {vndk}, absent from system",
                f"ro.vndk.version={vndk} but neither {' nor '.join(vndk_dirs)} "
                "exists. Every vendor HAL that depends on VNDK fails to start.",
                f"Use a system that ships VNDK {vndk}, or a vendor matching "
                "the VNDK that is available.",
            ))

    ctx.note(f"Walking {len(seeds)} binaries…")

    unresolved = {}       # soname -> set(binary peminta langsung)
    unresolved_origins = {}  # soname -> set(binary AWAL yang rantainya putus)
    misplaced = {}        # soname -> (lokasi asli, set peminta)
    cross_unknown = set() # lintas partisi tapi daftar LL-NDK tidak diketahui
    scanned = 0
    not_elf = 0


    pending = [(p, _side_of(p), p) for p in seeds]
    origin_of = {p: p for p in seeds}
    seen_lib = set()

    while pending:
        vpath, side, origin = pending.pop()
        ref = vfs.source_ref(vpath)
        if ref is None:
            continue
        needed = _read_needed(ref)
        if needed is None:
            not_elf += 1
            continue
        scanned += 1
        if scanned > _MAX_BINARIES:
            break

        dirs = _search_dirs(ctx, side)
        for soname in needed:
            hit = None
            for d in dirs:
                cand = posixpath.join(d, soname)
                if vfs.is_file(cand):
                    hit = cand
                    break
            if hit is None and apex.provides(soname):
                continue                       # disediakan APEX — sah


            if hit is None and side == "vendor" and soname in public:
                for d in cross_dirs:
                    cand = posixpath.join(d, soname)
                    if vfs.is_file(cand):
                        hit = cand
                        break

            if hit is None:
                where = lib_index.get(soname)
                if where:


                    if not public and where[0].startswith("/system/"):
                        cross_unknown.add(soname)
                    else:
                        entry = misplaced.setdefault(soname, [where[0], set()])
                        entry[1].add(vpath)
                else:
                    unresolved.setdefault(soname, set()).add(vpath)
                    unresolved_origins.setdefault(soname, set()).add(origin)
                continue


            next_side = _side_of(hit)
            key = (hit, next_side)
            if key not in seen_lib and len(seen_lib) < 8000:
                seen_lib.add(key)
                origin_of.setdefault(hit, origin)
                pending.append((hit, next_side, origin))

    unstarted = _unstarted_binaries(ctx)
    if unstarted:
        findings.append(Finding(
            KEY, "hal-unstarted", INFO,
            f"{len(unstarted)} binary in bin/hw has no init service",
            ", ".join(posixpath.basename(p) for p in unstarted[:_MAX_LIST])
            + (f" (+{len(unstarted) - _MAX_LIST} more)"
               if len(unstarted) > _MAX_LIST else "")
            + ". Nothing in init.rc starts them, so they never run — their "
            "dependencies are deliberately not walked.",
        ))

    ctx.shared["linker"] = {
        "unresolved": unresolved,
        "unresolved_origins": unresolved_origins,
        "misplaced": misplaced,
        "scanned": scanned,
        "seeds": seeds,
    }

    findings.append(Finding(
        KEY, "scanned", INFO,
        f"{scanned} ELF walked from {len(seeds)} seed binaries",
        f"{len(lib_index)} libraries indexed"
        + (f", {apex.apex_count} APEX read" if apex.available else
           ", APEX index unavailable")
        + (f", {len(public)} LL-NDK/public libraries listed" if public else
           ", no LL-NDK/public list found")
        + f", ELF reader: {backend_name()}"
        + (f", {not_elf} file(s) skipped (not ELF)" if not_elf else "")
        + ". This search model is based on partition paths + APEX + the "
        "ROM own LL-NDK list, not a full ld.config.txt namespace emulation.",
    ))

    if cross_unknown:
        findings.append(Finding(
            KEY, "cross-unknown", INFO,
            f"{len(cross_unknown)} system-side librar(ies) used by vendor binaries "
            "— not judged",
            "This ROM ships no llndk/public.libraries*.txt, so there is no "
            "basis to decide which libraries may be used across partitions. "
            "Skipped rather than accused wrongly.",
        ))

    if unresolved:
        items = []
        for so in sorted(unresolved)[:_MAX_LIST]:
            askers = sorted(unresolved[so])
            items.append(f"{so} ← {posixpath.basename(askers[0])}"
                         + (f" +{len(askers) - 1}" if len(askers) > 1 else ""))
        more = (f" (+{len(unresolved) - _MAX_LIST} more)"
                if len(unresolved) > _MAX_LIST else "")


        gaps = []
        if not ctx.has_system:
            gaps.append("the system partition is not in this project, so "
                        "system-side libraries are invisible from here")
        if not apex.available:
            gaps.append("the APEX index could not be built, so libraries "
                        "provided by APEX may be counted as missing")
        sev = WARN if gaps else BLOCKER
        findings.append(Finding(
            KEY, "lib-missing", sev,
            f"{len(unresolved)} librar(ies) not found on the search path",
            "; ".join(items) + more
            + (". The requesting binary will definitely fail to start."
               if not gaps else
               ". Severity lowered because " + "; ".join(gaps) + "."),
            "Copy the library from the source ROM into the same partition as "
            "the requesting binary."
            + ("" if not gaps else " Unpack the missing partitions and rerun "
               "for a definite answer."),
        ))

    if misplaced:
        items = []
        for so in sorted(misplaced)[:_MAX_LIST]:
            loc, askers = misplaced[so]
            items.append(f"{so} exists at {loc}, requested by "
                         f"{posixpath.basename(sorted(askers)[0])}")
        more = (f" (+{len(misplaced) - _MAX_LIST} more)"
                if len(misplaced) > _MAX_LIST else "")
        findings.append(Finding(
            KEY, "lib-misplaced", WARN,
            f"{len(misplaced)} librar(ies) exist, but outside the requester search path",
            "; ".join(items) + more + ". This is the 'but the file is right "
            "there' class of bug: the linker may only look inside its own "
            "partition paths. Because this is a path model (not a full "
            "ld.config.txt emulation), treat it as a strong hint, not a "
            "verdict.",
            "Move or copy the library into the same partition as the "
            "requesting binary, or provide it through the correct VNDK/APEX.",
        ))

    if not unresolved and not misplaced and scanned:
        findings.append(Finding(
            KEY, "lib-ok", PASS,
            f"All library dependencies from {scanned} ELF resolve",
        ))

    return findings
