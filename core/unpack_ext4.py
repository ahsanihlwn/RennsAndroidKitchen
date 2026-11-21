# SPDX-License-Identifier: MPL-2.0
import os


def main(img_path: str):
    img_path = os.path.abspath(os.path.normpath(img_path))

    if not os.path.isfile(img_path):
        return False, "Image not found"

    try:
        from core.native_backend import ext_unpack

        extracted_folder = ext_unpack(img_path)
        if not os.path.isdir(extracted_folder):
            return False, "Unpack finished but output folder missing"
        return True, extracted_folder
    except Exception as e:
        return False, str(e)


def ext4_x(img_path: str, out_dir: str = None):
    return main(img_path)
