# SPDX-License-Identifier: MPL-2.0
"""CLI runner for `python -m core.vdev <target>` (folder, super.img, or firmware .zip)."""
from __future__ import annotations

import argparse
import json
import sys
import time

from core.vdev import analyze, compare_firmwares


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m core.vdev",
        description="Fast full-firmware Boot Analyzer, Auto-Fix Planner & SystemServer Simulator",
    )
    parser.add_argument("target", help="ROM project folder, super.img, or firmware .zip (or Stock when --diff is set)")
    parser.add_argument("--diff", metavar="PORT_TARGET", default="", help="Compare target (stock) against PORT_TARGET")
    parser.add_argument("--slot", type=int, default=0, help="A/B slot index (default: 0)")
    parser.add_argument("--verify-hashtree", action="store_true", help="Recompute AVB dm-verity root digests")
    parser.add_argument("--no-vbmeta", action="store_true", help="Simulate with vbmeta verity bypassed")
    parser.add_argument("--force-permissive", action="store_true", help="Simulate SELinux permissive mode")
    parser.add_argument("--autofix", action="store_true", help="Print actionable Auto-Fix recipes (SELinux/AVB/Privapp)")
    parser.add_argument("--json", action="store_true", help="Output full JSON report")
    args = parser.parse_args(argv)

    t0 = time.perf_counter()
    opts = {
        "slot": args.slot,
        "verify_hashtree": args.verify_hashtree,
        "vbmeta_enabled": not args.no_vbmeta,
        "force_permissive": args.force_permissive,
    }

    if args.diff:
        diff = compare_firmwares(args.target, args.diff, options=opts)
        elapsed = time.perf_counter() - t0
        if args.json:
            print(json.dumps(diff, indent=2, ensure_ascii=False))
            return 1 if diff["new_blockers"] else 0
        print("\n" + "=" * 72)
        print(f"Stock    : {diff['stock_target']} -> {diff['stock_summary']['verdict']}")
        print(f"Port     : {diff['port_target']} -> {diff['port_summary']['verdict']}")
        print(f"Duration : {elapsed:.2f}s")
        print(f"Regressions: new_blockers={len(diff['new_blockers'])}, new_warns={len(diff['new_warns'])}, fixed={len(diff['fixed_issues'])}")
        for ph in diff.get("boot_simulation_diff") or []:
            mark = "!!" if ph.get("regressed") else "  "
            print(f" {mark} {ph['label']:34} {ph['stock_status']:7} -> {ph['port_status']:7}")
        for b in diff["new_blockers"]:
            print(f"  - [NEW BLOCKER] [{b['stage']}] {b['title']}")
        for w in diff["new_warns"]:
            print(f"  - [NEW WARN]    [{w['stage']}] {w['title']}")
        print("=" * 72)
        return 1 if diff["new_blockers"] else 0

    def progress(msg):
        if not args.json:
            print(f"  [vdev] {msg}", flush=True)

    report = analyze(args.target, options=opts, progress=progress)
    elapsed = time.perf_counter() - t0

    if args.json:
        print(report.to_json())
        return 1 if report.error or report.blockers else 0

    print("\n" + "=" * 72)
    print(f"Target   : {report.port_dir}")
    print(f"Duration : {elapsed:.2f}s")
    if report.error:
        print(f"ERROR    : {report.error}")
        return 2
    print(f"Verdict  : {report.verdict()} (blocker={report.blockers}, warn={report.warns}, pass={report.passes})")
    sim = report.boot_simulation or {}
    if sim:
        print(f"SystemServer Sim: {sim.get('verdict')} (reaches_system_server={sim.get('reaches_system_server')})")
        for ph in sim.get("phases") or []:
            print(f"  [{ph['status']:7}] {ph['label']} :: {'; '.join(ph.get('checks') or [])}")
    print("-" * 72)
    for st in report.stages:
        tag = "OK" if st.ran else "SKIP"
        print(f"[{tag:4}] {st.label:32} ({st.duration_ms:5} ms)  B={st.blockers} W={st.warns}")
        for f in st.findings:
            if f.severity in ("blocker", "warn"):
                print(f"   - [{f.severity.upper()}] {f.title}")
                if f.detail:
                    print(f"     {f.detail[:220]}")
    if args.autofix and report.autofix:
        print("-" * 72)
        print(f"Auto-Fix Recipes ({len(report.autofix)}):")
        for act in report.autofix:
            print(f"  * [{act['id']}] {act['title']}")
            if act.get("cil_rules"):
                print("    CIL:\n      " + act["cil_rules"].strip().replace("\n", "\n      ")[:400])
            if act.get("content"):
                print("    Snippet:\n      " + act["content"].strip().replace("\n", "\n      ")[:400])
    print("=" * 72)
    return 1 if report.blockers else 0


if __name__ == "__main__":
    sys.exit(main())
