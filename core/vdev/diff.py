# SPDX-License-Identifier: MPL-2.0
"""Stock-vs-Port firmware regression comparator combining vdev.analyze and kernel_boot_compare."""
from __future__ import annotations

import os
from typing import Any

from core.vdev import analyze


def _finding_keys(report) -> dict[tuple[str, str, str], dict]:
    out = {}
    for f in report.findings:
        if f.severity in ("blocker", "warn"):
            out[(f.stage, f.code, f.title)] = f.to_dict()
    return out


def _phase_map(report) -> dict[str, dict]:
    sim = report.boot_simulation or {}
    return {p["phase"]: p for p in (sim.get("phases") or [])}


def compare_firmwares(stock_target: str, port_target: str, options: dict | None = None) -> dict[str, Any]:
    """Analyze both stock and port firmwares and produce a structured regression diff."""
    opts = dict(options or {})
    stock = analyze(stock_target, options=opts)
    port = analyze(port_target, options=opts)

    stock_issues = _finding_keys(stock)
    port_issues = _finding_keys(port)

    new_issues = [v for k, v in port_issues.items() if k not in stock_issues]
    fixed_issues = [v for k, v in stock_issues.items() if k not in port_issues]

    stock_props = (stock.profile or {}).get("props") or {}
    port_props = (port.profile or {}).get("props") or {}
    tracked_props = (
        "ro.build.version.release",
        "ro.build.version.sdk",
        "ro.build.version.security_patch",
        "ro.product.device",
        "ro.board.platform",
        "ro.zygote",
        "ro.vndk.version",
        "ro.control_privapp_permissions",
    )
    prop_changes = {}
    for key in tracked_props:
        sv = stock_props.get(key, "")
        pv = port_props.get(key, "")
        if sv != pv:
            prop_changes[key] = {"stock": sv, "port": pv}

    stock_parts = set((stock.profile or {}).get("partitions") or ())
    port_parts = set((port.profile or {}).get("partitions") or ())

    stock_phases = _phase_map(stock)
    port_phases = _phase_map(port)
    phase_diff = []
    for ph_key in dict.fromkeys(list(stock_phases.keys()) + list(port_phases.keys())):
        sp = stock_phases.get(ph_key) or {}
        pp = port_phases.get(ph_key) or {}
        phase_diff.append({
            "phase": ph_key,
            "label": pp.get("label") or sp.get("label") or ph_key,
            "stock_status": sp.get("status", "N/A"),
            "port_status": pp.get("status", "N/A"),
            "regressed": (sp.get("status") == "PASS" and pp.get("status") in ("WARN", "BLOCKER"))
                         or (sp.get("status") == "WARN" and pp.get("status") == "BLOCKER"),
        })

    # Optional byte/kernel-level boot.img compare if both targets have loose boot.img
    boot_compare = None
    try:
        sb = os.path.join(stock_target, "boot.img") if os.path.isdir(stock_target) else ""
        pb = os.path.join(port_target, "boot.img") if os.path.isdir(port_target) else ""
        if sb and pb and os.path.isfile(sb) and os.path.isfile(pb):
            from core.kernel_boot_compare import compare_boot
            boot_compare = compare_boot(sb, pb)
    except Exception:
        boot_compare = None

    return {
        "stock_target": stock_target,
        "port_target": port_target,
        "stock_summary": {
            "verdict": stock.verdict(),
            "blockers": stock.blockers,
            "warns": stock.warns,
            "reaches_system_server": (stock.boot_simulation or {}).get("reaches_system_server"),
        },
        "port_summary": {
            "verdict": port.verdict(),
            "blockers": port.blockers,
            "warns": port.warns,
            "reaches_system_server": (port.boot_simulation or {}).get("reaches_system_server"),
        },
        "new_blockers": [i for i in new_issues if i["severity"] == "blocker"],
        "new_warns": [i for i in new_issues if i["severity"] == "warn"],
        "fixed_issues": fixed_issues,
        "prop_changes": prop_changes,
        "partitions_added": sorted(port_parts - stock_parts),
        "partitions_removed": sorted(stock_parts - port_parts),
        "boot_simulation_diff": phase_diff,
        "boot_image_compare": boot_compare,
        "port_autofix": port.autofix,
    }
