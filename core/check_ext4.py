# SPDX-License-Identifier: MPL-2.0
import os


def check_ext4_magic(img_path: str) -> str:
    img_path = os.path.abspath(os.path.normpath(img_path))
    if not os.path.isfile(img_path):
        return ""
    try:
        from core.native_backend import ext_inspect

        info = ext_inspect(img_path)
        fs = str(info.get("fs_type", ""))
        if fs.startswith("EXT4"):
            return "EXT4"
        return fs if fs in ("EXT2", "EXT3", "EXT4") else ""
    except Exception:
        return ""
