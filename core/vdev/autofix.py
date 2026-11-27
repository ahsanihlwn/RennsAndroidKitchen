# SPDX-License-Identifier: MPL-2.0
"""Actionable Auto-Fix recipe engine with safe Preview Diff, 1-Click Apply, and Undo."""
from __future__ import annotations

import datetime
import difflib
import json
import os
import posixpath
import shutil
from xml.sax.saxutils import escape as _xml_esc


def build_autofix_plan(ctx, report) -> list[dict]:
    """Generate structured, ready-to-apply fix recipes from vdev analysis findings."""
    actions: list[dict] = []

    # 1) SELinux AVC denials -> Rust selinux_audit_allow (CIL / TE / Magisk rules)
    avc = ctx.shared.get("avc") or {}
    denial_lines = avc.get("denial_lines") or []
    if denial_lines:
        try:
            from core import native_backend
            res = native_backend.selinux_audit_allow("\n".join(denial_lines))
            if res.get("rule_count", 0) > 0:
                actions.append({
                    "id": "selinux-audit2allow",
                    "stage": "avc",
                    "kind": "sepolicy_rules",
                    "title": f"Generate {res['rule_count']} SELinux allow rule(s) from {res['denial_count']} predicted denial(s)",
                    "tool": "native_backend.selinux_audit_allow",
                    "target_path": "/system/etc/selinux/plat_sepolicy.cil",
                    "cil_rules": res.get("cil_rules", ""),
                    "te_rules": res.get("te_rules", ""),
                    "magisk_rules": res.get("magisk_rules", ""),
                    "can_apply": True,
                    "apply_summary": "Append CIL allow rules into /system/etc/selinux/plat_sepolicy.cil (or drop-in CIL).",
                })
        except Exception:
            pass

    # 2) SELinux xattr drift -> in-place ext_edit_selinux / erofs_edit_selinux / NeoCat
    xattr_drift = avc.get("xattr_drift") or []
    if xattr_drift:
        actions.append({
            "id": "selinux-xattr-align",
            "stage": "avc",
            "kind": "xattr_relabel",
            "title": f"Align SELinux xattrs on {len(xattr_drift)} drifted service binary/binaries",
            "tool": "ext_edit_selinux / erofs_edit_selinux / DeviceSelinuxPatchEngine",
            "entries": xattr_drift[:25],
            "can_apply": False,
        })

    # 3) AVB / VBMeta blockers -> patch fstab + vbmeta
    all_findings = report.findings
    avb_codes = {f.code for f in all_findings if f.stage == "mount" and f.severity == "blocker"}
    if avb_codes & {"avb-verity-will-fail", "avb-img-stock", "verity-digest-bad", "verity-digest-mismatch"}:
        vb_paths = sorted((ctx.shared.get("vbmeta") or {}).keys())
        actions.append({
            "id": "avb-disable-verity",
            "stage": "mount",
            "kind": "vbmeta_fstab_patch",
            "title": "Disable AVB dm-verity & verification in vbmeta and fstab",
            "tool": "native_backend.patch_fstab / native_backend.patch_vbmeta",
            "targets": vb_paths or ["vbmeta.img"],
            "fstab_origin": ctx.fstab_origin,
            "can_apply": True,
            "apply_summary": "Strip avb/verify flags from writable vendor fstab.* and disable verity in writable vbmeta.img.",
        })

    # 3b) FBE encryption coherence issues -> fstab encryption patch
    fbe_codes = {f.code for f in all_findings if f.stage == "mount" and f.code.startswith(("fbe-", "fde-"))}
    if fbe_codes & {"fde-unsupported-sdk", "fbe-metadata-mount-missing", "fbe-keystore-hal-missing", "fbe-wrappedkey-mismatch"}:
        actions.append({
            "id": "fbe-fstab-fix",
            "stage": "mount",
            "kind": "fbe_fstab_patch",
            "title": "Patch /data FBE/FDE encryption flags in fstab",
            "tool": "native_backend.patch_fstab",
            "targets": [ctx.fstab_origin or "/vendor/etc/fstab.*"],
            "fstab_origin": ctx.fstab_origin,
            "can_apply": True,
            "apply_summary": "Disable incompatible fileencryption/encryptable flags on writable vendor fstab.*.",
        })

    # 4) Privapp-permissions violations -> exact XML allowlist snippet (1-click writable!)
    fw = ctx.shared.get("framework") or {}
    priv_viols = fw.get("privapp_violations") or []
    if priv_viols:
        xml_lines = ['<?xml version="1.0" encoding="utf-8"?>', "<permissions>"]
        for item in priv_viols:
            pkg = _xml_esc(item["package"])
            xml_lines.append(f'    <privapp-permissions package="{pkg}">')
            for perm in item["permissions"]:
                xml_lines.append(f'        <permission name="{_xml_esc(perm)}"/>')
            xml_lines.append("    </privapp-permissions>")
        xml_lines.append("</permissions>")
        actions.append({
            "id": "privapp-allowlist-xml",
            "stage": "framework",
            "kind": "xml_snippet",
            "title": f"Add privapp-permissions XML for {len(priv_viols)} privileged package(s)",
            "target_path": "/system/etc/permissions/privapp-permissions-port-autofix.xml",
            "content": "\n".join(xml_lines) + "\n",
            "can_apply": True,
            "apply_summary": "Write /system/etc/permissions/privapp-permissions-port-autofix.xml.",
        })

    # 4b) Zygote 64/32 mismatch -> patch ro.zygote=zygote64 in system/build.prop
    if any(f.code == "zygote-binary-missing" and "app_process32" in f.title for f in all_findings):
        actions.append({
            "id": "zygote-prop-fix",
            "stage": "runtime",
            "kind": "prop_patch",
            "title": "Switch ro.zygote from zygote64_32 to zygote64 (app_process32 absent)",
            "tool": "native_backend.patch_props",
            "target_path": "/system/build.prop",
            "updates": [("ro.zygote", "zygote64")],
            "content": "ro.zygote=zygote64\n",
            "can_apply": True,
            "apply_summary": "Update ro.zygote=zygote64 in /system/build.prop.",
        })

    # 5) Missing libraries / ABI mismatch / C++ symbols -> checklist
    linker = ctx.shared.get("linker") or {}
    unresolved = linker.get("unresolved") or {}
    abi_bad = linker.get("abi_mismatch") or {}
    if unresolved or abi_bad:
        actions.append({
            "id": "linker-missing-libs",
            "stage": "linker",
            "kind": "library_restore",
            "title": f"Restore {len(unresolved)} missing and {len(abi_bad)} wrong-ABI shared librar(ies)",
            "missing": sorted(unresolved.keys())[:30],
            "abi_mismatch": sorted(abi_bad.keys())[:30],
            "can_apply": False,
        })

    # 6) Mismatched / orphaned RRO overlays -> safe reversible quarantine
    dex_info = ctx.shared.get("dex_bytecode") or {}
    ov_items = dex_info.get("overlay_issue_items") or []
    orphaned_ov = [
        o for o in ov_items
        if o.get("kind") in ("orphaned-overlay-resources", "missing-target-package", "corrupt-overlay")
    ]
    if orphaned_ov:
        actions.append({
            "id": "overlay-prune-mismatch",
            "stage": "dex_bytecode",
            "kind": "overlay_cleanup",
            "title": f"Quarantine {len(orphaned_ov)} mismatched/orphaned RRO overlay APK(s)",
            "overlays": [
                {
                    "apk_path": o.get("apk_path", ""),
                    "target_package": o.get("target_package", ""),
                    "kind": o.get("kind", ""),
                    "missing_count": o.get("missing_count", 0),
                }
                for o in orphaned_ov[:25]
            ],
            "can_apply": True,
            "apply_summary": "Move broken/orphaned overlay APK(s) into reversible backup quarantine.",
        })

    return actions


def resolve_writable_host_path(vpath: str, workspace_root: str, layout=None) -> tuple[str | None, str]:
    """Map a logical ROM VFS path (e.g. /system/etc/...) to a writable host path inside workspace_root.
    Never modifies read-only .img/.zip containers; returns (None, reason) if not unpacked/writable."""
    vp = "/" + str(vpath or "").strip().lstrip("/")
    if not workspace_root or not os.path.isdir(workspace_root):
        return None, "Target is not an unpacked writable workspace directory (.img/.zip is read-only)."

    parts = [p for p in vp.split("/") if p]
    if not parts:
        return None, f"Invalid VFS path: {vpath}"
    part = parts[0]
    rel = parts[1:]

    # 1) Prefer layout.partitions[part] when available (actual unpacked folder)
    part_dir = None
    if layout is not None and getattr(layout, "partitions", None):
        cand = layout.partitions.get(part)
        if cand and os.path.isdir(cand):
            part_dir = cand
            # System-as-root unpacked folder has <system_dir>/system/build.prop
            if part == "system" and getattr(layout, "sar", False):
                if os.path.isdir(os.path.join(part_dir, "system")):
                    part_dir = os.path.join(part_dir, "system")

    # 2) Fallback: probe workspace_root directly (SAR or non-SAR unpacked workspace)
    if part_dir is None:
        if part == "system":
            sar_dir = os.path.join(workspace_root, "system", "system")
            plain_dir = os.path.join(workspace_root, "system")
            if os.path.isdir(sar_dir):
                part_dir = sar_dir
            elif os.path.isdir(plain_dir):
                part_dir = plain_dir
        else:
            cand = os.path.join(workspace_root, part)
            if os.path.isdir(cand):
                part_dir = cand

    if not part_dir:
        return None, (
            f"Partition '/{part}' is read-only (inside .img/.zip/super.img) or not unpacked under workspace. "
            "Unpack or checkout this partition in Workspace/NeoBranch to apply Auto-Fix directly."
        )
    return os.path.normpath(os.path.join(part_dir, *rel)), ""


def _find_writable_fstabs(workspace_root: str, layout=None) -> list[tuple[str, str]]:
    """Locate writable vendor/odm/ramdisk fstab.* files on disk."""
    hits = []
    for part in ("vendor", "odm"):
        etc_host, _ = resolve_writable_host_path(f"/{part}/etc", workspace_root, layout)
        if etc_host and os.path.isdir(etc_host):
            for fn in sorted(os.listdir(etc_host)):
                if fn.startswith("fstab.") and os.path.isfile(os.path.join(etc_host, fn)):
                    hits.append((f"/{part}/etc/{fn}", os.path.join(etc_host, fn)))
    return hits


def _plan_recipe_ops(recipe: dict, workspace_root: str, layout=None) -> tuple[list[dict], str]:
    """Compute concrete file operations for preview or apply without touching disk."""
    kind = str(recipe.get("kind") or "")
    ops: list[dict] = []

    if kind == "xml_snippet":
        vp = str(recipe.get("target_path") or "/system/etc/permissions/privapp-permissions-port-autofix.xml")
        content = str(recipe.get("content") or "")
        hp, err = resolve_writable_host_path(vp, workspace_root, layout)
        if not hp:
            return [], err
        old = ""
        action = "create"
        if os.path.isfile(hp):
            action = "modify"
            with open(hp, "r", encoding="utf-8", errors="replace") as f:
                old = f.read()
        ops.append({"action": action, "vpath": vp, "host": hp, "old": old, "new": content})
        return ops, ""

    if kind == "sepolicy_rules":
        cil = str(recipe.get("cil_rules") or "").strip()
        if not cil:
            return [], "No CIL rules present in recipe."
        vp = str(recipe.get("target_path") or "/system/etc/selinux/plat_sepolicy.cil")
        hp, err = resolve_writable_host_path(vp, workspace_root, layout)
        if not hp:
            return [], err
        marker = "; --- RAK Boot Analyzer Auto-Fix SELinux rules ---"
        old = ""
        action = "create"
        if os.path.isfile(hp):
            action = "modify"
            with open(hp, "r", encoding="utf-8", errors="replace") as f:
                old = f.read()
        existing_lines = {ln.strip() for ln in old.splitlines()}
        new_rules = [ln for ln in cil.splitlines() if ln.strip() and ln.strip() not in existing_lines]
        if not new_rules:
            return [], "All generated SELinux CIL rules are already present."
        sep = "" if (not old or old.endswith("\n")) else "\n"
        new_text = f"{old}{sep}\n{marker}\n" + "\n".join(new_rules) + "\n"
        ops.append({"action": action, "vpath": vp, "host": hp, "old": old, "new": new_text})
        return ops, ""

    if kind == "prop_patch":
        vp = str(recipe.get("target_path") or "/system/build.prop")
        updates = recipe.get("updates") or []
        hp, err = resolve_writable_host_path(vp, workspace_root, layout)
        if not hp or not os.path.isfile(hp):
            return [], err or f"Target prop file not found: {vp}"
        with open(hp, "r", encoding="utf-8", errors="replace") as f:
            old = f.read()
        try:
            from core import native_backend
            res = native_backend.patch_props(old, updates)
            new_text = str(res.get("modified_text") or res.get("text") or old)
        except Exception:
            lines = old.splitlines()
            upd = dict(updates)
            seen = set()
            out_lines = []
            for ln in lines:
                if "=" in ln and not ln.lstrip().startswith("#"):
                    k = ln.split("=", 1)[0].strip()
                    if k in upd:
                        out_lines.append(f"{k}={upd[k]}")
                        seen.add(k)
                        continue
                out_lines.append(ln)
            for k, v in upd.items():
                if k not in seen:
                    out_lines.append(f"{k}={v}")
            new_text = "\n".join(out_lines) + "\n"
        if new_text == old:
            return [], "No changes needed in build.prop."
        ops.append({"action": "modify", "vpath": vp, "host": hp, "old": old, "new": new_text})
        return ops, ""

    if kind in ("vbmeta_fstab_patch", "fbe_fstab_patch"):
        disable_avb = (kind == "vbmeta_fstab_patch")
        disable_enc = (kind == "fbe_fstab_patch")
        fstabs = _find_writable_fstabs(workspace_root, layout)
        if not fstabs:
            return [], "No writable fstab.* found under unpacked /vendor/etc or /odm/etc."
        for vp, hp in fstabs:
            with open(hp, "r", encoding="utf-8", errors="replace") as f:
                old = f.read()
            try:
                from core import native_backend
                res = native_backend.patch_fstab(old, disable_avb=disable_avb, disable_encryption=disable_enc)
                new_text = str(res.get("modified_text") or res.get("text") or old)
            except Exception:
                new_text = old
            if new_text != old:
                ops.append({"action": "modify", "vpath": vp, "host": hp, "old": old, "new": new_text})
        if not ops:
            return [], "fstab entries already have those flags removed."
        return ops, ""

    if kind == "overlay_cleanup":
        overlays = recipe.get("overlays") or []
        for ov in overlays:
            vp = str(ov.get("apk_path") or "")
            if not vp:
                continue
            hp, _ = resolve_writable_host_path(vp, workspace_root, layout)
            if hp and os.path.isfile(hp):
                ops.append({"action": "quarantine", "vpath": vp, "host": hp, "old": "", "new": ""})
        if not ops:
            return [], "No writable overlay APK files found on disk (partitions may be read-only .img/.zip)."
        return ops, ""

    return [], f"Recipe kind '{kind}' is advisory/manual only."


def preview_autofix(recipe: dict, workspace_root: str, layout=None) -> dict:
    """Compute a unified diff preview for a recipe without modifying any files."""
    ops, err = _plan_recipe_ops(recipe, workspace_root, layout)
    if err or not ops:
        return {"ok": False, "canApply": False, "reason": err or "No changes planned.", "changes": []}

    changes = []
    for op in ops:
        if op["action"] == "quarantine":
            diff_txt = f"--- {op['vpath']}\n+++ (moved to .rak_autofix_backups quarantine)\n"
        else:
            diff_lines = list(difflib.unified_diff(
                op["old"].splitlines(keepends=True),
                op["new"].splitlines(keepends=True),
                fromfile=f"a{op['vpath']}",
                tofile=f"b{op['vpath']}",
                n=2,
            ))
            diff_txt = "".join(diff_lines[:160])
            if len(diff_lines) > 160:
                diff_txt += f"\n... (+{len(diff_lines) - 160} more diff lines)\n"
        changes.append({
            "action": op["action"],
            "vpath": op["vpath"],
            "hostPath": op["host"],
            "diff": diff_txt,
        })
    return {
        "ok": True,
        "canApply": True,
        "reason": f"{len(changes)} file operation(s) ready to apply.",
        "changes": changes,
    }


def apply_autofix(recipe: dict, workspace_root: str, layout=None) -> dict:
    """Safely apply a recipe to an unpacked workspace directory with full atomic backup for Undo."""
    ops, err = _plan_recipe_ops(recipe, workspace_root, layout)
    if err or not ops:
        return {"ok": False, "message": err or "Nothing to apply.", "appliedCount": 0, "backupManifest": ""}

    rid = str(recipe.get("id") or "fix").replace("/", "_")
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    bdir = os.path.join(workspace_root, ".rak_autofix_backups", f"{ts}_{rid}")
    os.makedirs(bdir, exist_ok=True)

    manifest_entries = []
    try:
        for idx, op in enumerate(ops):
            hp = op["host"]
            action = op["action"]
            backup_file = ""
            if action in ("modify", "quarantine") and os.path.isfile(hp):
                backup_file = os.path.join(bdir, f"{idx}_{os.path.basename(hp)}")
                shutil.copy2(hp, backup_file)

            if action == "quarantine":
                os.remove(hp)
            else:
                os.makedirs(os.path.dirname(hp), exist_ok=True)
                tmp = hp + ".rak_tmp"
                with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                    f.write(op["new"])
                os.replace(tmp, hp)

            manifest_entries.append({
                "action": action,
                "vpath": op["vpath"],
                "host": hp,
                "backup": backup_file,
            })

        manifest_path = os.path.join(bdir, "manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump({"id": rid, "timestamp": ts, "entries": manifest_entries}, f, indent=2)
        return {
            "ok": True,
            "message": f"Applied '{recipe.get('title', rid)}' ({len(manifest_entries)} file(s) updated; backup saved).",
            "appliedCount": len(manifest_entries),
            "backupManifest": manifest_path,
        }
    except Exception as exc:
        return {"ok": False, "message": f"Apply failed: {exc}", "appliedCount": 0, "backupManifest": ""}


def undo_autofix(manifest_path: str) -> dict:
    """Restore workspace files from a previously created Auto-Fix backup manifest."""
    if not manifest_path or not os.path.isfile(manifest_path):
        return {"ok": False, "message": "No Auto-Fix backup manifest available to undo."}
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        restored = 0
        for ent in reversed(data.get("entries") or []):
            action = ent.get("action")
            hp = ent.get("host") or ""
            bk = ent.get("backup") or ""
            if action == "create":
                if hp and os.path.isfile(hp):
                    os.remove(hp)
                    restored += 1
            elif bk and os.path.isfile(bk) and hp:
                os.makedirs(os.path.dirname(hp), exist_ok=True)
                shutil.copy2(bk, hp)
                restored += 1
        return {
            "ok": True,
            "message": f"Reverted Auto-Fix '{data.get('id', '')}' ({restored} file(s) restored).",
            "restoredCount": restored,
        }
    except Exception as exc:
        return {"ok": False, "message": f"Undo failed: {exc}", "restoredCount": 0}
