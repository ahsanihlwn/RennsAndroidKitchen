# SPDX-License-Identifier: MPL-2.0
import os
import sys

# Use this family in QSS and QFont.
POPPINS_FAMILY = "Poppins"

_LOADED = False


def configure_text_rendering_env():

    # Pakai engine FreeType (rasterizer AA berkualitas) ketimbang GDI.
    if sys.platform == "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "windows:fontengine=freetype")
    # HiDPI: skala pecahan + rounding ke atas biar teks tidak buram.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    os.environ.setdefault(
        "QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough"
    )


def _fonts_dir() -> str:

    base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(base, "fonts")


def load_fonts() -> str:

    global _LOADED
    if _LOADED:
        return POPPINS_FAMILY

    from PySide6.QtGui import QFontDatabase

    fonts_dir = _fonts_dir()
    if not os.path.isdir(fonts_dir):
        print(f"[FONTS] folder tidak ditemukan: {fonts_dir}")
        return ""

    families = set()
    loaded = 0
    for fname in os.listdir(fonts_dir):
        if not fname.lower().endswith(".ttf"):
            continue
        path = os.path.join(fonts_dir, fname)
        fid = QFontDatabase.addApplicationFont(path)
        if fid == -1:
            print(f"[FONTS] gagal load: {fname}")
            continue
        loaded += 1
        for fam in QFontDatabase.applicationFontFamilies(fid):
            families.add(fam)

    if not loaded:
        print("[FONTS] tidak ada TTF yang termuat")
        return ""

    _LOADED = True
    # Pilih family bernama 'Poppins' bila ada, jika tidak ambil yang pertama.
    family = POPPINS_FAMILY if POPPINS_FAMILY in families else (
        sorted(families)[0] if families else POPPINS_FAMILY
    )
    print(f"[FONTS] {loaded} TTF Poppins termuat (family='{family}')")
    return family


def _apply_aa(font):

    from PySide6.QtGui import QFont
    # Poppins itu font geometris tanpa hint manual kuat → gampang jaggy.
    # Paksa AA + hinting vertikal ringan supaya mulus tanpa stroke jadi keras.
    font.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
    font.setHintingPreference(QFont.PreferVerticalHinting)
    return font


def install_font_polish_filter(app):

    from PySide6.QtCore import QObject, QEvent

    if getattr(app, "_font_polish_filter", None) is not None:
        return

    class _Filter(QObject):
        def eventFilter(self, obj, event):
            if event.type() == QEvent.Type.Polish:
                # obj bisa QWidget; hanya yang punya font()/setFont().
                setf = getattr(obj, "setFont", None)
                getf = getattr(obj, "font", None)
                if callable(setf) and callable(getf):
                    f = getf()
                    strat = f.styleStrategy()
                    # tambal hanya bila belum ber-AA, hindari kerja berulang.
                    from PySide6.QtGui import QFont
                    if not (strat & QFont.PreferAntialias):
                        setf(_apply_aa(f))
            return False  # jangan telan event

    flt = _Filter(app)
    app.installEventFilter(flt)
    app._font_polish_filter = flt  # cegah GC


def apply_app_font(app, point_size: int | None = None) -> str:

    family = load_fonts()
    if not family:
        return ""

    base = app.font()
    if point_size is not None:
        base.setPointSize(point_size)
    base.setFamily(family)
    _apply_aa(base)
    app.setFont(base)

    install_font_polish_filter(app)
    return family
