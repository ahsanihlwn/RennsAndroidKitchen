# SPDX-License-Identifier: MPL-2.0
"""End-to-end static boot simulation from bootloader/AVB up to Zygote & SystemServer."""
from __future__ import annotations

import posixpath

from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "system_server"
LABEL = "Boot-to-SystemServer simulation"
REQUIRES = ("system",)

_MAX_LIST = 10

_CLASSPATH_FILES = (
    "/system/etc/classpaths/bootclasspath.pb",
    "/system/etc/classpaths/systemserverclasspath.pb",
)


def _check_classpaths(ctx):
    vfs = ctx.vfs
    apex = ctx.apex
    jars = []
    pb_found = []

    try:
        from core import native_backend
        parse_pb = native_backend.vdev_parse_classpath_pb
    except Exception:
        parse_pb = None

    if parse_pb is not None:
        for vp in _CLASSPATH_FILES:
            raw = vfs.read_bytes(vp)
            if not raw:
                continue
            pb_found.append(vp)
            try:
                jars.extend(parse_pb(raw))
            except Exception:
                pass
        # Reuse pre-decoded APEX classpath jars from parallel Rust indexer if available
        if getattr(apex, "classpath_jars", None):
            jars.extend(apex.classpath_jars)
            pb_found.append(f"apex({len(apex.classpath_jars)} entries)")
        else:
            for pkg in sorted(apex.files):
                for rel in ("etc/classpaths/bootclasspath.pb", "etc/classpaths/systemserverclasspath.pb"):
                    if apex.has_path(pkg, rel):
                        vp = f"/apex/{pkg}/{rel}"
                        raw = vfs.read_bytes(vp)
                        if raw:
                            pb_found.append(vp)
                            try:
                                jars.extend(parse_pb(raw))
                            except Exception:
                                pass

    sdk_str = ctx.prop("ro.build.version.sdk")
    try:
        sdk = int(sdk_str) if sdk_str else 0
    except ValueError:
        sdk = 0

    missing_boot = []
    missing_sys = []
    checked = 0

    for item in jars:
        jpath = item.get("path", "")
        cp = item.get("classpath", "")
        if not jpath:
            continue
        min_s = item.get("min_sdk_version", "")
        max_s = item.get("max_sdk_version", "")
        if sdk:
            try:
                if min_s and sdk < int(min_s):
                    continue
                if max_s and sdk > int(max_s):
                    continue
            except ValueError:
                pass

        checked += 1
        exists = False
        if jpath.startswith("/apex/"):
            parts = jpath.strip("/").split("/", 2)
            if len(parts) == 3 and apex.has_path(parts[1], parts[2]):
                exists = True
        if not exists:
            exists = vfs.is_file(jpath)
        if not exists:
            if "SYSTEMSERVER" in cp:
                missing_sys.append(jpath)
            else:
                missing_boot.append(jpath)

    return pb_found, checked, missing_boot, missing_sys


def _check_dlkm_modules(ctx):
    """Verify modules.load in vendor_dlkm / odm_dlkm against kernel vermagic."""
    vfs = ctx.vfs
    boot_insps = getattr(ctx.layout, "boot_inspections", None) or {}
    kernel_ver = ""
    for k, rep in boot_insps.items():
        if "vendor_boot" not in k.lower() and "init_boot" not in k.lower():
            ki = rep.get("kernel") or {}
            if ki.get("version"):
                kernel_ver = ki["version"]
                break

    found_loads = []
    missing_kos = []
    for base in ("/vendor_dlkm/lib/modules", "/vendor/lib/modules", "/odm_dlkm/lib/modules"):
        for fname in ("modules.load", "modules.load.normal"):
            vp = posixpath.join(base, fname)
            text = vfs.read_text(vp)
            if not text:
                continue
            found_loads.append(vp)
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                mod_name = posixpath.basename(line)
                if not (vfs.is_file(posixpath.join(base, mod_name)) or vfs.is_file(posixpath.join(base, line))):
                    missing_kos.append(mod_name)
    return found_loads, missing_kos, kernel_ver


def run(ctx):
    findings = []
    vfs = ctx.vfs

    def _stage_blockers(key):
        return []

    # Pull results from prior stages via ctx.shared / layout
    boot_insps = getattr(ctx.layout, "boot_inspections", None) or {}
    vbmeta_map = ctx.shared.get("vbmeta") or {}
    init_info = ctx.shared.get("init") or {}
    init_model = init_info.get("model")
    avc_info = ctx.shared.get("avc") or {}
    linker_info = ctx.shared.get("linker") or {}
    readiness = ctx.shared.get("readiness") or {}

    phases = []

    # ── Phase 1: Bootloader & AVB ──
    vb_reports = getattr(ctx.layout, "vbmeta_reports", None) or {}
    avb_checks = []
    if vbmeta_map or vb_reports:
        names = sorted(set(vbmeta_map.keys()) | set(vb_reports.keys()))
        ht_count = sum(
            1 for r in vb_reports.values()
            for d in (r.get("descriptors") or [])
            if d.get("kind") == "hashtree"
        )
        avb_checks.append(f"{len(names)} vbmeta parsed ({', '.join(names[:4])})")
        if ht_count:
            avb_checks.append(f"{ht_count} dm-verity hashtree descriptor(s)")
    else:
        avb_checks.append("no standalone vbmeta parsed")
    avb_status = "PASS" if (vbmeta_map or vb_reports) else "WARN"
    if not ctx.vbmeta_enabled:
        avb_checks.append("AVB: OFF (verity/vbmeta bypassed)")
        avb_status = "PASS"
    elif ctx.shared.get("avb_blocker"):
        avb_checks.append("AVB / dm-verity FAILED")
        avb_status = "BLOCKER"
    phases.append({
        "phase": "bootloader_avb",
        "label": "1. Bootloader & AVB chain",
        "status": avb_status,
        "checks": avb_checks,
    })

    # ── Phase 2: Kernel, First-Stage Ramdisk & DLKM ──
    k_checks = []
    k_status = "PASS"
    if boot_insps:
        k_checks.append(f"{len(boot_insps)} boot container(s) inspected")
    else:
        k_checks.append("no boot.img / vendor_boot.img inspected")
        k_status = "WARN"
    if ctx.fstab:
        k_checks.append(f"fstab ready ({len(ctx.fstab)} entries via {ctx.fstab_origin})")
    else:
        k_checks.append("no fstab resolved")
        k_status = "WARN"

    dlkm_loads, dlkm_missing, _kver = _check_dlkm_modules(ctx)
    if dlkm_loads:
        if dlkm_missing:
            k_status = "WARN"
            k_checks.append(f"dlkm modules.load missing {len(dlkm_missing)} .ko")
            findings.append(Finding(
                KEY, "dlkm-missing", WARN,
                f"{len(dlkm_missing)} module(s) listed in dlkm modules.load are missing",
                ", ".join(dlkm_missing[:_MAX_LIST]),
                "Check vendor_dlkm/odm_dlkm lib/modules consistency.",
                dlkm_loads[0],
            ))
        else:
            k_checks.append(f"dlkm modules.load complete ({len(dlkm_loads)} list)")

    dtbo_info = ctx.shared.get("dtbo") or {}
    if dtbo_info:
        if not dtbo_info.get("valid") or not dtbo_info.get("soc_ok"):
            k_status = "BLOCKER"
            k_checks.append("dtbo invalid/incompatible")
        elif not dtbo_info.get("fixups_ok"):
            if k_status == "PASS":
                k_status = "WARN"
            k_checks.append(f"dtbo partial ({dtbo_info.get('summary', '')})")
        else:
            k_checks.append(f"dtbo ok ({dtbo_info.get('summary', '')})")

    phases.append({
        "phase": "kernel_mount",
        "label": "2. Kernel & first-stage mount",
        "status": k_status,
        "checks": k_checks,
    })

    # ── Phase 3: SELinux Policy & Domains ──
    se_status = "PASS"
    se_checks = []
    cil_info = ctx.shared.get("selinux_cil") or {}
    if cil_info:
        dups_n = len(cil_info.get("duplicate_types") or ())
        unres_n = len(cil_info.get("unresolved_symbols") or ())
        if dups_n or unres_n:
            se_status = "BLOCKER"
            se_checks.append(f"CIL assembly fail (collisions={dups_n}, unresolved={unres_n})")
        elif cil_info.get("files"):
            se_checks.append(f"CIL ok ({cil_info.get('type_count', 0)} types)")
    if avc_info:
        se_checks.append(
            f"mode={avc_info.get('rom_mode', 'enforcing')} · checked={avc_info.get('checked', 0)} services"
        )
        if avc_info.get("fatal", 0):
            se_status = "BLOCKER"
            se_checks.append(f"{avc_info['fatal']} critical AVC blocker(s)")
        elif avc_info.get("noisy", 0) or avc_info.get("binder_denials", 0):
            if se_status == "PASS":
                se_status = "WARN"
            if avc_info.get("noisy", 0):
                se_checks.append(f"{avc_info['noisy']} non-critical AVC denial(s)")
            if avc_info.get("binder_denials", 0):
                se_checks.append(f"{avc_info['binder_denials']} Binder HAL denial(s)")
    else:
        if se_status == "PASS":
            se_status = "WARN"
        se_checks.append("AVC domain trace unavailable")
    phases.append({
        "phase": "selinux_domains",
        "label": "3. SELinux assembly & domains",
        "status": se_status,
        "checks": se_checks,
    })

    # ── Phase 4: APEX & Linker Namespaces ──
    apex = ctx.apex
    lx_status = "PASS"
    lx_checks = []
    if apex.apex_count:
        lx_checks.append(f"{apex.apex_count} APEX mounted")
    else:
        lx_status = "WARN"
        lx_checks.append("no APEX indexed")
    if linker_info:
        unres = linker_info.get("unresolved") or {}
        lx_checks.append(f"{linker_info.get('scanned', 0)} ELF scanned, {len(unres)} unresolved sonames")
        if unres:
            lx_status = "BLOCKER"
    phases.append({
        "phase": "apex_linker",
        "label": "4. APEX & linker namespaces",
        "status": lx_status,
        "checks": lx_checks,
    })

    # ── Phase 5: derive_classpath (BOOTCLASSPATH & SYSTEMSERVERCLASSPATH) ──
    pb_found, cp_checked, missing_boot, missing_sys = _check_classpaths(ctx)
    cp_status = "PASS"
    cp_checks = []
    if pb_found:
        cp_checks.append(f"{len(pb_found)} classpath.pb parsed ({cp_checked} jars checked)")
        if missing_boot or missing_sys:
            cp_status = "BLOCKER"
            all_miss = missing_boot + missing_sys
            cp_checks.append(f"{len(all_miss)} classpath jar(s) missing")
            findings.append(Finding(
                KEY, "classpath-jar-missing", BLOCKER,
                f"{len(all_miss)} jar(s) declared in classpath.pb are missing",
                ", ".join(all_miss[:_MAX_LIST])
                + (f" (+{len(all_miss) - _MAX_LIST} more)" if len(all_miss) > _MAX_LIST else "")
                + ". Zygote/SystemServer cannot initialize its boot or system_server classpath without them.",
                "Ensure the matching framework/APEX jars are present in system/system_ext/APEX.",
                pb_found[0],
            ))
        else:
            findings.append(Finding(
                KEY, "classpath-ok", PASS,
                f"All {cp_checked} BOOTCLASSPATH & SYSTEMSERVERCLASSPATH jars resolve",
                f"Verified against {len(pb_found)} protobuf manifest(s).",
            ))
    else:
        cp_status = "WARN"
        cp_checks.append("no classpath.pb manifests parsed")
    art_oat = (ctx.shared.get("runtime") or {}).get("art_oat") or {}
    if art_oat.get("vdex_mismatches"):
        cp_status = "BLOCKER"
        cp_checks.append(f"{len(art_oat['vdex_mismatches'])} stale .vdex/.odex checksum mismatch(es)")
    elif art_oat.get("vdex_checked"):
        cp_checks.append(f"ART .vdex ok ({art_oat['vdex_checked']} pairs)")
    phases.append({
        "phase": "derive_classpath",
        "label": "5. derive_classpath & ART",
        "status": cp_status,
        "checks": cp_checks,
    })

    # ── Phase 6: Zygote -> SystemServer Bring-Up ──
    ss_checks = []
    ss_status = "PASS"
    has_zygote = any(s.name.startswith("zygote") for s in (init_model.started_services if init_model else []))
    has_services_jar = vfs.is_file("/system/framework/services.jar")
    has_fw_res = vfs.is_file("/system/framework/framework-res.apk")
    has_art_runtime = vfs.is_file("/system/lib64/libandroid_runtime.so") or vfs.is_file("/system/lib/libandroid_runtime.so")

    if not has_art_runtime:
        ss_status = "BLOCKER"
        ss_checks.append("libandroid_runtime.so missing")
        findings.append(Finding(
            KEY, "no-android-runtime", BLOCKER,
            "libandroid_runtime.so is missing",
            "Zygote cannot start the Android runtime or fork system_server without libandroid_runtime.so.",
            "Restore /system/lib64/libandroid_runtime.so from a matching system.",
        ))
    else:
        ss_checks.append("libandroid_runtime.so present")

    if readiness:
        met = readiness.get("met", 0)
        tot = readiness.get("total", 0)
        ss_checks.append(f"system_server prereqs: {met}/{tot}")
        if not readiness.get("ready"):
            ss_status = "BLOCKER"
    elif not (has_zygote and has_services_jar and has_fw_res):
        ss_status = "BLOCKER"

    core_fw_blockers = ctx.shared.get("core_framework_blockers") or []
    if core_fw_blockers:
        ss_status = "BLOCKER"
        ss_checks.extend(core_fw_blockers)

    hw_disp = ((ctx.shared.get("hardware") or {}).get("subsystems") or {}).get("display") or {}
    if hw_disp.get("status") == "fail":
        ss_status = "BLOCKER"
        ss_checks.append(f"Display/Graphics FAIL ({hw_disp.get('detail', 'missing core graphics HAL')})")
    elif hw_disp.get("status") == "ok":
        ss_checks.append("Display/Graphics 5-layer stack OK")

    dex_info = ctx.shared.get("dex_bytecode") or {}
    if dex_info:
        st = dex_info.get("stats") or {}
        if dex_info.get("has_blocker"):
            ss_status = "BLOCKER"
            parts = []
            if dex_info.get("verify_errors"):
                parts.append(f"{dex_info['verify_errors']} VerifyError")
            if dex_info.get("missing_superclasses"):
                parts.append(f"{dex_info['missing_superclasses']} NoClassDef")
            if dex_info.get("missing_methods"):
                parts.append(f"{dex_info['missing_methods']} NoSuchMethod")
            if dex_info.get("missing_fields"):
                parts.append(f"{dex_info['missing_fields']} NoSuchField")
            if dex_info.get("null_derefs"):
                parts.append(f"{dex_info['null_derefs']} NPE")
            if dex_info.get("missing_framework_prefixes"):
                parts.append(f"{dex_info['missing_framework_prefixes']} OEM-API-mismatch")
            ss_checks.append("DEX FAIL: " + (", ".join(parts) or "bytecode blocker"))
        else:
            ss_checks.append(
                f"DEX clean ({st.get('target_methods_verified', 0)} methods, "
                f"{st.get('overlays_checked', 0)} overlays)"
            )

    phases.append({
        "phase": "zygote_system_server",
        "label": "6. Zygote -> SystemServer bring-up",
        "status": ss_status,
        "checks": ss_checks,
    })

    overall_blocker = any(p["status"] == "BLOCKER" for p in phases)
    overall_warn = any(p["status"] == "WARN" for p in phases)
    verdict = (
        "BLOCKED before SystemServer" if overall_blocker
        else "Reaches SystemServer with warnings" if overall_warn
        else "Simulated clean up to SystemServer"
    )

    ctx.shared["boot_simulation"] = {
        "reaches_system_server": not overall_blocker,
        "verdict": verdict,
        "phases": phases,
    }
    ctx.shared["boot_trace"] = _build_boot_trace(ctx, phases)

    findings.append(Finding(
        KEY,
        "sim-verdict",
        BLOCKER if overall_blocker else (WARN if overall_warn else PASS),
        f"Boot simulation: {verdict}",
        " → ".join(f"{p['label']} [{p['status']}]" for p in phases),
    ))
    return findings


def _build_boot_trace(ctx, phases) -> list[dict]:
    """Assemble a complete chronological Android boot trace from all 16 stages' verified data."""
    trace: list[dict] = []

    def _emit(phase: str, pid: str, level: str, tag: str, msg: str, detail: str = "", path: str = "", verbose: bool = True):
        trace.append({
            "phase": phase,
            "pid": pid,
            "tid": pid,
            "level": level,
            "tag": tag,
            "msg": msg,
            "detail": detail,
            "path": path,
            "stage": "boot_trace",
            "code": f"trace-{phase}",
            "verbose": verbose,
        })

    # 0. Kernel KMI / IKCFG / .ko & VINTF HAL inventory trace
    khw = ctx.shared.get("kernel_hardware") or {}
    kcfg = ctx.shared.get("kernel_config") or {}
    if khw.get("ko_total") or kcfg.get("total"):
        kmi = khw.get("kmi") or {}
        fw = khw.get("firmware") or {}
        crcs = kmi.get("kmi_crcs") or ()
        _emit(
            "kernel", "0", "E" if (len(crcs) > 1 or kmi.get("sym_mismatches")) else "I", "kernel",
            f"Kernel inventory: {khw.get('ko_total', 0)} .ko modules · "
            f"KMI CRC={', '.join(crcs[:2]) or 'n/a'} · "
            f"IKCFG={kcfg.get('total', 0)} opts · "
            f"DTB↔.ko={len(khw.get('matched_of_compatibles') or ())} matched · "
            f"fw={fw.get('found', 0)}/{fw.get('declared', 0)}",
            "\n".join(
                [f"module_layout CRCs: {', '.join(crcs) or 'n/a'}",
                 f"exported symbol CRCs: {kmi.get('exported_symbols', 0)} (mismatches={kmi.get('sym_mismatches', 0)})"]
                + ([f"matched DTB compatibles: {', '.join((khw.get('matched_of_compatibles') or ())[:24])}"]
                   if khw.get("matched_of_compatibles") else [])
            ),
            "/vendor_dlkm/lib/modules",
        )

    vintf_provided = (ctx.shared.get("vintf") or {}).get("provided_hals") or {}
    if vintf_provided:
        hal_lines = []
        for hname in sorted(vintf_provided.keys()):
            hinfo = vintf_provided[hname]
            fmts = "/".join(sorted(hinfo.get("formats") or ())) or "?"
            vers = ", ".join(sorted(hinfo.get("versions") or ())) or "default"
            fq = ", ".join(sorted(hinfo.get("fqnames") or ())[:4])
            hal_lines.append(f"  {hname} [{fmts}] v={vers}" + (f" ({fq})" if fq else ""))
        _emit(
            "vintf", "1", "I", "vintf",
            f"Device VINTF manifest inventory: {len(vintf_provided)} declared HAL(s)",
            "\n".join(hal_lines),
            "/vendor/etc/vintf",
        )

    # 1. Property load order & overrides (property_load_boot_defaults)
    prop_trace = (ctx.profile or {}).get("prop_trace") or {}
    for pf in prop_trace.get("files") or ():
        ov = pf.get("overrides") or ()
        ov_txt = ""
        if ov:
            ov_txt = "\n".join(f"override {k}: '{old}' -> '{new}'" for k, old, new in ov)
        _emit(
            "props", "1", "I", "init",
            f"property_load_boot_defaults: {pf['path']} ({pf['count']} props"
            + (f", {len(ov)} override(s)" if ov else "") + ")",
            ov_txt, pf["path"],
        )
    ro_shadows = [c for c in (prop_trace.get("cross_conflicts") or ()) if c.get("key", "").startswith("ro.")]
    if ro_shadows:
        _emit(
            "props", "1", "I", "init",
            f"Read-only property precedence: {len(ro_shadows)} cross-partition ro.* shadow(s)",
            "\n".join(
                f"{c['key']}={c['winner_val']} ({c['winner_file']}) shadows '{c['ignored_val']}' ({c['ignored_file']})"
                for c in ro_shadows
            ),
        )

    # 2. SELinux CIL & Binder service_contexts trace
    cil = ctx.shared.get("selinux_cil") or {}
    if cil.get("files"):
        _emit(
            "selinux", "1", "I", "selinux",
            f"secilc assembly: {len(cil['files'])} CIL file(s) · {cil.get('type_count', 0)} types · {cil.get('attr_count', 0)} attributes",
            "\n".join(cil["files"]),
        )
    avc = ctx.shared.get("avc") or {}
    svc_domains = avc.get("service_domains") or {}
    for bc in avc.get("binder_checked") or ():
        lvl = "W" if bc.get("default_fallback") else "I"
        _emit(
            "binder", "232", lvl, "servicemanager",
            f"HAL context: {bc['service']} -> {bc['context']} (clients: {', '.join(bc.get('clients') or ())})",
            f"source={bc.get('source', '')}",
        )

    # 3. Chronological init.rc trigger execution + started services with SELinux domains
    init_info = ctx.shared.get("init") or {}
    init_model = init_info.get("model")
    timeline = init_info.get("trigger_timeline") or ()
    started_by_trig = {}
    if init_model is not None:
        for svc in getattr(init_model, "started_services", None) or ():
            for tr in sorted(svc.started_by):
                started_by_trig.setdefault(tr, []).append(svc)
    for item in timeline:
        trig = item["trigger"]
        parts = []
        if item.get("mount_all"):
            parts.append("mount_all(" + ", ".join(item["mount_all"]) + ")")
        if item.get("class_starts"):
            parts.append("class_start(" + ", ".join(item["class_starts"]) + ")")
        if item.get("explicit_starts"):
            parts.append("start(" + ", ".join(item["explicit_starts"][:8]) + (f" +{len(item['explicit_starts'])-8}" if len(item["explicit_starts"]) > 8 else "") + ")")
        if item.get("execs"):
            parts.append(f"{len(item['execs'])} exec(s)")
        svcs = started_by_trig.get(trig) or []
        if not parts and not svcs:
            continue
        detail_lines = []
        for ex in item.get("execs") or ():
            detail_lines.append(f"  {ex}")
        for svc in sorted(svcs, key=lambda s: s.name):
            dom = svc_domains.get(svc.name) or (svc.seclabel or "?")
            flags = []
            if svc.critical:
                flags.append("critical")
            if svc.oneshot:
                flags.append("oneshot")
            if svc.klass:
                flags.append(f"class={svc.klass}")
            f_str = f" [{' '.join(flags)}]" if flags else ""
            detail_lines.append(f"  service {svc.name} -> {svc.path} (domain={dom}){f_str}")
        _emit(
            "init_trigger", "1", "I", "init",
            f"on {trig}: " + (", ".join(parts) if parts else f"{len(svcs)} service(s)")
            + (f" -> starts {len(svcs)} service(s)" if svcs and parts else ""),
            "\n".join(detail_lines),
        )

    # 4. 5-layer Graphics stack & Hardware subsystems
    hw_subs = (ctx.shared.get("hardware") or {}).get("subsystems") or {}
    disp = hw_subs.get("display") or {}
    gstack = disp.get("graphics_stack") or {}
    if gstack:
        _emit(
            "graphics", "400", "E" if disp.get("status") == "fail" else ("W" if disp.get("status") == "warn" else "I"),
            "SurfaceFlinger",
            f"Graphics 5-layer trace: EGL={gstack.get('egl_prop') or '?'} · Vulkan={gstack.get('vulkan_prop') or '?'} · "
            f"HWC={'OK' if gstack.get('has_composer') else 'MISSING'} · "
            f"Allocator={'OK' if gstack.get('has_allocator') else 'MISSING'} · "
            f"Mapper={'OK' if gstack.get('has_mapper') else 'MISSING'}",
            "\n".join([
                f"EGL driver: {gstack.get('egl_resolved') or '(none)'}",
                f"Vulkan ICDs: {', '.join(gstack.get('vulkan_libs') or ()) or '(none)'}",
                f"Composer services: {', '.join(gstack.get('composer_services') or ()) or '(none)'}",
                f"Allocator services: {', '.join(gstack.get('allocator_services') or ()) or '(none)'}",
                f"Mapper libs: {', '.join(gstack.get('mapper_libs') or ()) or '(none)'} (VINTF 5.0: {', '.join(gstack.get('mapper5_suffixes') or ()) or 'none'})",
            ]),
            "/vendor/lib64/egl",
        )
    for skey in ("audio", "camera_media", "ril", "connectivity", "biometrics", "kernel_drivers", "ueventd"):
        sinfo = hw_subs.get(skey)
        if sinfo and sinfo.get("status") not in (None, "na"):
            st = sinfo["status"]
            _emit(
                "hardware", "260",
                "E" if st == "fail" else ("W" if st == "warn" else "I"),
                "hwservicemanager",
                f"Subsystem [{skey}]: {st.upper()} — {sinfo.get('detail', '')}",
            )

    # 5. Native C++ daemon .dynsym audit summary
    daemons = (ctx.shared.get("linker") or {}).get("daemon_symbol_audit") or ()
    if daemons:
        bad_d = [d for d in daemons if d.get("missing_symbols")]
        ok_d = [d for d in daemons if d.get("complete") and not d.get("missing_symbols")]
        partial_d = [d for d in daemons if not d.get("complete")]
        _emit(
            "linker", "248", "E" if bad_d else "I", "linker",
            f"C++ .dynsym audit: {len(ok_d)}/{len(daemons)} boot/HAL daemon(s) cleanly resolved"
            + (f" ({len(partial_d)} partial closure)" if partial_d else ""),
            "\n".join(
                f"{'[FAIL]   ' if d.get('missing_symbols') else ('[OK]     ' if d.get('complete') else '[PARTIAL]')} "
                f"{d.get('binary') or d.get('path') or '?'} ({d.get('tier')}, {d.get('libs_scanned', 0)} libs)"
                + (f" -> missing {d.get('missing_count', len(d['missing_symbols']))}: {', '.join(d['missing_symbols'])}"
                   if d.get("missing_symbols") else "")
                for d in daemons
            ),
        )

    # 6. ART .vdex/.odex + Full un-truncated DEX bytecode details
    art_oat = (ctx.shared.get("runtime") or {}).get("art_oat") or {}
    if art_oat.get("vdex_checked"):
        mm = art_oat.get("vdex_mismatches") or ()
        _emit(
            "art", "412", "E" if mm else "I", "artd",
            f"ART oat/vdex audit: {art_oat['vdex_checked']} pair(s) checked ({art_oat.get('vdex_stripped_ok', 0)} stripped, {len(mm)} mismatch)",
            "\n".join(mm) if mm else (f"Boot .art images: {', '.join(art_oat.get('boot_images') or ())}" if art_oat.get("boot_images") else ""),
        )

    dex_full = (ctx.shared.get("dex_bytecode") or {}).get("full_errors") or {}
    for err_key, err_label in (
        ("verify_errors", "Dalvik VerifyError (full list)"),
        ("null_derefs", "Guaranteed NullPointerException (full list)"),
        ("missing_superclasses", "NoClassDefFoundError superclasses (full list)"),
        ("missing_methods", "NoSuchMethodError calls (full list)"),
        ("missing_fields", "NoSuchFieldError accesses (full list)"),
        ("incompatible_changes", "AbstractMethodError / ICCE (full list)"),
        ("missing_res", "Missing framework-res.apk resource IDs (full list)"),
    ):
        items = dex_full.get(err_key) or ()
        if not items:
            continue
        lines = []
        for it in items:
            if isinstance(it, dict):
                bp = "[BOOT-PATH] " if it.get("in_boot_service") else ""
                cls = it.get("class_name") or ""
                mth = it.get("method") or ""
                tgt = it.get("target") or it.get("detail") or ""
                kind = f"[{it['kind']}] " if it.get("kind") else ""
                lines.append(f"{bp}{kind}{cls}{('.' + mth) if mth else ''} -> {tgt}")
            else:
                lines.append(str(it))
        boot_n = sum(1 for it in items if isinstance(it, dict) and it.get("in_boot_service"))
        _emit(
            "dex_full", "724", "E" if (err_key == "verify_errors" or boot_n > 0) else "W", "art",
            f"{err_label}: {len(items)} item(s)" + (f" ({boot_n} in SystemServer/SystemUI boot path)" if boot_n else ""),
            "\n".join(lines),
        )

    return trace
