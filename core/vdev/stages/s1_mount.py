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

    from core.vdev.avbdesc import summarize, Descriptor, TAG_HASHTREE
    from core.vdev.hashtree import verify

    # Kumpulkan deskriptor dari SEMUA vbmeta yang ketemu (file disk + layout.vbmeta_reports ZIP/staged).
    desc_by_part = {}
    for vname, vrep in (getattr(ctx.layout, "vbmeta_reports", None) or {}).items():
        if not isinstance(vrep, dict) or not vrep.get("valid"):
            continue
        for d in vrep.get("descriptors") or []:
            if d.get("kind") != "hashtree" or not d.get("partition"):
                continue
            ht = Descriptor(TAG_HASHTREE)
            ht.partition = str(d["partition"])
            ht.image_size = int(d.get("image_size") or 0)
            ht.tree_offset = int(d.get("tree_offset") or 0)
            ht.tree_size = int(d.get("tree_size") or 0)
            ht.data_block_size = int(d.get("data_block_size") or 4096)
            ht.hash_block_size = int(d.get("hash_block_size") or 4096)
            ht.hash_algorithm = str(d.get("hash_algorithm") or "sha256")
            ht.salt = bytes.fromhex(d["salt_hex"]) if d.get("salt_hex") else b""
            ht.digest = bytes.fromhex(d["digest_hex"]) if d.get("digest_hex") else b""
            desc_by_part.setdefault(ht.partition, (ht, vname))

    for name, meta in metas.items():
        if not meta.path or meta.path.startswith("zip://"):
            continue
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
        if not img or not os.path.isfile(img):
            skipped.append(part)
            continue
        # Skip super.img container path (logical partition digest requires extent slicing)
        if getattr(ctx.layout, "super_path", "") and os.path.normcase(img) == os.path.normcase(ctx.layout.super_path):
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
            f"{len(ok_list)} partition image(s) match their vbmeta hashtree",
            ", ".join(f"{p} (in {vb})" for p, vb in ok_list[:_MAX_LIST])
            + ". The dm-verity root digest was recomputed from the image and "
            "is identical to the one signed in vbmeta.",
        ))

    if bad_list:
        if ctx.vbmeta_enabled:
            ctx.shared["avb_blocker"] = True
            findings.append(Finding(
                KEY, "verity-digest-mismatch", BLOCKER,
                f"dm-verity corrupt: {len(bad_list)} partition image(s) fail vbmeta hashtree",
                ", ".join(f"{p} (in {vb})" for p, vb in bad_list[:_MAX_LIST])
                + ". The recomputed dm-verity root digest differs from the one "
                "signed in vbmeta — the kernel will reject mounting these partitions.",
                "Flash vbmeta with --disable-verity --disable-verification, "
                "or turn AVB: OFF in Boot Analyzer to continue simulating userspace.",
            ))
        else:
            findings.append(Finding(
                KEY, "verity-digest-bypassed", INFO,
                f"{len(bad_list)} partition image(s) differ from vbmeta hashtree (bypassed: AVB OFF)",
                ", ".join(f"{p} (in {vb})" for p, vb in bad_list[:_MAX_LIST])
                + ". Treated as bypassed because AVB is OFF.",
            ))

    if skipped:
        findings.append(Finding(
            KEY, "verity-digest-skipped", INFO,
            f"{len(skipped)} AVB-protected partition(s) not verified",
            ", ".join(sorted(skipped)[:_MAX_LIST])
            + ". Their image is unpacked, inside super.img, or absent.",
        ))

def _verify_dtbo_avb_hash(ctx, findings):
    """Verify dtbo.img payload digest against any 'dtbo' AVB hash descriptor in vbmeta."""
    dtbo_src = getattr(ctx.layout, "dtbo_source", "") or ""
    if not dtbo_src:
        return
    vb_reps = getattr(ctx.layout, "vbmeta_reports", None) or {}
    if not vb_reps:
        return
    # Locate AVB hash descriptor targeting dtbo (prefer vbmeta.img, fallback to dtbo:embedded)
    desc = None
    desc_owner = ""
    root_disabled = False
    root_vb = vb_reps.get("vbmeta")
    if isinstance(root_vb, dict):
        root_disabled = bool(root_vb.get("verification_disabled"))

    for vname, vrep in vb_reps.items():
        if not isinstance(vrep, dict) or not vrep.get("valid"):
            continue
        for d in vrep.get("descriptors") or []:
            if d.get("kind") == "hash" and str(d.get("partition", "")).lower() in ("dtbo", "dtbo_a", "dtbo_b"):
                desc = d
                desc_owner = vname
                break
        if desc is not None and vname != "dtbo:embedded":
            break
    if desc is None or not desc.get("digest_hex"):
        return

    zip_entry = None
    host_path = dtbo_src
    if dtbo_src.startswith("zip://") and "#" in dtbo_src:
        raw_z = dtbo_src[len("zip://"):]
        host_path, zip_entry = raw_z.split("#", 1)
    elif not os.path.isfile(host_path):
        return

    try:
        from core import native_backend
        res = native_backend.vdev_verify_avb_hash(
            host_path,
            int(desc.get("image_size") or 0),
            desc.get("hash_algorithm") or "sha256",
            desc.get("salt_hex") or "",
            desc.get("digest_hex") or "",
            zip_entry=zip_entry,
        )
    except Exception:
        return

    matched = res.get("matched")
    if matched is True:
        findings.append(Finding(
            KEY, "dtbo-avb-hash-ok", PASS,
            f"dtbo.img AVB {desc.get('hash_algorithm', 'sha256')} digest verified ({desc_owner})",
            f"Digest {res.get('computed_root', '')[:16]}… matches {desc_owner} ({res.get('elapsed_ms', 0)} ms).",
            "", dtbo_src,
        ))
    elif matched is False:
        if ctx.vbmeta_enabled and not root_disabled:
            ctx.shared["avb_blocker"] = True
            findings.append(Finding(
                KEY, "dtbo-avb-hash-mismatch", BLOCKER,
                f"dtbo.img AVB hash does not match {desc_owner}",
                f"Expected {str(desc.get('digest_hex', ''))[:16]}…, computed {str(res.get('computed_root', ''))[:16]}…. "
                "The bootloader will reject dtbo.img with a dm-verity/AVB Red State error.",
                "Flash vbmeta with --disable-verification or turn AVB: OFF in Boot Analyzer.",
                dtbo_src,
            ))
        else:
            findings.append(Finding(
                KEY, "dtbo-avb-hash-bypassed", INFO,
                f"dtbo.img AVB hash differs from {desc_owner} (verification disabled)",
                f"Computed {str(res.get('computed_root', ''))[:16]}… vs descriptor {str(desc.get('digest_hex', ''))[:16]}….",
                "", dtbo_src,
            ))


def _check_avb(ctx, entries, findings):

    # 0) Check for corrupt/unreadable vbmeta*.img files
    invalid_vb = getattr(ctx.layout, "invalid_vbmeta", None) or []
    if invalid_vb:
        names_bad = ", ".join(n for n, _ in invalid_vb)
        if ctx.vbmeta_enabled:
            ctx.shared["avb_blocker"] = True
            findings.append(Finding(
                KEY, "vbmeta-corrupt", BLOCKER,
                f"Corrupt or invalid AVB header in {names_bad}",
                "Staged vbmeta image(s) do not contain a valid AVB0 header — the bootloader will reject boot.",
                "Restore a valid vbmeta.img or turn AVB: OFF to bypass AVB verification.",
                invalid_vb[0][1],
            ))
        else:
            findings.append(Finding(
                KEY, "vbmeta-corrupt-bypassed", INFO,
                f"Invalid vbmeta header in {names_bad} ignored (AVB: OFF)",
                "AVB is OFF in Boot Analyzer; remember to flash a valid patched vbmeta on device.",
                "", invalid_vb[0][1],
            ))

    # vbmeta yang benar-benar ada di project (termasuk folder flashable & zip/boot).
    extra = []
    for sub in ("flshbl", "flashable", "out", "output"):
        p = os.path.join(ctx.port_dir, sub)
        if os.path.isdir(p):
            extra.append(p)
    metas = find_vbmeta_images(ctx.port_dir, extra)
    for k, rep in (getattr(ctx.layout, "vbmeta_reports", None) or {}).items():
        if k not in metas and isinstance(rep, dict) and rep.get("valid"):
            from core.vdev.avb import VbMeta
            metas[k] = VbMeta(
                f"zip://{ctx.layout.zip_path}#{k}" if getattr(ctx.layout, "zip_path", "") else k,
                int(rep.get("flags", 0)),
                rep.get("release", ""),
                int(rep.get("algorithm", 0)),
            )
    ctx.shared["vbmeta"] = {k: v.describe() for k, v in metas.items()}
    _verify_dtbo_avb_hash(ctx, findings)

    verified = [e for e in entries if e.avb]
    if not verified:
        findings.append(Finding(
            KEY, "avb-none", INFO, "No AVB-flagged partition in fstab",
            "First-stage mount entries did not carry explicit avb flags"
            + (f" ({len(metas)} vbmeta image(s) still inspected)." if metas else "."),
        ))
        return


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
        # Simulasi: AVB ON (vbmeta STOCK, verity aktif).
        if edited:
            ctx.shared["avb_blocker"] = True
            findings.append(Finding(
                KEY, "avb-verity-will-fail", BLOCKER,
                f"dm-verity will reject {len(names)} partition(s) you are editing",
                "AVB-flagged partitions present as unpacked folders in this "
                "project: " + ", ".join(names) + ". Once repacked their hash "
                "no longer matches the hashtree in vbmeta, so the mount is "
                "refused and the device bootloops.",
                "Flash vbmeta with --disable-verity --disable-verification, or "
                "switch AVB: OFF in Boot Analyzer to continue testing userspace.",
                ctx.fstab_origin,
            ))
        elif not ctx.shared.get("avb_blocker"):
            findings.append(Finding(
                KEY, "avb-clean", PASS,
                "AVB ON — no AVB-protected partition is modified",
            ))
        for name, meta in sorted(metas.items()):
            if meta.patched:
                findings.append(Finding(
                    KEY, "avb-img-patched", WARN,
                    f"{name}.img is already patched (verity/verification disabled)",
                    f"{meta.describe()} — while AVB toggle is ON.",
                    "Switch AVB: OFF to match the patched vbmeta, or use stock vbmeta.",
                    meta.path,
                ))
        return

    # Simulasi: AVB OFF (verity/vbmeta dilewati — tidak perlu patch vbmeta dulu di Boot Analyzer).
    ctx.shared["avb_bypassed"] = True
    findings.append(Finding(
        KEY, "avb-bypassed", INFO,
        f"AVB: OFF — dm-verity & vbmeta bypassed for {len(verified)} fstab entries",
        "Protected partitions: "
        + ", ".join(sorted({_partition_of(e) for e in verified}))
        + ". Modified images/folders are allowed during this simulation.",
        "Remember to flash vbmeta with --disable-verity --disable-verification on the real device.",
    ))

    for name, meta in sorted(metas.items()):
        if meta.patched:
            findings.append(Finding(
                KEY, "avb-img-ok", PASS,
                f"{name}.img is patched — matches AVB: OFF",
                meta.describe(), "", meta.path,
            ))
        elif edited:
            findings.append(Finding(
                KEY, "avb-img-stock-bypassed", INFO,
                f"{name}.img is still stock (bypassed because AVB: OFF)",
                f"{meta.describe()}. Edited partition(s): {', '.join(names)}. "
                "Allowed in simulation; flash a patched vbmeta on device.",
                "fastboot --disable-verity --disable-verification flash vbmeta vbmeta.img",
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
        available_parts = set(ctx.layout.all_partition_names()) | set(ctx.layout.images)
        if part not in available_parts:
            missing_part.append((mp, part))


        if ctx.has_system and mp != "/" and not mp.startswith("/mnt/") and not vfs.is_dir(mp):
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
    _check_fbe_coherence(ctx, entries, findings)
    return findings


def _check_fbe_coherence(ctx, entries, findings):
    """Verify File-Based Encryption (FBE) fstab flags against /metadata mount,
    Android SDK level, and vendor KeyMint / Keymaster / Gatekeeper HALs."""
    import xml.etree.ElementTree as ET

    data_entries = [e for e in entries if e.mount_point == "/data"]
    if not data_entries:
        return
    data_ent = data_entries[0]
    flags = list(data_ent.fs_mgr_flags or ())

    file_enc = ""
    meta_enc = ""
    encryptable = ""
    keydir = ""
    for fl in flags:
        low = fl.lower()
        if low.startswith("fileencryption="):
            file_enc = fl.split("=", 1)[1]
        elif low.startswith("metadata_encryption="):
            meta_enc = fl.split("=", 1)[1]
        elif low.startswith(("encryptable=", "forceencrypt=")):
            encryptable = fl.split("=", 1)[1]
        elif low.startswith("keydirectory="):
            keydir = fl.split("=", 1)[1]

    has_metadata_mount = any(e.mount_point == "/metadata" for e in entries)
    uses_wrappedkey = "wrappedkey" in (file_enc + " " + meta_enc).lower()
    uses_inlinecrypt = "inlinecrypt" in (file_enc + " " + meta_enc + " " + " ".join(flags)).lower()
    try:
        sdk = int(ctx.prop("ro.build.version.sdk") or (ctx.profile or {}).get("sdk") or 0)
    except Exception:
        sdk = 0

    fbe_info = {
        "fileencryption": file_enc,
        "metadata_encryption": meta_enc,
        "encryptable": encryptable,
        "keydirectory": keydir,
        "has_metadata_mount": has_metadata_mount,
        "uses_wrappedkey": uses_wrappedkey,
        "uses_inlinecrypt": uses_inlinecrypt,
        "keymint": False,
        "keymaster_versions": [],
        "gatekeeper": False,
    }
    ctx.shared["fbe"] = fbe_info

    # 1) Legacy FDE (encryptable=/forceencrypt=) on Android 13+ (SDK >= 33) -> removed in AOSP vold
    if encryptable and not file_enc and sdk >= 33:
        findings.append(Finding(
            KEY, "fde-unsupported-sdk", BLOCKER,
            f"fstab /data uses legacy FDE ({encryptable}) which Android {sdk} removed",
            "Android 13+ (API 33+) removed Full-Disk Encryption (FDE) support from vold. "
            "Boot will stall mounting /data and fall back to recovery.",
            "Switch /data in fstab to fileencryption=aes-256-xts:aes-256-cts:v2 or disable encryption cleanly.",
            ctx.fstab_origin,
        ))
        return

    if not file_enc and not meta_enc:
        findings.append(Finding(
            KEY, "fbe-disabled", INFO,
            "fstab /data has no fileencryption/metadata_encryption flag (unencrypted /data)",
            f"Source: {ctx.fstab_origin}",
            "", ctx.fstab_origin,
        ))
        return

    # 2) metadata_encryption or keydirectory=/metadata/... requires a /metadata entry in fstab
    needs_metadata = bool(meta_enc) or keydir.startswith("/metadata")
    if needs_metadata and not has_metadata_mount:
        findings.append(Finding(
            KEY, "fbe-metadata-mount-missing", BLOCKER,
            "fstab /data requires metadata encryption, but no /metadata mount entry exists",
            f"fileencryption={file_enc or '-'}, metadata_encryption={meta_enc or '-'}, keydirectory={keydir or '-'}. "
            "vold cannot store or unwrap FBE metadata keys without /metadata mounted in first-stage/init.",
            "Add the stock /metadata fstab entry matching this device's partition layout.",
            ctx.fstab_origin,
        ))

    # 3) Inspect vendor/odm VINTF manifests & /vendor/bin/hw for KeyMint / Keymaster / Gatekeeper
    if not ctx.has_vendor:
        findings.append(Finding(
            KEY, "fbe-config", INFO,
            f"FBE configured on /data ({file_enc or meta_enc})",
            "Vendor partition not loaded — KeyMint/Keymaster/Gatekeeper HAL presence not verified.",
            "", ctx.fstab_origin,
        ))
        return

    vfs = ctx.vfs
    vintf_hals = {}  # hal_name -> set(versions)
    vintf_dirs = (
        "/vendor/etc/vintf", "/vendor/etc/vintf/manifest",
        "/odm/etc/vintf", "/odm/etc/vintf/manifest",
    )
    for d in vintf_dirs:
        if not vfs.is_dir(d):
            continue
        for fn in (vfs.listdir(d) or ()):
            if not fn.endswith(".xml") or "matrix" in fn.lower():
                continue
            txt = vfs.read_text(posixpath.join(d, fn))
            if not txt:
                continue
            try:
                root = ET.fromstring(txt)
            except Exception:
                continue
            for hal in root.findall(".//hal"):
                nel = hal.find("name")
                if nel is None or not nel.text:
                    continue
                hname = nel.text.strip()
                vers = vintf_hals.setdefault(hname, set())
                for vel in hal.findall("version"):
                    if vel.text:
                        vers.add(vel.text.strip())
                for tel in hal.findall("fqname"):
                    if tel.text and "@" in tel.text:
                        vers.add(tel.text.strip().split("@", 1)[1].split("::", 1)[0])

    hw_bins = set()
    for hwd in ("/vendor/bin/hw", "/odm/bin/hw"):
        if vfs.is_dir(hwd):
            hw_bins.update(fn.lower() for fn in (vfs.listdir(hwd) or ()))

    has_keymint = (
        "android.hardware.security.keymint" in vintf_hals
        or any("keymint" in b for b in hw_bins)
    )
    km_vers = sorted(vintf_hals.get("android.hardware.keymaster") or ())
    has_keymaster = bool(km_vers) or any("keymaster" in b for b in hw_bins)
    has_gatekeeper = (
        "android.hardware.gatekeeper" in vintf_hals
        or any("gatekeeper" in b for b in hw_bins)
    )
    fbe_info["keymint"] = has_keymint
    fbe_info["keymaster_versions"] = km_vers
    fbe_info["gatekeeper"] = has_gatekeeper

    has_issue = False
    if not has_keymint and not has_keymaster:
        has_issue = True
        findings.append(Finding(
            KEY, "fbe-keystore-hal-missing", BLOCKER,
            "FBE enabled in fstab, but vendor has neither KeyMint nor Keymaster HAL",
            f"/data uses fileencryption={file_enc or meta_enc}, yet neither android.hardware.security.keymint "
            "nor android.hardware.keymaster was found in vendor/odm VINTF manifests or /vendor/bin/hw. "
            "vold/keystore2 cannot initialize FBE keys -> guaranteed bootloop to recovery.",
            "Restore the device's stock KeyMint/Keymaster HAL binary + VINTF manifest in vendor.",
            ctx.fstab_origin,
        ))
    elif uses_wrappedkey and not has_keymint and km_vers and all(v.startswith("3.") for v in km_vers):
        has_issue = True
        findings.append(Finding(
            KEY, "fbe-wrappedkey-mismatch", WARN,
            f"fstab /data requests wrappedkey, but vendor only declares Keymaster {', '.join(km_vers)}",
            "Hardware-wrapped FBE keys require KeyMint or Keymaster 4.0+.",
            "Remove wrappedkey_v0 from /data fstab flags or use a vendor with Keymaster 4.0+/KeyMint.",
            ctx.fstab_origin,
        ))

    if not has_gatekeeper:
        has_issue = True
        findings.append(Finding(
            KEY, "fbe-gatekeeper-missing", WARN,
            "FBE enabled in fstab, but vendor Gatekeeper HAL is absent",
            "android.hardware.gatekeeper was not found in vendor/odm VINTF or /vendor/bin/hw. "
            "Lockscreen credential verification and CE storage unlock may fail.",
            "Restore android.hardware.gatekeeper service and manifest into vendor.",
            ctx.fstab_origin,
        ))

    if not has_issue and not (needs_metadata and not has_metadata_mount):
        ks_lbl = "KeyMint" if has_keymint else (f"Keymaster {'/'.join(km_vers)}" if km_vers else "Keymaster")
        extra = []
        if meta_enc:
            extra.append(f"metadata_encryption={meta_enc}")
        if uses_inlinecrypt:
            extra.append("inlinecrypt")
        if uses_wrappedkey:
            extra.append("wrappedkey")
        findings.append(Finding(
            KEY, "fbe-coherent", PASS,
            f"FBE encryption coherent ({ks_lbl} + Gatekeeper present)",
            f"/data fileencryption={file_enc or '-'}"
            + (f" ({', '.join(extra)})" if extra else "")
            + ("; /metadata mount OK" if has_metadata_mount else ""),
            "", ctx.fstab_origin,
        ))
