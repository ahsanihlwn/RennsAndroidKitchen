# SPDX-License-Identifier: MPL-2.0
import os
import json
from core.cache import ASSET_CACHE_DIR
from core.native_backend import asset_index

# SOURCE (PROJECT)
BASE_DIR = os.path.dirname(__file__)
ICONS_JSON = os.path.join(BASE_DIR, "icons.json")

# RUNTIME CACHE (IMAGE ONLY)
ICONS_DIR = os.path.join(ASSET_CACHE_DIR, "icons")

_ICONS = None  # lazy
_ASSET_CATALOG = None
_PATH_CACHE = {}
_ASSET_GENERATION = 0

# Icons that are white-on-transparent and need inversion on light themes
_ADAPTIVE_ICONS = {
    "erofs_label", "ext2_label", "ext3_label", "ext4_label", "ext4l_label",
    "sparse_label", "super_label",
}

_FG_TINT_ICONS = {
    "arrow_right",
}


def _load_icons():
    global _ICONS
    if _ICONS is not None:
        return

    if not os.path.exists(ICONS_JSON):
        _ICONS = {}
        return

    with open(ICONS_JSON, "r", encoding="utf-8") as f:
        _ICONS = json.load(f)


def clear_icon_cache(*, reload_manifest=False):

    global _ICONS, _ASSET_CATALOG, _PATH_CACHE, _ASSET_GENERATION
    _ASSET_GENERATION += 1
    _ASSET_CATALOG = None
    _PATH_CACHE = {}
    if reload_manifest:
        _ICONS = None


def _asset_catalog():
    global _ASSET_CATALOG
    directories = tuple(os.path.normpath(path) for path in (ICONS_DIR, BASE_DIR))
    cached = _ASSET_CATALOG
    if cached is not None and cached[0] == directories:
        return cached[1], _ASSET_GENERATION
    generation = _ASSET_GENERATION
    raw = asset_index(directories)
    catalog = {directory: {kind: frozenset(os.path.normcase(name) for name in names)
                          for kind, names in data.items()} for directory, data in raw.items()}
    # A background cache top-up may finish while Rust has detached Python.
    # Do not publish an inventory from before that invalidation.
    if generation == _ASSET_GENERATION:
        _ASSET_CATALOG = directories, catalog
    return catalog, generation


def icon_resource_key():

    from main_ext.class_ThemeUi import theme
    return (ICONS_DIR, BASE_DIR, _ASSET_GENERATION, _is_light_theme(), theme.FG)


def warm_icon_catalog():

    _load_icons()
    _asset_catalog()


def _asset_has(catalog, directory, filename, *, file_only=False):
    if os.path.dirname(filename):
        # Preserve support for explicit/nested paths outside the flat catalog.
        path = os.path.join(directory, filename)
        return os.path.isfile(path) if file_only else os.path.exists(path)
    return os.path.normcase(filename) in catalog[os.path.normpath(directory)][
        "files" if file_only else "entries"]


def icon(name: str) -> str:
    _load_icons()
    bg = "light" if _is_light_theme() else "dark"
    key = (ICONS_DIR, BASE_DIR, _ASSET_GENERATION, name, bg)
    cached = _PATH_CACHE.get(key)
    if cached is not None:
        return cached
    catalog, generation = _asset_catalog()
    fname = _ICONS.get(name)
    if not fname:
        fname = name if os.path.splitext(name)[1] else f"{name}.svg"
        if not any(_asset_has(catalog, directory, fname, file_only=True)
                   for directory in (ICONS_DIR, BASE_DIR)):
            directory = ICONS_DIR if _asset_has(catalog, ICONS_DIR, "fallback.png") else BASE_DIR
            result = os.path.join(directory, "fallback.png")
        else:
            result = None
    else:
        result = None
    if result is None:
        root, ext = os.path.splitext(fname)
        variant = f"{root}_{bg}{ext}"
        result = next((os.path.join(directory, candidate)
                       for candidate in (variant, fname) for directory in (ICONS_DIR, BASE_DIR)
                       if _asset_has(catalog, directory, candidate)), os.path.join(ICONS_DIR, fname))
    if generation == _ASSET_GENERATION:
        if len(_PATH_CACHE) >= 1024:
            _PATH_CACHE.clear()
        _PATH_CACHE[key] = result
    return result


def _is_light_theme() -> bool:
    try:
        from main_ext.class_ThemeUi import theme as _th
        return _th._palette.get("_is_light", False)
    except Exception:
        return False


def _invert_pixmap(path: str):

    from PySide6.QtGui import QPixmap, QPainter, QColor
    from PySide6.QtCore import Qt
    src = QPixmap(path)
    if src.isNull():
        return src
    # draw black where pixels are, using source alpha via CompositionMode
    out = QPixmap(src.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    # fill black
    p.fillRect(out.rect(), QColor(0, 0, 0))
    # mask by original alpha using DestinationIn
    p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    p.drawPixmap(0, 0, src)
    p.end()
    return out


def _tint_pixmap(path: str, color: str):

    from PySide6.QtGui import QPixmap, QPainter, QColor
    from PySide6.QtCore import Qt
    src = QPixmap(path)
    if src.isNull():
        return src
    out = QPixmap(src.size())
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.fillRect(out.rect(), QColor(color))
    p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    p.drawPixmap(0, 0, src)
    p.end()
    return out


def icon_pixmap(name: str):

    from PySide6.QtGui import QPixmap
    path = icon(name)
    if name in _FG_TINT_ICONS and _is_light_theme():
        try:
            from main_ext.class_ThemeUi import theme as _th
            return _tint_pixmap(path, _th.FG)
        except Exception:
            return _invert_pixmap(path)
    if name in _ADAPTIVE_ICONS and _is_light_theme():
        return _invert_pixmap(path)
    return QPixmap(path)


def tint_icon(name: str, color: str, size: int = 14):

    from PySide6.QtGui import QIcon
    from PySide6.QtCore import Qt
    path = icon(name)
    pix = _tint_pixmap(path, color)
    if size > 0 and not pix.isNull():
        pix = pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return QIcon(pix)


def create_themed_icon(name: str, normal_color: str, hover_color: str = None,
                       size: int = 14, disabled_color=None):

    from PySide6.QtGui import QIcon
    from PySide6.QtCore import Qt
    path = icon(name)
    ic = QIcon()
    pix_norm = _tint_pixmap(path, normal_color)
    if size > 0 and not pix_norm.isNull():
        pix_norm = pix_norm.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    ic.addPixmap(pix_norm, QIcon.Normal)
    if hover_color:
        pix_hover = _tint_pixmap(path, hover_color)
        if size > 0 and not pix_hover.isNull():
            pix_hover = pix_hover.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        ic.addPixmap(pix_hover, QIcon.Active)
    if disabled_color is not None:
        pix_disabled = _tint_pixmap(path, disabled_color)
        if size > 0 and not pix_disabled.isNull():
            pix_disabled = pix_disabled.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        ic.addPixmap(pix_disabled, QIcon.Disabled)
    return ic
