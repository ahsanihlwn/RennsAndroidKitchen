# SPDX-License-Identifier: MPL-2.0
import os

# build.prop bisa nongol di beberapa lokasi tergantung partisi yang dibuka.
_BUILD_PROP_CANDIDATES = (
    "build.prop",
    "etc/build.prop",
    "vendor/build.prop",
    "vendor/etc/build.prop",
    "system/build.prop",
    "system/vendor/build.prop",
)

# Key prop yang kita pedulikan.
_NAME_KEYS = (
    "ro.product.name",
    "ro.product.vendor.name",
    "ro.product.device",
    "ro.product.vendor.device",
)
_ABILIST_KEYS = (
    "ro.product.cpu.abilist",
    "ro.product.cpu.abilist64",
    "ro.product.cpu.abi",
    "ro.vendor.product.cpu.abilist",
)


def _iter_build_props(img_path):

    from core import imgfs
    # Cari semua node bernama build.prop via walk (lebih andal dari guess path).
    try:
        fs, r = imgfs.open_reader(img_path)
    except Exception:
        return
    try:
        for n in r.walk():
            if n.is_dir:
                continue
            base = n.path.rsplit("/", 1)[-1]
            if base == "build.prop":
                try:
                    data = r.read_file(n.ino)
                    yield n.path, data.decode("utf-8", "replace")
                except Exception:
                    continue
    finally:
        try:
            r.f.close()
        except Exception:
            pass


def _parse_props(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def read_props(img_path):

    merged = {}
    for _path, text in _iter_build_props(img_path):
        for k, v in _parse_props(text).items():
            merged.setdefault(k, v)
    return merged


def probe(img_path):

    res = {"device_code": None, "abilist": None, "is_64bit": False, "ok": False}
    try:
        props = read_props(img_path)
    except Exception:
        return res
    if not props:
        return res
    res["ok"] = True

    for k in _NAME_KEYS:
        if props.get(k):
            res["device_code"] = props[k]
            break

    abilist = None
    for k in _ABILIST_KEYS:
        if props.get(k):
            abilist = props[k]
            break
    res["abilist"] = abilist
    if abilist:
        low = abilist.lower()
        res["is_64bit"] = ("arm64-v8a" in low) or ("x86_64" in low) or ("64" in k)

    return res
