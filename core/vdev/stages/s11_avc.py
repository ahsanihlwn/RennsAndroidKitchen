# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS
from core.vdev.selinux import Policy, FileContexts, UnpackContexts, type_of

KEY = "avc"
LABEL = "SELinux access (AVC)"
REQUIRES = ("system",)

_MAX_LIST = 6


def _denial_line(perm, path, scontext, tcontext, tclass):

    return (f"avc: denied {{ {perm} }} for path=\"{path}\" "
            f"scontext=u:r:{scontext}:s0 tcontext={tcontext} tclass={tclass}")


def _mode_from_cmdline(ctx):

    try:
        from core.vdev.bootimg import kernel_cmdline
    except Exception:                          # noqa: BLE001
        return "", ""
    for _kind, path in getattr(ctx.layout, "boot_images", []):
        try:
            cl = kernel_cmdline(path)
        except Exception:                      # noqa: BLE001
            continue
        for tok in cl.split():
            if tok.startswith("androidboot.selinux="):
                return tok.split("=", 1)[1].strip().lower(), path
    return "", ""


def run(ctx):
    findings = []
    vfs = ctx.vfs

    pol = Policy.load(vfs)
    if pol is None:
        findings.append(Finding(
            KEY, "no-policy", INFO,
            "No sepolicy CIL found — SELinux access cannot be judged",
            "None of the partitions carry *_sepolicy.cil, so there is no way "
            "to tell which accesses the device would allow. This is not a "
            "verdict on the ROM, only the absence of data.",
            "Unpack a system/vendor that ships its selinux policy.",
        ))
        return findings

    fc = FileContexts.load(vfs)
    uc = UnpackContexts.load(ctx.layout)
    init_model = (ctx.shared.get("init") or {}).get("model")
    if init_model is None:
        findings.append(Finding(
            KEY, "no-init", INFO,
            "Init model unavailable — AVC checks skipped",
            "The init stage did not produce a service list, so there is "
            "nothing whose exec chain could be checked.",
        ))
        return findings

    # ── mode: dibaca, bukan diasumsikan ──
    rom_mode, cmdline_src = _mode_from_cmdline(ctx)
    forced = bool(ctx.options.get("force_permissive"))
    already_permissive = (rom_mode == "permissive")
    effective_permissive = already_permissive or forced

    if already_permissive:
        mode_detail = (f"The ROM itself asks for permissive via "
                      f"androidboot.selinux=permissive ({cmdline_src.rsplit(chr(92), 1)[-1]}).")
        if forced:
            mode_detail += " Force permissive changes nothing — it is already permissive."
    elif forced:
        mode_detail = ("Forced to permissive by this panel. The ROM's own "
                      "cmdline does not ask for it" +
                      (f" ({cmdline_src.rsplit(chr(92), 1)[-1]} says "
                       f"androidboot.selinux={rom_mode})" if rom_mode else
                       " (no androidboot.selinux token, so the device would "
                       "boot enforcing)") + ".")
    else:
        mode_detail = ("No androidboot.selinux token in the boot image "
                      "cmdline, so the device boots enforcing — Android's "
                      "default." if not rom_mode else
                      f"Kernel cmdline says androidboot.selinux={rom_mode}.")

    findings.append(Finding(
        KEY, "mode", INFO,
        "SELinux mode: " + ("permissive" if effective_permissive else "enforcing"),
        mode_detail + f" Policy read from {len(pol.sources)} CIL file(s): "
        + ", ".join(s.rsplit("/", 1)[-1] for s in pol.sources) + ".",
    ))

    # ── domain pangkal, diturunkan dari data ──
    kernel_domain = pol.sid_domain("kernel")
    init_label = vfs.selinux("/init") or vfs.selinux("/system/bin/init")
    if not init_label and uc is not None:
        init_label = uc.lookup("/init") or uc.lookup("/system/bin/init")
    if not init_label and fc is not None:
        init_label = fc.lookup("/init") or fc.lookup("/system/bin/init")
    init_domain = ""
    if kernel_domain and init_label:
        init_domain = pol.domain_for_exec(kernel_domain, type_of(init_label))
    if not init_domain:
        findings.append(Finding(
            KEY, "no-init-domain", INFO,
            "init's SELinux domain could not be derived — AVC checks skipped",
            "The chain kernel SID -> /init label -> typetransition did not "
            "resolve, so there is no trustworthy source domain to check "
            "against. Nothing is assumed in its place.",
        ))
        return findings

    # ── periksa rantai exec tiap service yang benar-benar distart ──
    unlabeled = []
    denials = []        # (svc, perm, tclass, tcontext, target_domain)
    checked = 0

    for svc in init_model.started_services:
        path = svc.path
        if not path.startswith("/"):
            continue


        if not vfs.is_file(path):
            continue                        # sudah dilaporkan stage init


        node = vfs.lookup(path)
        real_path = getattr(node, "vpath", "") or path

        label = vfs.selinux(real_path)
        provenance = "xattr" if label else ""
        if not label and uc is not None:
            label = uc.lookup(real_path)
            provenance = "unpack-record" if label else ""
        if not label and fc is not None:
            label = fc.lookup(real_path)
            provenance = "file_contexts" if label else ""
        if not label:
            unlabeled.append(svc.name)
            continue

        exec_type = type_of(label)
        if not exec_type:
            unlabeled.append(svc.name)
            continue

        checked += 1
        inferred = (provenance == "file_contexts")
        target_domain = type_of(svc.seclabel) if svc.seclabel else \
            pol.domain_for_exec(init_domain, exec_type)

        if not pol.allowed(init_domain, exec_type, "file", "execute"):
            denials.append((svc, "execute", "file", label, target_domain, inferred))

        if target_domain and target_domain != init_domain:
            if not pol.allowed(target_domain, exec_type, "file", "entrypoint"):
                denials.append((svc, "entrypoint", "file", label, target_domain, inferred))
            if not pol.allowed(init_domain, target_domain, "process", "transition"):
                denials.append((svc, "transition", "process",
                                f"u:r:{target_domain}:s0", target_domain, inferred))
        else:
            # Tidak ada transisi: service jalan di domain init sendiri.
            if not pol.allowed(init_domain, exec_type, "file", "execute_no_trans"):
                denials.append((svc, "execute_no_trans", "file", label, init_domain, inferred))

    if not checked:
        findings.append(Finding(
            KEY, "nothing-checked", INFO,
            "No service could be checked for AVC",
            "Either no service is reached by the trigger chain, or none of "
            "their binaries carry a SELinux label in this project.",
        ))
        return findings

    # ── klasifikasi menurut AKIBAT, bukan sekadar "ada denial" ──
    fatal, noisy, silent, guessed = [], [], [], []
    for svc, perm, tclass, tcontext, tdom, inferred in denials:
        scontext = tdom if perm in ("entrypoint",) else init_domain
        line = _denial_line(perm, svc.path, scontext, tcontext, tclass)
        if pol.silenced(scontext, type_of(tcontext), tclass, perm):
            silent.append((svc, line))
        elif inferred:
            # Labelnya DITEBAK dari file_contexts, bukan dibaca dari xattr.
            # Tebakan tidak boleh memvonis — cukup ditandai untuk dicek.
            guessed.append((svc, line))
        elif (not effective_permissive) and svc.critical:
            fatal.append((svc, line))
        else:
            noisy.append((svc, line, perm))

    if fatal:
        items = "; ".join(f"{s.name}: {ln}" for s, ln in fatal[:_MAX_LIST])
        findings.append(Finding(
            KEY, "exec-denied-critical", BLOCKER,
            f"{len(fatal)} `critical` service(s) are denied their own exec chain",
            items + (f" (+{len(fatal) - _MAX_LIST} more)"
                    if len(fatal) > _MAX_LIST else "")
            + ". In enforcing mode the exec itself fails, so the process never "
            "starts; a `critical` service that keeps failing makes init reboot "
            "the device.",
            "Fix the label on the binary (restorecon / correct file_contexts "
            "after repack), or add the missing allow rule to the policy.",
        ))

    if noisy:
        items = "; ".join(f"{s.name}: {ln}" for s, ln, _p in noisy[:_MAX_LIST])
        if effective_permissive:
            detail = (". SELinux is permissive here, so the access still goes "
                      "through — but every one of these prints an avc line, "
                      "and a service that retries prints it over and over.")
            hint = ("Nothing is blocked right now. It WILL block the moment "
                    "this runs enforcing, so fix the labels before shipping.")
        else:
            detail = (". These are not `critical`, so init keeps retrying them "
                      "instead of rebooting: the device still boots, the "
                      "service stays dead, and the log fills up with the same "
                      "denial. This is the 'spams but boots' case.")
            hint = ("Fix if you need that service; ignore only if you know the "
                    "feature is dead weight in this port.")
        findings.append(Finding(
            KEY, "exec-denied", WARN,
            f"{len(noisy)} service(s) denied part of their exec chain "
            "(boot continues)",
            items + (f" (+{len(noisy) - _MAX_LIST} more)"
                    if len(noisy) > _MAX_LIST else "") + detail,
            hint,
        ))

    if silent:
        findings.append(Finding(
            KEY, "exec-denied-silent", INFO,
            f"{len(silent)} denial(s) are silenced by dontaudit",
            "; ".join(f"{s.name}: {ln}" for s, ln in silent[:_MAX_LIST])
            + ". The policy explicitly marks these dontaudit, so they never "
            "reach the log at all — the ROM's authors already decided they "
            "are harmless.",
        ))

    if guessed:
        findings.append(Finding(
            KEY, "exec-denied-inferred", WARN,
            f"{len(guessed)} denial(s) found using INFERRED labels — verify "
            "against the image",
            "; ".join(f"{s.name}: {ln}" for s, ln in guessed[:_MAX_LIST])
            + (f" (+{len(guessed) - _MAX_LIST} more)"
              if len(guessed) > _MAX_LIST else "")
            + ". This project reads those partitions from unpacked folders, "
            "which do not keep SELinux xattrs, so the label was matched from "
            "file_contexts instead of read from the partition. That match is "
            "an approximation, so these are NOT reported as blockers.",
            "Point the analysis at the .img to read the real labels before "
            "acting on any of these.",
        ))

    if unlabeled:
        findings.append(Finding(
            KEY, "unlabeled", INFO,
            f"{len(unlabeled)} service binary/binaries carry no SELinux label here",
            ", ".join(unlabeled[:_MAX_LIST])
            + (f" (+{len(unlabeled) - _MAX_LIST} more)"
              if len(unlabeled) > _MAX_LIST else "")
            + ". Their label could not be read (unpacked folders do not keep "
            "xattrs) and file_contexts had no match either, so their exec "
            "chain was NOT judged — this is missing data, not a pass.",
            "Analyse the .img instead of the unpacked folder to get real labels.",
        ))

    if not denials:
        findings.append(Finding(
            KEY, "exec-ok", PASS,
            f"All {checked} checked service(s) are allowed their exec chain",
            f"execute + entrypoint + transition resolve against the ROM's own "
            f"policy for every service the trigger chain starts, using the "
            f"real label on each binary. Source domain derived as "
            f"'{init_domain}' from the kernel SID and /init's label.",
        ))

    ctx.shared["avc"] = {
        "permissive": effective_permissive,
        "rom_mode": rom_mode or "enforcing",
        "forced": forced,
        "checked": checked,
        "fatal": len(fatal),
        "noisy": len(noisy),
        "silent": len(silent),
        "inferred": len(guessed),
        "init_domain": init_domain,
    }
    return findings
