# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import posixpath
import re

from core.vdev.kernel import (
    read_kernel, module_vermagics, vermagic_matches,
    extract_kmi_release, kmi_matches_kernel,
)
from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "kernel"
LABEL = "Kernel & modules"

_MAX_LIST = 8

_DLKM_ROOTS = (
    "/vendor_dlkm/lib/modules",
    "/odm_dlkm/lib/modules",
    "/system_dlkm/lib/modules",
    "/vendor/lib/modules",
)


def _norm_mod_name(name):
    base = posixpath.basename(str(name or "").strip())
    if base.endswith(".ko"):
        base = base[:-3]
    return base.replace("-", "_").lower()


def _boot_paths(ctx):

    boot = vendor_boot = ""
    for kind, path in getattr(ctx.layout, "boot_images", ()):
        if kind == "BOOT" and not boot:
            boot = path
        elif kind == "VNDRBOOT" and not vendor_boot:
            vendor_boot = path
    return boot, vendor_boot


def _scan_dlkm_kos(ctx, k_vm, findings, *, kernel_arch: str = ""):
    """Verify .ko vermagic, modules.load & modules.dep across DLKM/vendor module dirs via Rust."""
    vfs = ctx.vfs
    ko_paths = []
    ko_basenames_by_dir = {}
    all_ko_basenames = set()
    load_missing = []
    dep_missing = []
    seen_vp = set()

    for base in _DLKM_ROOTS:
        if not vfs.is_dir(base):
            continue
        files_under = list(vfs.walk_files(base, limit=6000))
        local_kos = set()
        load_files = []
        dep_files = []
        for vp in files_under:
            bn = posixpath.basename(vp)
            if bn.endswith(".ko"):
                if vp not in seen_vp:
                    seen_vp.add(vp)
                    ko_paths.append(vp)
                local_kos.add(bn)
                all_ko_basenames.add(bn)
                ko_basenames_by_dir.setdefault(posixpath.dirname(vp), set()).add(bn)
            elif bn == "modules.load" or bn.startswith("modules.load."):
                load_files.append(vp)
            elif bn == "modules.dep":
                dep_files.append(vp)

        for lf in load_files:
            ltext = vfs.read_text(lf, 262144) or ""
            ldir = posixpath.dirname(lf)
            dir_kos = ko_basenames_by_dir.get(ldir, local_kos)
            for raw in ltext.splitlines():
                line = raw.split("#", 1)[0].strip()
                if not line:
                    continue
                bn = posixpath.basename(line)
                if bn not in dir_kos and bn not in local_kos:
                    load_missing.append(f"{bn} (listed in {lf})")

        for df in dep_files:
            dtext = vfs.read_text(df, 512 * 1024) or ""
            ddir = posixpath.dirname(df)
            for line in dtext.splitlines():
                if ":" not in line:
                    continue
                lhs, rhs = line.split(":", 1)
                for dep in rhs.split():
                    dep = dep.strip()
                    if not dep:
                        continue
                    bn = posixpath.basename(dep)
                    cand = dep if dep.startswith("/") else posixpath.join(ddir, dep.lstrip("/"))
                    if bn not in local_kos and not vfs.is_file(cand):
                        dep_missing.append(f"{posixpath.basename(lhs)} -> {bn}")

    if load_missing:
        sev = BLOCKER if len(load_missing) >= 5 else WARN
        findings.append(Finding(
            KEY, "dlkm-modules-load-missing", sev,
            f"{len(load_missing)} kernel module(s) listed in DLKM modules.load are missing",
            "; ".join(load_missing[:_MAX_LIST])
            + (f" (+{len(load_missing) - _MAX_LIST} more)" if len(load_missing) > _MAX_LIST else ""),
            "Ensure every .ko entry in modules.load exists in its DLKM partition.",
        ))

    if dep_missing:
        findings.append(Finding(
            KEY, "dlkm-dep-missing", WARN,
            f"{len(dep_missing)} DLKM modules.dep dependency missing",
            "; ".join(dep_missing[:_MAX_LIST])
            + (f" (+{len(dep_missing) - _MAX_LIST} more)" if len(dep_missing) > _MAX_LIST else ""),
            "Ensure all transitive .ko dependencies exist in vendor_dlkm/system_dlkm/odm_dlkm.",
        ))

    if not ko_paths:
        return 0, 0, []

    try:
        from core import native_backend
        if native_backend._get_native() is None:
            return len(ko_paths), 0, []
        by_host = []
        by_erofs = {}
        by_ext = {}
        for vp in ko_paths:
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

        mods = []
        if by_host:
            mods.extend(native_backend.vdev_scan_ko_files(by_host))
        for handle, items in by_erofs.values():
            mods.extend(native_backend.vdev_scan_erofs_kos(handle, items))
        for handle, items in by_ext.values():
            mods.extend(native_backend.vdev_scan_ext_kos(handle, items))

        bad_mods = []
        if k_vm:
            for m in mods:
                vm = m.get("vermagic", "")
                if not vm:
                    continue
                verdict = vermagic_matches(k_vm, vm, bool(m.get("has_crcs")))
                if verdict is None:
                    verdict, _rsn = kmi_matches_kernel(k_vm, kernel_arch, vm, bool(m.get("has_crcs")))
                if verdict is False:
                    bad_mods.append(f"{m.get('name') or m.get('path', '').rsplit('/', 1)[-1]} ({vm})")

            if bad_mods:
                findings.append(Finding(
                    KEY, "dlkm-vermagic-mismatch", BLOCKER,
                    f"{len(bad_mods)} of {len(mods)} DLKM .ko module(s) vermagic/KMI mismatch",
                    "; ".join(bad_mods),
                    "Pair vendor_dlkm/system_dlkm/odm_dlkm modules built for the same kernel vermagic/GKI KMI.",
                ))
            elif mods:
                _, k_kmi = extract_kmi_release(k_vm)
                findings.append(Finding(
                    KEY, "dlkm-vermagic-ok", PASS,
                    f"All {len(mods)} DLKM .ko modules match kernel"
                    + (f" ({k_kmi})" if k_kmi else ""),
                    "Verified ELF .modinfo, KMI release & CONFIG_MODVERSIONS CRCs across DLKM/vendor modules.",
                ))
        return len(mods), len(bad_mods), mods
    except Exception:
        return 0, 0, []


def _audit_ramdisk_modules_load(boot_insps, findings):
    """Verify first-stage vendor_boot ramdisk modules.load & modules.dep entries exist."""
    missing_load = []
    missing_dep = []
    for bname, rep in (boot_insps or {}).items():
        if not isinstance(rep, dict):
            continue
        for rd in rep.get("ramdisks") or []:
            files_bn = {posixpath.basename(f) for f in (rd.get("files") or ())}
            if not files_bn:
                continue
            for load_path, entries in (rd.get("modules_load") or {}).items():
                # Skip recovery-only modules.load.recovery if recovery ramdisk isn't booted normally
                if "recovery" in load_path.lower() and "recovery" not in str(rd.get("name", "")).lower():
                    continue
                for mod in entries or ():
                    bn = posixpath.basename(str(mod).strip())
                    if bn and bn not in files_bn:
                        missing_load.append(f"{bn} (in {bname}:{load_path})")
            for dep_path, dep_txt in (rd.get("modules_dep") or {}).items():
                for line in str(dep_txt or "").splitlines():
                    if ":" not in line:
                        continue
                    lhs, rhs = line.split(":", 1)
                    for d in rhs.split():
                        bn = posixpath.basename(d.strip())
                        if bn and bn not in files_bn:
                            missing_dep.append(f"{posixpath.basename(lhs)} -> {bn}")

    if missing_load:
        findings.append(Finding(
            KEY, "kernel-modules-load-missing", BLOCKER,
            f"{len(missing_load)} first-stage ramdisk module(s) listed in modules.load are missing",
            "; ".join(missing_load[:_MAX_LIST])
            + (f" (+{len(missing_load) - _MAX_LIST} more)" if len(missing_load) > _MAX_LIST else "")
            + ". First-stage init will fail to load required boot drivers from vendor_boot.",
            "Ensure all .ko files listed in vendor_boot modules.load are packed inside the ramdisk.",
            "vendor_boot.img",
        ))
    if missing_dep:
        findings.append(Finding(
            KEY, "kernel-ramdisk-dep-missing", WARN,
            f"{len(missing_dep)} first-stage ramdisk modules.dep dependency missing",
            "; ".join(missing_dep[:_MAX_LIST]),
            "Pack all transitive .ko dependencies into vendor_boot ramdisk.",
            "vendor_boot.img",
        ))


def _collect_active_loaded_ko_names(vfs, boot_insps):
    """Collect .ko basenames explicitly loaded at boot (vendor_boot ramdisk + DLKM modules.load)."""
    active = set()
    for rep in (boot_insps or {}).values():
        if not isinstance(rep, dict):
            continue
        for rd in rep.get("ramdisks") or []:
            for entries in (rd.get("modules_load") or {}).values():
                for mod in entries or ():
                    bn = posixpath.basename(str(mod).strip())
                    if bn:
                        active.add(bn)
            for ko in rd.get("ko_modules") or []:
                bn = posixpath.basename(str(ko.get("path", "")).strip())
                if bn:
                    active.add(bn)
    for base in _DLKM_ROOTS:
        if not vfs.is_dir(base):
            continue
        for vp in vfs.walk_files(base, limit=6000):
            bn = posixpath.basename(vp)
            if bn == "modules.load" or bn.startswith("modules.load."):
                txt = vfs.read_text(vp, 262144) or ""
                for raw in txt.splitlines():
                    ln = raw.split("#", 1)[0].strip()
                    if ln:
                        active.add(posixpath.basename(ln))
    return active


def _audit_ko_kmi_and_symbols(all_kos, findings, active_ko_bns=None):
    """Audit CONFIG_MODVERSIONS module_layout CRC coherence & inter-module symbol CRCs across all .ko."""
    if not all_kos:
        return {"kmi_crcs": [], "sym_mismatches": 0, "ko_checked": 0}

    layout_groups = {}  # crc_hex -> [mod_path, ...]
    exported_crcs = {}  # sym -> {crc: owner_name}

    for m in all_kos:
        ml_crc = m.get("module_layout_crc")
        label = m.get("path") or m.get("name") or "?"
        if ml_crc is not None:
            chex = f"0x{int(ml_crc) & 0xFFFFFFFF:08x}"
            layout_groups.setdefault(chex, []).append(label)
        mname = m.get("name") or posixpath.basename(label)
        for sym, crc in (m.get("exported_crcs") or ()):
            exported_crcs.setdefault(sym, {})[int(crc) & 0xFFFFFFFF] = mname

    kmi_issue = False
    if len(layout_groups) > 1:
        kmi_issue = True
        ranked = sorted(layout_groups.items(), key=lambda kv: -len(kv[1]))
        minority_paths = [p for _crc, paths in ranked[1:] for p in paths]
        # If active modules.load list is known and NONE of the minority modules are loaded at boot, downgrade to WARN
        any_minority_active = (
            not active_ko_bns
            or any(posixpath.basename(p) in active_ko_bns for p in minority_paths)
        )
        sev = BLOCKER if any_minority_active else WARN
        summary = "; ".join(
            f"{crc} (x{len(paths)}, e.g. {posixpath.basename(paths[0])})"
            for crc, paths in ranked[:4]
        )
        findings.append(Finding(
            KEY, "kernel-kmi-crc-mismatch", sev,
            f"Kernel KMI module_layout CRC mismatch across .ko modules ({len(layout_groups)} conflicting ABIs)",
            f"Modules were compiled against different kernel ABIs: {summary}. "
            + ("insmod rejects modules with conflicting module_layout CRCs ('disagrees about version of symbol module_layout')."
               if any_minority_active else
               "Conflicting minority .ko files are not listed in modules.load (leftover unused modules from another kernel build)."),
            "Align boot.img, vendor_boot.img, vendor_dlkm, and system_dlkm to the same kernel build / GKI KMI.",
        ))

    sym_mismatches = []
    seen_mm = set()
    if exported_crcs:
        for m in all_kos:
            mname = m.get("name") or posixpath.basename(m.get("path", ""))
            for sym, req_crc in (m.get("imported_crcs") or ()):
                if sym == "module_layout":
                    continue
                exp = exported_crcs.get(sym)
                if not exp:
                    continue
                rc = int(req_crc) & 0xFFFFFFFF
                if rc not in exp:
                    key = (mname, sym)
                    if key not in seen_mm:
                        seen_mm.add(key)
                        got_crc, provider = next(iter(exp.items()))
                        sym_mismatches.append(
                            f"{mname} expects {sym}=0x{rc:08x} but {provider} exports 0x{got_crc:08x}"
                        )

    if sym_mismatches:
        kmi_issue = True
        findings.append(Finding(
            KEY, "kernel-symbol-crc-mismatch", BLOCKER,
            f"{len(sym_mismatches)} inter-module kernel symbol CRC mismatch(es)",
            "; ".join(sym_mismatches[:_MAX_LIST])
            + (f" (+{len(sym_mismatches) - _MAX_LIST} more)" if len(sym_mismatches) > _MAX_LIST else "")
            + ". Module loading will fail with 'disagrees about version of symbol'.",
            "Rebuild or pair dependent .ko modules from the exact same kernel build.",
        ))

    if not kmi_issue and layout_groups:
        only_crc = next(iter(layout_groups))
        cnt = len(layout_groups[only_crc])
        findings.append(Finding(
            KEY, "kernel-kmi-ok", PASS,
            f"Kernel KMI & symbol CRCs coherent across {cnt} .ko module(s) (module_layout={only_crc})",
            f"Verified {len(exported_crcs)} inter-module exported symbol CRC(s) with 0 ABI conflicts.",
        ))

    return {
        "kmi_crcs": sorted(layout_groups.keys()),
        "sym_mismatches": len(sym_mismatches),
        "ko_checked": len(all_kos),
        "exported_symbols": len(exported_crcs),
    }


def _collect_ueventd_firmware_dirs(vfs, boot_insps):
    dirs = []
    seen = set()

    def _add(d):
        norm = posixpath.normpath(d.strip())
        if norm.startswith("/") and norm not in seen:
            seen.add(norm)
            dirs.append(norm)

    for uep in (
        "/system/etc/ueventd.rc",
        "/vendor/etc/ueventd.rc",
        "/odm/etc/ueventd.rc",
        "/ueventd.rc",
    ):
        txt = vfs.read_text(uep, 131072)
        if not txt:
            continue
        for raw in txt.splitlines():
            s = raw.split("#", 1)[0].strip()
            if s.startswith("firmware_directories"):
                for tok in s.split()[1:]:
                    _add(tok)

    for rep in (boot_insps or {}).values():
        if not isinstance(rep, dict):
            continue
        for rd in rep.get("ramdisks") or []:
            for rpath, txt in (rd.get("init_rc") or {}).items():
                if "ueventd" not in rpath.lower():
                    continue
                for raw in str(txt or "").splitlines():
                    s = raw.split("#", 1)[0].strip()
                    if s.startswith("firmware_directories"):
                        for tok in s.split()[1:]:
                            _add(tok)

    for default_d in ("/vendor/firmware", "/odm/firmware", "/system/etc/firmware", "/etc/firmware", "/lib/firmware"):
        _add(default_d)
    return dirs


def _audit_ko_firmware(ctx, all_kos, boot_insps, findings):
    """Cross-check .modinfo firmware=... declarations in .ko drivers against VFS & ueventd firmware_directories."""
    vfs = ctx.vfs
    fw_dirs = _collect_ueventd_firmware_dirs(vfs, boot_insps)
    declared = []
    seen_decl = set()
    for m in all_kos:
        mname = m.get("name") or posixpath.basename(m.get("path", ""))
        for fw in (m.get("firmware") or ()):
            fw_s = str(fw).strip().lstrip("/")
            if fw_s and (mname, fw_s) not in seen_decl:
                seen_decl.add((mname, fw_s))
                declared.append((mname, fw_s))

    if not declared:
        return {"fw_dirs": fw_dirs, "declared": 0, "found": 0, "missing": []}

    # Check if an external unmounted firmware partition (like Qualcomm modem/firmware_mnt) is configured
    external_unmounted = any(
        d in ("/vendor/firmware_mnt/image", "/firmware/image", "/vendor/firmware_mnt")
        and not vfs.is_dir(d)
        for d in fw_dirs
    )

    apex_files_by_pkg = getattr(ctx.apex, "files", None) or {}
    found = []
    missing = []
    for mname, fw_rel in declared:
        hit = False
        for d in fw_dirs:
            cand = posixpath.join(d, fw_rel)
            if vfs.is_file(cand):
                hit = True
                break
            if d.startswith("/apex/"):
                parts = d.strip("/").split("/", 2)
                if len(parts) >= 2:
                    pkg = parts[1]
                    sub = parts[2] if len(parts) > 2 else ""
                    target_rel = posixpath.join(sub, fw_rel).lstrip("/")
                    afiles = apex_files_by_pkg.get(pkg) or ()
                    if target_rel in afiles or any(f.endswith("/" + fw_rel) for f in afiles):
                        hit = True
                        break
        if hit:
            found.append((mname, fw_rel))
        else:
            missing.append((mname, fw_rel))

    if missing:
        sample = "; ".join(f"{fw} ({mod})" for mod, fw in missing[:_MAX_LIST])
        more = f" (+{len(missing) - _MAX_LIST} more)" if len(missing) > _MAX_LIST else ""
        # cfg80211 regulatory.db is optional when CONFIG_CFG80211_INTERNAL_REGDB or modem/external fw partition exists
        non_regdb = [(m, f) for m, f in missing if not f.startswith("regulatory.db")]
        if non_regdb and not external_unmounted and ctx.has_vendor:
            findings.append(Finding(
                KEY, "kernel-firmware-missing", WARN,
                f"{len(non_regdb)} kernel driver firmware blob(s) declared in .ko not found",
                f"{sample}{more}. Kernel request_firmware() may fail if these blobs are required at runtime.",
                "Place the required firmware files in /vendor/firmware or check firmware_directories in ueventd.rc.",
                "/vendor/firmware",
            ))
        else:
            findings.append(Finding(
                KEY, "kernel-firmware-external", INFO,
                f"{len(missing)} .ko firmware blob(s) not present in mounted ROM partitions",
                f"{sample}{more}"
                + (" (external partition such as /vendor/firmware_mnt is not mounted in this project)."
                   if external_unmounted else " (optional/built-in regulatory database)."),
            ))
    elif found:
        findings.append(Finding(
            KEY, "kernel-firmware-ok", PASS,
            f"All {len(found)} .ko declared firmware blob(s) verified in firmware search paths",
            ", ".join(fw for _, fw in found[:6]),
        ))

    return {
        "fw_dirs": fw_dirs,
        "declared": len(declared),
        "found": len(found),
        "missing": [f"{fw} ({mod})" for mod, fw in missing],
    }


def _build_kernel_hardware_summary(boot_insps, dtbo_rep, all_kos, kmi_info, fw_info):
    """Aggregate DTB/DTBO compatibles, .ko OF modalias bindings, and module inventory for s14_hardware."""
    dtb_compats = []
    dtb_seen = set()
    for rep in (boot_insps or {}).values():
        if not isinstance(rep, dict):
            continue
        for key in ("dtb", "kernel_dtb"):
            for c in ((rep.get(key) or {}).get("compatibles") or ()):
                if c and c not in dtb_seen:
                    dtb_seen.add(c)
                    dtb_compats.append(c)
    if isinstance(dtbo_rep, dict):
        for c in (dtbo_rep.get("all_compatibles") or ()):
            if c and c not in dtb_seen:
                dtb_seen.add(c)
                dtb_compats.append(c)

    of_aliases = {}  # compat -> mod_name
    ko_names = set()
    for m in all_kos:
        mn = _norm_mod_name(m.get("name") or m.get("path", ""))
        if mn:
            ko_names.add(mn)
        for compat in (m.get("of_aliases") or ()):
            of_aliases.setdefault(compat, mn)

    matched_of = sorted(c for c in dtb_seen if c in of_aliases)
    return {
        "ko_total": len(all_kos),
        "ko_names": sorted(ko_names),
        "of_alias_count": len(of_aliases),
        "dtb_compatibles": dtb_compats[:120],
        "matched_of_compatibles": matched_of,
        "kmi": kmi_info,
        "firmware": fw_info,
    }

_SOC_CHIP_RE = re.compile(
    r"\b(mt\d{4}[a-z]?|sm\d{4}|sdm\d{3,4}|qcm\d{4}|sc\d{4}|exynos\d+|gs\d{3}|kirin\d+|ums\d{4})\b",
    re.IGNORECASE,
)


def _soc_vendor_tokens(strings):
    vendors = set()
    chips = set()
    for raw in strings or ():
        s = str(raw or "").lower()
        if any(k in s for k in ("mediatek", "mt6", "mt8")):
            vendors.add("mediatek")
        if any(k in s for k in ("qcom", "qualcomm", "msm", "sdm", "sm6", "sm7", "sm8")):
            vendors.add("qualcomm")
        if "exynos" in s or "samsung,exynos" in s:
            vendors.add("exynos")
        if "google,gs" in s or "tensor" in s:
            vendors.add("tensor")
        if "sprd" in s or "unisoc" in s or "ums" in s:
            vendors.add("unisoc")
        for m in _SOC_CHIP_RE.findall(s):
            chips.add(m.lower())
    return vendors, chips


def _check_dtbo(ctx, boot_insps, findings):
    rep = getattr(ctx.layout, "dtbo_report", None)
    if not isinstance(rep, dict):
        return
    src = getattr(ctx.layout, "dtbo_source", "") or "dtbo.img"
    entry_count = int(rep.get("entry_count") or 0)
    valid_fdt = int(rep.get("valid_fdt_count") or 0)
    invalid_idx = rep.get("invalid_fdt_indices") or []

    if not rep.get("valid"):
        findings.append(Finding(
            KEY, "dtbo-corrupt", BLOCKER,
            "dtbo.img container or FDT overlay is invalid",
            rep.get("error") or f"{len(invalid_idx)} of {entry_count} DTBO entries have invalid FDT headers.",
            "Re-unpack and repack dtbo.img with valid DTB/DTS entries, or restore stock dtbo.img.",
            src,
        ))
        ctx.shared["dtbo"] = {"valid": False, "entries": entry_count}
        return

    # Collect base DTB symbols & compatibles from vendor_boot / boot
    base_symbols = set()
    base_compatibles = []
    for brep in (boot_insps or {}).values():
        if not isinstance(brep, dict):
            continue
        for key in ("dtb", "kernel_dtb"):
            dm = brep.get(key) or {}
            base_symbols.update(dm.get("symbols") or [])
            base_compatibles.extend(dm.get("compatibles") or [])

    # 1) Overlay __fixups__ vs base DTB __symbols__
    fixup_issue = False
    best_fixup_summary = ""
    entries_with_fixups = [e for e in (rep.get("entries") or []) if e.get("fixups")]
    if base_symbols and entries_with_fixups:
        best = None
        for ent in entries_with_fixups:
            fxs = ent.get("fixups") or []
            missing = [fx for fx in fxs if fx not in base_symbols]
            ratio = (len(fxs) - len(missing)) / max(1, len(fxs))
            cand = (ratio, -len(missing), ent, missing)
            if best is None or cand[:2] > best[:2]:
                best = cand
        ratio, _, best_ent, missing = best
        total_fx = len(best_ent.get("fixups") or [])
        best_fixup_summary = f"{total_fx - len(missing)}/{total_fx} overlay fixups resolved"
        if missing:
            fixup_issue = True
            sample = ", ".join(missing[:8]) + (f" (+{len(missing) - 8} more)" if len(missing) > 8 else "")
            if ratio < 0.50 and len(missing) >= 4:
                findings.append(Finding(
                    KEY, "dtbo-fixups-incompatible", BLOCKER,
                    f"dtbo.img overlay symbols do not match vendor_boot DTB ({len(missing)}/{total_fx} missing)",
                    f"Best-matching DTBO entry #{best_ent.get('index', 0)} references missing base DTB symbols: {sample}. "
                    "The bootloader cannot apply hardware overlays onto this vendor_boot DTB.",
                    "Use dtbo.img built from the same kernel/DTB tree as vendor_boot.img.",
                    src,
                ))
            else:
                findings.append(Finding(
                    KEY, "dtbo-fixups-missing", WARN,
                    f"dtbo.img has {len(missing)} unresolved overlay symbol(s) vs base DTB",
                    f"Best-matching DTBO entry #{best_ent.get('index', 0)} ({best_fixup_summary}); missing: {sample}.",
                    "Check if dtbo.img matches your vendor_boot.img kernel build.",
                    src,
                ))

    # 2) SoC vendor / chip family coherence vs vendor_boot DTB & build.prop
    soc_issue = False
    dtbo_vendors, dtbo_chips = _soc_vendor_tokens(rep.get("all_compatibles") or [])
    props = (ctx.profile or {}).get("props") or {}
    ref_strings = list(base_compatibles) + [
        props.get("ro.board.platform", ""),
        props.get("ro.hardware", ""),
        props.get("ro.soc.model", ""),
    ]
    ref_vendors, ref_chips = _soc_vendor_tokens(ref_strings)
    if dtbo_vendors and ref_vendors and dtbo_vendors.isdisjoint(ref_vendors):
        soc_issue = True
        findings.append(Finding(
            KEY, "dtbo-soc-mismatch", BLOCKER,
            f"dtbo.img SoC ({', '.join(sorted(dtbo_vendors))}) does not match device ({', '.join(sorted(ref_vendors))})",
            f"DTBO compatibles: {', '.join((rep.get('all_compatibles') or [])[:4])}.",
            "Flash the stock dtbo.img for this exact device SoC.",
            src,
        ))
    elif dtbo_chips and ref_chips and dtbo_chips.isdisjoint(ref_chips):
        # Different chip ID within same vendor (e.g. mt6789 vs mt6855)
        soc_issue = True
        findings.append(Finding(
            KEY, "dtbo-chip-mismatch", WARN,
            f"dtbo.img chip ({', '.join(sorted(dtbo_chips)[:3])}) differs from base DTB/ROM ({', '.join(sorted(ref_chips)[:3])})",
            "DTBO overlay entries target a different SoC revision than vendor_boot/build.prop.",
            "Verify dtbo.img comes from the target device firmware.",
            src,
        ))

    if not fixup_issue and not soc_issue:
        extra_bits = [f"v{rep.get('version', 0)}", f"{valid_fdt} FDT overlay(s)"]
        if best_fixup_summary:
            extra_bits.append(best_fixup_summary)
        if rep.get("all_fstab"):
            extra_bits.append(f"{len(rep['all_fstab'])} early fstab entry(s)")
        findings.append(Finding(
            KEY, "dtbo-ok", PASS,
            f"DTBO valid ({' · '.join(extra_bits)})",
            ", ".join((rep.get("all_compatibles") or [])[:4]),
            "", src,
        ))

    ctx.shared["dtbo"] = {
        "valid": True,
        "entries": entry_count,
        "fixups_ok": not fixup_issue,
        "soc_ok": not soc_issue,
        "summary": best_fixup_summary or f"{valid_fdt} overlays",
    }


def _detect_rom_fs_and_fstab_flags(ctx, boot_insps):
    """Collect mounted/declared FS types and fstab security flags without needing s1_mount."""
    fs_types = set()
    fstab_flags = set()
    data_fs = set()

    # 1) Actual image FS types detected by layout (EROFS / EXT / F2FS)
    for pinfo in (getattr(ctx.layout, "partitions", None) or {}).values():
        ft = (getattr(pinfo, "fs_type", "") or "").strip().lower()
        if ft == "ext":
            fs_types.add("ext4")
        elif ft:
            fs_types.add(ft)

    # 2) Parse fstabs from boot/vendor_boot ramdisks and /vendor/etc/fstab.*
    fstab_texts = []
    for rep in (boot_insps or {}).values():
        if not isinstance(rep, dict):
            continue
        for rd in rep.get("ramdisks") or []:
            for txt in (rd.get("fstabs") or {}).values():
                if txt:
                    fstab_texts.append(txt)
    for d in ("/vendor/etc", "/odm/etc", "/system/etc"):
        if not ctx.vfs.is_dir(d):
            continue
        for name in ctx.vfs.listdir(d):
            if name.startswith("fstab."):
                txt = ctx.vfs.read_text(f"{d}/{name}", 131072)
                if txt:
                    fstab_texts.append(txt)

    for txt in fstab_texts:
        for raw in txt.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            mnt = parts[1].strip()
            fstype = parts[2].strip().lower()
            flags = " ".join(parts[3:]).lower()
            if fstype in ("erofs", "ext4", "f2fs", "squashfs"):
                fs_types.add(fstype)
                if mnt == "/data":
                    data_fs.add(fstype)
            if "fileencryption=" in flags:
                fstab_flags.add("fbe")
            if "inlinecrypt" in flags:
                fstab_flags.add("inlinecrypt")
            if "avb" in flags and "avb_keys" not in flags:
                fstab_flags.add("avb")
            elif "avb=" in flags or ",avb" in flags:
                fstab_flags.add("avb")

    return fs_types, data_fs, fstab_flags


def _check_kernel_config(ctx, kinfo, boot_insps, findings):
    """Audit extracted IKCFG kernel .config against ROM filesystems, fstab & Android SDK requirements."""
    if not kinfo:
        return
    kcfg = kinfo.get("kernel_config") or {}
    total_cfg = int(kinfo.get("kernel_config_count") or len(kcfg))
    if not kcfg:
        return

    def _is_enabled(key):
        val = kcfg.get(key)
        return val in ("y", "m")

    def _is_explicitly_off(key):
        # Either explicitly '# CONFIG_X is not set' ("n") OR absent from a full IKCFG (>200 options)
        val = kcfg.get(key)
        if val == "n":
            return True
        if val is None and total_cfg >= 200:
            return True
        return False

    fs_types, data_fs, fstab_flags = _detect_rom_fs_and_fstab_flags(ctx, boot_insps)
    props = (ctx.profile or {}).get("props") or {}
    sdk = int((ctx.profile or {}).get("sdk") or 0)
    if not sdk:
        try:
            sdk = int(str(props.get("ro.build.version.sdk") or "0").strip() or "0")
        except ValueError:
            sdk = 0

    avb_disabled = bool(getattr(ctx, "options", {}).get("avb_disabled", False))
    issues = 0

    # 1) Filesystem driver requirements
    if "erofs" in fs_types and _is_explicitly_off("CONFIG_EROFS_FS"):
        issues += 1
        findings.append(Finding(
            KEY, "kernel-config-erofs-missing", BLOCKER,
            "Kernel .config lacks CONFIG_EROFS_FS, but ROM uses EROFS partitions",
            "boot.img IKCFG shows CONFIG_EROFS_FS is disabled, while this ROM ships EROFS images/fstab entries. "
            "First-stage init will fail to mount /system or /vendor and kernel-panic.",
            "Enable CONFIG_EROFS_FS=y and CONFIG_EROFS_FS_ZIP=y in kernel defconfig, or repack partitions as ext4.",
            "boot.img",
        ))
    if "f2fs" in data_fs and _is_explicitly_off("CONFIG_F2FS_FS"):
        issues += 1
        findings.append(Finding(
            KEY, "kernel-config-f2fs-missing", BLOCKER,
            "Kernel .config lacks CONFIG_F2FS_FS, but /data fstab mounts F2FS",
            "boot.img IKCFG shows CONFIG_F2FS_FS is disabled, while fstab mounts /data as f2fs.",
            "Enable CONFIG_F2FS_FS=y in kernel or switch /data fstab entry to ext4.",
            "boot.img",
        ))
    if "ext4" in fs_types and _is_explicitly_off("CONFIG_EXT4_FS"):
        issues += 1
        findings.append(Finding(
            KEY, "kernel-config-ext4-missing", BLOCKER,
            "Kernel .config lacks CONFIG_EXT4_FS, but ROM uses EXT4 partitions",
            "boot.img IKCFG shows CONFIG_EXT4_FS is disabled while ext4 partitions/fstab entries are present.",
            "Enable CONFIG_EXT4_FS=y in kernel defconfig.",
            "boot.img",
        ))

    # 2) Mandatory Android core kernel subsystems
    if _is_explicitly_off("CONFIG_SECURITY_SELINUX"):
        issues += 1
        findings.append(Finding(
            KEY, "kernel-config-selinux-disabled", BLOCKER,
            "Kernel .config has CONFIG_SECURITY_SELINUX disabled",
            "Android init requires SELinux support in the kernel to load sepolicy and transition domains.",
            "Enable CONFIG_SECURITY_SELINUX=y in kernel defconfig.",
            "boot.img",
        ))
    if _is_explicitly_off("CONFIG_ANDROID_BINDER_IPC"):
        issues += 1
        findings.append(Finding(
            KEY, "kernel-config-binder-disabled", BLOCKER,
            "Kernel .config has CONFIG_ANDROID_BINDER_IPC disabled",
            "Binder IPC is mandatory for servicemanager, SurfaceFlinger, and all Android framework services.",
            "Enable CONFIG_ANDROID_BINDER_IPC=y and CONFIG_ANDROID_BINDERFS=y.",
            "boot.img",
        ))

    # 3) Android SDK-versioned requirements (12+ BPF, 14+ USERFAULTFD, 11+ PSI)
    if sdk >= 31 and (_is_explicitly_off("CONFIG_BPF_SYSCALL") or _is_explicitly_off("CONFIG_CGROUP_BPF")):
        missing_bpf = [k for k in ("CONFIG_BPF_SYSCALL", "CONFIG_CGROUP_BPF") if _is_explicitly_off(k)]
        issues += 1
        findings.append(Finding(
            KEY, "kernel-config-bpf-missing", BLOCKER,
            f"Kernel .config lacks {', '.join(missing_bpf)} required by Android SDK {sdk}",
            "Android 12+ netd/bpfloader requires eBPF syscall & cgroup BPF hooks; boot stalls in netd/connectivity.",
            "Enable CONFIG_BPF_SYSCALL=y, CONFIG_CGROUP_BPF=y, and CONFIG_BPF_JIT=y in kernel.",
            "boot.img",
        ))
    if sdk >= 34 and _is_explicitly_off("CONFIG_USERFAULTFD"):
        issues += 1
        findings.append(Finding(
            KEY, "kernel-config-userfaultfd-missing", WARN,
            f"Kernel .config lacks CONFIG_USERFAULTFD (recommended/expected on Android SDK {sdk})",
            "Android 14+ ART Concurrent Mark-Compact (CMC) GC relies on userfaultfd. Without it ART falls back or "
            "fails when CMC GC is forced.",
            "Enable CONFIG_USERFAULTFD=y in kernel defconfig for full Android 14/15/16 ART compatibility.",
            "boot.img",
        ))
    if sdk >= 30 and _is_explicitly_off("CONFIG_PSI"):
        issues += 1
        findings.append(Finding(
            KEY, "kernel-config-psi-missing", WARN,
            f"Kernel .config lacks CONFIG_PSI (required by lmkd on Android SDK {sdk})",
            "lmkd uses Pressure Stall Information (/proc/pressure/*) on Android 11+ to manage low-memory kills.",
            "Enable CONFIG_PSI=y in kernel defconfig.",
            "boot.img",
        ))

    # 4) FBE & dm-verity coherence
    if "fbe" in fstab_flags and _is_explicitly_off("CONFIG_FS_ENCRYPTION"):
        issues += 1
        findings.append(Finding(
            KEY, "kernel-config-fbe-missing", BLOCKER,
            "Kernel .config lacks CONFIG_FS_ENCRYPTION, but fstab enables fileencryption",
            "vold cannot set up File-Based Encryption without CONFIG_FS_ENCRYPTION=y; /data mount will fail.",
            "Enable CONFIG_FS_ENCRYPTION=y in kernel or disable FBE in fstab.",
            "boot.img",
        ))
    if ("inlinecrypt" in fstab_flags
            and _is_explicitly_off("CONFIG_FS_ENCRYPTION_INLINE_CRYPT")
            and _is_explicitly_off("CONFIG_BLK_INLINE_ENCRYPTION")):
        issues += 1
        findings.append(Finding(
            KEY, "kernel-config-inlinecrypt-missing", WARN,
            "Kernel .config lacks inline crypto, but fstab specifies inlinecrypt",
            "Both CONFIG_FS_ENCRYPTION_INLINE_CRYPT and CONFIG_BLK_INLINE_ENCRYPTION are disabled while fstab requests inlinecrypt.",
            "Enable CONFIG_FS_ENCRYPTION_INLINE_CRYPT=y or remove inlinecrypt from /data fstab flags.",
            "boot.img",
        ))
    if "avb" in fstab_flags and not avb_disabled and _is_explicitly_off("CONFIG_DM_VERITY"):
        issues += 1
        findings.append(Finding(
            KEY, "kernel-config-dm-verity-missing", WARN,
            "Kernel .config lacks CONFIG_DM_VERITY, but fstab enables AVB/dm-verity",
            "dm-verity verification requires CONFIG_DM_VERITY=y unless vbmeta disables hashtree verification.",
            "Enable CONFIG_DM_VERITY=y in kernel or disable AVB verification in vbmeta.",
            "boot.img",
        ))

    ctx.shared["kernel_config"] = {
        "total": total_cfg,
        "flags": dict(kcfg),
        "fs_types": sorted(fs_types),
        "issues": issues,
    }

    if issues == 0:
        enabled_highlights = [
            label for key, label in (
                ("CONFIG_EROFS_FS", "EROFS"),
                ("CONFIG_F2FS_FS", "F2FS"),
                ("CONFIG_EXT4_FS", "EXT4"),
                ("CONFIG_FS_ENCRYPTION", "FBE"),
                ("CONFIG_DM_VERITY", "dm-verity"),
                ("CONFIG_BPF_SYSCALL", "eBPF"),
                ("CONFIG_USERFAULTFD", "UFFD"),
                ("CONFIG_SECURITY_SELINUX", "SELinux"),
                ("CONFIG_ANDROID_BINDER_IPC", "Binder"),
            )
            if _is_enabled(key)
        ]
        findings.append(Finding(
            KEY, "kernel-config-ok", PASS,
            f"Kernel IKCFG .config matches ROM requirements ({total_cfg} options parsed)",
            "Verified enabled: " + (", ".join(enabled_highlights) or "core options"),
            "", "boot.img",
        ))


def run(ctx):
    findings = []
    boot_insps = getattr(ctx.layout, "boot_inspections", None) or {}
    if boot_insps:
        boot_rep = next((r for k, r in boot_insps.items()
                         if "vendor_boot" not in k.lower() and "init_boot" not in k.lower()
                         and r.get("kernel")), None)
        vnd_rep = next((r for k, r in boot_insps.items() if "vendor_boot" in k.lower()), None)
        kinfo = (boot_rep or {}).get("kernel")
        k_vm = (kinfo or {}).get("vermagic", "")
        k_ver = (kinfo or {}).get("version", "")
        k_arch = (kinfo or {}).get("arch", "")
        _, k_kmi = extract_kmi_release(k_ver or k_vm)
        if kinfo:
            findings.append(Finding(
                KEY, "kernel", INFO,
                f"Kernel {k_ver or '(version string not found)'}",
                f"{k_arch or 'arch ?'} · page {kinfo.get('page_size') or '?'} · "
                f"{kinfo.get('size', 0) / 1048576:.1f} MB uncompressed"
                + (f" · KMI {k_kmi}" if k_kmi else "")
                + (" · embedded config present" if kinfo.get("has_config") else ""),
                "", "boot.img",
            ))
        ramdisk_kos = []
        vermagics = {}
        for rep in ([vnd_rep] if vnd_rep else list(boot_insps.values())):
            if not rep:
                continue
            for rd in rep.get("ramdisks") or []:
                for ko in rd.get("ko_modules") or []:
                    ramdisk_kos.append(ko)
                    vm = ko.get("vermagic") or "(no vermagic)"
                    slot = vermagics.setdefault(vm, {"mods": [], "has_crcs": False})
                    slot["mods"].append(ko.get("path", "").rsplit("/", 1)[-1])
                    if ko.get("has_crcs"):
                        slot["has_crcs"] = True
        _audit_ramdisk_modules_load(boot_insps, findings)
        if vermagics:
            total = sum(len(v["mods"]) for v in vermagics.values())
            if not k_vm and not k_ver:
                findings.append(Finding(
                    KEY, "vermagic-unknown", INFO,
                    f"{total} kernel module(s) found, but kernel vermagic is unknown",
                    "; ".join(f"{k} (x{len(v['mods'])})" for k, v in list(vermagics.items())[:3]),
                ))
            else:
                good, bad = {}, {}
                reasons = []
                for magic, slot in vermagics.items():
                    verdict = vermagic_matches(k_vm, magic, slot["has_crcs"]) if k_vm else None
                    if verdict is None and k_ver:
                        verdict, rsn = kmi_matches_kernel(k_ver, k_arch, magic, slot["has_crcs"])
                        if rsn:
                            reasons.append(rsn)
                    (good if verdict is not False else bad)[magic] = slot
                if not bad:
                    crc = any(v["has_crcs"] for v in vermagics.values())
                    kmi_note = f"Matched {reasons[0]}" if reasons else (
                        "Modules carry CONFIG_MODVERSIONS CRCs (GKI compatible)." if crc
                        else "vermagic matches exactly."
                    )
                    findings.append(Finding(
                        KEY, "vermagic-ok", PASS,
                        f"All {total} first-stage kernel modules match kernel"
                        + (f" ({k_kmi})" if k_kmi else ""),
                        f"{kmi_note} — " + "; ".join(f"{k} (x{len(v['mods'])})" for k, v in list(vermagics.items())[:3]),
                    ))
                else:
                    bad_count = sum(len(v["mods"]) for v in bad.values())
                    detail = "; ".join(
                        f"{m} (x{len(s['mods'])}, e.g. {', '.join(s['mods'][:3])})"
                        for m, s in list(bad.items())[:_MAX_LIST]
                    )
                    findings.append(Finding(
                        KEY, "vermagic-mismatch", BLOCKER,
                        f"{bad_count} of {total} kernel module(s) will be rejected",
                        f"Kernel is \"{k_vm or k_ver}\", but modules carry: {detail}.",
                        "Use boot.img and vendor_boot.img with matching kernel KMI/vermagic.",
                        "vendor_boot.img",
                    ))
        dlkm_total, dlkm_bad, dlkm_kos = _scan_dlkm_kos(ctx, k_vm or k_ver, findings, kernel_arch=k_arch)
        all_kos = ramdisk_kos + dlkm_kos
        active_bns = _collect_active_loaded_ko_names(ctx.vfs, boot_insps)
        kmi_info = _audit_ko_kmi_and_symbols(all_kos, findings, active_ko_bns=active_bns)
        fw_info = _audit_ko_firmware(ctx, all_kos, boot_insps, findings)
        _check_kernel_config(ctx, kinfo, boot_insps, findings)
        _check_dtbo(ctx, boot_insps, findings)
        ctx.shared["kernel_hardware"] = _build_kernel_hardware_summary(
            boot_insps, getattr(ctx.layout, "dtbo_report", None), all_kos, kmi_info, fw_info,
        )
        ctx.shared["kernel"] = {
            "version": (kinfo or {}).get("version", ""),
            "vermagic": k_vm,
            "arch": (kinfo or {}).get("arch", ""),
            "first_stage_kos": sum(len(v["mods"]) for v in vermagics.values()),
            "dlkm_kos": dlkm_total,
            "dlkm_bad": dlkm_bad,
            "has_config": bool((kinfo or {}).get("has_config")),
            "kernel_config_count": int((kinfo or {}).get("kernel_config_count") or 0),
        }
        if kinfo or vermagics or dlkm_total or getattr(ctx.layout, "dtbo_report", None):
            return findings

    boot, vendor_boot = _boot_paths(ctx)

    if not boot and not vendor_boot:
        dlkm_total, dlkm_bad, dlkm_kos = _scan_dlkm_kos(ctx, "", findings)
        if dlkm_kos:
            active_bns = _collect_active_loaded_ko_names(ctx.vfs, boot_insps)
            kmi_info = _audit_ko_kmi_and_symbols(dlkm_kos, findings, active_ko_bns=active_bns)
            fw_info = _audit_ko_firmware(ctx, dlkm_kos, boot_insps, findings)
            ctx.shared["kernel_hardware"] = _build_kernel_hardware_summary(
                boot_insps, getattr(ctx.layout, "dtbo_report", None), dlkm_kos, kmi_info, fw_info,
            )
        if getattr(ctx.layout, "dtbo_report", None):
            _check_dtbo(ctx, boot_insps, findings)
        if findings:
            return findings
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
