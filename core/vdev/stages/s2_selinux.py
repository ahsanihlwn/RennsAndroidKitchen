# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "selinux"
LABEL = "SELinux policy"
REQUIRES = ("system",)

_SYS_SELINUX = "/system/etc/selinux"
_VND_SELINUX = "/vendor/etc/selinux"

_CONTEXT_FILES = (
    (_SYS_SELINUX + "/plat_file_contexts", "system"),
    (_SYS_SELINUX + "/plat_property_contexts", "system"),
    (_SYS_SELINUX + "/plat_service_contexts", "system"),
    (_VND_SELINUX + "/vendor_file_contexts", "vendor"),
    (_VND_SELINUX + "/vendor_property_contexts", "vendor"),
)


def _read_first_line(vfs, vpath):
    text = vfs.read_text(vpath, 4096)
    if not text:
        return ""
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def run(ctx):
    findings = []
    vfs = ctx.vfs

    has_sys = vfs.is_dir(_SYS_SELINUX)
    has_vnd = vfs.is_dir(_VND_SELINUX)

    if not has_sys:
        findings.append(Finding(
            KEY, "no-plat-policy", BLOCKER,
            "No system policy",
            f"{_SYS_SELINUX} not found. Without plat_sepolicy.cil, init has "
            "no policy to load.",
            "Make sure the system partition is fully unpacked.",
            _SYS_SELINUX,
        ))
        return findings

    if not vfs.is_file(_SYS_SELINUX + "/plat_sepolicy.cil"):
        findings.append(Finding(
            KEY, "no-plat-cil", BLOCKER,
            "plat_sepolicy.cil is missing",
            "The core system policy file is absent.",
            "Take it again from a stock system.",
            _SYS_SELINUX + "/plat_sepolicy.cil",
        ))

    if not has_vnd:
        findings.append(Finding(
            KEY, "no-vendor-policy", INFO,
            "No vendor partition in this project — compatibility check skipped",
            "Policy version matching can only be judged when vendor is "
            "unpacked in the same project.",
            "Unpack vendor.img into this project for a full analysis.",
        ))
        return findings

    # ── 1) versi mapping ──
    vers = _read_first_line(vfs, _VND_SELINUX + "/plat_sepolicy_vers.txt")
    if not vers:
        findings.append(Finding(
            KEY, "no-vers", WARN,
            "plat_sepolicy_vers.txt could not be read",
            "The plat policy version vendor was built against is unknown, so "
            "the presence of the mapping file cannot be confirmed.",
            "", _VND_SELINUX + "/plat_sepolicy_vers.txt",
        ))
    else:
        mapping = f"{_SYS_SELINUX}/mapping/{vers}.cil"
        if vfs.is_file(mapping):
            findings.append(Finding(
                KEY, "mapping-ok", PASS,
                f"Mapping policy {vers}.cil is available",
                f"Vendor was built against plat policy {vers}, and system "
                "provides the matching mapping file.",
                "", mapping,
            ))
        else:
            avail = [n[:-4] for n in vfs.listdir(_SYS_SELINUX + "/mapping")
                     if n.endswith(".cil") and not n.endswith(".compat.cil")]
            findings.append(Finding(
                KEY, "mapping-missing", BLOCKER,
                f"Mapping policy {vers}.cil is not present in system",
                f"Vendor asks for plat policy version {vers}, but system only "
                f"has mappings: {', '.join(sorted(avail)) or '(none)'}. init "
                "cannot assemble the policy, so the device will not boot.",
                f"Pick one: use a system that ships {vers}.cil, use a vendor "
                f"matching an available version, or copy {vers}.cil from a ROM "
                f"on the matching version.",
                mapping,
            ))

    # ── 2) precompiled policy vs sha256 ──
    vnd_sha = _read_first_line(
        vfs, _VND_SELINUX + "/precompiled_sepolicy.plat_sepolicy_and_mapping.sha256")
    sys_sha = _read_first_line(
        vfs, _SYS_SELINUX + "/plat_sepolicy_and_mapping.sha256")
    has_precompiled = vfs.is_file(_VND_SELINUX + "/precompiled_sepolicy")
    has_secilc = vfs.is_file("/system/bin/secilc")

    if has_precompiled and vnd_sha and sys_sha:
        if vnd_sha == sys_sha:
            findings.append(Finding(
                KEY, "precompiled-ok", PASS,
                "precompiled_sepolicy matches the system policy",
                "init will use the vendor prebuilt policy — normal boot.",
            ))
        elif has_secilc:
            findings.append(Finding(
                KEY, "precompiled-stale", WARN,
                "Vendor precompiled_sepolicy does not match system",
                f"vendor sha256 {vnd_sha[:16]}… vs system {sys_sha[:16]}…. init "
                "will ignore the prebuilt policy and rebuild it with "
                "/system/bin/secilc at boot — that works, but first boot is "
                "slower and it signals vendor and system come from different "
                "bases.",
                "Safe to leave if the device still boots. To be clean, "
                "regenerate precompiled_sepolicy against this system.",
            ))
        else:
            findings.append(Finding(
                KEY, "precompiled-stale-nosecilc", BLOCKER,
                "precompiled_sepolicy mismatched AND secilc is absent",
                f"vendor sha256 {vnd_sha[:16]}… vs system {sys_sha[:16]}…. init "
                "cannot use the prebuilt policy, and there is no "
                "/system/bin/secilc to rebuild it — the policy fails to load.",
                "Provide /system/bin/secilc, or regenerate a "
                "precompiled_sepolicy that matches this system.",
                _VND_SELINUX + "/precompiled_sepolicy",
            ))
    elif not has_precompiled:
        findings.append(Finding(
            KEY, "no-precompiled", INFO if has_secilc else WARN,
            "Vendor has no precompiled_sepolicy",
            "init will assemble the policy at boot"
            + (" using /system/bin/secilc." if has_secilc
               else ", but /system/bin/secilc is missing too."),
            "" if has_secilc else "Provide secilc in /system/bin.",
        ))

    # ── 3) file contexts ──
    missing_ctx = [p for p, _ in _CONTEXT_FILES if not vfs.is_file(p)]
    if missing_ctx:
        findings.append(Finding(
            KEY, "contexts-missing", WARN,
            f"{len(missing_ctx)} context file(s) missing",
            ", ".join(missing_ctx) + ". Files without correct labels can get "
            "services denied by SELinux even when the binary is present.",
            "Take them again from the corresponding stock partition.",
        ))
    else:
        findings.append(Finding(
            KEY, "contexts", PASS, "System and vendor context files are complete",
        ))

    return findings
