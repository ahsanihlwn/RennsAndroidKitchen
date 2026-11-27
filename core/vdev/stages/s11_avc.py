# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS
from core.vdev.selinux import (
    Policy, FileContexts, UnpackContexts, ServiceContexts, PropertyContexts, type_of,
)

KEY = "avc"
LABEL = "SELinux access (AVC)"
REQUIRES = ("system",)

_MAX_LIST = 6

# Core AIDL/native HAL services checked against service_contexts + SELinux client { find } rules
_CRITICAL_BINDER_CLIENTS = (
    ("android.hardware.graphics.composer3.IComposer", ("surfaceflinger",), True),
    ("android.hardware.graphics.allocator.IAllocator", ("surfaceflinger", "bootanim"), True),
    ("mapper/", ("surfaceflinger", "bootanim"), True),
    ("android.hardware.power.IPower", ("system_server",), False),
    ("android.hardware.light.ILights", ("system_server",), False),
    ("android.hardware.health.IHealth", ("system_server",), False),
    ("android.hardware.security.keymint.IKeyMintDevice", ("keystore",), True),
    ("android.hardware.gatekeeper.IGatekeeper", ("gatekeeperd",), False),
)

_CRITICAL_PROP_PREFIXES = (
    "ro.hardware.",
    "ro.zygote",
    "ro.surface_flinger.",
    "ro.opengles.version",
    "ro.board.platform",
    "ro.vendor.api_level",
)


def _denial_line(perm, path, scontext, tcontext, tclass):

    return (f"avc: denied {{ {perm} }} for path=\"{path}\" "
            f"scontext=u:r:{scontext}:s0 tcontext={tcontext} tclass={tclass}")


def _mode_from_cmdline(ctx):

    boot_props = getattr(ctx.layout, "boot_props", None) or {}
    if boot_props.get("androidboot.selinux"):
        return str(boot_props["androidboot.selinux"]).strip().lower(), "boot_cmdline"
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
    xattr_drift = []    # (real_path, actual_xattr, expected_fc)
    service_domains = {}  # svc.name -> target_domain
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
        if label and fc is not None:
            expected_fc = fc.lookup(real_path)
            if expected_fc and type_of(expected_fc) and type_of(expected_fc) != type_of(label):
                xattr_drift.append((real_path, label, expected_fc))
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
        service_domains[svc.name] = target_domain or init_domain

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

    if xattr_drift:
        drift_items = "; ".join(
            f"{p} (xattr={type_of(a)} vs file_contexts={type_of(e)})"
            for p, a, e in xattr_drift[:_MAX_LIST]
        )
        findings.append(Finding(
            KEY, "xattr-drift", INFO,
            f"{len(xattr_drift)} service binary/binaries differ between image xattr and file_contexts",
            drift_items + (f" (+{len(xattr_drift) - _MAX_LIST} more)"
                           if len(xattr_drift) > _MAX_LIST else "")
            + ". If repacked without restoring file_contexts, labels may shift.",
            "Review file_contexts or use ext_edit_selinux/erofs_edit_selinux to align labels.",
        ))

    # ── Binder service_contexts { find } audit for core VINTF AIDL/native HALs ──
    sc = ServiceContexts.load(vfs, kind="service")
    binder_checked = []
    binder_denials = []
    binder_unlabeled = []
    vintf_hals = (ctx.shared.get("vintf") or {}).get("provided_hals") or {}
    if sc is not None and vintf_hals:
        # Build list of concrete declared service names matching _CRITICAL_BINDER_CLIENTS
        targets_to_check = []
        for hname, hinfo in sorted(vintf_hals.items()):
            fmts = set(hinfo.get("formats") or ())
            fqnames = list(hinfo.get("fqnames") or ())
            if "native" in fmts and hname == "mapper":
                for fq in fqnames:
                    # "@5.0/powervr" -> "mapper/powervr"
                    suf = fq.split("/", 1)[1] if "/" in fq else ""
                    if suf:
                        targets_to_check.append((f"mapper/{suf}", ("surfaceflinger", "bootanim"), True))
            elif "aidl" in fmts:
                for pref, clients, is_crit in _CRITICAL_BINDER_CLIENTS:
                    if pref.endswith("/"):
                        continue
                    pkg_pref = pref.rsplit(".", 1)[0]
                    if hname == pkg_pref:
                        if fqnames:
                            for fq in fqnames:
                                svc_full = f"{hname}.{fq}" if "/" in fq and not fq.startswith(hname) else fq
                                targets_to_check.append((svc_full, clients, is_crit))
                        else:
                            targets_to_check.append((f"{pref}/default", clients, is_crit))

        seen_bt = set()
        for svc_full, clients, is_crit in targets_to_check:
            if svc_full in seen_bt:
                continue
            seen_bt.add(svc_full)
            sctx, ssrc, is_def = sc.lookup(svc_full)
            stype = type_of(sctx)
            binder_checked.append({
                "service": svc_full, "context": sctx, "source": ssrc,
                "default_fallback": is_def, "clients": list(clients),
            })
            if not stype or is_def:
                binder_unlabeled.append((svc_full, sctx or "(none)", is_crit))
                continue
            for cli in clients:
                if not pol.allowed(cli, stype, "service_manager", "find"):
                    if not pol.silenced(cli, stype, "service_manager", "find"):
                        binder_denials.append((
                            svc_full, cli, stype, sctx, is_crit,
                            f'avc: denied {{ find }} for service={svc_full} scontext=u:r:{cli}:s0 tcontext={sctx} tclass=service_manager',
                        ))

        if binder_unlabeled:
            crit_u = [x for x in binder_unlabeled if x[2]]
            sev_u = BLOCKER if (not effective_permissive and crit_u) else WARN
            findings.append(Finding(
                KEY, "binder-service-context-missing", sev_u,
                f"{len(binder_unlabeled)} core VINTF HAL service(s) lack an explicit service_contexts label",
                "; ".join(f"{s} -> {c}" for s, c, _ in binder_unlabeled)
                + ". Without an explicit entry in vendor_service_contexts/plat_service_contexts, "
                  "servicemanager falls back to default_android_service and rejects registration/lookup under Enforcing.",
                "Add the HAL service name -> u:object_r:<hal>_service:s0 entry to /vendor/etc/selinux/vendor_service_contexts.",
                "/vendor/etc/selinux/vendor_service_contexts",
            ))
        if binder_denials:
            crit_d = [x for x in binder_denials if x[4]]
            sev_d = BLOCKER if (not effective_permissive and crit_d) else WARN
            findings.append(Finding(
                KEY, "binder-avc-denied", sev_d,
                f"{len(binder_denials)} core Binder/HAL SELinux `find` denial(s)",
                "; ".join(x[5] for x in binder_denials),
                "Align service_contexts labels and allow rules so SurfaceFlinger/SystemServer/BootAnimation can reach declared HALs.",
            ))
        elif binder_checked and not binder_unlabeled:
            findings.append(Finding(
                KEY, "binder-avc-ok", PASS,
                f"All {len(binder_checked)} core VINTF Binder/HAL service_contexts & client permissions verified",
                ", ".join(f"{b['service']} ({type_of(b['context'])})" for b in binder_checked),
            ))

    # ── property_contexts { set } audit for /vendor & /odm build.prop (vendor_init) ──
    pc = PropertyContexts.load(vfs)
    prop_denials_crit = []
    prop_denials_other = []
    prop_checked = 0
    if pc is not None:
        by_part = ((ctx.profile.get("prop_trace") or {}).get("by_partition") or {})
        for part in ("vendor", "odm"):
            for pkey, _pval in sorted((by_part.get(part) or {}).items()):
                prop_checked += 1
                pctx, _psrc = pc.lookup(pkey)
                ptype = type_of(pctx)
                if not ptype:
                    continue
                if not pol.allowed("vendor_init", ptype, "property_service", "set"):
                    if pol.silenced("vendor_init", ptype, "property_service", "set"):
                        continue
                    rec = (pkey, part, pctx,
                           f"avc: denied {{ set }} for property={pkey} ({part}/build.prop) "
                           f"scontext=u:r:vendor_init:s0 tcontext={pctx} tclass=property_service")
                    if any(pkey.startswith(pref) for pref in _CRITICAL_PROP_PREFIXES):
                        prop_denials_crit.append(rec)
                    else:
                        prop_denials_other.append(rec)

        if prop_denials_crit:
            findings.append(Finding(
                KEY, "prop-avc-denied-critical", WARN,
                f"{len(prop_denials_crit)} boot/graphics build.prop propert(ies) denied to vendor_init",
                "; ".join(r[3] for r in prop_denials_crit),
                "Label these properties in vendor_property_contexts or allow vendor_init set access.",
                "/vendor/etc/selinux/vendor_property_contexts",
            ))
        if prop_denials_other:
            findings.append(Finding(
                KEY, "prop-avc-denied-info", INFO,
                f"{len(prop_denials_other)} non-critical vendor/odm build.prop propert(ies) denied to vendor_init",
                "; ".join(f"{r[0]} ({type_of(r[2])} in /{r[1]})" for r in prop_denials_other),
                "init skips these lines during property_load_boot_defaults() in enforcing mode.",
            ))
        elif prop_checked and not prop_denials_crit:
            findings.append(Finding(
                KEY, "prop-avc-ok", PASS,
                f"All {prop_checked} vendor/odm build.prop properties allowed for vendor_init",
            ))

    all_denial_lines = (
        [ln for _, ln in fatal]
        + [ln for _, ln, _ in noisy]
        + [ln for _, ln in guessed]
        + [x[5] for x in binder_denials]
        + [r[3] for r in prop_denials_crit]
    )
    ctx.shared["avc"] = {
        "permissive": effective_permissive,
        "rom_mode": rom_mode or "enforcing",
        "forced": forced,
        "checked": checked,
        "fatal": len(fatal),
        "noisy": len(noisy),
        "silent": len(silent),
        "inferred": len(guessed),
        "denial_lines": all_denial_lines,
        "xattr_drift": [{"path": p, "actual": a, "expected": e} for p, a, e in xattr_drift],
        "init_domain": init_domain,
        "service_domains": service_domains,
        "binder_checked": binder_checked,
        "binder_denials": len(binder_denials),
        "prop_checked": prop_checked,
        "prop_denials_crit": len(prop_denials_crit),
        "prop_denials_other": len(prop_denials_other),
    }
    return findings
