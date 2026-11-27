# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import io
import posixpath
import struct
import zipfile

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


def _parse_vdex_checksums(raw: bytes) -> tuple[list[int] | None, bool]:
    """Return (checksum_list, has_embedded_dex_section) from Android vdex027 / vdex021 header."""
    if not raw or len(raw) < 28 or raw[:4] != b"vdex":
        return None, False
    ver = raw[4:8]
    try:
        if ver.startswith(b"027"):
            nsec = struct.unpack_from("<I", raw, 8)[0]
            if nsec == 0 or 12 + nsec * 12 > len(raw):
                return None, False
            crcs = None
            has_dex_sec = False
            for i in range(nsec):
                kind, off, sz = struct.unpack_from("<III", raw, 12 + i * 12)
                if kind == 0 and sz >= 4 and (sz % 4 == 0) and off + sz <= len(raw):
                    crcs = list(struct.unpack_from(f"<{sz // 4}I", raw, off))
                elif kind == 1 and sz > 0:
                    has_dex_sec = True
            return crcs, has_dex_sec
        if ver.startswith((b"021", b"019", b"015")):
            ndex = struct.unpack_from("<I", raw, 12)[0]
            if ndex == 0 or ndex > 128 or 28 + ndex * 4 > len(raw):
                return None, False
            crcs = list(struct.unpack_from(f"<{ndex}I", raw, 28))
            return crcs, True
    except struct.error:
        return None, False
    return None, False


def _jar_dex_zip_crcs(raw: bytes) -> list[int] | None:
    """Read ZIP entry CRC-32 for classes*.dex (matches ART VDEX kChecksumSection)."""
    if not raw or len(raw) < 22:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            dex_names = [
                n for n in zf.namelist()
                if n.startswith("classes") and n.endswith(".dex") and "/" not in n
            ]
            if not dex_names:
                return []
            def _dex_key(name: str):
                mid = name[len("classes"):-len(".dex")]
                return (0, 0) if not mid else (1, int(mid) if mid.isdigit() else 9999)
            dex_names.sort(key=_dex_key)
            return [zf.getinfo(n).CRC for n in dex_names]
    except Exception:
        return None


def _audit_art_oat_vdex(vfs, findings) -> dict:
    """Verify companion .vdex/.odex DEX checksums against framework JARs and critical priv-apps.

    Catches the #1 ROM porting/modding bootloop: patching classes.dex inside services.jar,
    framework.jar, or SystemUI.apk while leaving stale precompiled oat/*.vdex/.odex behind.
    """
    pairs = []  # (companion_vpath, vdex_vpath, is_critical)
    missing_vdex_for_odex = []
    boot_images = []

    # 1) Boot image artifacts under /system/framework/<abi>/boot-*.vdex|.art
    for abi in ("arm64", "arm"):
        fdir = f"/system/framework/{abi}"
        if not vfs.is_dir(fdir):
            continue
        for nm in sorted(vfs.listdir(fdir)):
            if nm.endswith(".art"):
                boot_images.append(f"{fdir}/{nm}")
            elif nm.startswith("boot-") and nm.endswith(".vdex"):
                jar_stem = nm[len("boot-"):-len(".vdex")]
                cand_jar = f"/system/framework/{jar_stem}.jar"
                if vfs.is_file(cand_jar):
                    pairs.append((cand_jar, f"{fdir}/{nm}", True))

    # 2) Framework oat/*.vdex + *.odex across system / system_ext / product
    for fbase in ("/system/framework", "/system_ext/framework", "/product/framework"):
        oat_root = f"{fbase}/oat"
        if not vfs.is_dir(oat_root):
            continue
        for abi in ("arm64", "arm"):
            adir = f"{oat_root}/{abi}"
            if not vfs.is_dir(adir):
                continue
            entries = set(vfs.listdir(adir))
            for nm in sorted(entries):
                if nm.endswith(".odex"):
                    vd = nm[:-5] + ".vdex"
                    if vd not in entries:
                        missing_vdex_for_odex.append(f"{adir}/{nm}")
                elif nm.endswith(".vdex"):
                    stem = nm[:-5]
                    cand_jar = f"{fbase}/{stem}.jar"
                    if vfs.is_file(cand_jar):
                        pairs.append((cand_jar, f"{adir}/{nm}", True))

    # 3) Critical priv-app oat/*.vdex (SystemUI, Settings, Launcher, Telecom, Framework plugins)
    _CRIT_PRIV = ("systemui", "settings", "launcher", "telecom", "telephony", "keyguard")
    for pbase in ("/system/priv-app", "/system_ext/priv-app", "/product/priv-app"):
        if not vfs.is_dir(pbase):
            continue
        for app_dir in sorted(vfs.listdir(pbase)):
            if not any(tok in app_dir.lower() for tok in _CRIT_PRIV):
                continue
            oat_root = f"{pbase}/{app_dir}/oat"
            if not vfs.is_dir(oat_root):
                continue
            for abi in ("arm64", "arm"):
                adir = f"{oat_root}/{abi}"
                if not vfs.is_dir(adir):
                    continue
                entries = set(vfs.listdir(adir))
                for nm in sorted(entries):
                    if nm.endswith(".odex"):
                        vd = nm[:-5] + ".vdex"
                        if vd not in entries:
                            missing_vdex_for_odex.append(f"{adir}/{nm}")
                    elif nm.endswith(".vdex"):
                        stem = nm[:-5]
                        cand_apk = f"{pbase}/{app_dir}/{stem}.apk"
                        if vfs.is_file(cand_apk):
                            is_crit = "systemui" in app_dir.lower()
                            pairs.append((cand_apk, f"{adir}/{nm}", is_crit))

    checked = 0
    stripped_ok = 0
    mismatches = []  # (comp_vp, vdex_vp, is_crit, detail)
    seen_pairs = set()
    for comp_vp, vdex_vp, is_crit in pairs:
        if (comp_vp, vdex_vp) in seen_pairs:
            continue
        seen_pairs.add((comp_vp, vdex_vp))
        vdex_raw = vfs.read_bytes(vdex_vp)
        vcrcs, has_dex_sec = _parse_vdex_checksums(vdex_raw or b"")
        if vcrcs is None:
            continue
        comp_raw = vfs.read_bytes(comp_vp)
        jcrcs = _jar_dex_zip_crcs(comp_raw or b"")
        if jcrcs is None:
            continue
        checked += 1
        if len(jcrcs) == 0:
            # Dexpreopt-stripped JAR/APK: DEX lives inside the .vdex/.odex only (normal on stock ROMs)
            if has_dex_sec or vfs.is_file(vdex_vp[:-5] + ".odex"):
                stripped_ok += 1
            continue
        if jcrcs != vcrcs:
            mismatches.append((
                comp_vp, vdex_vp, is_crit,
                f"{comp_vp} DEX CRCs {[hex(c) for c in jcrcs]} != {vdex_vp} {[hex(c) for c in vcrcs]}",
            ))

    if mismatches:
        crit_mm = [m for m in mismatches if m[2]]
        sev = BLOCKER if crit_mm else WARN
        findings.append(Finding(
            KEY, "art-vdex-checksum-mismatch", sev,
            f"{len(mismatches)} stale ART .vdex/.odex checksum mismatch(es) detected",
            "; ".join(m[3] for m in mismatches)
            + ". ART rejects stale precompiled oat/vdex whose checksums differ from classes*.dex "
              "(classic bootloop after patching services.jar / framework.jar / SystemUI.apk without removing oat/).",
            "Delete the stale oat/ (.odex/.vdex) directory for the modified JAR/APK or re-run dex2oat.",
            mismatches[0][1],
        ))
    elif checked:
        findings.append(Finding(
            KEY, "art-vdex-ok", PASS,
            f"ART .vdex/.odex checksums coherent across {checked} core JAR/APK pair(s)",
            f"Verified DEX ZIP CRC-32 tables against companion .vdex headers"
            + (f" ({stripped_ok} dexpreopt-stripped)" if stripped_ok else "")
            + (f" · {len(boot_images)} boot .art image(s)" if boot_images else "") + ".",
        ))

    if missing_vdex_for_odex:
        findings.append(Finding(
            KEY, "art-odex-missing-vdex", WARN,
            f"{len(missing_vdex_for_odex)} .odex file(s) missing companion .vdex",
            ", ".join(missing_vdex_for_odex)
            + ". Modern ART requires paired .odex + .vdex files.",
            "Remove the orphaned .odex file or restore its matching .vdex.",
            missing_vdex_for_odex[0],
        ))

    return {
        "vdex_checked": checked,
        "vdex_stripped_ok": stripped_ok,
        "vdex_mismatches": [m[3] for m in mismatches],
        "missing_vdex_for_odex": missing_vdex_for_odex,
        "boot_images": boot_images,
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

    # ── ART .vdex / .odex / boot-*.art companion checksum integrity ──
    art_info = _audit_art_oat_vdex(vfs, findings)
    ctx.shared["runtime"] = {
        "zygote": zygote or "",
        "apex_count": len(apex_files) + len(apex_dirs),
        "art_oat": art_info,
    }

    return findings
