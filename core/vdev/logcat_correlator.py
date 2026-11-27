# SPDX-License-Identifier: MPL-2.0
"""Real Android logcat / dmesg / console-ramoops correlator against VirtualFS & vdev Report.

Parses real bootloop crash logs from a device (file import or live ADB capture),
identifies root-cause failure signatures (linker missing .so / symbol, Java/ART
exceptions, SELinux AVC denials, init failures, ServiceManager HAL stalls,
PackageManager signature/privapp rejections, AVB/FBE/vold mount errors), and
cross-correlates every event against the active ROM's VirtualFS and static report.
"""
from __future__ import annotations

import os
import posixpath
import re
import subprocess

_RE_LINK_LIB = re.compile(
    r'CANNOT LINK EXECUTABLE\s+"([^"]+)".*?library\s+"([^"]+)"\s+not found',
    re.IGNORECASE,
)
_RE_LINK_SYM = re.compile(
    r'CANNOT LINK EXECUTABLE\s+"([^"]+)".*?cannot locate symbol\s+"([^"]+)"(?:\s+referenced by\s+"([^"]+)")?',
    re.IGNORECASE,
)
_RE_AVC = re.compile(
    r'avc:\s+denied\s+\{\s*([^}]+)\s*\}.*?scontext=(\S+).*?tcontext=(\S+).*?tclass=(\S+)',
    re.IGNORECASE,
)
_RE_JAVA_EXC = re.compile(
    r'\b(java\.lang\.(?:ClassNotFoundException|NoClassDefFoundError|NoSuchMethodError|NoSuchFieldError|'
    r'UnsatisfiedLinkError|VerifyError|AbstractMethodError|IncompatibleClassChangeError|NullPointerException|'
    r'IllegalStateException|SecurityException)|android\.content\.res\.Resources\$NotFoundException):\s*(.+)',
)
_RE_INIT_SVC = re.compile(
    r'init(?:\[\d+\])?\s*:\s*(?:Could not start service|Cannot find|Unable to start service)\s+[\'"]?([^\'"\s:]+)[\'"]?',
    re.IGNORECASE,
)
_RE_SVC_WAIT = re.compile(
    r'(?:ServiceManager|hwservicemanager|servicemanager).*?(?:Waiting for service|Service)\s+[\'"]?([a-zA-Z0-9_./@-]+)[\'"]?',
    re.IGNORECASE,
)
_RE_SHARED_UID = re.compile(
    r'Signature mismatch for shared user\s+.*?([a-zA-Z0-9_.]+)|INSTALL_FAILED_SHARED_USER_INCOMPATIBLE',
    re.IGNORECASE,
)
_RE_PRIVAPP = re.compile(
    r'Privileged permission\s+(\S+)\s+for package\s+(\S+).*?not in privapp-permissions',
    re.IGNORECASE,
)
_RE_MOUNT_ERR = re.compile(
    r'(?:fs_mgr|vold|avb).*?(Failed to mount\s+\S+|dm-verity|avb_slot_verify.*failed|keymint|keymaster|metadata_encryption|fileencryption|FBE)',
    re.IGNORECASE,
)


def _find_so_in_vfs(vfs, soname: str) -> str:
    if vfs is None or not soname:
        return ""
    for d in (
        "/system/lib64", "/system_ext/lib64", "/product/lib64", "/vendor/lib64", "/odm/lib64",
        "/system/lib", "/system_ext/lib", "/product/lib", "/vendor/lib", "/odm/lib",
    ):
        cand = posixpath.join(d, soname)
        if vfs.is_file(cand):
            return cand
    return ""


def _finding_codes(report) -> dict:
    out = {}
    if report is None:
        return out
    for f in getattr(report, "findings", None) or ():
        out.setdefault(f.code, []).append(f)
    return out


def correlate_log(log_text: str, vfs=None, report=None, ctx=None) -> dict:
    """Parse real device logcat/dmesg/ramoops and correlate against VirtualFS & static Report."""
    lines = (log_text or "").splitlines()
    code_map = _finding_codes(report)

    events = []
    seen_keys = set()
    avc_lines = []
    categories = {}

    def _add(sev, cat, title, detail, vfs_path="", matched_code="", hint="", raw_line=""):
        key = (cat, title)
        if key in seen_keys:
            return
        seen_keys.add(key)
        matched = bool(matched_code and matched_code in code_map)
        categories[cat] = categories.get(cat, 0) + 1
        events.append({
            "severity": sev,
            "category": cat,
            "title": title,
            "detail": detail,
            "vfsPath": vfs_path,
            "matchedStatic": matched,
            "matchedCode": matched_code if matched else "",
            "hint": hint,
            "rawLine": raw_line[:320],
        })

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        # 1) Linker missing .so
        m = _RE_LINK_LIB.search(line)
        if m:
            binary, lib = m.group(1), m.group(2)
            found_vp = _find_so_in_vfs(vfs, lib)
            bin_vp = binary if (vfs is not None and vfs.is_file(binary)) else ""
            if found_vp:
                detail = f"{binary} cannot find {lib}, but it exists in VFS at {found_vp} (misplaced or wrong ABI)."
                mc = "abi-mismatch" if "abi-mismatch" in code_map else "lib-misplaced"
                hint = f"Move/copy {found_vp} into the search path matching {binary}'s partition & ABI."
            else:
                detail = f"{binary} failed to load missing shared library {lib} (not found in VFS)."
                mc = "lib-missing"
                hint = f"Restore {lib} from stock ROM into {posixpath.dirname(binary).replace('/bin', '/lib64')}."
            _add("blocker", "linker", f"Missing library {lib} ({posixpath.basename(binary)})",
                 detail, found_vp or bin_vp, mc, hint, line)
            continue

        # 2) Linker cannot locate C++ symbol
        m = _RE_LINK_SYM.search(line)
        if m:
            binary, sym, ref_by = m.group(1), m.group(2), m.group(3) or m.group(1)
            vp = ref_by if (vfs is not None and vfs.is_file(ref_by)) else (
                binary if (vfs is not None and vfs.is_file(binary)) else ""
            )
            mc = "elf-symbol-missing-critical" if "elf-symbol-missing-critical" in code_map else "elf-symbol-missing"
            _add("blocker", "linker", f"Unresolved symbol {sym[:52]} ({posixpath.basename(binary)})",
                 f"{binary} (via {ref_by}) failed dynamic linking: cannot locate symbol '{sym}'.",
                 vp, mc,
                 "Align the daemon binary and its shared libraries (libgui/libui/libbinder/VNDK) to the same ABI build.",
                 line)
            continue

        # 3) SELinux avc: denied
        m = _RE_AVC.search(line)
        if m:
            avc_lines.append(line)
            perms, scontext, tcontext, tclass = m.group(1).strip(), m.group(2), m.group(3), m.group(4)
            s_type = scontext.split(":")[2] if scontext.count(":") >= 2 else scontext
            t_type = tcontext.split(":")[2] if tcontext.count(":") >= 2 else tcontext
            mc = "exec-denied" if "exec-denied" in code_map else ("avc-denied" if "avc-denied" in code_map else "")
            _add("warn", "selinux", f"AVC denied {{{perms}}} {s_type} -> {t_type}:{tclass}",
                 f"scontext={scontext} tcontext={tcontext} tclass={tclass}",
                 "/system/etc/selinux/plat_sepolicy.cil" if (vfs and vfs.is_file("/system/etc/selinux/plat_sepolicy.cil")) else "",
                 mc, "Add the generated CIL/TE allow rule in Auto-Fix.", line)
            continue

        # 4) Privapp permission allowlist crash
        m = _RE_PRIVAPP.search(line)
        if m:
            perm, pkg = m.group(1), m.group(2)
            _add("blocker", "framework", f"Privapp allowlist missing: {pkg}",
                 f"Package {pkg} requested privileged permission {perm} not listed in privapp-permissions.",
                 "/system/etc/permissions", "privapp-violation",
                 f"Add <privapp-permissions package=\"{pkg}\"><permission name=\"{perm}\"/></privapp-permissions>.",
                 line)
            continue

        # 5) SharedUserId signature mismatch
        m = _RE_SHARED_UID.search(line)
        if m:
            uid = m.group(1) or "sharedUserId"
            mc = "shared-uid-mismatch" if "shared-uid-mismatch" in code_map else "shared-uid-group-mismatch"
            _add("blocker", "framework", f"APK signature mismatch for {uid}",
                 line, "/system/framework/framework-res.apk" if (vfs and vfs.is_file("/system/framework/framework-res.apk")) else "",
                 mc, "Re-sign conflicting system APK(s) with this ROM's platform key or use matching ROM build.", line)
            continue

        # 6) Java / ART runtime exception
        m = _RE_JAVA_EXC.search(line)
        if m:
            exc_full, msg = m.group(1), m.group(2).strip()
            exc_short = exc_full.rsplit(".", 1)[-1]
            mc = ""
            vp = "/system/framework/services.jar" if (vfs and vfs.is_file("/system/framework/services.jar")) else ""
            hint = "Inspect the target JAR/APK in DEX bytecode stage and align framework/services base."
            if exc_short in ("ClassNotFoundException", "NoClassDefFoundError"):
                mc = "dex-missing-superclass" if "dex-missing-superclass" in code_map else (
                    "dex-unresolved-boot-service" if "dex-unresolved-boot-service" in code_map else "dex-oem-framework-mismatch"
                )
            elif exc_short == "NoSuchMethodError":
                mc = "dex-missing-method"
            elif exc_short == "NoSuchFieldError":
                mc = "dex-missing-field"
            elif exc_short == "UnsatisfiedLinkError":
                mc = "dex-missing-jni-critical" if "dex-missing-jni-critical" in code_map else (
                    "dex-missing-jni-symbol" if "dex-missing-jni-symbol" in code_map else "dex-missing-jni-lib"
                )
                so_m = re.search(r'"([a-zA-Z0-9_.-]+\.so)"', msg)
                if so_m:
                    found = _find_so_in_vfs(vfs, so_m.group(1))
                    if found:
                        vp = found
                hint = "Restore the missing JNI .so library or verify its exported Java_* / JNI_OnLoad symbols."
            elif exc_short == "VerifyError":
                mc = "dex-verify-error"
            elif exc_short in ("AbstractMethodError", "IncompatibleClassChangeError"):
                mc = "dex-incompat-change"
            elif "NotFoundException" in exc_short:
                mc = "dex-missing-framework-res" if "dex-missing-framework-res" in code_map else "overlay-orphaned-resources"
                hint = "Check framework-res.apk and RRO overlay resource IDs."
            sev = "blocker" if exc_short not in ("NullPointerException", "SecurityException") else "warn"
            _add(sev, "runtime", f"{exc_short}: {msg[:72]}", msg, vp, mc, hint, line)
            continue

        # 7) Init service start failure
        m = _RE_INIT_SVC.search(line)
        if m:
            target = m.group(1)
            vp = target if (target.startswith("/") and vfs is not None and vfs.is_file(target)) else ""
            mc = "service-missing" if "service-missing" in code_map else "services-killed"
            _add("warn", "init", f"init failed: {target}", line, vp, mc,
                 "Check the service binary path, shared libraries, and SELinux exec context in init.rc.", line)
            continue

        # 8) ServiceManager / HAL stall
        m = _RE_SVC_WAIT.search(line)
        if m:
            svc = m.group(1)
            mc = "dex-blocking-hal-undeclared" if "dex-blocking-hal-undeclared" in code_map else "hal-missing"
            _add("warn", "vintf", f"Waiting for HAL/service: {svc}", line,
                 "/vendor/etc/vintf/manifest.xml" if (vfs and vfs.is_file("/vendor/etc/vintf/manifest.xml")) else "",
                 mc, "Verify this HAL service is declared in vendor VINTF manifest and its daemon starts cleanly.", line)
            continue

        # 9) Mount / AVB / dm-verity / FBE / vold error
        m = _RE_MOUNT_ERR.search(line)
        if m:
            tok = m.group(1)
            low = line.lower()
            if "verity" in low or "avb" in low:
                mc = "verity-digest-mismatch" if "verity-digest-mismatch" in code_map else "avb-verity-will-fail"
                hint = "Disable dm-verity/AVB in vbmeta or fix modified partition digests."
            elif any(k in low for k in ("keymint", "keymaster", "encrypt", "fbe", "vold")):
                mc = "fbe-keystore-hal-missing" if "fbe-keystore-hal-missing" in code_map else "fbe-metadata-mount-missing"
                hint = "Check /data fileencryption flags in fstab and vendor KeyMint/Keymaster/Gatekeeper HALs."
            else:
                mc = "mountpoint-missing"
                hint = "Verify fstab mount points and first-stage kernel modules."
            _add("blocker", "mount", f"Mount/AVB/FBE failure ({tok[:40]})", line, "", mc, hint, line)

    # Generate ready-to-apply SELinux CIL & TE rules from real AVC denials via Rust!
    generated_cil = ""
    generated_te = ""
    if avc_lines:
        try:
            from core import native_backend
            res = native_backend.selinux_audit_allow("\n".join(avc_lines[:500]))
            generated_cil = str(res.get("cil_rules") or "")
            generated_te = str(res.get("te_rules") or "")
        except Exception:
            pass

    matched_cnt = sum(1 for e in events if e["matchedStatic"])
    return {
        "ok": True,
        "linesParsed": len(lines),
        "totalEvents": len(events),
        "matchedStatic": matched_cnt,
        "runtimeOnly": len(events) - matched_cnt,
        "avcCount": len(avc_lines),
        "categories": categories,
        "events": events[:150],
        "generatedCil": generated_cil,
        "generatedTe": generated_te,
    }


def capture_adb_log(mode: str = "logcat", timeout: int = 10) -> tuple[bool, str]:
    """Capture live logcat or dmesg from a connected Android device via RAK's resolved ADB binary."""
    adb_args = ["logcat", "-d", "-v", "threadtime"] if mode == "logcat" else ["shell", "dmesg"]
    # Prefer RAK's native_backend / installed package registry ADB resolution
    try:
        from core import native_backend
        if native_backend._get_native() is not None and hasattr(native_backend, "adb_run_json"):
            pass
    except Exception:
        pass
    adb_exe = ""
    try:
        from core.android_tools import resolve_platform_tool
        adb_exe = resolve_platform_tool("adb") or ""
    except Exception:
        pass
    if not adb_exe:
        try:
            from core.packages import find_installed_tool
            adb_exe = find_installed_tool("adb") or ""
        except Exception:
            pass
    if not adb_exe:
        import shutil
        adb_exe = shutil.which("adb") or ""
    if not adb_exe:
        return False, "ADB binary not found in installed platform-tools registry or PATH."

    try:
        proc = subprocess.run(
            [adb_exe, *adb_args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if proc.returncode != 0:
            return False, err or f"adb exited with code {proc.returncode}"
        if not out:
            return False, "No log output received from connected device."
        return True, out
    except subprocess.TimeoutExpired:
        return False, f"ADB timed out after {timeout}s (is device connected & authorized?)."
    except Exception as exc:
        return False, f"Failed to run ADB: {exc}"
