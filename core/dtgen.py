#!/usr/bin/env python3


from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
from pathlib import Path

from core import magiskboot_tree as mbt
from core.proc_util import CREATE_NO_WINDOW

# Boot header v3+/v4 dropped the offset fields, so these are the conventional
# defaults twrpdtgen emits for modern images (absolute values, not computed).
DEFAULT_KERNEL_BASE = 0x3FFF8000
DEFAULT_RAMDISK_OFFSET = 0x26F08000
DEFAULT_TAGS_OFFSET = 0x07C88000


def _run_mb(mb, args, cwd):
    return subprocess.run([mb, *args], cwd=str(cwd), text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          encoding="utf-8", errors="replace",
                          creationflags=CREATE_NO_WINDOW)


# boot image probing
def probe_boot(mb: str, img: Path, work: Path) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    out = _run_mb(mb, ["unpack", "-h", str(img)], work).stdout
    info = {}
    for key in ("HEADER_VER", "KERNEL_SZ", "RAMDISK_SZ", "OS_VERSION",
                "OS_PATCH_LEVEL", "PAGESIZE", "CMDLINE", "KERNEL_FMT",
                "RAMDISK_FMT", "NAME", "DTB_SZ"):
        m = re.search(rf"{key}\s*\[(.*?)\]", out)
        if m:
            info[key] = m.group(1)
    hp = work / "header"
    if hp.is_file():
        for line in hp.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                info["hdr_" + k.strip()] = v.strip()
    return info


def kernel_arch(work: Path) -> str:
    k = work / "kernel"
    if not k.is_file():
        return "arm64"
    head = k.read_bytes()[:64]
    if head[0x38:0x3C] == b"ARMd":          # ARM64 Linux Image
        return "arm64"
    if head[0x24:0x28] == struct.pack("<I", 0x016F2818):  # 32-bit zImage
        return "arm"
    return "arm64"


# ramdisk facts
def extract_ramdisk(mb: str, work: Path) -> Path | None:
    comp = work / "ramdisk.cpio"
    if not comp.is_file():
        return None
    dec = work / "ramdisk.dec"
    _run_mb(mb, ["decompress", "ramdisk.cpio", "ramdisk.dec"], work)
    if not dec.is_file():
        dec.write_bytes(comp.read_bytes())
    _run_mb(mb, ["cpio", "ramdisk.dec", "extract"], work)
    rd = work / "ramdisk"
    return rd if rd.is_dir() else None


def _reuse_ramdisk(work: Path, unpack: Path | None) -> Path | None:

    if unpack is None:
        return None
    from core import boot_image as bi
    rd = Path(unpack) / bi.mbt.RAMDISK_DIR
    return rd if rd.is_dir() else None


def _reuse_dtb(unpack: Path | None) -> Path | None:

    if unpack is None:
        return None
    from core import boot_image as bi
    dtb = Path(unpack) / bi.META_DIR / bi.DTB_COMPONENT
    return dtb if dtb.is_file() else None


def _reuse_kernel(work: Path, unpack: Path | None) -> Path:

    if unpack is None:
        return work
    from core import boot_image as bi
    kernel = Path(unpack) / bi.META_DIR / "kernel"
    if kernel.is_file():
        work.mkdir(parents=True, exist_ok=True)
        target = work / "kernel"
        if target.exists():
            target.unlink()
        try:
            target.symlink_to(kernel)
        except OSError:
            shutil.copy2(kernel, target)
        return work
    return work


def find_fstab(rd: Path) -> Path | None:
    for p in rd.rglob("fstab*"):
        if p.is_file():
            return p
    return None


def clean_fstab(text: str) -> str:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if re.match(r'^#\s+\d+\s+"', s) or not s:
            continue
        out.append(line)
    return "\n".join(out) + "\n"


def to_twrp_fstab(text: str) -> str:

    rows = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        cols = s.split()
        if len(cols) < 3:
            continue
        src, mnt, fstype = cols[0], cols[1], cols[2]
        flags_field = cols[4] if len(cols) >= 5 else (cols[3] if len(cols) >= 4 else "")
        logical = "logical" in flags_field
        slot = "slotselect" in flags_field

        # block device column: logical partitions use the bare name, others the
        # by-name node (or the original src for /devices/... auto mounts).
        name = mnt.lstrip("/").split("/")[-1] if mnt != "auto" else "auto"
        if logical:
            blk = name
        elif src.startswith("/dev/") or src.startswith("/devices/"):
            blk = src
        else:
            blk = src

        disp = name if name else mnt.lstrip("/")
        dflags = ["display=" + disp]
        if logical:
            dflags.append("logical")
        if slot:
            dflags.append("slotselect")
        rows.append((mnt, fstype, blk, "flags=" + ";".join(dflags)))

    # pad columns to align like twrpdtgen
    out = []
    for mnt, fstype, blk, fl in rows:
        out.append(f"{mnt:<25} {fstype:<8} {blk:<48} {fl}")
    return "\n".join(out) + "\n"


def parse_props(rd: Path) -> dict:
    props = {}
    for name in ("prop.default", "default.prop", "build.prop"):
        for p in rd.rglob(name):
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                props.setdefault(k.strip(), v.strip())
    return props


def cpu_variant_from_dtb(dtb_path: Path) -> str:

    if not dtb_path.is_file():
        return ""
    try:
        from core import devicetree
        from core.devicetree import dt_image
        blob = dtb_path.read_bytes()
        if devicetree.looks_like_container(blob):
            entries = dt_image.split(blob).entries
            if not entries:
                return ""
            fdt = entries[0].fdt
        else:
            fdt = blob
        dts = devicetree.decompile(fdt)
        m = re.search(r'compatible = "arm,(cortex-a\d+)"', dts)
        return m.group(1) if m else ""
    except Exception:
        return ""


def detect_platform(rd: Path) -> str:
    for p in rd.rglob("init.recovery.*.rc"):
        m = re.match(r"init\.recovery\.(.+)\.rc", p.name)
        if m and m.group(1) not in ("usb", "logd", "service", "ldconfig", "hlthchrg"):
            return m.group(1)
    fstab = find_fstab(rd)
    if fstab and "." in fstab.name:
        return fstab.name.split(".", 1)[1]
    return "unknown"


def ab_partitions_from_fstab(text: str) -> list[str]:

    parts = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        cols = s.split()
        if len(cols) < 3:
            continue
        src, mnt = cols[0], cols[1]
        opts = cols[-1] if len(cols) >= 5 else ""
        name = mnt.lstrip("/")
        if "logical" in opts or "slotselect" in opts:
            if name and "/" not in name and name not in parts:
                parts.append(name)
    return parts


def detect_recovery_as_boot(text: str) -> bool:

    has_recovery_mount = False
    has_ab_boot = False
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        cols = s.split()
        if len(cols) < 3:
            continue
        mnt = cols[1].lstrip("/") if cols[1].startswith("/") else cols[0].lstrip("/")
        opts = cols[-1] if len(cols) >= 5 else ""
        if mnt == "recovery":
            has_recovery_mount = True
        if mnt == "boot" and "slotselect" in opts:
            has_ab_boot = True
    return has_ab_boot and not has_recovery_mount


def detect_fstype(text: str, mount: str, default: str = "ext4") -> str:

    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#") or not s:
            continue
        cols = s.split()
        if len(cols) < 3:
            continue
        # Android: col[1] is mount, fstype col[2]
        if cols[1].lstrip("/") == mount:
            return cols[2]
        # TWRP: col[0] is mount, fstype col[1]
        if cols[0].lstrip("/") == mount:
            return cols[1]
    return default


def dynamic_partition_list(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        cols = line.split()
        if len(cols) >= 5 and "logical" in cols[-1]:
            name = cols[1].lstrip("/")
            if name and "/" not in name and name not in out:
                out.append(name)
    return out


_GENERIC_MARKERS = ("mgvi", "common", "hal_", "alps", "generic", "lentitvos",
                    "treble", "gsi", "mssi")


def find_valid_vendor(search_root: Path) -> Path | None:

    if not search_root.is_dir():
        return None
    names = ("vendor", "vendor_a", "vendor_b")
    candidates = []
    for n in names:
        candidates.append(search_root / n)
        candidates.append(search_root / n / n)  # nested <vendor>/<vendor>
    # also any sibling dir literally named like vendor*
    for child in search_root.iterdir():
        if child.is_dir() and child.name.lower().startswith("vendor") and child.name.lower() not in ("vendor_boot",):
            candidates.append(child)
    seen = set()
    for c in candidates:
        if c in seen or not c.is_dir():
            continue
        seen.add(c)
        if (c / "etc" / "selinux" / "vendor_file_contexts").is_file():
            return c
    return None


def read_build_prop_multi(path: Path) -> dict:

    props = {}
    if not path.is_file():
        return props
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        props.setdefault(k.strip(), []).append(v.strip())
    return props


def pick_real(multi: dict, *keys: str, default: str = "") -> str:

    for key in keys:
        for v in multi.get(key, []):
            if v and not any(m in v.lower() for m in _GENERIC_MARKERS):
                return v
    # fall back to the last value of the first present key (often the real one)
    for key in keys:
        if multi.get(key):
            return multi[key][-1]
    return default


def collect(mb: str, boot: Path | None, vendor_boot: Path, work: Path,
            vendor_dir: Path | None = None, boot_unpack: Path | None = None,
            vendor_boot_unpack: Path | None = None) -> dict:

    facts = {"_vendor_dir": str(vendor_dir) if vendor_dir else None}

    # boot.img is optional: it carries the kernel + accurate arch/os_version.
    # Without it we fall back to the vendor abilist for arch and skip prebuilt.
    bwork = work / "boot"
    if boot is not None:
        binfo = probe_boot(mb, boot, bwork)
        facts["arch"] = kernel_arch(_reuse_kernel(bwork, boot_unpack))
    else:
        binfo = {}
        facts["arch"] = ""  # resolved from abilist below
    facts["boot"] = binfo
    facts["_has_boot"] = boot is not None

    vwork = work / "vendor_boot"
    vinfo = probe_boot(mb, vendor_boot, vwork)
    facts["vendor_boot"] = vinfo

    rd = (_reuse_ramdisk(vwork, vendor_boot_unpack) or extract_ramdisk(mb, vwork)
          or _reuse_ramdisk(bwork, boot_unpack) or extract_ramdisk(mb, bwork))
    fstab_path, fstab_text = None, ""
    if rd:
        fstab_path = find_fstab(rd)
        if fstab_path:
            fstab_text = fstab_path.read_text(encoding="utf-8", errors="replace")
    facts["fstab_path"] = str(fstab_path) if fstab_path else None
    facts["fstab_text"] = fstab_text
    facts["_rd"] = str(rd) if rd else None


    vendor_dir = facts.get("_vendor_dir")
    multi = read_build_prop_multi(Path(vendor_dir) / "build.prop") if vendor_dir else {}
    facts["_vendor_props"] = multi

    facts["device"] = pick_real(multi, "ro.product.vendor.device",
                                "ro.product.device", "ro.build.product")
    facts["brand"] = pick_real(multi, "ro.product.vendor.brand", "ro.product.brand")
    facts["model"] = pick_real(multi, "ro.product.vendor.model", "ro.product.model")
    facts["manufacturer"] = (pick_real(multi, "ro.product.vendor.manufacturer",
                                       "ro.product.manufacturer") or facts["brand"]).lower()
    facts["product_name"] = pick_real(multi, "ro.product.vendor.name",
                                      "ro.product.name") or facts["device"]
    # short codename for the output folder (ro.product.device, no brand prefix)
    facts["codename"] = pick_real(multi, "ro.product.vendor.device",
                                  "ro.product.device") or facts["device"]
    facts["platform"] = pick_real(multi, "ro.board.platform") or \
        (detect_platform(rd) if rd else "")
    facts["bootloader_board"] = pick_real(multi, "ro.product.board")
    facts["api_level"] = pick_real(multi, "ro.product.first_api_level",
                                   "ro.board.first_api_level")
    facts["abilist"] = pick_real(multi, "ro.product.cpu.abilist",
                                 "ro.vendor.product.cpu.abilist",
                                 "ro.system.product.cpu.abilist") or (
        "arm64-v8a" if facts["arch"] == "arm64" else "armeabi-v7a")
    # If boot.img was absent, derive arch from the vendor abilist.
    if not facts["arch"]:
        facts["arch"] = "arm64" if "arm64" in facts["abilist"] else "arm"
    facts["build_desc"] = pick_real(multi, "ro.build.description",
                                    "ro.vendor.build.description")
    facts["fingerprint"] = pick_real(multi, "ro.build.fingerprint",
                                     "ro.vendor.build.fingerprint")
    facts["vendor_security_patch"] = pick_real(multi, "ro.vendor.build.security_patch")
    facts["screen_density"] = pick_real(multi, "ro.sf.lcd_density")


    dtb = _reuse_dtb(vendor_boot_unpack) or (vwork / "dtb")
    facts["cpu_variant"] = cpu_variant_from_dtb(dtb) or "generic"
    facts["_dtb_path"] = str(dtb) if dtb.is_file() else None

    facts["ab_partitions"] = ab_partitions_from_fstab(fstab_text)
    facts["dyn_partitions"] = dynamic_partition_list(fstab_text)
    # filesystem types per partition — detected, not assumed (erofs vs ext4)
    facts["system_fstype"] = detect_fstype(fstab_text, "system")
    facts["userdata_fstype"] = detect_fstype(fstab_text, "data", default="f2fs")
    # true on every modern A/B device that merges recovery into boot (no
    # dedicated /recovery mount in the fstab) — a layout fact, not a guess.
    facts["recovery_as_boot"] = detect_recovery_as_boot(fstab_text)
    return facts


# templates (matched to twrpdtgen output)
HDR = """\
#
# Copyright (C) 2025 The Android Open Source Project
# Copyright (C) 2025 RennsAndroidKitchen device-tree generator
#
# SPDX-License-Identifier: Apache-2.0
#
"""


def board_config(c) -> str:
    ab = " \\\n    ".join(c["ab_partitions"]) if c["ab_partitions"] else ""
    dyn = " ".join(c["dyn_partitions"])
    second_arch = ""
    if c["arch"] == "arm64":
        second_arch = (
            "TARGET_2ND_ARCH := arm\n"
            "TARGET_2ND_ARCH_VARIANT := armv7-a-neon\n"
            "TARGET_2ND_CPU_ABI := armeabi-v7a\n"
            "TARGET_2ND_CPU_ABI2 := armeabi\n"
            "TARGET_2ND_CPU_VARIANT := generic\n"
            f"TARGET_2ND_CPU_VARIANT_RUNTIME := {c['cpu_variant']}\n")
    recovery_as_boot = "\nBOARD_USES_RECOVERY_AS_BOOT := true" if c["recovery_as_boot"] else ""
    density = f"\nTARGET_SCREEN_DENSITY := {c['screen_density']}" if c["screen_density"] else ""
    return HDR + f"""
DEVICE_PATH := device/{c['vendor']}/{c['device']}

# For building with minimal manifest
ALLOW_MISSING_DEPENDENCIES := true

# A/B
AB_OTA_UPDATER := true
AB_OTA_PARTITIONS += \\
    {ab}{recovery_as_boot}

# Architecture
TARGET_ARCH := {c['arch']}
TARGET_ARCH_VARIANT := {'armv8-a' if c['arch'] == 'arm64' else 'armv7-a-neon'}
TARGET_CPU_ABI := {c['cpu_abi']}
TARGET_CPU_VARIANT := generic
TARGET_CPU_VARIANT_RUNTIME := {c['cpu_variant']}
{second_arch}
# APEX
OVERRIDE_TARGET_FLATTEN_APEX := true

# Bootloader
{('TARGET_BOOTLOADER_BOARD_NAME := ' + c['bootloader_board']) if c['bootloader_board'] else ('# TARGET_BOOTLOADER_BOARD_NAME unknown from boot images — set from build.prop\nTARGET_BOOTLOADER_BOARD_NAME := ' + c['platform'])}
TARGET_NO_BOOTLOADER := true
TARGET_BOARD_PLATFORM := {c['platform']}{density}

# Kernel
BOARD_BOOTIMG_HEADER_VERSION := {c['header_version']}
BOARD_KERNEL_BASE := {c['kernel_base']}
BOARD_KERNEL_CMDLINE := {c['kernel_cmdline']}
BOARD_KERNEL_PAGESIZE := {c['pagesize']}
BOARD_RAMDISK_OFFSET := {c['ramdisk_offset']}
BOARD_KERNEL_TAGS_OFFSET := {c['tags_offset']}
BOARD_KERNEL_IMAGE_NAME := Image{'.gz' if c['kernel_gz'] else ''}
BOARD_MKBOOTIMG_ARGS += --header_version $(BOARD_BOOTIMG_HEADER_VERSION)
BOARD_MKBOOTIMG_ARGS += --ramdisk_offset $(BOARD_RAMDISK_OFFSET)
BOARD_MKBOOTIMG_ARGS += --tags_offset $(BOARD_KERNEL_TAGS_OFFSET)
TARGET_KERNEL_ARCH := {c['arch']}
TARGET_KERNEL_SOURCE := kernel/{c['vendor']}/{c['device']}
TARGET_KERNEL_CONFIG := {c['device']}_defconfig

# Prebuilt kernel
TARGET_FORCE_PREBUILT_KERNEL := true
ifeq ($(TARGET_FORCE_PREBUILT_KERNEL),true)
TARGET_PREBUILT_KERNEL := $(DEVICE_PATH)/prebuilt/kernel
TARGET_PREBUILT_DTB := $(DEVICE_PATH)/prebuilt/dtb.img
BOARD_MKBOOTIMG_ARGS += --dtb $(TARGET_PREBUILT_DTB)
BOARD_INCLUDE_DTB_IN_BOOTIMG :=
endif

# Partitions
BOARD_FLASH_BLOCK_SIZE := {int(c['pagesize']) * 64}
BOARD_BOOTIMAGE_PARTITION_SIZE := {c['boot_size']}
BOARD_VENDOR_BOOTIMAGE_PARTITION_SIZE := {c['vendor_boot_size']}
BOARD_RECOVERYIMAGE_PARTITION_SIZE := {c['boot_size']}
BOARD_HAS_LARGE_FILESYSTEM := true
BOARD_SYSTEMIMAGE_PARTITION_TYPE := {c['system_fstype']}
BOARD_USES_METADATA_PARTITION := true
TARGET_COPY_OUT_VENDOR := vendor

# Dynamic partitions
BOARD_SUPER_PARTITION_GROUPS := {c['vendor']}_dynamic_partitions
BOARD_{c['vendor'].upper()}_DYNAMIC_PARTITIONS_PARTITION_LIST := {dyn}

# AVB
BOARD_AVB_ENABLE := true

# Recovery
TARGET_RECOVERY_PIXEL_FORMAT := BGRA_8888
TARGET_USERIMAGES_USE_EXT4 := true
TARGET_USERIMAGES_USE_F2FS := true

# Security patch
{('PLATFORM_SECURITY_PATCH := ' + c['os_patch']) if c['os_patch'] else ''}
{('PLATFORM_VERSION := ' + c['os_version']) if c['os_version'] else ''}
{('VENDOR_SECURITY_PATCH := ' + c['vendor_security_patch']) if c['vendor_security_patch'] else ''}
"""


def device_mk(c) -> str:
    return HDR + f"""
LOCAL_PATH := device/{c['vendor']}/{c['device']}

# A/B
AB_OTA_POSTINSTALL_CONFIG += \\
    RUN_POSTINSTALL_system=true \\
    POSTINSTALL_PATH_system=system/bin/otapreopt_script \\
    FILESYSTEM_TYPE_system={c['system_fstype']} \\
    POSTINSTALL_OPTIONAL_system=true

PRODUCT_PACKAGES += \\
    android.hardware.boot@1.0-impl \\
    android.hardware.boot@1.0-service

# Boot control HAL
PRODUCT_PACKAGES += \\
    bootctrl.{c['platform']}

# Update engine
PRODUCT_PACKAGES += \\
    otapreopt_script \\
    update_engine \\
    update_engine_sideload \\
    update_verifier

# Inherit the proprietary files (run extract-files.sh first)
$(call inherit-product-if-exists, vendor/{c['vendor']}/{c['device']}/{c['device']}-vendor.mk)
"""


def omni_mk(c) -> str:
    desc = c['build_desc']
    fp = c['fingerprint']
    extra = ""
    if desc:
        extra += f'\nPRODUCT_BUILD_PROP_OVERRIDES += \\\n    PRIVATE_BUILD_DESC="{desc}"\n'
    if fp:
        extra += f"\nBUILD_FINGERPRINT := {fp}\n"
    return HDR + f"""
# Inherit from those products. Most specific first.
$(call inherit-product, $(SRC_TARGET_DIR)/product/core_64_bit.mk)
$(call inherit-product, $(SRC_TARGET_DIR)/product/full_base_telephony.mk)

# Inherit some common Omni stuff.
$(call inherit-product, vendor/omni/config/common.mk)

# Inherit from {c['device']} device
$(call inherit-product, device/{c['vendor']}/{c['device']}/device.mk)

PRODUCT_DEVICE := {c['device']}
PRODUCT_NAME := omni_{c['device']}
PRODUCT_BRAND := {c['brand']}
PRODUCT_MODEL := {c['model']}
PRODUCT_MANUFACTURER := {c['vendor']}

PRODUCT_GMS_CLIENTID_BASE := android-{c['vendor']}
{extra}"""


def android_mk(c):
    return HDR + f"""
LOCAL_PATH := $(call my-dir)

ifeq ($(TARGET_DEVICE),{c['device']})
include $(call all-subdir-makefiles,$(LOCAL_PATH))
endif
"""


def android_products_mk(c):
    return HDR + f"""
PRODUCT_MAKEFILES := \\
    $(LOCAL_DIR)/omni_{c['device']}.mk

COMMON_LUNCH_CHOICES := \\
    omni_{c['device']}-user \\
    omni_{c['device']}-userdebug \\
    omni_{c['device']}-eng
"""


def vendorsetup_sh(c):
    return HDR + f"""
add_lunch_combo omni_{c['device']}-user
add_lunch_combo omni_{c['device']}-userdebug
add_lunch_combo omni_{c['device']}-eng
"""


def android_bp(c):
    return "soong_namespace {\n}\n"


def board_config_common(c):
    return HDR + f"\ninclude device/{c['vendor']}/{c['device']}/BoardConfig.mk\n"


def system_prop(c):
    return HDR + f"\n# system.prop for {c['device']}\nro.product.board={c['platform']}\n"


def readme_md(c):
    return f"""# Android device tree for {c['brand']} {c['model']} ({c['device']})

```
#
# Copyright (C) 2025 The Android Open Source Project
# Copyright (C) 2025 RennsAndroidKitchen device-tree generator
#
# SPDX-License-Identifier: Apache-2.0
#
```

The {c['brand']} {c['model']} (codename: _{c['device']}_) is a device powered
by the {c['platform']} ({c['arch']}) platform.

## Spec sheet

| Feature        | Value                          |
| -------------- | ------------------------------ |
| SoC            | {c['platform']}                |
| Architecture   | {c['arch']}                    |
| Header version | {c['header_version']}          |
| ABI            | {c['cpu_abi']}                 |

Generated from boot.img + vendor_boot.img by RennsAndroidKitchen.
"""


EXTRACT_FILES_TMPL = """\
#!/usr/bin/env bash
#
# Copyright (C) 2025 RennsAndroidKitchen device-tree generator
# SPDX-License-Identifier: Apache-2.0
#
set -e

DEVICE={device}
VENDOR={vendor}

# Pulls proprietary blobs listed in proprietary-files.txt from a connected
# device or a system dump. Requires the AOSP/OmniROM extract_utils helpers.
export DEVICE
export DEVICE_COMMON
export VENDOR

"${{ANDROID_ROOT:-../../..}}"/vendor/omni/build/tools/extract_utils.sh "$@"
"""

SETUP_MAKEFILES_TMPL = """\
#!/usr/bin/env bash
#
# Copyright (C) 2025 RennsAndroidKitchen device-tree generator
# SPDX-License-Identifier: Apache-2.0
#
set -e

DEVICE={device}
VENDOR={vendor}

"${{ANDROID_ROOT:-../../..}}"/vendor/omni/build/tools/setup_makefiles.sh "$@"
"""


# emit
def emit(facts: dict, out_root: Path, work: Path) -> Path:
    vendor = facts["manufacturer"].lower().replace(" ", "_")
    device = facts["device"]
    codename = facts["codename"]


    dt_root = out_root / f"devicetree_{codename}"
    recovery_root = dt_root / "recovery"
    ddir = recovery_root / "device" / vendor / device
    ddir.mkdir(parents=True, exist_ok=True)

    binfo, vinfo = facts["boot"], facts["vendor_boot"]
    cmd_b = binfo.get("hdr_cmdline", binfo.get("CMDLINE", "")).strip()
    cmd_v = vinfo.get("hdr_cmdline", vinfo.get("CMDLINE", "")).strip()
    cmdline = " ".join(x for x in (cmd_b, cmd_v) if x)

    c = {
        "brand": facts["brand"], "model": facts["model"], "device": device,
        "vendor": vendor, "manufacturer": facts["manufacturer"],
        "arch": facts["arch"],
        "cpu_abi": "arm64-v8a" if facts["arch"] == "arm64" else "armeabi-v7a",
        "cpu_variant": facts["cpu_variant"],
        "platform": facts["platform"],
        "bootloader_board": facts["bootloader_board"],
        "kernel_cmdline": cmdline,
        "header_version": binfo.get("HEADER_VER", "4"),
        "kernel_base": "0x%08x" % DEFAULT_KERNEL_BASE,
        "ramdisk_offset": "0x%08x" % DEFAULT_RAMDISK_OFFSET,
        "tags_offset": "0x%08x" % DEFAULT_TAGS_OFFSET,
        "pagesize": binfo.get("PAGESIZE", "4096"),
        "kernel_gz": binfo.get("KERNEL_FMT", "") in ("gzip", "gz"),
        "boot_size": str(os.path.getsize(facts["_boot_path"]))
        if facts.get("_boot_path") and os.path.isfile(facts["_boot_path"])
        else str(os.path.getsize(facts["_vendor_boot_path"])),
        "vendor_boot_size": str(os.path.getsize(facts["_vendor_boot_path"])),
        "api_level": facts["api_level"] or "30",
        "os_version": binfo.get("OS_VERSION", ""),
        "os_patch": binfo.get("OS_PATCH_LEVEL", ""),
        "build_desc": facts["build_desc"],
        "fingerprint": facts["fingerprint"],
        "ab_partitions": facts["ab_partitions"],
        "dyn_partitions": facts["dyn_partitions"],
        "system_fstype": facts.get("system_fstype", "ext4"),
        "userdata_fstype": facts.get("userdata_fstype", "f2fs"),
        "vendor_security_patch": facts.get("vendor_security_patch", ""),
        "screen_density": facts.get("screen_density", ""),
        "recovery_as_boot": facts.get("recovery_as_boot", False),
    }

    (ddir / "BoardConfig.mk").write_text(board_config(c), encoding="utf-8")
    (ddir / "BoardConfigCommon.mk").write_text(board_config_common(c), encoding="utf-8")
    (ddir / "device.mk").write_text(device_mk(c), encoding="utf-8")
    (ddir / f"omni_{device}.mk").write_text(omni_mk(c), encoding="utf-8")
    (ddir / "Android.mk").write_text(android_mk(c), encoding="utf-8")
    (ddir / "Android.bp").write_text(android_bp(c), encoding="utf-8")
    (ddir / "AndroidProducts.mk").write_text(android_products_mk(c), encoding="utf-8")
    (ddir / "vendorsetup.sh").write_text(vendorsetup_sh(c), encoding="utf-8")
    (ddir / "system.prop").write_text(system_prop(c), encoding="utf-8")

    if facts.get("fstab_text"):
        (ddir / "recovery.fstab").write_text(
            to_twrp_fstab(facts["fstab_text"]), encoding="utf-8")

    (ddir / "README.md").write_text(readme_md(c), encoding="utf-8")
    (ddir / "extract-files.sh").write_text(EXTRACT_FILES_TMPL.format(**c), encoding="utf-8")
    (ddir / "setup-makefiles.sh").write_text(SETUP_MAKEFILES_TMPL.format(**c), encoding="utf-8")

    # prebuilt kernel + dtb
    pdir = ddir / "prebuilt"
    pdir.mkdir(exist_ok=True)
    if (work / "boot" / "kernel").is_file():
        shutil.copy2(work / "boot" / "kernel", pdir / "kernel")
    if facts.get("_dtb_path"):
        shutil.copy2(facts["_dtb_path"], pdir / "dtb.img")

    # recovery/root: copy the device's init.recovery.*.rc and helper rc files.
    rd = facts.get("_rd")
    if rd:
        rroot = ddir / "recovery" / "root"
        rroot.mkdir(parents=True, exist_ok=True)
        rd = Path(rd)
        seen = set()
        # top-level recovery rc files
        for p in list(rd.glob("init.recovery.*.rc")) + list(rd.glob("*plpath*.rc")):
            if p.is_file():
                shutil.copy2(p, rroot / p.name)
                seen.add(p.name)
                # also emit a copy named after the real SoC platform if the
                # device used a marketing alias (mt8781 -> mt6789).
                m = re.match(r"init\.recovery\.(.+)\.rc", p.name)
                if m and m.group(1) != c["platform"] and m.group(1) not in (
                        "usb", "logd", "service", "ldconfig", "hlthchrg"):
                    alias = rroot / f"init.recovery.{c['platform']}.rc"
                    if not alias.exists():
                        shutil.copy2(p, alias)
        # snapuserd.rc may live deeper (system/etc/init)
        for p in rd.rglob("snapuserd.rc"):
            if p.is_file() and "snapuserd.rc" not in seen:
                shutil.copy2(p, rroot / "snapuserd.rc")
                seen.add("snapuserd.rc")
                break

    (ddir / "_generated.txt").write_text(
        "\n".join(f"{k}: {v}" for k, v in {
            "device": device, "codename": codename, "vendor": vendor,
            "brand": facts["brand"], "model": facts["model"],
            "product_name": facts["product_name"], "arch": facts["arch"],
            "platform": facts["platform"], "cpu_variant": facts["cpu_variant"],
            "bootloader_board": facts["bootloader_board"],
            "header_version": c["header_version"],
            "ab_partitions": ",".join(facts["ab_partitions"]),
        }.items()) + "\n", encoding="utf-8")


    chain = str(ddir.relative_to(recovery_root)).replace("\\", "/")
    (recovery_root / ".rak_dtgen.json").write_text(
        json.dumps({"collapse_to": chain, "codename": codename,
                    "kind": "recovery"}, indent=2) + "\n", encoding="utf-8")

    print("device tree generated:", dt_root)
    print("  device tree dir:", ddir.relative_to(dt_root))
    return dt_root


def generate_device_tree(vendor_boot: Path, vendor_dir: Path, out_root: Path,
                         boot: Path | None = None, boot_unpack: Path | None = None,
                         vendor_boot_unpack: Path | None = None) -> Path:

    if not is_valid_vendor(vendor_dir):
        raise ValueError(
            "%s is not a valid vendor partition "
            "(missing etc/selinux/vendor_file_contexts)" % vendor_dir)

    mb = mbt.find_magiskboot(None)
    work = out_root / "_dtgen_work"
    vendor_boot = Path(vendor_boot).resolve()
    boot = Path(boot).resolve() if boot else None
    try:
        facts = collect(str(mb), boot, vendor_boot, work, vendor_dir=Path(vendor_dir),
                        boot_unpack=boot_unpack, vendor_boot_unpack=vendor_boot_unpack)
        facts["_boot_path"] = str(boot) if boot else ""
        facts["_vendor_boot_path"] = str(vendor_boot)

        # Fail-safe: a device tree with a fake identity is worse than none.
        if not facts.get("device") or not facts.get("codename"):
            raise ValueError(
                "could not read device identity from vendor/build.prop — refusing "
                "to generate a device tree with placeholder names")
        return emit(facts, out_root, work)
    finally:


        def _rm_readonly(func, path, exc_info):
            os.chmod(path, stat.S_IWRITE)
            func(path)
        if work.is_dir():
            shutil.rmtree(work, onerror=_rm_readonly)


# input discovery (the "cek folder sebelah" logic)
BOOT_UNPACK_MARKER = ".rak_boot"  # core/boot_image.py drops this in <name>/


def boot_unpack_root(path: Path) -> Path | None:

    p = Path(path)
    if p.is_file():
        p = p.parent
    for cand in (p, *p.parents):
        if (cand / BOOT_UNPACK_MARKER).is_dir():
            return cand
    return None


def is_valid_vendor(path: Path) -> bool:

    return (Path(path) / "etc" / "selinux" / "vendor_file_contexts").is_file()


def _find_vendor_near(folder: Path) -> Path | None:

    folder = Path(folder)
    if not folder.is_dir():
        return None
    for name in ("vendor", "vendor_a", "vendor_b"):
        for cand in (folder / name, folder / name / name):
            if cand.is_dir() and is_valid_vendor(cand):
                return cand
    for child in folder.iterdir():
        if (child.is_dir() and child.name.lower().startswith("vendor")
                and child.name.lower() != "vendor_boot"
                and is_valid_vendor(child)):
            return child
    return None


def inputs_for_boot_unpack(clicked_path: Path) -> "Inputs | None":

    unpack = boot_unpack_root(clicked_path)
    if unpack is None:
        return None
    source_img = unpack / BOOT_UNPACK_MARKER / "source.img"
    if not source_img.is_file():
        return None

    base = unpack.parent
    vendor = _find_vendor_near(base) or _find_vendor_near(unpack)
    if vendor is None:
        return None

    boot = None
    boot_unpack = None
    for cand in (base / "boot.img", *base.glob("*.img")):
        low = cand.name.lower()
        if cand.is_file() and low.endswith("boot.img") and "vendor" not in low and "init" not in low:
            boot = cand
            # if that boot.img was itself already unpacked beside it, reuse
            # its already-extracted kernel/header instead of unpacking again.
            cand_unpack = base / cand.stem
            if (cand_unpack / BOOT_UNPACK_MARKER).is_dir():
                boot_unpack = cand_unpack
            break

    return Inputs(vendor_boot=source_img, vendor=vendor, boot=boot,
                  vendor_boot_unpack=unpack, boot_unpack=boot_unpack)


class Inputs:
    __slots__ = ("vendor_boot", "vendor", "boot", "vendor_boot_unpack", "boot_unpack")

    def __init__(self, vendor_boot, vendor, boot=None, vendor_boot_unpack=None,
                boot_unpack=None):
        self.vendor_boot = vendor_boot
        self.vendor = vendor
        self.boot = boot
        self.vendor_boot_unpack = vendor_boot_unpack
        self.boot_unpack = boot_unpack

    def __repr__(self):
        return (f"Inputs(vendor_boot={self.vendor_boot}, vendor={self.vendor}, "
                f"boot={self.boot}, vendor_boot_unpack={self.vendor_boot_unpack}, "
                f"boot_unpack={self.boot_unpack})")


def find_inputs(folder: Path) -> "Inputs | None":

    folder = Path(folder)
    if not folder.is_dir():
        return None

    # vendor: a sibling dir literally named vendor / vendor_a / vendor_b that
    # passes the file-contexts validation (never system — it may be GSI).
    vendor = None
    for name in ("vendor", "vendor_a", "vendor_b"):
        for cand in (folder / name, folder / name / name):
            if cand.is_dir() and is_valid_vendor(cand):
                vendor = cand
                break
        if vendor:
            break
    if vendor is None:
        for child in folder.iterdir():
            if (child.is_dir() and child.name.lower().startswith("vendor")
                    and child.name.lower() != "vendor_boot"
                    and is_valid_vendor(child)):
                vendor = child
                break
    if vendor is None:
        return None

    # vendor_boot.img (required), boot.img (optional)
    def _find_img(*names):
        for n in names:
            p = folder / n
            if p.is_file():
                return p
        # any *vendor_boot*.img / *boot*.img variant
        for p in folder.glob("*.img"):
            low = p.name.lower()
            if all(part in low for part in names[0].lower().replace(".img", "").split("_")):
                return p
        return None

    vendor_boot = _find_img("vendor_boot.img")
    if vendor_boot is None:
        # fall back to any *vendor_boot*.img
        for p in folder.glob("*.img"):
            if "vendor_boot" in p.name.lower():
                vendor_boot = p
                break
    if vendor_boot is None:
        return None

    boot = folder / "boot.img"
    if not boot.is_file():
        boot = None
        for p in folder.glob("*.img"):
            low = p.name.lower()
            if low.endswith("boot.img") and "vendor" not in low and "init" not in low:
                boot = p
                break

    return Inputs(vendor_boot=vendor_boot, vendor=vendor, boot=boot)


def main(argv=None):
    p = argparse.ArgumentParser(description="generate a device-tree build folder")
    p.add_argument("folder", help="working folder with boot/vendor_boot/vendor")
    p.add_argument("-o", "--output", help="output root (default: same folder)")
    args = p.parse_args(argv)

    folder = Path(args.folder).resolve()
    inputs = find_inputs(folder)
    if inputs is None:
        print("error: no valid vendor + vendor_boot found in", folder, file=sys.stderr)
        return 1
    out_root = Path(args.output).resolve() if args.output else folder
    try:
        dt = generate_device_tree(inputs.vendor_boot, inputs.vendor, out_root,
                                  boot=inputs.boot)
    except ValueError as e:
        print("error:", e, file=sys.stderr)
        return 1
    print("device tree:", dt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
