# SPDX-License-Identifier: MPL-2.0
"""Bake Qt Quick shaders for Direct3D, OpenGL, Vulkan and Metal."""
from pathlib import Path
import subprocess
import sys


def build_shaders(*, force=False):
    import PySide6
    root=Path(__file__).resolve().parents[1]
    qsb=Path(PySide6.__path__[0])/('qsb.exe' if sys.platform=='win32' else 'qsb')
    sources=sorted(path for path in (root/'ui/shaders').iterdir() if path.suffix in ('.frag','.vert'))
    for source in sources:
        target=source.with_suffix(source.suffix+'.qsb')
        if not force and target.exists() and target.stat().st_mtime>=source.stat().st_mtime:
            continue
        temporary=target.with_suffix(target.suffix+'.tmp')
        try:
            subprocess.run([str(qsb),'--qt6','-o',str(temporary),str(source)],
                           check=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        print(f'Shader baked: {target.relative_to(root)}',flush=True)


if __name__=='__main__':
    build_shaders(force='--force' in sys.argv)
