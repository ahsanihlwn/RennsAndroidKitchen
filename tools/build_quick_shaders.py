# SPDX-License-Identifier: MPL-2.0
"""Bake Qt Quick shaders for Direct3D, OpenGL, Vulkan and Metal."""
from pathlib import Path
import shutil
import subprocess
import sys


def _find_qsb(pyside_dir: Path) -> Path:
    name = "qsb.exe" if sys.platform == "win32" else "qsb"
    candidates = [
        pyside_dir / name,
        pyside_dir / "Qt" / "libexec" / name,
        pyside_dir / "Qt" / "bin" / name,
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    found = shutil.which(name)
    if found:
        return Path(found)
    return candidates[0]


def build_shaders(*, force=False):
    import PySide6
    root=Path(__file__).resolve().parents[1]
    qsb=_find_qsb(Path(PySide6.__path__[0]))
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
