# SPDX-License-Identifier: MPL-2.0
"""Thin bridge to RAK's Rust GLSL Gaussian; Qt/Skia own the shared GL context."""
import ctypes
from pathlib import Path
import sys

from PySide6.QtGui import QImage


class RustGaussian:
    def __init__(self):
        self.handle = None
        self.frames = 0
        self.error = None
        try:
            if sys.platform != "win32":
                raise OSError("Rust OpenGL loader currently supports Windows")
            library = ctypes.CDLL(str(Path(__file__).with_name("native") / "rak_blur.dll"))
            library.rak_blur_abi.restype = ctypes.c_uint
            if library.rak_blur_abi() != 1:
                raise OSError("Rust blur ABI mismatch")
            library.rak_blur_create.restype = ctypes.c_void_p
            library.rak_blur_error.restype = ctypes.c_char_p
            library.rak_blur_run.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
                                            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_float]
            library.rak_blur_run.restype = ctypes.c_int
            library.rak_blur_destroy.argtypes = [ctypes.c_void_p]
            library.rak_blur_destroy.restype = None
            self.library = library
            self.handle = library.rak_blur_create()
            if not self.handle:
                raise OSError(library.rak_blur_error().decode("utf-8", "replace"))
        except OSError as error:
            self.error = str(error)

    def blur(self, source, sigma):
        if not self.handle:
            return None
        image = source.toImage() if hasattr(source, "toImage") else source
        image = image.convertToFormat(QImage.Format_RGBA8888_Premultiplied)
        output = QImage(image.width(), image.height(), QImage.Format_RGBA8888_Premultiplied)
        source_bytes = ctypes.c_ubyte.from_buffer(image.bits())
        output_bytes = ctypes.c_ubyte.from_buffer(output.bits())
        if not self.library.rak_blur_run(self.handle, ctypes.addressof(source_bytes), ctypes.addressof(output_bytes),
                image.width(), image.height(), image.bytesPerLine(), sigma):
            self.error = self.library.rak_blur_error().decode("utf-8", "replace")
            return None
        output.setDevicePixelRatio(source.devicePixelRatioF())
        self.frames += 1
        return output

    def close(self):
        if self.handle:
            self.library.rak_blur_destroy(self.handle)
            self.handle = None
