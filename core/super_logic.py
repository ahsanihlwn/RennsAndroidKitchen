# SPDX-License-Identifier: MPL-2.0
import json
import os
from types import SimpleNamespace

from core.app_paths import PROJECT_DATA_DIR
from core.command_context import OperationError

print("[CORE STAGE] SUPER SPARSE LOGIC CORE")

PROJECT_DIR = PROJECT_DATA_DIR
SETTINGS_FILE = os.path.join(PROJECT_DIR, "project_settings.json")


def is_super_norm_enabled(settings_file: str | None = None):
    path = settings_file or SETTINGS_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        return bool(cfg.get("super", {}).get("norm", True))
    except Exception as e:
        print("[SUPER] settings read failed → default norm=true:", e)
        return True


def normalize_partition_names(partitions):
    """Return normalized unique output names (strip *_a / *_b) preserving order."""
    seen = set()
    normalized = []
    for name in partitions:
        if len(name) > 2 and (name.endswith("_a") or name.endswith("_b")):
            base = name[:-2]
        else:
            base = name
        if base and base not in seen:
            seen.add(base)
            normalized.append(base)
    return normalized


class SuperInfo:
    def __init__(self):
        self.raw = ""
        self.partitions = []
        self.groups = set()
        self.active_slot = None
        self.slots = set()

    def add_partition(self, name):
        self.partitions.append(name)
        if name.endswith("_a"):
            self.slots.add("a")
        elif name.endswith("_b"):
            self.slots.add("b")


def read_super_info(img_path):
    from core.native_backend import super_info

    info = SuperInfo()
    try:
        data = super_info(img_path)
    except Exception as e:
        print("SUPER INFO ERROR:", e)
        return info

    info.raw = data.get("raw", "")
    for part in data.get("partitions", []):
        info.add_partition(part)
    for grp in data.get("groups", []):
        info.groups.add(grp)
    info.active_slot = data.get("active_slot")
    return info


def extract_partition(img_path, part_name, out_dir):
    from core.native_backend import super_extract

    try:
        res = super_extract(img_path, out_dir, partitions=[part_name], normalize=False)
        return SimpleNamespace(returncode=0, stdout=json.dumps(res), stderr="")
    except Exception as e:
        return SimpleNamespace(returncode=1, stdout="", stderr=str(e))


def extract_all(img_path, out_dir):
    from core.native_backend import super_extract

    norm = is_super_norm_enabled()

    print("[SUPER] extract_all called")
    print("[SUPER] IMG  :", img_path)
    print("[SUPER] OUT  :", out_dir)
    print("[SUPER] norm :", norm)
    if norm:
        print("[SUPER] policy → normalize enabled")
    else:
        print("[SUPER] policy → normalize disabled")

    try:
        res = super_extract(img_path, out_dir, normalize=norm)
        print("[SUPER] OUTPUTS:", res.get("outputs", []))
        return True, out_dir
    except Exception as e:
        return False, str(e)


def convert_to_raw(img_path, out_dir):
    from core.native_backend import super_unsparse

    print("[SUPER] UNSPARSE START")

    if not os.path.isfile(img_path):
        return False, "File tidak ditemukan"

    if not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    try:
        out_path = super_unsparse(img_path, out_dir)
        print("[SUPER] UNSPARSE OK :", out_path)
        return True, out_path
    except OperationError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


REQUIRED_SUPER_PARTITIONS = ("system", "system_ext", "product")
DEFAULT_METADATA_SIZE = 65536
DEFAULT_BLOCK_SIZE = 4096
DEFAULT_ALIGNMENT = 1024 * 1024


def _align_up(value: int, align: int) -> int:
    if align <= 1:
        return value
    return ((value + align - 1) // align) * align


def strip_image_partition_name(path_or_name: str) -> tuple[str, str | None]:
    """Return `(base_partition_name, slot)` where slot is `'a'`, `'b'`, or `None`."""
    name = os.path.basename(str(path_or_name or "").strip())
    stem, _ext = os.path.splitext(name)
    low = stem.lower()
    if len(low) > 2 and low.endswith("_a"):
        return stem[:-2], "a"
    if len(low) > 2 and low.endswith("_b"):
        return stem[:-2], "b"
    return stem, None


def partition_raw_size(img_path: str) -> int:
    """Return the logical (unsparsed) byte size of a raw or sparse partition image."""
    try:
        from core.native_backend import image_logical_size
        return image_logical_size(img_path)
    except Exception:
        from core import flashable_size
        kind = flashable_size.classify(img_path)
        return int(flashable_size.raw_image_size(img_path, kind))


def _prepare_super_spec(
    partitions,
    *,
    super_type: str = "a_only",
    group_name: str = "qti_dynamic_partitions",
    attrib: str = "readonly",
    metadata_size: int = DEFAULT_METADATA_SIZE,
    block_size: int = DEFAULT_BLOCK_SIZE,
    alignment: int = DEFAULT_ALIGNMENT,
):
    stype = str(super_type or "a_only").strip().lower()
    if stype in ("1", "a", "a_only", "a-only"):
        stype = "a_only"
    elif stype in ("2", "vab", "virtual_ab", "virtual-ab"):
        stype = "virtual_ab"
    elif stype in ("3", "ab", "a/b", "a_b"):
        stype = "ab"
    else:
        raise ValueError(f"Unsupported super_type: {super_type}")

    attr = "none" if str(attrib or "").strip().lower() == "none" else "readonly"
    grp = (group_name or "qti_dynamic_partitions").strip() or "qti_dynamic_partitions"

    # Group selected images by normalized base partition name.
    grouped: dict[str, dict] = {}
    order: list[str] = []
    for entry in partitions:
        if isinstance(entry, str):
            img_path = entry
            base, slot = strip_image_partition_name(img_path)
            raw_size = partition_raw_size(img_path)
            part_attr = attr
        else:
            img_path = str(entry.get("path") or entry.get("image") or "")
            raw_name = str(entry.get("name") or "")
            if raw_name:
                base, slot = strip_image_partition_name(raw_name)
            else:
                base, slot = strip_image_partition_name(img_path)
            if entry.get("slot") in ("a", "b"):
                slot = entry["slot"]
            raw_size = int(entry.get("size") or 0) or partition_raw_size(img_path)
            part_attr = str(entry.get("attr") or attr)
        if not base or not img_path:
            continue
        key = base.lower()
        if key not in grouped:
            grouped[key] = {"base": base.lower(), "a": None, "b": None, "unsuffixed": None, "attr": part_attr}
            order.append(key)
        bucket = grouped[key]
        info = {"path": img_path, "size": raw_size}
        if slot == "a":
            bucket["a"] = info
        elif slot == "b":
            bucket["b"] = info
        else:
            bucket["unsuffixed"] = info

    metadata_slots = 2 if stype == "a_only" else 3
    virtual_ab = stype == "virtual_ab"

    return stype, grp, metadata_slots, virtual_ab, [grouped[k] for k in order], metadata_size, block_size, alignment


def calculate_min_super_size(
    partitions,
    *,
    super_type: str = "a_only",
    metadata_size: int = DEFAULT_METADATA_SIZE,
    block_size: int = DEFAULT_BLOCK_SIZE,
    alignment: int = DEFAULT_ALIGNMENT,
) -> int:
    stype, _grp, metadata_slots, _vab, buckets, meta_sz, blk_sz, align = _prepare_super_spec(
        partitions,
        super_type=super_type,
        metadata_size=metadata_size,
        block_size=block_size,
        alignment=alignment,
    )
    total_reserved = 4096 + 8192 + (meta_sz * metadata_slots * 2)
    min_bytes = _align_up(total_reserved, align)
    for b in buckets:
        if stype == "a_only":
            primary = b["a"] or b["unsuffixed"] or b["b"]
            if primary:
                min_bytes += _align_up(_align_up(primary["size"], blk_sz), align)
        else:
            slot_a = b["a"] or b["unsuffixed"]
            slot_b = b["b"]
            if slot_a:
                min_bytes += _align_up(_align_up(slot_a["size"], blk_sz), align)
            if slot_b:
                min_bytes += _align_up(_align_up(slot_b["size"], blk_sz), align)
    return _align_up(min_bytes, blk_sz)


def suggest_super_size(min_bytes: int) -> int:
    """Round minimum required bytes up to a clean 256 MiB boundary (or 16 MiB for tiny fixtures)."""
    if min_bytes <= 0:
        return 256 * 1024 * 1024
    if min_bytes <= 64 * 1024 * 1024:
        return _align_up(min_bytes, 16 * 1024 * 1024)
    quarter_gib = 256 * 1024 * 1024
    return _align_up(min_bytes, quarter_gib)


def pack_super(
    partitions,
    output_path: str,
    *,
    super_type: str = "a_only",
    group_name: str = "qti_dynamic_partitions",
    super_size: int = 0,
    attrib: str = "readonly",
    sparse: bool = False,
    delete_sources: bool = False,
    super_name: str = "super",
    metadata_size: int = DEFAULT_METADATA_SIZE,
    block_size: int = DEFAULT_BLOCK_SIZE,
    alignment: int = DEFAULT_ALIGNMENT,
    progress=None,
):
    """Build a raw or sparse Android `super.img` using the native Rust `lp_tools` engine."""
    from core.native_backend import super_make

    stype, grp, metadata_slots, virtual_ab, buckets, meta_sz, blk_sz, align = _prepare_super_spec(
        partitions,
        super_type=super_type,
        group_name=group_name,
        attrib=attrib,
        metadata_size=metadata_size,
        block_size=block_size,
        alignment=alignment,
    )
    if not buckets:
        return False, "No valid partition images selected."

    for b in buckets:
        for slot_key in ("a", "b", "unsuffixed"):
            item = b[slot_key]
            if item and not os.path.isfile(item["path"]):
                return False, f"Partition image not found: {item['path']}"

    min_size = calculate_min_super_size(
        partitions,
        super_type=stype,
        metadata_size=meta_sz,
        block_size=blk_sz,
        alignment=align,
    )
    target_size = int(super_size or 0)
    if target_size <= 0:
        target_size = suggest_super_size(min_size)
    else:
        target_size = _align_up(target_size, blk_sz)
    if target_size < min_size:
        return (
            False,
            f"Super size ({target_size} bytes) is smaller than minimum required ({min_size} bytes).",
        )

    groups_spec = []
    parts_spec = []
    source_files = []

    if stype == "a_only":
        groups_spec.append({"name": grp, "max_size": target_size})
        for b in buckets:
            primary = b["a"] or b["unsuffixed"] or b["b"]
            if not primary:
                continue
            source_files.append(primary["path"])
            parts_spec.append({
                "name": b["base"],
                "attr": b["attr"],
                "size": primary["size"],
                "group": grp,
                "image": primary["path"],
            })
    else:
        grp_a = f"{grp}_a"
        grp_b = f"{grp}_b"
        groups_spec.append({"name": grp_a, "max_size": target_size})
        groups_spec.append({"name": grp_b, "max_size": target_size})
        for b in buckets:
            slot_a = b["a"] or b["unsuffixed"]
            if slot_a:
                source_files.append(slot_a["path"])
                parts_spec.append({
                    "name": f"{b['base']}_a",
                    "attr": b["attr"],
                    "size": slot_a["size"],
                    "group": grp_a,
                    "image": slot_a["path"],
                })
            else:
                parts_spec.append({
                    "name": f"{b['base']}_a",
                    "attr": b["attr"],
                    "size": 0,
                    "group": grp_a,
                    "image": None,
                })
            slot_b = b["b"]
            if slot_b:
                source_files.append(slot_b["path"])
                parts_spec.append({
                    "name": f"{b['base']}_b",
                    "attr": b["attr"],
                    "size": slot_b["size"],
                    "group": grp_b,
                    "image": slot_b["path"],
                })
            else:
                parts_spec.append({
                    "name": f"{b['base']}_b",
                    "attr": b["attr"],
                    "size": 0,
                    "group": grp_b,
                    "image": None,
                })

    out_abs = os.path.abspath(output_path)
    tmp_out = out_abs + ".rak-super.tmp"
    try:
        res = super_make(
            tmp_out,
            target_size,
            groups_spec,
            parts_spec,
            metadata_size=meta_sz,
            metadata_slots=metadata_slots,
            block_size=blk_sz,
            alignment=align,
            super_name=super_name,
            sparse=bool(sparse),
            virtual_ab=virtual_ab,
            force_full_image=True,
            progress=progress,
        )
        if not os.path.isfile(tmp_out):
            return False, "Super image build produced no output file."
        os.replace(tmp_out, out_abs)
        res["output_path"] = out_abs
    except Exception as e:
        try:
            if os.path.exists(tmp_out):
                os.remove(tmp_out)
        except OSError:
            pass
        return False, str(e)

    if delete_sources and os.path.isfile(out_abs):
        out_norm = os.path.normcase(os.path.normpath(out_abs))
        for src in dict.fromkeys(source_files):
            if os.path.normcase(os.path.normpath(src)) == out_norm:
                continue
            try:
                if os.path.isfile(src):
                    os.remove(src)
            except OSError:
                pass

    return True, out_abs
