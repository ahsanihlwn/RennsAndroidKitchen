# SPDX-License-Identifier: MPL-2.0
# Analyze unpacked ROM partitions without executing firmware.
from __future__ import annotations

import os
import time

from core.vdev.context import Context
from core.vdev.mounts import scan_layout, build_vfs, find_fstab
from core.vdev.report import Report, StageResult, Finding, BLOCKER, WARN, INFO, PASS

print("[CORE STAGE] VDEV")

__all__ = [
    "analyze",
    "analyze_workspace_mounts",
    "compare_firmwares",
    "Report",
    "Finding",
    "BLOCKER",
    "WARN",
    "INFO",
    "PASS",
]


def compare_firmwares(stock_target, port_target, options=None):
    from core.vdev.diff import compare_firmwares as _cmp
    return _cmp(stock_target, port_target, options=options)


def analyze_workspace_mounts(workspace_mounts, base=None, options=None, progress=None):
    """Analyze a workspace project reusing images currently mounted in WorkspaceMounts."""
    opts = dict(options or {})
    mounted = {}
    image_base = getattr(workspace_mounts, "_image_base", {}) or {}
    registries = getattr(workspace_mounts, "_registries", {}) or {}
    target_base = base
    for img_path, img_base in image_base.items():
        if target_base and os.path.normpath(img_base) != os.path.normpath(target_base):
            continue
        if not target_base:
            target_base = img_base
        reg = registries.get(img_base)
        meta = (getattr(reg, "metadata", {}) or {}).get(img_path, {})
        physical = meta.get("physical") or img_path
        name = os.path.splitext(os.path.basename(img_path))[0]
        mounted[name] = physical
    if mounted:
        prev = opts.get("mounted_images")
        if isinstance(prev, dict):
            merged = dict(prev)
            merged.update(mounted)
            opts["mounted_images"] = merged
        else:
            opts["mounted_images"] = mounted
    return analyze(target_base or "", options=opts, progress=progress)


def analyze(port_dir, options=None, progress=None, on_vfs=None, on_stage=None):

    options = options or {}
    report = Report(port_dir=port_dir, options=dict(options))

    def note(text):
        if progress:
            try:
                progress(text)
            except Exception:
                pass

    try:
        note("Scanning firmware source…")
        layout = scan_layout(port_dir, options, progress=note)
        if not layout.any_partition:
            report.error = (
                "No analysable partition staged — add super.img, system.img "
                "(or unpacked partitions), or a firmware .zip first."
            )
            return report

        note("Mounting virtual filesystem…")
        vfs, ramdisk_vfs = build_vfs(layout)
        fstab, fstab_origin = find_fstab(vfs, ramdisk_vfs, layout)
        layout.fstab_origin = fstab_origin

        report.mounts = [
            {"point": m.point, "partition": m.partition, "host": m.host}
            for m in sorted(vfs.mounts, key=lambda x: x.point)
        ]

        if on_vfs is not None:
            try:
                on_vfs(vfs, ramdisk_vfs, layout, fstab, fstab_origin)
            except Exception:
                pass

        ctx = Context(port_dir, layout, vfs, ramdisk_vfs,
                      fstab=fstab, fstab_origin=fstab_origin,
                      options=options, progress=progress)

        from core.vdev.stages import STAGES
        total_stages = max(1, len(STAGES))
        for stage_idx, mod in enumerate(STAGES, start=1):
            res = StageResult(key=mod.KEY, label=mod.LABEL)


            missing_req = [p for p in getattr(mod, "REQUIRES", ())
                           if not getattr(ctx, "has_" + p, False)]
            if missing_req:
                res.ran = False
                res.skipped_reason = (
                    "Partition " + ", ".join(missing_req) + " is not staged"
                )
                res.findings = [Finding(
                    mod.KEY, "stage-skipped", INFO,
                    f"{mod.LABEL} skipped — {res.skipped_reason}",
                    "This stage requires that partition to be staged in the SUPER container.",
                    "Stage " + ", ".join(missing_req) + " for a full analysis.",
                )]
                report.stages.append(res)
                if on_stage is not None:
                    try:
                        on_stage(res, ctx)
                    except Exception:
                        pass
                continue

            pct = int(stage_idx * 100 / total_stages)
            note(f"Stage {stage_idx}/{total_stages}: {mod.LABEL} ({pct}%)…")
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
            if on_stage is not None:
                try:
                    on_stage(res, ctx)
                except Exception:
                    pass


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
        report.boot_simulation = ctx.shared.get("boot_simulation", {})
        report.boot_trace = list(ctx.shared.get("boot_trace", ()) or ())
        # Build prioritized Root-Cause Verdict list (BLOCKERs first, then WARNs)
        causes = []
        rank = 0
        for f in report.sorted_findings():
            if f.severity not in (BLOCKER, WARN):
                continue
            if f.code == "sim-verdict":
                continue
            rank += 1
            causes.append({
                "rank": rank,
                "severity": f.severity,
                "stage": f.stage,
                "code": f.code,
                "title": f.title,
                "detail": f.detail,
                "hint": f.hint,
                "path": f.path,
            })
        report.root_causes = causes
        try:
            from core.vdev.autofix import build_autofix_plan
            report.autofix = build_autofix_plan(ctx, report)
        except Exception:
            report.autofix = []

    except Exception as exc:                       # noqa: BLE001
        import traceback
        traceback.print_exc()
        report.error = f"{type(exc).__name__}: {exc}"

    return report
