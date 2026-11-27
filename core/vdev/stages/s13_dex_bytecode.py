# SPDX-License-Identifier: MPL-2.0
"""Pure-Rust DEX/VDEX bytecode verifier, cross-classpath linkage checker,
null-flow analyzer, and RRO overlay / resources.arsc compatibility auditor."""
from __future__ import annotations

import json
import posixpath

from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "dex_bytecode"
LABEL = "DEX bytecode & overlays"
REQUIRES = ("system",)

_MAX_LIST = 8

_FRAMEWORK_DIRS = (
    "/system/framework",
    "/system_ext/framework",
    "/product/framework",
    "/vendor/framework",
    "/odm/framework",
)

_CLASSPATH_FILES = (
    "/system/etc/classpaths/bootclasspath.pb",
    "/system/etc/classpaths/systemserverclasspath.pb",
)

_CORE_APEX_HINTS = (
    "com.android.art",
    "com.android.conscrypt",
    "com.android.i18n",
    "com.android.os.statsd",
    "com.android.sdkext",
    "com.android.tethering",
    "com.android.wifi",
    "com.android.btservices",
    "com.android.permission",
    "com.android.mediaprovider",
    "com.android.appsearch",
    "com.android.scheduling",
    "com.android.uwb",
    "com.android.adservices",
    "com.android.configinfrastructure",
    "com.android.devicelock",
    "com.android.healthfitness",
    "com.android.ondevicepersonalization",
)


def _sibling_vdex_paths(vpath):
    """Return candidate .vdex paths corresponding to a .jar or .apk vpath."""
    parent = posixpath.dirname(vpath)
    stem = posixpath.splitext(posixpath.basename(vpath))[0]
    out = [
        posixpath.join(parent, "oat", "arm64", f"{stem}.vdex"),
        posixpath.join(parent, "oat", "arm", f"{stem}.vdex"),
        posixpath.join(parent, "arm64", f"boot-{stem}.vdex"),
        posixpath.join(parent, f"boot-{stem}.vdex"),
    ]
    return out


def _bucket_nodes(vfs, items):
    """Group (label, vpath, extra...) into host / erofs / ext / bytes buckets."""
    by_host = []
    by_erofs = {}
    by_ext = {}
    by_bytes = []

    for item in items:
        label, vp = item[0], item[1]
        extra = item[2:]
        node = vfs.lookup(vp)
        if node is None or not node.is_file:
            continue
        if node.host:
            by_host.append((label, node.host) + extra)
        elif getattr(node.source, "native_handle", None) is not None:
            fs_t = node.source.fs_type
            if fs_t == "EROFS":
                by_erofs.setdefault(id(node.source), (node.source.native_handle, []))[1].append(
                    (label, node.rel) + extra
                )
            elif fs_t == "EXT":
                by_ext.setdefault(id(node.source), (node.source.native_handle, []))[1].append(
                    (label, node.rel) + extra
                )
            else:
                by_bytes.append((label, vp) + extra)
        else:
            by_bytes.append((label, vp) + extra)
    return by_host, by_erofs, by_ext, by_bytes


def _add_providers(verifier, vfs, paths):
    by_host, by_erofs, by_ext, by_bytes = _bucket_nodes(vfs, [(p, p) for p in paths])
    empty = []
    if by_host:
        empty.extend(verifier.add_provider_files(by_host))
    for handle, rels in by_erofs.values():
        empty.extend(verifier.add_provider_erofs(handle, rels))
    for handle, rels in by_ext.values():
        empty.extend(verifier.add_provider_ext(handle, rels))
    for label, vp in by_bytes:
        raw = vfs.read_bytes(vp)
        if not raw or verifier.add_provider_bytes(label, raw) == 0:
            empty.append(label)
    # Only touch sibling .vdex for stripped (dexpreopt) jars that had 0 embedded DEX
    if empty:
        vdex_fallbacks = []
        for orig_vp in empty:
            for vdex_vp in _sibling_vdex_paths(orig_vp):
                if vfs.is_file(vdex_vp):
                    vdex_fallbacks.append(vdex_vp)
                    break
        if vdex_fallbacks:
            vh, ve, vx, vb = _bucket_nodes(vfs, [(p, p) for p in vdex_fallbacks])
            if vh:
                verifier.add_provider_files(vh)
            for handle, rels in ve.values():
                verifier.add_provider_erofs(handle, rels)
            for handle, rels in vx.values():
                verifier.add_provider_ext(handle, rels)
            for label, vp in vb:
                raw = vfs.read_bytes(vp)
                if raw:
                    verifier.add_provider_bytes(label, raw)


def _add_targets(verifier, vfs, items):
    """items: list of (label, vpath, is_core_target). Batched in parallel per source."""
    loaded_targets = []
    missing_dex = []

    by_host, by_erofs, by_ext, by_bytes = _bucket_nodes(vfs, items)
    if by_host:
        ok, miss = verifier.add_target_files(by_host)
        loaded_targets.extend(ok)
        missing_dex.extend(miss)
    for handle, rels in by_erofs.values():
        ok, miss = verifier.add_target_erofs(handle, rels)
        loaded_targets.extend(ok)
        missing_dex.extend(miss)
    for handle, rels in by_ext.values():
        ok, miss = verifier.add_target_ext(handle, rels)
        loaded_targets.extend(ok)
        missing_dex.extend(miss)
    for lbl, vp, bc in by_bytes:
        raw = vfs.read_bytes(vp)
        cnt = verifier.add_target_bytes(lbl, raw, bc) if raw else 0
        if cnt > 0:
            loaded_targets.append(lbl)
        else:
            missing_dex.append((lbl, bc))

    # Fallback to sibling .vdex ONLY for stripped (dexpreopt) targets
    for orig_vp, bc in missing_dex:
        for vdex_vp in _sibling_vdex_paths(orig_vp):
            if vfs.is_file(vdex_vp):
                raw = vfs.read_bytes(vdex_vp)
                if raw and verifier.add_target_bytes(orig_vp, raw, bc) > 0:
                    loaded_targets.append(orig_vp + " (vdex)")
                    break

    return loaded_targets


def _add_arsc_targets(verifier, vfs, pkg_to_vp):
    items = [(pkg, vp) for pkg, vp in pkg_to_vp.items() if vp]
    by_host, by_erofs, by_ext, by_bytes = _bucket_nodes(vfs, items)
    if by_host:
        verifier.add_arsc_target_files(by_host)
    for handle, rels in by_erofs.values():
        verifier.add_arsc_target_erofs(handle, rels)
    for handle, rels in by_ext.values():
        verifier.add_arsc_target_ext(handle, rels)
    # bytes fallback not usually needed for framework-res/SystemUI


def _add_overlays(verifier, vfs, overlay_vps):
    items = [(vp, vp) for vp in overlay_vps]
    by_host, by_erofs, by_ext, _by_bytes = _bucket_nodes(vfs, items)
    if by_host:
        verifier.add_overlays_files(by_host)
    for handle, rels in by_erofs.values():
        verifier.add_overlays_erofs(handle, rels)
    for handle, rels in by_ext.values():
        verifier.add_overlays_ext(handle, rels)


def _collect_classpath_jars(ctx):
    vfs = ctx.vfs
    apex = ctx.apex
    jar_paths = []
    seen = set()

    def _add(p):
        if p and p not in seen:
            seen.add(p)
            jar_paths.append(p)

    # 1. All JARs from classpath.pb (system + APEX)
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
            try:
                for item in parse_pb(raw):
                    _add(item.get("path", ""))
            except Exception:
                pass
    for item in (getattr(apex, "classpath_jars", None) or ()):
        _add(item.get("path", ""))

    # 2. All JARs inside framework directories (picks up OEM jars across system/vendor/product/odm + OEM partitions like tr_*, my_*, mi_ext)
    fw_dirs = list(_FRAMEWORK_DIRS)
    try:
        for root_ent in sorted(vfs.listdir("/") or ()):
            cand = f"/{root_ent}/framework"
            if cand not in fw_dirs and vfs.is_dir(cand):
                fw_dirs.append(cand)
    except Exception:
        pass
    for fdir in fw_dirs:
        if not vfs.is_dir(fdir):
            continue
        for fname in vfs.listdir(fdir):
            if fname.endswith(".jar"):
                _add(posixpath.join(fdir, fname))

    # 3. Core ART/conscrypt/i18n APEX javalib JARs if not already in classpath_jars
    for pkg, files in (getattr(apex, "files", None) or {}).items():
        if any(pkg.startswith(h) for h in _CORE_APEX_HINTS):
            for rel in files:
                if rel.startswith("javalib/") and rel.endswith(".jar"):
                    _add(f"/apex/{pkg}/{rel}")

    return jar_paths


def run(ctx):
    if ctx.options.get("dex_verify") is False:
        return [Finding(KEY, "skipped", INFO, "DEX bytecode verification disabled by option")]

    try:
        from core import native_backend
        if native_backend._get_native() is None:
            return [Finding(KEY, "no-native", INFO, "Native Rust DEX verifier unavailable")]
        verifier = native_backend.vdev_dex_verifier()
    except Exception as exc:
        return [Finding(KEY, "init-failed", WARN, f"DEX verifier unavailable: {exc}")]

    vfs = ctx.vfs
    apks = ctx.shared.get("apks") or []
    findings = []

    # 1. Identify verification targets (services.jar + OEM services + SystemUI APKs)
    target_items = []
    target_vps = set()

    def _add_target(vp, boot_critical):
        if vp and vp not in target_vps and vfs.is_file(vp):
            target_vps.add(vp)
            target_items.append((vp, vp, boot_critical))

    _add_target("/system/framework/services.jar", True)
    for fdir in ("/system/framework", "/system_ext/framework", "/product/framework"):
        if not vfs.is_dir(fdir):
            continue
        for fname in vfs.listdir(fdir):
            low = fname.lower()
            if low.endswith(".jar") and ("services" in low or "service-" in low):
                _add_target(posixpath.join(fdir, fname), low == "services.jar")

    for a in apks:
        pkg = (a.package or "").lower()
        name_low = posixpath.basename(a.vpath).lower()
        if (
            pkg == "com.android.systemui"
            or ("systemui" in name_low and "plugin" not in name_low and "/overlay/" not in a.vpath)
        ):
            _add_target(a.vpath, True)

    # 2. Collect all classpath provider JARs (excluding targets which get indexed on add_target)
    ctx.note("Indexing DEX classpath…")
    all_jars = _collect_classpath_jars(ctx)
    provider_jars = [p for p in all_jars if p not in target_vps]

    # Also include APK-based shared libraries referenced via <uses-library> by targets
    # (e.g., Xiaomi HyperOS com.miui.system -> /system_ext/app/miuisystem/miuisystem.apk)
    apk_by_pkg = {a.package: a.vpath for a in apks if a.package and not a.overlay_target}
    target_apks = [a for a in apks if a.vpath in target_vps]
    for ta in target_apks:
        for lib_name, _req in (getattr(ta, "libraries", None) or ()):
            lib_vp = apk_by_pkg.get(lib_name)
            if lib_vp and lib_vp not in target_vps and lib_vp not in provider_jars:
                provider_jars.append(lib_vp)

    # Separate APEX JARs (which must be read via VFS/ApexSource bytes) from partition JARs
    part_providers = [p for p in provider_jars if not p.startswith("/apex/")]
    apex_providers = [p for p in provider_jars if p.startswith("/apex/")]

    _add_providers(verifier, vfs, part_providers)

    # Load APEX classpath JARs (ART core-oj, core-libart, framework-*, etc.)
    for ap_vp in apex_providers:
        raw = vfs.read_bytes(ap_vp)
        if raw:
            verifier.add_provider_bytes(ap_vp, raw)

    # 3. Load targets for deep verification
    ctx.note("Verifying DEX bytecode…")
    loaded_targets = _add_targets(verifier, vfs, target_items)

    # 4. Load resources.arsc for framework-res + all overlay target packages
    pkg_to_vp = {}
    if vfs.is_file("/system/framework/framework-res.apk"):
        pkg_to_vp["android"] = "/system/framework/framework-res.apk"
    for a in apks:
        if a.package and not a.overlay_target and a.package not in pkg_to_vp:
            pkg_to_vp[a.package] = a.vpath

    overlay_vps = []
    needed_arsc_pkgs = {"android"}
    for a in apks:
        if a.overlay_target or "/overlay/" in a.vpath:
            overlay_vps.append(a.vpath)
            if a.overlay_target:
                needed_arsc_pkgs.add(a.overlay_target)

    arsc_load_map = {pkg: pkg_to_vp[pkg] for pkg in needed_arsc_pkgs if pkg in pkg_to_vp}
    if arsc_load_map:
        _add_arsc_targets(verifier, vfs, arsc_load_map)
    if overlay_vps:
        ctx.note("Auditing RRO overlays…")
        _add_overlays(verifier, vfs, overlay_vps)

    installed_pkgs = sorted({a.package for a in apks if a.package} | {"android"})
    enabled_features = _collect_enabled_features(ctx)
    rom_props = {str(k): str(v) for k, v in ((ctx.profile or {}).get("props") or {}).items()}

    # 5. Run full parallel Rust verification (with ROM features + props for dead-branch & static-string evaluation)
    try:
        raw_json = verifier.verify_json(installed_pkgs, enabled_features, rom_props)
    except TypeError:
        raw_json = verifier.verify_json(installed_pkgs)
    report = json.loads(raw_json)
    stats = report.get("stats") or {}

    verify_errors = report.get("verify_errors") or []
    null_derefs = report.get("null_deref_risks") or []
    missing_supers = report.get("missing_superclasses") or []
    missing_methods = report.get("missing_methods") or []
    missing_fields = report.get("missing_fields") or []
    incompat = report.get("incompatible_changes") or []
    missing_by_pfx = report.get("missing_ref_classes_by_prefix") or {}
    missing_res = report.get("missing_framework_res_ids") or []
    overlay_issues = report.get("overlay_issues") or []
    logic_map = report.get("logic_map") or {}

    # Build exact set of boot-path class descriptors from this ROM's own extracted SystemServer startup services
    boot_service_descs = {
        "Lcom/android/server/SystemServer;",
        "Lcom/android/systemui/SystemUIApplication;",
        "Lcom/android/systemui/SystemUIFactory;",
        "Lcom/android/systemui/SystemUIService;",
        "Lcom/android/internal/os/ZygoteInit;",
    }
    for _svc_ent in (logic_map.get("system_services") or ()):
        if _svc_ent.get("dead_branch"):
            continue
        _sv = str(_svc_ent.get("value") or "").strip()
        if _sv:
            boot_service_descs.add("L" + _sv.replace(".", "/") + ";")

    def _is_boot_path_item(item: dict) -> bool:
        if item.get("boot_critical"):
            return True
        cn = str(item.get("class_name") or "")
        if cn in boot_service_descs:
            return True
        # Also match outer class of inner classes inside live SystemServer boot services
        if "$" in cn and (cn.split("$", 1)[0] + ";") in boot_service_descs:
            return True
        return False

    for _lst in (null_derefs, missing_supers, missing_methods, missing_fields, incompat):
        for _it in _lst:
            _it["in_boot_service"] = _is_boot_path_item(_it)

    # Summary info finding
    findings.append(Finding(
        KEY, "dex-summary", INFO,
        f"DEX verified {stats.get('target_jars', 0)} core JAR/APK(s): "
        f"{stats.get('target_methods_verified', 0)} methods, "
        f"{stats.get('insns_verified', 0):,} bytecode units "
        f"(classpath: {stats.get('provider_classes', 0):,} classes, "
        f"{stats.get('overlays_checked', 0)} overlays)",
        "Targets: " + (", ".join(loaded_targets[:6]) if loaded_targets else "none")
        + (f" · vdex extracted: {stats.get('vdex_extracted', 0)}" if stats.get("vdex_extracted") else ""),
    ))

    has_blocker = False

    # 5a. Dalvik VerifyError (broken Smali / register bounds / return mismatch / bad branch)
    if verify_errors:
        has_blocker = True
        sample = "; ".join(
            f"{e['class_name']}.{e['method']} @pc={e['pc']}: {e['detail']}"
            for e in verify_errors[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "dex-verify-error", BLOCKER,
            f"{len(verify_errors)} Dalvik VerifyError(s) in core bytecode",
            sample + ". ART rejects classes that fail bytecode verification, "
            "causing immediate VerifyError crashes during boot or SystemUI bring-up.",
            "Fix the broken Smali registers, return opcode, branch target, or try/catch block "
            "and recompile the DEX cleanly.",
            verify_errors[0].get("source", ""),
        ))

    # 5b. Guaranteed straight-line NullPointerException
    boot_nulls = [e for e in null_derefs if e.get("in_boot_service")]
    if null_derefs:
        sev = BLOCKER if boot_nulls else WARN
        if sev == BLOCKER:
            has_blocker = True
        picked = boot_nulls if boot_nulls else null_derefs
        sample = "; ".join(
            f"{e['class_name']}.{e['method']} -> {e['target']} ({e['detail']})"
            for e in picked[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "dex-null-deref", sev,
            f"{len(null_derefs)} guaranteed NullPointerException path(s) detected"
            + (f" ({len(boot_nulls)} in boot-path class)" if boot_nulls else ""),
            sample + ". A register explicitly set to null (const/4 0) is dereferenced "
            "in the same basic block without a null check.",
            "Inspect the patched Smali/Java method and restore object initialization or add an if-eqz null guard.",
            picked[0].get("source", ""),
        ))

    # 5c. Missing superclass / interface (NoClassDefFoundError at class load)
    if missing_supers:
        _CORE_SUPER_PFX = ("Landroid/", "Lcom/android/", "Ljava/")
        core_supers = [
            e for e in missing_supers
            if e.get("in_boot_service")
            and str(e.get("target", "")).startswith(_CORE_SUPER_PFX)
        ]
        sev = BLOCKER if core_supers else WARN
        if sev == BLOCKER:
            has_blocker = True
        picked = core_supers if core_supers else missing_supers
        sample = "; ".join(
            f"{e['class_name']} extends/implements {e['target']} ({posixpath.basename(e['source'])})"
            for e in picked[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "dex-missing-superclass", sev,
            f"{len(missing_supers)} class(es) inherit from missing framework class/interface (NoClassDefFoundError)"
            + (f" ({len(core_supers)} in boot path)" if core_supers else ""),
            sample + ". ART cannot define or load these classes when their parent class/interface "
            "is absent from BOOTCLASSPATH/SYSTEMSERVERCLASSPATH.",
            "Match services.jar / SystemUI.apk with the framework.jar and OEM framework JARs from the same ROM base.",
            picked[0].get("source", ""),
        ))

    # 5d. NoSuchMethodError on resolved classes
    if missing_methods:
        boot_m = [e for e in missing_methods if e.get("in_boot_service")]
        aosp_m = [e for e in missing_methods if str(e.get("target", "")).startswith(("Landroid/", "Lcom/android/", "Ljava/"))]
        sev = BLOCKER if (boot_m or len(aosp_m) >= 25) else WARN
        if sev == BLOCKER:
            has_blocker = True
        picked = boot_m if boot_m else (aosp_m if len(aosp_m) >= 25 else missing_methods)
        sample = "; ".join(
            f"{e['class_name']}.{e['method']} calls {e['target']}"
            for e in picked[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "dex-missing-method", sev,
            f"{len(missing_methods)} unresolved method call(s) on existing framework classes (NoSuchMethodError)"
            + (f" ({len(boot_m)} in boot path)" if boot_m else ""),
            sample + ". The target class exists in this ROM's classpath, but the called method "
            "or signature is absent — typical of mismatched SystemUI / services.jar / framework.jar.",
            "Port the missing method stub into framework.jar or align services.jar & SystemUI.apk to the same framework base.",
            picked[0].get("source", ""),
        ))

    # 5e. NoSuchFieldError on resolved classes
    if missing_fields:
        boot_f = [e for e in missing_fields if e.get("in_boot_service")]
        sev = BLOCKER if (boot_f or len(missing_fields) >= 25) else WARN
        if sev == BLOCKER:
            has_blocker = True
        picked = boot_f if boot_f else missing_fields
        sample = "; ".join(
            f"{e['class_name']}.{e['method']} accesses {e['target']}"
            for e in picked[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "dex-missing-field", sev,
            f"{len(missing_fields)} unresolved field access(es) on existing classes (NoSuchFieldError)"
            + (f" ({len(boot_f)} in boot path)" if boot_f else ""),
            sample + ". Accessing a missing field on a loaded framework class throws NoSuchFieldError at runtime.",
            "Restore the field in framework.jar or match the APK/JAR version to the framework.",
            picked[0].get("source", ""),
        ))

    # 5f. AbstractMethodError / IncompatibleClassChangeError
    incompat_filtered = [
        e for e in incompat
        if not (e.get("kind") == "AbstractMethodError" and "/jarjar/kotlin/" in e.get("class_name", ""))
    ]
    if incompat_filtered:
        icce_or_boot = [
            e for e in incompat_filtered
            if e.get("kind") == "IncompatibleClassChangeError" or e.get("in_boot_service")
        ]
        sev = BLOCKER if icce_or_boot else WARN
        if sev == BLOCKER:
            has_blocker = True
        picked = icce_or_boot if icce_or_boot else incompat_filtered
        sample = "; ".join(
            f"[{e['kind']}] {e['class_name']}.{e['method']} -> {e['target']}"
            for e in picked[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "dex-incompat-change", sev,
            f"{len(incompat_filtered)} class/method ABI incompatibility error(s) (AbstractMethodError / ICCE)"
            + (f" ({len(icce_or_boot)} in boot path/ICCE)" if icce_or_boot else ""),
            sample,
            "Re-align superclass/interface signatures or static vs virtual modifiers.",
            picked[0].get("source", ""),
        ))

    # 5g. Cross-OEM / missing framework package namespaces (e.g., Samsung/Oplus/Transsion SystemUI swapped onto wrong base)
    heavy_prefixes = [
        (pfx, info) for pfx, info in missing_by_pfx.items()
        if info.get("count", 0) >= 5
    ]
    heavy_prefixes.sort(key=lambda kv: kv[1].get("count", 0), reverse=True)
    if heavy_prefixes:
        total_missing_cls = sum(i.get("count", 0) for _, i in heavy_prefixes)
        core_aosp_missing = sum(
            i.get("count", 0) for pfx, i in heavy_prefixes
            if pfx.startswith(("android.", "com.android."))
        )
        sev = BLOCKER if core_aosp_missing >= 25 else WARN
        if sev == BLOCKER:
            has_blocker = True
        detail_parts = [
            f"{pfx} ({info['count']} classes: {', '.join(info.get('sample_classes', [])[:3])})"
            for pfx, info in heavy_prefixes[:5]
        ]
        findings.append(Finding(
            KEY, "dex-oem-framework-mismatch", sev,
            f"{total_missing_cls} missing framework/OEM class reference(s) across {len(heavy_prefixes)} namespace(s)",
            "; ".join(detail_parts)
            + ". Core bytecode references an entire framework/OEM API family absent from this ROM's classpath "
            "(common when SystemUI.apk or services.jar is swapped across different ROM bases).",
            "Bring over the matching OEM framework JARs (and register them in BOOTCLASSPATH / permissions XML), "
            "or patch out the vendor API calls.",
        ))

    # 5h. Missing framework resource IDs
    if missing_res:
        sev = BLOCKER if len(missing_res) >= 10 else WARN
        if sev == BLOCKER:
            has_blocker = True
        sample = ", ".join(f"{e['target']} in {e['class_name']}" for e in missing_res[:_MAX_LIST])
        findings.append(Finding(
            KEY, "dex-missing-framework-res", sev,
            f"{len(missing_res)} framework resource ID(s) absent from framework-res.apk",
            sample + ". Hardcoded android.R.* IDs missing in framework-res.apk trigger Resources$NotFoundException.",
            "Use matching framework-res.apk or remap the resource IDs.",
        ))

    # 5i. RRO Overlay issues (corrupt, orphaned resources, static conflicts)
    corrupt_ov = [o for o in overlay_issues if o["kind"] == "corrupt-overlay"]
    orphaned_ov = [o for o in overlay_issues if o["kind"] == "orphaned-overlay-resources"]
    conflict_ov = [o for o in overlay_issues if o["kind"] == "static-overlay-conflict"]
    dup_ov = [o for o in overlay_issues if o["kind"] == "duplicate-overlay"]

    if corrupt_ov:
        findings.append(Finding(
            KEY, "overlay-corrupt", WARN,
            f"{len(corrupt_ov)} corrupt overlay APK(s)",
            ", ".join(o["apk_path"] for o in corrupt_ov[:_MAX_LIST]),
            "Replace or remove unreadable overlay APKs.",
        ))
    if orphaned_ov:
        severe_ov = [
            o for o in orphaned_ov
            if o.get("target_package") in ("android", "com.android.systemui")
            and o.get("missing_count", 0) >= 10
            and (o["missing_count"] * 100 // max(o.get("total_resources", 1), 1)) >= 60
        ]
        sev = WARN
        sample = "; ".join(
            f"{posixpath.basename(o['apk_path'])} -> {o['target_package']} "
            f"({o['missing_count']}/{o['total_resources']} missing: {', '.join(o.get('sample_missing', [])[:4])})"
            for o in orphaned_ov[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "overlay-orphaned-resources", sev,
            f"{len(orphaned_ov)} overlay APK(s) override resource names absent from target APK"
            + (f" ({len(severe_ov)} heavy framework/SystemUI mismatch)" if severe_ov else ""),
            sample + ". Crossed or mismatched RRO overlays fail to map resources in OverlayManagerService "
            "(idmap2 skips missing names, causing broken theming, status bar layout glitches, or SystemUI crash).",
            "Remove foreign-ROM overlays or remap their resource names to match the target APK's resources.arsc.",
        ))
    if conflict_ov:
        sample = "; ".join(
            f"{posixpath.basename(o['apk_path'])} ({o['detail']}: {', '.join(o.get('sample_missing', [])[:3])})"
            for o in conflict_ov[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "overlay-static-conflict", WARN,
            f"{len(conflict_ov)} static overlay resource conflict(s) at equal priority",
            sample + ". Multiple static overlays overriding the same resource at identical priority "
            "produce non-deterministic UI/config values.",
            "Remove duplicate static overlays or assign distinct android:priority values.",
        ))
    if dup_ov:
        findings.append(Finding(
            KEY, "overlay-duplicate", WARN,
            f"{len(dup_ov)} duplicate overlay package name(s)",
            "; ".join(o["detail"] for o in dup_ov[:_MAX_LIST]),
            "Keep only one copy of each overlay package across /product, /vendor, and /system_ext.",
        ))

    # 5j. Semantic DEX Logic & Dataflow Audit (SystemServer boot graph, JNI loadLibrary, Reflection, Binder/HAL, SystemProperties)
    logic_summary = _audit_logic_map(
        ctx, logic_map, installed_pkgs, findings,
        enabled_features=enabled_features, target_vps=target_vps,
    )
    if logic_summary.get("has_blocker"):
        has_blocker = True

    # 5k. Manifest Component (<application> / <service> / <provider>) -> DEX Class Cross-Verifier
    manifest_comp_audit = _audit_manifest_components(ctx, verifier, loaded_targets, findings)
    if manifest_comp_audit.get("has_blocker"):
        has_blocker = True

    if not has_blocker and not verify_errors and not missing_supers and not missing_methods and not missing_fields and not null_derefs:
        findings.append(Finding(
            KEY, "dex-clean", PASS,
            f"Core DEX bytecode & classpath linkage clean ({stats.get('target_methods_verified', 0)} methods verified)",
            f"Zero VerifyError, zero resolved NoSuchMethodError/NoSuchFieldError, zero straight-line NPE in {stats.get('elapsed_ms', 0)} ms.",
        ))

    ctx.shared["dex_bytecode"] = {
        "stats": stats,
        "has_blocker": has_blocker,
        "verify_errors": len(verify_errors),
        "null_derefs": len(null_derefs),
        "missing_superclasses": len(missing_supers),
        "missing_methods": len(missing_methods),
        "missing_fields": len(missing_fields),
        "incompatible_changes": len(incompat),
        "missing_framework_prefixes": len(heavy_prefixes),
        "overlay_issues": len(overlay_issues),
        "overlay_issue_items": overlay_issues[:30],
        "verify_error_items": verify_errors,
        "full_errors": {
            "verify_errors": verify_errors,
            "null_derefs": null_derefs,
            "missing_superclasses": missing_supers,
            "missing_methods": missing_methods,
            "missing_fields": missing_fields,
            "incompatible_changes": incompat_filtered,
            "missing_res": missing_res,
        },
        "boot_service_error_count": sum(
            1 for lst in (null_derefs, missing_supers, missing_methods, missing_fields, incompat_filtered)
            for it in lst if it.get("in_boot_service")
        ),
        "loaded_targets": loaded_targets,
        "logic_map": logic_summary,
        "manifest_components": manifest_comp_audit,
    }
    return findings


def _audit_manifest_components(ctx, verifier, loaded_targets, findings):
    """Cross-check <application>, <service>, and <provider> classes in verified APK manifests against DEX."""
    summary = {"checked": 0, "missing": [], "has_blocker": False}
    if not hasattr(verifier, "has_classes") or not loaded_targets:
        return summary

    apks = ctx.shared.get("apks") or (ctx.shared.get("framework") or {}).get("apks") or []
    if not apks:
        return summary

    loaded_set = set(loaded_targets)
    loaded_tails = {posixpath.basename(vp): vp for vp in loaded_targets}
    apk_by_vp = {a.vpath: a for a in apks if getattr(a, "vpath", "")}

    critical_missing = []
    warn_missing = []
    checked = 0

    for apk in apks:
        if not apk.vpath.endswith(".apk") or not getattr(apk, "components", None):
            continue
        target_label = None
        if apk.vpath in loaded_set:
            target_label = apk.vpath
        else:
            tail = posixpath.basename(apk.vpath)
            if tail in loaded_tails and loaded_tails[tail].endswith(".apk"):
                target_label = loaded_tails[tail]
        if not target_label:
            continue

        # Only audit <application> and <service> (and <provider> for SystemUI) to avoid stub provider noise
        comps = [
            (tag, cname) for tag, cname in apk.components
            if tag in ("application", "service") and cname
        ]
        if not comps:
            continue
        names = [cname for _tag, cname in comps]
        try:
            present = verifier.has_classes(target_label, names)
        except Exception:
            continue
        checked += len(names)
        is_sysui = (
            apk.package == "com.android.systemui"
            or "systemui" in posixpath.basename(apk.vpath).lower()
        )
        _core_sysui_services = (
            "SystemUIService", "KeyguardService", "ImageWallpaper",
            "SystemUISecondaryUserService",
        )
        for (tag, cname), ok in zip(comps, present):
            if ok:
                continue
            item = {"apk": apk.vpath, "package": apk.package, "tag": tag, "class": cname}
            summary["missing"].append(item)
            short_cls = cname.rsplit(".", 1)[-1]
            if tag == "application" or (is_sysui and short_cls in _core_sysui_services):
                critical_missing.append(item)
            else:
                warn_missing.append(item)

    summary["checked"] = checked
    if critical_missing:
        summary["has_blocker"] = True
        sample = "; ".join(
            f"<{m['tag']} {m['class']}> in {posixpath.basename(m['apk'])}"
            for m in critical_missing[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "dex-manifest-component-missing", BLOCKER,
            f"{len(critical_missing)} manifest <application>/<service> class(es) absent from DEX bytecode",
            f"{sample}. Android ActivityThread/LoadedApk will throw ClassNotFoundException when starting this core APK.",
            "Ensure classes*.dex / .vdex matches AndroidManifest.xml (do not strip or mix manifest and DEX across builds).",
            critical_missing[0]["apk"],
        ))
    elif warn_missing:
        sample = "; ".join(
            f"<{m['tag']} {m['class']}> in {posixpath.basename(m['apk'])}"
            for m in warn_missing[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "dex-manifest-component-missing", WARN,
            f"{len(warn_missing)} declared manifest <service> class(es) absent from DEX bytecode",
            f"{sample}. Binding or starting these services will throw ClassNotFoundException.",
            "Verify APK DEX files match its AndroidManifest.xml.",
            warn_missing[0]["apk"],
        ))
    return summary


_CRITICAL_JNI_LIBS = {
    "android_servers", "android_runtime", "surfaceflinger", "binder_ndk",
}
_BUILTIN_JVM_JNI = {
    "javacore", "openjdk", "openjdkjvm", "art", "art-compiler", "nativehelper",
    "c", "m", "dl", "log", "z",
}


def _collect_enabled_features(ctx):
    """Collect active Android system features from */etc/permissions & */etc/sysconfig XMLs
    (all <feature name="..."/> minus <unavailable-feature name="..."/>)."""
    import xml.etree.ElementTree as ET
    vfs = ctx.vfs
    enabled = set()
    unavailable = set()
    dirs = (
        "/system/etc/permissions", "/system_ext/etc/permissions",
        "/product/etc/permissions", "/vendor/etc/permissions", "/odm/etc/permissions",
        "/system/etc/sysconfig", "/system_ext/etc/sysconfig",
        "/product/etc/sysconfig", "/vendor/etc/sysconfig", "/odm/etc/sysconfig",
    )
    for d in dirs:
        if not vfs.is_dir(d):
            continue
        for fn in (vfs.listdir(d) or ()):
            if not fn.endswith(".xml"):
                continue
            txt = vfs.read_text(posixpath.join(d, fn))
            if not txt or "feature" not in txt:
                continue
            try:
                root = ET.fromstring(txt)
            except Exception:
                continue
            for el in root.iter():
                tag = el.tag.rsplit("}", 1)[-1]
                if tag == "feature":
                    name = (el.attrib.get("name") or "").strip()
                    if name:
                        enabled.add(name)
                elif tag == "unavailable-feature":
                    name = (el.attrib.get("name") or "").strip()
                    if name:
                        unavailable.add(name)
    return sorted(enabled - unavailable)


def _collect_available_so_map(ctx, target_vps=()):
    """Return (all_so_names: set[str], so_vpaths: dict[str, list[str]]).
    Includes partition libs, priv-app/app extracted libs, APEX libs, and
    uncompressed native .so libraries embedded inside target APKs (extractNativeLibs=false)."""
    import io
    import zipfile
    vfs = ctx.vfs
    names = set()
    vpaths = {}
    roots = (
        "/system/lib64", "/system_ext/lib64", "/product/lib64", "/vendor/lib64", "/odm/lib64",
        "/system/lib", "/system_ext/lib", "/product/lib", "/vendor/lib", "/odm/lib",
        "/system/priv-app", "/system_ext/priv-app", "/product/priv-app",
        "/system/app", "/system_ext/app", "/product/app",
    )

    def _rec(fn, vp):
        names.add(fn)
        if vp:
            lst = vpaths.setdefault(fn, [])
            if vp not in lst:
                lst.append(vp)

    for root in roots:
        if not vfs.is_dir(root):
            continue
        if root.endswith(("priv-app", "/app")):
            for app_dir in (vfs.listdir(root) or ()):
                for abi in ("lib/arm64", "lib/arm64-v8a", "lib/arm", "lib/armeabi-v7a"):
                    ld = posixpath.join(root, app_dir, abi)
                    if vfs.is_dir(ld):
                        for fn in (vfs.listdir(ld) or ()):
                            if fn.endswith(".so"):
                                _rec(fn, posixpath.join(ld, fn))
            continue
        for fn in (vfs.listdir(root) or ()):
            if fn.endswith(".so"):
                _rec(fn, posixpath.join(root, fn))
            elif fn in ("hw", "drm", "extractors") or fn.startswith("vndk"):
                sub = posixpath.join(root, fn)
                if vfs.is_dir(sub):
                    for sfn in (vfs.listdir(sub) or ()):
                        if sfn.endswith(".so"):
                            _rec(sfn, posixpath.join(sub, sfn))
    # Scan embedded lib/*/*.so inside target APKs (Android extractNativeLibs="false")
    for vp in (target_vps or ()):
        if not str(vp).endswith(".apk"):
            continue
        raw = vfs.read_bytes(vp)
        if not raw:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                for entry in zf.namelist():
                    if entry.startswith("lib/") and entry.endswith(".so"):
                        names.add(posixpath.basename(entry))
        except Exception:
            pass
    apex = getattr(ctx, "apex", None)
    if apex is not None:
        names.update(getattr(apex, "libs", None) or ())
        names.update(getattr(apex, "public_libs", None) or ())
    return names, vpaths


def _jni_mangle_part(text: str) -> str:
    out = []
    for ch in text:
        if ch == "_":
            out.append("_1")
        elif ch == ";":
            out.append("_2")
        elif ch == "[":
            out.append("_3")
        elif ch == "/":
            out.append("_")
        elif ch.isascii() and ch.isalnum():
            out.append(ch)
        else:
            out.append(f"_0{ord(ch):04x}")
    return "".join(out)


def _jni_short_symbol(class_desc: str, method_name: str) -> str:
    cls = class_desc
    if cls.startswith("L") and cls.endswith(";"):
        cls = cls[1:-1]
    return f"Java_{_jni_mangle_part(cls)}_{_jni_mangle_part(method_name)}"


def _scan_so_defined_syms(ctx, target_vpaths):
    """Batch-read ELF .dynsym defined symbols for a targeted list of .so vpaths."""
    if not target_vpaths:
        return {}
    try:
        from core import native_backend
    except Exception:
        return {}
    vfs = ctx.vfs
    by_host = []
    by_erofs = {}
    by_ext = {}
    host_to_vp = {}
    erofs_rel_to_vp = {}
    ext_rel_to_vp = {}

    for vp in sorted(set(target_vpaths)):
        node = vfs.lookup(vp)
        if node is None or not node.is_file:
            continue
        if node.host:
            by_host.append(node.host)
            host_to_vp[node.host] = vp
        elif getattr(node.source, "native_handle", None) is not None:
            fs_t = node.source.fs_type
            sid = id(node.source)
            if fs_t == "EROFS":
                by_erofs.setdefault(sid, (node.source.native_handle, []))[1].append(node.rel)
                erofs_rel_to_vp[(sid, node.rel)] = vp
            elif fs_t == "EXT":
                by_ext.setdefault(sid, (node.source.native_handle, []))[1].append(node.rel)
                ext_rel_to_vp[(sid, node.rel)] = vp

    out = {}
    if by_host:
        try:
            for rec in native_backend.vdev_scan_elfs(by_host, with_symbols=True):
                vp = host_to_vp.get(rec.get("path", "")) or rec.get("path", "")
                if rec.get("ok"):
                    out[vp] = set(rec.get("defined_syms") or ())
        except Exception:
            pass
    for sid, (handle, rels) in by_erofs.items():
        try:
            for rec in native_backend.vdev_scan_erofs_elfs(handle, rels, with_symbols=True):
                vp = erofs_rel_to_vp.get((sid, rec.get("path", ""))) or rec.get("path", "")
                if rec.get("ok"):
                    out[vp] = set(rec.get("defined_syms") or ())
        except Exception:
            pass
    for sid, (handle, rels) in by_ext.items():
        try:
            for rec in native_backend.vdev_scan_ext_elfs(handle, rels, with_symbols=True):
                vp = ext_rel_to_vp.get((sid, rec.get("path", ""))) or rec.get("path", "")
                if rec.get("ok"):
                    out[vp] = set(rec.get("defined_syms") or ())
        except Exception:
            pass
    return out


def _audit_jni_symbols(ctx, jni_bindings, so_vpaths, findings):
    """Cross-verify DEX ACC_NATIVE methods against static Java_* exports in loaded .so files.
    Skips libraries exporting JNI_OnLoad (which bind dynamically via RegisterNatives)."""
    if not jni_bindings or not so_vpaths:
        return [], 0

    # Group bindings by class_name so multi-loadLibrary classes are evaluated together
    by_class = {}
    needed_vps = set()
    for b in jni_bindings:
        lib = str(b.get("lib") or "").strip()
        cls = str(b.get("class_name") or "").strip()
        methods = [str(m) for m in (b.get("methods") or ()) if m]
        if not lib or not cls or not methods or lib in _BUILTIN_JVM_JNI or "/" in lib:
            continue
        so_name = lib if lib.endswith(".so") else f"lib{lib}.so"
        vps = so_vpaths.get(so_name) or []
        if not vps:
            continue
        # Scan primary (lib64 preferred) .so for this lib
        needed_vps.add(vps[0])
        entry = by_class.setdefault(cls, {
            "libs": [],
            "methods": set(),
            "source": b.get("source", ""),
            "guarded": True,
            "boot_critical": False,
        })
        if (so_name, vps[0]) not in entry["libs"]:
            entry["libs"].append((so_name, vps[0]))
        entry["methods"].update(methods)
        entry["guarded"] = entry["guarded"] and bool(b.get("guarded"))
        entry["boot_critical"] = entry["boot_critical"] or bool(b.get("boot_critical"))

    if not needed_vps:
        return [], 0

    syms_by_vp = _scan_so_defined_syms(ctx, list(needed_vps))
    if not syms_by_vp:
        return [], 0

    missing_syms = []
    checked_classes = 0
    for cls, info in by_class.items():
        union_syms = set()
        has_onload = False
        any_scanned = False
        lib_names = []
        for so_name, vp in info["libs"]:
            syms = syms_by_vp.get(vp)
            if syms is None:
                continue
            any_scanned = True
            lib_names.append(so_name)
            if "JNI_OnLoad" in syms:
                has_onload = True
                break
            union_syms.update(syms)
        if not any_scanned or has_onload:
            continue

        # Core android.* / com.android.server.* classes often register natives via libandroid_runtime /
        # libandroid_servers JNI_OnLoad even when they also load a helper .so. Only audit them if the
        # loaded .so actually exports static Java_* symbols (proving static JNI binding).
        has_any_java_sym = any(s.startswith("Java_") for s in union_syms)
        is_core_runtime_cls = cls.startswith(("Landroid/", "Lcom/android/server/", "Lcom/android/internal/"))
        if is_core_runtime_cls and not has_any_java_sym:
            continue

        checked_classes += 1
        cls_missing = []
        for m in sorted(info["methods"]):
            short = _jni_short_symbol(cls, m)
            ovl_prefix = short + "__"
            if short in union_syms or any(s.startswith(ovl_prefix) for s in union_syms):
                continue
            cls_missing.append((m, short))

        if cls_missing:
            # If the .so has zero Java_* exports at all AND this class isn't specifically bound to it,
            # only report when there's just 1 loaded .so (avoiding helper C lib false positives)
            if not has_any_java_sym and len(lib_names) > 1:
                continue
            missing_syms.append({
                "class_name": cls,
                "libs": lib_names,
                "methods": [m for m, _ in cls_missing],
                "symbols": [sym for _, sym in cls_missing],
                "source": info["source"],
                "guarded": info["guarded"],
                "boot_critical": info["boot_critical"],
            })

    if missing_syms:
        unguarded = [m for m in missing_syms if not m["guarded"]]
        crit = [m for m in unguarded if m["boot_critical"]]
        if unguarded:
            sev = BLOCKER if crit else WARN
            picked = crit if crit else unguarded
            sample = "; ".join(
                f"{m['class_name'].strip('L;').replace('/', '.')}.{','.join(m['methods'][:3])} not exported in {'+'.join(m['libs'])}"
                for m in picked[:_MAX_LIST]
            )
            findings.append(Finding(
                KEY, "dex-missing-jni-symbol", sev,
                f"{len(unguarded)} class(es) declare native method(s) missing from static JNI .so exports (UnsatisfiedLinkError)",
                sample + ". The loaded .so does not export JNI_OnLoad or the expected Java_* symbol(s), "
                "causing UnsatisfiedLinkError as soon as the native method is invoked.",
                "Align the JNI .so library with the JAR/APK version or verify RegisterNatives / JNI_OnLoad exports.",
                picked[0].get("source", ""),
            ))
        else:
            sample = "; ".join(
                f"{m['class_name'].strip('L;').replace('/', '.')}.{','.join(m['methods'][:3])} ({'+'.join(m['libs'])})"
                for m in missing_syms[:_MAX_LIST]
            )
            findings.append(Finding(
                KEY, "dex-missing-jni-symbol-guarded", INFO,
                f"{len(missing_syms)} try/catch-guarded JNI class(es) lack matching static Java_* exports",
                sample,
                "",
                missing_syms[0].get("source", ""),
            ))
    return missing_syms, checked_classes


def _collect_declared_vintf_hals(ctx):
    import xml.etree.ElementTree as ET
    vfs = ctx.vfs
    declared = set()
    vintf_dirs = (
        "/vendor/etc/vintf", "/vendor/etc/vintf/manifest",
        "/odm/etc/vintf", "/odm/etc/vintf/manifest",
        "/system/etc/vintf", "/system/etc/vintf/manifest",
        "/system_ext/etc/vintf", "/system_ext/etc/vintf/manifest",
        "/product/etc/vintf", "/product/etc/vintf/manifest",
    )
    for d in vintf_dirs:
        if not vfs.is_dir(d):
            continue
        for fn in (vfs.listdir(d) or ()):
            if not fn.endswith(".xml") or "matrix" in fn.lower():
                continue
            txt = vfs.read_text(posixpath.join(d, fn))
            if not txt:
                continue
            try:
                root = ET.fromstring(txt)
            except Exception:
                continue
            for hal in root.findall(".//hal"):
                name_el = hal.find("name")
                if name_el is not None and name_el.text:
                    declared.add(name_el.text.strip())
    return declared


_FORM_FACTOR_SERVICE_GATES = (
    (("com.android.clockwork.", "com.google.android.clockwork.", "com.android.server.wear."), ("android.hardware.type.watch",)),
    (("com.android.internal.car.", "com.android.car."), ("android.hardware.type.automotive",)),
    (("com.android.server.arc.",), ("org.chromium.arc",)),
    (("com.android.server.tv.",), ("android.software.leanback", "android.software.live_tv")),
    (("com.android.things.",), ("android.hardware.type.embedded",)),
)


def _is_form_factor_gated_out(svc_name: str, enabled_features_set: set) -> bool:
    if not enabled_features_set:
        return False
    for prefixes, req_feats in _FORM_FACTOR_SERVICE_GATES:
        if any(svc_name.startswith(p) for p in prefixes):
            return not any(f in enabled_features_set for f in req_feats)
    return False


def _audit_logic_map(ctx, logic_map, installed_pkgs, findings, *, enabled_features=None, target_vps=()):
    sys_svcs = logic_map.get("system_services") or []
    jni_libs = logic_map.get("jni_libraries") or []
    jni_bindings = logic_map.get("jni_bindings") or []
    refl_cls = logic_map.get("reflection_classes") or []
    refl_members = logic_map.get("reflection_members") or []
    binders = logic_map.get("binder_services") or []
    sysprops = logic_map.get("system_properties") or []
    comp_targets = logic_map.get("component_targets") or []
    dead_pruned = int(logic_map.get("dead_branches_pruned") or 0)
    static_str_resolved = int(logic_map.get("static_strings_resolved") or 0)
    feat_set = set(enabled_features or ())

    has_blocker = False

    # 1) SystemServer boot sequence summary (live branches only for unresolved audit)
    by_phase = {"bootstrap": [], "core": [], "other": [], "runtime": []}
    unresolved_boot_live = []
    unresolved_boot_guarded = []
    dead_boot_skipped = 0
    for s in sys_svcs:
        svc_val = s.get("value", "")
        if s.get("dead_branch") or _is_form_factor_gated_out(svc_val, feat_set):
            if not s.get("resolved"):
                dead_boot_skipped += 1
            continue
        ph = s.get("secondary") or "runtime"
        by_phase.setdefault(ph, []).append(svc_val)
        if not s.get("resolved"):
            if s.get("guarded"):
                unresolved_boot_guarded.append(s)
            else:
                unresolved_boot_live.append(s)

    props_map = (ctx.profile or {}).get("props") or {}
    props_set = [p for p in sysprops if p in props_map]
    try:
        rom_sdk = int(props_map.get("ro.build.version.sdk") or (ctx.profile or {}).get("sdk") or 0)
    except Exception:
        rom_sdk = 0

    # 2) JNI libraries + static Java_* ELF symbol cross-verification
    avail_so, so_vpaths = _collect_available_so_map(ctx, target_vps) if (jni_libs or jni_bindings) else (set(), {})
    missing_jni_unguarded = []
    missing_jni_guarded = []
    critical_missing_jni = []
    if jni_libs:
        seen_miss = set()
        for item in jni_libs:
            if item.get("dead_branch"):
                continue
            lib = str(item.get("value") or "").strip()
            if not lib or lib in _BUILTIN_JVM_JNI or "/" in lib or " " in lib:
                continue
            # AndroidX PathIteratorPreApi34Impl is only instantiated on SDK < 34
            if lib == "androidx.graphics.path" and rom_sdk >= 34 and "PreApi34" in str(item.get("caller", "")):
                continue
            so_name = lib if lib.endswith(".so") else f"lib{lib}.so"
            if so_name not in avail_so and so_name not in seen_miss:
                seen_miss.add(so_name)
                if item.get("guarded"):
                    missing_jni_guarded.append((so_name, item))
                else:
                    missing_jni_unguarded.append((so_name, item))
                    if lib in _CRITICAL_JNI_LIBS:
                        critical_missing_jni.append((so_name, item))

    missing_jni_syms, jni_sym_classes_checked = _audit_jni_symbols(ctx, jni_bindings, so_vpaths, findings)
    if any((not m["guarded"]) and m["boot_critical"] for m in missing_jni_syms):
        has_blocker = True

    if sys_svcs or jni_libs or binders or sysprops or refl_members:
        boot_detail_parts = []
        if by_phase["bootstrap"]:
            boot_detail_parts.append(
                f"bootstrap({len(by_phase['bootstrap'])}): " + ", ".join(x.rsplit(".", 1)[-1] for x in by_phase["bootstrap"][:6])
            )
        if by_phase["core"]:
            boot_detail_parts.append(
                f"core({len(by_phase['core'])}): " + ", ".join(x.rsplit(".", 1)[-1] for x in by_phase["core"][:6])
            )
        if by_phase["other"]:
            boot_detail_parts.append(
                f"other({len(by_phase['other'])}): " + ", ".join(x.rsplit(".", 1)[-1] for x in by_phase["other"][:6])
            )
        if static_str_resolved or dead_pruned:
            boot_detail_parts.append(
                f"static-const={static_str_resolved}, dead-branches-pruned={dead_pruned}"
                + (f" ({dead_boot_skipped} gated service skipped)" if dead_boot_skipped else "")
            )
        findings.append(Finding(
            KEY, "dex-logic-summary", INFO,
            f"DEX logic map: {sum(len(v) for v in by_phase.values())} live SystemServer service(s) · "
            f"{len(jni_libs)} JNI loadLibrary · {len(refl_members)} reflection member(s) · "
            f"{len(binders)} Binder/HAL call(s) · {len(props_set)}/{len(sysprops)} SystemProperties set",
            " | ".join(boot_detail_parts) if boot_detail_parts else "Extracted from core DEX bytecode.",
        ))

    # 2b) Unresolved SystemServiceManager.startService("...") -> ClassNotFoundException during SystemServer boot!
    if unresolved_boot_live:
        sample = "; ".join(f"{s['value']} (phase={s.get('secondary')}, caller={s['caller']})" for s in unresolved_boot_live[:_MAX_LIST])
        findings.append(Finding(
            KEY, "dex-unresolved-boot-service", WARN,
            f"{len(unresolved_boot_live)} live unguarded SystemServer startService() class(es) not found in classpath",
            sample + ". SystemServer invokes startService(String) outside try/catch on an active feature branch; "
            "if reached during boot while absent from SYSTEMSERVERCLASSPATH/APEX, SystemServer throws ClassNotFoundException.",
            "Verify the matching service JAR/APEX is present or that the feature flag disables this service.",
            unresolved_boot_live[0].get("source", ""),
        ))
    elif unresolved_boot_guarded:
        sample = "; ".join(f"{s['value']} ({s.get('secondary')})" for s in unresolved_boot_guarded[:_MAX_LIST])
        findings.append(Finding(
            KEY, "dex-unresolved-boot-service-guarded", INFO,
            f"{len(unresolved_boot_guarded)} try/catch-guarded SystemServer startService() class(es) absent from classpath",
            sample + ". Wrapped in try/catch so missing class will not crash SystemServer.",
            "",
            unresolved_boot_guarded[0].get("source", ""),
        ))

    # 3) JNI System.loadLibrary("...") findings (unguarded vs try/catch guarded)
    if critical_missing_jni:
        has_blocker = True
        sample = "; ".join(f"{so} (loaded by {it['caller']})" for so, it in critical_missing_jni[:_MAX_LIST])
        findings.append(Finding(
            KEY, "dex-missing-jni-critical", BLOCKER,
            f"{len(critical_missing_jni)} boot-critical JNI library(s) missing (UnsatisfiedLinkError)",
            sample + ". Core SystemServer/Android runtime calls unguarded System.loadLibrary() on these libraries; "
            "missing them causes an immediate fatal UnsatisfiedLinkError crash.",
            "Restore the missing .so into /system/lib64 (or /system_ext/lib64).",
            critical_missing_jni[0][1].get("source", ""),
        ))
    elif missing_jni_unguarded:
        sample = "; ".join(f"{so} ({it['caller'].split(';')[0].rsplit('/', 1)[-1]})" for so, it in missing_jni_unguarded[:_MAX_LIST])
        findings.append(Finding(
            KEY, "dex-missing-jni-lib", WARN,
            f"{len(missing_jni_unguarded)} unguarded System.loadLibrary() native .so target(s) not found in VFS/APEX",
            sample + ". Calling unguarded System.loadLibrary() on a missing .so throws UnsatisfiedLinkError.",
            "Check if these JNI .so files belong in /system/lib64, /system_ext/lib64, /vendor/lib64, or inside the APK's lib/.",
            missing_jni_unguarded[0][1].get("source", ""),
        ))
    if missing_jni_guarded:
        sample = "; ".join(f"{so} ({it['caller'].split(';')[0].rsplit('/', 1)[-1]})" for so, it in missing_jni_guarded[:_MAX_LIST])
        findings.append(Finding(
            KEY, "dex-missing-jni-guarded", INFO,
            f"{len(missing_jni_guarded)} try/catch-guarded System.loadLibrary() optional .so(s) absent",
            sample + ". Guarded by try/catch(UnsatisfiedLinkError/Throwable) — safe optional OEM probe.",
            "",
            missing_jni_guarded[0][1].get("source", ""),
        ))

    # 4) Unresolved Reflection Class.forName / loadClass (OEM / Android framework classes)
    _REFL_PREFIXES = (
        "com.android.", "android.", "com.transsion.", "com.miui.", "miui.",
        "com.oplus.", "com.coloros.", "com.samsung.", "com.mediatek.", "com.qualcomm.",
    )
    unresolved_refl = [
        r for r in refl_cls
        if not r.get("resolved")
        and not r.get("dead_branch")
        and r.get("secondary") != "SystemServiceManager.startService"
        and any(r.get("value", "").startswith(pfx) for pfx in _REFL_PREFIXES)
    ]
    unresolved_refl_unguarded = [r for r in unresolved_refl if not r.get("guarded")]
    if unresolved_refl_unguarded:
        sample = "; ".join(f"{r['value']} ({r['caller'].split(';')[0].rsplit('/', 1)[-1]})" for r in unresolved_refl_unguarded[:_MAX_LIST])
        findings.append(Finding(
            KEY, "dex-unresolved-reflection", WARN,
            f"{len(unresolved_refl_unguarded)} unguarded Class.forName/loadClass target(s) absent from classpath",
            sample + ". Invoked outside try/catch — reaching these calls throws ClassNotFoundException.",
            "Restore the referenced framework/OEM class or wrap the optional lookup in try/catch.",
            unresolved_refl_unguarded[0].get("source", ""),
        ))
    elif unresolved_refl:
        sample = "; ".join(f"{r['value']} ({r['caller'].split(';')[0].rsplit('/', 1)[-1]})" for r in unresolved_refl[:_MAX_LIST])
        findings.append(Finding(
            KEY, "dex-unresolved-reflection", INFO,
            f"{len(unresolved_refl)} try/catch-guarded reflection class probe(s) absent from classpath",
            sample + ". Guarded OEM/AOSP feature probes (safe unless fallback is broken).",
            "",
            unresolved_refl[0].get("source", ""),
        ))

    # 4b) Reflection Method & Field Audit (Class.getMethod/getDeclaredMethod/getField/getDeclaredField)
    unresolved_members = [
        m for m in refl_members
        if not m.get("resolved") and not m.get("dead_branch")
    ]
    unresolved_members_unguarded = [m for m in unresolved_members if not m.get("guarded")]
    unresolved_members_guarded = [m for m in unresolved_members if m.get("guarded")]
    if unresolved_members_unguarded:
        sample = "; ".join(
            f"{m['value']} via {m['secondary']} ({m['caller'].split(';')[0].rsplit('/', 1)[-1]})"
            for m in unresolved_members_unguarded[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "dex-missing-reflection-member", WARN,
            f"{len(unresolved_members_unguarded)} unguarded reflection method/field lookup(s) missing on target class",
            sample + ". The reflected class exists, but the requested method/field name is absent across its "
            "class hierarchy — throws NoSuchMethodException / NoSuchFieldException at runtime.",
            "Align the reflected member name with this ROM's framework/OEM API or guard it with try/catch.",
            unresolved_members_unguarded[0].get("source", ""),
        ))
    elif unresolved_members_guarded:
        sample = "; ".join(
            f"{m['value']} ({m['secondary']})"
            for m in unresolved_members_guarded[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "dex-missing-reflection-member-guarded", INFO,
            f"{len(unresolved_members_guarded)} try/catch-guarded reflection member probe(s) absent on target class",
            sample + ". Guarded by try/catch — typical cross-version compatibility fallback.",
            "",
            unresolved_members_guarded[0].get("source", ""),
        ))

    # 5) Blocking AIDL HAL calls (ServiceManager.waitForDeclaredService / waitForService) vs VINTF
    undeclared_blocking_hals = []
    undeclared_blocking_guarded = []
    if binders and ctx.has_vendor:
        declared_hals = _collect_declared_vintf_hals(ctx)
        if declared_hals:
            seen_hal = set()
            for b in binders:
                if not b.get("blocking") or b.get("dead_branch"):
                    continue
                svc = str(b.get("value") or "")
                if "/" not in svc:
                    continue
                iface_full = svc.split("/", 1)[0]
                if "." not in iface_full:
                    continue
                pkg = iface_full.rsplit(".", 1)[0]
                if not (pkg.startswith("android.hardware.") or pkg.startswith("vendor.")):
                    continue
                if pkg not in declared_hals and svc not in seen_hal:
                    seen_hal.add(svc)
                    if b.get("guarded"):
                        undeclared_blocking_guarded.append((svc, b))
                    else:
                        undeclared_blocking_hals.append((svc, b))
    if undeclared_blocking_hals:
        sample = "; ".join(
            f"{svc} ({it['secondary']} in {it['caller'].split(';')[0].rsplit('/', 1)[-1]})"
            for svc, it in undeclared_blocking_hals[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "dex-blocking-hal-undeclared", WARN,
            f"{len(undeclared_blocking_hals)} unguarded blocking AIDL HAL wait(s) not declared in VINTF manifests",
            sample + ". ServiceManager.waitForDeclaredService()/waitForService() blocks if that HAL is expected "
            "and absent from vendor/odm VINTF manifest.",
            "Confirm whether the vendor provides this HAL or if the calling service is gated off.",
            undeclared_blocking_hals[0][1].get("source", ""),
        ))
    elif undeclared_blocking_guarded:
        sample = "; ".join(f"{svc} ({it['secondary']})" for svc, it in undeclared_blocking_guarded[:_MAX_LIST])
        findings.append(Finding(
            KEY, "dex-blocking-hal-guarded", INFO,
            f"{len(undeclared_blocking_guarded)} try/catch-guarded AIDL HAL wait(s) absent from VINTF",
            sample,
            "",
            undeclared_blocking_guarded[0][1].get("source", ""),
        ))

    # 6) Cross-APK ComponentName(pkg, cls) check against installed APKs
    missing_comp_pkgs = []
    if comp_targets and installed_pkgs:
        inst_set = set(installed_pkgs) | {"android"}
        seen_cp = set()
        for ct in comp_targets:
            if ct.get("dead_branch"):
                continue
            pkg = str(ct.get("secondary") or "")
            if not pkg or pkg in inst_set or pkg in seen_cp:
                continue
            if pkg.startswith(("com.android.", "com.google.android.", "com.transsion.", "com.miui.", "com.oplus.", "com.samsung.")):
                seen_cp.add(pkg)
                missing_comp_pkgs.append(ct)
    if missing_comp_pkgs:
        sample = "; ".join(f"{c['value']}" for c in missing_comp_pkgs[:_MAX_LIST])
        findings.append(Finding(
            KEY, "dex-missing-component-target", INFO,
            f"{len(missing_comp_pkgs)} ComponentName target package(s) referenced in DEX are not installed",
            sample + ". Explicit ComponentName intents targeting uninstalled packages fail to resolve if triggered.",
            "",
            missing_comp_pkgs[0].get("source", ""),
        ))

    return {
        "has_blocker": has_blocker,
        "system_services_count": sum(len(v) for v in by_phase.values()),
        "bootstrap_services": by_phase["bootstrap"][:30],
        "core_services": by_phase["core"][:30],
        "other_services": by_phase["other"][:40],
        "jni_libraries_count": len(jni_libs),
        "jni_bindings_checked": jni_sym_classes_checked,
        "missing_jni": [so for so, _ in missing_jni_unguarded[:20]],
        "missing_jni_guarded": [so for so, _ in missing_jni_guarded[:20]],
        "missing_jni_symbols": missing_jni_syms[:20],
        "reflection_count": len(refl_cls),
        "unresolved_reflection_count": len(unresolved_refl),
        "unresolved_reflection_unguarded_count": len(unresolved_refl_unguarded),
        "reflection_members_count": len(refl_members),
        "missing_reflection_members": [m["value"] for m in unresolved_members_unguarded[:20]],
        "binder_calls_count": len(binders),
        "undeclared_blocking_hals": [svc for svc, _ in undeclared_blocking_hals[:20]],
        "system_properties_count": len(sysprops),
        "system_properties_set_count": len(props_set),
        "component_targets_count": len(comp_targets),
        "dead_branches_pruned": dead_pruned,
        "static_strings_resolved": static_str_resolved,
    }
