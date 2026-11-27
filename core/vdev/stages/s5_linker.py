# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import json
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
    ("/system/etc", "vndkcore.libraries"),
    ("/system_ext/etc", "public.libraries"),
    ("/product/etc", "public.libraries"),
    ("/vendor/etc", "public.libraries"),
    ("/vendor/etc", "llndk.libraries"),
    ("/odm/etc", "public.libraries"),
)

_LINKER_CONFIG_PATHS = (
    "/system/etc/linker.config.json",
    "/system/etc/linker.config.pb",
    "/system_ext/etc/linker.config.json",
    "/system_ext/etc/linker.config.pb",
    "/product/etc/linker.config.json",
    "/product/etc/linker.config.pb",
    "/vendor/etc/linker.config.json",
    "/vendor/etc/linker.config.pb",
    "/odm/etc/linker.config.json",
    "/odm/etc/linker.config.pb",
)


def _decode_linker_config_pb(raw):
    """Minimal protobuf decoder for Android linker.config.pb (field 1 = provideLibs, field 2 = requireLibs)."""
    provides = []
    pos = 0
    n = len(raw)

    def _varint():
        nonlocal pos
        shift = 0
        val = 0
        while pos < n:
            b = raw[pos]
            pos += 1
            val |= (b & 0x7F) << shift
            if not (b & 0x80):
                return val
            shift += 7
            if shift > 63:
                return None
        return None

    while pos < n:
        tag = _varint()
        if tag is None:
            break
        field = tag >> 3
        wire = tag & 7
        if wire == 2:
            ln = _varint()
            if ln is None or pos + ln > n:
                break
            chunk = raw[pos:pos + ln]
            pos += ln
            if field == 1:
                try:
                    s = chunk.decode("utf-8", errors="ignore").strip()
                    if s.endswith(".so"):
                        provides.append(s)
                except Exception:
                    pass
        elif wire == 0:
            if _varint() is None:
                break
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            break
    return provides


def _load_linker_configs(vfs):
    """Parse Android linker.config.json & linker.config.pb files to emulate Bionic linker namespace exports."""
    system_provides = set()
    vendor_provides = set()
    configs_loaded = 0

    candidates = list(_LINKER_CONFIG_PATHS)
    if vfs.is_dir("/apex"):
        for name in vfs.listdir("/apex")[:120]:
            for fname in ("linker.config.json", "linker.config.pb"):
                p = f"/apex/{name}/etc/{fname}"
                if vfs.is_file(p):
                    candidates.append(p)

    for cfg_path in candidates:
        is_vendor = cfg_path.startswith(("/vendor/", "/odm/", "/system/vendor/", "/system/odm/"))
        target = vendor_provides if is_vendor else system_provides
        if cfg_path.endswith(".pb"):
            raw_b = vfs.read_bytes(cfg_path, 262144)
            if not raw_b:
                continue
            configs_loaded += 1
            for lib in _decode_linker_config_pb(raw_b):
                target.add(lib)
            continue
        raw = vfs.read_text(cfg_path, 262144)
        if not raw:
            continue
        try:
            doc = json.loads(raw)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        configs_loaded += 1
        provides = doc.get("provideLibs") or []
        if not isinstance(provides, list):
            continue
        for lib in provides:
            if isinstance(lib, str) and lib.strip().endswith(".so"):
                target.add(lib.strip())

    return {
        "configs_loaded": configs_loaded,
        "system_provides": system_provides,
        "vendor_provides": vendor_provides,
    }


def _expand_rpath_dirs(vpath, rpaths):
    """Expand DT_RUNPATH / DT_RPATH entries including $ORIGIN / ${ORIGIN}."""
    if not rpaths:
        return []
    origin = posixpath.dirname(vpath) or "/"
    out = []
    seen = set()
    for raw in rpaths:
        if not raw:
            continue
        for tok in str(raw).split(":"):
            tok = tok.strip()
            if not tok:
                continue
            exp = tok.replace("${ORIGIN}", origin).replace("$ORIGIN", origin)
            exp = posixpath.normpath(exp)
            if exp.startswith("/") and exp not in seen:
                seen.add(exp)
                out.append(exp)
    return out


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
    norm = vpath.strip("/")
    if norm.startswith(("system/vendor/", "system/odm/")):
        return "vendor"
    part = norm.split("/", 1)[0]
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

    ctx.note("Indexing libraries & linker namespaces…")
    lib_index = _build_lib_index(ctx)
    apex = ctx.apex
    lcfg = _load_linker_configs(vfs)
    # Daftar publik datang dari llndk/public/vndksp.libraries*.txt, linker.config.json, dan VNDK/APEX.
    public = _public_libs(vfs) | apex.public_libs | lcfg["system_provides"]
    vendor_public = set(lcfg["vendor_provides"])
    for vdir in ("/vendor/etc", "/odm/etc"):
        if not vfs.is_dir(vdir):
            continue
        for fname in vfs.listdir(vdir):
            if fname.startswith("public.libraries") and fname.endswith(".txt"):
                txt = vfs.read_text(posixpath.join(vdir, fname), 65536) or ""
                for line in txt.splitlines():
                    tok = line.split("#", 1)[0].strip().split()
                    if tok and tok[0].endswith(".so"):
                        vendor_public.add(tok[0])
    has_namespace_rules = bool(public or lcfg["configs_loaded"])

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

    elf_cache = {}
    elf_is64 = {}
    elf_rpaths = {}
    native_reader_label = ""
    _native_mod = None
    try:
        from core import native_backend
        if native_backend._get_native() is not None:
            _native_mod = native_backend
            native_reader_label = "rust goblin (wave-batch+ABI)"
    except Exception:
        _native_mod = None

    def _scan_wave(vpaths):
        if not _native_mod or not vpaths:
            return
        to_scan = [vp for vp in dict.fromkeys(vpaths) if vp not in elf_cache]
        if not to_scan:
            return
        by_host = []
        by_erofs = {}
        by_ext = {}
        for vp in to_scan:
            node = vfs.lookup(vp)
            if node is None or not node.is_file:
                elf_cache[vp] = None
                continue
            if node.host:
                by_host.append((vp, node.host))
            elif getattr(node.source, "native_handle", None) is not None:
                fs_t = node.source.fs_type
                if fs_t == "EROFS":
                    by_erofs.setdefault(id(node.source), (node.source.native_handle, []))[1].append((vp, node.rel))
                elif fs_t == "EXT":
                    by_ext.setdefault(id(node.source), (node.source.native_handle, []))[1].append((vp, node.rel))
        try:
            if by_host:
                res = _native_mod.vdev_scan_elfs([h for _, h in by_host])
                for (vp, _), rec in zip(by_host, res):
                    if rec.get("ok"):
                        elf_cache[vp] = rec.get("needed", [])
                        elf_is64[vp] = bool(rec.get("is_64"))
                        rp = (rec.get("runpath") or []) + (rec.get("rpath") or [])
                        if rp:
                            elf_rpaths[vp] = _expand_rpath_dirs(vp, rp)
                    else:
                        elf_cache[vp] = None
            for handle, items in by_erofs.values():
                res = _native_mod.vdev_scan_erofs_elfs(handle, [rel for _, rel in items])
                for (vp, _), rec in zip(items, res):
                    if rec.get("ok"):
                        elf_cache[vp] = rec.get("needed", [])
                        elf_is64[vp] = bool(rec.get("is_64"))
                        rp = (rec.get("runpath") or []) + (rec.get("rpath") or [])
                        if rp:
                            elf_rpaths[vp] = _expand_rpath_dirs(vp, rp)
                    else:
                        elf_cache[vp] = None
            for handle, items in by_ext.values():
                res = _native_mod.vdev_scan_ext_elfs(handle, [rel for _, rel in items])
                for (vp, _), rec in zip(items, res):
                    if rec.get("ok"):
                        elf_cache[vp] = rec.get("needed", [])
                        elf_is64[vp] = bool(rec.get("is_64"))
                        rp = (rec.get("runpath") or []) + (rec.get("rpath") or [])
                        if rp:
                            elf_rpaths[vp] = _expand_rpath_dirs(vp, rp)
                    else:
                        elf_cache[vp] = None
        except Exception:
            pass

    unresolved = {}       # soname -> set(binary peminta langsung)
    unresolved_origins = {}  # soname -> set(binary AWAL yang rantainya putus)
    misplaced = {}        # soname -> (lokasi asli, set peminta)
    ns_violations = {}    # soname -> [lokasi asli, set(peminta), direction]
    abi_mismatch = {}     # soname -> (lokasi asli, set peminta)
    cross_unknown = set() # lintas partisi tapi daftar LL-NDK tidak diketahui
    rpath_hits = 0
    scanned = 0
    not_elf = 0

    # Pre-build O(1) path set from lib_index to avoid repeated vfs.is_file lookups
    lib_path_set = {vp for paths in lib_index.values() for vp in paths}

    def _abi_dirs(side, is64):
        all_d = _search_dirs(ctx, side)
        if is64 is True:
            return [d for d in all_d if "/lib64" in d]
        if is64 is False:
            return [d for d in all_d if "/lib64" not in d]
        return all_d

    dir_cache = {
        (s, b): _abi_dirs(s, b)
        for s in ("system", "vendor")
        for b in (True, False, None)
    }
    cross_by_abi = {
        True: ["/system/lib64"],
        False: ["/system/lib"],
        None: cross_dirs,
    }

    wave = [(p, _side_of(p), p) for p in seeds]
    origin_of = {p: p for p in seeds}
    seen_lib = set()

    while wave and scanned < _MAX_BINARIES:
        _scan_wave([vp for vp, _, _ in wave])
        next_wave = []
        for vpath, side, origin in wave:
            if scanned >= _MAX_BINARIES:
                break
            if vpath in elf_cache:
                needed = elf_cache[vpath]
            else:
                ref = vfs.source_ref(vpath)
                if ref is None:
                    continue
                needed = _read_needed(ref)
            if needed is None:
                not_elf += 1
                continue
            scanned += 1

            want64 = elf_is64.get(vpath)
            dirs = dir_cache[(side, want64)]
            rdirs = elf_rpaths.get(vpath)
            for soname in needed:
                hit = None
                if rdirs:
                    for d in rdirs:
                        cand = posixpath.join(d, soname)
                        if cand in lib_path_set or vfs.is_file(cand):
                            if want64 is not None and cand in elf_is64 and elf_is64[cand] != want64:
                                continue
                            hit = cand
                            rpath_hits += 1
                            break
                if hit is None:
                    for d in dirs:
                        cand = posixpath.join(d, soname)
                        if cand in lib_path_set or vfs.is_file(cand):
                            if want64 is not None and cand in elf_is64 and elf_is64[cand] != want64:
                                abi_mismatch.setdefault(soname, [cand, set()])[1].add(vpath)
                                continue
                            hit = cand
                            break
                if hit is None and apex.provides(soname):
                    continue                       # disediakan APEX — sah

                if hit is None and side == "vendor" and soname in public:
                    for d in cross_by_abi[want64]:
                        cand = posixpath.join(d, soname)
                        if cand in lib_path_set or vfs.is_file(cand):
                            hit = cand
                            break

                if hit is None and side == "system" and soname in vendor_public:
                    for d in dir_cache[("vendor", want64)]:
                        cand = posixpath.join(d, soname)
                        if cand in lib_path_set or vfs.is_file(cand):
                            hit = cand
                            break

                if hit is None:
                    where = lib_index.get(soname)
                    if where:
                        same_abi = [w for w in where if want64 is None or elf_is64.get(w, want64) == want64]
                        loc = same_abi[0] if same_abi else where[0]
                        loc_side = _side_of(loc)
                        if not same_abi and want64 is not None:
                            abi_mismatch.setdefault(soname, [where[0], set()])[1].add(vpath)
                        elif not has_namespace_rules and loc_side != side:
                            cross_unknown.add(soname)
                        elif has_namespace_rules and loc_side != side:
                            direction = f"{side}->{loc_side}"
                            entry = ns_violations.setdefault(soname, [loc, set(), direction])
                            entry[1].add(vpath)
                        else:
                            entry = misplaced.setdefault(soname, [loc, set()])
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
                    next_wave.append((hit, next_side, origin))
        wave = next_wave

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

    # ── Native C++ .dynsym Symbol Resolution Audit for Boot-Critical Daemons ──
    daemon_sym_audit = _audit_critical_daemon_symbols(
        ctx, seeds, lib_index, lib_path_set, public, apex,
        elf_cache, elf_is64, dir_cache, cross_by_abi, _native_mod, findings,
    )

    ctx.shared["linker"] = {
        "unresolved": unresolved,
        "unresolved_origins": unresolved_origins,
        "misplaced": misplaced,
        "ns_violations": ns_violations,
        "abi_mismatch": abi_mismatch,
        "daemon_symbol_audit": daemon_sym_audit,
        "linker_configs": lcfg["configs_loaded"],
        "rpath_hits": rpath_hits,
        "scanned": scanned,
        "seeds": seeds,
    }

    findings.append(Finding(
        KEY, "scanned", INFO,
        f"{scanned} ELF walked from {len(seeds)} seed binaries",
        f"{len(lib_index)} libraries indexed"
        + (f", {apex.apex_count} APEX read" if apex.available else
           ", APEX index unavailable")
        + (f", {len(public)} LL-NDK/public/provideLibs listed" if public else
           ", no LL-NDK/public list found")
        + (f", {lcfg['configs_loaded']} linker.config.json parsed" if lcfg["configs_loaded"] else "")
        + (f", {rpath_hits} DT_RUNPATH hit(s)" if rpath_hits else "")
        + f", ELF reader: {native_reader_label or backend_name()}"
        + (f", {not_elf} file(s) skipped (not ELF)" if not_elf else "")
        + ".",
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

    if ns_violations:
        crit_ns = []
        warn_ns = []
        for so in sorted(ns_violations):
            loc, askers, direction = ns_violations[so]
            sample_asker = sorted(askers)[0]
            orig_names = {posixpath.basename(origin_of.get(a, a)) for a in askers}
            is_crit = any(
                (n in _CRITICAL_DAEMON_NAMES)
                or any(sub in n for sub in _CRITICAL_HAL_SUBSTRS)
                for n in orig_names
            )
            desc = (
                f"{so} ({direction}, exists at {loc}, requested by "
                f"{posixpath.basename(sample_asker)}"
                + (f" +{len(askers) - 1}" if len(askers) > 1 else "")
                + ")"
            )
            (crit_ns if is_crit else warn_ns).append(desc)
        if crit_ns:
            more = f" (+{len(crit_ns) - _MAX_LIST} more)" if len(crit_ns) > _MAX_LIST else ""
            findings.append(Finding(
                KEY, "linker-namespace-violation", BLOCKER,
                f"{len(crit_ns)} critical Bionic linker namespace violation(s) across Treble boundary",
                "; ".join(crit_ns[:_MAX_LIST]) + more
                + ". Bionic blocks cross-partition loading of private libraries that are not in "
                "LL-NDK, VNDK-SP, public.libraries, or linker.config.json provideLibs.",
                "Ship a vendor copy of the library in /vendor/lib64, or export it via LL-NDK / linker.config.json.",
            ))
        if warn_ns:
            more = f" (+{len(warn_ns) - _MAX_LIST} more)" if len(warn_ns) > _MAX_LIST else ""
            findings.append(Finding(
                KEY, "linker-namespace-violation", WARN,
                f"{len(warn_ns)} cross-partition Bionic linker namespace violation(s)",
                "; ".join(warn_ns[:_MAX_LIST]) + more
                + ". Not listed in LL-NDK / public.libraries / linker.config.json provideLibs.",
                "Provide the library inside the requester's own partition or verify linker.config.json exports.",
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
            f"{len(misplaced)} librar(ies) exist in a subdirectory outside the default search path",
            "; ".join(items) + more + ". Library is on the same partition side but in a subdirectory not covered by DT_RUNPATH or default linker search paths.",
            "Move the library to the standard lib64/lib directory or set DT_RUNPATH ($ORIGIN).",
        ))

    if abi_mismatch:
        items = []
        for so in sorted(abi_mismatch)[:_MAX_LIST]:
            loc, askers = abi_mismatch[so]
            items.append(f"{so} (only at {loc}, requested by {posixpath.basename(sorted(askers)[0])})")
        findings.append(Finding(
            KEY, "abi-mismatch", BLOCKER,
            f"{len(abi_mismatch)} librar(ies) exist only in the wrong ABI (32-bit vs 64-bit)",
            "; ".join(items) + ". A 64-bit ELF cannot load a 32-bit .so or vice versa.",
            "Install the matching 64-bit/32-bit variant of the library into lib64/ or lib/.",
        ))

    if not unresolved and not ns_violations and not misplaced and not abi_mismatch and scanned:
        findings.append(Finding(
            KEY, "lib-ok", PASS,
            f"All library dependencies & namespaces from {scanned} ELF resolve",
        ))

    return findings


# ── Native C++ .dynsym Symbol Audit for Boot-Critical Daemons ──

_CRITICAL_DAEMON_NAMES = {
    "surfaceflinger", "app_process64", "app_process32", "bootanimation",
    "servicemanager", "hwservicemanager", "vndservicemanager",
    "vold", "netd", "keystore2", "installd", "lmkd", "ueventd", "logd",
}
_SUBSYSTEM_DAEMON_NAMES = {
    "audioserver", "cameraserver", "mediaserver", "media.extractor",
    "drmserver", "storaged", "wificond", "tombstoned", "adbd", "rild", "mtkrild",
}
_CRITICAL_HAL_SUBSTRS = (
    "graphics.composer", "hwcomposer", "graphics.allocator", "gralloc",
    "keymint", "keymaster", "gatekeeper",
)
_SUBSYSTEM_HAL_SUBSTRS = (
    "audio", "camera", "power", "light", "health", "thermal", "sensor",
    "bluetooth", "wifi", "radio", "ril", "nfc", "biometric", "fingerprint",
    "face", "vibrator", "drm", "usb", "boot", "media",
)

# Symbols provided implicitly by bionic linker / dynamic loader / compiler runtime
_LINKER_BUILTIN_SYMS = {
    "_DYNAMIC", "__bss_start", "_end", "_edata", "__dso_handle",
    "__stack_chk_guard", "environ", "__progname", "optarg", "optind",
    "dlopen", "dlsym", "dlclose", "dlerror", "dladdr", "dlvsym",
    "dl_iterate_phdr", "android_dlopen_ext", "android_get_LD_LIBRARY_PATH",
    "android_update_LD_LIBRARY_PATH", "android_set_application_target_sdk_version",
    "android_get_application_target_sdk_version", "android_init_anonymous_namespace",
    "android_create_namespace", "android_link_namespaces", "android_link_namespaces_all_libs",
    "android_get_exported_namespace",
}
_LINKER_BUILTIN_PREFIXES = (
    "__loader_", "loader_", "_Unwind_", "__aeabi_", "__gnu_", "__cxa_",
    "__gxx_", "__asan_", "__hwasan_", "__ubsan_", "__cfi_", "android_dl",
)


def _classify_daemon_tier(vpath: str) -> str:
    base = posixpath.basename(vpath)
    low = base.lower()
    if base in _CRITICAL_DAEMON_NAMES:
        return "critical"
    if "/bin/hw/" in vpath and any(k in low for k in _CRITICAL_HAL_SUBSTRS):
        return "critical"
    if base in _SUBSYSTEM_DAEMON_NAMES:
        return "subsystem"
    if "/bin/hw/" in vpath and any(k in low for k in _SUBSYSTEM_HAL_SUBSTRS):
        return "subsystem"
    if "/bin/hw/" in vpath:
        return "subsystem"
    return ""


def _build_apex_so_vpaths(apex):
    """Map (soname, is_64) -> list of /apex/<pkg>/<rel> paths from indexed APEXes."""
    out = {}
    if not apex or not getattr(apex, "available", False):
        return out
    for pkg, rels in (getattr(apex, "files", None) or {}).items():
        for rel in rels:
            if not rel.endswith(".so"):
                continue
            is64 = ("lib64/" in rel or rel.startswith("lib64/"))
            is32 = ("lib/" in rel or rel.startswith("lib/")) and not is64
            if not is64 and not is32:
                continue
            soname = posixpath.basename(rel)
            vp = f"/apex/{pkg}/{rel.lstrip('/')}"
            out.setdefault((soname, is64), []).append(vp)
    return out


def _batch_scan_symbols(vfs, vpaths, native_mod):
    """Batch-read ELF .dynsym (needed, defined_syms, undef_syms, is_64) for VFS paths including APEX."""
    if not native_mod or not vpaths:
        return {}
    by_host = []
    by_erofs = {}
    by_ext = {}
    by_bytes = []

    for vp in dict.fromkeys(vpaths):
        node = vfs.lookup(vp)
        if node is None or not node.is_file:
            continue
        if node.host:
            by_host.append((vp, node.host))
        elif getattr(node.source, "native_handle", None) is not None:
            fs_t = node.source.fs_type
            if fs_t == "EROFS":
                by_erofs.setdefault(id(node.source), (node.source.native_handle, []))[1].append((vp, node.rel))
            elif fs_t == "EXT":
                by_ext.setdefault(id(node.source), (node.source.native_handle, []))[1].append((vp, node.rel))
            else:
                raw = vfs.read_bytes(vp)
                if raw:
                    by_bytes.append((vp, raw))
        else:
            raw = vfs.read_bytes(vp)
            if raw:
                by_bytes.append((vp, raw))

    out = {}

    def _store(vp, rec):
        if rec.get("ok"):
            out[vp] = {
                "is_64": bool(rec.get("is_64")),
                "needed": list(rec.get("needed") or ()),
                "defined": set(rec.get("defined_syms") or ()),
                "undef": list(rec.get("undef_syms") or ()),
            }

    try:
        if by_host:
            for (vp, _), rec in zip(by_host, native_mod.vdev_scan_elfs([h for _, h in by_host], with_symbols=True)):
                _store(vp, rec)
        for handle, items in by_erofs.values():
            for (vp, _), rec in zip(items, native_mod.vdev_scan_erofs_elfs(handle, [r for _, r in items], with_symbols=True)):
                _store(vp, rec)
        for handle, items in by_ext.values():
            for (vp, _), rec in zip(items, native_mod.vdev_scan_ext_elfs(handle, [r for _, r in items], with_symbols=True)):
                _store(vp, rec)
        if by_bytes and hasattr(native_mod, "vdev_scan_elf_bytes"):
            try:
                for rec in native_mod.vdev_scan_elf_bytes(by_bytes, with_symbols=True):
                    _store(rec.get("path", ""), rec)
            except Exception:
                pass
    except Exception:
        pass
    return out


def _audit_critical_daemon_symbols(
    ctx, seeds, lib_index, lib_path_set, public, apex,
    elf_cache, elf_is64, dir_cache, cross_by_abi, native_mod, findings,
):
    """Resolve strong undefined C++/C symbols (.dynsym) for boot-critical daemons
    against their transitive DT_NEEDED library closure (including APEX bionic/ART/VNDK)."""
    if not native_mod:
        return []
    vfs = ctx.vfs

    # Collect target daemons from started seeds + canonical core paths
    targets = []
    seen_t = set()
    for p in list(seeds) + [
        "/system/bin/surfaceflinger", "/system/bin/app_process64", "/system/bin/app_process32",
        "/system/bin/bootanimation", "/system/bin/servicemanager", "/system/bin/vold",
        "/system/bin/netd", "/system/bin/keystore2", "/system/bin/logd",
        "/system/bin/audioserver", "/system/bin/cameraserver", "/system/bin/mediaserver",
    ]:
        if p in seen_t:
            continue
        tier = _classify_daemon_tier(p)
        if tier and vfs.is_file(p):
            seen_t.add(p)
            targets.append((p, tier))
    if not targets:
        return []

    ctx.note(f"Auditing C++ symbols on {len(targets)} critical daemon(s)…")
    apex_so_map = _build_apex_so_vpaths(apex)

    # Prefer real runtime bionic .so inside APEX over stub .so in /system/lib64
    _BIONIC_APEX_PREF = {"libc.so", "libm.so", "libdl.so", "libdl_android.so"}

    def _resolve_dep_paths(soname, side, is64):
        paths = []
        if soname in _BIONIC_APEX_PREF:
            for ap in apex_so_map.get((soname, bool(is64)), ()):
                if ap not in paths:
                    paths.append(ap)
        dirs = dir_cache.get((side, is64)) or dir_cache.get((side, None)) or ()
        for d in dirs:
            cand = posixpath.join(d, soname)
            if cand in lib_path_set or vfs.is_file(cand):
                if cand not in paths:
                    paths.append(cand)
                break
        if not paths and side == "vendor" and soname in public:
            for d in cross_by_abi.get(is64, ()):
                cand = posixpath.join(d, soname)
                if cand in lib_path_set or vfs.is_file(cand):
                    paths.append(cand)
                    break
        if not paths:
            for ap in apex_so_map.get((soname, bool(is64)), ()):
                if ap not in paths:
                    paths.append(ap)
        # Fallback: also union APEX copy if system copy might be a stub
        if soname in _BIONIC_APEX_PREF and not any(p.startswith("/apex/") for p in paths):
            for ap in apex_so_map.get((soname, bool(is64)), ()):
                paths.append(ap)
        return paths

    # Discover transitive closure of all ELFs needed by target daemons
    sym_cache = {}
    all_to_scan = [p for p, _ in targets]
    sym_cache.update(_batch_scan_symbols(vfs, all_to_scan, native_mod))

    daemon_closures = {}  # daemon_vp -> (tier, set(dep_vpaths), complete: bool, missing_deps: list)
    pending_libs = set()
    for dvp, tier in targets:
        rec = sym_cache.get(dvp)
        if not rec:
            continue
        is64 = rec["is_64"]
        side = _side_of(dvp)
        dep_vps = set()
        missing_deps = []
        for so in rec["needed"]:
            hits = _resolve_dep_paths(so, side, is64)
            if hits:
                dep_vps.update(hits)
                pending_libs.update(hits)
            else:
                missing_deps.append(so)
        daemon_closures[dvp] = [tier, dep_vps, missing_deps, is64]

    # Wave-expand transitive DT_NEEDED up to 3 hops (covers libgui -> libui -> libbinder -> libc++/libc)
    frontier = [p for p in pending_libs if p not in sym_cache]
    for _hop in range(3):
        if not frontier:
            break
        scanned_wave = _batch_scan_symbols(vfs, frontier[:600], native_mod)
        sym_cache.update(scanned_wave)
        next_frontier = set()
        for lv in scanned_wave:
            lrec = sym_cache[lv]
            lside = _side_of(lv)
            l64 = lrec["is_64"]
            for so in lrec["needed"]:
                for h in _resolve_dep_paths(so, lside, l64):
                    if h not in sym_cache:
                        next_frontier.add(h)
        frontier = list(next_frontier)

    # Evaluate each daemon's strong undefined symbols against its full transitive DT_NEEDED defined_syms
    audit_results = []
    crit_broken = []
    sub_broken = []

    for dvp, (tier, direct_deps, missing_deps, is64) in daemon_closures.items():
        drec = sym_cache.get(dvp)
        if not drec or not drec["undef"]:
            continue
        # Collect transitive closure of scanned libs for this daemon
        closure = set()
        stack = list(direct_deps)
        complete = (len(missing_deps) == 0)
        while stack and len(closure) < 250:
            cur = stack.pop()
            if cur in closure:
                continue
            crec = sym_cache.get(cur)
            if crec is None:
                complete = False
                continue
            closure.add(cur)
            cside = _side_of(cur)
            for so in crec["needed"]:
                hits = _resolve_dep_paths(so, cside, is64)
                if not hits:
                    complete = False
                    continue
                for h in hits:
                    if h not in closure:
                        stack.append(h)

        # Require complete closure before accusing missing symbols (zero false positives when APEX/libs absent!)
        if not complete:
            audit_results.append({
                "binary": dvp,
                "tier": tier,
                "libs_scanned": len(closure),
                "complete": False,
                "missing_symbols": [],
            })
            continue

        defined_union = set(drec["defined"])
        for lv in closure:
            defined_union.update(sym_cache[lv]["defined"])

        missing_syms = []
        for sym in drec["undef"]:
            if sym in defined_union or sym in _LINKER_BUILTIN_SYMS:
                continue
            if any(sym.startswith(pfx) for pfx in _LINKER_BUILTIN_PREFIXES):
                continue
            missing_syms.append(sym)

        entry = {
            "binary": dvp,
            "tier": tier,
            "libs_scanned": len(closure),
            "complete": True,
            "missing_symbols": missing_syms[:30],
            "missing_count": len(missing_syms),
        }
        audit_results.append(entry)
        if missing_syms:
            if tier == "critical":
                crit_broken.append(entry)
            else:
                sub_broken.append(entry)

    complete_cnt = sum(1 for r in audit_results if r["complete"])
    if crit_broken:
        sample = "; ".join(
            f"{posixpath.basename(r['binary'])} missing {r['missing_count']} symbol(s) ({', '.join(r['missing_symbols'][:3])})"
            for r in crit_broken[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "elf-symbol-missing-critical", BLOCKER,
            f"{len(crit_broken)} boot-critical daemon(s) have unresolved C++/C dynamic symbols",
            sample + ". Bionic linker fails at execve() with CANNOT LINK EXECUTABLE: cannot locate symbol — "
            "typical of mismatched libgui/libui/libbinder/HWC/VNDK ABI across Android versions.",
            "Align the daemon and its shared libraries (libgui, libui, libbinder, VNDK) to the same ABI build.",
            crit_broken[0]["binary"],
        ))
    if sub_broken:
        sample = "; ".join(
            f"{posixpath.basename(r['binary'])} missing {r['missing_count']} symbol(s) ({', '.join(r['missing_symbols'][:3])})"
            for r in sub_broken[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "elf-symbol-missing", WARN,
            f"{len(sub_broken)} subsystem daemon(s) have unresolved C++/C dynamic symbols",
            sample + ". Audio/Camera/Media/HAL daemon fails dynamic linking with cannot locate symbol.",
            "Match the subsystem daemon binary with the vendor/system libraries from the same ROM base.",
            sub_broken[0]["binary"],
        ))
    if complete_cnt and not crit_broken and not sub_broken:
        findings.append(Finding(
            KEY, "elf-symbols-ok", PASS,
            f"All C++/C dynamic symbols resolved across {complete_cnt} critical daemon(s)",
            ", ".join(posixpath.basename(r["binary"]) for r in audit_results if r["complete"])[:200],
        ))
    return audit_results
