# SPDX-License-Identifier: MPL-2.0
"""Build an owned tiny EXT image with the kitchen's installed make_ext4fs."""
from pathlib import Path
import subprocess
from core.app_paths import BIN_DIR
from core.binary_handler import resolve_binary
from core.proc_util import CREATE_NO_WINDOW


def build_fixture(root,*,contexts=None,name='native_mount.img'):
    root=Path(root);source=root/'mount_source';(source/'etc').mkdir(parents=True,exist_ok=True)
    (source/'etc'/'native.txt').write_bytes(b'Original mounted document\n')
    image=root/name
    program=resolve_binary(BIN_DIR,'make_ext4fs.exe')
    if not program:raise FileNotFoundError('make_ext4fs is not installed')
    arguments=[program,'-l','16777216','-a','system']
    if contexts:arguments.extend(['-S',str(contexts)])
    result=subprocess.run([*arguments,str(image),str(source)],capture_output=True,creationflags=CREATE_NO_WINDOW,timeout=25)
    if result.returncode:raise RuntimeError(result.stderr.decode(errors='replace'))
    if not image.is_file():raise RuntimeError('The fixture builder did not create an image')
    return image
