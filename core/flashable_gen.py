# SPDX-License-Identifier: MPL-2.0
from dataclasses import dataclass, field


@dataclass
class PartSpec:
    role: str            # 'dynamic' | 'both_slot'
    name: str            # nama partisi
    arc: str             # path di dalam zip
    size: int = 0        # raw byte size (dynamic wajib)
    zstd: bool = False
    src: str = ""        # path file SUMBER di disk (builder baca dari sini)
    zstd_level: int = 19 # level kompresi zstd


@dataclass
class FlashableConfig:
    parts: list = field(default_factory=list)
    device_code: str = ""        # ro.product.name; kalau kosong & getprop off -> no checkDevice
    getprop_check: bool = False  # aktifin checkDevice security
    banner_title: str = "Flashable ROM"
    banner_device: str = ""
    banner_version: str = ""
    use_zstd: bool = False       # ada minimal 1 partisi zstd -> bawa helper + binary


_BANNER = """\
ui_print " "
ui_print "============================================"
ui_print "{title}"
ui_print "Device : {device}"
ui_print "Version: {version}"
ui_print "============================================"
"""

_HEADER = """\
#!/sbin/sh

OUTFD=/proc/self/fd/$2
ZIPFILE="$3"

ui_print() {
    printf 'ui_print %s\\nui_print\\n' "$1" >>"$OUTFD"
}

# Function to extract and flash a file to a partition
flash_partition() {
    src="$1"
    dest="$2"
    msg="$3"

    if [ "$#" -lt 3 ]; then
        partition_name=$(echo "$dest" | cut -d '/' -f 5)
        ui_print "- Flashing partition $partition_name"
    elif [ -n "$msg" ]; then
        ui_print "$msg"
    fi

    unzip -p "$ZIPFILE" "$src" >"$dest"
}
"""

_ZSTD_HELPER = """\

# Function to extract and flash a zstd compressed file to a partition
flash_partition_zstd() {
    src="$1"
    dest="$2"
    partition_name=$(echo "$dest" | cut -d '/' -f 5)

    ui_print "- Flashing partition $partition_name"
    unzip -p "$ZIPFILE" "$src" | /tmp/META-INF/zstd -c -d >"$dest"
}
"""

_FIRMWARE_HELPER = """\

# Function to flash firmware to both slots while logging once
flash_firmware_both_slots() {
    img_file="$1"
    base_name="$2"

    flash_partition "$img_file" "/dev/block/by-name/${base_name}_a" "- Flashing partition ${base_name} to both slots"
    flash_partition "$img_file" "/dev/block/by-name/${base_name}_b" ""
}
"""

_GETPROP_HELPER = """\

getVolumeKey() {
    ui_print "- Listening to volume keys. Press [+] for 'Yes' and [-] for 'No'"
    keyInfo=true
    while $keyInfo; do
        keyInfo=$(getevent -qlc 1 | grep KEY_VOLUME)
        [ -z "$keyInfo" ] && continue
        isUpKey=$(printf '%s\\n' "$keyInfo" | grep KEY_VOLUMEUP)
        [ -n "$isUpKey" ] && return 0 || return 1
        break
    done
}

checkDevice() {
    myDevice=$(getprop ro.product.name)
    romDevice="__DEVICE_CODE__"
    if [ "$myDevice" != "$romDevice" ]; then
        ui_print "- Device code verification failed. Please double-check if this package matches your device model."
        ui_print "- Flashing the wrong package may cause bricking, and you will bear the consequences. Do you want to continue flashing?"
        if ! getVolumeKey; then
            ui_print "- You chose to abort flashing."
            exit 1
        else
            ui_print "- You chose to continue flashing."
        fi
    fi
}
"""

_COMMON_HELPERS = """\

checkExit() {
    status=$?
    if [ "$status" -ne 0 ]; then
        ui_print "Error: Exit status $status detected. There may be an issue with your super partition. Flash the stock super.img first, then retry. Exiting..."
        exit 1
    fi
}

# Function to handle logical partition operations
manage_logical_partition() {
    operation="$1"
    partition="$2"
    size="$3"
    slot="$4"

    case "$operation" in
        clear)
            lptools unmap "$partition$slot" && lptools remove "$partition$slot"
            ;;
        create)
            lptools create "$partition$slot" "$size"
            ;;
        map)
            lptools map "$partition$slot" && checkExit
            ;;
        unmap_map)
            lptools unmap "$partition$slot" && lptools map "$partition$slot"
            ;;
    esac
}

create_partitions_for_slot() {
    target_slot="$1"
    other_slot="$2"
    shift 2

    for spec in "$@"; do
        partition="${spec%%:*}"
        size="${spec#*:}"
        manage_logical_partition "create" "$partition" "$size" "$target_slot"
        manage_logical_partition "create" "$partition" "0" "$other_slot"
    done
}

process_partitions_for_slots() {
    operation="$1"
    shift

    for partition in "$@"; do
        manage_logical_partition "$operation" "$partition" "" "_a"
        manage_logical_partition "$operation" "$partition" "" "_b"
    done
}
"""


def _ind(items, per_line_prefix='        "', suffix='"', sep=" \\\n"):

    return sep.join(f"{per_line_prefix}{x}{suffix}" for x in items)


def _unmount_helper(logical):

    names = []
    for p in logical:
        names.append("system_root" if p.name == "system" else p.name)
    if not names:
        return ""
    joined = " ".join(names)
    return (
        "\nunmountPartitions() {\n"
        f"    umount {joined} 2>/dev/null\n"
        "}\n"
    )


def generate(cfg: FlashableConfig) -> str:


    dynamic   = [p for p in cfg.parts if p.role == "dynamic"]
    both_slot = [p for p in cfg.parts if p.role == "both_slot"]


    fw_parts  = [p for p in both_slot if p.arc.startswith("firmware/")]
    sys_parts = [p for p in both_slot if not p.arc.startswith("firmware/")]

    has_zstd = cfg.use_zstd or any(p.zstd for p in cfg.parts)
    do_getprop = cfg.getprop_check and bool(cfg.device_code)

    out = [_HEADER]
    if has_zstd:
        out.append(_ZSTD_HELPER)
    if both_slot:
        out.append(_FIRMWARE_HELPER)
    if do_getprop:
        out.append(_GETPROP_HELPER.replace("__DEVICE_CODE__", cfg.device_code))
    if dynamic:
        out.append(_unmount_helper(dynamic))
        out.append(_COMMON_HELPERS)

    body = []

    if has_zstd:
        body.append('unzip "$ZIPFILE" META-INF/zstd -d /tmp')
        body.append("chmod -R 0755 /tmp")
        body.append("")

    body.append(_BANNER.format(
        title=cfg.banner_title or "Flashable ROM",
        device=cfg.banner_device or "-",
        version=cfg.banner_version or "-",
    ).rstrip())

    if do_getprop:
        body.append("")
        body.append("checkDevice")

    # Unmount partitions (fungsi, dipanggil sekali — gaya kiblat).
    if dynamic:
        body.append("")
        body.append("unmountPartitions")

    # FIRMWARE (both slots, physical by-name)
    if fw_parts:
        body.append("")
        body.append('ui_print " "')
        body.append('ui_print "Patching firmware to both slot..."')
        for p in fw_parts:
            body.append(f'flash_firmware_both_slots "{p.arc}" "{p.name}"')

    # DYNAMIC (super) — setup slot + clear/create/map
    if dynamic:
        names = [p.name for p in dynamic]
        body.append("")
        body.append('ui_print " "')
        body.append("SLOT=$(getprop ro.boot.slot_suffix)")
        body.append('ui_print "Checking boot slot... ${SLOT}"')
        body.append("")
        body.append("lptools clear-cow")
        body.append("checkExit")
        body.append("")
        body.append('# Clear existing partitions')
        body.append('process_partitions_for_slots "clear" \\')
        body.append(_ind(names))
        body.append("")
        body.append('# Determine target / other slot')
        body.append('case "$SLOT" in')
        body.append('    "_a") OTHER_SLOT="_b" ;;')
        body.append('    "_b") OTHER_SLOT="_a" ;;')
        body.append('    *) ui_print "- Unknown boot slot: $SLOT"; exit 1 ;;')
        body.append('esac')
        body.append("")
        body.append('# Create new partitions (size = exact raw bytes)')
        body.append('create_partitions_for_slot "$SLOT" "$OTHER_SLOT" \\')
        body.append(_ind([f"{p.name}:{int(p.size)}" for p in dynamic]))
        body.append("")
        body.append('# Map all partitions')
        body.append('process_partitions_for_slots "map" \\')
        body.append(_ind(names))

    # ── "Patching system..." — boot/dtbo/vbmeta (both slots) + dynamic flash ─
    if sys_parts or dynamic:
        body.append("")
        body.append('ui_print " "')
        body.append('ui_print "Patching system..."')
        for p in sys_parts:
            body.append(f'flash_firmware_both_slots "{p.arc}" "{p.name}"')
        # dynamic: flash CUMA ke current $SLOT (mapper) — persis kiblat.
        for p in dynamic:
            if p.zstd:
                body.append(f'flash_partition_zstd "{p.arc}" "/dev/block/mapper/{p.name}$SLOT"')
            else:
                body.append(f'flash_partition "{p.arc}" "/dev/block/mapper/{p.name}$SLOT"')

    # Final remap (super)
    if dynamic:
        names = [p.name for p in dynamic]
        body.append("")
        body.append('# Final unmapping and mapping to ensure proper mounting')
        body.append('process_partitions_for_slots "unmap_map" \\')
        body.append(_ind(names))

    body.append("")
    body.append("exit 0")

    return "".join(out) + "\n" + "\n".join(body) + "\n"
