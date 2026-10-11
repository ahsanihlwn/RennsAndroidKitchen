# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
import shutil
from typing import Any

from core.context_patcher import ContextPatcher
from core.app_paths import neocat_cache_dir
from core.vdev.context import Context
from core.vdev.initrc import collect
from core.vdev.mounts import build_vfs, find_fstab, scan_layout
from core.vdev.selinux import FileContexts, Policy, type_of
from core.vdev.stages import s0_device, s2_selinux


ENGINE_VERSION = 1


@dataclass
class PreflightIssue:
    code: str
    severity: str
    detail: str


@dataclass
class Proposal:
    path: str
    context: str
    decision: str
    evidence: str
    detail: str
    predicted_context: str = ""
    service: str = ""

    @property
    def accepted(self) -> bool:
        return self.decision == "accept"


@dataclass
class PatchPlan:
    firmware_base: str
    target_base: str
    fs_root: str
    ready: bool
    issues: list[PreflightIssue] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    policy_sources: list[str] = field(default_factory=list)
    mounts: list[dict[str, str]] = field(default_factory=list)
    init_domain: str = ""
    active_branches: dict[str, str] = field(default_factory=dict)

    @property
    def accepted(self) -> list[Proposal]:
        return [item for item in self.proposals if item.accepted]

    @property
    def rejected(self) -> list[Proposal]:
        return [item for item in self.proposals if not item.accepted]

    def summary(self) -> str:
        blockers = [i for i in self.issues if i.severity == "blocker"]
        if blockers:
            return "AI SELinux gate blocked: " + "; ".join(i.detail for i in blockers)
        return (f"{len(self.accepted)} verified context(s), "
                f"{len(self.rejected)} proposal(s) withheld")

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_version": ENGINE_VERSION,
            "firmware_base": self.firmware_base,
            "target_base": self.target_base,
            "fs_root": self.fs_root,
            "ready": self.ready,
            "policy_sources": self.policy_sources,
            "mounts": self.mounts,
            "init_domain": self.init_domain,
            "active_branches": self.active_branches,
            "issues": [asdict(item) for item in self.issues],
            "proposals": [asdict(item) for item in self.proposals],
        }


@dataclass
class _Evidence:
    layout: Any
    vfs: Any
    policy: Policy | None
    file_contexts: FileContexts | None
    services: dict[str, list[Any]]
    init_domain: str
    issues: list[PreflightIssue]


class DeviceSelinuxPatchEngine:


    def __init__(self, firmware_base: str, target_base: str | None = None,
                 cache_dir: str | None = None,
                 partition_overrides: list[str] | None = None):
        self.firmware_base = os.path.abspath(firmware_base)
        self.target_base = os.path.abspath(target_base or firmware_base)
        self.cache_dir = cache_dir or neocat_cache_dir(self.firmware_base)
        self.partition_overrides = tuple(partition_overrides or ())

    @staticmethod
    def _has_partition(layout, name: str) -> bool:
        return name in layout.partitions or name in layout.fs_images

    @staticmethod
    def _boot_kinds(layout) -> set[str]:
        return {kind for kind, _path in layout.boot_images}

    @staticmethod
    def _target_partition(target_base: str, fs_root: str) -> str:


        root_name = os.path.basename(fs_root.strip("/\\")).lower()
        if root_name.endswith("_a") or root_name.endswith("_b"):
            root_name = root_name[:-2]
        if root_name in {"boot", "vendor_boot", "init_boot", "recovery"}:
            return root_name
        probe = os.path.join(target_base, fs_root)
        patcher = ContextPatcher(target_base)
        return patcher.detect_fs_partition(probe)

    def _overlay_target_tree(self, layout, fs_root: str) -> None:

        host = os.path.join(self.target_base, fs_root)
        if not os.path.isdir(host):
            return
        part = self._target_partition(self.target_base, fs_root)
        if part not in ("system", "system_ext", "product", "vendor", "odm",
                        "vendor_dlkm", "odm_dlkm"):
            return
        layout.partitions[part] = host
        layout.partition_names.add(part)
        layout.fs_images.pop(part, None)
        layout._sources.pop(part, None)
        if part == "system":
            layout.system_root = host
            layout.system_from_image = False
            layout.sar = os.path.isdir(os.path.join(host, "system"))

    def _preflight(self, fs_root: str) -> _Evidence:
        issues: list[PreflightIssue] = []
        layout = scan_layout(self.firmware_base)
        for branch_fs in self.partition_overrides:
            self._overlay_target_tree(layout, branch_fs)
        boot_kinds = self._boot_kinds(layout)

        # This is the explicit activation contract for AI mode.  A partial
        # dump can still use plain mode, but must not obtain made-up AI labels.
        for name, present, help_text in (
            ("boot", "BOOT" in boot_kinds,
             "boot.img is required to read the ROM SELinux boot mode"),
            ("vendor_boot", "VNDRBOOT" in boot_kinds,
             "vendor_boot.img is required for the first-stage ramdisk/fstab"),
            ("system", self._has_partition(layout, "system"),
             "system partition is required for platform policy and mapping"),
            ("vendor", self._has_partition(layout, "vendor"),
             "vendor partition is required for vendor policy and file contexts"),
        ):
            if not present:
                issues.append(PreflightIssue("missing-" + name, "blocker", help_text))

        target_host = os.path.join(self.target_base, fs_root)
        if not os.path.isdir(target_host):
            issues.append(PreflightIssue(
                "target-not-unpacked", "blocker",
                f"AI patches a working folder, but '{fs_root}' is not unpacked in the target tree"))

        if any(item.severity == "blocker" for item in issues):
            return _Evidence(layout, None, None, None, {}, "", issues)

        if fs_root not in self.partition_overrides:
            self._overlay_target_tree(layout, fs_root)
        vfs, ramdisk_vfs = build_vfs(layout)
        fstab, origin = find_fstab(vfs, ramdisk_vfs, layout)
        ctx = Context(self.firmware_base, layout, vfs, ramdisk_vfs,
                      fstab=fstab, fstab_origin=origin, options={})

        policy = Policy.load(vfs)
        file_contexts = FileContexts.load(vfs)
        if policy is None:
            issues.append(PreflightIssue(
                "no-policy", "blocker",
                "no *_sepolicy.cil could be read from the complete firmware"))
        if file_contexts is None:
            issues.append(PreflightIssue(
                "no-file-contexts", "blocker",
                "no *_file_contexts could be read from the complete firmware"))

        if policy is not None:


            for partition, title in (
                ("system", "platform"), ("vendor", "vendor"),
            ):
                prefix = f"/{partition}/etc/selinux/"
                if not any(source.startswith(prefix) for source in policy.sources):
                    issues.append(PreflightIssue(
                        f"missing-{partition}-policy", "blocker",
                        f"{title} CIL was not loaded from {partition}; AI cannot validate a split policy partially"))
        if file_contexts is not None:
            for partition, title in (("system", "platform"), ("vendor", "vendor")):
                prefix = f"/{partition}/etc/selinux/"
                if not any(source.startswith(prefix) for source in file_contexts.sources):
                    issues.append(PreflightIssue(
                        f"missing-{partition}-file-contexts", "blocker",
                        f"{title} file_contexts was not loaded from {partition}; AI has no device rule set to consult"))

        # Reuse the virtual-device split-policy checks.  A blocker here means
        # there is no trustworthy policy assembly against which to validate a
        # generated context, so AI has to stop instead of papering it over.
        for finding in s2_selinux.run(ctx):
            if finding.severity == "blocker":
                issues.append(PreflightIssue("split-policy", "blocker", finding.title))

        if policy is None or file_contexts is None:
            return _Evidence(layout, vfs, policy, file_contexts, {}, "", issues)

        s0_device.run(ctx)
        props = ctx.profile.get("props", {})
        model = collect(vfs, props)
        model.set_fstab(fstab)
        model.run_triggers()

        # Resolve service paths once.  Both requested and resolved vpaths are
        # indexed because init may exec a symlink (e.g. /system/bin/ueventd).
        services: dict[str, list[Any]] = {}
        for service in model.started_services:
            for path in (service.path, getattr(vfs.lookup(service.path), "vpath", "")):
                if path:
                    services.setdefault(path, []).append(service)

        init_label = vfs.selinux("/init") or vfs.selinux("/system/bin/init")
        if not init_label:
            init_label = file_contexts.lookup("/init") or file_contexts.lookup("/system/bin/init")
        init_domain = ""
        kernel_domain = policy.sid_domain("kernel")
        if kernel_domain and init_label:
            init_domain = policy.domain_for_exec(kernel_domain, type_of(init_label))
        if not init_domain:
            issues.append(PreflightIssue(
                "init-domain-unresolved", "warning",
                "init domain could not be derived; heuristic proposals need an explicit stock file_contexts rule"))

        return _Evidence(layout, vfs, policy, file_contexts, services, init_domain, issues)

    @staticmethod
    def _read_existing_contexts(patcher: ContextPatcher, fs_root: str) -> dict[str, str]:

        prefix = fs_root.strip("/")
        out = {}
        for path, context in patcher.ctx_map.items():
            raw = path.lstrip("/")
            if raw == prefix or raw.startswith(prefix + "/"):
                out[path] = context
        return out

    def _raw_candidates(self, fs_root: str) -> list[tuple[str, str, str, str]]:

        patcher = ContextPatcher(self.target_base)
        root = fs_root.strip("/")
        host_root = os.path.join(self.target_base, root)
        if not os.path.isdir(host_root):
            return []

        known = self._read_existing_contexts(patcher, root)


        out: list[tuple[str, str, str, str]] = []

        # The root itself is relevant after a partial/unusual unpack.  It is
        # still rejected unless the current ROM policy supplies evidence.
        root_key = "/" + root
        if root_key not in known and not patcher.is_context_locked(root):
            inferred = patcher.infer_context(root)
            out.append((root_key, root_key, inferred["context"], inferred["reason"]))

        for dirname, dirs, files in os.walk(host_root):
            for name in (*dirs, *files):
                rel = os.path.relpath(os.path.join(dirname, name), self.target_base)
                rel = rel.replace("\\", "/")
                key = patcher.escape_ctx_key(rel)
                if key in known or patcher.is_context_locked(rel):
                    continue
                inferred = patcher.infer_context(rel)
                out.append(("/" + rel, key, inferred["context"], inferred["reason"]))
        return out

    @staticmethod
    def _exec_check(policy: Policy, init_domain: str, service, context: str) -> tuple[bool, str]:

        if not init_domain:
            return False, "init domain cannot be derived"
        exec_type = type_of(context)
        if not policy.allowed(init_domain, exec_type, "file", "execute"):
            return False, f"init lacks execute on {exec_type}"
        target_domain = type_of(service.seclabel) if service.seclabel else \
            policy.domain_for_exec(init_domain, exec_type)
        if target_domain and target_domain != init_domain:
            if not policy.allowed(target_domain, exec_type, "file", "entrypoint"):
                return False, f"{target_domain} lacks entrypoint on {exec_type}"
            if not policy.allowed(init_domain, target_domain, "process", "transition"):
                return False, f"init lacks transition to {target_domain}"
            return True, f"init -> {target_domain} exec chain is allowed"
        if not policy.allowed(init_domain, exec_type, "file", "execute_no_trans"):
            return False, f"init lacks execute_no_trans on {exec_type}"
        return True, "init execute_no_trans chain is allowed"

    def _evaluate(self, evidence: _Evidence, device_path: str, predicted: str,
                  reason: str, *, cache_path: str | None = None) -> Proposal:
        cache_path = cache_path or device_path
        stock = evidence.file_contexts.lookup(device_path) if evidence.file_contexts else ""
        context = stock or predicted
        source = "stock-file-contexts" if stock else "heuristic:" + reason
        label_type = type_of(context)

        if not context or not label_type or label_type not in evidence.policy.types:
            return Proposal(cache_path, context, "reject", source,
                            f"'{label_type or context or 'empty'}' is not a concrete type in this ROM policy; "
                            "this needs a real sepolicy source change, which AI file_contexts will not invent",
                            predicted)

        services = evidence.services.get(device_path, [])
        if services:
            for service in services:
                ok, detail = self._exec_check(evidence.policy, evidence.init_domain,
                                              service, context)
                if not ok:
                    return Proposal(cache_path, context, "reject", source, detail,
                                    predicted, service.name)
            return Proposal(cache_path, context, "accept", source,
                            "all observed init service exec chains allow this label",
                            predicted, services[0].name)

        # A device's own file_contexts is sufficient evidence for a data file
        # or a binary that is not launched by init.  A path-only guess is not.
        if stock:
            return Proposal(cache_path, context, "accept", source,
                            "label comes from this firmware's file_contexts",
                            predicted)
        return Proposal(cache_path, context, "reject", source,
                        "no matching stock file_contexts rule and no observed init service can validate this guess",
                        predicted)

    def plan(self, fs_root: str) -> PatchPlan:

        evidence = self._preflight(fs_root)
        ready = not any(item.severity == "blocker" for item in evidence.issues)
        plan = PatchPlan(self.firmware_base, self.target_base, fs_root, ready,
                         issues=evidence.issues,
                         policy_sources=(evidence.policy.sources if evidence.policy else []),
                         mounts=([
                             {"point": mount.point, "partition": mount.partition,
                              "source": mount.source.label}
                             for mount in evidence.vfs.mounts
                         ] if evidence.vfs is not None else []),
                         init_domain=evidence.init_domain)
        if not ready:
            return plan

        for device_path, cache_path, predicted, reason in self._raw_candidates(fs_root):
            plan.proposals.append(self._evaluate(
                evidence, device_path, predicted, reason, cache_path=cache_path))
        return plan

    @staticmethod
    def _read_context_file(path: str) -> dict[str, str]:
        out = {}
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                for raw in handle:
                    fields = raw.strip().split(None, 1)
                    if len(fields) == 2 and not fields[0].startswith("#"):
                        out[fields[0]] = fields[1]
        except OSError:
            pass
        return out

    def commit(self, plan: PatchPlan) -> int:

        if not plan.ready:
            raise ValueError(plan.summary())

        cache_dir = self.cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        stem = plan.fs_root.strip("/") + "_file_contexts"
        cache_path = os.path.join(cache_dir, stem)
        manifest_path = os.path.join(cache_dir, stem + ".policy-ai.json")
        current = self._read_context_file(cache_path)

        managed = set()
        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                previous = json.load(handle)
            if previous.get("engine_version") == ENGINE_VERSION:
                managed = set((previous.get("accepted") or {}).keys())
        except (OSError, ValueError, TypeError):
            pass

        legacy = {key: value for key, value in current.items() if key not in managed}
        if legacy and os.path.isfile(cache_path):
            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            shutil.copy2(cache_path, cache_path + f".legacy-{suffix}.bak")

        accepted = {item.path: item.context for item in plan.accepted}
        with open(cache_path, "w", encoding="utf-8", newline="\n") as handle:
            for path in sorted(accepted):
                handle.write(f"{path} {accepted[path]}\n")

        manifest = plan.to_dict()
        manifest["committed_at"] = datetime.now(timezone.utc).isoformat()
        manifest["accepted"] = accepted
        manifest["legacy_entries_backed_up"] = sorted(legacy)
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
        return len(accepted)
