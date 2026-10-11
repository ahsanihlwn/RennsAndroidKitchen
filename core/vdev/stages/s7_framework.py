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
    out = []
    for vdir in _APK_DIRS:
        if not vfs.is_dir(vdir):
            continue
        # priv-app/app: <dir>/<Nama>/<Nama>.apk ; overlay: <dir>/<Nama>.apk
        for entry in vfs.listdir(vdir):
            sub = posixpath.join(vdir, entry)
            if entry.endswith(".apk") and vfs.is_file(sub):
                ref = vfs.source_ref(sub)
                if ref is not None:
                    out.append(read_apk(sub, ref))
            elif vfs.is_dir(sub):
                for apk in vfs.glob(sub, ".apk"):
                    ref = vfs.source_ref(apk)
                    if ref is not None:
                        out.append(read_apk(apk, ref))
            if len(out) >= _MAX_APK:
                return out
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
    if not vfs.is_file("/system/framework/services.jar"):
        findings.append(Finding(
            KEY, "no-services-jar", BLOCKER,
            "services.jar is missing",
            "services.jar contains system_server itself.",
            "Restore it from a stock system.",
            "/system/framework/services.jar",
        ))

    packages = {a.package for a in apks if a.package}
    if "com.android.providers.settings" not in packages:
        findings.append(Finding(
            KEY, "no-settings-provider", BLOCKER,
            "SettingsProvider is missing",
            "com.android.providers.settings holds the entire Settings.* "
            "database. system_server reads it very early; without it boot "
            "stops.",
            "Restore SettingsProvider.apk into priv-app.",
        ))

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


def _check_privapp(ctx, apks, allow, deny, findings):
    vfs = ctx.vfs
    mode = (ctx.prop("ro.control_privapp_permissions") or "").strip().lower()

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

    platform_cert = (cert_fingerprint(fw)
                     if (fw is not None and cert_available()) else None)
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
        if platform_cert is not None and cert_fingerprint(apk.host) == platform_cert:
            continue
        violations.append((apk, sorted(want)))

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
    fw = vfs.source_ref(_FRAMEWORK_RES)
    platform_cert = cert_fingerprint(fw) if fw is not None else None
    if not platform_cert:
        return

    bad = []
    unknown = []
    total = len(users)
    for apk in users:
        fp = cert_fingerprint(apk.host)
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
        return

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
