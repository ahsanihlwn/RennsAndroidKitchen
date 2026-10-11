# SPDX-License-Identifier: MPL-2.0
import os, sys, shutil, subprocess, filecmp
from core.app_paths import APP_ROOT, ASSET_CACHE_DIR, CACHE_DIR, ensure_app_layout
from core.proc_util import CREATE_NO_WINDOW
print("[CORE STAGE] CACHE INIT")

CACHE_VERSION = "v169"   # refresh icon assets in the isolated asset cache
CACHE_MARK = os.path.join(ASSET_CACHE_DIR, f".ready_{CACHE_VERSION}")

def hide_folder(path):
    try:
        subprocess.run(
            ["attrib", "+H", path],
            check=False, capture_output=True, creationflags=CREATE_NO_WINDOW
        )
    except Exception:
        pass


REQUIRED_DIRS = (
    os.path.join(ASSET_CACHE_DIR, "icons"),
    os.path.join(ASSET_CACHE_DIR, "textures"),
)


def _migrate_legacy_asset_cache():

    for name in ("icons", "textures"):
        source = os.path.join(CACHE_DIR, name)
        target = os.path.join(ASSET_CACHE_DIR, name)
        if not os.path.isdir(source):
            continue
        if not os.path.exists(target):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(source, target)
            continue
        for root, _dirs, files in os.walk(source):
            relative = os.path.relpath(root, source)
            target_dir = target if relative == "." else os.path.join(target, relative)
            os.makedirs(target_dir, exist_ok=True)
            for filename in files:
                old_file = os.path.join(root, filename)
                new_file = os.path.join(target_dir, filename)
                if not os.path.exists(new_file):
                    shutil.move(old_file, new_file)
        shutil.rmtree(source, ignore_errors=True)


def cache_is_valid():
    if not os.path.exists(CACHE_MARK):
        return False

    for d in REQUIRED_DIRS:
        if not os.path.isdir(d):
            return False
        if not os.listdir(d):
            return False

    return True


def _asset_base():

    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        exe_dir = os.path.dirname(sys.executable)
        if os.path.isdir(os.path.join(exe_dir, "icons")):
            return exe_dir
    cand = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if os.path.isdir(os.path.join(cand, "icons")):
        return cand
    exe_dir = os.path.dirname(sys.executable)
    if os.path.isdir(os.path.join(exe_dir, "icons")):
        return exe_dir
    return cand


def _topup_cache():

    base = _asset_base()
    for sub in ("icons", "textures"):
        src = os.path.join(base, sub)
        dst = os.path.join(ASSET_CACHE_DIR, sub)
        if not os.path.isdir(src) or not os.path.isdir(dst):
            continue
        try:
            for fname in os.listdir(src):
                if fname == "__pycache__":
                    continue
                s = os.path.join(src, fname)
                if os.path.isdir(s):
                    continue
                if not fname.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico")):
                    continue
                cached = os.path.join(dst, fname)
                if not os.path.isfile(cached):
                    shutil.copy2(s, cached)
                    print(f"[CACHE] +{sub}/{fname}")
                elif not filecmp.cmp(s, cached, shallow=False):
                    shutil.copy2(s, cached)
                    print(f"[CACHE] updated {sub}/{fname}")
        except OSError as exc:
            print(f"[CACHE] top-up {sub} dilewati: {exc}")


def _invalidate_icon_catalog():
    handler = sys.modules.get("icons.handler")
    invalidate = getattr(handler, "clear_icon_cache", None)
    if invalidate:
        invalidate()


def ensure_cache():
    ensure_app_layout()
    _migrate_legacy_asset_cache()


    if cache_is_valid():
        _topup_cache()
        _invalidate_icon_catalog()
        print("[CACHE] ready")
        return

    print("[CACHE] rebuilding asset cache")

    # Only copied assets are disposable. Project cache and user state live in
    # sibling AppData folders and must survive an asset cache rebuild.
    if os.path.exists(ASSET_CACHE_DIR):
        shutil.rmtree(ASSET_CACHE_DIR, ignore_errors=True)

    os.makedirs(ASSET_CACHE_DIR, exist_ok=True)

    # ASSET SOURCE BASE
    def get_asset_base():
        # Nuitka standalone / compiled executable
        if getattr(sys, "frozen", False) or "__compiled__" in globals():
            exe_dir = os.path.dirname(sys.executable)
            if os.path.isdir(os.path.join(exe_dir, "icons")):
                return exe_dir

        # source run → project root (naik dari core/)
        cand = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..")
        )
        if os.path.isdir(os.path.join(cand, "icons")):
            return cand

        exe_dir = os.path.dirname(sys.executable)
        if os.path.isdir(os.path.join(exe_dir, "icons")):
            return exe_dir

        return cand

    BASE = get_asset_base()
    IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico")

    missing_sources = []

    # icons: copy dari icons/ (SVG semua, + vGrayr.png yg harus PNG)
    icons_dst = os.path.join(ASSET_CACHE_DIR, "icons")
    os.makedirs(icons_dst, exist_ok=True)

    icons_src = os.path.join(BASE, "icons")
    if os.path.isdir(icons_src):
        for fname in os.listdir(icons_src):
            if fname == "__pycache__":
                continue
            s = os.path.join(icons_src, fname)
            if os.path.isdir(s):
                continue
            if not fname.lower().endswith(IMAGE_EXT):
                continue
            shutil.copy2(s, os.path.join(icons_dst, fname))
    else:
        missing_sources.append(icons_src)

    # textures: copy dari textures/
    textures_src = os.path.join(BASE, "textures")
    textures_dst = os.path.join(ASSET_CACHE_DIR, "textures")
    if os.path.isdir(textures_src):
        os.makedirs(textures_dst, exist_ok=True)
        for fname in os.listdir(textures_src):
            if fname == "__pycache__":
                continue
            s = os.path.join(textures_src, fname)
            if os.path.isdir(s):
                continue
            if not fname.lower().endswith(IMAGE_EXT):
                continue
            shutil.copy2(s, os.path.join(textures_dst, fname))
    else:
        missing_sources.append(textures_src)


    # source wajib ada
    if missing_sources:
        raise RuntimeError(
            "Asset source missing:\n" + "\n".join(missing_sources)
        )

    # hasil cache wajib ada isinya
    for d in REQUIRED_DIRS:
        if not os.path.isdir(d) or not os.listdir(d):
            raise RuntimeError(f"Cache build failed: {d} empty")

    with open(CACHE_MARK, "w") as f:
        f.write("ok")

    hide_folder(CACHE_DIR)
    _invalidate_icon_catalog()
    print("[CACHE] ready")
