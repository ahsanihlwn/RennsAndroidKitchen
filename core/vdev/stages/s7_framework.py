# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import posixpath
import xml.etree.ElementTree as ET

from core.vdev.apkinfo import (
    read_apk, cert_fingerprint, cert_available, platform_permissions,
)
from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "framework"
LABEL = "Framework & APK"
REQUIRES = ("system",)

_MAX_LIST = 10
_MAX_APK = 1500

# Bit protectionLevel (android.R.attr.protectionLevel).
_PROTECTION_MASK = 0x0F
_FLAG_PRIVILEGED = 0x10
_FLAG_DEVELOPMENT = 0x20
_FLAG_APPOP = 0x40
_SIGNATURE = 0x02

# Pintu keluar: permission dengan salah satu flag ini tidak dinilai lewat
# daftar putih privapp — lihat catatan modul.
_ALLOWLIST_EXEMPT = _FLAG_DEVELOPMENT | _FLAG_APPOP

_APK_DIRS = (
    "/system/priv-app", "/system/app", "/system/overlay",
    "/system_ext/priv-app", "/system_ext/app", "/system_ext/overlay",
    "/product/priv-app", "/product/app", "/product/overlay",
    "/vendor/app", "/vendor/overlay",
    "/odm/app", "/odm/overlay",
)

_PERM_XML_DIRS = (
    "/system/etc/permissions", "/system/etc/sysconfig",
    "/system_ext/etc/permissions", "/system_ext/etc/sysconfig",
    "/product/etc/permissions", "/product/etc/sysconfig",
    "/vendor/etc/permissions", "/vendor/etc/sysconfig",
    "/odm/etc/permissions", "/odm/etc/sysconfig",
)

_FRAMEWORK_RES = "/system/framework/framework-res.apk"


# pengumpulan
def _scan_apks(ctx):

    vfs = ctx.vfs
    candidates = []
    for vdir in _APK_DIRS:
        if not vfs.is_dir(vdir):
            continue
        # priv-app/app: <dir>/<Nama>/<Nama>.apk ; overlay: <dir>/<Nama>.apk
        for entry in vfs.listdir(vdir):
            sub = posixpath.join(vdir, entry)
            if entry.endswith(".apk") and vfs.is_file(sub):
                candidates.append(sub)
            elif vfs.is_dir(sub):
                candidates.extend(vfs.glob(sub, ".apk"))
            if len(candidates) >= _MAX_APK:
                candidates = candidates[:_MAX_APK]
                break
        if len(candidates) >= _MAX_APK:
            break

    if not candidates:
        return []

    try:
        from core import native_backend
        from core.vdev.apkinfo import ApkInfo
        if native_backend._get_native() is not None:
            by_host = []
            by_erofs = {}
            by_ext = {}
            fallback_vp = []
            for vp in candidates:
                node = vfs.lookup(vp)
                if node is None or not node.is_file:
                    continue
                if node.host:
                    by_host.append((vp, node.host))
                elif getattr(node.source, "native_handle", None) is not None:
                    fs_t = node.source.fs_type
                    if fs_t == "EROFS":
                        by_erofs.setdefault(id(node.source), (node.source, node.source.native_handle, []))[2].append((vp, node.rel))
                    elif fs_t == "EXT":
                        by_ext.setdefault(id(node.source), (node.source, node.source.native_handle, []))[2].append((vp, node.rel))
                    else:
                        fallback_vp.append(vp)
                else:
                    fallback_vp.append(vp)

            def _to_info(rec, host_obj):
                info = ApkInfo(rec["vpath"], host_obj)
                info.package = rec.get("package", "")
                info.shared_user_id = rec.get("shared_user_id", "")
                info.permissions = set(rec.get("permissions") or ())
                info.libraries = [(n, bool(req)) for n, req in (rec.get("uses_libraries") or ())]
                info.overlay_target = rec.get("overlay_target", "")
                info.is_launcher = bool(rec.get("is_launcher"))
                info.cert_sha256 = rec.get("cert_sha256", "")
                info.declared_permissions = {
                    n: int(lvl) for n, lvl in (rec.get("declared_permissions") or ())
                }
                info.components = [
                    (str(t), str(n)) for t, n in (rec.get("components") or ())
                ]
                info.error = rec.get("error", "")
                return info

            # Include framework-res.apk in the same parallel Rust scan so we get platform permissions & cert without Python re-read
            fw_node = vfs.lookup(_FRAMEWORK_RES)
            if fw_node is not None and fw_node.is_file:
                if fw_node.host:
                    by_host.append((_FRAMEWORK_RES, fw_node.host))
                elif getattr(fw_node.source, "native_handle", None) is not None:
                    fs_t = fw_node.source.fs_type
                    if fs_t == "EROFS":
                        by_erofs.setdefault(id(fw_node.source), (fw_node.source, fw_node.source.native_handle, []))[2].append((_FRAMEWORK_RES, fw_node.rel))
                    elif fs_t == "EXT":
                        by_ext.setdefault(id(fw_node.source), (fw_node.source, fw_node.source.native_handle, []))[2].append((_FRAMEWORK_RES, fw_node.rel))

            out = []
            fw_info = None
            if by_host:
                for (vp, hp), rec in zip(by_host, native_backend.vdev_scan_apk_files(by_host)):
                    obj = _to_info(rec, hp)
                    if vp == _FRAMEWORK_RES:
                        fw_info = obj
                    else:
                        out.append(obj)
            for src, handle, items in by_erofs.values():
                rels = [(vp, rel) for vp, rel in items]
                for (vp, rel), rec in zip(items, native_backend.vdev_scan_erofs_apks(handle, rels)):
                    obj = _to_info(rec, lambda _s=src, _r=rel: _s.open(_r))
                    if vp == _FRAMEWORK_RES:
                        fw_info = obj
                    else:
                        out.append(obj)
            for src, handle, items in by_ext.values():
                rels = [(vp, rel) for vp, rel in items]
                for (vp, rel), rec in zip(items, native_backend.vdev_scan_ext_apks(handle, rels)):
                    obj = _to_info(rec, lambda _s=src, _r=rel: _s.open(_r))
                    if vp == _FRAMEWORK_RES:
                        fw_info = obj
                    else:
                        out.append(obj)
            for vp in fallback_vp:
                ref = vfs.source_ref(vp)
                if ref is not None:
                    out.append(read_apk(vp, ref))
            if fw_info is not None:
                ctx.shared["framework_res_info"] = fw_info
            return out
    except Exception:
        pass

    out = []
    for vp in candidates:
        ref = vfs.source_ref(vp)
        if ref is not None:
            out.append(read_apk(vp, ref))
    return out


def _read_permission_xml(ctx):

    vfs = ctx.vfs
    allow, deny, libs = {}, {}, {}
    for vdir in _PERM_XML_DIRS:
        if not vfs.is_dir(vdir):
            continue
        for vp in vfs.glob(vdir, ".xml"):
            raw = vfs.read_bytes(vp)
            if not raw:
                continue
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                continue
            for blk in root.iter("privapp-permissions"):
                pkg = blk.get("package")
                if not pkg:
                    continue
                a = allow.setdefault(pkg, set())
                d = deny.setdefault(pkg, set())
                for p in blk:
                    n = p.get("name")
                    if not n:
                        continue
                    if p.tag == "deny-permission":
                        d.add(n)
                    else:
                        a.add(n)
            for lib in root.iter("library"):
                n = lib.get("name")
                if n:
                    libs[n] = lib.get("file") or ""
    return allow, deny, libs


# pemeriksaan
def _check_core(ctx, apks, findings):
    vfs = ctx.vfs
    if not vfs.is_file(_FRAMEWORK_RES):
        findings.append(Finding(
            KEY, "no-framework-res", BLOCKER,
            "framework-res.apk is missing",
            "This is the \"android\" package — the source of every resource "
            "and every platform permission declaration. Without it "
            "system_server cannot start.",
            "Restore /system/framework/framework-res.apk from a stock system.",
            _FRAMEWORK_RES,
        ))
    core_blockers = []
    if not vfs.is_file(_FRAMEWORK_RES):
        core_blockers.append("framework-res.apk missing")
    if not vfs.is_file("/system/framework/services.jar"):
        core_blockers.append("services.jar missing")
        findings.append(Finding(
            KEY, "no-services-jar", BLOCKER,
            "services.jar is missing",
            "services.jar contains system_server itself.",
            "Restore it from a stock system.",
            "/system/framework/services.jar",
        ))

    packages = {a.package for a in apks if a.package}
    if "com.android.providers.settings" not in packages:
        core_blockers.append("SettingsProvider missing")
        findings.append(Finding(
            KEY, "no-settings-provider", BLOCKER,
            "SettingsProvider is missing",
            "com.android.providers.settings holds the entire Settings.* "
            "database. system_server reads it very early; without it boot "
            "crashes with a fatal loop.",
            "Restore SettingsProvider.apk into priv-app.",
        ))

    if "com.android.systemui" not in packages:
        core_blockers.append("SystemUI missing")
        findings.append(Finding(
            KEY, "no-systemui", BLOCKER,
            "SystemUI (com.android.systemui) is missing",
            "SystemServer starts com.android.systemui/.SystemUIService during boot. "
            "Without SystemUI, the device boots to a permanent black screen with "
            "no lockscreen, status bar, or navigation bar.",
            "Restore SystemUI.apk into /system/priv-app or /system_ext/priv-app.",
        ))

    has_perm_apex = False
    apex_files = getattr(ctx.apex, "files", None) or {}
    if any(p.startswith(("com.android.permission", "com.google.android.permission")) for p in apex_files):
        has_perm_apex = True
    else:
        for adir in ("/system/apex", "/system_ext/apex", "/product/apex"):
            if vfs.is_dir(adir):
                for fn in (vfs.listdir(adir) or ()):
                    low = fn.lower()
                    if "permission" in low and (low.endswith(".apex") or low.endswith(".capex") or vfs.is_dir(f"{adir}/{fn}")):
                        has_perm_apex = True
                        break
            if has_perm_apex:
                break

    if not has_perm_apex and not (packages & {"com.android.permissioncontroller", "com.google.android.permissioncontroller"}):
        findings.append(Finding(
            KEY, "no-permission-controller", WARN,
            "PermissionController priv-app is missing",
            "RoleManager and runtime permission grants depend on PermissionController; "
            "removing it breaks runtime permission dialogs and can stall boot setup.",
            "Restore PermissionController APK (or Permission apex) into priv-app/apex.",
        ))

    _PKG_INSTALLERS = {
        "com.android.packageinstaller",
        "com.google.android.packageinstaller",
        "com.miui.packageinstaller",
        "com.oplus.appdetail",
        "com.samsung.android.packageinstaller",
        "com.transsion.installer",
    }
    if not (packages & _PKG_INSTALLERS) and not any("packageinstaller" in p for p in packages):
        findings.append(Finding(
            KEY, "no-package-installer", WARN,
            "PackageInstaller priv-app is missing",
            "Manual APK installation and system installer dialogs will fail.",
            "Restore PackageInstaller.apk into priv-app.",
        ))

    if "com.android.settings" not in packages:
        findings.append(Finding(
            KEY, "no-settings-app", WARN,
            "Settings app (com.android.settings) is missing",
            "Core Settings activities and device setup intents will crash when invoked.",
            "Restore Settings.apk into priv-app.",
        ))

    ctx.shared["core_framework_blockers"] = core_blockers

    launchers = [a.package for a in apks if a.is_launcher]
    if not launchers:
        findings.append(Finding(
            KEY, "no-launcher", WARN,
            "No package declares the HOME category",
            "No launcher found. Boot can finish while the screen stays blank "
            "— frequently mistaken for a bootloop.",
            "Install a launcher (e.g. Launcher3 / QuickStep) into priv-app.",
        ))
    else:
        findings.append(Finding(
            KEY, "launcher", PASS,
            f"{len(launchers)} launcher(s) available",
            ", ".join(sorted(launchers)[:5]),
        ))


def _apk_cert(apk):
    if getattr(apk, "cert_sha256", ""):
        return apk.cert_sha256
    return cert_fingerprint(apk.host)


def _check_privapp(ctx, apks, allow, deny, findings):
    vfs = ctx.vfs
    mode = (ctx.prop("ro.control_privapp_permissions") or "").strip().lower()

    fw_info = ctx.shared.get("framework_res_info")
    plat = fw_info.declared_permissions if (fw_info and fw_info.declared_permissions) else None
    fw = None
    if not plat:
        fw = vfs.source_ref(_FRAMEWORK_RES)
        plat = platform_permissions(fw) if fw is not None else None
    if not plat:
        findings.append(Finding(
            KEY, "no-platform-perms", WARN,
            "Platform permissions could not be read from framework-res.apk",
            "Without the protectionLevel list, privapp-permissions matching "
            "cannot be judged.",
        ))
        return

    # Permission yang kepatuhannya BENAR-BENAR ditentukan daftar putih.
    gated = {n for n, lvl in plat.items()
             if (lvl & _FLAG_PRIVILEGED) and not (lvl & _ALLOWLIST_EXEMPT)}

    if mode == "disable":
        findings.append(Finding(
            KEY, "privapp-disabled", INFO,
            "ro.control_privapp_permissions=disable — enforcement is off",
            f"{len(gated)} privileged permissions are not judged because this "
            "ROM disables the check.",
        ))
        return

    platform_cert = (fw_info.cert_sha256 if (fw_info and fw_info.cert_sha256) else None)
    if platform_cert is None and cert_available():
        if fw is None:
            fw = vfs.source_ref(_FRAMEWORK_RES)
        platform_cert = cert_fingerprint(fw) if fw is not None else None
    violations = []
    checked = 0

    for apk in apks:
        if not apk.privileged or not apk.package:
            continue
        checked += 1
        want = (apk.permissions & gated) - allow.get(apk.package, set()) \
            - deny.get(apk.package, set())
        if not want:
            continue
        # Pintu keluar kedua: ditandatangani kunci platform.
        if platform_cert is not None and _apk_cert(apk) == platform_cert:
            continue
        violations.append((apk, sorted(want)))

    ctx.shared.setdefault("framework", {})["privapp_violations"] = [
        {"package": a.package, "vpath": a.vpath, "permissions": perms}
        for a, perms in violations
    ]

    if not checked:
        return

    if not violations:
        findings.append(Finding(
            KEY, "privapp-ok", PASS,
            f"All {checked} priv-apps pass the privapp-permissions allowlist",
            f"{len(gated)} permissions whose enforcement is decided by the "
            f"allowlist (mode: {mode or 'default'}).",
        ))
        return

    items = []
    for apk, perms in violations[:_MAX_LIST]:
        short = ", ".join(p.rsplit(".", 1)[-1] for p in perms[:3])
        if len(perms) > 3:
            short += f" +{len(perms) - 3}"
        items.append(f"{apk.package} ({short})")
    more = f" (+{len(violations) - _MAX_LIST} more)" if len(violations) > _MAX_LIST else ""

    enforcing = mode == "enforce"
    findings.append(Finding(
        KEY, "privapp-violation", BLOCKER if enforcing else WARN,
        f"{len(violations)} priv-app(s) request privileged permissions outside the allowlist",
        "; ".join(items) + more + ". "
        + ("ro.control_privapp_permissions=enforce, so "
           "PackageManagerService throws IllegalStateException in "
           "systemReady() — system_server dies and the device bootloops."
           if enforcing else
           f"Enforcement mode '{mode or 'default'}' is not enforce, so this "
           "is probably only written to the log."),
        "Add <privapp-permissions package=\"…\"> for those packages under "
        "etc/permissions of the same partition, or drop them from priv-app.",
    ))


def _check_libraries(ctx, apks, libs, findings):
    vfs = ctx.vfs

    # Jar yang dideklarasikan tapi filenya hilang — app pemakainya gagal start.
    dead = [(n, f) for n, f in libs.items() if f and not vfs.is_file(f)]
    if dead:
        findings.append(Finding(
            KEY, "library-file-missing", WARN,
            f"{len(dead)} shared librar(ies) declared but the file is missing",
            "; ".join(f"{n} -> {f}" for n, f in dead[:_MAX_LIST])
            + (f" (+{len(dead) - _MAX_LIST} more)" if len(dead) > _MAX_LIST else "")
            + ". Apps that use them fail to load.",
            "Provide the jar, or drop the dangling <library> declaration.",
        ))

    declared = set(libs)
    missing = {}
    for apk in apks:
        for name, required in apk.libraries:
            if not required or name in declared:
                continue
            missing.setdefault(name, []).append(apk)

    if not missing:
        if declared:
            findings.append(Finding(
                KEY, "library-ok", PASS,
                f"All required uses-library entries satisfied ({len(declared)} libraries declared)",
            ))
        return

    priv_hit = [n for n, users in missing.items() if any(a.privileged for a in users)]
    items = []
    for name in sorted(missing)[:_MAX_LIST]:
        users = missing[name]
        items.append(f"{name} <- {users[0].package or users[0].name}"
                     + (f" +{len(users) - 1}" if len(users) > 1 else ""))
    more = f" (+{len(missing) - _MAX_LIST} more)" if len(missing) > _MAX_LIST else ""

    findings.append(Finding(
        KEY, "library-missing", WARN,
        f"{len(missing)} required shared librar(ies) are not declared",
        "; ".join(items) + more + ". An APK requesting an undeclared required "
        "library fails to parse in PackageManager and is not installed"
        + (f". {len(priv_hit)} of them are requested by priv-apps, where the "
           "impact is heavier than for a normal app." if priv_hit else "."),
        "Copy the declaration XML (etc/permissions/<name>.xml) along with its "
        "jar from the source ROM, or drop the APKs that depend on it.",
    ))


def _check_shared_uid(ctx, apks, findings):
    vfs = ctx.vfs
    users = [a for a in apks if a.shared_user_id == "android.uid.system"]
    if not users:
        return
    if not cert_available():
        findings.append(Finding(
            KEY, "sig-unavailable", INFO,
            f"{len(users)} android.uid.system package(s) had no signature check",
            "The certificate reader (cryptography) is unavailable, so the "
            "platform key match cannot be confirmed. Skipped rather than "
            "accused wrongly.",
        ))
        return
    fw_info = ctx.shared.get("framework_res_info")
    platform_cert = fw_info.cert_sha256 if (fw_info and fw_info.cert_sha256) else None
    if not platform_cert:
        fw = vfs.source_ref(_FRAMEWORK_RES)
        platform_cert = cert_fingerprint(fw) if fw is not None else None
    if not platform_cert:
        return

    bad = []
    unknown = []
    total = len(users)
    for apk in users:
        fp = _apk_cert(apk)
        if fp is None:


            unknown.append(apk)
        elif fp != platform_cert:
            bad.append(apk)

    if unknown:
        findings.append(Finding(
            KEY, "shared-uid-unknown", INFO,
            f"{len(unknown)} android.uid.system package(s) have no readable certificate",
            ", ".join(a.package or a.name for a in unknown[:_MAX_LIST])
            + (f" (+{len(unknown) - _MAX_LIST} more)"
               if len(unknown) > _MAX_LIST else "")
            + ". Their signature could not be extracted from either the v1 "
            "block or the APK Signing Block. Not judged — that does not mean "
            "they are fine, only that it could not be confirmed.",
        ))

    if not bad:
        checked = total - len(unknown)
        if checked:
            findings.append(Finding(
                KEY, "shared-uid-ok", PASS,
                f"{checked} android.uid.system package(s) signed with the platform key",
            ))
    else:
        items = ", ".join(a.package or a.name for a in bad[:_MAX_LIST])
        findings.append(Finding(
            KEY, "shared-uid-mismatch", BLOCKER,
            f"{len(bad)} android.uid.system package(s) signed with a different key",
            items + ". A package asking for sharedUserId android.uid.system MUST "
            "be signed with the same platform key as framework-res. Otherwise "
            "PackageManager refuses to install it — and if that package is a "
            "system component, boot stops.",
            "Re-sign the package with this ROM platform key, or use the build of "
            "that package that came from this ROM.",
        ))

    # ── Audit ALL other sharedUserId groups (systemui, phone, bluetooth, nfc, networkstack, OEM UIDs) ──
    _CORE_SHARED_UIDS = {
        "android.uid.systemui", "android.uid.phone", "android.uid.bluetooth",
        "android.uid.nfc", "android.uid.networkstack", "android.uid.se",
    }
    groups = {}
    for apk in apks:
        suid = (apk.shared_user_id or "").strip()
        if not suid or suid == "android.uid.system" or apk.overlay_target:
            continue
        fp = _apk_cert(apk)
        if not fp:
            continue
        groups.setdefault(suid, []).append((apk, fp))

    core_conflicts = []
    oem_conflicts = []
    all_conflicts = []
    for suid, members in sorted(groups.items()):
        certs = {}
        for apk, fp in members:
            certs.setdefault(fp, []).append(apk.package or apk.name)
        if len(certs) <= 1:
            continue
        summary_entry = {
            "shared_user_id": suid,
            "cert_count": len(certs),
            "groups": [
                {"cert": c[:12], "packages": pkgs[:6]}
                for c, pkgs in sorted(certs.items(), key=lambda kv: -len(kv[1]))
            ],
        }
        all_conflicts.append(summary_entry)
        if suid in _CORE_SHARED_UIDS:
            core_conflicts.append(summary_entry)
        else:
            oem_conflicts.append(summary_entry)

    ctx.shared.setdefault("framework", {})["shared_uid_conflicts"] = all_conflicts
    if core_conflicts:
        sample = "; ".join(
            f"{c['shared_user_id']} ({c['cert_count']} keys: "
            + " vs ".join("/".join(g["packages"][:2]) for g in c["groups"][:2]) + ")"
            for c in core_conflicts[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "shared-uid-group-mismatch", BLOCKER,
            f"{len(core_conflicts)} core sharedUserId group(s) have conflicting APK signatures",
            sample + ". Android PackageManager rejects APKs sharing a core sharedUserId "
            "(systemui/phone/bluetooth/nfc) when signed with different certificates (INSTALL_FAILED_SHARED_USER_INCOMPATIBLE).",
            "Align or re-sign all APKs sharing the same sharedUserId with the same key.",
        ))
    elif oem_conflicts:
        sample = "; ".join(
            f"{c['shared_user_id']} ({c['cert_count']} keys: "
            + " vs ".join("/".join(g["packages"][:2]) for g in c["groups"][:2]) + ")"
            for c in oem_conflicts[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "shared-uid-group-mismatch", WARN,
            f"{len(oem_conflicts)} OEM sharedUserId group(s) have conflicting APK signatures",
            sample + ". Mixed APK signatures under the same sharedUserId prevent secondary OEM packages from installing.",
            "Match all APKs in the sharedUserId group from the same ROM build or re-sign them consistently.",
        ))


def _check_overlays(ctx, apks, findings):
    packages = {a.package for a in apks if a.package}
    packages.add("android")          # framework-res

    dangling = [a for a in apks
                if a.overlay_target and a.overlay_target not in packages]
    if not dangling:
        return
    items = ", ".join(f"{a.name} -> {a.overlay_target}" for a in dangling[:_MAX_LIST])
    findings.append(Finding(
        KEY, "overlay-dangling", INFO,
        f"{len(dangling)} overlay(s) target a package that does not exist",
        items + (f" (+{len(dangling) - _MAX_LIST} more)"
                 if len(dangling) > _MAX_LIST else "")
        + ". Those overlays sit idle — they do not break boot, but the "
        "customisation they carry is never applied.",
        "Remove the idle overlays, or install their target package.",
    ))


# entry
def run(ctx):
    findings = []

    ctx.note("Scanning APKs…")
    apks = _scan_apks(ctx)
    if not apks:
        return [Finding(
            KEY, "no-apk", INFO,
            "No APK found",
            "The priv-app/app/overlay folders are empty or absent — the "
            "framework layer cannot be judged.",
        )]

    broken = [a for a in apks if a.error]
    ctx.shared["apks"] = apks

    findings.append(Finding(
        KEY, "scanned", INFO,
        f"{len(apks)} APKs read, {len({a.package for a in apks if a.package})} unique packages",
        f"{sum(1 for a in apks if a.privileged)} in priv-app"
        + (f", {len(broken)} unreadable" if broken else ""),
    ))

    if broken:
        findings.append(Finding(
            KEY, "apk-unreadable", WARN,
            f"{len(broken)} APK(s) could not be read",
            "; ".join(f"{a.name}: {a.error}" for a in broken[:_MAX_LIST])
            + (f" (+{len(broken) - _MAX_LIST} more)" if len(broken) > _MAX_LIST else "")
            + ". A corrupt APK is rejected by PackageManager at boot.",
            "Replace them with intact copies from the source ROM.",
        ))

    ctx.note("Reading permission XML…")
    allow, deny, libs = _read_permission_xml(ctx)

    _check_core(ctx, apks, findings)
    ctx.note("Checking privapp-permissions…")
    _check_privapp(ctx, apks, allow, deny, findings)
    _check_libraries(ctx, apks, libs, findings)
    _check_shared_uid(ctx, apks, findings)
    _check_overlays(ctx, apks, findings)

    return findings
