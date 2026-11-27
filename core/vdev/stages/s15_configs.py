# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import posixpath
import re
import xml.etree.ElementTree as ET

from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "configs"
LABEL = "Config syntax & integrity"

_MAX_LIST = 10
_MAX_XML_FILES = 600
_MAX_CIL_FILES = 80
_MAX_RC_FILES = 250

_XML_DIRS = (
    "/system/etc/permissions",
    "/system_ext/etc/permissions",
    "/product/etc/permissions",
    "/vendor/etc/permissions",
    "/odm/etc/permissions",
    "/system/etc/sysconfig",
    "/system_ext/etc/sysconfig",
    "/product/etc/sysconfig",
    "/vendor/etc/sysconfig",
    "/odm/etc/sysconfig",
    "/system/etc/vintf",
    "/system/etc/vintf/manifest",
    "/system_ext/etc/vintf",
    "/system_ext/etc/vintf/manifest",
    "/product/etc/vintf",
    "/product/etc/vintf/manifest",
    "/vendor/etc/vintf",
    "/vendor/etc/vintf/manifest",
    "/odm/etc/vintf",
    "/odm/etc/vintf/manifest",
    "/system/etc/default-permissions",
    "/product/etc/default-permissions",
    "/vendor/etc/default-permissions",
)

_XML_SINGLE_DIRS = (
    ("/vendor/etc", ("audio_", "media_")),
    ("/odm/etc", ("audio_", "media_")),
    ("/system/etc", ("audio_", "media_")),
)

_CIL_DIRS = (
    "/system/etc/selinux",
    "/system/etc/selinux/mapping",
    "/system_ext/etc/selinux",
    "/system_ext/etc/selinux/mapping",
    "/product/etc/selinux",
    "/product/etc/selinux/mapping",
    "/vendor/etc/selinux",
    "/odm/etc/selinux",
)

_RC_DIRS = (
    "/system/etc/init",
    "/system_ext/etc/init",
    "/product/etc/init",
    "/vendor/etc/init",
    "/odm/etc/init",
)

_RE_CONFLICT = re.compile(r"^(?:<<<<<<<\s|=======\s*$|>>>>>>>\s)", re.MULTILINE)


def _has_merge_conflict(text):
    # Require at least <<<<<<< AND (======= or >>>>>>>) to avoid matching comment banners made of =======
    if "<<<<<<<" not in text:
        return False
    has_start = bool(re.search(r"^<<<<<<<(?:\s|$)", text, re.MULTILINE))
    has_end = bool(re.search(r"^>>>>>>>(?:\s|$)", text, re.MULTILINE))
    return has_start and has_end


def _check_cil_parens(text):
    """Verify balanced S-expression parentheses in SELinux .cil text, ignoring ; comments & strings."""
    depth = 0
    in_str = False
    esc = False
    line_no = 1
    first_bad_line = 0
    for ch in text:
        if ch == "\n":
            line_no += 1
            in_str = False
            esc = False
            continue
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0 and not first_bad_line:
                first_bad_line = line_no
    if first_bad_line:
        return f"extra ')' around line {first_bad_line}"
    if depth != 0:
        return f"unclosed '(' (remaining depth={depth})"
    return ""


def _strip_cil_comments(text):
    out_lines = []
    for line in text.splitlines():
        in_str = False
        esc = False
        cut = len(line)
        for i, ch in enumerate(line):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == ";":
                cut = i
                break
        out_lines.append(line[:cut])
    return "\n".join(out_lines)


def _check_xml_files(vfs):
    checked = 0
    corrupt = []
    conflicts = []
    seen = set()

    xml_paths = []
    for d in _XML_DIRS:
        if not vfs.is_dir(d):
            continue
        for name in vfs.listdir(d):
            if name.endswith(".xml"):
                vp = posixpath.join(d, name)
                if vp not in seen:
                    seen.add(vp)
                    xml_paths.append(vp)
    for d, prefixes in _XML_SINGLE_DIRS:
        if not vfs.is_dir(d):
            continue
        for name in vfs.listdir(d):
            if name.endswith(".xml") and name.startswith(prefixes):
                vp = posixpath.join(d, name)
                if vp not in seen:
                    seen.add(vp)
                    xml_paths.append(vp)

    for vp in xml_paths[:_MAX_XML_FILES]:
        raw_bytes = vfs.read_bytes(vp, 512 * 1024)
        if not raw_bytes:
            continue
        # Skip compiled binary AXML (magic 0x00080003) if ever placed under etc/
        if len(raw_bytes) >= 4 and raw_bytes[:4] == b"\x03\x00\x08\x00":
            continue
        checked += 1
        if b"\x00" in raw_bytes:
            corrupt.append((vp, "contains NUL bytes (corrupt/binary XML)"))
            continue
        text = raw_bytes.decode("utf-8", errors="replace").lstrip("\ufeff")
        if not text.strip():
            continue
        if _has_merge_conflict(text):
            conflicts.append(vp)
            continue
        try:
            ET.fromstring(text)
        except ET.ParseError as exc:
            # Some Android OEM XMLs omit a single root element or use bare fragments;
            # wrap only if ParseError is specifically "junk after document element" on multi-root sysconfig/perms?
            # Actually standard Android XML parser (XmlPullParser) ALSO rejects malformed tags/unclosed quotes!
            # Only allow well-formed multi-element fragments if wrapping in <root> parses cleanly AND it's a known fragment?
            # Better: keep strict on standard XML, since Android XmlPullParser requires a single root!
            corrupt.append((vp, str(exc)))

    return checked, corrupt, conflicts


def _check_cil_files(vfs):
    checked = 0
    corrupt = []
    conflicts = []
    seen = set()
    for d in _CIL_DIRS:
        if not vfs.is_dir(d):
            continue
        for name in vfs.listdir(d):
            if not name.endswith(".cil"):
                continue
            vp = posixpath.join(d, name)
            if vp in seen or checked >= _MAX_CIL_FILES:
                continue
            seen.add(vp)
            text = vfs.read_text(vp, 4 * 1024 * 1024)
            if not text:
                continue
            checked += 1
            if _has_merge_conflict(text):
                conflicts.append(vp)
                continue
            err = _check_cil_parens(_strip_cil_comments(text))
            if err:
                corrupt.append((vp, err))
    return checked, corrupt, conflicts


def _check_rc_files(vfs):
    checked = 0
    conflicts = []
    seen_services = {}  # name -> (vpath, exec_path, has_override)
    duplicates = []
    seen_files = set()

    rc_paths = []
    for d in _RC_DIRS:
        if not vfs.is_dir(d):
            continue
        for name in vfs.listdir(d):
            if name.endswith(".rc"):
                vp = posixpath.join(d, name)
                if vp not in seen_files:
                    seen_files.add(vp)
                    rc_paths.append(vp)

    for vp in rc_paths[:_MAX_RC_FILES]:
        text = vfs.read_text(vp, 262144)
        if not text:
            continue
        checked += 1
        if _has_merge_conflict(text):
            conflicts.append(vp)
            continue
        cur_svc = None
        cur_exec = ""
        cur_override = False

        def _flush():
            if not cur_svc:
                return
            prev = seen_services.get(cur_svc)
            if prev is None:
                seen_services[cur_svc] = (vp, cur_exec, cur_override)
            else:
                p_vp, p_exec, p_ovr = prev
                if not cur_override and not p_ovr and p_vp != vp and p_exec != cur_exec:
                    duplicates.append((cur_svc, p_vp, vp))

        for raw in text.splitlines():
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            if not raw[0].isspace():
                _flush()
                cur_svc = None
                cur_exec = ""
                cur_override = False
                parts = s.split()
                if len(parts) >= 3 and parts[0] == "service":
                    cur_svc = parts[1]
                    cur_exec = parts[2]
            elif cur_svc is not None:
                if s == "override" or s.startswith("override "):
                    cur_override = True
        _flush()

    return checked, conflicts, duplicates


def run(ctx):
    findings = []
    vfs = ctx.vfs

    if not ctx.has_system and not ctx.has_vendor and not any(
        vfs.is_dir(d) for d in ("/system/etc", "/vendor/etc", "/product/etc", "/system_ext/etc", "/odm/etc")
    ):
        return [Finding(KEY, "no-configs", INFO, "No system/vendor config directories to validate")]

    ctx.note("Validating boot-critical XML, SELinux .cil & init .rc syntax…")
    xml_n, xml_bad, xml_conf = _check_xml_files(vfs)
    cil_n, cil_bad, cil_conf = _check_cil_files(vfs)
    rc_n, rc_conf, rc_dups = _check_rc_files(vfs)

    all_conflicts = xml_conf + cil_conf + rc_conf

    if all_conflicts:
        sample = ", ".join(all_conflicts[:_MAX_LIST])
        more = f" (+{len(all_conflicts) - _MAX_LIST} more)" if len(all_conflicts) > _MAX_LIST else ""
        findings.append(Finding(
            KEY, "config-merge-conflict", BLOCKER,
            f"{len(all_conflicts)} boot-critical config file(s) contain unresolved Git merge conflicts",
            f"Found <<<<<<< / >>>>>>> markers in: {sample}{more}. "
            "Android XML/SELinux/init parsers will reject these files at boot.",
            "Resolve the merge conflict markers in the listed file(s).",
            all_conflicts[0],
        ))

    if xml_bad:
        # Critical partitions/paths like permissions, sysconfig, vintf, audio/media XML break SystemServer or HALs
        sample = "; ".join(f"{vp} ({err})" for vp, err in xml_bad[:_MAX_LIST])
        more = f" (+{len(xml_bad) - _MAX_LIST} more)" if len(xml_bad) > _MAX_LIST else ""
        findings.append(Finding(
            KEY, "config-syntax-corrupt", BLOCKER,
            f"{len(xml_bad)} boot-critical XML config file(s) failed syntax validation",
            f"{sample}{more}. Malformed XML in permissions/sysconfig/vintf/audio/media causes parser exceptions during boot.",
            "Open the file in RAK Editor and fix the XML syntax error.",
            xml_bad[0][0],
        ))

    if cil_bad:
        sample = "; ".join(f"{vp} ({err})" for vp, err in cil_bad[:_MAX_LIST])
        more = f" (+{len(cil_bad) - _MAX_LIST} more)" if len(cil_bad) > _MAX_LIST else ""
        findings.append(Finding(
            KEY, "config-cil-syntax-corrupt", BLOCKER,
            f"{len(cil_bad)} SELinux .cil policy file(s) have unbalanced parentheses",
            f"{sample}{more}. secilc will abort compiling sepolicy at first-stage init, triggering a reboot to bootloader.",
            "Fix the unbalanced S-expression parentheses in the listed .cil file(s).",
            cil_bad[0][0],
        ))

    if rc_dups:
        sample = "; ".join(f"{svc} ({a} vs {b})" for svc, a, b in rc_dups[:_MAX_LIST])
        more = f" (+{len(rc_dups) - _MAX_LIST} more)" if len(rc_dups) > _MAX_LIST else ""
        findings.append(Finding(
            KEY, "config-duplicate-service", WARN,
            f"{len(rc_dups)} duplicate init service definition(s) without 'override'",
            f"{sample}{more}. Android init ignores the later duplicate service unless 'override' is specified.",
            "Remove the duplicate service block or add 'override' if intentional.",
            rc_dups[0][2],
        ))

    ctx.shared["configs"] = {
        "xml_checked": xml_n,
        "cil_checked": cil_n,
        "rc_checked": rc_n,
        "xml_corrupt": len(xml_bad),
        "cil_corrupt": len(cil_bad),
        "conflicts": len(all_conflicts),
        "rc_duplicates": len(rc_dups),
    }

    total = xml_n + cil_n + rc_n
    if not all_conflicts and not xml_bad and not cil_bad and not rc_dups and total:
        findings.append(Finding(
            KEY, "configs-ok", PASS,
            f"All {total} boot-critical config files passed syntax validation",
            f"{xml_n} XML · {cil_n} SELinux .cil · {rc_n} init .rc",
        ))
    elif not total:
        findings.append(Finding(
            KEY, "no-configs", INFO,
            "No XML/.cil/.rc config files found to validate",
        ))

    return findings
