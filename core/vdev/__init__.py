# SPDX-License-Identifier: MPL-2.0
# Analyze unpacked ROM partitions without executing firmware.
from __future__ import annotations

import time

from core.vdev.context import Context
from core.vdev.mounts import scan_layout, build_vfs, find_fstab
from core.vdev.report import Report, StageResult, Finding, BLOCKER, WARN, INFO, PASS

print("[CORE STAGE] VDEV")

__all__ = ["analyze", "Report", "Finding", "BLOCKER", "WARN", "INFO", "PASS"]


def analyze(port_dir, options=None, progress=None):

    options = options or {}
    report = Report(port_dir=port_dir, options=dict(options))

    def note(text):
        if progress:
            try:
                progress(text)
            except Exception:
                pass

    try:
        note("Scanning port folder…")
        layout = scan_layout(port_dir)
        if not layout.any_partition:
            report.error = (
                "No analysable partition in this folder — neither an unpacked "
                "folder nor an EXT/EROFS image. Point this at a ROM folder "
                "containing system.img (or an unpacked system)."
            )
            return report

        note("Building mount table…")
        vfs, ramdisk_vfs = build_vfs(layout)
        fstab, fstab_origin = find_fstab(vfs, ramdisk_vfs, layout)
        layout.fstab_origin = fstab_origin

        report.mounts = [
            {"point": m.point, "partition": m.partition, "host": m.host}
            for m in sorted(vfs.mounts, key=lambda x: x.point)
        ]

        ctx = Context(port_dir, layout, vfs, ramdisk_vfs,
                      fstab=fstab, fstab_origin=fstab_origin,
                      options=options, progress=progress)

        from core.vdev.stages import STAGES
        for mod in STAGES:
            res = StageResult(key=mod.KEY, label=mod.LABEL)


            missing_req = [p for p in getattr(mod, "REQUIRES", ())
                           if not getattr(ctx, "has_" + p, False)]
            if missing_req:
                res.ran = False
                res.skipped_reason = (
                    "Partition " + ", ".join(missing_req) + " is not in this project"
                )
                res.findings = [Finding(
                    mod.KEY, "stage-skipped", INFO,
                    f"{mod.LABEL} skipped — {res.skipped_reason}",
                    "This stage can only judge anything when that partition is "
                    "unpacked in the same folder.",
                    "Unpack " + ", ".join(missing_req) + " into this project "
                    "for a full analysis.",
                )]
                report.stages.append(res)
                continue

            note(mod.LABEL + "…")
            t0 = time.perf_counter()
            try:
                res.findings = mod.run(ctx) or []
                res.ran = True
            except Exception as exc:               # noqa: BLE001
                # Satu stage gagal tidak boleh menjatuhkan seluruh laporan —
                # stage lain masih memberi informasi yang berguna.
                import traceback
                traceback.print_exc()
                res.ran = False
                res.skipped_reason = f"{type(exc).__name__}: {exc}"
                res.findings = [Finding(
                    mod.KEY, "stage-error", WARN,
                    f"Stage '{mod.LABEL}' failed to run",
                    res.skipped_reason,
                    "Report it if it repeats — the other stages are still valid.",
                )]
            res.duration_ms = int((time.perf_counter() - t0) * 1000)
            report.stages.append(res)


            if ctx.profile.get("no_rom"):
                report.error = (
                    "This folder holds no ROM that can be analysed — there is "
                    "no build.prop in any partition. Open a project whose "
                    "system partition has been unpacked."
                )
                break

        report.profile = ctx.profile
        report.profile["link_hops"] = vfs.link_hops
        report.profile["vbmeta"] = ctx.shared.get("vbmeta", {})
        report.readiness = ctx.shared.get("readiness", {})

    except Exception as exc:                       # noqa: BLE001
        import traceback
        traceback.print_exc()
        report.error = f"{type(exc).__name__}: {exc}"

    return report
