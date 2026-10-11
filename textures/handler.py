# SPDX-License-Identifier: MPL-2.0
import os
import json
from core.cache import ASSET_CACHE_DIR

# SOURCE (PROJECT)
BASE_DIR = os.path.dirname(__file__)
TEXTURES_JSON = os.path.join(BASE_DIR, "textures.json")

# RUNTIME CACHE (IMAGE ONLY)
TEXTURES_DIR = os.path.join(ASSET_CACHE_DIR, "textures")

_TEXTURES = None  # lazy

def _load_textures():
    global _TEXTURES
    if _TEXTURES is not None:
        return

    if not os.path.exists(TEXTURES_JSON):
        _TEXTURES = {}
        return

    with open(TEXTURES_JSON, "r", encoding="utf-8") as f:
        _TEXTURES = json.load(f)

def texture(name: str) -> str:

    _load_textures()
    fname = _TEXTURES.get(name)
    if not fname:
        return ""
    return os.path.join(TEXTURES_DIR, fname)

def texture_pixmap(name: str):

    from PySide6.QtGui import QPixmap
    path = texture(name)
    if not path or not os.path.exists(path):
        return QPixmap()
    return QPixmap(path)
