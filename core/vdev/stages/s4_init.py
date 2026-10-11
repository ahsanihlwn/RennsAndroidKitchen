# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from core.vdev.initrc import collect
from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "init"
LABEL = "init.rc & trigger model"
REQUIRES = ("system",)

_MAX_LIST = 12
_BOOT_CLASSES = {"core", "main", "hal", "early_hal", "late_start"}


def run(ctx):
    findings = []
    vfs = ctx.vfs
    model = collect(vfs, ctx.profile.get("props", {}))
    model.set_fstab(ctx.fstab)
    model.run_triggers()
    ctx.shared["init"] = {
        "model": model,
        "services": model.services,          # kompat: konsumen lama
        "root_rc": model.root_rc,
    }

    if not model.root_rc:
        findings.append(Finding(
            KEY, "no-init-rc", BLOCKER,
            "init.rc not found",
            "Neither /system/etc/init/hw/init.rc nor /init.rc exists. Without "
            "it init has nothing to run.",
            "Make sure the system partition is fully unpacked.",
        ))
        return findings

    findings.append(Finding(
        KEY, "parsed", INFO,
        f"{len(model.files)} .rc files read, {len(model.services)} service "
        f"blocks, {len(model.actions)} action blocks",
        f"Root: {model.root_rc}", "", model.root_rc,
    ))


    if model.missing_imports:
        items = ", ".join(p for p, _ in model.missing_imports[:_MAX_LIST])
        more = (f" (+{len(model.missing_imports) - _MAX_LIST} more)"
               if len(model.missing_imports) > _MAX_LIST else "")
        findings.append(Finding(
            KEY, "import-missing", WARN,
            f"{len(model.missing_imports)} init.rc import(s) point to missing files",
            f"Not found: {items}{more}. init logs the error and carries on, "
            "so this alone is not a bootloop — but every block in that file "
            "never registers.",
            "Provide the imported file, or drop the import line if that "
            "partition/feature is genuinely unused in this ROM.",
            model.root_rc,
        ))
    else:
        findings.append(Finding(
            KEY, "import-ok", PASS, "All init.rc imports resolve",
        ))


    hw_blocked = "ro.hardware" in model.unknown_props
    if hw_blocked:
        findings.append(Finding(
            KEY, "hardware-prop-unknown", INFO,
            "ro.hardware is unknown — hardware-specific init.rc not resolved",
            "This property is not in any build.prop; on many devices it comes "
            "from the boot cmdline/DTB instead, which this analysis does not "
            "read yet. import /init.${ro.hardware}.rc (where mount_all and "
            "hardware-specific class_start rules usually live) could not be "
            "followed.",
            "Partition mounting is still checked from fstab directly (see the "
            "mount stage) — this only affects init.rc-level verification.",
            model.root_rc,
        ))
    elif model.unknown_props:
        names = ", ".join(sorted(model.unknown_props)[:_MAX_LIST])
        findings.append(Finding(
            KEY, "prop-unknown", INFO,
            f"{len(model.unknown_props)} propert(ies) used in init.rc are unknown",
            f"{names}. Actions gated on them are not counted as guaranteed.",
        ))

    # ── mount_all ──
    if model.mount_all:
        args = ", ".join(f"{a} (on {t})" for a, t in model.mount_all[:_MAX_LIST])
        findings.append(Finding(
            KEY, "mount-all", INFO,
            f"{len(model.mount_all)} mount_all command(s) found",
            args,
        ))
    elif not hw_blocked:
        findings.append(Finding(
            KEY, "no-mount-all", WARN,
            "No mount_all command found in the reachable init.rc chain",
            "Second-stage partitions are normally mounted this way. It may "
            "live in a per-hardware file this analysis did not reach.",
        ))

    # ── service yang dijamin start vs yang tidak ──
    started = model.started_services
    critical_missing, boot_missing = [], []
    skipped_apex = 0
    checked = 0

    for s in started:
        if not s.path.startswith("/"):
            continue
        if s.path.startswith("/apex/"):
            skipped_apex += 1
            continue
        checked += 1
        if vfs.is_file(s.path):
            continue
        if s.critical:
            critical_missing.append(s)
        elif s.klass in _BOOT_CLASSES or not s.klass:
            boot_missing.append(s)


    from core.vdev.mounts import lost_symlink
    dropped = {}
    for s in list(critical_missing) + list(boot_missing):
        target = lost_symlink(ctx.layout, s.path)
        if target:
            dropped[s.path] = target

    def _describe(svc):
        target = dropped.get(svc.path)
        if target:
            return f"{svc.name} ({svc.path} -> {target})"
        return f"{svc.name} ({svc.path})"

    def _symlink_note(group, generic):

        hit = [(s.path, dropped[s.path]) for s in group if s.path in dropped]
        if not hit:
            return "", generic
        detail = (" " + ("Both" if len(hit) == 2 else str(len(hit)))
                  + (" of these are symlinks" if len(hit) != 1
                     else " of these is a symlink")
                  + " that the unpack dropped, not a missing binary.")
        hint = ("Recreate the symlink(s): "
                + "; ".join(f"ln -s {t} {p}" for p, t in hit) + ".")
        # Kalimat umum cuma ditempel kalau memang masih ada yang bukan symlink,
        # supaya hint-nya tidak menyuruh dua hal yang bertentangan.
        if len(hit) < len(group):
            hint += " " + generic
        return detail, hint

    if critical_missing:
        items = ", ".join(_describe(s) for s in critical_missing[:_MAX_LIST])
        extra, hint = _symlink_note(
            critical_missing[:_MAX_LIST],
            "Copy the binary from the source ROM, or remove the service "
            "block if that feature is genuinely unused.")
        findings.append(Finding(
            KEY, "critical-missing", BLOCKER,
            f"{len(critical_missing)} `critical` service(s) the trigger chain "
            "starts cannot run",
            items + ". A critical service that keeps failing makes init "
            "reboot the device into the bootloader." + extra,
            hint,
        ))

    if boot_missing:
        items = ", ".join(_describe(s) for s in boot_missing[:_MAX_LIST])
        more = (f" (+{len(boot_missing) - _MAX_LIST} more)"
               if len(boot_missing) > _MAX_LIST else "")
        extra, hint = _symlink_note(
            boot_missing[:_MAX_LIST],
            "Supply the binaries, or drop the service block.")
        findings.append(Finding(
            KEY, "service-missing", WARN,
            f"{len(boot_missing)} boot service(s) the trigger chain starts "
            "cannot run",
            items + more + ". These are reached by class_start on a boot "
            "trigger (or the vold-decrypt gate), so they run — and their "
            "target is absent." + extra,
            hint,
        ))

    if not critical_missing and not boot_missing and checked:
        findings.append(Finding(
            KEY, "service-ok", PASS,
            f"All {checked} services reached by the trigger chain have their binary",
        ))

    if skipped_apex:
        findings.append(Finding(
            KEY, "service-apex", INFO,
            f"{skipped_apex} service(s) reached by the trigger chain live "
            "inside APEX — not judged here",
            "Contents of /apex/... are only mounted after apexd runs; the "
            "APEX stage checks these instead using the payload it already "
            "indexed.",
        ))

    unreached = len(model.never_started)
    if unreached:
        findings.append(Finding(
            KEY, "service-unreached", INFO,
            f"{unreached} declared service(s) are never reached by any trigger",
            "Not started by early-init/init/late-init, any `trigger` command "
            "in the reachable chain, or the vold-decrypt gate — so a missing "
            "binary there does not affect boot. Not checked.",
        ))

    return findings
