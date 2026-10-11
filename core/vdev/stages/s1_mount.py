# SPDX-License-Identifier: MPL-2.0
from __future__ import annotations

import os
import posixpath

from core.vdev.avb import find_vbmeta_images
from core.vdev.report import Finding, BLOCKER, WARN, INFO, PASS

KEY = "mount"
LABEL = "First-stage mount & AVB"

# Mount point yang isinya memang bukan partisi ROM — jangan dilaporin hilang.
_RUNTIME_MOUNTS = {
    "/data", "/metadata", "/cache", "/mnt", "/storage", "/proc", "/sys",
    "/dev", "/tmp", "/apex", "/linkerconfig", "/debug_ramdisk", "/postinstall",
}


_FS_TYPES = {"ext4", "ext3", "ext2", "erofs", "f2fs", "squashfs", "vfat",
             "exfat", "virtiofs", "overlay"}

_MAX_LIST = 10


def _partition_of(entry):

    mp = entry.mount_point.strip("/")
    return mp.split("/")[0] if mp else "system"


def _check_modules(ctx, findings):

    rd = ctx.ramdisk_vfs
    if rd is None:
        return

    listed = None
    load_path = ""
    for cand in ("/lib/modules/modules.load",
                 "/lib/modules/modules.load.recovery",
                 "/modules.load"):
        text = rd.read_text(cand)
        if text:
            listed = [x.strip() for x in text.splitlines() if x.strip()]
            load_path = cand
            break

    if listed is None:
        # Banyak device non-GKI memang tidak punya ini — bukan masalah.
        return

    moddir = posixpath.dirname(load_path) or "/lib/modules"
    missing = []
    for name in listed:
        base = posixpath.basename(name)
        if not rd.exists(posixpath.join(moddir, base)) and not rd.exists(name):
            missing.append(base)

    if missing:
        findings.append(Finding(
            KEY, "modules-missing", BLOCKER,
            f"{len(missing)} kernel module(s) from modules.load are missing",
            "Missing: " + ", ".join(missing[:12])
            + (f" (+{len(missing) - 12} more)" if len(missing) > 12 else ""),
            "Missing first-stage modules stop init from bringing up storage "
            "— no partition can be mounted at all. Take them from the "
            "target device stock vendor_boot.",
            load_path,
        ))
    else:
        findings.append(Finding(
            KEY, "modules", PASS,
            f"All {len(listed)} first-stage kernel modules present", "", "", load_path,
        ))


def _hashtree_available(ctx, metas, findings):

    from core.vdev.avbdesc import summarize
    parts = set()
    for meta in metas.values():
        try:
            with open(meta.path, "rb") as f:
                blob = f.read()
        except OSError:
            continue
        for d in summarize(blob)["hashtree"]:
            parts.add(d.partition)
    if not parts:
        return
    images = set(ctx.layout.images) | set(ctx.layout.fs_images)
    checkable = sorted(parts & images)
    if not checkable:
        return
    findings.append(Finding(
        KEY, "verity-digest-available", INFO,
        f"{len(checkable)} partition(s) can be verified against vbmeta",
        ", ".join(checkable[:_MAX_LIST])
        + ". Their dm-verity root digest can be recomputed from the image and "
        "compared with the one signed in vbmeta — turning \"probably rejected\" "
        "into a measured yes or no.",
        "Enable the hashtree verification option to run it (reads every "
        "protected partition in full, so it takes a while).",
    ))

def _verify_hashtrees(ctx, metas, extra_dirs, findings):

    from core.vdev.avbdesc import summarize
    from core.vdev.hashtree import verify

    # Kumpulkan deskriptor dari SEMUA vbmeta yang ketemu, termasuk yang
    # dirantai (vbmeta_system melindungi system, vbmeta_vendor -> vendor).
    desc_by_part = {}
    for name, meta in metas.items():
        try:
            with open(meta.path, "rb") as f:
                blob = f.read()
        except OSError:
            continue
        for d in summarize(blob)["hashtree"]:
            desc_by_part.setdefault(d.partition, (d, name))

    if not desc_by_part:
        return

    images = dict(ctx.layout.images)
    images.update(ctx.layout.fs_images)

    ok_list, bad_list, skipped = [], [], []
    for part, (desc, vb_name) in sorted(desc_by_part.items()):
        img = images.get(part)
        if not img:
            skipped.append(part)
            continue
        matched, _root = verify(img, desc)
        if matched is None:
            skipped.append(part)
        elif matched:
            ok_list.append((part, vb_name))
        else:
            bad_list.append((part, vb_name))

    if ok_list:
        findings.append(Finding(
            KEY, "verity-digest-ok", PASS,
            f"{len(ok_list)} partition image(s) still match their vbmeta hashtree",
            ", ".join(f"{p} (in {vb})" for p, vb in ok_list[:_MAX_LIST])
            + ". The dm-verity root digest was recomputed from the image and "
            "is identical to the one signed in vbmeta — these will pass "
            "verification as they are.",
        ))

    if bad_list:
        findings.append(Finding(
            KEY, "verity-digest-mismatch", BLOCKER,
            f"{len(bad_list)} partition image(s) no longer match their vbmeta",
            ", ".join(f"{p} (in {vb})" for p, vb in bad_list[:_MAX_LIST])
            + ". The recomputed dm-verity root digest differs from the one "
            "signed in vbmeta, so the kernel will refuse to mount them. This "
            "is measured, not inferred.",
            "Flash a vbmeta patched with --disable-verity "
            "--disable-verification, or re-sign vbmeta against these images.",
        ))

    if skipped:
        findings.append(Finding(
            KEY, "verity-digest-skipped", INFO,
            f"{len(skipped)} AVB-protected partition(s) not verified",
            ", ".join(sorted(skipped)[:_MAX_LIST])
            + ". Their image is not in this project (only an unpacked folder, "
            "or absent), and a digest cannot be computed for an image that "
            "has not been repacked yet.",
        ))

def _check_avb(ctx, entries, findings):

    verified = [e for e in entries if e.avb]
    if not verified:
        findings.append(Finding(
            KEY, "avb-none", INFO, "No AVB-flagged partition in fstab",
            "This device does not use dm-verity for its first-stage mounts, "
            "or the fstab was not fully read.",
        ))
        return

    # vbmeta yang benar-benar ada di project (termasuk folder flashable).
    extra = []
    for sub in ("flshbl", "flashable", "out", "output"):
        p = os.path.join(ctx.port_dir, sub)
        if os.path.isdir(p):
            extra.append(p)
    metas = find_vbmeta_images(ctx.port_dir, extra)
    ctx.shared["vbmeta"] = {k: v.describe() for k, v in metas.items()}


    if ctx.options.get("verify_hashtree"):
        _verify_hashtrees(ctx, metas, extra, findings)
    else:
        _hashtree_available(ctx, metas, findings)


    edited = []
    for e in verified:
        part = _partition_of(e)
        if part in ctx.layout.partitions or part in ctx.layout.boot_dirs:
            edited.append((part, e))

    names = sorted({p for p, _ in edited})

    if ctx.vbmeta_enabled:
        # Simulasi: vbmeta STOCK, verity aktif.
        if edited:
            findings.append(Finding(
                KEY, "avb-verity-will-fail", BLOCKER,
                f"dm-verity will reject {len(names)} partition(s) you are editing",
                "AVB-flagged partitions present as unpacked folders in this "
                "project: " + ", ".join(names) + ". Once repacked their hash "
                "no longer matches the hashtree in vbmeta, so the mount is "
                "refused and the device bootloops.",
                "Flash vbmeta with --disable-verity --disable-verification, or "
                "switch the vbmeta toggle off in this panel to see the report "
                "without this class of problem.",
                ctx.fstab_origin,
            ))
        else:
            findings.append(Finding(
                KEY, "avb-clean", PASS,
                "Verity is on and no AVB-protected partition is being edited",
            ))
        for name, meta in sorted(metas.items()):
            if meta.patched:
                findings.append(Finding(
                    KEY, "avb-img-patched", WARN,
                    f"{name}.img in this project is already patched",
                    f"{meta.describe()} — but the toggle is set to stock vbmeta. "
                    "The report above counts verity as active, while the file "
                    "you are about to flash actually disables it.",
                    "Make them agree: switch the vbmeta toggle off, or use a "
                    "stock vbmeta.",
                    meta.path,
                ))
        return

    # Simulasi: vbmeta DIPATCH, verity dilewati.
    findings.append(Finding(
        KEY, "avb-bypassed", INFO,
        f"Verity treated as bypassed for {len(verified)} fstab entries",
        "AVB-protected partitions: "
        + ", ".join(sorted({_partition_of(e) for e in verified}))
        + ". Content changes will not be refused at mount time.",
        "You must actually flash a patched vbmeta — otherwise this report "
        "does not apply.",
    ))

    if not metas:
        findings.append(Finding(
            KEY, "avb-img-absent", WARN,
            "No vbmeta.img in the project to check",
            "Bypass is being simulated, but there is no vbmeta*.img in the "
            "port folder, so there is no way to confirm the one you flash is "
            "actually patched.",
            "Put the vbmeta.img you intend to flash in the project folder.",
        ))
        return

    for name, meta in sorted(metas.items()):
        if meta.patched:
            findings.append(Finding(
                KEY, "avb-img-ok", PASS,
                f"{name}.img is patched — matches the simulation",
                meta.describe(), "", meta.path,
            ))
        elif edited:
            findings.append(Finding(
                KEY, "avb-img-stock", BLOCKER,
                f"{name}.img is still stock, but the simulation assumes verity is off",
                f"{meta.describe()}. If this is the file you flash, dm-verity "
                "stays active and the partitions you are editing ("
                + ", ".join(names) + ") are rejected at mount time.",
                "Patch it first: avbtool make_vbmeta_image --flags 2 …, or "
                "flash vbmeta with --disable-verity --disable-verification.",
                meta.path,
            ))
        else:


            findings.append(Finding(
                KEY, "avb-img-stock-idle", WARN,
                f"{name}.img is still stock while the toggle assumes verity is off",
                f"{meta.describe()}. Nothing in this project is being edited, "
                "so verity has nothing to reject and the mismatch changes "
                "nothing yet. It will matter the moment you unpack a partition "
                "that vbmeta covers.",
                "Leave it as is while you are only reading, or patch vbmeta "
                "before you repack anything.",
                meta.path,
            ))


def run(ctx):
    findings = []
    vfs = ctx.vfs
    entries = ctx.fstab

    if not entries:
        findings.append(Finding(
            KEY, "no-fstab", WARN,
            "fstab not found",
            "The mount map cannot be established, so parts of the partition "
            "and AVB checks in this stage are skipped.",
            "Unpack vendor_boot.img so the first-stage fstab can be read, or "
            "make sure /vendor/etc/fstab.* exists.",
        ))
        _check_modules(ctx, findings)
        return findings

    findings.append(Finding(
        KEY, "fstab", INFO,
        f"fstab in use: {len(entries)} entries",
        f"Source: {ctx.fstab_origin}", "", ctx.fstab_origin,
    ))

    first_stage = [e for e in entries if e.flag("first_stage_mount")] or entries
    seen = set()
    missing_part = []
    missing_point = []

    for e in first_stage:
        mp = e.mount_point
        if mp in seen or mp in _RUNTIME_MOUNTS:
            continue
        if e.fs_type.lower() not in _FS_TYPES:
            continue          # partisi blok mentah — bukan filesystem
        seen.add(mp)
        part = _partition_of(e)

        # Partisi ada di project? (kalau tidak di-unpack, tidak bisa dianalisa —
        # tapi belum tentu salah: user mungkin cuma pakai partisi stock.)
        if part not in ctx.layout.partitions and part not in ctx.layout.images:
            missing_part.append((mp, part))


        if ctx.has_system and mp != "/" and not vfs.is_dir(mp):
            missing_point.append(mp)

    if missing_point:
        findings.append(Finding(
            KEY, "mountpoint-missing", BLOCKER,
            f"{len(missing_point)} mount point(s) missing from root",
            "Not found: " + ", ".join(missing_point)
            + ". init does not create mount-point folders at this stage; if "
            "the folder is absent, the mount fails.",
            "Create the empty folders in the system root (e.g. /vendor, "
            "/product) with the correct permissions and SELinux context.",
        ))
    elif ctx.has_system:
        findings.append(Finding(
            KEY, "mountpoint", PASS,
            f"{len(seen)} first-stage mount point(s) present in root",
        ))

    if missing_part:
        findings.append(Finding(
            KEY, "partition-absent", INFO,
            f"{len(missing_part)} fstab partition(s) not present in this project",
            ", ".join(f"{mp} (needs '{p}')" for mp, p in missing_part)
            + ". Not automatically an error — these may be used as-is from "
            "the device. But their contents are not analysed.",
            "Unpack those partitions into this project if you want them "
            "checked.",
        ))

    logical = [e for e in first_stage if e.flag("logical")]
    if logical:
        findings.append(Finding(
            KEY, "logical", INFO,
            f"{len(logical)} logical partition(s) (dynamic partitions / super)",
            ", ".join(sorted({_partition_of(e) for e in logical}))
            + " — built from super.img via dm-linear, not physical partitions.",
        ))

    _check_modules(ctx, findings)
    _check_avb(ctx, first_stage, findings)
    return findings
