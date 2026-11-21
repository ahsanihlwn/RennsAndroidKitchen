# SPDX-License-Identifier: MPL-2.0
"""Build an owned tiny EXT image with the kitchen's built-in Rust make_ext4fs."""
from pathlib import Path
from core.native_backend import ext_make_fs


def build_fixture(root, *, contexts=None, name='native_mount.img'):
    root = Path(root)
    source = root / 'mount_source'
    (source / 'etc').mkdir(parents=True, exist_ok=True)
    (source / 'etc' / 'native.txt').write_bytes(b'Original mounted document\n')
    image = root / name
    ext_make_fs(
        image,
        source,
        size_bytes=16777216,
        mount_point='system',
        file_contexts=contexts,
    )
    if not image.is_file():
        raise RuntimeError('The fixture builder did not create an image')
    return image
